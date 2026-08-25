// 왼쪽 사이드바: Supabase Storage에 업로드된 논문 목록을 보여주고, 각 항목 오른쪽의
// 작은 그래프 버튼은 토글이다 - 누르면 그 논문이 "켜짐"으로 표시되고, 켜진
// 논문들의 그래프가 전부 합쳐져서 그래프 뷰에 나온다(제목도 켜진 논문들의
// 제목을 나열). 이미 켜진 논문 버튼을 다시 누르면 꺼지고, 그 논문의 노드들은
// (다른 켜진 논문과 공유하지 않는 한) 그래프에서 사라진다. 하나도 안 켜져
// 있으면 전체 그래프를 보여준다(btnFullGraph와 동일).
// 휴지통 버튼을 누르면 확인 후 vault + Supabase + 그래프 뷰에서 모두 삭제한다.
// graph.js가 먼저 로드되어 btnFullGraph/loadGraph/currentFocusSlugs가 전역으로
// 존재한다고 가정한다.

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
const selectedPaperSlugs = new Set();

// graph.js의 "전체 그래프 보기" 버튼이 논문 토글도 같이 끄기 위해 호출한다.
window.clearSelectedPapers = function clearSelectedPapers() {
  selectedPaperSlugs.clear();
  renderPaperList();
};

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

  for (const paper of papersCache) {
    const { slug, title: paperTitle } = paper;
    const isOn = selectedPaperSlugs.has(slug);
    const row = document.createElement('div');
    row.className = 'paper-row' + (isOn ? ' selected' : '');

    const title = document.createElement('span');
    title.className = 'paper-title';
    title.textContent = paperTitle;
    title.title = paperTitle;

    const graphBtn = document.createElement('button');
    graphBtn.type = 'button';
    graphBtn.className = 'paper-graph-btn' + (isOn ? ' active' : '');
    graphBtn.title = isOn
      ? `${paperTitle} 그래프에서 끄기`
      : `${paperTitle} 그래프에 켜기`;
    graphBtn.innerHTML = GRAPH_ICON_SVG;
    graphBtn.addEventListener('click', () => togglePaperGraph(slug));

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'paper-delete-btn';
    deleteBtn.title = `${paperTitle} 삭제`;
    deleteBtn.innerHTML = DELETE_ICON_SVG;
    deleteBtn.addEventListener('click', () => deletePaper(slug, paperTitle));

    row.appendChild(title);
    row.appendChild(graphBtn);
    row.appendChild(deleteBtn);
    paperListEl.appendChild(row);
  }
}

function togglePaperGraph(slug) {
  if (selectedPaperSlugs.has(slug)) {
    selectedPaperSlugs.delete(slug);
  } else {
    selectedPaperSlugs.add(slug);
    // 끌 때는 요약 카드를 그대로 둔다(다른 켜진 논문 카드가 있을 수도, 없을
    // 수도 있음 - 굳이 추측해서 바꾸지 않는다). 켤 때만 그 논문 카드로 갱신.
    loadGraphSummaryCard(slug);
  }
  renderPaperList();
  loadGraph([...selectedPaperSlugs], selectedPaperSlugs.size > 0);
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
      <p>${data.tldr ?? data.one_line_summary ?? ''}</p>
      <div class="meta-row"><span>API 비용</span><code>$${data.api_cost_usd.toFixed(4)}</code></div>
      ${renderNodeSummaryRows(data.node_summary)}
    </div>`;
}

async function deletePaper(slug, title) {
  if (!confirm(`"${title}"를 삭제할까요?\nObsidian 노트와 Supabase에 저장된 사본이 모두 삭제되며 되돌릴 수 없습니다.`)) {
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

  selectedPaperSlugs.delete(slug);
  loadGraph([...selectedPaperSlugs], selectedPaperSlugs.size > 0);
  loadPaperList();
}

loadPaperList();
