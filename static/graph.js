// AutoNote 그래프 뷰: Obsidian 그래프 뷰와 같은 로직(노트=노드, [[위키링크]]/공통 tag=에지)으로
// /api/graph 결과를 d3-force로 렌더링한다. index.html/papers.js에서
// `loadGraph(titleSlug, onlyFocus)`만 호출하면 되도록 전역 함수로 노출한다.

const graphSvg = d3.select('#graphSvg');
const graphTitleEl = document.getElementById('graphTitle');
const btnFullGraph = document.getElementById('btnFullGraph');
const toggleTagsInput = document.getElementById('toggleTags');
const toggleExpandInput = document.getElementById('toggleExpand');
const graphColumnEl = document.querySelector('.graph-column');
const graphPanelEl = document.getElementById('graphPanel');
let simulation = null;
let currentFocus = null;
let currentGraphData = null;
let hideTagNodes = !toggleTagsInput.checked;

btnFullGraph.addEventListener('click', () => loadGraph(null, false));
toggleTagsInput.addEventListener('change', () => {
  hideTagNodes = !toggleTagsInput.checked;
  if (currentGraphData) renderGraph(currentGraphData);
});

// 확대 시 사이드바/업로드 영역을 숨기고, 그래프 박스가 그 세로 공간을 이어받도록
// 높이를 고정값으로 잡아준다. 요약 카드가 떠 있으면 "그래프 박스 + 요약 카드"가
// 차지하던 높이 그대로, 요약 카드가 없으면(전체 그래프 보기 등) 화면 아래쪽
// 여백까지 채우도록 뷰포트 기준으로 계산한다. 다시 누르면 원래 높이(620px,
// CSS 기본값)로 복귀한다.
toggleExpandInput.addEventListener('change', () => {
  if (toggleExpandInput.checked) {
    const graphSummaryEl = document.getElementById('graphSummary');
    const hasSummary = graphSummaryEl.innerHTML.trim() !== '';
    const expandedHeight = hasSummary
      ? graphColumnEl.getBoundingClientRect().height
      : window.innerHeight - graphPanelEl.getBoundingClientRect().top - 24;

    document.body.classList.add('graph-expanded');
    graphPanelEl.style.height = `${expandedHeight}px`;
  } else {
    document.body.classList.remove('graph-expanded');
    graphPanelEl.style.height = '';
  }
  if (currentGraphData) renderGraph(currentGraphData);
});

async function loadGraph(focusSlug, onlyFocus = false) {
  currentFocus = onlyFocus ? focusSlug : null;
  if (!onlyFocus) {
    document.getElementById('graphSummary').innerHTML = '';
  }

  const params = new URLSearchParams();
  if (focusSlug) params.set('focus', focusSlug);
  if (onlyFocus) params.set('only_focus', 'true');
  const qs = params.toString();

  let data;
  try {
    const res = await fetch(qs ? `/api/graph?${qs}` : '/api/graph');
    if (!res.ok) return;
    data = await res.json();
  } catch {
    return;
  }

  if (onlyFocus && data.focus) {
    const node = data.nodes.find((n) => n.id === data.focus);
    graphTitleEl.textContent = `${node ? node.label : data.focus} Knowledge Graph`;
  } else {
    graphTitleEl.textContent = 'Knowledge Graph';
  }

  currentGraphData = data;
  renderGraph(data);
}

function renderGraph(data) {
  const svgEl = document.getElementById('graphSvg');
  const width = svgEl.clientWidth || 360;
  const height = svgEl.clientHeight || 520;

  if (simulation) simulation.stop();
  graphSvg.selectAll('*').remove();
  graphSvg.attr('viewBox', [0, 0, width, height]);

  const g = graphSvg.append('g');
  graphSvg.call(
    d3.zoom()
      .scaleExtent([0.3, 3])
      // 팬 가능 범위를 캔버스 크기 기준으로 제한한다. 제한이 없으면 마우스처럼
      // 한 동작에 큰 픽셀 이동량이 들어오는 입력 장치에서 그래프 전체가 뷰포트
      // 밖으로 팬되어 "사라진 것처럼" 보이는 문제가 있었다(트랙패드는 이동
      // 거리가 작아 잘 드러나지 않았음).
      .translateExtent([[-width, -height], [width * 2, height * 2]])
      .on('zoom', (event) => g.attr('transform', event.transform))
  );

  const visibleNodes = hideTagNodes ? data.nodes.filter((n) => n.type !== 'tag') : data.nodes;
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = hideTagNodes
    ? data.edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
    : data.edges;

  const nodes = visibleNodes.map((n) => ({ ...n }));
  const links = visibleEdges.map((e) => ({ ...e }));

  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(70).strength(0.4))
    .force('charge', d3.forceManyBody().strength(-160))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(20));

  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', 'graph-link')
    .attr('stroke-width', 1.2);

  const node = g.append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', (d) => (d.type === 'note' ? 8 : 6))
    .attr('class', (d) => {
      // concept/entity는 node_store.py에 실제 md 파일이 있을 때만(node_slug) 클릭
      // 가능 - 아직 마이그레이션 안 된 노드는 흐리게 표시해 비활성 상태를 알린다.
      const unavailable = (d.type === 'concept' || d.type === 'entity') && !d.node_slug;
      return `node-${d.type}` + (unavailable ? ' node-disabled' : '');
    })
    .call(drag(simulation));

  const label = g.append('g')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .attr('class', 'graph-label')
    .attr('dx', 12)
    .attr('dy', 4)
    .text((d) => d.label);

  // 호버한 노드와 직접 연결된 노드/에지만 원래대로 두고 나머지는 흐리게 만든다
  // (Obsidian 그래프 뷰의 호버 강조와 동일한 방식).
  node
    .on('mouseenter', (event, d) => highlightNode(d.id))
    .on('mouseleave', clearHighlight);

  // d3.drag가 붙은 요소는 드래그 도중 마우스가 조금이라도 움직이면 그 뒤에 오는
  // 브라우저 native 'click' 이벤트를 d3-drag가 내부적으로 삼켜버린다(알려진
  // 동작). 그 native click에 기대는 대신, pointerdown/pointerup을 직접 붙여서
  // "누른 지점과 뗀 지점이 화면 픽셀 기준으로 충분히 가깝고 빠르게 끝났으면
  // 클릭"으로 판정한다 - 화면 좌표(clientX/Y)를 쓰므로 그래프를 확대/축소해도
  // 판정 기준이 흔들리지 않는다. Obsidian 그래프 뷰도 노드 클릭 시 같은 방식
  // (누른 위치 근처에서 뗐는지)으로 클릭과 드래그를 구분한다.
  let pointerDownAt = null;
  node
    .on('pointerdown', (event) => {
      pointerDownAt = { x: event.clientX, y: event.clientY, t: Date.now() };
    })
    .on('pointerup', (event, d) => {
      if (!pointerDownAt) return;
      const moved = Math.hypot(event.clientX - pointerDownAt.x, event.clientY - pointerDownAt.y);
      const elapsed = Date.now() - pointerDownAt.t;
      pointerDownAt = null;
      if (moved < 6 && elapsed < 600) handleNodeClick(d);
    });

  function highlightNode(hoveredId) {
    const connectedIds = new Set([hoveredId]);
    const connectedLinkIndexes = new Set();

    links.forEach((l, i) => {
      const sourceId = typeof l.source === 'object' ? l.source.id : l.source;
      const targetId = typeof l.target === 'object' ? l.target.id : l.target;
      if (sourceId === hoveredId || targetId === hoveredId) {
        connectedLinkIndexes.add(i);
        connectedIds.add(sourceId);
        connectedIds.add(targetId);
      }
    });

    node.classed('dimmed', (d) => !connectedIds.has(d.id));
    label.classed('dimmed', (d) => !connectedIds.has(d.id));
    link
      .classed('dimmed', (d, i) => !connectedLinkIndexes.has(i))
      .classed('highlighted', (d, i) => connectedLinkIndexes.has(i));
  }

  function clearHighlight() {
    node.classed('dimmed', false);
    label.classed('dimmed', false);
    link.classed('dimmed', false).classed('highlighted', false);
  }

  simulation.on('tick', () => {
    link
      .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    label.attr('x', (d) => d.x).attr('y', (d) => d.y);
  });
}

function drag(sim) {
  return d3.drag()
    .on('start', (event, d) => {
      if (!event.active) sim.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on('end', (event, d) => {
      if (!event.active) sim.alphaTarget(0);
      d.fx = null; d.fy = null;
    });
}

// 노드 클릭 시 그래프 화면을 그 노드의 md 노트 화면으로 전환한다(Obsidian에서
// 노드를 클릭해 노트로 들어가는 것과 같은 경험). note는 항상 vault에 md가 있어
// 바로 이동 가능하고, concept/entity는 node_store.py에 실제 노드 파일이 있을
// 때만(node_slug) 이동 가능 - 없는 쪽은 렌더링 단계에서 이미 클릭 비활성화 처리됨.
function handleNodeClick(d) {
  if (d.type === 'note') {
    openNodeView('note', d.id, d.label);
    return;
  }
  if ((d.type === 'concept' || d.type === 'entity') && d.node_slug) {
    openNodeView(d.type, d.node_slug, d.label);
  }
}

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// 노드 md 파일은 frontmatter + 간단한 마크다운(헤딩/목록/굵게/wikilink/구분선)
// 정도만 쓰므로, 별도 라이브러리 없이 그 범위만 직접 렌더링한다. PDF/LLM에서
// 추출된 텍스트가 들어있어 신뢰할 수 없는 입력이므로 escapeHtml을 먼저 거친 뒤에만
// 마크다운 문법에 해당하는 태그를 끼워넣는다(escape 전 원본에 직접 태그를 넣지 않음).
function attachmentUrl(path) {
  return /^https?:\/\//.test(path) || path.startsWith('/') ? path : `/${path}`;
}

function renderInline(text, links) {
  let html = escapeHtml(text);

  // 링크/이미지 변환을 순서대로 적용하면, 뒤 단계의 정규식이 앞 단계가 이미 만든
  // HTML 속성값 안의 URL 문자열까지 다시 매칭해버리는 문제가 있다(예: 방금 만든
  // <a href="https://..."> 안의 URL을 "본문 URL"로 착각해서 그 안에 또 <a>를
  // 끼워넣는 식). 그래서 변환된 조각은 최종 HTML을 바로 끼우지 않고 플레이스홀더
  // 토큰으로 임시 치환해뒀다가, 모든 정규식이 다 지나간 뒤 마지막에 한 번에
  // 되돌린다 - 이후 단계가 이전 단계의 결과물을 다시 건드릴 일이 없어진다.
  const stashed = [];
  const stash = (fragment) => {
    const token = `\uE000${stashed.length}\uE001`;
    stashed.push(fragment);
    return token;
  };

  html = html.replace(/\*\*(.+?)\*\*/g, (_, inner) => stash(`<strong>${inner}</strong>`));
  // 첨부 이미지: ![alt](경로) -> 즉시 인라인 렌더링(Obsidian 임베드와 같은 경험).
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, src) => {
    return stash(`<img class="md-img" src="${attachmentUrl(src)}" alt="${alt}">`);
  });
  // 마크다운 링크: [설명](https://...) -> 클릭하면 새 탭에서 그 주소로 이동.
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) => {
    return stash(`<a class="md-link" href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  });
  // [[wikilink]]: 백엔드가 미리 풀어준 links 맵에 그 대상이 있으면(같은 논문
  // slug이거나 node_store에 매칭되는 concept/entity) 실제로 클릭해서 이동
  // 가능한 노드로 만들고, 없으면(아직 매칭 안 되는 경우) 예전처럼 그냥 스타일만
  // 입힌 텍스트로 둔다.
  html = html.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, target, display) => {
    const label = display || target;
    const resolved = links?.[target.trim()];
    if (resolved) {
      return stash(
        `<span class="md-wikilink md-wikilink-clickable" data-node-type="${resolved.type}" data-node-slug="${escapeHtml(resolved.slug)}">${label}</span>`
      );
    }
    return stash(`<span class="md-wikilink">${label}</span>`);
  });
  // 마크다운 문법 없이 그냥 붙여넣은 맨 URL도 클릭 가능하게 만든다.
  html = html.replace(/(https?:\/\/[^\s<>()]+)/g, (url) => {
    return stash(`<a class="md-link" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  });

  return html.replace(/\uE000(\d+)\uE001/g, (_, i) => stashed[Number(i)]);
}

// 표 셀 구분자로 쓰이는 "|"만 나누고, obsidian_writer.py의 _table_cell()이 이스케이프해
// 리터럴 파이프로 심어둔 "\|"는 다시 "|"로 되돌린다.
function splitTableRow(line) {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|\s*$/, '');
  return trimmed.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'));
}

function isTableSeparatorRow(line) {
  return /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(line.trim());
}

function renderMarkdown(markdown, links = {}) {
  const parts = [];
  let listOpen = false;
  let bqOpen = false;
  let paragraph = [];
  let tableBuffer = [];
  let codeFenceLang = null;
  let codeLines = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      parts.push(`<p class="md-p">${renderInline(paragraph.join(' '), links)}</p>`);
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listOpen) { parts.push('</ul>'); listOpen = false; }
  };
  const closeBlockquote = () => {
    if (bqOpen) { parts.push('</blockquote>'); bqOpen = false; }
  };
  // 표는 최소 2줄(헤더 + 구분자 행)이 있어야 진짜 표로 인정한다. 구분자 행이 없으면
  // (본문에 우연히 "|"가 섞인 경우 등) 표로 오인하지 않고 원래대로 문단 취급한다.
  const flushTable = () => {
    if (!tableBuffer.length) return;
    if (tableBuffer.length >= 2 && isTableSeparatorRow(tableBuffer[1])) {
      const header = splitTableRow(tableBuffer[0]);
      const bodyRows = tableBuffer.slice(2).map(splitTableRow);
      parts.push(
        '<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
        header.map((h) => `<th>${renderInline(h, links)}</th>`).join('') +
        '</tr></thead><tbody>' +
        bodyRows.map((row) => '<tr>' + row.map((c) => `<td>${renderInline(c, links)}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>'
      );
    } else {
      parts.push(`<p class="md-p">${renderInline(tableBuffer.join(' '), links)}</p>`);
    }
    tableBuffer = [];
  };
  const flushAll = () => { flushParagraph(); closeList(); closeBlockquote(); flushTable(); };

  for (const rawLine of markdown.split('\n')) {
    const line = rawLine.trim();

    const fence = line.match(/^```\s*(\w*)\s*$/);
    if (codeFenceLang !== null) {
      if (fence) {
        const escaped = escapeHtml(codeLines.join('\n'));
        parts.push(
          codeFenceLang === 'mermaid'
            ? `<pre class="mermaid">${escaped}</pre>`
            : `<pre class="md-code"><code>${escaped}</code></pre>`
        );
        codeFenceLang = null;
        codeLines = [];
      } else {
        codeLines.push(rawLine);
      }
      continue;
    }
    if (fence) { flushAll(); codeFenceLang = fence[1] || 'text'; continue; }

    if (line.startsWith('<!--')) {
      flushAll();
      if (line.includes('user-notes')) parts.push('<div class="md-h3">개인 메모</div>');
      continue;
    }
    if (!line) { flushAll(); continue; }
    if (line === '---') { flushAll(); parts.push('<hr class="md-hr">'); continue; }

    const h3 = line.match(/^###\s+(.*)$/);
    if (h3) { flushAll(); parts.push(`<div class="md-h3">${renderInline(h3[1], links)}</div>`); continue; }
    const h2 = line.match(/^##\s+(.*)$/);
    if (h2) { flushAll(); parts.push(`<div class="md-h2">${renderInline(h2[1], links)}</div>`); continue; }
    const h1 = line.match(/^#\s+(.*)$/);
    if (h1) { flushAll(); parts.push(`<div class="md-h2">${renderInline(h1[1], links)}</div>`); continue; }

    if (line.startsWith('|') && line.endsWith('|')) {
      flushParagraph(); closeList(); closeBlockquote();
      tableBuffer.push(line);
      continue;
    }
    flushTable();

    const bq = line.match(/^>\s?(.*)$/);
    if (bq) {
      flushParagraph(); closeList();
      if (!bqOpen) { parts.push('<blockquote class="md-blockquote">'); bqOpen = true; }
      parts.push(`<p>${renderInline(bq[1], links)}</p>`);
      continue;
    }
    closeBlockquote();

    const li = line.match(/^-\s+(.*)$/);
    if (li) {
      flushParagraph();
      if (!listOpen) { parts.push('<ul class="md-ul">'); listOpen = true; }
      parts.push(`<li>${renderInline(li[1], links)}</li>`);
      continue;
    }

    closeList();
    paragraph.push(line);
  }
  flushAll();
  return parts.join('\n');
}

async function openNodeView(type, slug, fallbackLabel) {
  const bodyEl = document.getElementById('nodeModeBody');
  bodyEl.innerHTML = `<div class="node-view-title">${escapeHtml(fallbackLabel)}</div><p class="node-view-body">불러오는 중...</p>`;
  document.body.classList.add('node-mode');

  let data;
  try {
    const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}`);
    if (!res.ok) throw new Error('not ok');
    data = await res.json();
  } catch {
    bodyEl.innerHTML = `<div class="node-view-title">${escapeHtml(fallbackLabel)}</div><p class="node-view-body">노트를 불러오지 못했습니다.</p>`;
    return;
  }

  const metaChips = [];
  if (data.type === 'note') {
    if (data.meta.authors) metaChips.push(`<span>저자: ${escapeHtml(data.meta.authors)}</span>`);
    if (data.meta.tags?.length) metaChips.push(`<span>${escapeHtml(data.meta.tags.map((t) => '#' + t).join(' '))}</span>`);
  } else {
    if (data.meta.category) metaChips.push(`<span>카테고리: ${escapeHtml(data.meta.category)}</span>`);
    if (data.meta.aliases?.length) metaChips.push(`<span>다른 표기: ${escapeHtml(data.meta.aliases.join(', '))}</span>`);
    if (data.meta.sources?.length) metaChips.push(`<span>등장 논문 ${data.meta.sources.length}편</span>`);
  }

  // note(논문)는 아직 편집 대상이 아니고, concept/entity 노드 파일만 개인 메모
  // 편집(+이미지 붙여넣기)을 지원한다.
  const editable = data.type === 'concept' || data.type === 'entity';

  // obsidian_writer.py는 본문을 항상 "# {title}"로 시작한다(Obsidian에서 노트를
  // 열었을 때 제목이 보이도록). 여기선 위 .node-view-title이 이미 같은 제목을
  // 보여주므로, 본문 첫 줄이 그 h1이면 중복 표시되지 않게 걷어낸다.
  const bodyMarkdown = data.body_markdown.replace(/^#\s+.*(\n+|$)/, '');

  bodyEl.innerHTML = `
    <div class="node-view-title">${escapeHtml(data.title)}</div>
    <div class="node-view-meta">${metaChips.join('')}</div>
    <div class="node-view-body" id="nodeViewRenderedBody">${renderMarkdown(bodyMarkdown, data.links)}</div>
    ${editable ? `
      <div class="node-view-edit-bar">
        <button class="graph-btn" id="btnEditNotes">메모 편집</button>
      </div>
      <div class="node-view-edit-area" id="nodeViewEditArea" style="display:none;">
        <textarea class="node-view-textarea" id="nodeViewTextarea" placeholder="자유롭게 메모를 남기세요. 이미지를 붙여넣으면(Ctrl+V) 자동으로 첨부됩니다."></textarea>
        <div class="node-view-edit-actions">
          <button class="graph-btn" id="btnSaveNotes">저장</button>
          <button class="graph-btn" id="btnCancelEdit">취소</button>
          <span class="node-view-edit-status" id="nodeViewEditStatus"></span>
        </div>
      </div>
    ` : ''}
  `;

  if (editable) wireNoteEditing(type, slug, data.title, data.user_markdown || '');
  renderMermaidBlocks();
}

// renderMarkdown()은 mermaid 코드펜스를 <pre class="mermaid"> 텍스트로만 만들어둔다 -
// 실제 다이어그램 SVG로 그리는 건 mermaid.js가 그 요소를 보고 나서야 할 수 있으므로,
// DOM에 innerHTML로 끼워넣은 "다음"에 별도로 호출해야 한다.
function renderMermaidBlocks() {
  if (typeof mermaid === 'undefined') return;
  const blocks = document.querySelectorAll('#nodeModeBody pre.mermaid');
  if (blocks.length) mermaid.run({ nodes: blocks });
}

function wireNoteEditing(type, slug, title, userMarkdown) {
  const renderedBody = document.getElementById('nodeViewRenderedBody');
  const editBar = document.querySelector('.node-view-edit-bar');
  const editArea = document.getElementById('nodeViewEditArea');
  const textarea = document.getElementById('nodeViewTextarea');
  const statusEl = document.getElementById('nodeViewEditStatus');

  document.getElementById('btnEditNotes').addEventListener('click', () => {
    textarea.value = userMarkdown;
    renderedBody.style.display = 'none';
    editBar.style.display = 'none';
    editArea.style.display = 'block';
    textarea.focus();
  });

  document.getElementById('btnCancelEdit').addEventListener('click', () => {
    editArea.style.display = 'none';
    editBar.style.display = '';
    renderedBody.style.display = '';
  });

  document.getElementById('btnSaveNotes').addEventListener('click', async () => {
    const saveBtn = document.getElementById('btnSaveNotes');
    saveBtn.disabled = true;
    statusEl.textContent = '저장 중...';
    try {
      const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/notes`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_notes_markdown: textarea.value }),
      });
      if (!res.ok) throw new Error('save failed');
      openNodeView(type, slug, title);
    } catch {
      statusEl.textContent = '저장 실패';
      saveBtn.disabled = false;
    }
  });

  // 클립보드에 이미지가 있으면 기본 붙여넣기(텍스트로 들어가버림)를 막고,
  // 대신 즉시 업로드한 뒤 커서 위치에 ![](경로)를 자동으로 끼워넣는다.
  textarea.addEventListener('paste', (event) => handleImagePaste(event, type, slug, textarea));
}

async function handleImagePaste(event, type, slug, textarea) {
  const items = event.clipboardData?.items;
  if (!items) return;
  const imageItem = Array.from(items).find((item) => item.type.startsWith('image/'));
  if (!imageItem) return; // 이미지가 아니면 기본 붙여넣기(텍스트)를 그대로 둔다

  event.preventDefault();
  const file = imageItem.getAsFile();
  if (!file) return;

  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  // 토큰을 매번 다르게 둬서, 업로드가 끝나기 전에 이미지를 연달아 붙여넣어도
  // 서로 다른 placeholder끼리 잘못 치환되지 않게 한다.
  const token = Math.random().toString(36).slice(2, 8);
  const placeholder = `![업로드 중 ${token}...]()`;
  textarea.value = textarea.value.slice(0, start) + placeholder + textarea.value.slice(end);
  const cursorAfter = start + placeholder.length;
  textarea.selectionStart = textarea.selectionEnd = cursorAfter;

  const ext = imageItem.type.split('/')[1] || 'png';
  const formData = new FormData();
  formData.append('file', file, `pasted.${ext}`);

  let replacement = '![이미지 업로드 실패]()';
  try {
    const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/attachments`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('upload failed');
    const uploaded = await res.json();
    replacement = `![](${uploaded.path})`;
  } catch {
    // replacement는 이미 실패 메시지로 설정돼 있음
  }
  textarea.value = textarea.value.replace(placeholder, replacement);
}

document.getElementById('btnBackToGraph').addEventListener('click', () => {
  document.body.classList.remove('node-mode');
});

// 노드 본문은 매번 innerHTML을 통째로 새로 그리므로(openNodeView), 위키링크마다
// 개별로 리스너를 다는 대신 안 바뀌는 부모 컨테이너에 이벤트 위임 하나만 걸어둔다.
document.getElementById('nodeModeBody').addEventListener('click', (event) => {
  const link = event.target.closest('.md-wikilink-clickable');
  if (!link) return;
  openNodeView(link.dataset.nodeType, link.dataset.nodeSlug, link.textContent);
});

loadGraph(null);
