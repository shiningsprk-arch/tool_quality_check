/* 质量体检前端：范围选择 → 检查项勾选 → 启动 → 进度 → 报告（按书 / 按检查项）。
 *
 * 纯静态、无构建步骤；宿主能力只通过 toolbox-bridge（fetch / theme / locale / notify）使用。
 * 报告由后端分页提供（整库体检结果可能几 MB，筛选与分页都走 /report 的参数），
 * "按检查项"视图走 /summary（后端已按检查项聚合好，一次请求即可）。
 *
 * 结构约定：事件回调只改 `state`，然后调用 `render([parts])` 把状态画到 DOM；
 * 需要局部重画时传 parts（例如清单过滤框只重画清单，避免整页重排）。
 */
(function () {
  'use strict';

  var bridge = window.MyBooksToolBridge;
  var i18n = window.MyBooksToolI18n.create();

  var PREFS_KEY = 'qc.ui';
  var ALL_PARTS = ['scope', 'checks', 'presets', 'run', 'report'];

  var state = {
    // 范围
    scope: 'all',
    picked: [],
    // 检查项
    checks: [],
    selected: {},
    filter: '',
    collapsed: {},
    presets: {},
    // 运行
    running: false,
    stopping: false,
    startedAt: 0,
    lastProgress: null,
    pollTimer: null,
    tickTimer: null,
    notFound: 0,
    // 报告
    report: null,
    summary: null,
    view: 'books',
    page: 0,
    pageSize: 50,
    filteredTotal: 0,
    filteredIssues: 0,
    expanded: {},
    focus: null,
    reportAbort: null
  };

  // ---------------------------------------------------------------- 基础工具

  function $(id) { return document.getElementById(id); }

  function t(key, params) { return i18n.t(key, params); }

  function api(path, options) {
    if (!bridge) return Promise.reject(new Error('toolbox bridge 未加载'));
    return bridge.fetch(path, options).then(function (rsp) {
      if (rsp && rsp.err && rsp.err !== 'ok') {
        var err = new Error(rsp.msg || rsp.err);
        err.code = rsp.err;
        err.data = rsp.data;
        throw err;
      }
      return rsp;
    });
  }

  function notify(message, level) {
    if (bridge && bridge.notify) bridge.notify(message, level || 'info');
  }

  function showAlert(text, level) {
    var box = $('alert');
    if (!text) { box.hidden = true; return; }
    box.textContent = text;
    box.className = 'qc-alert ' + (level || 'error');
    box.hidden = false;
  }

  function applyTheme() {
    document.body.setAttribute('data-theme', (bridge && bridge.theme) || 'light');
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function bookLink(bookId, title) {
    var a = el('a', null, title || ('#' + bookId));
    a.href = '/book/' + bookId;
    a.target = '_blank';
    a.rel = 'noopener';
    return a;
  }

  // 语言无关的时长格式：不足一小时 M:SS，超过 H:MM:SS。
  function formatDuration(ms) {
    if (!ms || ms < 0) return '—';
    var total = Math.round(ms / 1000);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return h ? (h + ':' + pad(m) + ':' + pad(s)) : (m + ':' + pad(s));
  }

  // 掉线/后端不认 signal 时也要能跑：AbortController 缺失就退化成"不管旧响应"。
  function newAbort() {
    return (typeof window.AbortController === 'function') ? new AbortController() : null;
  }

  // ---------------------------------------------------------------- 本地偏好

  function loadPrefs() {
    var raw = null;
    try { raw = window.localStorage.getItem(PREFS_KEY); } catch (e) { raw = null; }
    var prefs = {};
    if (raw) { try { prefs = JSON.parse(raw) || {}; } catch (e) { prefs = {}; } }
    state.collapsed = prefs.collapsed || {};
    state.presets = prefs.presets || {};
    state.view = prefs.view === 'checks' ? 'checks' : 'books';
    if (prefs.pageSize) state.pageSize = prefs.pageSize;
  }

  function savePrefs() {
    try {
      window.localStorage.setItem(PREFS_KEY, JSON.stringify({
        collapsed: state.collapsed,
        presets: state.presets,
        view: state.view,
        pageSize: state.pageSize
      }));
    } catch (e) { /* 隐私模式等场景下写不了，忽略即可 */ }
  }

  // ---------------------------------------------------------------- 检查项

  var GROUP_ORDER = ['epub', 'mobi', 'covers', 'metadata', 'missing'];
  var GROUP_LABEL_KEY = {
    epub: 'group.epub', mobi: 'group.mobi', covers: 'group.covers',
    metadata: 'group.metadata', missing: 'group.missing'
  };

  // 与后端 driver._SEVERITY_OVERRIDES 对齐的"排版/杂项"子集，用于预设按钮。
  var STYLE_CHECKS = [
    'check_epub_unused_css', 'check_epub_unused_images', 'check_epub_broken_images',
    'check_epub_fonts', 'check_epub_font_faces', 'check_epub_javascript',
    'check_epub_css_justify', 'check_epub_css_margins', 'check_epub_css_no_margins',
    'check_epub_inline_margins', 'check_epub_xpgt', 'check_epub_inline_xpgt',
    'check_epub_smarten_punc', 'check_epub_address', 'check_epub_html_size',
    'check_epub_jacket', 'check_epub_legacy_jacket', 'check_epub_multi_jacket',
    'check_epub_no_jacket', 'check_epub_svg_cover', 'check_epub_no_svg_cover',
    'check_epub_repl_cover', 'check_epub_no_repl_cover', 'check_epub_converted',
    'check_epub_not_converted', 'check_epub_itunes', 'check_epub_bookmark',
    'check_epub_os_artifacts', 'check_epub_toc_hierarchy', 'check_epub_toc_size'
  ];

  // 检查项名称：优先用本工具的翻译（键就是检查项的 key），没有则回退后端给的英文名。
  function checkName(check) {
    var translated = t(check.key);
    return (translated && translated !== check.key) ? translated : (check.name || check.key);
  }

  // 噪声项（后端标 noisy）为什么"命中的书特别多"：优先用本工具的翻译，回退后端的英文说明。
  // 传入的既可能是注册表条目（key）也可能是报告里的问题条目（check）。
  function noisyReason(item) {
    if (!item || !item.noisy) return '';
    var key = item.key || item.check;
    if (!key) return item.noisy_reason || '';
    var translated = t('noisy.' + key);
    return (translated && translated !== 'noisy.' + key)
      ? translated : (item.noisy_reason || '');
  }

  function noisyBadge(reason) {
    var badge = el('span', 'qc-sev noisy-tag', t('checks.noisyTag'));
    badge.title = reason;
    return badge;
  }

  function findCheck(key) {
    for (var i = 0; i < state.checks.length; i++) {
      if (state.checks[i].key === key) return state.checks[i];
    }
    return null;
  }

  function checkLabelText(key) {
    var check = findCheck(key);
    return check ? checkName(check) : key;
  }

  function matchesFilter(check) {
    if (!state.filter) return true;
    var hay = (check.key + ' ' + (check.name || '') + ' ' + (check.tooltip || '') +
               ' ' + checkName(check)).toLowerCase();
    return hay.indexOf(state.filter) !== -1;
  }

  function renderChecklist() {
    var host = $('checks-groups');
    host.innerHTML = '';
    var byGroup = {};
    var shown = 0;
    state.checks.forEach(function (check) {
      if (!matchesFilter(check)) return;
      (byGroup[check.cat] = byGroup[check.cat] || []).push(check);
      shown++;
    });
    if (!shown) {
      host.appendChild(el('div', 'qc-empty', t('checks.filterEmpty')));
      updateCount();
      return;
    }

    var allCollapsed = true;
    GROUP_ORDER.forEach(function (cat) {
      var items = byGroup[cat];
      if (!items || !items.length) return;
      var collapsed = !!state.collapsed[cat];
      if (!collapsed) allCollapsed = false;

      var box = el('div', 'qc-group');
      // 组头整行可点（折叠/展开），但里面还要放"全选/清空"，所以用 div+role 而不是 button
      // ——按钮里嵌按钮是不合法的 HTML。
      var head = el('div', 'qc-group-head');
      head.setAttribute('role', 'button');
      head.tabIndex = 0;
      head.setAttribute('aria-expanded', String(!collapsed));
      head.appendChild(el('span', 'qc-caret', collapsed ? '▸' : '▾'));
      head.appendChild(el('span', 'qc-group-title', t(GROUP_LABEL_KEY[cat] || cat)));

      var selectable = items.filter(function (c) { return c.supported; });
      var picked = items.filter(function (c) { return !!state.selected[c.key]; }).length;
      head.appendChild(el('span', 'qc-sub', t('checks.groupSel', { n: picked, total: items.length })));

      var allPicked = selectable.length > 0 && selectable.every(function (c) {
        return !!state.selected[c.key];
      });
      var toggle = el('button', 'qc-group-toggle',
        allPicked ? t('checks.presetNone') : t('checks.presetAll'));
      toggle.type = 'button';
      toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        selectable.forEach(function (check) {
          if (allPicked) delete state.selected[check.key];
          else state.selected[check.key] = true;
        });
        syncCoverOption();
        updateCount();
        renderChecklist();
      });
      head.appendChild(toggle);

      function toggleCollapse() {
        state.collapsed[cat] = !state.collapsed[cat];
        savePrefs();
        renderChecklist();
      }
      head.addEventListener('click', toggleCollapse);
      head.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleCollapse(); }
      });
      box.appendChild(head);

      var body = el('div', 'qc-group-body');
      body.hidden = collapsed;
      items.forEach(function (check) { body.appendChild(checkRow(check)); });
      box.appendChild(body);
      host.appendChild(box);
    });

    $('checks-toggle-all').textContent = allCollapsed ? t('checks.expandAll') : t('checks.collapseAll');
    updateCount();
  }

  function checkRow(check) {
    var row = el('div', 'qc-check' + (check.supported ? '' : ' unsupported'));
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.id = 'chk-' + check.key;
    box.checked = !!state.selected[check.key];
    box.disabled = !check.supported;
    box.addEventListener('change', function () {
      if (box.checked) state.selected[check.key] = true;
      else delete state.selected[check.key];
      if (check.key === 'check_covers') syncCoverOption();
      updateCount();
      renderChecklistCounts();
    });
    var label = document.createElement('label');
    label.htmlFor = box.id;
    label.textContent = checkName(check);
    if (check.tooltip) label.title = check.tooltip;
    row.appendChild(box);
    row.appendChild(label);
    if (!check.supported) {
      var tag = el('span', 'qc-sev unsupported-tag', t('checks.unsupportedTag'));
      tag.title = t('checks.unsupportedWhy', { why: check.unsupported_reason || '' });
      row.appendChild(tag);
    } else {
      row.appendChild(el('span', 'qc-sev ' + check.severity, t('severity.' + check.severity)));
      if (check.noisy) row.appendChild(noisyBadge(noisyReason(check)));
    }
    return row;
  }

  // 勾选变化时只更新组头计数，避免整份清单重建（输入焦点/滚动位置都不受影响）。
  function renderChecklistCounts() {
    Array.prototype.slice.call($('checks-groups').querySelectorAll('.qc-group')).forEach(function (group) {
      var inputs = group.querySelectorAll('input[type=checkbox]');
      var picked = 0;
      Array.prototype.forEach.call(inputs, function (input) { if (input.checked) picked++; });
      var counter = group.querySelector('.qc-group-head .qc-sub');
      if (counter) counter.textContent = t('checks.groupSel', { n: picked, total: inputs.length });
    });
    updateCount();
  }

  function setSelected(keys) {
    state.selected = {};
    (keys || []).forEach(function (k) { state.selected[k] = true; });
    state.checks.forEach(function (check) {
      var box = $('chk-' + check.key);
      if (box) box.checked = !!state.selected[check.key];
    });
    // 预设也会勾上"封面检查"，那它的判定方式就该跟着可编辑（否则勾了却不生效）。
    syncCoverOption();
    updateCount();
    renderChecklistCounts();
  }

  function supportedKeys() {
    return state.checks.filter(function (c) { return c.supported; }).map(function (c) { return c.key; });
  }

  function keysForPreset(name) {
    if (name === 'none') return [];
    if (name === 'all') return supportedKeys();
    // 推荐 = 全部支持的检查项减去"对 MyBooks 库必然大范围命中"的那些（后端标 noisy），
    // 因为把噪声一起跑出来会把真正的问题淹掉。噪声项仍可在清单里单独勾选。
    if (name === 'recommended') {
      return state.checks.filter(function (c) {
        return c.supported && !c.noisy;
      }).map(function (c) { return c.key; });
    }
    if (name === 'structure') {
      return state.checks.filter(function (c) {
        return c.supported && (c.cat === 'epub' || c.cat === 'mobi') && STYLE_CHECKS.indexOf(c.key) === -1;
      }).map(function (c) { return c.key; });
    }
    if (name === 'metadata') {
      return state.checks.filter(function (c) {
        return c.supported && (c.cat === 'metadata' || c.cat === 'missing' || c.cat === 'covers');
      }).map(function (c) { return c.key; });
    }
    if (name === 'style') {
      return state.checks.filter(function (c) {
        return c.supported && STYLE_CHECKS.indexOf(c.key) !== -1;
      }).map(function (c) { return c.key; });
    }
    return null;
  }

  function applyPreset(name) {
    var keys = keysForPreset(name);
    if (keys) setSelected(keys);
  }

  function renderPresets() {
    var select = $('preset-select');
    var current = select.value;
    select.innerHTML = '';
    var none = document.createElement('option');
    none.value = '';
    none.textContent = t('checks.savedPresets');
    select.appendChild(none);
    Object.keys(state.presets).sort().forEach(function (name) {
      var opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name + ' (' + (state.presets[name] || []).length + ')';
      select.appendChild(opt);
    });
    select.value = current;
  }

  function savePreset() {
    var name = ($('preset-name').value || '').trim() || $('preset-select').value;
    if (!name) { showAlert(t('checks.needName')); return; }
    state.presets[name] = Object.keys(state.selected);
    savePrefs();
    showAlert('');
    renderPresets();
    $('preset-select').value = name;
    notify(t('checks.presetSaved', { name: name }), 'success');
  }

  function deletePreset() {
    var name = $('preset-select').value;
    if (!name) return;
    delete state.presets[name];
    savePrefs();
    renderPresets();
    notify(t('checks.presetDeleted', { name: name }), 'info');
  }

  function toggleAllGroups() {
    var anyExpanded = GROUP_ORDER.some(function (cat) { return !state.collapsed[cat]; });
    GROUP_ORDER.forEach(function (cat) { state.collapsed[cat] = anyExpanded; });
    savePrefs();
    renderChecklist();
  }

  function updateCount() {
    $('checks-count').textContent = t('checks.selected', { n: Object.keys(state.selected).length });
  }

  // ---------------------------------------------------------------- 范围

  function renderScope() {
    Array.prototype.forEach.call($('scope-seg').children, function (btn) {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-scope') === state.scope));
    });
    $('picked-panel').hidden = state.scope !== 'picked';
    $('query-panel').hidden = state.scope !== 'query';
    $('scope-hint').textContent = t('scope.hint.' + state.scope);
  }

  function bookRow(book) {
    var row = el('label', 'qc-book');
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = state.picked.some(function (b) { return b.id === book.id; });
    cb.addEventListener('change', function () {
      if (cb.checked) {
        if (!state.picked.some(function (b) { return b.id === book.id; })) state.picked.push(book);
      } else {
        state.picked = state.picked.filter(function (b) { return b.id !== book.id; });
      }
      renderPicked();
    });
    var img = document.createElement('img');
    img.src = book.thumb || '';
    img.alt = '';
    var info = el('div');
    info.appendChild(el('div', 't', book.title));
    info.appendChild(el('div', 'a', book.author || ''));
    row.appendChild(cb);
    row.appendChild(img);
    row.appendChild(info);
    return row;
  }

  function searchBooks() {
    var q = $('book-search').value.trim();
    $('book-results').textContent = t('scope.searching');
    api('books?limit=40&q=' + encodeURIComponent(q)).then(function (rsp) {
      var books = (rsp.data && rsp.data.books) || [];
      var host = $('book-results');
      host.innerHTML = '';
      if (!books.length) { host.textContent = t('scope.noBooks'); return; }
      books.forEach(function (book) { host.appendChild(bookRow(book)); });
    }).catch(function (err) {
      $('book-results').textContent = err.message;
    });
  }

  function renderPicked() {
    var host = $('picked-chips');
    host.innerHTML = '';
    if (!state.picked.length) return;
    state.picked.forEach(function (book) {
      var chip = el('span', 'qc-chip', book.title);
      var x = el('button', null, ' ×');
      x.type = 'button';
      x.addEventListener('click', function () {
        state.picked = state.picked.filter(function (b) { return b.id !== book.id; });
        renderPicked();
        searchBooks();
      });
      chip.appendChild(x);
      host.appendChild(chip);
    });
    var clear = el('button', 'qc-btn', t('scope.clear'));
    clear.type = 'button';
    clear.addEventListener('click', function () {
      state.picked = [];
      renderPicked();
      searchBooks();
    });
    host.appendChild(clear);
  }

  // ---------------------------------------------------------------- 体检参数

  var COVER_ROWS = {
    dimensions: ['cover-dims-row'],
    file_size: ['cover-file-row'],
    aspect: ['cover-aspect-row'],
    no_cover: []
  };
  var COVER_NUMBER_INPUTS = ['cover-w', 'cover-h', 'cover-kb', 'aspect-x', 'aspect-y', 'aspect-tol'];

  // 判定方式决定哪几个输入框有意义：尺寸用宽高、文件大小用 KB、宽高比用比例±容差，
  // "缺封面"不需要数值（它走宿主搜索 cover:False）。
  function applyCoverEnabled() {
    var on = $('opt-cover').checked;
    var mode = $('cover-mode').value;
    $('cover-mode').disabled = !on;
    $('cover-operator').disabled = !on || mode === 'no_cover';
    Object.keys(COVER_ROWS).forEach(function (key) {
      var show = on && key === mode;
      COVER_ROWS[key].forEach(function (id) { $(id).hidden = !show; });
    });
    COVER_NUMBER_INPUTS.forEach(function (id) { $(id).disabled = !on; });
  }

  // "封面检查"这一项与下方的判定方式选项是同一件事的两处开关：让它们互相跟随，
  // 免得出现"选项开着但检查项没勾"（看不出为什么没跑）这类状态。
  function syncCoverOption() {
    var box = $('chk-check_covers');
    $('opt-cover').checked = !!(box && box.checked);
    applyCoverEnabled();
  }

  function coverOptions() {
    if (!$('opt-cover').checked) return { mode: 'none' };
    var mode = $('cover-mode').value;
    var opts = { mode: mode, operator: $('cover-operator').value };
    if (mode === 'file_size') {
      opts.file_size = parseInt($('cover-kb').value, 10) || 0;
    } else if (mode === 'dimensions') {
      opts.image_width = parseInt($('cover-w').value, 10) || 0;
      opts.image_height = parseInt($('cover-h').value, 10) || 0;
    } else if (mode === 'aspect') {
      opts.aspect_x = parseFloat($('aspect-x').value) || 2;
      opts.aspect_y = parseFloat($('aspect-y').value) || 3;
      opts.aspect_tolerance_pct = parseFloat($('aspect-tol').value) || 0;
    }
    return opts;
  }

  function options() {
    return {
      qc: { maxTags: parseInt($('opt-maxtags').value, 10) || 5 },
      cover: coverOptions()
    };
  }

  function payload() {
    var body = {
      scope: state.scope,
      book_ids: state.picked.map(function (b) { return b.id; }),
      query: $('query-input').value,
      checks: Object.keys(state.selected),
      options: options()
    };
    if (state.scope === 'picked' && state.picked.length === 1) body.scope = 'single';
    return body;
  }

  // ---------------------------------------------------------------- 运行与进度

  function renderRunBar() {
    $('run-btn').disabled = state.running;
    $('cancel-btn').disabled = !state.running || state.stopping;
    $('cancel-btn').textContent = state.stopping ? t('run.stopping') : t('run.cancel');
  }

  function renderProgress() {
    var data = state.lastProgress;
    var fill = $('progress-fill');
    var checkLine = $('run-check');
    var bookLine = $('run-book');
    var timeLine = $('run-time');
    checkLine.textContent = '';
    bookLine.textContent = '';
    timeLine.textContent = '';

    if (!data) { fill.style.width = '0%'; return; }
    fill.style.width = (data.status === 'completed' ? 100 : (data.progress || 0)) + '%';
    if (data.status !== 'running') return;

    if (data.check_index && data.check_total) {
      checkLine.textContent = t('run.checkNow', {
        n: data.check_index, total: data.check_total, name: data.check_name || ''
      });
    }
    if (data.current_id) {
      bookLine.appendChild(document.createTextNode(t('run.currentBook', { title: '' }) + ' '));
      bookLine.appendChild(bookLink(data.current_id, data.current_title || ('#' + data.current_id)));
    }
    if (state.startedAt) {
      var elapsed = Date.now() - state.startedAt;
      var doneChecks = Math.max(0, (data.check_index || 1) - 1);
      var eta = '';
      if (doneChecks > 0 && data.check_total) {
        var avg = elapsed / doneChecks;
        var remaining = Math.max(0, (data.check_total - doneChecks + 1)) * avg;
        eta = t('run.eta', { time: formatDuration(remaining) });
      } else {
        eta = t('run.etaUnknown');
      }
      timeLine.textContent = t('run.elapsed', { time: formatDuration(elapsed) }) + ' · ' + eta;
    }
  }

  function startTicker() {
    stopTicker();
    state.tickTimer = setInterval(renderProgress, 1000);
  }

  function stopTicker() {
    if (state.tickTimer) clearInterval(state.tickTimer);
    state.tickTimer = null;
  }

  function setRunning(running) {
    state.running = running;
    if (running) {
      state.startedAt = state.startedAt || Date.now();
      startTicker();
    } else {
      state.stopping = false;
      stopTicker();
    }
    renderRunBar();
  }

  function start() {
    var keys = Object.keys(state.selected);
    if (!keys.length) return showAlert(t('error.noChecks'));
    if (state.scope === 'picked' && !state.picked.length) return showAlert(t('error.noBooks'));
    showAlert('');
    state.notFound = 0;
    state.startedAt = Date.now();
    state.lastProgress = null;
    setRunning(true);
    $('run-status').textContent = t('run.starting');
    renderProgress();
    api('start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload())
    }).then(function (rsp) {
      var total = (rsp.data && rsp.data.total) || 0;
      $('run-status').textContent = t('run.started', { total: total });
      poll();
    }).catch(function (err) {
      state.startedAt = 0;
      setRunning(false);
      showAlert(t('error.' + (err.code || 'unknown'), { msg: err.message }));
    });
  }

  function cancel() {
    state.stopping = true;
    renderRunBar();
    api('cancel', { method: 'POST' }).then(function () {
      notify(t('run.cancelRequested'), 'info');
    }).catch(function (err) {
      state.stopping = false;
      renderRunBar();
      showAlert(err.message);
    });
  }

  // 轮询用"响应回来再排下一次"，避免慢响应叠加。
  function poll() {
    stopPolling();
    fetchProgress();
  }

  function schedulePoll() {
    stopPolling();
    state.pollTimer = setTimeout(fetchProgress, 2000);
  }

  function stopPolling() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  function fetchProgress() {
    api('progress').then(function (rsp) {
      var data = rsp.data || {};
      state.lastProgress = data;
      if (data.status === 'running') {
        if (!state.running) setRunning(true);
        renderProgress();
        schedulePoll();
        return;
      }
      setRunning(false);
      state.lastProgress = data;
      var cancelledSuffix = data.cancelled ? ' · ' + t('run.cancelled') : '';
      if (data.status === 'failed') {
        showAlert(rsp.msg || t('error.task.failed'));
        return;
      }
      $('run-status').textContent = t('run.done') + cancelledSuffix;
      renderProgress();
      loadReport(0);
    }).catch(function (err) {
      if (err.code === 'task.not_found') {
        // 任务记录还没写出来，给几次机会
        state.notFound++;
        if (state.notFound <= 5) { schedulePoll(); return; }
        state.notFound = 0;
        setRunning(false);
        showAlert(err.message);
        return;
      }
      setRunning(false);
      showAlert(t('error.' + (err.code || 'unknown'), { msg: err.message }));
    });
  }

  // 进页面时把上一次任务的状态捡回来：报告本来就在任务工作目录里躺着，刷新一下不该让它消失。
  function restore() {
    return api('progress').then(function (rsp) {
      var data = rsp.data || {};
      state.lastProgress = data;
      if (data.status === 'running') {
        state.startedAt = state.startedAt || Date.now();
        setRunning(true);
        renderProgress();
        poll();
        return;
      }
      if (data.status === 'completed') {
        $('run-status').textContent = t('run.done') + (data.cancelled ? ' · ' + t('run.cancelled') : '');
        renderProgress();
        loadReport(0);
      }
    }).catch(function () {
      // 还没有任何任务（task.not_found）——保持初始状态。
      return null;
    });
  }

  // ---------------------------------------------------------------- 报告

  function reportUrl(offset) {
    return 'report?offset=' + offset + '&limit=' + state.pageSize +
      '&severity=' + encodeURIComponent($('filter-severity').value) +
      '&check=' + encodeURIComponent($('filter-check').value);
  }

  function loadReport(offset) {
    if (state.reportAbort) state.reportAbort.abort();
    var ctrl = newAbort();
    state.reportAbort = ctrl;
    state.page = Math.max(0, Math.floor(offset / state.pageSize));
    return api(reportUrl(offset), ctrl ? { signal: ctrl.signal } : undefined).then(function (rsp) {
      state.reportAbort = null;
      state.report = rsp.data || {};
      state.filteredTotal = state.report.filtered_total || 0;
      state.filteredIssues = state.report.filtered_issues || 0;
      renderReport();
      loadSummary();
    }).catch(function (err) {
      state.reportAbort = null;
      if (err && (err.name === 'AbortError' || err.code === 'AbortError')) return;
      showAlert(t('error.' + (err.code || 'unknown'), { msg: err.message }));
    });
  }

  function loadSummary() {
    return api('summary').then(function (rsp) {
      state.summary = rsp.data || {};
      if (state.view === 'checks') renderReport();
    }).catch(function () {
      state.summary = null;
      return null;
    });
  }

  function setView(view) {
    state.view = view;
    savePrefs();
    renderReport();
    if (view === 'checks' && !state.summary) loadSummary();
  }

  function renderReport() {
    if (!state.report) return;
    $('report-card').hidden = false;

    Array.prototype.forEach.call($('view-seg').children, function (btn) {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-view') === state.view));
    });
    $('books-view').hidden = state.view !== 'books';
    $('checks-view').hidden = state.view !== 'checks';

    renderStats();
    renderNotes();
    renderErrors();
    if (state.view === 'books') renderBooksView();
    else renderChecksView();
  }

  function renderStats() {
    var data = state.report;
    var stats = $('report-stats');
    stats.innerHTML = '';
    [['report.statBooks', data.total_books],
     ['report.statBooksWithIssues', data.books_with_issues],
     ['report.statIssues', data.issues_total]].forEach(function (pair) {
      stats.appendChild(statBox(pair[1] == null ? '-' : pair[1], t(pair[0])));
    });
    var counts = data.severity_counts || {};
    ['error', 'warn', 'info'].forEach(function (sev) {
      stats.appendChild(statBox(counts[sev] || 0, t('severity.' + sev)));
    });
  }

  function statBox(value, label) {
    var box = el('div');
    box.appendChild(el('b', null, String(value)));
    box.appendChild(el('div', 'qc-sub', label));
    return box;
  }

  function renderNotes() {
    var data = state.report;
    var notes = [];
    if (data.scope_label) notes.push(t('report.scope', { label: data.scope_label }));
    if (data.generated_at) notes.push(data.generated_at);
    Object.keys(data.skipped || {}).forEach(function (key) {
      notes.push(t('report.skipped', { check: checkLabelText(key), why: data.skipped[key] }));
    });
    $('report-notes').textContent = notes.join(' · ');
  }

  function renderErrors() {
    var errors = (state.report && state.report.errors) || [];
    var panel = $('errors-panel');
    panel.hidden = !errors.length;
    if (!errors.length) { $('errors-list').innerHTML = ''; return; }
    $('errors-summary').textContent = t('report.errorsTitle', { n: errors.length });
    var list = $('errors-list');
    list.innerHTML = '';
    errors.forEach(function (err) {
      var li = el('li');
      var where = (err.book_id ? '#' + err.book_id + ' ' : '') + (err.check || '');
      li.textContent = where ? (where + ' — ' + (err.error || '')) : (err.error || '');
      list.appendChild(li);
    });
  }

  function renderBooksView() {
    renderCheckFilter();
    renderRows();
  }

  function renderCheckFilter() {
    var summary = (state.report && state.report.summary) || {};
    var select = $('filter-check');
    var current = select.value;
    select.innerHTML = '';
    var all = document.createElement('option');
    all.value = '';
    all.textContent = t('report.allChecks');
    select.appendChild(all);
    Object.keys(summary).sort(function (a, b) { return summary[b] - summary[a]; })
      .forEach(function (key) {
        var opt = document.createElement('option');
        opt.value = key;
        opt.textContent = checkLabelText(key) + ' (' + summary[key] + ')';
        select.appendChild(opt);
      });
    select.value = current || '';
  }

  function rowExpandable(book) {
    return !!(book && book.issues && book.issues.length);
  }

  function renderRows() {
    var host = $('report-body');
    host.innerHTML = '';
    var books = (state.report && state.report.books) || [];
    var filtering = !!($('filter-severity').value || $('filter-check').value);

    if (!books.length) {
      var tr = el('tr');
      var td = el('td', 'qc-empty', filtering ? t('report.empty') : t('report.emptyClean'));
      td.colSpan = 3;
      tr.appendChild(td);
      host.appendChild(tr);
    }

    books.forEach(function (book) {
      var tr = el('tr', 'issue-row');
      tr.tabIndex = 0;
      tr.setAttribute('role', 'button');
      tr.setAttribute('aria-expanded', String(!!state.expanded[book.book_id]));

      var tdBook = el('td');
      tdBook.setAttribute('data-label', t('report.colBook'));
      var titleBtn = el('button', 'qc-title-btn', book.title || ('#' + book.book_id));
      titleBtn.type = 'button';
      titleBtn.addEventListener('click', function (e) {
        e.stopPropagation();  // 打开弹层，别顺带把整行也展开/收起
        openFocus(book);
      });
      tdBook.appendChild(titleBtn);
      var open = bookLink(book.book_id, '↗');
      open.className = 'qc-open-book';
      open.title = t('report.openBook');
      open.addEventListener('click', function (e) { e.stopPropagation(); });
      tdBook.appendChild(open);
      tdBook.appendChild(el('div', 'qc-sub', book.author || ''));

      var tdFormats = el('td', null, (book.formats || []).join(', '));
      tdFormats.setAttribute('data-label', t('report.colFormats'));

      var tdIssues = el('td');
      tdIssues.setAttribute('data-label', t('report.colIssues'));
      (book.issues || []).forEach(function (issue) {
        var line = el('div', 'qc-issue');
        line.appendChild(el('span', 'qc-sev ' + issue.severity, t('severity.' + issue.severity)));
        line.appendChild(el('code', null, checkLabelText(issue.check)));
        if (issue.noisy) line.appendChild(noisyBadge(noisyReason(issue)));
        tdIssues.appendChild(line);
      });

      tr.appendChild(tdBook);
      tr.appendChild(tdFormats);
      tr.appendChild(tdIssues);
      host.appendChild(tr);

      // 展开行：把每条问题对应的日志明细摊开（上游有逐条日志的检查才有内容）
      var detailTr = el('tr', 'detail');
      detailTr.hidden = !state.expanded[book.book_id];
      var detailTd = el('td');
      detailTd.colSpan = 3;
      (book.issues || []).forEach(function (issue) {
        detailTd.appendChild(el('div', null, checkLabelText(issue.check)));
        if (issue.noisy) detailTd.appendChild(el('div', 'qc-note', noisyReason(issue)));
        if (issue.detail && issue.detail.length) {
          var ul = el('ul', 'qc-detail');
          issue.detail.forEach(function (line) { ul.appendChild(el('li', null, line)); });
          detailTd.appendChild(ul);
        } else {
          detailTd.appendChild(el('div', 'qc-note', t('report.noDetail')));
        }
      });
      detailTr.appendChild(detailTd);
      host.appendChild(detailTr);

      function toggle() {
        state.expanded[book.book_id] = !state.expanded[book.book_id];
        detailTr.hidden = !state.expanded[book.book_id];
        tr.setAttribute('aria-expanded', String(!!state.expanded[book.book_id]));
      }
      tr.addEventListener('click', toggle);
      tr.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    });

    var from = state.filteredTotal ? state.page * state.pageSize + 1 : 0;
    var to = Math.min((state.page + 1) * state.pageSize, state.filteredTotal);
    $('page-info').textContent = t('report.page', { from: from, to: to, total: state.filteredTotal });
    $('report-count').textContent = t('report.matched', {
      n: state.filteredTotal,
      issues: state.filteredIssues || 0
    });
    $('prev-page').disabled = state.page <= 0;
    $('next-page').disabled = to >= state.filteredTotal;
    $('page-size').value = String(state.pageSize);
  }

  function renderChecksView() {
    var body = $('checks-body');
    body.innerHTML = '';
    var data = state.summary;
    if (!data) {
      var tr = el('tr');
      var td = el('td', 'qc-empty', t('scope.searching'));
      td.colSpan = 5;
      tr.appendChild(td);
      body.appendChild(tr);
      $('summary-line').textContent = '';
      return;
    }
    $('summary-line').textContent = t('report.summaryLine', {
      checks: data.checks_with_hits || 0,
      books: data.books_with_issues || 0
    });
    var rows = data.checks || [];
    if (!rows.length) {
      var emptyTr = el('tr');
      var emptyTd = el('td', 'qc-empty', t('report.emptyClean'));
      emptyTd.colSpan = 5;
      emptyTr.appendChild(emptyTd);
      body.appendChild(emptyTr);
      return;
    }
    rows.forEach(function (row) {
      var tr = el('tr');
      var nameCell = tdWithLabel(checkLabelText(row.check), t('report.colCheck'));
      if (row.noisy) nameCell.appendChild(noisyBadge(noisyReason(row)));
      tr.appendChild(nameCell);
      var sev = el('td');
      sev.setAttribute('data-label', t('report.colSeverity'));
      sev.appendChild(el('span', 'qc-sev ' + row.severity, t('severity.' + row.severity)));
      tr.appendChild(sev);
      tr.appendChild(tdWithLabel(String(row.books), t('report.colBooksHit')));
      tr.appendChild(tdWithLabel(String(row.detail_lines), t('report.colDetailLines')));

      var sample = el('td');
      sample.setAttribute('data-label', t('report.colSample'));
      (row.sample || []).forEach(function (book, index) {
        if (index) sample.appendChild(document.createTextNode('、'));
        sample.appendChild(bookLink(book.book_id, book.title || ('#' + book.book_id)));
      });
      var only = el('button', 'qc-btn qc-btn-sm', t('report.filterByCheck'));
      only.type = 'button';
      only.addEventListener('click', function () {
        setView('books');
        $('filter-check').value = row.check;
        loadReport(0);
      });
      sample.appendChild(only);
      tr.appendChild(sample);
      body.appendChild(tr);
    });
  }

  function tdWithLabel(text, label) {
    var td = el('td', null, text);
    td.setAttribute('data-label', label);
    return td;
  }

  // ---------------------------------------------------------------- 单本聚焦

  function openFocus(book) {
    state.focus = book;
    $('focus-title').textContent = book.title || ('#' + book.book_id);
    $('focus-count').textContent = t('report.focusIssues', { n: (book.issues || []).length });
    $('focus-open-book').href = '/book/' + book.book_id;
    var body = $('focus-body');
    body.innerHTML = '';
    (book.issues || []).forEach(function (issue) {
      var block = el('div', 'qc-focus-issue');
      var head = el('div');
      head.appendChild(el('span', 'qc-sev ' + issue.severity, t('severity.' + issue.severity)));
      head.appendChild(el('code', null, checkLabelText(issue.check)));
      if (issue.noisy) head.appendChild(noisyBadge(noisyReason(issue)));
      block.appendChild(head);
      if (issue.noisy) block.appendChild(el('div', 'qc-note', noisyReason(issue)));
      if (issue.detail && issue.detail.length) {
        var ul = el('ul', 'qc-detail');
        issue.detail.forEach(function (line) { ul.appendChild(el('li', null, line)); });
        block.appendChild(ul);
      } else {
        block.appendChild(el('div', 'qc-note', t('report.noDetail')));
      }
      body.appendChild(block);
    });
    $('focus-overlay').hidden = false;
    $('focus-close').focus();
  }

  function closeFocus() {
    state.focus = null;
    $('focus-overlay').hidden = true;
  }

  // ---------------------------------------------------------------- 导出

  function exportCsv() {
    var severity = $('filter-severity').value;
    var checkKey = $('filter-check').value;
    var rows = [['book_id', 'title', 'author', 'formats', 'severity', 'check', 'check_name', 'detail']];
    var collected = [];

    function page(offset) {
      return api('report?offset=' + offset + '&limit=500&severity=' +
        encodeURIComponent(severity) + '&check=' + encodeURIComponent(checkKey))
        .then(function (rsp) {
          var data = rsp.data || {};
          collected = collected.concat(data.books || []);
          if (collected.length < (data.filtered_total || 0) && collected.length < 20000) {
            return page(offset + 500);
          }
          return null;
        });
    }

    page(0).then(function () {
      collected.forEach(function (book) {
        (book.issues || []).forEach(function (issue) {
          rows.push([
            book.book_id,
            book.title || '',
            book.author || '',
            (book.formats || []).join(' '),
            issue.severity,
            issue.check,
            checkLabelText(issue.check),
            (issue.detail || []).join(' | ')
          ]);
        });
      });
      var csv = rows.map(function (row) {
        return row.map(function (cell) {
          return '"' + String(cell == null ? '' : cell).replace(/"/g, '""') + '"';
        }).join(',');
      }).join('\r\n');
      var blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'quality_check.csv';
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
    }).catch(function (err) {
      showAlert(err.message);
    });
  }

  // ---------------------------------------------------------------- 渲染入口

  function render(parts) {
    var todo = parts || ALL_PARTS;
    if (todo.indexOf('scope') >= 0) { renderScope(); renderPicked(); }
    if (todo.indexOf('checks') >= 0) renderChecklist();
    if (todo.indexOf('presets') >= 0) renderPresets();
    if (todo.indexOf('run') >= 0) { renderRunBar(); renderProgress(); }
    if (todo.indexOf('report') >= 0) renderReport();
  }

  // ---------------------------------------------------------------- 绑定

  function bind() {
    Array.prototype.forEach.call($('scope-seg').children, function (btn) {
      btn.addEventListener('click', function () {
        state.scope = btn.getAttribute('data-scope');
        render(['scope']);
      });
    });
    $('book-search-btn').addEventListener('click', searchBooks);
    $('book-search').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') searchBooks();
    });

    $('checks-filter').addEventListener('input', function () {
      state.filter = this.value.trim().toLowerCase();
      renderChecklist();
    });
    $('checks-toggle-all').addEventListener('click', toggleAllGroups);
    Array.prototype.forEach.call(document.querySelectorAll('[data-preset]'), function (btn) {
      btn.addEventListener('click', function () { applyPreset(btn.getAttribute('data-preset')); });
    });
    $('preset-select').addEventListener('change', function () {
      var name = this.value;
      if (name && state.presets[name]) setSelected(state.presets[name]);
    });
    $('preset-save').addEventListener('click', savePreset);
    $('preset-delete').addEventListener('click', deletePreset);

    $('opt-cover').addEventListener('change', function () {
      var on = $('opt-cover').checked;
      var box = $('chk-check_covers');
      if (box) box.checked = on;
      if (on) state.selected['check_covers'] = true;
      else delete state.selected['check_covers'];
      applyCoverEnabled();
      updateCount();
      renderChecklistCounts();
    });
    $('cover-mode').addEventListener('change', applyCoverEnabled);

    $('run-btn').addEventListener('click', start);
    $('cancel-btn').addEventListener('click', cancel);
    $('export-btn').addEventListener('click', exportCsv);

    Array.prototype.forEach.call($('view-seg').children, function (btn) {
      btn.addEventListener('click', function () { setView(btn.getAttribute('data-view')); });
    });
    $('filter-severity').addEventListener('change', function () { loadReport(0); });
    $('filter-check').addEventListener('change', function () { loadReport(0); });
    $('page-size').addEventListener('change', function () {
      state.pageSize = parseInt(this.value, 10) || 50;
      savePrefs();
      loadReport(0);
    });
    $('prev-page').addEventListener('click', function () {
      loadReport(Math.max(0, (state.page - 1) * state.pageSize));
    });
    $('next-page').addEventListener('click', function () {
      loadReport((state.page + 1) * state.pageSize);
    });

    $('focus-close').addEventListener('click', closeFocus);
    $('focus-overlay').addEventListener('click', function (e) {
      if (e.target === $('focus-overlay')) closeFocus();
    });

    // 键盘：/ 聚焦搜索框、Esc 关弹层（列表行的 Enter/空格在行上处理）
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !$('focus-overlay').hidden) { closeFocus(); return; }
      if (e.key !== '/' || e.ctrlKey || e.metaKey) return;
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      e.preventDefault();
      if (state.scope === 'picked') $('book-search').focus();
      else $('checks-filter').focus();
    });

    if (bridge && bridge.onThemeChange) bridge.onThemeChange(applyTheme);
    window.addEventListener('resize', function () { /* 移动端布局交给 CSS，无需重画 */ });
  }

  function init() {
    loadPrefs();
    applyTheme();
    bind();
    $('opt-cover').checked = false;
    $('page-size').value = String(state.pageSize);
    applyCoverEnabled();
    render(['scope', 'run']);

    i18n.ready.then(function () {
      document.title = t('app.title');
      i18n.applyDom(document);
      document.documentElement.lang = i18n.locale();
      render(['scope']);
      loadChecks().then(restore).catch(function (err) {
        showAlert(t('error.loadChecks', { msg: err.message }));
      });
    });
    i18n.onChange(function () {
      i18n.applyDom(document);
      render();
    });
  }

  function loadChecks() {
    return api('checks').then(function (rsp) {
      state.checks = (rsp.data && rsp.data.checks) || [];
      // 默认勾"推荐"而不是"全选"：全选会把必然大范围命中的噪声项一起跑，报告淹没在噪声里。
      applyPreset('recommended');
      render(['checks', 'presets']);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
