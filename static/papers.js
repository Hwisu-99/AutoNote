// 왼쪽 사이드바: Supabase Storage에 업로드된 논문 목록을 보여주고, 각 항목 오른쪽의
// 작은 그래프 버튼을 누르면 해당 논문만의 그래프를 보여준다(제목 "<논문제목> Knowledge Graph").
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

    row.appendChild(title);
    row.appendChild(graphBtn);
    paperListEl.appendChild(row);
  }
}

function selectPaperGraph(slug) {
  selectedPaperSlug = slug;
  renderPaperList();
  loadGraph(slug, true);
}

loadPaperList();
