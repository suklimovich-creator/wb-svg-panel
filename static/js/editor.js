(function () {
    'use strict';
    var root = document.body.getAttribute('data-root');
    var api = root + 'api/editor/';
    var model, panel, section, selected, busy, padOpen;
    var previewToken = 0;
    var previewPending = 0;
    var previewStopped = false;
    var issue;
    var initialPanel = '';
    if (root === '../') {
        try { initialPanel = decodeURIComponent(location.pathname.split('/').pop()); }
        catch (err) { initialPanel = ''; }
    }
    var operationNames = {
        add_instance: 'Добавлен', remove_instance: 'Удалён',
        move_instance: 'Перемещён', update_instance: 'Изменён'
    };

    function byId(id) { return document.getElementById(id); }

    function node(tag, text) {
        var item = document.createElement(tag);
        item.textContent = text;
        return item;
    }

    function empty(id) {
        var item = byId(id);
        item.textContent = '';
        return item;
    }

    function option(select, value, label) {
        var item = node('option', label);
        item.value = value;
        select.appendChild(item);
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
            if ((xhr.getResponseHeader('Content-Type') || '').indexOf('image/svg+xml') < 0) {
                try { result = JSON.parse(result); }
                catch (err) { result = {}; }
            }
            if (xhr.status >= 200 && xhr.status < 300) {
                done(null, result);
            } else {
                done(result.error || {message: xhr.status === 401 || xhr.status === 403 ?
                    'Доступ к редактору отклонён. Проверьте, что вы вошли, и откройте страницу заново.' :
                    'Сервер не смог обработать запрос (HTTP ' + xhr.status + ').'});
            }
        };
        xhr.onerror = function () { done({message: 'Связь с редактором прервалась.'}); };
        xhr.ontimeout = function () { done({message: 'За 25 секунд браузер не получил ответ.'}); };
        xhr.send(data ? JSON.stringify(data) : null);
    }

    function draftPath(suffix) {
        return 'drafts/' + encodeURIComponent(model.draft_id) + suffix;
    }

    function status(text, failed) {
        var target = byId('status');
        target.className = failed ? 'error' : '';
        target.textContent = text;
    }

    function errorMessage(error) {
        if (error.current) {
            model = error.current;
            showModel();
        }
        if (error.code === 'base_revision' || error.code === 'draft_unknown') {
            model = null;
            byId('workspace').hidden = true;
            byId('check').disabled = true;
            byId('publish').disabled = true;
            previewToken += 1;
        }
        status(error.message, true);
        markIssue(error);
    }

    function markIssue(error) {
        if (error && !error.id) { return; }
        issue = error;
        var selectedError = issue && issue.id === selected;
        byId('instance-error').hidden = !selectedError;
        byId('instance-error').textContent = selectedError ?
            (issue.field ? issue.field + ' · ' : '') + issue.message : '';
        var list = byId('instances').querySelectorAll('[data-instance]');
        for (var i = 0; i < list.length; i++) {
            var id = list[i].getAttribute('data-instance');
            list[i].className = issue && issue.id === id ? 'invalid' : '';
        }
    }

    function progress() {
        if (!model) { return; }
        if (!model.draft_version) {
            status('Черновик открыт. Пока изменений нет.');
        } else if (model.checked) {
            status('Проверка прошла. Изменения можно опубликовать.');
        } else {
            status('Изменения пока в черновике. Проверьте их перед публикацией.');
        }
        byId('publish').disabled = !model.checked || !model.draft_version;
        byId('check').disabled = false;
    }

    function selectedPanel() { return model.panels[byId('panels').selectedIndex]; }
    function selectedSection() {
        var p = selectedPanel();
        return p && p.sections[byId('sections').selectedIndex];
    }

    function libraryItem(id) {
        return model.library.filter(function (item) { return item.id === id; })[0];
    }

    function label(item) { return item.title + ' · ' + item.id + ' · ' + item.type; }

    function search() {
        var query = byId('search').value.toLowerCase().trim();
        var menu = empty('elements');
        model.library.forEach(function (item) {
            if (label(item).toLowerCase().indexOf(query) >= 0) {
                option(menu, item.id, label(item));
            }
        });
        if (model.inline_tiles) {
            model.panels.forEach(function (p) {
                p.sections.forEach(function (s) {
                    s.tiles.filter(function (tile) { return !tile.element; }).forEach(function (tile) {
                        var name = label({id: tile.id, type: tile.type, title: tile.title || tile.type});
                        if (name.toLowerCase().indexOf(query) >= 0) {
                            option(menu, 'copy:' + tile.id, 'Копия · ' + name);
                        }
                    });
                });
            });

        }
        byId('library-title').textContent = 'Добавить плитку из библиотеки';
        byId('custom-id').hidden = !model.inline_tiles;
        byId('add').disabled = !menu.options.length;
    }

    function showModel() {
        var menu = empty('panels');
        model.panels.forEach(function (item) { option(menu, item.id, item.title + ' · ' + item.id); });
        if (model.panels.some(function (item) { return item.id === panel; })) {
            menu.value = panel;
        }
        var theme = byId('theme').value;
        var themes = empty('theme');
        option(themes, '', 'Из конфига');
        model.themes.forEach(function (name) { option(themes, name, name); });
        themes.value = theme;
        byId('workspace').hidden = !model.panels.length;
        showPanel();
        progress();
    }

    function showPanel() {
        var current = selectedPanel();
        if (!current) { return; }
        panel = current.id;
        var menu = empty('sections');
        current.sections.forEach(function (item, index) {
            option(menu, index, item.title + (item.readonly ? ' · только чтение' : ''));
            if (item.section === section) { menu.selectedIndex = index; }
        });
        showSection();
    }

    function currentTile() {
        var current = selectedSection();
        return current && current.tiles.filter(function (item) { return item.id === selected; })[0];
    }

    function showSection() {
        var current = selectedSection();
        var sources = empty('sources');
        var instances = empty('instances');
        byId('library').hidden = !current || current.readonly;
        if (current) {
            section = current.section;
            if (current.outside_sections) {
                sources.appendChild(node('p', 'Этот список расположен вне разделов и показан отдельным предпросмотром. Рабочая панель отображает разделы.'));
            }
            (current.sources || []).forEach(function (name) {
                var p = node('p', 'Источник · ');
                var a = node('a', name);
                a.href = root + 'editor/' + encodeURIComponent(name);
                a.onclick = function (event) {
                    event.preventDefault();
                    byId('panels').value = name;
                    panel = name;
                    selected = null;
                    section = null;
                    showPanel();
                };
                p.appendChild(a);
                sources.appendChild(p);
            });
            if (current.readonly && !sources.childNodes.length) {
                sources.appendChild(node('p', 'Раздел доступен только для чтения.'));
            }
            current.tiles.forEach(function (tile) {
                var li = node('li', '');
                var button = node('button', (tile.title || (tile.element ? libraryItem(tile.element).title : tile.type)) + ' · ' + (tile.implicit_id ? tile.element : tile.id));
                button.setAttribute('aria-pressed', String(selected === tile.id));
                button.onclick = function () {
                    selected = tile.id;
                    padOpen = false;
                    var buttons = instances.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        buttons[i].setAttribute('aria-pressed', String(buttons[i] === button));
                    }
                    showInstance();
                    markIssue(issue);
                };
                li.setAttribute('data-instance', tile.id);
                li.appendChild(button);
                instances.appendChild(li);
            });
        }
        showInstance();
        markIssue(issue);
        search();
        refreshPreview(true);
    }

    function showInstance() {
        var current = selectedSection();
        var tile = currentTile();
        byId('placement').hidden = !tile;
        byId('selection-empty').hidden = !!tile;
        byId('instance-error').hidden = true;
        byId('pad-status').textContent = '';
        byId('pad-image').hidden = true;
        if (!tile) {
            padOpen = false;
            return;
        }
        var element = tile.element ? libraryItem(tile.element) : tile;
        byId('identity').textContent = (tile.implicit_id ? 'Элемент ' + tile.element : 'ID ' + tile.id + (tile.element ? ' · элемент ' + element.id : '')) + ' · ' + element.type;
        byId('width').value = tile.w == null ? '' : tile.w;
        byId('height').value = tile.h == null ? '' : tile.h;
        byId('width').placeholder = element.w == null ? 'Авто' : String(element.w);
        byId('height').placeholder = element.h == null ? 'Авто' : String(element.h);
        byId('title').value = tile.title || '';
        byId('title').placeholder = tile.title_protected ? 'Защищённое название' : (element.title || element.type);
        byId('inherit-title').hidden = !tile.title_protected;
        var index = current.tiles.indexOf(tile);
        byId('up').disabled = index === 0;
        byId('down').disabled = index === current.tiles.length - 1;
        byId('pad-capability').textContent = element.pad ? 'У типа есть полноэкранный пульт.' : 'У типа нет собственного пульта.';
        byId('pad').hidden = !element.pad;
        byId('pad').textContent = padOpen ? 'Скрыть пульт' : 'Показать пульт';
    }

    function showTileList(target, current, visited) {
        if (!current || visited.indexOf(current.id) >= 0) { return; }
        var branch = visited.concat(current.id);
        current.sections.forEach(function (part) {
            if (part.tiles.length) {
                target.appendChild(node('strong', current.title + ' · ' + part.title));
                var list = node('div', '');
                var cols = Math.max(1, Math.min(12, Number(byId('cols').value) || selectedPanel().cols || 4));
                list.className = 'layout-grid';
                list.style.gridTemplateColumns = 'repeat(' + (2 * cols) + ', minmax(0, 1fr))';
                part.tiles.forEach(function (tile) {
                    var item = tile.element && libraryItem(tile.element);
                    var cell = node('div', tile.title || (item && item.title) || tile.id);
                    cell.style.gridColumn = 'span ' + Math.min(cols * 2, Math.max(1, Math.round(2 * (tile.w || (item && item.w) || 1))));
                    cell.style.gridRow = 'span ' + Math.max(1, Math.round(2 * (tile.h || (item && item.h) || 1)));
                    list.appendChild(cell);
                });
                target.appendChild(list);
            }
            (part.sources || []).forEach(function (source) {
                var panelSource = model.panels.filter(function (entry) { return entry.id === source; })[0];
                showTileList(target, panelSource, branch);
            });
        });
    }

    function image(path, id, message, token, clear) {
        var target = byId(id);
        var feedback = byId(message);
        if (clear) {
            target.hidden = true;
            target.removeAttribute('src');
            feedback.textContent = 'Загрузка SVG…';
        }
        previewPending += 1;
        request('GET', draftPath(path), null, function (error, svg) {
            previewPending -= 1;
            if (token !== previewToken) { return; }
            if (error || typeof svg !== 'string') {
                target.hidden = true;
                target.removeAttribute('src');
                feedback.textContent = error ? error.message : 'Получен некорректный SVG';
                if (error) {
                    previewStopped = true;
                    feedback.textContent = 'Предпросмотр: ' + error.message;
                    var retryImage = node('button', 'Повторить предпросмотр');
                    retryImage.onclick = function () { refreshPreview(true); };
                    feedback.appendChild(retryImage);
                    if (id === 'preview-image') { byId('layout').hidden = false; }
                    markIssue(error);
                }
                return;
            }
            if (id === 'pad-image' && !padOpen) { return; }
            if (id === 'preview-image') { byId('layout').hidden = true; }
            target.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
            target.hidden = false;
            feedback.textContent = '';
        });
    }

    function refreshPreview(clear) {
        if (!model || !selectedPanel()) { return; }
        if (!clear && (previewPending || previewStopped)) { return; }
        if (clear) {
            previewStopped = false;
            var layout = empty('layout');
            layout.hidden = false;
            layout.appendChild(node('h3', 'Схема плиток'));
            showTileList(layout, selectedPanel(), []);
        }
        var token = ++previewToken;
        var theme = byId('theme').value;
        var cols = byId('cols');
        var path = encodeURIComponent(panel);
        var query = '?draft_version=' + model.draft_version + '&theme=' + encodeURIComponent(theme);
        if (cols.value && !cols.validity.valid) {
            byId('preview-image').hidden = true;
            byId('preview-status').textContent = 'Введите целое число колонок от 1 до 12.';
            return;
        }
        if (cols.value) { query += '&cols=' + encodeURIComponent(cols.value); }
        var current = selectedSection();
        if (current && current.outside_sections) { query += '&root_tiles=1'; }
        image('/preview/' + path + '.svg' + query + '&mode=list', 'preview-image', 'preview-status', token, clear);
        if (padOpen && currentTile()) {
            image('/tile/' + path + '/' + encodeURIComponent(selected) + '.svg' + query,
                  'pad-image', 'pad-status', token, clear);
        }
    }

    function run(method, path, data, success, failure) {
        if (busy) { return; }
        busy = true;
        request(method, path, data, function (error, result) {
            busy = false;
            if (error) {
                errorMessage(error);
                if (failure) { failure(error); }
            } else { success(result); }
        });
    }

    function newDraft() {
        if (busy) { return; }
        var retry = byId('retry-start');
        function showRetry() { retry.hidden = false; }
        retry.hidden = true;
        status('Создаётся черновик текущей конфигурации…');
        empty('details').hidden = true;
        run('POST', 'drafts', {}, function (result) {
            model = result;
            issue = null;
            panel = panel || initialPanel;
            byId('legacy').hidden = true;
            byId('actions').hidden = false;
            showModel();
        }, function (error) {
            if (error.code === 'legacy') {
                byId('legacy').hidden = false;
                byId('actions').hidden = true;
                byId('workspace').hidden = true;
                status('Редактор станет доступен после миграции YAML.');
            } else { showRetry(); }
        });
    }

    byId('retry-start').onclick = newDraft;

    function operate(op, fields) {
        var current = selectedSection();
        if (!current || current.readonly || !model) { return; }
        fields.op = op;
        fields.panel = panel;
        fields.section = section;
        run('POST', draftPath('/operation'), {draft_version: model.draft_version, operation: fields}, function (result) {
            model = result;
            issue = null;
            if (op === 'add_instance') { byId('new-id').value = ''; }
            if (op === 'add_instance' && panel === fields.panel && section === fields.section) {
                var tiles = selectedSection().tiles;
                selected = tiles[tiles.length - 1].id;
            }
            empty('details').hidden = true;
            showModel();
        });
    }

    function receivedRevision(revision, message) {
        model = null;
        previewToken += 1;
        byId('workspace').hidden = true;
        byId('check').disabled = true;
        byId('publish').disabled = true;
        status(message + ' · ' + revision.id, false);
    }

    byId('restart').onclick = function () {
        if (model && model.draft_version && !window.confirm('Открыть новый черновик? Незавершённая работа останется в предыдущем черновике до истечения его срока.')) { return; }
        newDraft();
    };
    byId('panels').onchange = function () { section = null; selected = null; showPanel(); };
    byId('sections').onchange = function () { selected = null; showSection(); };
    byId('search').oninput = search;
    byId('theme').onchange = function () { refreshPreview(true); };
    byId('cols').onchange = function () { refreshPreview(true); };
    byId('pad').onclick = function () {
        padOpen = !padOpen;
        showInstance();
        refreshPreview(true);
    };
    byId('add').onclick = function () {
        var source = byId('elements').value;
        var fields = {element: source};
        if (model.inline_tiles && source.indexOf('copy:') === 0) { fields = {element: source.slice(5)}; }
        var id = byId('new-id').value.trim();
        if (model.inline_tiles && id) { fields.id = id; }
        operate('add_instance', fields);
    };
    byId('up').onclick = function () { operate('move_instance', {id: selected, direction: 'up'}); };
    byId('down').onclick = function () { operate('move_instance', {id: selected, direction: 'down'}); };
    byId('remove').onclick = function () {
        if (window.confirm('Убрать с панели ' + (currentTile().element || selected) + '?')) { operate('remove_instance', {id: selected}); }
    };
    byId('inherit-title').onclick = function () {
        operate('update_instance', {id: selected, fields: {title: ''}});
    };
    byId('placement').onsubmit = function (event) {
        event.preventDefault();
        var tile = currentTile();
        if (!tile) { return; }
        var fields = {};
        ['width', 'height'].forEach(function (id) {
            var field = id.charAt(0);
            var value = byId(id).value;
            if (value !== (tile[field] == null ? '' : String(tile[field]))) {
                fields[field] = value === '' ? null : Number(value);
            }
        });
        if (byId('title').value !== (tile.title || '')) { fields.title = byId('title').value; }
        if (Object.keys(fields).length) { operate('update_instance', {id: selected, fields: fields}); }
    };
    byId('check').onclick = function () {
        run('GET', draftPath('/diff'), null, function (diff) {
            var target = empty('details');
            var s = diff.summary;
            markIssue(null);
            model.checked = model.draft_version === diff.draft_version;
            target.hidden = false;
            target.appendChild(node('h2', 'Изменения черновика'));
            target.appendChild(node('p', 'Файлы к записи · ' + s.writes + '. Экземпляры: добавлено ' +
                                    s.instances.added + ', удалено ' + s.instances.removed +
                                    ', изменено ' + s.instances.changed + '.'));
            var list = node('div', '');
                var cols = Math.max(1, Math.min(12, Number(byId('cols').value) || selectedPanel().cols || 4));
                list.className = 'layout-grid';
                list.style.gridTemplateColumns = 'repeat(' + (2 * cols) + ', minmax(0, 1fr))';
            diff.operations.forEach(function (op) {
                list.appendChild(node('li', operationNames[op.op] + ' · ' + op.panel + ' · ' +
                                      (op.element || op.id) + (op.file ? ' · ' + op.file : '') + (op.fields ? ' · ' + op.fields.join(', ') : '')));
            });
            target.appendChild(list);
            progress();
            byId('publish').disabled = !s.writes && !s.deletes;
        });
    };
    byId('publish').onclick = function () {
        if (!model || !window.confirm('Опубликовать проверенные изменения этого черновика?')) { return; }
        run('POST', draftPath('/publish'), {draft_version: model.draft_version}, function (revision) {
            empty('details').hidden = true;
            receivedRevision(revision, 'Опубликовано');
        });
    };
    byId('history').onclick = function () {
        run('GET', 'revisions', null, function (history) {
            var target = empty('details');
            target.hidden = false;
            target.appendChild(node('h2', 'История ревизий'));
            if (!history.revisions.length) { target.appendChild(node('p', 'Ревизий пока нет.')); }
            history.revisions.forEach(function (item) {
                var row = node('p', item.timestamp + ' · ' + item.id + ' · ' + item.action + ' ');
                if (item.id !== history.head) {
                    var original = item.id === 'legacy-0';
                    var button = node('button', original ? 'Вернуть конфигурацию до миграции' : 'Откатить к этой ревизии');
                    button.onclick = function () {
                        if (!window.confirm(original ? 'Восстановить исходные файлы? Панели вернутся к состоянию до миграции. Для дальнейшего редактирования потребуется миграция через терминал.' : 'Восстановить ' + item.id + ' как новую ревизию?')) { return; }
                        run('POST', 'rollback', {revision: item.id, base_revision: history.head, confirm: true}, function (rev) {
                            receivedRevision(rev, 'Откат выполнен');
                            if (original) { newDraft(); } else { byId('history').click(); }
                        });
                    };
                    row.appendChild(button);
                }
                target.appendChild(row);
            });
        });
    };
    newDraft();
    window.setInterval(function () { if (!busy && !document.hidden) { refreshPreview(false); } }, 12000);
}());
