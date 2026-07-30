// 왼쪽 사이드바: Supabase Storage에 업로드된 논문 목록을 보여주고, 각 항목 오른쪽의
// 작은 그래프 버튼을 누르면 해당 논문만의 그래프를 보여준다(제목 "<논문제목> Knowledge Graph").
// 휴지통 버튼을 누르면 확인 후 vault + Supabase + 그래프 뷰에서 모두 삭제한다.
// graph.js가 먼저 로드되어 btnFullGraph/loadGraph가 전역으로 존재한다고 가정한다.

const GRAPH_ICON_SVG = `
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="6" cy="6" r="2.5"/>
  <circle cx="18" cy="6" r="2.5"/>
  <circle cx="12" cy="18" r="2.5"/>
  <line x1="8" y1="7.5" x2="10.5" y2="16.5"/>
  <line x1="16" y1="7.5" x2="13.5" y2="16.5"/>
  <line x1="8.5" y1="6" x2="15.5" y2="6"/>
</svg>`;

const DELETE_ICON_SVG = `
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="3 6 5 6 21 6"/>
  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
  <path d="M10 11v6"/>
  <path d="M14 11v6"/>
  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
</svg>`;

const paperListEl = document.getElementById('paperList');
let papersCache = [];
let selectedPaperSlug = null;

async function loadPaperList() {
  let data;
  try {
    const res = await fetch('/api/papers');
    if (!res.ok) return;
    data = await res.json();
  } catch {
    return;
  }
  papersCache = data.papers || [];
  renderPaperList();
}

function renderPaperList() {
  paperListEl.innerHTML = '';

  if (papersCache.length === 0) {
    paperListEl.innerHTML = '<div class="paper-empty">아직 업로드된 논문이 없습니다.</div>';
    return;
  }

  for (const slug of papersCache) {
    const row = document.createElement('div');
    row.className = 'paper-row' + (slug === selectedPaperSlug ? ' selected' : '');

    const title = document.createElement('span');
    title.className = 'paper-title';
    title.textContent = slug;
    title.title = slug;

    const graphBtn = document.createElement('button');
    graphBtn.type = 'button';
    graphBtn.className = 'paper-graph-btn';
    graphBtn.title = `${slug} Knowledge Graph 보기`;
    graphBtn.innerHTML = GRAPH_ICON_SVG;
    graphBtn.addEventListener('click', () => selectPaperGraph(slug));

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'paper-delete-btn';
    deleteBtn.title = `${slug} 삭제`;
    deleteBtn.innerHTML = DELETE_ICON_SVG;
    deleteBtn.addEventListener('click', () => deletePaper(slug));

    row.appendChild(title);
    row.appendChild(graphBtn);
    row.appendChild(deleteBtn);
    paperListEl.appendChild(row);
  }
}

function selectPaperGraph(slug) {
  selectedPaperSlug = slug;
  renderPaperList();
  loadGraph(slug, true);
  loadGraphSummaryCard(slug);
}

async function loadGraphSummaryCard(slug) {
  const graphSummaryEl = document.getElementById('graphSummary');
  graphSummaryEl.innerHTML = '';

  let data;
  try {
    const res = await fetch(`/api/papers/${encodeURIComponent(slug)}/summary`);
    if (!res.ok) return;
    data = await res.json();
  } catch {
    return;
  }

  graphSummaryEl.innerHTML = `
    <div class="card result">
      <strong>${data.title}</strong>
      <p>${data.one_line_summary}</p>
      <div class="meta-row"><span>API 비용</span><code>$${data.api_cost_usd.toFixed(4)}</code></div>
      ${renderNodeSummaryRows(data.node_summary)}
    </div>`;
}

async function deletePaper(slug) {
  if (!confirm(`"${slug}"를 삭제할까요?\nObsidian 노트와 Supabase에 저장된 사본이 모두 삭제되며 되돌릴 수 없습니다.`)) {
    return;
  }

  let data;
  try {
    const res = await fetch(`/api/papers/${encodeURIComponent(slug)}`, { method: 'DELETE' });
    data = await res.json();
  } catch {
    alert('삭제 요청에 실패했습니다.');
    return;
  }

  if (data.local_error || data.remote_error) {
    alert(
      '일부 삭제에 실패했습니다.\n' +
      (data.local_error ? `Obsidian: ${data.local_error}\n` : '') +
      (data.remote_error ? `Supabase: ${data.remote_error}` : '')
    );
  }

  if (selectedPaperSlug === slug) {
    selectedPaperSlug = null;
    loadGraph(null, false);
  } else {
    loadGraph(currentFocus, currentFocus !== null);
  }
  loadPaperList();
}

loadPaperList();
