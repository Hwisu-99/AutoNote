/* AI Inspector — 요소를 클릭하면 DevTools 스타일의 구조화된 정보(선택자, DOM 경로,
   속성, computed style, outerHTML)를 클립보드에 복사합니다. LLM에게 붙여넣어
   "이 요소를 이렇게 고쳐줘" 라고 정확히 지시할 때 쓰는 용도입니다.

   켜기/끄기: 우하단 원형 버튼 클릭, 또는 Alt+Shift+C. Esc로 인스펙트 모드 종료.
   의존성 없는 단일 파일 — 다른 스크립트 뒤에 추가:
     <script src="/inspector.js"></script>
*/
(function () {
  'use strict';

  var UI_MARK = 'data-ai-inspector-ui';
  var active = false;
  var hoverBox, hoverLabel, toggleBtn, toast, panel;
  var pickCount = 0;
  var toastTimer = null;

  var STYLE_ID = 'ai-inspector-styles';
  var STYLE = [
    '[' + UI_MARK + '] { box-sizing: border-box; }',
    '#ai-inspector-toggle {',
    '  position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;',
    '  width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;',
    '  background: var(--surface, #fff); color: var(--text-muted, #6B6A65);',
    '  box-shadow: 0 1px 2px rgba(20,20,19,.08), 0 6px 16px rgba(20,20,19,.12);',
    '  display: flex; align-items: center; justify-content: center;',
    '  transition: transform .15s ease, background .15s ease, color .15s ease;',
    '}',
    '#ai-inspector-toggle:hover { transform: translateY(-1px); }',
    '#ai-inspector-toggle.active { background: var(--accent, #D97757); color: #fff; }',
    '#ai-inspector-hoverbox {',
    '  position: fixed; z-index: 2147483001; pointer-events: none;',
    '  border: 1.5px solid var(--accent, #D97757); background: rgba(217, 119, 87, .12);',
    '  border-radius: 3px; display: none;',
    '}',
    '#ai-inspector-hoverlabel {',
    '  position: fixed; z-index: 2147483002; pointer-events: none; display: none;',
    '  background: #141413; color: #F5F4ED; font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;',
    '  padding: 3px 7px; border-radius: 4px; white-space: nowrap;',
    '  box-shadow: 0 4px 12px rgba(0,0,0,.25);',
    '}',
    '#ai-inspector-toast {',
    '  position: fixed; left: 50%; bottom: 76px; transform: translateX(-50%) translateY(8px);',
    '  z-index: 2147483003; background: #141413; color: #F5F4ED;',
    '  font: 600 12.5px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", sans-serif;',
    '  padding: 9px 14px; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.25);',
    '  opacity: 0; transition: opacity .15s ease, transform .15s ease; pointer-events: none;',
    '  max-width: 360px; text-align: center;',
    '}',
    '#ai-inspector-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }',
    '#ai-inspector-panel {',
    '  position: fixed; right: 20px; bottom: 72px; z-index: 2147483000;',
    '  width: 240px; max-height: 40vh; overflow: auto;',
    '  background: var(--surface, #fff); border: 1px solid var(--border, #E8E6DC);',
    '  border-radius: 10px; box-shadow: 0 1px 2px rgba(20,20,19,.06), 0 8px 24px rgba(20,20,19,.12);',
    '  font: 12px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "Pretendard", sans-serif;',
    '  color: var(--text, #141413); display: none;',
    '}',
    '#ai-inspector-panel.show { display: block; }',
    '#ai-inspector-panel .aip-head {',
    '  padding: 8px 10px; font-weight: 700; font-size: 10.5px; letter-spacing: .04em; text-transform: uppercase;',
    '  color: var(--text-muted, #6B6A65); border-bottom: 1px solid var(--border, #E8E6DC);',
    '}',
    '#ai-inspector-panel .aip-row {',
    '  padding: 7px 10px; border-bottom: 1px solid var(--border, #E8E6DC); cursor: pointer;',
    '}',
    '#ai-inspector-panel .aip-row:hover { background: var(--accent-soft, #FBE9E1); }',
    '#ai-inspector-panel .aip-row:last-child { border-bottom: none; }',
    '#ai-inspector-panel .aip-sel { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; word-break: break-all; }',
    '#ai-inspector-panel .aip-hint { color: var(--text-muted, #6B6A65); font-size: 10px; margin-top: 2px; }'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function el(tag, attrs, parent) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    node.setAttribute(UI_MARK, '1');
    if (parent) parent.appendChild(node);
    return node;
  }

  function buildUi() {
    toggleBtn = el('button', { id: 'ai-inspector-toggle', type: 'button', title: 'AI Inspector 켜기/끄기 (Alt+Shift+C)' }, document.body);
    toggleBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/></svg>';
    toggleBtn.addEventListener('click', function () { setActive(!active); });

    hoverBox = el('div', { id: 'ai-inspector-hoverbox' }, document.body);
    hoverLabel = el('div', { id: 'ai-inspector-hoverlabel' }, document.body);
    toast = el('div', { id: 'ai-inspector-toast' }, document.body);
    panel = el('div', { id: 'ai-inspector-panel' }, document.body);
    panel.innerHTML = '<div class="aip-head" ' + UI_MARK + '="1">최근 캡처</div>';
  }

  function isOwnUi(node) {
    return !!(node && node.nodeType === 1 && node.closest && node.closest('[' + UI_MARK + ']'));
  }

  function setActive(next) {
    active = next;
    toggleBtn.classList.toggle('active', active);
    if (active) {
      document.addEventListener('mousemove', onMouseMove, true);
      document.addEventListener('click', onClick, true);
      document.addEventListener('keydown', onEscKeyDown, true);
      showToastMsg('AI Inspector 켜짐 — 요소를 클릭하면 정보가 복사됩니다 (Esc로 종료)');
    } else {
      document.removeEventListener('mousemove', onMouseMove, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onEscKeyDown, true);
      hoverBox.style.display = 'none';
      hoverLabel.style.display = 'none';
    }
  }

  function onEscKeyDown(e) {
    if (e.key === 'Escape') setActive(false);
  }

  function getClassStr(elm) {
    return (elm.getAttribute && elm.getAttribute('class')) || '';
  }

  function shortDesc(elm) {
    var s = elm.tagName.toLowerCase();
    if (elm.id) s += '#' + elm.id;
    var cls = getClassStr(elm).trim().split(/\s+/).filter(Boolean);
    if (cls.length) s += '.' + cls.slice(0, 3).join('.');
    return s;
  }

  function onMouseMove(e) {
    var target = e.target;
    if (isOwnUi(target)) { hoverBox.style.display = 'none'; hoverLabel.style.display = 'none'; return; }
    var r = target.getBoundingClientRect();
    hoverBox.style.display = 'block';
    hoverBox.style.left = r.left + 'px';
    hoverBox.style.top = r.top + 'px';
    hoverBox.style.width = r.width + 'px';
    hoverBox.style.height = r.height + 'px';

    hoverLabel.style.display = 'block';
    hoverLabel.textContent = shortDesc(target) + '  ' + Math.round(r.width) + '×' + Math.round(r.height);
    var lx = Math.min(e.clientX + 14, window.innerWidth - 220);
    var ly = (r.top > 28) ? r.top - 24 : r.bottom + 6;
    hoverLabel.style.left = lx + 'px';
    hoverLabel.style.top = ly + 'px';
  }

  function onClick(e) {
    var target = e.target;
    if (isOwnUi(target)) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    capture(target);
  }

  // ---- capture helpers ----

  function cssEscape(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([^\w-])/g, '\\$1');
  }

  function buildSelector(elm) {
    if (elm.id) return '#' + cssEscape(elm.id);
    var parts = [];
    var node = elm;
    while (node && node.nodeType === 1 && node !== document.body && node !== document.documentElement) {
      if (node.id) { parts.unshift('#' + cssEscape(node.id)); break; }
      var part = node.tagName.toLowerCase();
      var parent = node.parentElement;
      if (parent) {
        var siblings = Array.prototype.filter.call(parent.children, function (c) { return c.tagName === node.tagName; });
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  function buildBreadcrumb(elm) {
    var parts = [];
    var node = elm;
    while (node && node.nodeType === 1) {
      var part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + node.id;
        parts.unshift(part);
        break; // id가 있는 조상이 나오면 거기서부터만 보여줘도 충분
      }
      var cls = getClassStr(node).trim().split(/\s+/).filter(Boolean);
      if (cls.length) part += '.' + cls.slice(0, 2).join('.');
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  var STYLE_KEYS = [
    'display', 'position', 'top', 'right', 'bottom', 'left',
    'width', 'height', 'margin', 'padding',
    'flexDirection', 'justifyContent', 'alignItems', 'gap',
    'gridTemplateColumns', 'gridTemplateRows',
    'color', 'backgroundColor', 'border', 'borderRadius',
    'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
    'boxShadow', 'opacity', 'zIndex', 'overflow', 'cursor', 'transition'
  ];

  function toKebab(k) { return k.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); }); }

  function collectComputedStyle(elm) {
    var cs = window.getComputedStyle(elm);
    var lines = [];
    STYLE_KEYS.forEach(function (k) {
      var v = cs[k];
      if (v === undefined || v === '') return;
      lines.push('  ' + toKebab(k) + ': ' + v);
    });
    return lines.join('\n');
  }

  function collectAttributes(elm) {
    var attrs = Array.prototype.filter.call(elm.attributes, function (a) { return a.name !== UI_MARK; })
      .map(function (a) { return a.name + '="' + a.value + '"'; })
      .join(', ');
    return attrs || '(없음)';
  }

  function truncate(str, max) {
    if (!str) return '';
    str = str.trim();
    return str.length > max ? str.slice(0, max) + '…' : str;
  }

  function collectOuterHtml(elm, max) {
    var html = elm.outerHTML || '';
    if (html.length > max) {
      return html.slice(0, max) + '\n…(총 ' + html.length + '자 중 ' + max + '자만 표시, 잘림)';
    }
    return html;
  }

  function formatSnapshot(elm) {
    var r = elm.getBoundingClientRect();
    var cls = getClassStr(elm).trim();
    var text = elm.innerText !== undefined ? elm.innerText : elm.textContent;
    var lines = [];
    lines.push('🔍 Inspected Element');
    lines.push('Tag: <' + elm.tagName.toLowerCase() + (elm.id ? ' id="' + elm.id + '"' : '') + '>');
    lines.push('Selector: ' + buildSelector(elm));
    lines.push('DOM Path: ' + buildBreadcrumb(elm));
    lines.push('Classes: ' + (cls || '(없음)'));
    lines.push('Attributes: ' + collectAttributes(elm));
    lines.push('Text: "' + truncate((text || '').replace(/\s+/g, ' '), 200) + '"');
    lines.push('Rect: ' + Math.round(r.width) + '×' + Math.round(r.height) + ' at (' + Math.round(r.left) + ', ' + Math.round(r.top) + ')');
    lines.push('Computed Style:');
    lines.push(collectComputedStyle(elm));
    lines.push('Outer HTML:');
    lines.push(collectOuterHtml(elm, 1500));
    return lines.join('\n');
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute(UI_MARK, '1');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { document.body.removeChild(ta); }
    return Promise.resolve();
  }

  function showToastMsg(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 2200);
  }

  function addToPanel(selector, text) {
    pickCount += 1;
    panel.classList.add('show');
    var row = el('div', { 'class': 'aip-row' }, panel);
    row.innerHTML = '<div class="aip-sel">' + selector.replace(/</g, '&lt;') + '</div><div class="aip-hint">#' + pickCount + ' · 클릭해서 다시 복사</div>';
    row.addEventListener('click', function () { copyText(text); showToastMsg('다시 복사됨: ' + selector); });
    // 패널이 너무 길어지지 않도록 최근 8개만 유지
    var rows = panel.querySelectorAll('.aip-row');
    if (rows.length > 8) rows[0].remove();
  }

  function capture(elm) {
    var snapshot = formatSnapshot(elm);
    var selector = buildSelector(elm);
    copyText(snapshot).then(function () {
      showToastMsg('✅ 복사됨: ' + selector);
      addToPanel(selector, snapshot);
    }).catch(function () {
      showToastMsg('복사 실패 — 브라우저 권한을 확인하세요');
    });
  }

  function onGlobalKeyDown(e) {
    if (e.altKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
      e.preventDefault();
      setActive(!active);
    }
  }

  function init() {
    injectStyle();
    buildUi();
    document.addEventListener('keydown', onGlobalKeyDown);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
