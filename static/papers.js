// 왼쪽 사이드바: Supabase Storage에 업로드된 논문 목록을 옵시디언 파일 탐색기처럼
// 폴더로 묶어서 보여준다. 폴더는 가상 그룹이다(실제 vault의 논문 파일 위치는
// AutoNote/<slug>/ 그대로 - paper_notes/paper_folders.py가 "이 논문은 이 폴더에
// 속한다"만 로컬 JSON으로 기록한다). 논문을 폴더에 넣거나 뺄 때는 옵시디언처럼
// 드래그 앤 드롭을 쓴다 - 논문 행을 폴더 위로 끌어다 놓으면 그 폴더로 옮겨지고,
// 폴더 밖(다른 폴더가 아닌 빈 자리)에 놓으면 "폴더 없음" 상태로 빠진다. 각
// 논문/폴더 오른쪽의 작은 그래프 버튼은 토글이다 - 누르면 "켜짐"으로 표시되고,
// 켜진 논문들의 그래프가 전부 합쳐져서 그래프 뷰에 나온다(폴더 그래프 버튼은 그
// 폴더에 속한 논문 전체를 한 번에 토글). 하나도 안 켜져 있으면 전체 그래프를
// 보여준다(btnFullGraph와 동일). 휴지통 버튼을 누르면 확인 후 vault + Supabase +
// 그래프 뷰에서 모두 삭제한다. graph.js가 먼저 로드되어
// btnFullGraph/loadGraph/currentFocusSlugs/escapeHtml이 전역으로 존재한다고
// 가정한다.

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

const FOLDER_ICON_SVG = `
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
</svg>`;

const paperListEl = document.getElementById('paperList');
let papersCache = [];
let foldersCache = [];
const selectedPaperSlugs = new Set();
const collapsedFolderIds = new Set();

// graph.js의 "전체 그래프 보기" 버튼이 논문 토글도 같이 끄기 위해 호출한다.
window.clearSelectedPapers = function clearSelectedPapers() {
  selectedPaperSlugs.clear();
  renderPaperList();
};

async function loadFolders() {
  try {
    const res = await fetch('/api/paper-folders');
    if (!res.ok) return;
    const data = await res.json();
    foldersCache = data.folders || [];
  } catch {
    foldersCache = [];
  }
}

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
  await loadFolders();
  renderPaperList();
  // 상단바 통계(index.html) - 실제 저장된 논문 수를 그대로 보여준다(가짜
  // 숫자가 아니라 지금 이 목록의 길이 그대로).
  const statPapersEl = document.getElementById('statPapers');
  if (statPapersEl) statPapersEl.textContent = `논문 ${papersCache.length}개`;
}

function folderMemberPapers(folder) {
  const bySlug = new Map(papersCache.map((p) => [p.slug, p]));
  return folder.paper_slugs.map((slug) => bySlug.get(slug)).filter(Boolean);
}

function currentFolderIdOf(slug) {
  const folder = foldersCache.find((f) => f.paper_slugs.includes(slug));
  return folder ? folder.id : null;
}

function isFolderActive(members) {
  return members.length > 0 && members.every((p) => selectedPaperSlugs.has(p.slug));
}

function renderPaperList() {
  paperListEl.innerHTML = '';

  if (papersCache.length === 0 && foldersCache.length === 0) {
    paperListEl.innerHTML = '<div class="paper-empty">아직 업로드된 논문이 없습니다.</div>';
    return;
  }

  const inFolderSlugs = new Set();
  for (const folder of foldersCache) {
    for (const slug of folder.paper_slugs) inFolderSlugs.add(slug);
    paperListEl.appendChild(buildFolderBlock(folder));
  }

  const loose = papersCache.filter((p) => !inFolderSlugs.has(p.slug));
  for (const paper of loose) {
    paperListEl.appendChild(buildPaperRow(paper));
  }
}

function buildFolderBlock(folder) {
  const members = folderMemberPapers(folder);
  const collapsed = collapsedFolderIds.has(folder.id);

  const wrap = document.createElement('div');
  wrap.className = 'folder-block';
  wrap.dataset.folderId = folder.id;

  const header = document.createElement('div');
  header.className = 'folder-header';

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'folder-toggle-btn';
  toggleBtn.textContent = collapsed ? '▸' : '▾';
  toggleBtn.title = collapsed ? '펼치기' : '접기';
  toggleBtn.addEventListener('click', () => {
    if (collapsed) collapsedFolderIds.delete(folder.id);
    else collapsedFolderIds.add(folder.id);
    renderPaperList();
  });

  const icon = document.createElement('span');
  icon.className = 'folder-icon';
  icon.innerHTML = FOLDER_ICON_SVG;

  const name = document.createElement('span');
  name.className = 'folder-name';
  name.textContent = `${folder.name} (${members.length})`;
  name.title = folder.name;

  const active = isFolderActive(members);
  const graphBtn = document.createElement('button');
  graphBtn.type = 'button';
  graphBtn.className = 'paper-graph-btn' + (active ? ' active' : '');
  graphBtn.title = active ? `${folder.name} 폴더 그래프에서 끄기` : `${folder.name} 폴더 그래프에 켜기`;
  graphBtn.innerHTML = GRAPH_ICON_SVG;
  graphBtn.addEventListener('click', () => toggleFolderGraph(folder));

  const deleteBtn = document.createElement('button');
  deleteBtn.type = 'button';
  deleteBtn.className = 'paper-delete-btn';
  deleteBtn.title = `${folder.name} 폴더 삭제`;
  deleteBtn.innerHTML = DELETE_ICON_SVG;
  deleteBtn.addEventListener('click', () => deleteFolderWithConfirm(folder));

  header.appendChild(toggleBtn);
  header.appendChild(icon);
  header.appendChild(name);
  header.appendChild(graphBtn);
  header.appendChild(deleteBtn);
  wrap.appendChild(header);

  if (!collapsed) {
    const body = document.createElement('div');
    body.className = 'folder-body';
    for (const paper of members) body.appendChild(buildPaperRow(paper));
    wrap.appendChild(body);
  }

  return wrap;
}

function buildPaperRow(paper) {
  const { slug, title: paperTitle } = paper;
  const isOn = selectedPaperSlugs.has(slug);

  const row = document.createElement('div');
  row.className = 'paper-row' + (isOn ? ' selected' : '');
  row.draggable = true;
  row.dataset.slug = slug;
  row.addEventListener('dragstart', (event) => {
    event.dataTransfer.setData('text/plain', slug);
    event.dataTransfer.effectAllowed = 'move';
    row.classList.add('dragging');
  });
  row.addEventListener('dragend', () => row.classList.remove('dragging'));

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
  return row;
}

// 논문 행을 폴더 헤더/본문 위로 끌어다 놓으면 그 폴더로, 폴더가 아닌 빈 자리에
// 놓으면 "폴더 없음"으로 옮긴다. dragover/drop을 #paperList 하나에만 걸어두고
// (매번 다시 그려지는 자식 요소마다 리스너를 새로 안 달아도 되게) 이벤트
// 위임으로 처리한다.
paperListEl.addEventListener('dragover', (event) => {
  event.preventDefault(); // 이게 있어야 drop 이벤트가 실제로 발생한다
  const hovered = event.target.closest('.folder-block');
  for (const el of paperListEl.querySelectorAll('.folder-block.drag-over')) {
    if (el !== hovered) el.classList.remove('drag-over');
  }
  if (hovered) hovered.classList.add('drag-over');
});

paperListEl.addEventListener('dragleave', (event) => {
  if (!paperListEl.contains(event.relatedTarget)) {
    for (const el of paperListEl.querySelectorAll('.folder-block.drag-over')) el.classList.remove('drag-over');
  }
});

paperListEl.addEventListener('drop', async (event) => {
  event.preventDefault();
  for (const el of paperListEl.querySelectorAll('.folder-block.drag-over')) el.classList.remove('drag-over');

  const slug = event.dataTransfer.getData('text/plain');
  if (!slug) return;
  const folderBlock = event.target.closest('.folder-block');
  const folderId = folderBlock ? folderBlock.dataset.folderId : null;
  if (folderId === currentFolderIdOf(slug)) return; // 이미 그 상태면 아무것도 안 함
  await movePaperToFolder(slug, folderId);
});

async function movePaperToFolder(slug, folderId) {
  try {
    const res = await fetch(`/api/papers/${encodeURIComponent(slug)}/folder`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_id: folderId }),
    });
    if (!res.ok) throw new Error();
  } catch {
    alert('폴더 이동에 실패했습니다.');
    return;
  }
  await loadFolders();
  renderPaperList();
}

async function deleteFolderWithConfirm(folder) {
  if (!confirm(`"${folder.name}" 폴더를 삭제할까요?\n안에 있던 논문은 삭제되지 않고 "폴더 없음" 목록으로 돌아갑니다.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/paper-folders/${encodeURIComponent(folder.id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error();
  } catch {
    alert('폴더 삭제에 실패했습니다.');
    return;
  }
  await loadFolders();
  renderPaperList();
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

function toggleFolderGraph(folder) {
  const members = folderMemberPapers(folder);
  if (members.length === 0) return;
  const active = isFolderActive(members);
  if (active) {
    for (const p of members) selectedPaperSlugs.delete(p.slug);
  } else {
    for (const p of members) selectedPaperSlugs.add(p.slug);
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
      <div class="card-status"><span class="status-dot"></span>완료</div>
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

// 옵시디언의 "새 폴더" 버튼처럼, 누르면 목록 맨 위에 바로 이름 입력 상태인 행이
// 나타난다(확인 버튼 없이 Enter나 포커스 아웃으로 바로 생성). 아직 서버에
// 생성 요청을 보내지 않은 상태라 이름을 안 채우고 벗어나면(빈 채로 Enter/블러,
// 또는 Escape) 아무 일도 없었던 것처럼 그냥 사라진다.
document.getElementById('btnNewFolder').addEventListener('click', () => {
  if (paperListEl.querySelector('.folder-ghost-row')) return; // 이미 만드는 중이면 무시

  const ghost = document.createElement('div');
  ghost.className = 'folder-ghost-row';
  ghost.innerHTML = `
    <span class="folder-toggle-btn"></span>
    <span class="folder-icon">${FOLDER_ICON_SVG}</span>
    <input type="text" class="folder-name-input" placeholder="폴더 이름">
  `;
  paperListEl.prepend(ghost);
  const input = ghost.querySelector('input');
  input.focus();

  let settled = false;
  const commitOrCancel = async () => {
    if (settled) return;
    settled = true;
    const name = input.value.trim();
    ghost.remove();
    if (!name) return;
    try {
      const res = await fetch('/api/paper-folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : '폴더 생성에 실패했습니다.');
      }
      await loadFolders();
      renderPaperList();
    } catch (err) {
      alert(err.message);
    }
  };

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitOrCancel();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      settled = true;
      ghost.remove();
    }
  });
  input.addEventListener('blur', () => commitOrCancel());
});

loadPaperList();
