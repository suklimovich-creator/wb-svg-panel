(function () {
    'use strict';
    var root = document.body.getAttribute('data-root');
    var api = root + 'api/editor/';
    var model, selected = '', busy;
    var affectedPanels = [], previewToken = 0;
    var resumeKey = 'wb-editor:shared';
    var operationNames = {
        update_element: 'Изменены свойства элемента', update_binding: 'Изменена привязка', copy_panel: 'Скопирована панель', copy_section: 'Скопирована секция',
        add_instance: 'Добавлен', remove_instance: 'Удалён', move_instance: 'Перемещён', update_instance: 'Изменён',
        update_panel: 'Настройки панели', update_section: 'Настройки раздела', add_panel: 'Добавлена панель', remove_panel: 'Удалена панель',
        add_section: 'Добавлен раздел', remove_section: 'Удалён раздел', assign_screen: 'Назначен экран'
    };

    function byId(id) { return document.getElementById(id); }
    function node(tag, text) { var item = document.createElement(tag); item.textContent = text; return item; }
    function empty(id) { var item = byId(id); item.textContent = ''; return item; }
    function option(select, value, label) {
        var item = node('option', label); item.value = value; select.appendChild(item);
    }
    function request(method, path, data, done) {
        var xhr = new XMLHttpRequest();
        xhr.open(method, api + path);
        xhr.timeout = 25000;
        xhr.setRequestHeader('Accept', 'application/json, image/svg+xml');
        if (method === 'POST') {
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-WB-Editor', '1');
        }
        xhr.onload = function () {
            var result = xhr.responseText;
            try { result = JSON.parse(result); }
            catch (err) {
                if (xhr.status >= 200 && xhr.status < 300) { done({message: 'Сервер вернул неожиданный ответ.'}); return; }
                result = {};
            }
            if (xhr.status >= 200 && xhr.status < 300) { done(null, result); }
            else {
                done(result.error || {message: xhr.status === 401 || xhr.status === 403 ?
                    'Доступ к редактору отклонён. Проверьте, что вы вошли, и откройте страницу заново.' :
                    'Сервер не смог обработать запрос (HTTP ' + xhr.status + ').'});
            }
        };
        xhr.onerror = function () { done({message: 'Связь с редактором прервалась.'}); };
        xhr.ontimeout = function () { done({message: 'За 25 секунд браузер не получил ответ.'}); };
        xhr.send(data ? JSON.stringify(data) : null);
        return xhr;
    }
    function draftPath(suffix) { return 'drafts/' + encodeURIComponent(model.draft_id) + suffix; }
    function status(text, failed) {
        byId('status').className = failed ? 'error' : '';
        byId('status').textContent = text;
        byId('edit-status').className = failed ? 'error' : '';
        byId('edit-status').textContent = text;
    }
    function selectedItem() {
        return model && model.library.filter(function (item) { return item.id === selected; })[0];
    }
    function progress() {
        if (!model) { return; }
        if (!model.draft_version) { status('Черновик открыт. Пока изменений нет.'); }
        else if (model.checked) { status('Проверка прошла. Изменения можно опубликовать.'); }
        else { status('Сохранено в черновике. Нажмите «Опубликовать», чтобы проверить и применить изменения.'); }
        byId('publish').disabled = !model.draft_version;
        byId('check').disabled = false;
    }
    function errorMessage(error) {
        if (error.current) { model = error.current; showModel(); }
        if (error.code === 'base_revision' || error.code === 'draft_unknown') {
            model = null;
            byId('workspace').hidden = true;
            byId('check').disabled = true;
            byId('publish').disabled = true;
        }
        status(error.message, true);
    }
    function run(method, path, data, success, failure) {
        if (busy) { return; }
        busy = true;
        var focused = document.activeElement;
        var locked = Array.prototype.map.call(document.querySelectorAll('#workspace input, #workspace textarea, #workspace select, #workspace button, #actions button'), function (el) {
            var entry = [el, el.disabled]; el.disabled = true; return entry;
        });
        byId('workspace').setAttribute('aria-busy', 'true');
        request(method, path, data, function (error, result) {
            busy = false;
            locked.forEach(function (entry) { entry[0].disabled = entry[1]; });
            if (focused && document.contains(focused)) { focused.focus({preventScroll: true}); }
            byId('workspace').setAttribute('aria-busy', 'false');
            if (error) { errorMessage(error); if (failure) { failure(error); } }
            else { success(result); }
        });
    }

    function formDirty() {
        var changed = false;
        Array.prototype.forEach.call(byId('property-form').elements, function (input) {
            if (input.name && !input.disabled && input.value !== input.dataset.initial) { changed = true; }
        });
        return changed;
    }
    function leaveEdits() {
        return !formDirty() || window.confirm('Не сохранять введённые изменения свойств?');
    }
    function remember() {
        try {
            if (model) {
                var saved = {};
                try { saved = JSON.parse(sessionStorage.getItem(resumeKey) || '{}'); } catch (ignore) {}
                saved.id = model.draft_id; saved.element = selected; saved.version = model.draft_version;
                sessionStorage.setItem(resumeKey, JSON.stringify(saved));
            } else { sessionStorage.removeItem(resumeKey); }
        } catch (err) { /* Storage may be disabled by the browser. */ }
    }
    function rememberAddress() {
        if (!selected) { return; }
        try { history.replaceState(null, '', location.pathname + '?element=' + encodeURIComponent(selected)); } catch (err) {}
    }
    function initialElement() {
        var match = location.search.match(/[?&]element=([^&]+)/);
        if (!match) { return ''; }
        try { return decodeURIComponent(match[1]); } catch (err) { return ''; }
    }

    function drawList() {
        var query = byId('library-search').value.toLowerCase().trim();
        var list = empty('library-list'), shown = 0;
        model.library.forEach(function (item) {
            var usage = item.affected_panels || [];
            var haystack = (item.title + ' ' + item.id + ' ' + item.type).toLowerCase();
            if (query && haystack.indexOf(query) < 0) { return; }
            shown++;
            var li = node('li', ''), button = node('button', '');
            button.type = 'button';
            button.setAttribute('data-element', item.id);
            button.setAttribute('aria-pressed', String(item.id === selected));
            button.appendChild(node('span', item.title)).className = 'name';
            button.appendChild(node('span', item.id + ' · ' + item.type + ' · ' +
                (usage.length ? 'панелей: ' + usage.length : 'не используется'))).className = 'meta';
            button.onclick = function () {
                if (busy || item.id === selected || !leaveEdits()) { return; }
                selected = item.id;
                drawList(); showProperties(); remember(); rememberAddress();
            };
            li.appendChild(button); list.appendChild(li);
        });
        byId('library-count').textContent = shown + ' из ' + model.library.length;
        byId('library-empty').hidden = shown > 0;
    }

    var rowColumns = {
        series: {channel: 'Канал', label: 'Название', unit: 'Единица', color: 'Цвет',
            digits: 'Знаков после запятой', min: 'Минимум', max: 'Максимум', grid: 'Шаг сетки'},
        items: {title: 'Название', channel: 'Канал', icon: 'Иконка', command_topic: 'Топик команды',
            command_on: 'Значение команды', level: 'Уровень от 0 до 1'},
        status: {title: 'Название', icon: 'Иконка', channel: 'Канал', value_channel: 'Канал значения',
            value_unit: 'Единица', air: 'Канал качества воздуха'}
    };

    function propertyRows(container, field, input) {
        var columns = rowColumns[field.key];
        var rows = field.value || [];
        if (!columns || !Array.isArray(rows) || field.protected) { return; }
        var holder = node('div', '');
        var add = node('button', 'Добавить строку');
        add.type = 'button';
        container.appendChild(holder); container.appendChild(add);
        function draw() {
            holder.textContent = '';
            rows.forEach(function (row, index) {
                var block = node('fieldset', '');
                block.appendChild(node('legend', 'Строка ' + (index + 1)));
                Object.keys(columns).forEach(function (key) {
                    var label = node('label', columns[key]);
                    var control = node('input', '');
                    var numeric = ['digits', 'min', 'max', 'grid', 'level'].indexOf(key) >= 0;
                    if (numeric) { control.type = 'number'; control.step = key === 'digits' ? '1' : 'any'; }
                    control.value = row[key] == null ? '' : row[key];
                    control.oninput = function () {
                        if (control.value === '') { delete row[key]; }
                        else { row[key] = numeric ? Number(control.value) : control.value; }
                        input.value = JSON.stringify(rows, null, 2);
                    };
                    label.appendChild(control); block.appendChild(label);
                });
                var remove = node('button', 'Удалить строку');
                remove.type = 'button';
                remove.onclick = function () {
                    rows.splice(index, 1); input.value = JSON.stringify(rows, null, 2); draw();
                };
                block.appendChild(remove); holder.appendChild(block);
            });
        }
        var full = node('details', '');
        full.appendChild(node('summary', 'Все поля в JSON'));
        full.appendChild(input.parentNode);
        container.appendChild(full);
        function readRows() {
            try {
                var next = JSON.parse(input.value || '[]');
                if (Array.isArray(next) && next.every(function (r) {
                    return r && typeof r === 'object' && !Array.isArray(r);
                })) {
                    rows = next; add.disabled = false; draw(); return;
                }
            } catch (ignore) {}
            add.disabled = true; holder.textContent = '';
        }
        input.addEventListener('input', readRows);
        add.onclick = function () {
            rows.push({}); input.value = JSON.stringify(rows, null, 2); draw();
            holder.lastChild.querySelector('input').focus();
        };
        readRows();
    }

    function showUsage(item) {
        var host = empty('element-usage');
        var panels = item.affected_panels || [];
        host.appendChild(node('p', panels.length ?
            'После публикации общие свойства изменятся на панелях:' :
            'Элемент пока не размещён ни на одной панели.'));
        if (panels.length) {
            var list = node('ul', '');
            panels.forEach(function (name) { list.appendChild(node('li', name)); });
            host.appendChild(list);
        }
    }
    function showProperties() {
        var item = selectedItem(), fields = empty('property-fields');
        previewToken++;
        if (!item) {
            byId('property-title').textContent = 'Элемент не выбран';
            byId('property-type').textContent = ''; byId('property-id').textContent = '';
            byId('property-preview').hidden = true; byId('property-preview').removeAttribute('src');
            byId('property-preview-status').hidden = false;
            byId('property-preview-status').textContent = 'Выберите элемент в списке слева.';
            byId('save-properties').disabled = true; empty('element-usage'); return;
        }
        byId('property-title').textContent = item.title;
        byId('property-type').textContent = item.type;
        byId('property-id').textContent = item.id;
        byId('save-properties').disabled = false;
        showUsage(item);
        item.properties.forEach(function (field) {
            var spec = field.spec;
            var label = node('label', spec[0]);
            var kind = spec[1];
            var select = kind === 'choice' || kind === 'boolean';
            var input = node(select ? 'select' : kind === 'json' ? 'textarea' : 'input', '');
            input.dataset.kind = kind;
            if (select) {
                option(input, '', 'По умолчанию');
                if (kind === 'boolean') { option(input, 'true', 'Да'); option(input, 'false', 'Нет'); }
                else { spec.slice(2).forEach(function (choice) { option(input, choice, choice); }); }
            } else if (kind === 'json') {
                input.rows = 4; label.appendChild(node('small', 'JSON, например ' + spec[2]));
            } else if (['size', 'integer', 'number', 'nonnegative', 'positive'].indexOf(kind) >= 0) {
                input.type = 'number'; input.step = kind === 'size' ? '0.5' : kind === 'integer' ? '1' : 'any';
                if (kind === 'size' || kind === 'integer') {
                    input.min = kind === 'size' ? 0.5 : spec[2]; input.max = kind === 'size' ? 32 : spec[3];
                } else if (kind !== 'number') { input.min = 0; }
            }
            input.name = field.key; input.disabled = field.protected;
            input.value = field.value == null ? '' : kind === 'json' ? JSON.stringify(field.value, null, 2) : field.value;
            input.dataset.initial = input.value;
            if (field.protected) { input.placeholder = 'Задаётся в YAML'; }
            input.addEventListener('input', function () { byId('edit-status').textContent = 'Есть несохранённые изменения свойств.'; });
            label.appendChild(input);
            if (kind === 'json' && rowColumns[field.key] && !field.protected) {
                var group = node('fieldset', '');
                group.appendChild(node('legend', spec[0])); group.appendChild(label); fields.appendChild(group);
                propertyRows(group, field, input);
            } else { fields.appendChild(label); }
        });
        var preview = byId('property-preview'), feedback = byId('property-preview-status'), token = ++previewToken;
        feedback.hidden = false; feedback.textContent = 'Загрузка предпросмотра…'; preview.hidden = true;
        preview.onload = function () { if (token === previewToken) { feedback.hidden = true; preview.hidden = false; } };
        preview.onerror = function () {
            if (token !== previewToken) { return; }
            preview.hidden = true; feedback.hidden = false; feedback.textContent = 'Не удалось показать SVG элемента.';
        };
        preview.src = api + draftPath('/element.svg?element=' + encodeURIComponent(selected) + '&draft_version=' + model.draft_version);
    }
    function showModel() {
        if (!model.library.some(function (item) { return item.id === selected; })) {
            selected = model.library.length ? model.library[0].id : '';
        }
        byId('workspace').hidden = false;
        drawList(); showProperties(); progress(); remember(); rememberAddress();
    }

    byId('library-search').oninput = drawList;
    byId('property-form').onsubmit = function (event) {
        event.preventDefault();
        var fields = {}, invalid = false;
        Array.prototype.forEach.call(this.elements, function (input) {
            if (input.name && !input.disabled && input.value !== input.dataset.initial) {
                if (input.value && input.dataset.kind === 'json') {
                    try { fields[input.name] = JSON.parse(input.value); }
                    catch (error) {
                        input.setCustomValidity('Проверьте формат JSON');
                        input.oninput = function () { this.setCustomValidity(''); };
                        input.reportValidity(); invalid = true;
                    }
                } else if (input.value && input.dataset.kind === 'boolean') {
                    fields[input.name] = input.value === 'true';
                } else {
                    fields[input.name] = input.value === '' ? null : input.type === 'number' ? Number(input.value) : input.value;
                }
            }
        });
        if (invalid || busy) { return; }
        if (!Object.keys(fields).length) { status('Поля элемента не изменились.'); return; }
        status('Сохранение свойств в черновике…');
        run('POST', draftPath('/operation'), {draft_version: model.draft_version, operation: {
            op: 'update_element', element: selected, fields: fields
        }}, function (result) {
            model = result; empty('details').hidden = true; showModel();
            byId('edit-status').textContent = 'Сохранено в черновике.';
        });
    };

    function receivedRevision(revision, message) {
        model = null; remember(); previewToken++;
        byId('workspace').hidden = true; byId('check').disabled = true; byId('publish').disabled = true;
        status(message + ' · ' + revision.id, false);
    }
    byId('restart').onclick = function () {
        if (model && model.draft_version && !window.confirm('Открыть новый черновик? Незавершённая работа останется в предыдущем черновике до истечения его срока.')) { return; }
        if (!leaveEdits()) { return; }
        newDraft();
    };
    byId('editor-link').onclick = function (event) {
        if (busy || !leaveEdits()) { event.preventDefault(); return; }
        remember();
    };
    window.addEventListener('pagehide', function () { if (model) { remember(); } });
    window.addEventListener('beforeunload', function (event) {
        if (formDirty()) { event.preventDefault(); event.returnValue = ''; }
    });

    function start() {
        var saved;
        try { saved = JSON.parse(sessionStorage.getItem(resumeKey)); } catch (err) { saved = null; }
        if (!saved || !saved.id) { newDraft(); return; }
        byId('retry-start').hidden = true;
        status('Открывается сохранённый черновик…');
        run('GET', 'drafts/' + encodeURIComponent(saved.id), null, function (result) {
            model = result;
            selected = saved.version === result.draft_version && saved.element ? saved.element : initialElement();
            byId('actions').hidden = false; showModel();
        }, function (error) {
            if (error.code === 'draft_unknown' || error.code === 'base_revision') {
                remember(); byId('retry-start').textContent = 'Новый черновик';
            }
            byId('retry-start').hidden = false;
        });
    }
    function newDraft(published) {
        if (busy) { return; }
        previewToken++;
        var retry = byId('retry-start'); retry.hidden = true;
        status(published === true ? 'Опубликовано. Открывается следующий черновик…' : 'Создаётся черновик текущей конфигурации…');
        empty('details').hidden = true;
        run('POST', 'drafts', {}, function (result) {
            model = result;
            if (!selected) { selected = initialElement(); }
            byId('legacy').hidden = true; byId('actions').hidden = false;
            showModel();
            if (published === true) { status('Опубликовано. Можно продолжать редактирование в новом черновике.'); }
        }, function (error) {
            if (error.code === 'legacy') {
                byId('legacy').hidden = false; byId('actions').hidden = true; byId('workspace').hidden = true;
                status('Редактор станет доступен после миграции YAML.');
            } else {
                if (published === true) { status('Опубликовано. Новый черновик открыть не удалось. ' + error.message, true); }
                retry.hidden = false;
            }
        });
    }
    byId('retry-start').onclick = start;

    byId('check').onclick = function () {
        run('GET', draftPath('/diff'), null, function (diff) {
            var target = empty('details'), s = diff.summary;
            affectedPanels = diff.affected_panels || [];
            model.checked = model.draft_version === diff.draft_version;
            target.hidden = false; target.appendChild(node('h2', 'Изменения черновика'));
            if (affectedPanels.length) { target.appendChild(node('p', 'Затронутые панели: ' + affectedPanels.join(', '))); }
            target.appendChild(node('p', 'Файлы к записи · ' + s.writes + '. Экземпляры: добавлено ' +
                s.instances.added + ', удалено ' + s.instances.removed + ', изменено ' + s.instances.changed + '.'));
            var list = node('ul', '');
            diff.operations.forEach(function (op) {
                list.appendChild(node('li', operationNames[op.op] + ' · ' + (op.panel ? op.panel + ' · ' : '') +
                    (op.new_panel || op.element || op.id || '') + (op.file ? ' · ' + op.file : '') +
                    (op.fields ? ' · ' + op.fields.join(', ') : '')));
            });
            target.appendChild(list); progress(); byId('publish').disabled = !s.writes && !s.deletes;
        });
    };
    byId('publish').onclick = function () {
        if (!model || busy || !leaveEdits()) { return; }
        function confirmPublish() {
            if (!window.confirm('Применить проверенные изменения? Затронутые панели: ' + affectedPanels.join(', '))) { progress(); return; }
            status('Публикация изменений…'); previewToken++;
            function continueEditing(revision) {
                empty('details').hidden = true; receivedRevision(revision, 'Опубликовано'); newDraft(true);
            }
            var receipt = draftPath('/publication');
            run('POST', draftPath('/publish'), {draft_version: model.draft_version}, continueEditing, function (error) {
                if (error.code && error.code !== 'editor_failure') { return; }
                status('Ответ о публикации не получен. Проверяется результат…'); busy = true;
                function checkResult() {
                    request('GET', receipt, null, function (problem, result) {
                        if (!problem && result.pending) { window.setTimeout(checkResult, 1500); return; }
                        busy = false;
                        if (!problem && result.revision) { continueEditing(result.revision); }
                        else { status('Публикация не подтверждена. Откройте историю и проверьте рабочую панель.', true); }
                    });
                }
                checkResult();
            });
        }
        status('Проверка черновика перед публикацией…');
        run('GET', draftPath('/diff'), null, function (diff) {
            if (!diff.summary.writes && !diff.summary.deletes) {
                status('Черновик совпадает с рабочей конфигурацией.'); byId('publish').disabled = true; return;
            }
            model.checked = model.draft_version === diff.draft_version;
            affectedPanels = diff.affected_panels || [];
            if (model.checked) { confirmPublish(); }
        });
    };
    byId('history').onclick = function () {
        run('GET', 'revisions', null, function (history) {
            var target = empty('details'); target.hidden = false; target.appendChild(node('h2', 'История ревизий'));
            if (!history.revisions.length) { target.appendChild(node('p', 'Ревизий пока нет.')); }
            history.revisions.forEach(function (item) {
                var row = node('p', item.timestamp + ' · ' + item.id + ' · ' + item.action + ' ');
                if (item.id !== history.head) {
                    var original = item.id === 'legacy-0';
                    var button = node('button', original ? 'Вернуть конфигурацию до миграции' : 'Откатить к этой ревизии');
                    button.onclick = function () {
                        if (!window.confirm(original ? 'Восстановить исходные файлы? Панели вернутся к состоянию до миграции. Для дальнейшего редактирования потребуется миграция через терминал.' : 'Восстановить ' + item.id + ' как новую ревизию?')) { return; }
                        run('POST', 'rollback', {revision: item.id, base_revision: history.head, confirm: true}, function (rev) {
                            receivedRevision(rev, 'Откат выполнен'); if (original) { newDraft(); } else { byId('history').click(); }
                        });
                    };
                    row.appendChild(button);
                    var remove = node('button', 'Удалить ревизию');
                    remove.onclick = function () {
                        if (!window.confirm('Удалить снимок ' + item.id + '? Откат к нему станет недоступен.')) { return; }
                        run('POST', 'revisions/delete', {revision: item.id, base_revision: history.head, confirm: true}, function () {
                            status('Ревизия удалена.'); byId('history').click();
                        });
                    };
                    row.appendChild(remove);
                }
                target.appendChild(row);
            });
        });
    };
    start();
})();
