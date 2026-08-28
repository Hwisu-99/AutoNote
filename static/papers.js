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
let brainsCache = [];
const selectedPaperSlugs = new Set();
const collapsedFolderIds = new Set();
const collapsedBrainIds = new Set();
// Brain별로 묶어보기(뇌 아이콘 토글) 상태 - 꺼져 있으면 지금까지처럼 폴더/
// loose 논문을 평평하게 보여주고, 켜지면 #paperList 안에 Brain마다 하나씩
// (+ "브레인 없음" 묶음) folder-block과 같은 모양의 접이식 블록으로 다시
// 그린다. Folder/Brain 계층은 paper_notes/brains.py 참고.
let groupByBrain = false;
// 패널 헤더 툴바(panel-title-actions)의 나머지 토글 상태 - 전부 순수 화면
// 표시 설정이라(서버에 저장 안 함) 새로고침하면 기본값(정렬 안 함/일반
// 밀도)으로 돌아간다.
let sortAlphabetically = false;
let compactDensity = false;

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

async function loadBrains() {
  try {
    const res = await fetch('/api/brains');
    if (!res.ok) return;
    const data = await res.json();
    brainsCache = data.brains || [];
  } catch {
    brainsCache = [];
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
  await Promise.all([loadFolders(), loadBrains()]);
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

// 폴더 자신의 Brain 소속(직접 지정된 값, 없으면 null) - paper_folders.py의
// set_folder_brain()이 쓴다.
function folderBrainId(folder) {
  return folder.brain_id || null;
}

// slug 논문이 (어느 폴더도 거치지 않고) 어느 Brain에 직접 속해 있는지 - Brain의
// paper_slugs에 직접 들어있는 경우만 여기 해당하고, 폴더를 통해 간접적으로
// 속한 논문은 folderBrainId(그 폴더)로 판단한다(brains.get_paper_brain_id의
// 우선순위와 동일).
function paperDirectBrainId(slug) {
  const brain = brainsCache.find((b) => (b.paper_slugs || []).includes(slug));
  return brain ? brain.id : null;
}

// id('none' 포함)에 속한 폴더/loose 논문만 걸러낸다 - buildBrainBlock이 각
// Brain 블록의 내용물을 채울 때 쓴다.
function papersAndFoldersOfBrain(id) {
  const inFolderSlugs = new Set();
  for (const folder of foldersCache) {
    for (const slug of folder.paper_slugs) inFolderSlugs.add(slug);
  }
  const allLoose = papersCache.filter((p) => !inFolderSlugs.has(p.slug));
  const matches = id === 'none'
    ? { folders: foldersCache.filter((f) => !folderBrainId(f)), loosePapers: allLoose.filter((p) => !paperDirectBrainId(p.slug)) }
    : { folders: foldersCache.filter((f) => folderBrainId(f) === id), loosePapers: allLoose.filter((p) => paperDirectBrainId(p.slug) === id) };
  if (sortAlphabetically) {
    matches.folders = [...matches.folders].sort((a, b) => a.name.localeCompare(b.name, 'ko'));
    matches.loosePapers = [...matches.loosePapers].sort((a, b) => a.title.localeCompare(b.title, 'ko'));
  }
  return matches;
}

function renderPaperList() {
  paperListEl.innerHTML = '';
  paperListEl.classList.toggle('compact', compactDensity);
  // 뇌 아이콘 - Brain별로 묶어보기가 켜져 있으면 눌려있는 것처럼 표시한다.
  document.getElementById('btnToggleBrainTabs').classList.toggle('active', groupByBrain);

  if (papersCache.length === 0 && foldersCache.length === 0) {
    paperListEl.innerHTML = '<div class="paper-empty">아직 업로드된 논문이 없습니다.</div>';
    return;
  }

  if (groupByBrain) {
    paperListEl.appendChild(buildNewBrainRow());
    for (const brain of brainsCache) paperListEl.appendChild(buildBrainBlock(brain.id, brain));
    paperListEl.appendChild(buildBrainBlock('none', null));
    return;
  }

  const inFolderSlugs = new Set();
  for (const folder of foldersCache) {
    for (const slug of folder.paper_slugs) inFolderSlugs.add(slug);
  }
  let folders = foldersCache;
  let loosePapers = papersCache.filter((p) => !inFolderSlugs.has(p.slug));

  // 정렬 토글(#btnToggleSort) - 기본은 서버가 준 순서(업로드/생성 순) 그대로,
  // 켜면 폴더/loose 논문 각각 이름순으로 다시 정렬한다(원본 배열은 안 건드리고
  // 얕은 복사 후 정렬 - foldersCache/papersCache는 다른 곳에서도 원래 순서
  // 그대로 쓰므로).
  if (sortAlphabetically) {
    folders = [...folders].sort((a, b) => a.name.localeCompare(b.name, 'ko'));
    loosePapers = [...loosePapers].sort((a, b) => a.title.localeCompare(b.title, 'ko'));
  }

  for (const folder of folders) paperListEl.appendChild(buildFolderBlock(folder));
  for (const paper of loosePapers) paperListEl.appendChild(buildPaperRow(paper));
}

// Brain별로 묶어보기 모드에서 하나의 Brain(또는 'none' = 브레인 없음)을
// folder-block과 같은 모양의 접이식 블록으로 그린다 - 안에는 그 Brain
// 소속의 folder-block/paper-row가 평소와 똑같이 들어간다. brain이 null이면
// "브레인 없음" 의사(pseudo) 묶음이라 이름 바꾸기/삭제/드래그(병합)를 뺀다.
function buildBrainBlock(id, brain) {
  const { folders, loosePapers } = papersAndFoldersOfBrain(id);
  const collapsed = collapsedBrainIds.has(id);
  const label = brain ? brain.name : '브레인 없음';

  const wrap = document.createElement('div');
  wrap.className = 'brain-block';
  wrap.dataset.brainId = id;

  const header = document.createElement('div');
  header.className = 'brain-header';

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'folder-toggle-btn';
  toggleBtn.textContent = collapsed ? '▸' : '▾';
  toggleBtn.title = collapsed ? '펼치기' : '접기';
  toggleBtn.addEventListener('click', () => {
    if (collapsed) collapsedBrainIds.delete(id);
    else collapsedBrainIds.add(id);
    renderPaperList();
  });

  const icon = document.createElement('img');
  icon.className = 'brain-block-icon';
  icon.src = '/brain-node-icon.png';
  icon.alt = '';

  // 폴더는 그 안 논문 개수만큼, loose 논문은 1개씩 - 폴더를 1개로 세면
  // 안에 몇 편이 들었는지 안 보여서 논문 수 기준으로 더한다.
  const paperCount = folders.reduce((sum, f) => sum + folderMemberPapers(f).length, 0) + loosePapers.length;
  const nameEl = document.createElement('span');
  nameEl.className = 'folder-name';
  nameEl.textContent = `${label} (${paperCount})`;
  nameEl.title = label;

  header.appendChild(toggleBtn);
  header.appendChild(icon);
  header.appendChild(nameEl);

  if (brain) {
    // 더블클릭으로 이름 바꾸기 - 옵시디언 파일/폴더 이름 바꾸기와 같은 제스처.
    nameEl.addEventListener('dblclick', (event) => {
      event.stopPropagation();
      startRenameBrainInput(nameEl, brain);
    });

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'paper-delete-btn';
    deleteBtn.title = `${brain.name} Brain 삭제`;
    deleteBtn.innerHTML = DELETE_ICON_SVG;
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      deleteBrainWithConfirm(brain);
    });
    header.appendChild(deleteBtn);

    // 다른 Brain 헤더 위로 이 헤더를 끌어다 놓으면 병합(merge-into) - 이
    // Brain이 사라지고 안의 폴더/논문이 놓인 쪽(survivor)으로 옮겨진다.
    header.draggable = true;
    header.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('application/x-brain-id', brain.id);
      event.dataTransfer.effectAllowed = 'move';
      wrap.classList.add('dragging');
    });
    header.addEventListener('dragend', () => wrap.classList.remove('dragging'));
  }

  wrap.appendChild(header);

  if (!collapsed) {
    const body = document.createElement('div');
    body.className = 'folder-body brain-body';
    if (folders.length === 0 && loosePapers.length === 0) {
      body.innerHTML = '<div class="paper-empty">비어 있습니다.</div>';
    }
    for (const folder of folders) body.appendChild(buildFolderBlock(folder));
    for (const paper of loosePapers) body.appendChild(buildPaperRow(paper));
    wrap.appendChild(body);
  }

  return wrap;
}

// Brain별로 묶어보기 모드일 때 목록 맨 위에 뜨는 "새 Brain" 버튼 - #brainTabs
// 탭 바의 "+" 탭이 하던 일을 그대로 잇는다.
function buildNewBrainRow() {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'brain-add-row';
  row.textContent = '+ 새 Brain';
  row.addEventListener('click', () => startNewBrainInput());
  return row;
}

function buildFolderBlock(folder) {
  const members = folderMemberPapers(folder);
  const collapsed = collapsedFolderIds.has(folder.id);

  const wrap = document.createElement('div');
  wrap.className = 'folder-block';
  wrap.dataset.folderId = folder.id;

  const header = document.createElement('div');
  header.className = 'folder-header';
  // 폴더를 통째로 Brain 블록 헤더 위로 드래그해 그 Brain 소속으로 옮길 수
  // 있게 draggable로 만든다(개별 논문 행과 같은 패턴 - 대상은 #paperList의
  // 위임 dragover/drop이 .brain-header를 찾아서 받는다).
  header.draggable = true;
  header.addEventListener('dragstart', (event) => {
    event.dataTransfer.setData('application/x-folder-id', folder.id);
    event.dataTransfer.effectAllowed = 'move';
    wrap.classList.add('dragging');
  });
  header.addEventListener('dragend', () => wrap.classList.remove('dragging'));
  // 드래그의 대안 - 폴더를 통째로 우클릭하면 지금 없는 Brain들로 바로
  // 배정할 수 있는 메뉴가 뜬다(드래그해서 Brain 탭 위에 놓는 것과 동일한
  // moveFolderToBrain()을 호출).
  header.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    event.stopPropagation();
    showContextMenu(event.clientX, event.clientY, buildBrainAssignItems(
      folderBrainId(folder), (brainId) => moveFolderToBrain(folder.id, brainId),
    ));
  });

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
  // 드래그의 대안 - 논문 행을 우클릭하면 Brain 배정 메뉴가 뜬다. 폴더 안
  // 논문은 movePaperToBrain()이 어차피 막으므로(그 논문만 빼서 옮기는 게
  // 아니라 폴더째로 옮겨야 함) 메뉴 자체를 그 안내 문구 하나로 대체한다.
  row.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (currentFolderIdOf(slug) !== null) {
      showContextMenu(event.clientX, event.clientY, [
        { label: '폴더 안 논문 - 폴더째로 Brain에 옮겨주세요', onClick: () => {} },
      ]);
      return;
    }
    showContextMenu(event.clientX, event.clientY, buildBrainAssignItems(
      paperDirectBrainId(slug), (brainId) => movePaperToBrain(slug, brainId),
    ));
  });

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

// #paperList 하나에 dragover/drop을 걸어두고(매번 다시 그려지는 자식
// 요소마다 리스너를 새로 안 달아도 되게) 이벤트 위임으로 세 가지 드래그를
// 전부 받는다:
//   - 논문 행(text/plain: slug) - .brain-header 위면 그 Brain에 직접 배정,
//     .folder-block 위면 그 폴더로, 둘 다 아니면 "폴더 없음"으로.
//   - 폴더 헤더(application/x-folder-id) - .brain-header 위에서 놓으면 그
//     Brain으로 폴더 전체를 배정(그 외 위치는 무시 - 폴더끼리 겹쳐 놓는 건
//     의미가 없다).
//   - Brain 헤더(application/x-brain-id) - 다른 .brain-header 위에서 놓으면
//     병합(놓인 쪽이 survivor).
// Brain별로 묶어보기가 꺼져 있을 땐 .brain-header 자체가 없으니 폴더/loose
// 논문 드래그는 지금까지와 똑같이 동작한다.
paperListEl.addEventListener('dragover', (event) => {
  const types = event.dataTransfer.types;
  const isPaperDrag = types.includes('text/plain');
  const isFolderDrag = types.includes('application/x-folder-id');
  const isBrainDrag = types.includes('application/x-brain-id');
  if (!isPaperDrag && !isFolderDrag && !isBrainDrag) return;
  event.preventDefault(); // 이게 있어야 drop 이벤트가 실제로 발생한다

  const hoveredBrain = event.target.closest('.brain-header');
  const hoveredFolder = isPaperDrag ? event.target.closest('.folder-block') : null;
  for (const el of paperListEl.querySelectorAll('.brain-header.drag-over')) {
    if (el !== hoveredBrain) el.classList.remove('drag-over');
  }
  for (const el of paperListEl.querySelectorAll('.folder-block.drag-over')) {
    if (el !== hoveredFolder) el.classList.remove('drag-over');
  }
  if (hoveredBrain) hoveredBrain.classList.add('drag-over');
  if (hoveredFolder) hoveredFolder.classList.add('drag-over');
});

paperListEl.addEventListener('dragleave', (event) => {
  if (!paperListEl.contains(event.relatedTarget)) {
    for (const el of paperListEl.querySelectorAll('.drag-over')) el.classList.remove('drag-over');
  }
});

paperListEl.addEventListener('drop', async (event) => {
  event.preventDefault();
  for (const el of paperListEl.querySelectorAll('.drag-over')) el.classList.remove('drag-over');

  const draggedBrainId = event.dataTransfer.getData('application/x-brain-id');
  if (draggedBrainId) {
    const targetHeader = event.target.closest('.brain-header');
    const targetBrainId = targetHeader ? targetHeader.closest('.brain-block').dataset.brainId : null;
    if (targetBrainId && targetBrainId !== 'none' && targetBrainId !== draggedBrainId) {
      await mergeBrainsWithConfirm(draggedBrainId, targetBrainId);
    }
    return;
  }

  const draggedFolderId = event.dataTransfer.getData('application/x-folder-id');
  if (draggedFolderId) {
    const targetHeader = event.target.closest('.brain-header');
    if (targetHeader) {
      const targetBrainId = targetHeader.closest('.brain-block').dataset.brainId;
      await moveFolderToBrain(draggedFolderId, targetBrainId === 'none' ? null : targetBrainId);
    }
    return;
  }

  const slug = event.dataTransfer.getData('text/plain');
  if (!slug) return;

  const brainHeader = event.target.closest('.brain-header');
  if (brainHeader) {
    const targetBrainId = brainHeader.closest('.brain-block').dataset.brainId;
    await movePaperToBrain(slug, targetBrainId === 'none' ? null : targetBrainId);
    return;
  }

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

// Brain 배정/삭제/병합은 app.py가 로컬 변경 직후 바로 응답하고, Neo4j
// 재태깅(brain_id/brain_ids)은 응답을 보낸 뒤 백그라운드에서 마저 한다
// (docs/neo4j/synchronization.md 참고 - 원격 Neo4j 왕복 때문에 응답 안에서
// 기다리면 체감이 느려서 뺐다). 그래서 아래 fetch 자체가 성공해도 그
// 백그라운드 작업은 아직 안 끝났을 수 있다 - sinceMs(액션을 시작한 시각)를
// 들고 잠깐 뒤에 /api/neo4j-sync-status를 다시 확인해서, 그 시각 이후에
// 새로 기록된 실패면 알려준다(Date 비교로 오래된 이전 실패를 다시 보여주지
// 않는다 - 이 상태는 "마지막 실패 하나"만 기억하지 큐가 아니므로).
function checkBrainSyncError(sinceMs) {
  setTimeout(async () => {
    let data;
    try {
      const res = await fetch('/api/neo4j-sync-status');
      if (!res.ok) return;
      data = await res.json();
    } catch {
      return; // 상태 확인 자체가 실패해도 조용히 넘어간다 - 로컬 배정은 이미 성공했다
    }
    const err = data.last_error;
    if (err && Date.parse(err.at) >= sinceMs) {
      alert(`Neo4j 동기화에 실패했습니다(GraphRAG 검색 범위가 최신이 아닐 수 있어요):\n${err.message}`);
    }
  }, 2000);
}

// 폴더/논문 행 우클릭 메뉴에 채워 넣을 "Brain에 배정" 항목 목록을 만든다 -
// Brain 블록 헤더로 드래그하는 것과 동일한 동작을 마우스가 불편하거나
// (트랙패드, 터치) 그냥 클릭이 더 빠른 사용자를 위해 대안으로 제공한다. currentBrainId는
// 지금 이미 배정된 Brain(없으면 null) - 그 Brain 자신은 목록에서 빼고, 이미
// 뭔가에 배정돼 있으면 "브레인 없음으로" 항목을 추가한다. showContextMenu는
// graph.js가 전역으로 노출해두는 함수를 그대로 쓴다(파일 맨 위 주석 참고).
function buildBrainAssignItems(currentBrainId, onSelect) {
  const items = brainsCache
    .filter((b) => b.id !== currentBrainId)
    .map((b) => ({ label: b.name, onClick: () => onSelect(b.id) }));
  if (currentBrainId !== null) {
    items.push({ label: '브레인 없음으로', onClick: () => onSelect(null) });
  }
  if (!items.length) {
    items.push({ label: '+ 새 Brain 만들기', onClick: () => startNewBrainInput() });
  }
  return items;
}

// ---- Brain 배정/병합/삭제 --------------------------------------------------
// Folder보다 한 단계 위 컨테이너(paper_notes/brains.py). Brain별로 묶어보기
// 모드(buildBrainBlock)의 헤더가 드래그/우클릭 메뉴로 이 함수들을 호출한다.

async function moveFolderToBrain(folderId, brainId) {
  const sinceMs = Date.now();
  try {
    const res = await fetch(`/api/paper-folders/${encodeURIComponent(folderId)}/brain`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brain_id: brainId }),
    });
    if (!res.ok) throw new Error();
  } catch {
    alert('Brain 배정에 실패했습니다.');
    return;
  }
  await loadFolders();
  renderPaperList();
  checkBrainSyncError(sinceMs);
}

async function movePaperToBrain(slug, brainId) {
  // 폴더 안 논문은 그 폴더의 Brain을 따라가는 간접 소속이라(get_paper_brain_id
  // 참고), loose(폴더 없음) 논문만 여기서 직접 배정한다 - 폴더 소속 논문을
  // 옮기고 싶으면 폴더째로 드래그하게 안내한다.
  if (currentFolderIdOf(slug) !== null) {
    alert('폴더 안에 있는 논문은 폴더째로 Brain에 옮겨주세요.');
    return;
  }
  const sinceMs = Date.now();
  try {
    const res = await fetch(`/api/papers/${encodeURIComponent(slug)}/brain`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brain_id: brainId }),
    });
    if (!res.ok) throw new Error();
  } catch {
    alert('Brain 배정에 실패했습니다.');
    return;
  }
  await loadBrains();
  renderPaperList();
  checkBrainSyncError(sinceMs);
}

async function mergeBrainsWithConfirm(loserId, survivorId) {
  const loser = brainsCache.find((b) => b.id === loserId);
  const survivor = brainsCache.find((b) => b.id === survivorId);
  if (!loser || !survivor) return;
  if (!confirm(`"${loser.name}" Brain을 "${survivor.name}" Brain으로 합칠까요?
${loser.name}의 폴더/논문이 전부 ${survivor.name}로 옮겨지고 ${loser.name}은 사라집니다.`)) {
    return;
  }
  const sinceMs = Date.now();
  try {
    const res = await fetch(
      `/api/brains/${encodeURIComponent(loserId)}/merge-into/${encodeURIComponent(survivorId)}`,
      { method: 'POST' }
    );
    if (!res.ok) throw new Error();
  } catch {
    alert('Brain 병합에 실패했습니다.');
    return;
  }
  collapsedBrainIds.delete(loserId);
  await Promise.all([loadFolders(), loadBrains()]);
  renderPaperList();
  checkBrainSyncError(sinceMs);
}

async function deleteBrainWithConfirm(brain) {
  if (!confirm(`"${brain.name}" Brain을 삭제할까요?
안에 있던 폴더/논문은 삭제되지 않고 "브레인 없음" 상태로 돌아갑니다.`)) {
    return;
  }
  const sinceMs = Date.now();
  try {
    const res = await fetch(`/api/brains/${encodeURIComponent(brain.id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error();
  } catch {
    alert('Brain 삭제에 실패했습니다.');
    return;
  }
  collapsedBrainIds.delete(brain.id);
  await Promise.all([loadFolders(), loadBrains()]);
  renderPaperList();
  checkBrainSyncError(sinceMs);
}

// nameEl은 buildBrainBlock이 만든 .folder-name 스팬 자체 - 더블클릭한 그
// 자리에서 바로 입력 상태로 바뀐다(옵시디언 파일/폴더 이름 바꾸기와 같은
// 제스처).
function startRenameBrainInput(nameEl, brain) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'brain-name-input';
  input.value = brain.name;
  nameEl.replaceWith(input);
  input.focus();
  input.select();

  let settled = false;
  const commitOrCancel = async () => {
    if (settled) return;
    settled = true;
    const name = input.value.trim();
    if (!name || name === brain.name) {
      renderPaperList();
      return;
    }
    try {
      const res = await fetch(`/api/brains/${encodeURIComponent(brain.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : 'Brain 이름 변경에 실패했습니다.');
      }
      await loadBrains();
    } catch (err) {
      alert(err.message);
    }
    renderPaperList();
  };

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitOrCancel();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      settled = true;
      renderPaperList();
    }
  });
  input.addEventListener('blur', () => commitOrCancel());
}

// "+ 새 Brain" 버튼 클릭 시 옵시디언 "새 폴더"와 같은 제스처 - 그 자리에 바로
// 이름 입력 상태인 임시 행이 나타난다(확인 버튼 없이 Enter/블러로 바로 생성,
// 빈 채로 벗어나면 아무 일도 없었던 것처럼 사라진다).
function startNewBrainInput() {
  if (paperListEl.querySelector('.brain-ghost-row')) return;

  const addBtn = paperListEl.querySelector('.brain-add-row');
  const ghost = document.createElement('div');
  ghost.className = 'brain-ghost-row';
  ghost.innerHTML = '<input type="text" class="brain-name-input" placeholder="Brain 이름">';
  if (addBtn) addBtn.replaceWith(ghost);
  else paperListEl.prepend(ghost);
  const input = ghost.querySelector('input');
  input.focus();

  let settled = false;
  const commitOrCancel = async () => {
    if (settled) return;
    settled = true;
    const name = input.value.trim();
    if (!name) {
      renderPaperList();
      return;
    }
    try {
      const res = await fetch('/api/brains', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : 'Brain 생성에 실패했습니다.');
      }
      await loadBrains();
    } catch (err) {
      alert(err.message);
    }
    renderPaperList();
  };

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitOrCancel();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      settled = true;
      renderPaperList();
    }
  });
  input.addEventListener('blur', () => commitOrCancel());
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

// ---- 패널 헤더 툴바의 나머지 토글 3종 ---------------------------------------

// 뇌 아이콘 - #paperList를 Brain별 묶음(folder-block과 같은 모양의 접이식
// 블록)으로 다시 그릴지 토글한다. 예전엔 별도 #brainTabs 탭 바가 있었지만
// 패널 안에 흡수했다 - 병합/삭제/이름 바꾸기도 이제 그 블록 헤더에서 한다.
const btnToggleBrainTabsEl = document.getElementById('btnToggleBrainTabs');
btnToggleBrainTabsEl.addEventListener('click', () => {
  groupByBrain = !groupByBrain;
  renderPaperList();
});

// 폴더 전체 펼치기/접기 - 하나라도 펼쳐져 있으면(collapsedFolderIds에 없는
// 폴더가 하나라도 있으면) 전부 접고, 전부 이미 접혀 있으면 전부 편다(옵시디언
// 파일 탐색기의 "모두 접기"와 같은 스마트 토글).
document.getElementById('btnToggleCollapseAll').addEventListener('click', () => {
  const anyExpanded = foldersCache.some((f) => !collapsedFolderIds.has(f.id));
  collapsedFolderIds.clear();
  if (anyExpanded) {
    for (const f of foldersCache) collapsedFolderIds.add(f.id);
  }
  renderPaperList();
});

const btnToggleSortEl = document.getElementById('btnToggleSort');
btnToggleSortEl.addEventListener('click', () => {
  sortAlphabetically = !sortAlphabetically;
  btnToggleSortEl.classList.toggle('active', sortAlphabetically);
  renderPaperList();
});

const btnToggleDensityEl = document.getElementById('btnToggleDensity');
btnToggleDensityEl.addEventListener('click', () => {
  compactDensity = !compactDensity;
  btnToggleDensityEl.classList.toggle('active', compactDensity);
  renderPaperList();
});

loadPaperList();
