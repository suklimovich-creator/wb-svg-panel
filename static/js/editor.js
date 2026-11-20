(function () {
    'use strict';
    var root = document.body.getAttribute('data-root');
    var api = root + 'api/editor/';
    var model, panel, section, selected, busy, padOpen;
    var previewToken = 0;
    var previewRequest, padRequest, padToken = 0;
    var libraryIndex = {};
    var inspectorMode = "tile", returnFocus, savedScroll = 0;
    var mobile = window.matchMedia("(max-width: 760px)");
    var issue;
    var initialPanel = '';
    if (root === '../') {
        try { initialPanel = decodeURIComponent(location.pathname.split('/').pop()); }
        catch (err) { initialPanel = ''; }
    }
    var operationNames = {
        add_instance: 'Добавлен', remove_instance: 'Удалён',
        move_instance: 'Перемещён', update_instance: 'Изменён',
        update_panel: 'Настройки панели', update_section: 'Настройки раздела',
        add_panel: 'Добавлена панель', remove_panel: 'Удалена панель',
        add_section: 'Добавлен раздел', remove_section: 'Удалён раздел', assign_screen: 'Назначен экран'
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
                catch (err) {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        done({message: 'Сервер вернул неожиданный ответ.'});
                        return;
                    }
                    result = {};
                }
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
        return xhr;
    }

    function draftPath(suffix) {
        return 'drafts/' + encodeURIComponent(model.draft_id) + suffix;
    }

    function status(text, failed) {
        var target = byId('status');
        target.className = failed ? 'error' : '';
        target.textContent = text;
        byId('edit-status').textContent = text;
        byId('edit-status').className = failed ? 'error' : '';
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
            status('Сохранено в черновике. Нажмите «Опубликовать», чтобы проверить и применить изменения.');
        }
        byId('publish').disabled = !model.draft_version;
        byId('check').disabled = false;
    }

    function selectedPanel() { return model.panels[byId('panels').selectedIndex]; }
    function selectedSection() {
        var p = selectedPanel();
        return p && p.sections[byId('sections').selectedIndex];
    }

    function libraryItem(id) {
        return libraryIndex[id];
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
        libraryIndex = Object.create(null);
        model.library.forEach(function (item) { libraryIndex[item.id] = item; });
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
        byId('workspace').hidden = false;
        var hasPanel = model.panels.length > 0;
        ['.components', '.preview', '.inspector'].forEach(function (selector) {
            document.querySelector(selector).hidden = !hasPanel;
        });
        ['section-management', 'assign-screen', 'remove-panel', 'panel-settings'].forEach(function (id) {
            byId(id).hidden = !hasPanel;
        });
        if (!hasPanel) {
            empty('sections'); empty('sources'); byId('section-settings').hidden = true;
            byId('structure').open = true;
        }
        var screens = empty('screen-names');
        (model.screens || []).forEach(function (screen) { if (screen.name) { option(screens, screen.name, screen.panel); } });
        showPanel();
        progress();
    }

    function showPanel() {
        var current = selectedPanel();
        if (!current) { return; }
        panel = current.id;
        var assigned = (model.screens || []).filter(function (screen) { return screen.panel === panel; });
        byId('screen-list').textContent = 'Назначенные экраны: ' + (assigned.map(function (screen) { return screen.name || 'Имя скрыто'; }).join(', ') || 'нет явных назначений');
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
        empty('instances');
        byId('remove-section').disabled = !current || current.section == null;
        byId('section-settings').hidden = !current || current.section == null || !current.settings;
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
                    if (busy || !leaveEdits()) { return; }
                    closeInspector(true); inspectorMode = 'tile';
                    byId('preview-image').hidden = true;
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
            syncInstances();
        }
        showInstance();
        markIssue(issue);
        search();
        refreshPreview(true);
    }

    function syncInstances() {
        var current = selectedSection();
        var list = byId('instances');
        var existing = {};
        Array.prototype.forEach.call(list.children, function (li) { existing[li.getAttribute('data-instance')] = li; });
        (current ? current.tiles : []).forEach(function (tile, index) {
            var li = existing[tile.id];
            var item = tile.element && libraryItem(tile.element);
            var title = (tile.title || (item && item.title) || tile.type) + ' · ' + (tile.implicit_id ? tile.element : tile.id);
            if (!li) {
                li = node('li', '');
                li.setAttribute('data-instance', tile.id);
                var row = node('div', '');
                row.className = 'instance-row';
                var select = node('button', '');
                select.type = 'button';
                select.onclick = function () {
                    if (busy || !leaveEdits()) { return; }
                    selected = tile.id;
                    padOpen = false;
                    syncInstances();
                    inspectorMode = 'tile';
                    showInstance();
                    markIssue(issue);
                    openInspector(select);
                };
                row.appendChild(select);
                ['up', 'down'].forEach(function (direction) {
                    var arrow = node('button', direction === 'up' ? '↑' : '↓');
                    arrow.type = 'button';
                    arrow.setAttribute('data-direction', direction);
                    arrow.onclick = function () { operate('move_instance', {id: tile.id, direction: direction}); };
                    row.appendChild(arrow);
                });
                li.appendChild(row);
            }
            var buttons = li.querySelectorAll('button');
            buttons[0].textContent = title;
            buttons[0].setAttribute('aria-pressed', String(selected === tile.id));
            buttons[1].disabled = current.readonly || index === 0;
            buttons[2].disabled = current.readonly || index === current.tiles.length - 1;
            buttons[1].setAttribute('aria-label', 'Поднять ' + title);
            buttons[2].setAttribute('aria-label', 'Опустить ' + title);
            if (list.children[index] !== li) { list.insertBefore(li, list.children[index] || null); }
            delete existing[tile.id];
        });
        Object.keys(existing).forEach(function (id) { list.removeChild(existing[id]); });
    }

    function dirtyTile() {
        var tile = currentTile();
        if (inspectorMode !== 'tile' || !tile) { return false; }
        return byId('title').value !== (tile.title || '') ||
            byId('width').value !== (tile.w == null ? '' : String(tile.w)) ||
            byId('height').value !== (tile.h == null ? '' : String(tile.h));
    }

    function leaveEdits() {
        return !(dirtyTile() || (inspectorMode !== 'tile' && settingsDirty())) || window.confirm('Не сохранять введённые изменения?');
    }

    function openInspector(trigger) {
        returnFocus = trigger || document.activeElement;
        if (!mobile.matches) { return; }
        if (!byId('inspector').classList.contains('open')) {
            savedScroll = window.pageYOffset;
            document.body.style.position = 'fixed';
            document.body.style.top = '-' + savedScroll + 'px';
            document.body.style.width = '100%';
        }
        byId('inspector').classList.add('open');
        byId('inspector').setAttribute('role', 'dialog');
        byId('inspector').setAttribute('aria-modal', 'true');
        byId('inspector-backdrop').hidden = false;
        byId('inspector').focus({preventScroll: true});
    }

    function closeInspector(force) {
        if (!force && (busy || !leaveEdits())) { return; }
        var wasOpen = byId('inspector').classList.contains('open');
        byId('inspector').classList.remove('open');
        byId('inspector').removeAttribute('role');
        byId('inspector').removeAttribute('aria-modal');
        byId('inspector-backdrop').hidden = true;
        document.body.style.position = '';
        document.body.style.top = '';
        document.body.style.width = '';
        if (wasOpen) {
            window.scrollTo(0, savedScroll);
            if (returnFocus && document.contains(returnFocus)) { returnFocus.focus({preventScroll: true}); }
        }
        if (!force) {
            if (inspectorMode === 'tile') { showInstance(); }
            else { showSettings(inspectorMode); }
        }
    }

    byId('close-inspector').onclick = function () { closeInspector(false); };
    byId('inspector-backdrop').onclick = function () { closeInspector(false); };
    mobile.addListener(function () { if (!mobile.matches) { closeInspector(true); } });
    byId('inspector').onkeydown = function (event) {
        if (!mobile.matches || !byId('inspector').classList.contains('open')) { return; }
        if (event.key === 'Escape') { event.preventDefault(); closeInspector(false); }
        if (event.key === 'Tab') {
            var controls = Array.prototype.filter.call(byId('inspector').querySelectorAll('button, input, select'), function (el) { return !el.disabled && el.getClientRects().length; });
            var first = controls[0], last = controls[controls.length - 1];
            if (event.shiftKey && (document.activeElement === first || document.activeElement === byId('inspector'))) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && (document.activeElement === last || document.activeElement === byId('inspector'))) { event.preventDefault(); first.focus(); }
        }
    };

    function showInstance() {
        if (inspectorMode !== 'tile') { return; }
        byId('inspector-title').textContent = 'Настройки плитки';
        byId('settings-form').hidden = true;
        var current = selectedSection();
        var tile = currentTile();
        byId('placement').hidden = !tile;
        byId('selection-empty').hidden = !!tile;
        byId('instance-error').hidden = true;
        padToken += 1;
        if (padRequest) { padRequest.abort(); }
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
        byId('pad-capability').textContent = element.pad ? 'У типа есть полноэкранный пульт.' : 'У типа нет собственного пульта.';
        byId('pad').hidden = !element.pad;
        byId('pad').textContent = padOpen ? 'Скрыть пульт' : 'Показать пульт';
    }

    var settingsInitial = {};

    function rememberSettings() {
        settingsInitial = {};
        Array.prototype.forEach.call(byId('settings-form').querySelectorAll('[data-setting]'), function (el) {
            settingsInitial[el.getAttribute('data-setting')] = el.value;
        });
    }

    function settingsDirty() {
        return Array.prototype.some.call(byId('settings-form').querySelectorAll('[data-setting]'), function (el) {
            return el.value !== settingsInitial[el.getAttribute('data-setting')];
        });
    }

    function showSettings(mode) {
        inspectorMode = mode;
        padOpen = false;
        refreshPad();
        byId('placement').hidden = true;
        byId('selection-empty').hidden = true;
        byId('inspector-title').textContent = mode === 'panel' ? 'Настройки панели' : 'Настройки раздела';
        var form = empty('settings-form');
        form.hidden = false;
        var values = (mode === 'panel' ? selectedPanel() : selectedSection()).settings || {};
        function field(key, title, choices, type) {
            var label = node('label', title);
            var control = node(choices ? 'select' : 'input', '');
            control.id = 'setting-' + key;
            control.setAttribute('data-setting', key);
            if (choices) { choices.forEach(function (choice) { option(control, choice[0], choice[1]); }); }
            else { control.type = type || 'text'; }
            var value = values[key] == null ? '' : String(values[key]);
            control.value = value;
            if (choices && control.value !== value) { option(control, value, value); control.value = value; }
            label.appendChild(control);
            form.appendChild(label);
            return control;
        }
        field('title', 'Название').maxLength = 512;
        var booleanChoices = [['', 'Наследовать'], ['true', 'Да'], ['false', 'Нет']];
        if (mode === 'panel') {
            field('hidden', 'Скрыть из списка панелей', booleanChoices);
            var cols = field('cols', 'Колонки (пусто — из общих настроек)', null, 'number');
            cols.min = '1'; cols.max = '12'; cols.step = '1';
            field('theme', 'Тема панели', [['', 'Из общих настроек']].concat(model.themes.map(function (name) { return [name, name]; })));
            field('tabs', 'Вкладки разделов', booleanChoices);
            field('accordion', 'Сворачиваемые разделы', booleanChoices);
            field('swipe', 'Перелистывание на ESP32', booleanChoices);
            field('pad_close', 'Закрытие пульта при бездействии (например, 30s или 2m)');
        }
        var saver = values.screensaver;
        var saverMode = saver == null ? '' : (saver === 0 || saver === 'none' || (typeof saver === 'object' && (saver.after === 0 || saver.after === 'none')) ? 'off' : 'on');
        var choices = mode === 'panel' ? [['', 'Из общих настроек'], ['off', 'Выключена'], ['on', 'Свои настройки']] :
            [['', 'Обычный раздел'], ['off', 'Служебный раздел без автопоказа'], ['on', 'Раздел заставки ESP32']];
        var saverSelect = field('saver_mode', mode === 'panel' ? 'Браузерная заставка' : 'Назначение раздела', choices);
        saverSelect.value = saverMode;
        var after = field('saver_after', mode === 'panel' ? 'Бездействие до заставки (например, 1m)' : 'Интервал заставки ESP32 (например, 30s)');
        after.value = saver && typeof saver === 'object' ? (saver.after == null ? '' : String(saver.after)) : (saverMode === 'on' ? String(saver) : '');
        var destination;
        if (mode === 'panel') {
            destination = field('saver_panel', 'Панель заставки', [['', 'Не переопределять панель']].concat(model.panels.filter(function (p) { return p.id !== panel; }).map(function (p) { return [p.id, p.title + ' · ' + p.id]; })));
            destination.value = saver && typeof saver === 'object' ? (saver.panel || '') : '';
            if (saver && typeof saver === 'object' && saver.panel && !destination.value) { option(destination, saver.panel, saver.panel); destination.value = saver.panel; }
        }
        function saverFields() {
            after.parentNode.hidden = saverSelect.value !== 'on';
            after.required = saverSelect.value === 'on';
            if (destination) { destination.parentNode.hidden = saverSelect.value !== 'on'; }
        }
        saverSelect.onchange = saverFields;
        saverFields();
        form.appendChild(node('p', mode === 'panel' ? 'Эти настройки сохраняются в конфигурации панели. Тема и колонки над предпросмотром меняют только просмотр.' :
            'Служебные разделы не входят в обычные вкладки ESP32. Сейчас первый положительный интервал задаёт и запуск, и смену разделов заставки.'));
        var submit = node('button', 'Сохранить в черновике');
        submit.type = 'submit';
        form.appendChild(submit);
        rememberSettings();
    }

    ['panel', 'section'].forEach(function (mode) {
        byId(mode + '-settings').onclick = function () {
            if (busy || !leaveEdits()) { return; }
            showSettings(mode);
            openInspector(this);
        };
    });

    byId('settings-form').onsubmit = function (event) {
        event.preventDefault();
        var fields = {};
        var controls = {};
        Array.prototype.forEach.call(this.querySelectorAll('[data-setting]'), function (el) {
            var key = el.getAttribute('data-setting');
            controls[key] = el.value;
            if (key.indexOf('saver_') === 0 || el.value === settingsInitial[key]) { return; }
            fields[key] = el.value === '' ? null : (key === 'cols' ? Number(el.value) :
                (['hidden', 'tabs', 'accordion', 'swipe'].indexOf(key) >= 0 ? el.value === 'true' : el.value));
        });
        var saverChanged = ['saver_mode', 'saver_after', 'saver_panel'].some(function (key) { return controls[key] !== settingsInitial[key]; });
        if (saverChanged) {
            if (controls.saver_mode === '') { fields.screensaver = null; }
            else if (controls.saver_mode === 'off') { fields.screensaver = 0; }
            else if (inspectorMode === 'section') { fields.screensaver = controls.saver_after; }
            else {
                fields.screensaver = {after: controls.saver_after};
                if (controls.saver_panel !== settingsInitial.saver_panel) { fields.screensaver.panel = controls.saver_panel || null; }
            }
        }
        if (Object.keys(fields).length) { operate('update_' + inspectorMode, {fields: fields}); }
        else { status('Настройки не изменились.'); }
    };

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

    function invalidatePreview() {
        previewToken += 1;
        padToken += 1;
        if (previewRequest) { previewRequest.abort(); }
        if (padRequest) { padRequest.abort(); }
    }

    function image(path, id, message, token) {
        var target = byId(id);
        var feedback = byId(message);
        var isPanel = id === 'preview-image';
        feedback.textContent = 'Загрузка SVG…';
        return request('GET', draftPath(path), null, function (error, svg) {
            if (token !== (isPanel ? previewToken : padToken)) { return; }
            if (error || typeof svg !== 'string') {
                feedback.textContent = 'Предпросмотр: ' + (error ? error.message : 'Получен некорректный SVG');
                if (isPanel) {
                    byId('refresh-preview').hidden = false;
                    byId('layout').hidden = !target.hidden;
                }
                if (error) { markIssue(error); }
                return;
            }
            if (!isPanel && !padOpen) { return; }
            // Decode away from the document: the previous preview keeps its height until ready.
            var ready = new Image();
            ready.onload = function () {
                if (token !== (isPanel ? previewToken : padToken)) { return; }
                target.src = ready.src;
                target.hidden = false;
                if (isPanel) { byId('layout').hidden = true; byId('refresh-preview').hidden = true; }
                feedback.textContent = '';
            };
            ready.onerror = function () {
                if (token !== (isPanel ? previewToken : padToken)) { return; }
                feedback.textContent = 'Не удалось показать SVG.';
                if (isPanel) { byId('refresh-preview').hidden = false; }
            };
            ready.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
        });
    }

    function previewQuery() {
        var query = '?draft_version=' + model.draft_version + '&theme=' + encodeURIComponent(byId('theme').value);
        var cols = byId('cols');
        if (cols.value && !cols.validity.valid) {
            byId('preview-status').textContent = 'Введите целое число колонок от 1 до 12.';
            return null;
        }
        if (cols.value) { query += '&cols=' + encodeURIComponent(cols.value); }
        return query;
    }

    function refreshPreview() {
        if (!model || !selectedPanel() || busy) { return; }
        var query = previewQuery();
        if (!query) { return; }
        var current = selectedSection();
        if (current && current.outside_sections) { query += '&root_tiles=1'; }
        var layout = empty('layout');
        layout.hidden = !byId('preview-image').hidden;
        layout.appendChild(node('h3', 'Схема плиток'));
        showTileList(layout, selectedPanel(), []);
        if (previewRequest) { previewRequest.abort(); }
        previewRequest = image('/preview/' + encodeURIComponent(panel) + '.svg' + query + '&mode=list',
                               'preview-image', 'preview-status', ++previewToken);
    }

    function refreshPad() {
        padToken += 1;
        if (padRequest) { padRequest.abort(); }
        byId('pad').textContent = padOpen ? 'Скрыть пульт' : 'Показать пульт';
        if (!padOpen) { byId('pad-image').hidden = true; byId('pad-status').textContent = ''; return; }
        var query = previewQuery();
        if (query) {
            padRequest = image('/tile/' + encodeURIComponent(panel) + '/' + encodeURIComponent(selected) + '.svg' + query,
                               'pad-image', 'pad-status', padToken);
        }
    }

    function run(method, path, data, success, failure) {
        if (busy) { return; }
        busy = true;
        var focused = document.activeElement;
        var locked = Array.prototype.map.call(document.querySelectorAll('#workspace input, #workspace select, #workspace button, #actions button'), function (el) {
            var entry = [el, el.disabled]; el.disabled = true; return entry;
        });
        byId('workspace').setAttribute('aria-busy', 'true');
        request(method, path, data, function (error, result) {
            busy = false;
            locked.forEach(function (entry) { entry[0].disabled = entry[1]; });
            if (focused && document.contains(focused)) { focused.focus({preventScroll: true}); }
            byId('workspace').setAttribute('aria-busy', 'false');
            if (error) {
                errorMessage(error);
                if (failure) { failure(error); }
            } else { success(result); }
        });
    }

    function newDraft(published) {
        if (busy) { return; }
        invalidatePreview();
        closeInspector(true);
        inspectorMode = 'tile';
        var retry = byId('retry-start');
        function showRetry() { retry.hidden = false; }
        retry.hidden = true;
        status(published === true ? 'Опубликовано. Открывается следующий черновик…' : 'Создаётся черновик текущей конфигурации…');
        empty('details').hidden = true;
        run('POST', 'drafts', {}, function (result) {
            model = result;
            issue = null;
            panel = panel || initialPanel;
            byId('legacy').hidden = true;
            byId('actions').hidden = false;
            showModel();
            if (published === true) { status('Опубликовано. Можно продолжать редактирование в новом черновике.'); }
        }, function (error) {
            if (error.code === 'legacy') {
                byId('legacy').hidden = false;
                byId('actions').hidden = true;
                byId('workspace').hidden = true;
                status('Редактор станет доступен после миграции YAML.');
            } else {
                if (published === true) { status('Опубликовано. Новый черновик открыть не удалось. ' + error.message, true); }
                showRetry();
            }
        });
    }

    byId('retry-start').onclick = newDraft;

    function operate(op, fields) {
        var current = selectedSection();
        var settings = op === 'update_panel' || op === 'update_section';
        if ((!settings && (!current || current.readonly)) || !model || busy) { return; }
        status('Сохранение изменений в черновике…');
        fields.op = op;
        fields.panel = panel;
        fields.section = op === 'update_panel' ? null : section;
        invalidatePreview();
        run('POST', draftPath('/operation?result=panel'), {draft_version: model.draft_version, operation: fields}, function (result) {
            var updated = result.panels[0];
            model.panels = model.panels.map(function (p) { return p.id === updated.id ? updated : p; });
            model.draft_version = result.draft_version;
            model.checked = result.checked;
            issue = null;
            if (panel === fields.panel) {
                byId('panels').options[byId('panels').selectedIndex].textContent = updated.title + ' · ' + updated.id;
                var part = selectedSection();
                if (part) { byId('sections').options[byId('sections').selectedIndex].textContent = part.title + (part.readonly ? ' · только чтение' : ''); }
                if (op === 'add_instance') { byId('new-id').value = ''; search(); }
                syncInstances();
                if (op === 'remove_instance' && selected === fields.id) { selected = null; closeInspector(true); showInstance(); }
                if (settings) { rememberSettings(); }
                if (op === 'update_instance') {
                    if (fields.fields.title === '') { byId('inherit-title').hidden = true; }
                }
            }
            empty('details').hidden = true;
            byId('preview-status').textContent = 'Изменения сохранены. Предпросмотр показывает предыдущую версию.';
            byId('refresh-preview').hidden = false;
            if (padOpen) { byId('pad-status').textContent = 'Изменения сохранены. Откройте пульт заново для обновления.'; }
            markIssue(null);
            progress();
            byId('edit-status').textContent = 'Сохранено в черновике.';
        }, function () {
            if (model) { byId('refresh-preview').hidden = false; }
        });
    }

    function changeStructure(operation, form) {
        if (busy || !model || !leaveEdits()) { return; }
        byId('structure-status').textContent = 'Сохранение в черновике…';
        run('POST', draftPath('/operation'), {draft_version: model.draft_version, operation: operation}, function (result) {
            if (operation.op === 'assign_screen') {
                model.screens = result.screens;
                model.checked = result.checked;
                model.draft_version = result.draft_version;
                var names = empty('screen-names');
                model.screens.forEach(function (entry) { if (entry.name) { option(names, entry.name, entry.panel); } });
                byId('screen-list').textContent = 'Назначенные экраны: ' + model.screens.filter(function (entry) {
                    return entry.panel === panel;
                }).map(function (entry) { return entry.name || 'Имя скрыто'; }).join(', ');
            } else {
                invalidatePreview(); closeInspector(true); inspectorMode = 'tile'; padOpen = false;
                issue = null; selected = null; section = null; model = result;
                if (operation.op === 'add_panel') { panel = operation.panel; }
                if (operation.op === 'add_section') {
                    var changed = model.panels.filter(function (p) { return p.id === panel; })[0];
                    section = changed.sections[changed.sections.length - 1].section;
                }
                byId('preview-image').hidden = true;
                showModel();
            }
            if (form) { form.reset(); }
            empty('details').hidden = true; progress();
            byId('structure-status').textContent = 'Сохранено в черновике. Для применения нажмите «Опубликовать».';
        }, function (error) { byId('structure-status').textContent = error.message; });
    }

    byId('create-panel').onsubmit = function (event) {
        event.preventDefault();
        changeStructure({op: 'add_panel', panel: this.elements.key.value.trim(), title: this.elements.caption.value.trim(), section: null}, this);
    };
    byId('remove-panel').onclick = function () {
        if (window.confirm('Удалить панель «' + selectedPanel().title + '» вместе со всеми её разделами и плитками из черновика?')) {
            changeStructure({op: 'remove_panel', panel: panel, section: null});
        }
    };
    byId('create-section').onsubmit = function (event) {
        event.preventDefault();
        var parts = selectedPanel().sections;
        if (parts.length === 1 && parts[0].section == null && parts[0].tiles.length &&
            !window.confirm('Существующие плитки панели перейдут в первый раздел. Продолжить?')) { return; }
        changeStructure({op: 'add_section', panel: panel, section: null, title: this.elements.caption.value.trim()}, this);
    };
    byId('remove-section').onclick = function () {
        if (section != null && window.confirm('Удалить раздел «' + selectedSection().title + '» вместе с его плитками из черновика?')) {
            changeStructure({op: 'remove_section', panel: panel, section: section});
        }
    };
    byId('assign-screen').onsubmit = function (event) {
        event.preventDefault();
        changeStructure({op: 'assign_screen', panel: panel, section: null, screen: this.elements.screen.value.trim()}, this);
    };

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
    byId('panels').onchange = function () {
        var next = byId('panels').value;
        byId('panels').value = panel;
        if (busy || !leaveEdits()) { return; }
        byId('panels').value = next;
        closeInspector(true); inspectorMode = 'tile'; section = null; selected = null;
        byId('preview-image').hidden = true; showPanel();
    };
    byId('sections').onchange = function () {
        var next = byId('sections').selectedIndex;
        selectedPanel().sections.forEach(function (s, i) { if (s.section === section) { byId('sections').selectedIndex = i; } });
        if (busy || !leaveEdits()) { return; }
        byId('sections').selectedIndex = next;
        closeInspector(true); inspectorMode = 'tile'; selected = null; showSection();
    };
    byId('search').oninput = search;
    byId('theme').onchange = function () { refreshPreview(true); };
    byId('cols').onchange = function () { refreshPreview(true); };
    byId('refresh-preview').onclick = function () { refreshPreview(true); };
    byId('pad').onclick = function () {
        padOpen = !padOpen;
        refreshPad();
    };
    byId('add').onclick = function () {
        var source = byId('elements').value;
        var fields = {element: source};
        if (model.inline_tiles && source.indexOf('copy:') === 0) { fields = {element: source.slice(5)}; }
        var id = byId('new-id').value.trim();
        if (model.inline_tiles && id) { fields.id = id; }
        operate('add_instance', fields);
    };
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
        else { status('Поля плитки не изменились.'); }
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
            var list = node('ul', '');
            diff.operations.forEach(function (op) {
                list.appendChild(node('li', operationNames[op.op] + ' · ' + op.panel + ' · ' +
                                      (op.element || op.id || '') + (op.file ? ' · ' + op.file : '') + (op.fields ? ' · ' + op.fields.join(', ') : '')));
            });
            target.appendChild(list);
            progress();
            byId('publish').disabled = !s.writes && !s.deletes;
        });
    };
    byId('publish').onclick = function () {
        if (!model || busy) { return; }
        function confirmPublish() {
            if (!window.confirm('Применить проверенные изменения на рабочих панелях?')) { progress(); return; }
            status('Публикация изменений…');
            previewToken += 1;
            function continueEditing(revision) {
                empty('details').hidden = true;
                receivedRevision(revision, 'Опубликовано');
                newDraft(true);
            }
            var receipt = draftPath('/publication');
            run('POST', draftPath('/publish'), {draft_version: model.draft_version}, continueEditing, function (error) {
                if (error.code && error.code !== 'editor_failure') { return; }
                status('Ответ о публикации не получен. Проверяется результат…');
                busy = true;
                function checkResult() {
                    request('GET', receipt, null, function (problem, result) {
                        if (!problem && result.pending) {
                            window.setTimeout(checkResult, 1500);
                            return;
                        }
                        busy = false;
                        if (!problem && result.revision) { continueEditing(result.revision); }
                        else { status('Публикация не подтверждена. Откройте историю и проверьте рабочую панель.', true); }
                    });
                }
                checkResult();
            });
        }
        if (model.checked) { confirmPublish(); return; }
        status('Проверка черновика перед публикацией…');
        run('GET', draftPath('/diff'), null, function (diff) {
            if (!diff.summary.writes && !diff.summary.deletes) {
                status('Черновик совпадает с рабочей конфигурацией.');
                byId('publish').disabled = true;
                return;
            }
            model.checked = model.draft_version === diff.draft_version;
            if (model.checked) { confirmPublish(); }
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
                    var remove = node('button', 'Удалить ревизию');
                    remove.onclick = function () {
                        if (!window.confirm('Удалить снимок ' + item.id + '? Откат к нему станет недоступен.')) { return; }
                        run('POST', 'revisions/delete', {revision: item.id, base_revision: history.head, confirm: true}, function () {
                            status('Ревизия удалена.');
                            byId('history').click();
                        });
                    };
                    row.appendChild(remove);
                }
                target.appendChild(row);
            });
        });
    };
    newDraft();

}());
