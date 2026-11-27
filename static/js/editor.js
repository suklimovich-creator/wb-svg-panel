(function () {
    'use strict';
    var api = 'api/editor';
    var byId = function (id) { return document.getElementById(id); };
    var model = null;
    var panel = '';
    var section = 0;
    var selected = null;
    var checked = null;
    var busy = false;
    var inspectorOpen = false;
    var listSignature = '';
    var previewCounter = 0;
    var padCounter = 0;
    var padImage = '';
    var mobileMedia = window.matchMedia('(max-width: 900px)');
    var resumeKey = 'wb-editor:shared';
    var legacyResumeKey = 'wb-editor:' + location.pathname;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
        });
    }
    function status(text, bad) {
        byId('status').textContent = text;
        byId('status').className = bad ? 'err' : 'ok';
    }
    function setBusy(value) {
        busy = value;
        ['restart', 'check', 'publish', 'history'].forEach(function (id) {
            byId(id).disabled = value;
        });
        document.querySelectorAll('form button').forEach(function (el) {
            if (el.id !== 'close-inspector') { el.disabled = value; }
        });
    }
    function errorText(err) {
        var e = err && err.error;
        if (e && e.message) { return e.message + ' (' + e.code + ')'; }
        return String(err && err.message || err || 'Ошибка запроса');
    }
    function explainError(err) {
        var code = err && err.error && err.error.code;
        if (err && err.status === 401) {
            return 'Сервер запросил повторный вход. Откройте панель заново через штатный адрес Wiren Board.';
        }
        if (err && err.status === 403 && (code === 'origin' || code === 'editor_header' || code === 'request_too_large' || code === 'json')) {
            return 'Редактор не смог подтвердить адрес страницы. Откройте его снова по ссылке «Редактор панелей» с главной страницы панели.';
        }
        return errorText(err);
    }
    function request(method, path, data) {
        var opts = {
            method: method,
            headers: {'X-WB-Editor': '1'},
            credentials: 'same-origin'
        };
        if (data !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(data);
        }
        return fetch(api + path, opts).then(function (r) {
            return r.text().then(function (text) {
                var body = {};
                try { body = text ? JSON.parse(text) : {}; }
                catch (ignore) { body = {error: {message: text || ('HTTP ' + r.status)}}; }
                if (!r.ok) {
                    body.status = r.status;
                    throw body;
                }
                return body;
            });
        });
    }
    function run(method, path, data, done) {
        setBusy(true);
        status('Выполняется…');
        return request(method, path, data).then(function (result) {
            setBusy(false);
            if (done) { done(result); }
            return result;
        }).catch(function (err) {
            setBusy(false);
            status(explainError(err), true);
            if (err && err.draft) { showModel(err.draft); }
            throw err;
        });
    }
    function selectedPanel() {
        return model && model.panels.filter(function (p) { return p.id === panel; })[0];
    }
    function selectedSection() {
        var p = selectedPanel();
        return p && p.sections[section];
    }
    function currentTiles() {
        var p = selectedPanel();
        var s = selectedSection();
        return p && p.sections.length ? (s ? s.tiles : []) : (p ? p.tiles : []);
    }
    function findTile(id) {
        return currentTiles().filter(function (item) { return item.id === id; })[0];
    }
    function rowTile(item) {
        var active = item.id === selected;
        return '<li class="' + (item.valid === false ? 'invalid' : '') +
            '" data-instance="' + esc(item.id) + '">' +
            '<button type="button" class="tile-select" aria-pressed="' + active + '">' +
            '<strong>' + esc(item.effective_title || item.title || item.element) + '</strong>' +
            '<small>' + esc(item.element || item.type) + ' · ' + esc(item.type) + '</small></button>' +
            '<span class="row-actions">' +
            '<button type="button" data-direction="up" aria-label="Выше" ' + (busy ? 'disabled' : '') + '>↑</button>' +
            '<button type="button" data-direction="down" aria-label="Ниже" ' + (busy ? 'disabled' : '') + '>↓</button></span></li>';
    }
    function remember() {
        if (!model) { return; }
        try {
            sessionStorage.setItem(resumeKey, JSON.stringify({
                id: model.draft_id,
                panel: panel,
                section: section,
                tile: selected,
                version: model.draft_version
            }));
        } catch (ignore) {}
    }
    function forget() {
        try {
            sessionStorage.removeItem(resumeKey);
            sessionStorage.removeItem(legacyResumeKey);
        } catch (ignore) {}
    }
    function leaveEdits() {
        return !formDirty() || window.confirm(
            'Есть несохранённые поля. Перейти без их сохранения?'
        );
    }
    function option(value, title, current) {
        return '<option value="' + esc(value) + '"' +
            (String(value) === String(current) ? ' selected' : '') + '>' + esc(title) + '</option>';
    }
    function drawSections() {
        var p = selectedPanel();
        var sections = p ? p.sections : [];
        byId('sections').innerHTML = sections.map(function (s, i) {
            return option(i, s.title || 'Раздел ' + (i + 1), section);
        }).join('');
        byId('sections').hidden = !sections.length;
        byId('section-label').hidden = !sections.length;
        byId('remove-section').disabled = busy || !sections.length;
        byId('sources').innerHTML = (p && p.sources || []).map(function (s) {
            return '<a href="editor/' + encodeURIComponent(s) + '">' + esc(s) + '</a>';
        }).join('');
        byId('sources-wrap').hidden = !(p && p.sources && p.sources.length);
        byId('library').hidden = !!(p && p.sources && p.sources.length);
        byId('section-settings').disabled = busy || !sections.length;
        byId('create-section').querySelector('button').disabled = busy || !!(p && p.sources && p.sources.length);
    }
    function drawList() {
        var list = currentTiles();
        var signature = JSON.stringify([model.draft_version, list.map(function (x) {
            return [x.id, x.effective_title, x.title, x.element, x.type, x.valid];
        }), selected, busy]);
        if (signature === listSignature) { return; }
        listSignature = signature;
        byId('instances').innerHTML = list.map(rowTile).join('');
        byId('empty').hidden = list.length > 0;
        Array.prototype.forEach.call(byId('instances').children, function (row, i) {
            var tile = list[i];
            var select = row.querySelector('[aria-pressed]');
            select.onclick = function () {
                if (!leaveEdits()) { return; }
                selected = tile.id;
                remember();
                listSignature = '';
                drawList();
                showProperties();
                if (mobileMedia.matches) { openInspector(); }
            };
            Array.prototype.forEach.call(row.querySelectorAll('[data-direction]'), function (button) {
                button.disabled = busy || (button.dataset.direction === 'up' ? i === 0 : i === list.length - 1);
                button.onclick = function () {
                    selected = tile.id;
                    remember();
                    operation({op: 'move_instance', id: tile.id, direction: button.dataset.direction}, true);
                };
            });
        });
    }
    function drawLibrary(filter) {
        var query = (filter == null ? byId('search').value : filter).toLowerCase();
        byId('elements').innerHTML = model.library.filter(function (item) {
            return !query || (item.title + ' ' + item.id + ' ' + item.type).toLowerCase().indexOf(query) >= 0;
        }).map(function (item) {
            return option(item.id, item.title + ' · ' + item.type, '');
        }).join('');
        byId('add').disabled = busy || !byId('elements').children.length || !!selectedPanel().sources.length;
    }
    function drawScreens() {
        var p = selectedPanel();
        var assigned = (model.screens || []).filter(function (s) { return s.panel === panel; });
        byId('screen-list').innerHTML = assigned.map(function (s) {
            return '<li>' + esc(s.id) + ' <button type="button" data-screen="' + esc(s.id) + '">Снять</button></li>';
        }).join('');
        Array.prototype.forEach.call(byId('screen-list').querySelectorAll('button'), function (button) {
            button.disabled = busy;
            button.onclick = function () { assignScreen(button.dataset.screen, true); };
        });
        byId('assign-screen').hidden = !(model.screens && model.screens.length);
        byId('screen-list-wrap').hidden = !assigned.length;
    }
    function showModel(next) {
        model = next;
        checked = null;
        if (!model.panels.length) { return; }
        if (!selectedPanel()) { panel = model.panels[0].id; section = 0; selected = null; }
        if (!selectedSection() && selectedPanel().sections.length) { section = 0; selected = null; }
        if (selected && !findTile(selected)) { selected = null; }
        byId('workspace').hidden = false;
        byId('panels').innerHTML = model.panels.map(function (p) {
            return option(p.id, p.title + ' (' + p.id + ')', panel);
        }).join('');
        drawSections();
        listSignature = '';
        drawList();
        drawLibrary();
        drawScreens();
        showProperties();
        byId('remove-panel').disabled = busy || model.panels.length < 2;
        byId('version').textContent = 'Черновик ' + model.draft_id + ' · версия ' + model.draft_version;
        byId('publish').disabled = busy || !!model.base_conflict;
        remember();
    }
    function mergeModel(next) {
        if (!next || !next.panels || next.panels.length !== 1 || next.library.length) {
            return next;
        }
        var merged = Object.assign({}, model, next);
        merged.library = model.library;
        merged.themes = model.themes;
        merged.screens = model.screens;
        merged.panels = model.panels.map(function (p) {
            return p.id === next.panels[0].id ? next.panels[0] : p;
        });
        return merged;
    }
    function showPreview() {
        if (!model || !panel) { return; }
        var seq = ++previewCounter;
        var preview = byId('preview');
        var image = byId('preview-image');
        var old = image.getAttribute('src') || '';
        var query = '?draft_version=' + model.draft_version + '&theme=' + encodeURIComponent(byId('theme').value) +
            '&cols=' + encodeURIComponent(byId('cols').value || '') + '&_=' + seq;
        preview.classList.add('loading');
        image.onload = function () {
            if (seq !== previewCounter) { return; }
            preview.classList.remove('loading');
            image.hidden = false;
            if (old && old !== image.src) { URL.revokeObjectURL(old); }
            byId('refresh-preview').hidden = true;
        };
        image.onerror = function () {
            if (seq !== previewCounter) { return; }
            preview.classList.remove('loading');
            image.hidden = !old;
            byId('refresh-preview').hidden = false;
            byId('preview-error').textContent = 'Не удалось построить SVG. Исправьте кандидат или повторите предпросмотр.';
        };
        byId('preview-error').textContent = '';
        image.src = api + '/drafts/' + encodeURIComponent(model.draft_id) + '/preview/' + encodeURIComponent(panel) + '.svg' + query;
    }
    function fieldRow(field) {
        var id = 'setting-' + field.key;
        var value = field.value == null ? '' : field.value;
        if (field.type === 'bool') {
            return '<label for="' + id + '">' + esc(field.label) + '</label><select id="' + id + '" name="' + esc(field.key) + '">' +
                option('', 'По умолчанию', value == null ? '' : String(value)) + option('true', 'Да', value) + option('false', 'Нет', value) + '</select>';
        }
        if (field.choices) {
            return '<label for="' + id + '">' + esc(field.label) + '</label><select id="' + id + '" name="' + esc(field.key) + '">' +
                option('', 'По умолчанию', value) + field.choices.map(function (x) { return option(x, x, value); }).join('') + '</select>';
        }
        return '<label for="' + id + '">' + esc(field.label) + '</label><input id="' + id + '" name="' + esc(field.key) + '" value="' + esc(value) + '">';
    }
    function openInspector() {
        inspectorOpen = true;
        byId('inspector').classList.add('open');
        if (mobileMedia.matches) {
            byId('inspector').setAttribute('aria-modal', 'true');
            byId('inspector').setAttribute('role', 'dialog');
            document.body.classList.add('inspector-open');
        }
    }
    function closeInspector() {
        inspectorOpen = false;
        byId('inspector').classList.remove('open');
        byId('inspector').removeAttribute('aria-modal');
        byId('inspector').setAttribute('role', 'region');
        document.body.classList.remove('inspector-open');
        var active = byId('instances').querySelector('[aria-pressed=true]');
        if (active) { active.focus(); }
    }
    function showProperties() {
        var tile = findTile(selected);
        var hasTile = !!tile;
        byId('placement').hidden = !hasTile;
        byId('instance-error').hidden = !hasTile || tile.valid !== false;
        byId('no-selection').hidden = hasTile;
        if (!hasTile) { return; }
        byId('selected-title').textContent = tile.effective_title || tile.title || tile.element;
        ['width', 'height', 'title'].forEach(function (id, i) {
            var key = ['w', 'h', 'title'][i];
            var field = byId(id);
            if (document.activeElement !== field) { field.value = tile[key] == null ? '' : tile[key]; }
        });
        byId('element-id').textContent = tile.element || 'inline: ' + tile.id;
        byId('pad').disabled = busy || !tile.pad;
        byId('remove').disabled = busy;
        if (tile.valid === false) {
            byId('instance-error').textContent = 'У плитки неполные или неверные настройки. Откройте проверку кандидата.';
        }
    }
    function operation(op, keep) {
        op.panel = panel;
        op.section = selectedPanel().sections.length ? section : null;
        return run('POST', '/drafts/' + encodeURIComponent(model.draft_id) + '/operation?result=panel', {
            draft_version: model.draft_version,
            operation: op
        }, function (next) {
            showModel(mergeModel(next));
            if (!keep) { selected = null; showProperties(); }
            status('Сохранено. Проверьте кандидат перед публикацией.');
        });
    }
    function assignScreen(screen, clear) {
        run('POST', '/drafts/' + encodeURIComponent(model.draft_id) + '/operation', {
            draft_version: model.draft_version,
            operation: {op: 'assign_screen', screen: screen, panel: clear ? null : panel}
        }, function (next) {
            showModel(next);
            status(clear ? 'Назначение снято' : 'Экран назначен в черновике');
        }).catch(function () {});
    }
    function check() {
        return run('GET', '/drafts/' + encodeURIComponent(model.draft_id) + '/diff?draft_version=' + model.draft_version, undefined, function (result) {
            checked = {version: model.draft_version, id: model.draft_id};
            var d = result.diff || {};
            byId('diff').textContent = JSON.stringify({
                version: result.draft_version,
                changes: d.summary || d,
                affected_panels: result.affected_panels || [],
                errors: result.errors || [],
                warnings: result.warnings || []
            }, null, 2);
            byId('diff-wrap').hidden = false;
            byId('publish').disabled = busy || !result.ok || !!model.base_conflict;
            drawList();
            status(result.ok ? 'Проверка пройдена. Можно публиковать.' : 'Проверка нашла ошибки.', !result.ok);
        });
    }
    function publish(force) {
        var version = model.draft_version;
        var id = model.draft_id;
        return run('POST', '/drafts/' + encodeURIComponent(id) + '/publish' + (force ? '?force=1' : ''), {draft_version: version}, function (result) {
            if (result.conflict) {
                status('Активная конфигурация изменилась. Создайте новый черновик.', true);
                byId('publish').disabled = true;
                return;
            }
            status('Опубликовано: ' + result.revision.id);
            return restart(panel);
        });
    }
    function restart(preferred, savedTile, savedSection) {
        return run('POST', '/drafts', {}, function (next) {
            showModel(next);
            if (preferred && model.panels.some(function (p) { return p.id === preferred; })) { panel = preferred; }
            if (typeof savedSection === 'number') { section = savedSection; }
            selected = savedTile || null;
            showModel(model);
            showPreview();
            status('Новый черновик готов. Можно продолжать.');
        });
    }
    function restoreDraft(saved) {
        return run('GET', '/drafts/' + encodeURIComponent(saved.id), undefined, function (next) {
            showModel(next);
            if (next.panels.some(function (p) { return p.id === saved.panel; })) { panel = saved.panel; }
            if (typeof saved.section === 'number') { section = saved.section; }
            selected = saved.tile || null;
            showModel(next);
            showPreview();
            status('Восстановлены подтверждённые правки. Несохранённые поля формы не восстанавливаются.');
        });
    }
    function recoverPublish(id, preferred) {
        status('Ответ публикации потерян. Проверяю результат…');
        return request('GET', '/drafts/' + encodeURIComponent(id)).then(function (result) {
            if (result.publish_result && result.publish_result.ok) {
                status('Опубликовано: ' + result.publish_result.revision.id);
                return restart(preferred);
            }
            if (result.publish_result && result.publish_result.error) {
                throw {error: result.publish_result.error};
            }
            showModel(result);
            status('Публикация не подтверждена. Повторите проверку и публикацию.', true);
        }).catch(function (err) {
            status('Результат публикации не получен: ' + explainError(err), true);
        });
    }
    function history() {
        return run('GET', '/revisions', undefined, function (result) {
            byId('details').innerHTML = (result.revisions || []).map(function (r) {
                return '<details><summary>' + esc(r.id) + ' · ' + esc(r.created_at || '') + '</summary>' +
                    '<pre>' + esc(JSON.stringify({parent: r.parent, files: r.files, operation: r.operation}, null, 2)) + '</pre>' +
                    '<button type="button" data-rollback="' + esc(r.id) + '">Откатить к этой ревизии</button> ' +
                    '<button type="button" data-delete="' + esc(r.id) + '">Удалить ревизию</button></details>';
            }).join('') || '<p>История пуста.</p>';
            byId('history-view').hidden = false;
            Array.prototype.forEach.call(byId('details').querySelectorAll('[data-rollback]'), function (button) {
                button.disabled = busy;
                button.onclick = function () {
                    if (!window.confirm('Откатить конфигурацию к ' + button.dataset.rollback + '?')) { return; }
                    run('POST', '/rollback', {revision: button.dataset.rollback, base_revision: result.current, confirm: true}, function () {
                        status('Откат выполнен');
                        return restart(panel);
                    }).catch(function () {});
                };
            });
            Array.prototype.forEach.call(byId('details').querySelectorAll('[data-delete]'), function (button) {
                button.disabled = busy;
                button.onclick = function () {
                    if (!window.confirm('Удалить снимок ' + button.dataset.delete + '?')) { return; }
                    run('DELETE', '/revisions/' + encodeURIComponent(button.dataset.delete), {}, function () {
                        status('Ревизия удалена');
                        return history();
                    }).catch(function () {});
                };
            });
            status('История загружена');
        });
    }
    function openPad() {
        var tile = findTile(selected);
        if (!tile || !tile.pad) { return; }
        var seq = ++padCounter;
        byId('pad-view').hidden = false;
        byId('pad-title').textContent = tile.effective_title || tile.title || tile.element;
        byId('pad-status').textContent = 'Загрузка пульта…';
        var image = byId('pad-image');
        image.onload = function () {
            if (seq !== padCounter) { return; }
            byId('pad-status').textContent = '';
            image.hidden = false;
            padImage = image.src;
        };
        image.onerror = function () {
            if (seq !== padCounter) { return; }
            byId('pad-status').textContent = 'Пульт для этой плитки недоступен.';
            image.hidden = true;
        };
        image.hidden = true;
        image.src = api + '/drafts/' + encodeURIComponent(model.draft_id) + '/tile/' + encodeURIComponent(panel) + '/' + encodeURIComponent(tile.id) + '.svg?draft_version=' + model.draft_version + '&_=' + seq;
    }
    function closePad() {
        padCounter++;
        byId('pad-view').hidden = true;
        byId('pad-image').hidden = true;
        byId('pad-image').removeAttribute('src');
        byId('pad-status').textContent = '';
    }
    function formDirty() {
        var forms = [byId('placement'), byId('settings-form')];
        return forms.some(function (form) {
            if (!form || form.hidden || form.closest('[hidden]')) { return false; }
            return Array.prototype.some.call(form.querySelectorAll('input,select,textarea'), function (field) {
                if (field.disabled || !field.name || field.type === 'submit' || field.type === 'button') { return false; }
                var initial = field.tagName === 'SELECT' ?
                    Array.prototype.filter.call(field.options, function (o) { return o.defaultSelected; }).map(function (o) { return o.value; }).join(',') :
                    field.defaultValue;
                var current = field.tagName === 'SELECT' ?
                    Array.prototype.filter.call(field.selectedOptions, function (o) { return true; }).map(function (o) { return o.value; }).join(',') :
                    field.value;
                return current !== initial;
            });
        });
    }
    function bind() {
        byId('restart').onclick = function () { if (leaveEdits()) { restart(panel); } };
        byId('check').onclick = function () { check().catch(function () {}); };
        byId('publish').onclick = function () {
            if (!checked || checked.version !== model.draft_version || checked.id !== model.draft_id) {
                status('Сначала выполните проверку текущей версии.', true);
                return;
            }
            if (!window.confirm('Опубликовать проверенный черновик? Рабочие панели изменятся.')) { return; }
            publish(false).catch(function (err) {
                if (!err || !err.status || err.status < 500) { return; }
                recoverPublish(model.draft_id, panel);
            });
        };
        byId('history').onclick = function () { history().catch(function () {}); };
        byId('panels').onchange = function () {
            if (!leaveEdits()) { byId('panels').value = panel; return; }
            panel = byId('panels').value; section = 0; selected = null;
            remember();
            showModel(model); showPreview();
        };
        byId('sections').onchange = function () {
            if (!leaveEdits()) { byId('sections').value = section; return; }
            section = +byId('sections').value; selected = null;
            remember();
            listSignature = ''; drawList(); showProperties();
        };
        byId('search').oninput = function () { drawLibrary(); };
        byId('add').onclick = function () {
            var element = byId('elements').value;
            if (!element) { return; }
            operation({op: 'add_instance', element: element}, true);
        };
        byId('placement').onsubmit = function (event) {
            event.preventDefault();
            if (!selected) { return; }
            var fields = {};
            ['width', 'height', 'title'].forEach(function (id, i) {
                var key = ['w', 'h', 'title'][i];
                var raw = byId(id).value.trim();
                fields[key] = raw === '' ? null : (key === 'title' ? raw : +raw);
            });
            operation({op: 'update_instance', id: selected, fields: fields}, true);
        };
        byId('remove').onclick = function () {
            if (selected && window.confirm('Удалить выбранную плитку из черновика?')) {
                operation({op: 'remove_instance', id: selected}, false);
            }
        };
        byId('create-panel').onsubmit = function (event) {
            event.preventDefault();
            var key = byId('create-panel').querySelector('[name=key]').value.trim();
            var caption = byId('create-panel').querySelector('[name=caption]').value.trim();
            if (!key) { return; }
            operation({op: 'create_panel', panel: key, title: caption}, false);
        };
        byId('remove-panel').onclick = function () {
            if (window.confirm('Удалить панель «' + panel + '» из черновика?')) {
                operation({op: 'remove_panel'}, false);
            }
        };
        byId('create-section').onsubmit = function (event) {
            event.preventDefault();
            var caption = byId('create-section').querySelector('[name=caption]').value.trim();
            operation({op: 'create_section', title: caption}, false);
        };
        byId('remove-section').onclick = function () {
            if (selectedPanel().sections.length && window.confirm('Удалить текущий раздел?')) {
                operation({op: 'remove_section'}, false);
            }
        };
        byId('assign-screen').onsubmit = function (event) {
            event.preventDefault();
            var screen = byId('assign-screen').querySelector('[name=screen]').value.trim();
            if (screen) { assignScreen(screen, false); }
        };
        byId('refresh-preview').onclick = showPreview;
        byId('theme').onchange = showPreview;
        byId('cols').onchange = showPreview;
        byId('pad').onclick = openPad;
        byId('close-pad').onclick = closePad;
        byId('close-inspector').onclick = closeInspector;
        byId('panel-settings').onclick = function () {
            var p = selectedPanel();
            byId('settings-title').textContent = 'Настройки панели';
            byId('settings-kind').value = 'panel';
            var fields = [
                {key: 'title', label: 'Название', value: p.title},
                {key: 'hidden', label: 'Скрыть панель', type: 'bool', value: p.hidden},
                {key: 'cols', label: 'Колонки', value: p.cols},
                {key: 'theme', label: 'Тема', value: p.theme, choices: model.themes},
                {key: 'tabs', label: 'Вкладки', value: p.tabs, choices: ['true', 'bottom']},
                {key: 'accordion', label: 'Гармошка', type: 'bool', value: p.accordion},
                {key: 'swipe', label: 'Свайп', type: 'bool', value: p.swipe},
                {key: 'pad_close', label: 'Закрытие пульта, с', value: p.pad_close},
                {key: 'saver_mode', label: 'Заставка', value: p.screensaver && p.screensaver.mode, choices: ['on', 'off']},
                {key: 'saver_after', label: 'Бездействие', value: p.screensaver && p.screensaver.after},
                {key: 'saver_panel', label: 'Панель заставки', value: p.screensaver && p.screensaver.panel, choices: model.panels.map(function (x) { return x.id; })}
            ];
            byId('settings-fields').innerHTML = fields.map(fieldRow).join('');
            byId('settings-form').hidden = false;
            openInspector();
        };
        byId('section-settings').onclick = function () {
            var s = selectedSection();
            if (!s) { return; }
            byId('settings-title').textContent = 'Настройки раздела';
            byId('settings-kind').value = 'section';
            byId('settings-fields').innerHTML = [
                {key: 'title', label: 'Название', value: s.title},
                {key: 'saver_mode', label: 'Заставка', value: s.screensaver && s.screensaver.mode, choices: ['on', 'off']},
                {key: 'saver_after', label: 'Бездействие', value: s.screensaver && s.screensaver.after},
                {key: 'saver_panel', label: 'Панель заставки', value: s.screensaver && s.screensaver.panel, choices: model.panels.map(function (x) { return x.id; })}
            ].map(fieldRow).join('');
            byId('settings-form').hidden = false;
            openInspector();
        };
        byId('settings-form').onsubmit = function (event) {
            event.preventDefault();
            var fields = {};
            Array.prototype.forEach.call(byId('settings-fields').querySelectorAll('[name]'), function (field) {
                var value = field.value;
                if (value === '') { fields[field.name] = null; }
                else if (value === 'true') { fields[field.name] = true; }
                else if (value === 'false') { fields[field.name] = false; }
                else { fields[field.name] = value; }
            });
            var kind = byId('settings-kind').value;
            operation({op: kind === 'panel' ? 'update_panel' : 'update_section', fields: fields}, true);
        };
        byId('library-link').onclick = function (event) {
            if (busy || !leaveEdits()) { event.preventDefault(); return; }
            remember();
        };
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                if (!byId('pad-view').hidden) { closePad(); }
                else if (inspectorOpen) { closeInspector(); }
            }
        });
        window.addEventListener('beforeunload', function (event) {
            if (formDirty()) { event.preventDefault(); event.returnValue = ''; }
        });
        if (mobileMedia.addEventListener) {
            mobileMedia.addEventListener('change', function () {
                if (!mobileMedia.matches) { closeInspector(); }
            });
        }
    }
    function start() {
        bind();
        var saved = null;
        try {
            saved = JSON.parse(
                sessionStorage.getItem(resumeKey) ||
                sessionStorage.getItem(legacyResumeKey)
            );
        } catch (ignore) {}
        if (saved && saved.id) {
            restoreDraft(saved).catch(function () {
                forget();
                restart().catch(function (err) {
                    byId('workspace').hidden = true;
                    var retry = byId('retry-load');
                    retry.hidden = false;
                    retry.onclick = function () {
                        retry.hidden = true;
                        restart().catch(function () { retry.hidden = false; });
                    };
                    status(explainError(err), true);
                });
            });
            return;
        }
        restart().catch(function (err) {
            byId('workspace').hidden = true;
            var retry = byId('retry-load');
            retry.hidden = false;
            retry.onclick = function () {
                retry.hidden = true;
                restart().catch(function () { retry.hidden = false; });
            };
            status(explainError(err), true);
        });
    }
    start();

}());
