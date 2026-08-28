// AutoNote 그래프 뷰: Obsidian 그래프 뷰와 같은 로직(노트=노드, [[위키링크]]/공통 tag=에지)으로
// /api/graph 결과를 d3-force로 렌더링한다. index.html/papers.js에서
// `loadGraph(focusSlugs, onlyFocus)`만 호출하면 되도록 전역 함수로 노출한다.
// focusSlugs는 논문 slug 배열(0개면 전체 그래프) - 사이드바에서 여러 논문을
// 동시에 켜면(멀티 토글) 그 논문들의 focus 그래프가 합쳐져서 보인다.

// paper_notes/node_store.py의 CONCEPT_CATEGORIES와 반드시 같은 목록을 유지해야
// 한다 - 서버가 이 목록 밖의 값을 add_category()에서 거부하므로, 프론트도 같은
// 선택지만 보여줘야 "선택했는데 서버가 거부하는" 혼란이 없다.
const CONCEPT_CATEGORIES = [
  'problem', 'proposed_method', 'architecture', 'algorithm', 'theory',
  'optimization', 'training_strategy', 'evaluation_setup', 'finding',
  'input_representation', 'limitation', 'other',
];

const graphSvg = d3.select('#graphSvg');
const graphTitleEl = document.getElementById('graphTitle');
const btnFullGraph = document.getElementById('btnFullGraph');
const toggleTagsInput = document.getElementById('toggleTags');
const toggleEntitiesInput = document.getElementById('toggleEntities');
const toggleAttachmentsInput = document.getElementById('toggleAttachments');
const toggleExpandInput = document.getElementById('toggleExpand');
const graphColumnEl = document.querySelector('.graph-column');
const graphPanelEl = document.getElementById('graphPanel');
let simulation = null;
let currentFocusSlugs = [];
let currentGraphData = null;
let hideTagNodes = !toggleTagsInput.checked;
let hideEntityNodes = !toggleEntitiesInput.checked;
let hideAttachmentNodes = !toggleAttachmentsInput.checked;
// openNodeView()가 마지막으로 연 노드를 기억해둔다 - 노트 본문에서 텍스트를 선택해
// 우클릭으로 생성할 때 "어느 논문/개념/엔티티를 보고 있었는지"를 알아야 자동 연결할
// 수 있다.
let currentOpenNode = null;
// currentOpenNode를 그래프 노드 id(note는 slug 그대로, concept/entity는 "type:라벨")로
// 바꾼다 - _nodePositions에서 "지금 보고 있던 노드가 그래프에서 마지막으로 어디
// 있었는지" 찾을 때 쓴다.
function currentOpenNodeGraphId() {
  if (!currentOpenNode) return null;
  return currentOpenNode.type === 'note' ? currentOpenNode.slug : `${currentOpenNode.type}:${currentOpenNode.title}`;
}
// 노드 id -> 마지막으로 기록된 {x, y}. 두 곳에서 쓴다: (1) 텍스트 선택 우클릭으로
// 자동연결 노드를 만들 때 "지금 보고 있던 carrier 노드가 마지막으로 어디 있었는지"
// 찾는 용도(그래프 화면 자체가 안 보이는 상태라 클릭 좌표를 못 씀), (2) renderGraph()가
// 에지 없는(orphan) 노드의 초기 위치를 이어서 쓰는 용도(포커스/전체 그래프를 오가도
// 자리 유지). 에지가 있는 노드에는 절대 안 쓴다 - 예전에 "모든 노드"에 이 방식을
// 썼다가 포커스/전체 그래프의 서로 다른 좌표계가 섞여 그래프 전체가 화면 밖으로
// 밀려나는 문제가 있었다.
const _nodePositions = new Map();

// orphan(에지 없는) 노드 id -> { anchorId, dx, dy }. 생성 시점에 가장 가까웠던
// 노드(타입 무관 - 논문/개념/엔티티 다 될 수 있음)와 그때의 상대 위치(dx/dy)를
// 기억해뒀다가, renderGraph()가 매 tick마다 "그 노드의 지금 위치 + dx/dy"로
// orphan을 계속 따라다니게 한다 - 그래야 포커스 뷰든 수백 개짜리 전체 그래프든,
// 그 기준 노드가 어디로 배치되든 orphan은 항상 그 옆에 있는다(정확한 화면 좌표를
// 고정하면 전체 그래프처럼 완전히 다른 레이아웃에서는 엉뚱한 자리에 못박히게
// 된다). 기준 노드가 실제로 연결되거나(더 이상 orphan이 아니게 됨) 사용자가 직접
// 드래그로 옮기면(수동으로 자리를 정했으므로) 이 항목은 지운다.
const _orphanAnchors = new Map();

// ---- 노드 검색 ----
// renderGraph()가 매번 새로 그리는 노드/라벨/에지 selection과 zoom behavior를
// 여기 모듈 스코프에 저장해둔다 - 검색창(바깥 스코프)에서 하이라이트를 걸거나
// 특정 노드로 화면을 이동(panToPoint)시키려면 그 참조가 필요하기 때문이다.
let _nodeSel = null;
let _labelSel = null;
let _linkSel = null;
let _graphZoom = null;

const NODE_TYPE_SEARCH_LABELS = { note: '논문', concept: '개념', entity: '용어', tag: '태그', attachment: '첨부' };
const graphSearchInput = document.getElementById('graphSearchInput');
const graphSearchResultsEl = document.getElementById('graphSearchResults');

// 지금 켜진 표시 토글(태그/용어/첨부파일)에 맞춰, 실제로 화면에 보이는 노드
// 중에서만 라벨 부분일치(대소문자 무시)로 찾는다 - 숨겨진 노드가 검색되면
// 하이라이트할 원 자체가 없어 클릭해도 아무 반응이 없는 것처럼 보인다.
function matchGraphNodes(query) {
  const q = query.trim().toLowerCase();
  if (!q || !currentGraphData) return [];
  return currentGraphData.nodes.filter((n) => {
    if (hideTagNodes && n.type === 'tag') return false;
    if (hideEntityNodes && n.type === 'entity') return false;
    if (hideAttachmentNodes && n.type === 'attachment') return false;
    return n.label.toLowerCase().includes(q);
  });
}

function clearSearchHighlight() {
  _nodeSel?.classed('dimmed', false).classed('search-match', false);
  _labelSel?.classed('dimmed', false);
  _linkSel?.classed('dimmed', false);
}

function applySearchHighlight(matchedIds) {
  if (!_nodeSel || !matchedIds.size) { clearSearchHighlight(); return; }
  _nodeSel.classed('search-match', (d) => matchedIds.has(d.id)).classed('dimmed', (d) => !matchedIds.has(d.id));
  // 라벨은 스타일 강조 없이 dimmed 여부만 반영한다 - search-match 원(위)만
  // 검은 테두리로 강조되고, 라벨은 흐려지지만 않으면 충분히 도드라진다.
  _labelSel.classed('dimmed', (d) => !matchedIds.has(d.id));
  // 매칭된 노드만 도드라져 보이게, 에지는 어느 쪽이든 전부 흐리게 둔다.
  _linkSel.classed('dimmed', true);
}

function renderSearchResults(matches) {
  if (!matches.length) {
    graphSearchResultsEl.innerHTML = '<div class="graph-search-empty">일치하는 노드가 없습니다.</div>';
    graphSearchResultsEl.classList.add('open');
    return;
  }
  graphSearchResultsEl.innerHTML = '';
  matches.slice(0, 12).forEach((n) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'graph-search-result';
    item.innerHTML = `<span class="graph-search-result-label">${escapeHtml(n.label)}</span><span class="graph-search-result-type">${NODE_TYPE_SEARCH_LABELS[n.type] || n.type}</span>`;
    item.addEventListener('click', () => focusSearchResult(n));
    graphSearchResultsEl.appendChild(item);
  });
  graphSearchResultsEl.classList.add('open');
}

// 지금 확대/축소 배율은 유지한 채, (x, y)가 화면 중앙에 오도록 부드럽게
// 이동시킨다 - 노드가 힘 시뮬레이션 중이라 위치가 계속 바뀌므로 정확한 좌표보다는
// "그 근처로 화면을 옮겨준다"는 정도의 목적이다.
function panToPoint(x, y) {
  if (!_graphZoom) return;
  const svgEl = document.getElementById('graphSvg');
  const width = svgEl.clientWidth || 360;
  const height = svgEl.clientHeight || 520;
  const scale = d3.zoomTransform(svgEl).k || 1;
  const transform = d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-x, -y);
  graphSvg.transition().duration(400).call(_graphZoom.transform, transform);
}

function focusSearchResult(n) {
  applySearchHighlight(new Set([n.id]));
  const pos = _nodePositions.get(n.id);
  if (pos) panToPoint(pos.x, pos.y);
  graphSearchResultsEl.classList.remove('open');
}

function clearSearch() {
  graphSearchInput.value = '';
  graphSearchResultsEl.innerHTML = '';
  graphSearchResultsEl.classList.remove('open');
  clearSearchHighlight();
}

graphSearchInput.addEventListener('input', () => {
  if (!graphSearchInput.value.trim()) { clearSearch(); return; }
  const matches = matchGraphNodes(graphSearchInput.value);
  applySearchHighlight(new Set(matches.map((n) => n.id)));
  renderSearchResults(matches);
});

graphSearchInput.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { clearSearch(); graphSearchInput.blur(); return; }
  if (event.key === 'Enter') {
    const matches = matchGraphNodes(graphSearchInput.value);
    if (matches.length) focusSearchResult(matches[0]);
  }
});

document.addEventListener('click', (event) => {
  if (!event.target.closest('.graph-search-wrap')) graphSearchResultsEl.classList.remove('open');
});

// 그래프에 지금 렌더링된 노드(타입 무관) 중 (x, y)에 가장 가까운 것의 id를 찾는다 -
// orphan 생성 시 "이 노드는 저 노드 근처에 있다"고 기억해두는 기준점으로 쓴다.
function findNearestNodeId(x, y) {
  if (!currentGraphData) return null;
  let bestId = null;
  let bestDist = Infinity;
  for (const n of currentGraphData.nodes) {
    const pos = _nodePositions.get(n.id);
    if (!pos) continue;
    const dist = Math.hypot(pos.x - x, pos.y - y);
    if (dist < bestDist) { bestDist = dist; bestId = n.id; }
  }
  return bestId;
}

btnFullGraph.addEventListener('click', () => {
  // 전체 그래프 보기는 사이드바에서 켜둔 논문 토글도 전부 끈다 - 안 그러면
  // 사이드바는 "켜짐"으로 보이는데 그래프는 전체가 나오는 상태로 어긋난다.
  window.clearSelectedPapers?.();
  loadGraph([], false);
});

// 그래프 배경/노드 우클릭 시 뜨는 작은 플로팅 메뉴 (개념/엔티티 생성 2개, 또는 삭제
// 1개). 화면 좌표(clientX/Y) 기준 position:fixed라 그래프 확대/축소·팬과 무관하게
// 클릭한 자리 그대로 뜬다. 항목에 onClick 대신 items(하위 항목 배열)를 주면
// 클릭 시 실행하지 않고 옆에 서브메뉴를 펼친다(papers.js의 "Brain으로 이동"
// 처럼 옵션이 많아 최상위 메뉴를 다 채우면 번잡한 경우용) - 몇 단계든 중첩
// 가능하지만 지금은 1단계만 쓴다.
let _activeContextMenus = []; // [루트 메뉴, 열려 있는 서브메뉴, 그 서브메뉴의 서브메뉴, ...] 순서
function closeContextMenu() {
  for (const el of _activeContextMenus) el.remove();
  _activeContextMenus = [];
  document.removeEventListener('click', _onOutsideContextMenuClick, true);
  document.removeEventListener('keydown', _onContextMenuEscape, true);
}
function _onContextMenuEscape(event) {
  if (event.key === 'Escape') closeContextMenu();
}
// 메뉴(또는 그 서브메뉴) 내부 클릭은 무시하고, 그 바깥을 클릭했을 때만 전체를
// 닫는다 - 서브메뉴를 펼치는 클릭까지 "메뉴 바깥 클릭"으로 오인해 곧장 닫아
// 버리면 서브메뉴가 뜨자마자 사라지므로, 클릭이 메뉴 트리 안인지 먼저 확인한다.
function _onOutsideContextMenuClick(event) {
  if (_activeContextMenus.some((el) => el.contains(event.target))) return;
  closeContextMenu();
}
function showContextMenu(clientX, clientY, items) {
  closeContextMenu();
  const menu = _buildContextMenuEl(items, clientX, clientY);
  document.body.appendChild(menu);
  _activeContextMenus.push(menu);
  // 이번 우클릭 이벤트 자체가 지금 document에 새로 등록하는 리스너까지 곧장
  // 버블링해 메뉴를 열자마자 닫아버리지 않도록, 다음 tick에 등록한다.
  setTimeout(() => {
    document.addEventListener('click', _onOutsideContextMenuClick, true);
    document.addEventListener('keydown', _onContextMenuEscape, true);
  }, 0);
}
function _buildContextMenuEl(items, x, y) {
  const menu = document.createElement('div');
  menu.className = 'graph-context-menu';
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  let openSubmenuBtn = null;
  items.forEach((item) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'graph-context-menu-item' + (item.items ? ' has-submenu' : '');
    if (item.items) {
      // 라벨과 화살표를 별도 span으로 나눠야 CSS의 justify-content:
      // space-between이 실제로 둘을 양끝으로 벌려준다(한 텍스트 노드로는
      // 안 먹음).
      btn.innerHTML = `<span></span><span class="submenu-arrow">▸</span>`;
      btn.firstChild.textContent = item.label;
    } else {
      btn.textContent = item.label;
    }
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      if (!item.items) {
        closeContextMenu();
        item.onClick();
        return;
      }
      // 서브메뉴 토글 - 같은 버튼을 다시 누르면 닫고, 다른 버튼을 누르면 이
      // 메뉴(menu) 자신은 그대로 둔 채 그 아래(서브메뉴, 서브메뉴의 서브메뉴 …)
      // 만 지운 뒤 새로 연다.
      const idx = _activeContextMenus.indexOf(menu);
      const removed = idx === -1 ? [] : _activeContextMenus.splice(idx + 1);
      for (const el of removed) el.remove();
      if (openSubmenuBtn === btn) {
        openSubmenuBtn = null;
        return;
      }
      const rect = btn.getBoundingClientRect();
      const submenu = _buildContextMenuEl(item.items, rect.right + 2, rect.top);
      document.body.appendChild(submenu);
      // 화면 오른쪽/아래로 넘치면 반대쪽으로 뒤집는다.
      const submenuRect = submenu.getBoundingClientRect();
      if (submenuRect.right > window.innerWidth) {
        submenu.style.left = `${rect.left - submenuRect.width - 2}px`;
      }
      if (submenuRect.bottom > window.innerHeight) {
        submenu.style.top = `${Math.max(4, window.innerHeight - submenuRect.height - 8)}px`;
      }
      _activeContextMenus.push(submenu);
      openSubmenuBtn = btn;
    });
    menu.appendChild(btn);
  });
  return menu;
}

// orphan 노드 생성 패널/자동연결 생성 패널처럼 클릭 좌표에 떠야 하는(트리거 버튼
// 아래 고정 위치가 아닌) 폼을 담는 얕은 래퍼. 바깥을 클릭하면 스스로 사라진다.
function openFloatingPanel(clientX, clientY, buildFn) {
  const panel = document.createElement('div');
  panel.className = 'graph-floating-panel';
  panel.style.left = `${clientX}px`;
  panel.style.top = `${clientY}px`;
  document.body.appendChild(panel);
  buildFn(panel);
  const outsideClick = (event) => {
    if (panel.isConnected && !panel.contains(event.target)) {
      panel.remove();
      document.removeEventListener('click', outsideClick, true);
    }
  };
  setTimeout(() => document.addEventListener('click', outsideClick, true), 0);
  return panel;
}

// 그래프 배경(빈 공간) 우클릭 -> 개념/엔티티 생성 버튼 2개. orphan(carrier 논문
// 없음)으로 만든 뒤, 우클릭했던 바로 그 자리에 나타나게 한다(연결은 이제 별도로
// 노드를 0.5초 홀드+드래그해서 한다).
let _pendingSpawnPosition = null;
graphSvg.on('contextmenu', (event) => {
  event.preventDefault();
  const svgNode = graphSvg.node();
  // clientX/Y(화면 좌표)를 지금 줌/팬 상태를 되돌린 그래프 로컬 좌표로 바꾼다 -
  // 노드 x/y가 사는 좌표계와 같아야 스폰 위치로 그대로 쓸 수 있다.
  const [localX, localY] = d3.zoomTransform(svgNode).invert(d3.pointer(event, svgNode));
  const nearestNodeId = findNearestNodeId(localX, localY);
  showContextMenu(event.clientX, event.clientY, [
    { label: '개념 생성', onClick: () => openOrphanCreatePanel('concept', event.clientX, event.clientY, localX, localY, nearestNodeId) },
    { label: '엔티티 생성', onClick: () => openOrphanCreatePanel('entity', event.clientX, event.clientY, localX, localY, nearestNodeId) },
  ]);
});

function openOrphanCreatePanel(type, clientX, clientY, spawnX, spawnY, nearestNodeId) {
  openFloatingPanel(clientX, clientY, (container) => {
    openCreateNodePanel(container, {
      fixedType: type,
      orphan: true,
      orphanAnchorId: nearestNodeId,
      onCreated: async (result) => {
        container.remove();
        if (result?.type && result?.label) {
          const id = `${result.type}:${result.label}`;
          _pendingSpawnPosition = { id, x: spawnX, y: spawnY };
          // 우클릭했을 때 가장 가까웠던 노드(타입 무관)를 기준점으로 기억해둔다 -
          // 전체 그래프처럼 완전히 다른 레이아웃으로 바뀌어도 "그 노드 옆"이라는
          // 상대적 위치는 그대로 유지된다(renderGraph 참고).
          const anchorPos = nearestNodeId ? _nodePositions.get(nearestNodeId) : null;
          if (anchorPos) {
            _orphanAnchors.set(id, { anchorId: nearestNodeId, dx: spawnX - anchorPos.x, dy: spawnY - anchorPos.y });
          }
        }
        await loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
      },
    });
  });
}

// 노트/개념/엔티티 md 본문에서 텍스트를 선택한 뒤 우클릭 -> 같은 2버튼 메뉴지만
// 지금 보고 있는 노드에 자동으로 연결된다(orphan을 거치지 않음).
document.getElementById('nodeModeBody').addEventListener('contextmenu', (event) => {
  const selectedText = window.getSelection()?.toString().trim();
  if (!selectedText || !currentOpenNode) return;

  const carrierOptions = currentOpenNode.type === 'note'
    ? [{ slug: currentOpenNode.slug, title: currentOpenNode.title }]
    : (currentOpenNode.sources || []).map((s) => ({ slug: s.slug, title: s.title }));
  if (!carrierOptions.length) return; // orphan concept/entity 안이면 자동 연결할 논문이 없다

  event.preventDefault();
  showContextMenu(event.clientX, event.clientY, [
    { label: '개념 생성', onClick: () => openAutoConnectCreatePanel('concept', selectedText, carrierOptions, event.clientX, event.clientY) },
    { label: '엔티티 생성', onClick: () => openAutoConnectCreatePanel('entity', selectedText, carrierOptions, event.clientX, event.clientY) },
  ]);
});

function openAutoConnectCreatePanel(type, prefillLabel, carrierOptions, clientX, clientY) {
  openFloatingPanel(clientX, clientY, (container) => {
    const singleCarrier = carrierOptions.length === 1 ? carrierOptions[0] : undefined;
    openCreateNodePanel(container, {
      fixedType: type,
      prefillLabel,
      fixedCarrier: singleCarrier,
      carrierOptions: singleCarrier ? undefined : carrierOptions,
      needsConcepts: type === 'entity',
      onCreated: async (result) => {
        container.remove();
        // 지금 보고 있던 노드(carrier) 근처에서 시작하게 한다 - 그래프 화면이 지금
        // 안 보이는 상태(node-mode)라 클릭 좌표를 그래프 좌표로 쓸 수 없으므로,
        // 대신 그 carrier의 마지막 위치를 기준으로 살짝 흩어(겹치지 않게) 배치한다.
        const carrierId = currentOpenNodeGraphId();
        const carrierPos = carrierId ? _nodePositions.get(carrierId) : null;
        if (result?.type && result?.label && carrierPos) {
          _pendingSpawnPosition = {
            id: `${result.type}:${result.label}`,
            x: carrierPos.x + (Math.random() - 0.5) * 50,
            y: carrierPos.y + (Math.random() - 0.5) * 50,
          };
        }
        await loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
      },
    });
  });
}

// 진입점 2: 그래프 화면 독립 버튼 - 타입/carrier 논문/concept 연결 전부 사용자가 고른다.
document.getElementById('btnAddNode').addEventListener('click', async () => {
  const container = document.getElementById('createNodeStandaloneContainer');
  if (container.innerHTML.trim()) { container.innerHTML = ''; return; } // 토글: 다시 누르면 닫힘

  let papers = [];
  try {
    const res = await fetch('/api/papers');
    if (res.ok) papers = (await res.json()).papers || [];
  } catch { /* 아래서 빈 목록으로 처리됨 */ }

  if (!papers.length) {
    container.innerHTML = '<p class="node-view-usernotes-empty">먼저 논문을 하나 이상 처리해야 새 노드를 연결할 수 있어요.</p>';
    return;
  }

  openCreateNodePanel(container, {
    carrierOptions: papers,
    needsConcepts: true,
    onCreated: () => loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0),
  });
});

toggleTagsInput.addEventListener('change', () => {
  hideTagNodes = !toggleTagsInput.checked;
  if (currentGraphData) renderGraph(currentGraphData);
});

toggleEntitiesInput.addEventListener('change', () => {
  hideEntityNodes = !toggleEntitiesInput.checked;
  if (currentGraphData) renderGraph(currentGraphData);
});

toggleAttachmentsInput.addEventListener('change', () => {
  hideAttachmentNodes = !toggleAttachmentsInput.checked;
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

async function loadGraph(focusSlugs, onlyFocus = false) {
  clearSearch(); // 그래프가 통째로 새로 그려지면 이전 검색 결과/하이라이트는 더 이상 유효하지 않다
  focusSlugs = focusSlugs ? (Array.isArray(focusSlugs) ? focusSlugs : [focusSlugs]) : [];
  currentFocusSlugs = onlyFocus ? focusSlugs : [];
  if (!onlyFocus) {
    document.getElementById('graphSummary').innerHTML = '';
  }

  const params = new URLSearchParams();
  for (const slug of focusSlugs) params.append('focus', slug);
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

  // 상단바 통계(index.html) - 논문 토글로 필터링된 focus 그래프 말고, 전체
  // 그래프를 불러온 시점(!onlyFocus)에만 갱신한다. 그래야 "몇 개 켰을 때"의
  // 부분 개수가 아니라 Brain 전체의 실제 개념/용어 노드 수를 보여준다.
  if (!onlyFocus) {
    const statNodesEl = document.getElementById('statNodes');
    if (statNodesEl) {
      const nodeCount = data.nodes.filter((n) => n.type === 'concept' || n.type === 'entity').length;
      statNodesEl.textContent = `노드 ${nodeCount}개`;
    }
  }

  if (onlyFocus && currentFocusSlugs.length) {
    const labels = currentFocusSlugs.map((slug) => {
      const node = data.nodes.find((n) => n.id === slug);
      return node ? node.label : slug;
    });
    graphTitleEl.textContent = `${labels.join(', ')} Knowledge Graph`;
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
  const zoomBehavior = d3.zoom()
    .scaleExtent([0.3, 3])
    // 노드(hitArea) 위에서 시작된 이벤트는 배경 팬으로 취급하지 않는다 - 이걸
    // 안 하면 hitArea의 d3.drag()로 노드를 옮기는 동안 같은 이벤트가 부모인
    // graphSvg까지 버블링돼 d3.zoom()도 동시에 팬을 시작해버려서, 노드를
    // 드래그하면 배경도 같이 움직이는 것처럼 보인다. drag() 쪽에서
    // stopPropagation()도 걸어두지만, event.target을 직접 확인하는 이 필터가
    // 더 확실하다(d3-zoom 공식 문서가 권장하는 패턴).
    .filter((event) => !event.target.closest('.node-hit-area'))
    // 팬 가능 범위를 캔버스 크기 기준으로 제한한다. 제한이 없으면 마우스처럼
    // 한 동작에 큰 픽셀 이동량이 들어오는 입력 장치에서 그래프 전체가 뷰포트
    // 밖으로 팬되어 "사라진 것처럼" 보이는 문제가 있었다(트랙패드는 이동
    // 거리가 작아 잘 드러나지 않았음).
    .translateExtent([[-width, -height], [width * 2, height * 2]])
    .on('zoom', (event) => g.attr('transform', event.transform));
  graphSvg.call(zoomBehavior);
  _graphZoom = zoomBehavior; // 검색 결과 클릭 시 panToPoint()가 이 인스턴스로 화면을 이동시킨다

  const visibleNodes = data.nodes.filter((n) => {
    if (hideTagNodes && n.type === 'tag') return false;
    if (hideEntityNodes && n.type === 'entity') return false;
    if (hideAttachmentNodes && n.type === 'attachment') return false;
    return true;
  });
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = data.edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));

  const edgeNodeIds = new Set();
  visibleEdges.forEach((e) => { edgeNodeIds.add(e.source); edgeNodeIds.add(e.target); });

  // "진짜"(그 자신도 앵커가 없는) 노드에 실제로 연결된 노드만 더 이상 orphan이
  // 아니다 - forceLink가 알아서 그쪽으로 끌어당긴다. 앵커끼리(둘 다 orphan)만
  // 서로 연결된 경우(orphan entity를 orphan concept으로 드래그해 붙이는 등)는
  // 둘 다 여전히 진짜 그래프에는 안 붙어있으므로 앵커를 유지한다 - 안 그러면
  // 한쪽만 물리 시뮬레이션에 풀려나(charge -160로 반발) 나머지 그래프 전체의
  // 반발력에 밀려 원래 있던 자리에서 한참 먼 곳으로 튕겨나간다.
  for (const id of [..._orphanAnchors.keys()]) {
    const hasRealNeighbor = visibleEdges.some((e) => {
      if (e.source !== id && e.target !== id) return false;
      const other = e.source === id ? e.target : e.source;
      return !_orphanAnchors.has(other);
    });
    if (hasRealNeighbor) _orphanAnchors.delete(id);
  }

  // _orphanAnchors는 브라우저 메모리에만 있어 새로고침/서버 재시작으로 사라진다 -
  // 그럴 땐 노드 파일에 저장해둔 anchor_id(생성 당시 가장 가까웠던 노드, app.py의
  // create_orphan_node가 저장)로 앵커를 새로 만든다. 정확한 픽셀 오프셋은 세션마다
  // 의미가 없으므로(화면 크기/줌 상태가 다를 수 있음) 작은 기본 오프셋만 준다 -
  // 어차피 매 tick마다 계속 갱신되므로 시작값은 "겹치지만 않으면" 충분하다.
  for (const n of visibleNodes) {
    if (!edgeNodeIds.has(n.id) && !_orphanAnchors.has(n.id) && n.anchor_id) {
      _orphanAnchors.set(n.id, { anchorId: n.anchor_id, dx: 20, dy: -14 });
    }
  }

  // 전체 그래프 뷰(currentFocusSlugs가 비어있음, 즉 켜진 논문 토글이 하나도
  // 없음)는 노드가 훨씬 많아(수백 개) "진짜" 노드까지 이전 위치를 계속
  // 이어받으면, 조작할 때마다(orphan 하나만 새로 만들어도 전체 reload가
  // 일어남) 레이아웃이 조금씩 넓게 드리프트하다가 결국 팬 가능 범위
  // (translateExtent) 밖으로 나가버린다 - 그래서 전체 뷰는 "진짜" 노드만큼은
  // 매번 완전히 새로(d3 기본 초기 배치) 자리잡게 두고, 포커스 뷰(논문 토글이
  // 하나 이상 켜진 상태, 노드 수가 적어 드리프트가 문제되지 않음)에서만
  // 이어받는다.
  const isFullGraphView = currentFocusSlugs.length === 0;

  // 방금 만든 노드(자동연결 포함)는 최초 한 프레임만 원하는 자리(우클릭 지점/carrier
  // 근처)에서 시작하게 한다 - 에지 유무와 무관하게 적용(자동연결 노드는 이미 에지가
  // 생겨 있어 아래 orphan 전용 처리 대상이 아니므로 이 초기 시드가 유일한 위치 힌트).
  const nodes = visibleNodes.map((n) => {
    if (_pendingSpawnPosition?.id === n.id) {
      const spawn = { ...n, x: _pendingSpawnPosition.x, y: _pendingSpawnPosition.y };
      // orphan(에지 없음)으로 막 생성된 노드는 그 자리에 완전히 고정한다 -
      // 특히 전체 그래프 뷰는 매번 강하게 재정렬되므로 고정이 없으면 첫 틱부터
      // 주변 수백 개 노드의 반발력에 밀려 클릭 지점을 순식간에 벗어난다.
      // 이미 에지가 있는(자동연결) 노드는 고정하지 않는다 - 연결된 이웃 옆으로
      // 자연스럽게 자리잡아야 하므로 시작 위치 힌트만 준다.
      if (!edgeNodeIds.has(n.id)) { spawn.fx = spawn.x; spawn.fy = spawn.y; }
      return spawn;
    }
    const pos = _nodePositions.get(n.id);
    if (!edgeNodeIds.has(n.id) && !_orphanAnchors.has(n.id)) {
      // 앵커도 없고(우클릭 생성이 아니었거나 이미 지워짐) 방금 만든 것도 아닌
      // 에지 없는 노드는, 마지막으로 알려진 자리에 그대로 고정해서 큰 그래프가
      // 다시 자리잡는 동안 밀려나지 않게 한다.
      if (pos) return { ...n, x: pos.x, y: pos.y, fx: pos.x, fy: pos.y };
    } else if (pos && !isFullGraphView) {
      // 에지가 있거나(논문 등 "진짜" 노드) 앵커가 있는 노드도, 포커스 뷰에서는
      // 마지막으로 알려진 자리를 시작 위치로 물려받는다(고정은 안 함 - 힘
      // 시뮬레이션은 계속 작동해야 하므로).
      return { ...n, x: pos.x, y: pos.y };
    }
    return { ...n };
  });
  _pendingSpawnPosition = null;
  const links = visibleEdges.map((e) => ({ ...e }));
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // 앵커를 따라다니는 orphan은 위치가 이미 fx/fy로 완전히 결정돼 있어(위 tick
  // 핸들러가 매번 다시 계산) 반발력/충돌 반경이 전혀 필요 없다 - 오히려 앵커
  // 바로 옆(작은 고정 오프셋)에 딱 붙어서 일반 세기로 반발하면, 그 반발력이 정작
  // 진짜 연결된 앵커 노드 자신을 원래 있어야 할 클러스터 밖으로 밀어내 버린다.
  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(70).strength(0.4))
    .force('charge', d3.forceManyBody().strength((d) => (_orphanAnchors.has(d.id) ? 0 : -160)))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide((d) => (_orphanAnchors.has(d.id) ? 0 : 20)));

  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', 'graph-link')
    .attr('stroke-width', 1.2);

  // 눈에 보이는 선(1.2px)은 우클릭하기엔 너무 가늘다 - 노드의 hitArea와 같은
  // 이유로, 투명하고 훨씬 굵은 선을 그 위에 겹쳐 그려 실제 반응 영역만 넓힌다.
  const linkHitArea = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', 'transparent')
    .attr('stroke-width', 12)
    .attr('class', 'graph-link-hit-area');

  // note↔concept, note↔entity(직접 연결), concept↔entity 에지만 사용자가 직접
  // 끊을 수 있다 - tag/논문 간 위키링크/첨부파일 에지는 논문 본문 자연어나
  // frontmatter를 직접 편집해야 해서 위험도가 다르다(지금 범위 밖). d3.forceLink가
  // 초기화되면서 link.source/target이 문자열 id에서 실제 노드 객체 참조로
  // 바뀌므로, 여기서는 d.source.type/d.target.type을 바로 쓸 수 있다.
  function handleLinkContextMenu(event, d) {
    event.preventDefault();
    event.stopPropagation();
    const { source, target } = d;

    if (source.type === 'note' && (target.type === 'concept' || target.type === 'entity') && target.node_slug) {
      showContextMenu(event.clientX, event.clientY, [
        { label: '연결 끊기', onClick: () => deleteEdgeWithConfirm(
            `${source.label} ↔ ${target.label}`,
            () => callDeleteSourceApi(target.type, target.node_slug, source.id),
          ) },
      ]);
      return;
    }
    if (source.type === 'concept' && target.type === 'entity' && source.node_slug && target.node_slug) {
      showContextMenu(event.clientX, event.clientY, [
        { label: '연결 끊기', onClick: () => deleteEdgeWithConfirm(
            `${source.label} ↔ ${target.label}`,
            () => callUnlinkConceptApi(target.node_slug, source.node_slug),
          ) },
      ]);
    }
    // 그 외 조합(tag, 논문 간 링크, 첨부파일 등)은 메뉴를 띄우지 않는다.
  }
  linkHitArea.on('contextmenu', handleLinkContextMenu);

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
    });

  // 실제 눈에 보이는 원(6~8px)은 클릭/우클릭/드래그 시작점을 정확히 맞추기엔 너무
  // 작다 - 특히 0.5초 홀드+드래그 연결 제스처는 애초에 그 작은 원 위에 포인터를 놓는
  // 것부터 실패하기 쉽다. 그래서 보이지 않는(투명 채움) 더 큰 원을 눈에 보이는 원
  // 위에 하나 더 겹쳐 그리고, 모든 포인터 상호작용(드래그/우클릭/클릭/hover)은
  // 이 큰 원에만 건다 - 시각적으로는 원래 크기 그대로지만 실제로 반응하는 영역은
  // 훨씬 넓어진다. fill="transparent"는 fill="none"과 달리 그 영역 전체가 여전히
  // 클릭 가능하다(SVG pointer-events 기본값 visiblePainted 기준).
  const hitArea = g.append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', 16)
    .attr('fill', 'transparent')
    .attr('class', 'node-hit-area')
    .call(drag(simulation, nodes, g, onConnectDrop, node));

  // 노드 위 우클릭 -> 삭제 메뉴 1개. stopPropagation으로 배경 우클릭 핸들러(그래프
  // 배경 우클릭 = 생성 메뉴)로 이벤트가 새지 않게 막는다. note/tag/attachment나
  // 아직 node_store 파일이 없는(node_slug 없음) 노드는 삭제 대상이 아니라 메뉴 자체를
  // 띄우지 않는다.
  function handleNodeContextMenu(event, d) {
    event.preventDefault();
    event.stopPropagation();
    if ((d.type !== 'concept' && d.type !== 'entity') || !d.node_slug) return;
    showContextMenu(event.clientX, event.clientY, [
      { label: '삭제', onClick: () => deleteNodeWithConfirm(d.type, d.node_slug, d.label) },
    ]);
  }
  hitArea.on('contextmenu', handleNodeContextMenu);

  const label = g.append('g')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .attr('class', 'graph-label')
    .attr('dx', 12)
    .attr('dy', 4)
    .text((d) => d.label);
  // label(.graph-label)은 CSS에서 pointer-events: none이라 여기 handler를 달아도
  // 실제로는 절대 발동하지 않는다 - 노드 상호작용은 hitArea 하나로 통일한다.

  // 검색창(모듈 스코프)이 이 렌더의 node/label/link selection에 하이라이트를
  // 걸 수 있도록 참조를 넘겨둔다 - loadGraph()마다 renderGraph가 다시 불려
  // selection 자체가 매번 새로 만들어지므로 매번 갱신해야 한다.
  _nodeSel = node;
  _labelSel = label;
  _linkSel = link;

  // 호버한 노드와 직접 연결된 노드/에지만 원래대로 두고 나머지는 흐리게 만든다
  // (Obsidian 그래프 뷰의 호버 강조와 동일한 방식). 강조 자체는 눈에 보이는 node/label에
  // 적용하지만, hover가 시작되는 판정 영역은 더 넓은 hitArea 기준이다.
  hitArea
    .on('mouseenter', (event, d) => highlightNode(d.id))
    .on('mouseleave', clearHighlight);

  // 다른 논문/개념 노드로 드래그해 연결할 때(onConnectDrop), source/target이 어떤
  // 조합이면 어느 API를 어떻게 호출해야 하는지 판단한다. 규칙: concept -> note,
  // entity -> note, entity -> concept(그 concept의 sources 중에서 골라 paper_slug로
  // 씀 - 1개면 바로, 2개 이상이면 드롭 지점에 논문 선택 메뉴)만 유효하고 나머지
  // 조합(note가 source, concept<->concept, entity<->entity, concept -> entity 등)은
  // 무시한다.
  async function onConnectDrop(source, target, sourceEvent) {
    if ((source.type !== 'concept' && source.type !== 'entity') || !source.node_slug) return;

    if (target.type === 'note') {
      await callLinkApi(source.type, source.node_slug, target.id, null);
      return;
    }
    if (source.type === 'entity' && target.type === 'concept') {
      const paperIds = [...new Set(
        data.edges.filter((e) => e.type === 'link' && e.target === target.id).map((e) => e.source)
      )].filter((id) => data.nodes.some((n) => n.id === id && n.type === 'note'));

      // orphan concept(연결된 논문이 하나도 없음) - 붙일 논문이 없으니 논문 없이
      // concept_slug만으로 연결한다(node_store.link_node_to_paper 참고).
      if (!paperIds.length) {
        await callLinkApi('entity', source.node_slug, null, target.node_slug);
        return;
      }

      // concept이 이미 연결된 논문 전부에 entity를 연결한다(하나만 골라 물어보지
      // 않음) - concept 자체가 그 논문들과 이미 연결돼 있으니, entity도 전부와
      // 연결돼야 나중에 특정 논문↔concept 에지 하나만 지웠을 때 그 논문에
      // 한정해서만 entity의 concept 그룹핑이 풀리는 일관된 삭제 동작이 나온다
      // (하나만 연결했다면 다른 논문↔concept 에지를 지워도 entity 쪽엔 반영할
      // 대상 자체가 없다). 같은 파일을 순차로 갱신해야 하므로 병렬(Promise.all)
      // 대신 하나씩 await한다.
      for (const paperId of paperIds) {
        await callLinkApi('entity', source.node_slug, paperId, target.node_slug, { reload: false });
      }
      loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
    }
  }

  // reload=false로 여러 번 부른 뒤 마지막에 한 번만 loadGraph()하면(entity를
  // concept이 걸린 논문 전부에 연결하는 루프처럼) 중간에 그래프가 여러 번
  // 깜빡이며 다시 그려지는 걸 피할 수 있다.
  async function callLinkApi(nodeType, nodeSlug, paperSlug, conceptSlug, { reload = true } = {}) {
    try {
      const res = await fetch(`/api/nodes/${nodeType}/${encodeURIComponent(nodeSlug)}/link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paper_slug: paperSlug, concept_slug: conceptSlug || null }),
      });
      if (!res.ok) throw new Error('link failed');
      if (reload) loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
    } catch {
      alert('연결에 실패했습니다.');
    }
  }

  // d3.drag가 붙은 요소는 드래그 도중 마우스가 조금이라도 움직이면 그 뒤에 오는
  // 브라우저 native 'click' 이벤트를 d3-drag가 내부적으로 삼켜버린다(알려진
  // 동작). 그 native click에 기대는 대신, pointerdown/pointerup을 직접 붙여서
  // "누른 지점과 뗀 지점이 화면 픽셀 기준으로 충분히 가깝고 빠르게 끝났으면
  // 클릭"으로 판정한다 - 화면 좌표(clientX/Y)를 쓰므로 그래프를 확대/축소해도
  // 판정 기준이 흔들리지 않는다. Obsidian 그래프 뷰도 노드 클릭 시 같은 방식
  // (누른 위치 근처에서 뗐는지)으로 클릭과 드래그를 구분한다.
  let pointerDownAt = null;
  hitArea
    .on('pointerdown', (event) => {
      if (event.button !== 0) return; // 우클릭(2)/휠클릭(1)은 컨텍스트 메뉴 전용 - 클릭 열기 판정에서 제외
      pointerDownAt = { x: event.clientX, y: event.clientY, t: Date.now() };
    })
    .on('pointerup', (event, d) => {
      if (!pointerDownAt || event.button !== 0) return;
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
    label
      .classed('dimmed', (d) => !connectedIds.has(d.id))
      .classed('hovered', (d) => d.id === hoveredId);
    link
      .classed('dimmed', (d, i) => !connectedLinkIndexes.has(i))
      .classed('highlighted', (d, i) => connectedLinkIndexes.has(i));
  }

  function clearHighlight() {
    label.classed('hovered', false);
    link.classed('highlighted', false);
    // 검색어가 입력된 채로 노드에 호버했다 벗어나는 경우, hover 강조가 걷히면
    // 검색 하이라이트 상태로 되돌아가야 한다(전부 지우면 검색 결과가 사라진
    // 것처럼 보인다) - 없으면 평소처럼 완전히 지운다.
    const activeQuery = graphSearchInput.value.trim();
    if (activeQuery) {
      applySearchHighlight(new Set(matchGraphNodes(activeQuery).map((n) => n.id)));
      return;
    }
    node.classed('dimmed', false);
    label.classed('dimmed', false);
    link.classed('dimmed', false);
  }

  simulation.on('tick', () => {
    // orphan을 그 기준 노드의 "지금" 위치 + 생성 당시의 상대 오프셋으로 계속
    // 따라다니게 한다 - 기준 노드 자체가 이번 렌더에서 어디로 자리잡든(포커스 뷰든
    // 수백 개짜리 전체 그래프든) orphan은 항상 그 옆에 붙어있는다. 지금 사용자가
    // 이 orphan 자체를 드래그 중이면 건드리지 않는다(안 그러면 드래그와 힘겨루기 함).
    _orphanAnchors.forEach((anchor, orphanId) => {
      const orphanNode = nodeById.get(orphanId);
      if (!orphanNode || orphanNode._dragging) return;
      const anchorNode = nodeById.get(anchor.anchorId);
      if (!anchorNode) return;

      // 기준 노드(다른 orphan일 수도 있음) 자체를 지금 사용자가 드래그하고
      // 있으면, 이 팔로워를 실시간으로 같이 끌고 다니지 않는다 - 화면엔 그대로
      // 있는 채로 "지금 화면 위치 기준" 상대 오프셋만 계속 갱신해둔다. 그래야
      // 드래그가 끝난 뒤에도 갑자기 튀지 않고(오프셋이 이미 지금 위치에 맞게
      // 갱신돼 있으므로) 자연스럽게 다시 따라가기 시작한다.
      if (anchorNode._dragging) {
        anchor.dx = orphanNode.x - anchorNode.x;
        anchor.dy = orphanNode.y - anchorNode.y;
        return;
      }

      orphanNode.x = orphanNode.fx = anchorNode.x + anchor.dx;
      orphanNode.y = orphanNode.fy = anchorNode.y + anchor.dy;
    });

    link
      .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    linkHitArea
      .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    hitArea.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    label.attr('x', (d) => d.x).attr('y', (d) => d.y);
    nodes.forEach((d) => _nodePositions.set(d.id, { x: d.x, y: d.y }));
  });
}

// 노드 하나를 0.5초 이상 누르고 있으면(미세하게 움직여도 취소되지 않음) 위치이동
// 대신 "다른 노드로 드래그해 연결" 모드로 바뀐다: 임시 점선을 포인터를 따라
// 그리다가, 다른 노드 위에서 놓으면 onConnectDrop(source, target, sourceEvent)을
// 호출한다. 0.5초가 되기 전에 놓으면 지금까지와 똑같이 위치이동으로 끝난다.
// d3.drag의 event.x/y는 이 노드가 속한 <g>(확대/축소 transform 적용된 그룹) 기준
// 로컬 좌표라, nodes 배열의 x/y(시뮬레이션 좌표)와 그대로 비교/사용할 수 있다.
const CONNECT_HOLD_MS = 500;
// 눈에 보이는 원 반지름(6~8px)이 아니라 실제 반응 영역인 hitArea 반지름(16)에
// 맞춘다 - 드롭 판정도 클릭/드래그 시작 판정만큼 넉넉해야 한다.
const CONNECT_HIT_RADIUS = 18;

function drag(sim, nodes, g, onConnectDrop, visibleNode) {
  return d3.drag()
    .on('start', (event, d) => {
      // hitArea에서 시작된 pointerdown이 부모인 graphSvg까지 그대로 버블링되면,
      // 거기 걸려 있는 d3.zoom(배경 팬)의 리스너도 같은 이벤트를 받아 동시에
      // 팬을 시작해버린다(노드를 드래그하는데 배경도 같이 움직이는 원인). 노드
      // 위에서 시작된 드래그는 배경 팬으로 새지 않게 여기서 막는다.
      event.sourceEvent.stopPropagation();
      d._connecting = false;
      d._dragging = true; // renderGraph()의 tick 핸들러가 이 노드의 앵커를 강제 적용하지 않게 막는 플래그
      d._reheated = false; // 이번 제스처에서 아직 시뮬레이션을 재가열하지 않았음
      d._dragStartX = d.x;
      d._dragStartY = d.y;
      d._holdTimer = setTimeout(() => {
        d._connecting = true;
        // 0.5초가 실제로 지나서 연결 모드로 들어갔다는 걸 눈에 보이게 알려준다 -
        // 안 그러면 지금 홀드가 인식됐는지 사용자가 전혀 알 길이 없다(점선
        // 미리보기는 그 자리에서 움직이지 않으면 길이가 0이라 안 보임).
        visibleNode.filter((n) => n === d).classed('node-connecting', true);
        d._previewLine = g.append('line')
          .attr('class', 'graph-connect-preview')
          .attr('x1', d.x).attr('y1', d.y)
          .attr('x2', d.x).attr('y2', d.y);
      }, CONNECT_HOLD_MS);

      // 시뮬레이션 재가열(alphaTarget)은 실제로 노드를 옮기기 시작할 때만 한다
      // (아래 'drag' 핸들러) - 여기 'start'에서 무조건 재가열하면, 눌린 노드
      // 자신은 fx/fy로 고정돼 있어도 노드가 수백 개인 전체 그래프 뷰에서는
      // 이 재가열 하나로 그래프 전체가 들썩여서, 그냥 누르고만 있는(연결 모드
      // 홀드) 동안에도 그 노드가 움직이는 것처럼 보이는 원인이 된다. 순수한
      // 홀드는 노드 위치를 전혀 안 바꾸므로(마우스가 안 움직이면 'drag' 이벤트
      // 자체가 안 옴) 애초에 재가열이 필요 없다.
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag', (event, d) => {
      if (d._connecting) {
        d._previewLine.attr('x2', event.x).attr('y2', event.y);
        return; // 연결 모드에서는 노드 자체 위치(fx/fy)를 옮기지 않는다
      }
      // event.active는 'start'/'end' 쌍에서만 의도대로 동작하는 값이라(d3-drag
      // 문서 참고) 'drag' 이벤트에서 그대로 쓰면 항상 거짓으로 평가돼 재가열이
      // 아예 안 일어난다 - 제스처당 한 번만 재가열하면 되므로 직접 플래그로
      // 관리한다.
      if (!d._reheated) {
        d._reheated = true;
        sim.alphaTarget(0.3).restart();
      }
      d.fx = event.x; d.fy = event.y;
    })
    .on('end', (event, d) => {
      clearTimeout(d._holdTimer);
      d._dragging = false;
      if (!event.active) sim.alphaTarget(0);

      if (d._connecting) {
        d._connecting = false;
        visibleNode.filter((n) => n === d).classed('node-connecting', false);
        d._previewLine?.remove();
        d._previewLine = null;
        d.fx = null; d.fy = null;
        const target = nodes.find((n) => n !== d && Math.hypot(n.x - event.x, n.y - event.y) < CONNECT_HIT_RADIUS);
        if (target) onConnectDrop(d, target, event.sourceEvent);
        return;
      }

      // 정말로(단순 클릭이 아니라) 옮겼는데 이 노드가 앵커 추적 중이었다면, 추적
      // 자체는 유지하되 기준 노드로부터의 오프셋을 방금 옮긴 새 위치로 다시
      // 계산한다 - 그래야 드래그로 옮긴 게 다음 tick에 예전 오프셋으로 도로
      // 튕겨나가지 않는다. 추적을 아예 끊는 건 실제로 연결됐을 때만 한다
      // (renderGraph 참고).
      const anchor = _orphanAnchors.get(d.id);
      if (anchor && Math.hypot(d.x - d._dragStartX, d.y - d._dragStartY) > 5) {
        const anchorNode = nodes.find((n) => n.id === anchor.anchorId);
        if (anchorNode) {
          anchor.dx = d.x - anchorNode.x;
          anchor.dy = d.y - anchorNode.y;
        }
      }
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
    return;
  }
  if (d.type === 'attachment') {
    openAttachment(d);
  }
}

// 첨부 이미지 노드는 별도 md 파일이 없으므로 openNodeView 대신 이미지 하나만
// node-mode 패널에 띄운다. concept/entity 첨부는 node_store.py가 만든 경로라
// /attachments로 바로 서빙되지만(attachmentUrl), note에 Obsidian이 붙여넣은
// 첨부는 vault 어디 있는지 여기서 알 수 없어 서버가 대신 찾아 서빙하는
// /api/vault-attachment를 거친다.
function openAttachment(d) {
  const url = d.owner_type === 'note'
    ? `/api/vault-attachment?note_slug=${encodeURIComponent(d.owner_slug)}&filename=${encodeURIComponent(d.src)}`
    : attachmentUrl(d.src);

  document.getElementById('nodeModeBody').innerHTML = `
    <div class="node-view-title">${escapeHtml(d.label)}</div>
    <div class="node-view-body"><img class="md-img" src="${url}" alt="${escapeHtml(d.label)}"></div>
  `;
  document.body.classList.add('node-mode');
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
    // data-md-src에 마크다운 원문 그대로의 경로를 남겨둔다 - attachmentUrl()이 화면
    // 표시용으로 바꾼 src("/attachments/...")가 아니라 이 원본을 써야, 편집 후
    // serializeToMarkdown()이 저장할 때 경로가 슬래시 유무 등으로 매번 바뀌지 않는다.
    return stash(`<img class="md-img" src="${attachmentUrl(src)}" data-md-src="${escapeHtml(src)}" alt="${alt}">`);
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
    const trimmedTarget = target.trim();
    const resolved = links?.[trimmedTarget];
    // data-wikilink-target에 원래 타깃 텍스트를 남겨둔다 - resolved.slug는 그
    // 타깃을 매칭시킨 결과(정규화/퍼지 매칭 슬러그)라 원문과 다를 수 있어서,
    // 이것 없이는 편집 후 [[타깃|라벨]] 형태를 원문 그대로 복원할 수 없다.
    if (resolved) {
      return stash(
        `<span class="md-wikilink md-wikilink-clickable" data-node-type="${resolved.type}" data-node-slug="${escapeHtml(resolved.slug)}" data-wikilink-target="${escapeHtml(trimmedTarget)}">${label}</span>`
      );
    }
    return stash(`<span class="md-wikilink" data-wikilink-target="${escapeHtml(trimmedTarget)}">${label}</span>`);
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

// 목록 항목들(들여쓰기 폭·마커 종류·내용만 뽑아둔 평평한 배열)을 들여쓰기 기준
// 트리로 묶어 중첩 <ul>/<ol>로 그린다. Turndown이 블로그의 계층 있는 목록(항목
// 아래 하위 항목)을 들여쓰기로 표현해서 내보내므로, 부모/자식 관계를 그대로
// 살리려면 한 줄씩 독립적으로 처리해선 안 되고 이렇게 통째로 봐야 한다.
// 들여쓰기가 불규칙해 부모를 못 찾는 항목이 있어도(둘째 while의 첫 분기) 무한
// 루프에 빠지거나 항목이 사라지지 않고, 그 자리에서 새 목록으로 취급된다.
function buildNestedListHtml(items, links) {
  let i = 0;
  const render = (baseIndent) => {
    let out = '';
    while (i < items.length && items[i].indent >= baseIndent) {
      if (items[i].indent > baseIndent) { out += render(items[i].indent); continue; }
      const type = items[i].type;
      let inner = '';
      while (i < items.length && items[i].indent === baseIndent && items[i].type === type) {
        const content = renderInline(items[i].content, links);
        i++;
        const nested = (i < items.length && items[i].indent > baseIndent) ? render(items[i].indent) : '';
        inner += `<li>${content}${nested}</li>`;
      }
      out += `<${type === 'ol' ? 'ol' : 'ul'} class="md-${type}">${inner}</${type === 'ol' ? 'ol' : 'ul'}>`;
    }
    return out;
  };
  return render(items[0].indent);
}

function renderMarkdown(markdown, links = {}) {
  const parts = [];
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
  const flushAll = () => { flushParagraph(); closeBlockquote(); flushTable(); };

  // 마커(-, 1.) + 들여쓰기 폭을 함께 뽑는다 - 중첩 목록 인식은 들여쓰기가 있는 그대로의
  // rawLine을 봐야 하므로, 다른 모든 검사에 쓰는 trim된 line과는 별도로 확인한다.
  const listItemRe = /^(\s*)([-*]|\d+\.)\s+(.*)$/;

  const lines = markdown.split('\n');
  for (let idx = 0; idx < lines.length; idx++) {
    const rawLine = lines[idx];
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
      flushParagraph(); closeBlockquote();
      tableBuffer.push(line);
      continue;
    }
    flushTable();

    const bq = line.match(/^>\s?(.*)$/);
    if (bq) {
      flushParagraph();
      if (!bqOpen) { parts.push('<blockquote class="md-blockquote">'); bqOpen = true; }
      parts.push(`<p>${renderInline(bq[1], links)}</p>`);
      continue;
    }
    closeBlockquote();

    // 목록: 이 줄부터 시작해 연속으로 이어지는 목록 항목(들여쓰기 깊이 무관)을
    // 한 번에 다 모은 뒤 buildNestedListHtml()에 통째로 넘긴다 - 한 줄씩 처리하면
    // 중첩된 하위 목록을 부모 항목과의 관계 없이 별개의 형제 항목으로 오인하게 된다
    // (Turndown이 블로그의 계층 있는 목록을 들여쓰기로 표현해서 내보낼 때 특히 문제).
    if (listItemRe.test(rawLine)) {
      flushParagraph();
      const items = [];
      while (idx < lines.length) {
        const m = listItemRe.exec(lines[idx]);
        if (!m) break;
        items.push({ indent: m[1].length, type: /\d/.test(m[2]) ? 'ol' : 'ul', content: m[3] });
        idx++;
      }
      idx--; // for 루프의 idx++가 다음 미처리 줄로 이어지도록 마지막으로 소비한 줄에 맞춰둔다
      parts.push(buildNestedListHtml(items, links));
      continue;
    }

    paragraph.push(line);
  }
  flushAll();
  return parts.join('\n');
}

// concept/entity 노드 삭제(+참조하던 논문 frontmatter 정리)를 공용화한 함수 -
// 노드 뷰의 삭제 버튼과 그래프 노드 우클릭 삭제 메뉴가 같은 confirm() + DELETE
// 호출 + 그래프 새로고침 로직을 공유한다. onBeforeReload는 노드 뷰가 열려 있을
// 때만(그래프 화면에서 우클릭 삭제한 경우는 이미 그래프 화면이라 불필요) node-mode를
// 벗어나기 위해 넘긴다.
async function deleteNodeWithConfirm(type, slug, title, onBeforeReload) {
  if (!confirm(`"${title}" 노드를 삭제할까요?\n이 노드를 참조하던 모든 논문에서도 연결이 제거됩니다. 되돌릴 수 없습니다.`)) {
    return;
  }

  // concept이고 그 밑에 entity가 걸려있으면, "entity도 같이 지울지"를 한 번 더
  // 물어본다 - 기본(취소)은 기존 동작 그대로(entity는 남기고 concept 연결만 해제).
  let cascadeEntities = false;
  if (type === 'concept') {
    try {
      const res = await fetch(`/api/nodes/concept/${encodeURIComponent(slug)}/linked-entities`);
      if (res.ok) {
        const { entities } = await res.json();
        if (entities.length) {
          cascadeEntities = confirm(
            `이 개념에 연결된 엔티티가 ${entities.length}개 있습니다 (${entities.map((e) => e.label).join(', ')}).\n\n` +
            `확인: 엔티티도 함께 삭제\n취소: 엔티티는 남기고 개념 연결만 해제`
          );
        }
      }
    } catch { /* 조회 실패해도 기본 동작(entity 유지)으로 계속 진행 */ }
  }

  try {
    const qs = cascadeEntities ? '?cascade_entities=true' : '';
    const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}${qs}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('delete failed');
    onBeforeReload?.();
    loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
  } catch {
    alert('삭제에 실패했습니다.');
  }
}

// 그래프에서 에지(연결)를 사용자가 직접 끊는 공용 확인+새로고침 로직 - 노드
// 삭제(deleteNodeWithConfirm)와 같은 패턴이다. apiCall은 실제 DELETE 요청을
// 보내는 함수(callDeleteSourceApi 또는 callUnlinkConceptApi) 중 하나.
async function deleteEdgeWithConfirm(edgeLabel, apiCall) {
  if (!confirm(`"${edgeLabel}" 연결을 끊을까요?\n두 노드 자체는 그대로 남고 이 연결만 사라집니다.`)) {
    return;
  }
  try {
    await apiCall();
    loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
  } catch {
    alert('연결 끊기에 실패했습니다.');
  }
}

// note↔concept, note↔entity(직접 연결) 에지 끊기 - 그 노드의 sources[]에서
// 논문 항목 하나를 지운다(node_store.remove_source_from_node).
async function callDeleteSourceApi(nodeType, nodeSlug, paperSlug) {
  const res = await fetch(
    `/api/nodes/${nodeType}/${encodeURIComponent(nodeSlug)}/sources/${encodeURIComponent(paperSlug)}`,
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error('delete source failed');
}

// concept↔entity 에지 끊기 - entity의 sources[] 중 이 concept_slug와 일치하는
// 항목을 전부 처리한다(node_store.unlink_concept_from_entity).
async function callUnlinkConceptApi(entitySlug, conceptSlug) {
  const res = await fetch(
    `/api/nodes/entity/${encodeURIComponent(entitySlug)}/concept/${encodeURIComponent(conceptSlug)}`,
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error('unlink concept failed');
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

  currentOpenNode = { type, slug, title: data.title, sources: data.meta?.sources || [] };

  const metaChips = [];
  if (data.type === 'note') {
    if (data.meta.authors) metaChips.push(`<span>저자: ${escapeHtml(data.meta.authors)}</span>`);
    if (data.meta.tags?.length) metaChips.push(`<span>${escapeHtml(data.meta.tags.map((t) => '#' + t).join(' '))}</span>`);
  }
  // concept/entity의 "등장 논문 N편" + 목록은 이제 본문(auto_markdown)이 순서대로
  // 보여주므로(node_store._write_node_file 참고) 여기서 따로 칩으로 요약하지 않는다.

  // note(논문)는 아직 편집 대상이 아니고, concept/entity 노드 파일만 개인 메모
  // 편집(+이미지 붙여넣기)을 지원한다.
  const editable = data.type === 'concept' || data.type === 'entity';

  // obsidian_writer.py/node_store.py 둘 다 본문을 "# {제목}"으로 시작한다
  // (Obsidian에서 파일을 직접 열었을 때 제목이 보이도록). 여기선 위
  // .node-view-title이 이미 같은 제목을 보여주므로 걷어낸다. concept/entity는
  // 제목 다음에 "다른 표기"/"카테고리"/"등장 논문 N편"도 오는데, 이 전부 위쪽
  // 메타 행(node-view-aliases 등)이 이미 일관된 스타일로 보여주고 있어 - 본문
  // 마크다운의 **볼드**는 본문 기본 글자색(--text)이라 메타 행의 옅은 색
  // (--text-muted)과 안 맞기도 하고, 그대로 두면 정보가 중복 표시된다.
  // 본문은 "등장 논문" 목록(실제 클릭 가능한 링크)부터만 보여준다 -
  // node_store._write_node_file()이 만드는 순서를 그대로 아는 상태로 걷어내는
  // 것이라, 그 함수의 순서가 바뀌면 이 정규식도 같이 바꿔야 한다.
  const bodyMarkdown = editable
    ? (data.auto_markdown || '').replace(
        /^#[^\n]*\n\n\*\*다른 표기\*\*:[^\n]*\n\n(?:\*\*카테고리\*\*:[^\n]*\n\n)?\*\*등장 논문\*\*[^\n]*\n/,
        ''
      )
    : data.body_markdown.replace(/^#\s+.*(\n+|$)/, '');

  const userMarkdown = data.user_markdown || '';
  const userNotesHtml = userMarkdown.trim()
    ? renderMarkdown(userMarkdown, data.links)
    : '<p class="node-view-usernotes-empty">클릭해서 메모를 남기세요 (Ctrl+S로 저장, Esc로 취소)</p>';

  bodyEl.innerHTML = `
    ${editable ? `
      <div class="node-view-title-row">
        <div class="node-view-title">${escapeHtml(data.title)}</div>
        <button class="graph-btn" id="btnRenameNode" title="이름 변경">이름 변경</button>
      </div>
      <div id="renameNodeContainer"></div>
    ` : `
      <div class="node-view-title">${escapeHtml(data.title)}</div>
    `}
    <div class="node-view-meta">${metaChips.join('')}</div>
    ${editable ? `
      <div class="node-view-aliases">
        <span class="node-view-aliases-label">다른 표기</span>
        <span id="nodeAliasChips">${renderAliasChips(data.meta.aliases || [])}</span>
        <button class="graph-btn" id="btnAddAlias">+ 별칭</button>
      </div>
      <div id="addAliasContainer"></div>
    ` : ''}
    ${data.type === 'concept' ? `
      <div class="node-view-aliases">
        <span class="node-view-aliases-label">카테고리</span>
        <span id="nodeCategoryChips">${renderCategoryChips(data.meta.categories || [])}</span>
        <button class="graph-btn" id="btnAddCategory">+ 카테고리</button>
      </div>
      <div id="addCategoryContainer"></div>
    ` : ''}
    ${editable ? `
      <div class="node-view-aliases">
        <span class="node-view-aliases-label">등장 논문</span>
        <span>${(data.meta.sources || []).filter((s) => s.slug).length}편</span>
      </div>
    ` : ''}
    ${data.type === 'note' ? `
      <div class="node-view-side-panel">
        <button class="graph-btn" id="btnAddNodeToNote">+ 개념/엔티티 추가</button>
        <div id="createNodeInNoteContainer"></div>
      </div>
    ` : ''}
    ${data.type === 'concept' ? `
      <div class="node-view-side-panel">
        <div class="node-view-create-bar">
          <button class="graph-btn" id="btnAddEntityToConcept">+ 엔티티 추가</button>
          <button class="graph-btn" id="btnDeleteNode">삭제</button>
        </div>
        <div id="createNodeInConceptContainer"></div>
      </div>
    ` : ''}
    ${data.type === 'entity' ? `
      <div class="node-view-side-panel"><button class="graph-btn" id="btnDeleteNode">삭제</button></div>
    ` : ''}
    <div class="node-view-body">${renderMarkdown(bodyMarkdown, data.links)}</div>
    ${editable ? `
      <div class="node-view-usernotes">
        <div class="node-view-body node-view-usernotes-rendered" id="nodeViewUserNotesRendered">${userNotesHtml}</div>
        <span class="node-view-edit-status" id="nodeViewUserNotesStatus"></span>
      </div>
    ` : ''}
  `;

  if (editable) wireUserNotesEditing(type, slug, userMarkdown, data.links);
  if (editable) wireAliasEditing(type, slug);
  if (editable) wireRenameEditing(type, slug, data.title);
  if (data.type === 'concept') wireCategoryEditing(slug, data.meta.categories || []);

  // concept/entity 노드는 (LLM이 뽑았든 사용자가 직접 만들었든) 삭제 가능 - 노드
  // 파일뿐 아니라 이 노드를 참조하던 논문들의 frontmatter까지 서버가 같이 정리한다.
  // 그래프에서 노드 우클릭 -> 삭제 메뉴도 같은 deleteNodeWithConfirm()을 쓴다.
  if (editable) {
    document.getElementById('btnDeleteNode').addEventListener('click', () => {
      deleteNodeWithConfirm(type, slug, data.title, () => document.body.classList.remove('node-mode'));
    });
  }

  // 진입점 1: 논문 노드 뷰에서 이 논문을 carrier로 고정하고 개념/엔티티를 바로 추가.
  if (data.type === 'note') {
    document.getElementById('btnAddNodeToNote').addEventListener('click', () => {
      const container = document.getElementById('createNodeInNoteContainer');
      if (container.innerHTML.trim()) { container.innerHTML = ''; return; } // 토글: 다시 누르면 닫힘
      openCreateNodePanel(container, {
        fixedCarrier: { slug: data.slug, title: data.title },
        needsConcepts: true,
        onCreated: () => loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0),
      });
    });
  }

  // 진입점 3: concept 노드 뷰에서 entity 타입 고정 + concept 고정, carrier 논문만
  // 이 concept의 sources(이미 이 concept을 참조하는 논문들) 중에서 고르면 됨.
  if (data.type === 'concept') {
    document.getElementById('btnAddEntityToConcept').addEventListener('click', () => {
      const container = document.getElementById('createNodeInConceptContainer');
      if (container.innerHTML.trim()) { container.innerHTML = ''; return; }
      const sources = data.meta.sources || [];
      if (!sources.length) {
        container.innerHTML = '<p class="node-view-usernotes-empty">연결된 논문이 없어 엔티티를 추가할 수 없습니다.</p>';
        return;
      }
      openCreateNodePanel(container, {
        fixedType: 'entity',
        fixedConcept: { label: data.title, slug: data.slug },
        carrierOptions: sources,
        onCreated: () => loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0),
      });
    });
  }

  renderMermaidBlocks();
}

function renderAliasChips(aliases) {
  if (!aliases.length) return '<span class="node-view-usernotes-empty">없음</span>';
  return aliases
    .map((a) => `<span class="alias-chip">${escapeHtml(a)}<button type="button" class="alias-chip-remove" data-alias="${escapeHtml(a)}" title="별칭 삭제">×</button></span>`)
    .join('');
}

// LLM이 판단한 별칭이 항상 맞는 건 아니다(놓친 표기가 있거나, 반대로 실제로는
// 다른 개념인데 같다고 오판했을 수도 있음) - 사용자가 노드 화면에서 직접 별칭을
// 추가/삭제할 수 있게 한다. 서버가 이미 다른 노드가 쓰는 표기(alias_taken)를
// 걸러주므로, 여기서는 그 응답을 사용자에게 그대로 보여주기만 하면 된다.
function wireAliasEditing(type, slug) {
  const chipsEl = document.getElementById('nodeAliasChips');
  const addContainer = document.getElementById('addAliasContainer');

  chipsEl.addEventListener('click', async (event) => {
    const btn = event.target.closest('.alias-chip-remove');
    if (!btn) return;
    try {
      const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/aliases`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias: btn.dataset.alias }),
      });
      if (!res.ok) throw new Error();
      const { aliases } = await res.json();
      chipsEl.innerHTML = renderAliasChips(aliases);
    } catch {
      alert('별칭 삭제에 실패했습니다.');
    }
  });

  document.getElementById('btnAddAlias').addEventListener('click', () => {
    if (addContainer.innerHTML.trim()) { addContainer.innerHTML = ''; return; } // 토글: 다시 누르면 닫힘
    addContainer.innerHTML = `
      <div class="create-node-panel">
        <div class="create-node-row">
          <label>별칭</label>
          <input type="text" id="newAliasInput" placeholder="예: MLA">
        </div>
        <div class="create-node-actions">
          <button class="graph-btn" id="btnConfirmAddAlias">추가</button>
          <button class="graph-btn" id="btnCancelAddAlias">취소</button>
          <span class="node-view-edit-status" id="aliasAddStatus"></span>
        </div>
      </div>
    `;
    document.getElementById('btnCancelAddAlias').addEventListener('click', () => { addContainer.innerHTML = ''; });
    document.getElementById('btnConfirmAddAlias').addEventListener('click', async () => {
      const input = document.getElementById('newAliasInput');
      const statusEl = document.getElementById('aliasAddStatus');
      const alias = input.value.trim();
      if (!alias) { statusEl.textContent = '별칭을 입력하세요.'; return; }
      statusEl.textContent = '추가 중...';
      try {
        const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/aliases`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ alias }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const message = typeof err.detail === 'string' ? err.detail : (err.detail?.message || '추가에 실패했습니다.');
          throw new Error(message);
        }
        const { aliases } = await res.json();
        chipsEl.innerHTML = renderAliasChips(aliases);
        addContainer.innerHTML = '';
      } catch (err) {
        statusEl.textContent = err.message;
      }
    });
  });
}

// LLM이 맨 처음 고른 표기가 항상 이 개념을 가장 잘 설명하는 건 아니다(예:
// "Mamba-2 Architecture"보다 "Mamba-2"). slug(파일명/다른 노드가 참조하는 키)는
// 그대로 두고 display_label만 바꾼다 - node_store.rename_display_label() 참고.
// 성공하면 예전 이름은 자동으로 별칭이 되고(다른 논문 본문의 기존 위키링크가
// 계속 풀리도록), 뷰 전체를 새 이름으로 다시 불러온다(별칭 목록도 같이 바뀌었으므로).
function wireRenameEditing(type, slug, currentTitle) {
  document.getElementById('btnRenameNode').addEventListener('click', () => {
    const container = document.getElementById('renameNodeContainer');
    if (container.innerHTML.trim()) { container.innerHTML = ''; return; } // 토글: 다시 누르면 닫힘
    container.innerHTML = `
      <div class="create-node-panel">
        <div class="create-node-row">
          <label>이름</label>
          <input type="text" id="renameLabelInput" value="${escapeHtml(currentTitle)}">
        </div>
        <div class="create-node-actions">
          <button class="graph-btn" id="btnConfirmRename">변경</button>
          <button class="graph-btn" id="btnCancelRename">취소</button>
          <span class="node-view-edit-status" id="renameStatus"></span>
        </div>
      </div>
    `;
    document.getElementById('btnCancelRename').addEventListener('click', () => { container.innerHTML = ''; });
    document.getElementById('btnConfirmRename').addEventListener('click', async () => {
      const input = document.getElementById('renameLabelInput');
      const statusEl = document.getElementById('renameStatus');
      const label = input.value.trim();
      if (!label) { statusEl.textContent = '이름을 입력하세요.'; return; }
      statusEl.textContent = '변경 중...';
      try {
        const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/display-label`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ label }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const message = typeof err.detail === 'string' ? err.detail : (err.detail?.message || '변경에 실패했습니다.');
          throw new Error(message);
        }
        await openNodeView(type, slug, label);
      } catch (err) {
        statusEl.textContent = err.message;
      }
    });
  });
}

function renderCategoryChips(categories) {
  if (!categories.length) return '<span class="node-view-usernotes-empty">없음</span>';
  return categories
    .map((c) => `<span class="alias-chip">${escapeHtml(c)}<button type="button" class="alias-chip-remove" data-category="${escapeHtml(c)}" title="카테고리 삭제">×</button></span>`)
    .join('');
}

// LLM이 매긴 카테고리가 항상 사용자 마음에 들 리 없고, 모호한 개념은 여러
// 카테고리에 동시에 걸칠 수도 있어 concept 화면에서 직접 추가/삭제할 수 있게
// 한다. alias와 달리 카테고리는 통제 어휘(CONCEPT_CATEGORIES)라 자유 텍스트
// 입력 대신 드롭다운으로만 고르게 한다 - 표기만 다른 카테고리가 늘어나는 걸
// 막기 위함(node_store.py의 add_category()도 이 목록 밖의 값을 거부한다).
function wireCategoryEditing(slug, initialCategories) {
  let categories = initialCategories;
  const chipsEl = document.getElementById('nodeCategoryChips');
  const addContainer = document.getElementById('addCategoryContainer');

  chipsEl.addEventListener('click', async (event) => {
    const btn = event.target.closest('.alias-chip-remove');
    if (!btn) return;
    try {
      const res = await fetch(`/api/nodes/concept/${encodeURIComponent(slug)}/categories`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: btn.dataset.category }),
      });
      if (!res.ok) throw new Error();
      ({ categories } = await res.json());
      chipsEl.innerHTML = renderCategoryChips(categories);
    } catch {
      alert('카테고리 삭제에 실패했습니다.');
    }
  });

  document.getElementById('btnAddCategory').addEventListener('click', () => {
    if (addContainer.innerHTML.trim()) { addContainer.innerHTML = ''; return; } // 토글: 다시 누르면 닫힘
    const remaining = CONCEPT_CATEGORIES.filter((c) => !categories.includes(c));
    if (!remaining.length) {
      addContainer.innerHTML = '<p class="node-view-usernotes-empty">이미 모든 카테고리가 추가되어 있습니다.</p>';
      return;
    }
    addContainer.innerHTML = `
      <div class="create-node-panel">
        <div class="create-node-row">
          <label>카테고리</label>
          <select id="newCategoryInput">
            ${remaining.map((c) => `<option value="${c}">${c}</option>`).join('')}
          </select>
        </div>
        <div class="create-node-actions">
          <button class="graph-btn" id="btnConfirmAddCategory">추가</button>
          <button class="graph-btn" id="btnCancelAddCategory">취소</button>
          <span class="node-view-edit-status" id="categoryAddStatus"></span>
        </div>
      </div>
    `;
    document.getElementById('btnCancelAddCategory').addEventListener('click', () => { addContainer.innerHTML = ''; });
    document.getElementById('btnConfirmAddCategory').addEventListener('click', async () => {
      const category = document.getElementById('newCategoryInput').value;
      const statusEl = document.getElementById('categoryAddStatus');
      statusEl.textContent = '추가 중...';
      try {
        const res = await fetch(`/api/nodes/concept/${encodeURIComponent(slug)}/categories`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(typeof err.detail === 'string' ? err.detail : '추가에 실패했습니다.');
        }
        ({ categories } = await res.json());
        chipsEl.innerHTML = renderCategoryChips(categories);
        addContainer.innerHTML = '';
      } catch (err) {
        statusEl.textContent = err.message;
      }
    });
  });
}

// concept/entity 생성 POST 하나를 공용으로 처리한다 - 서버가 "완전히 같은 이름"이
// 아니라 "비슷한 노드가 이미 있음"(409, detail.type === 'similar_exists')으로
// 응답하면, 사용자에게 그대로 새로 만들지 물어보고(confirm) 그렇다면 같은 요청을
// force:true로 다시 보낸다. 사용자가 취소하면 에러가 아니라 null을 반환한다(호출부가
// "만들지 않음"으로 조용히 처리하도록) - 기존 노드를 연결하고 싶으면 그 노드를 직접
// 찾아 드래그로 연결하면 된다는 안내만 남긴다.
async function postWithDuplicateCheck(url, body) {
  let res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    if (err.detail && typeof err.detail === 'object' && err.detail.type === 'similar_exists') {
      const proceed = confirm(
        `이미 비슷한 노드가 있습니다: "${err.detail.existing.label}"\n\n` +
        `확인: 그래도 새로 만들기\n취소: 만들지 않기 (기존 노드를 찾아 드래그로 연결해보세요)`
      );
      if (!proceed) return null;
      res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, force: true }),
      });
      if (!res.ok) {
        const err2 = await res.json().catch(() => ({}));
        throw new Error(typeof err2.detail === 'string' ? err2.detail : '생성에 실패했습니다.');
      }
    } else {
      throw new Error(typeof err.detail === 'string' ? err.detail : '생성에 실패했습니다.');
    }
  }
  return res.json().catch(() => ({}));
}

// 사용자가 직접 concept/entity 노드를 만드는 인라인 패널(모달 아님) - 트리거
// 버튼 바로 아래 컨테이너에 폼을 그려 넣는다. 세 진입점(논문 뷰/concept 뷰/그래프
// 독립 버튼)이 이 함수 하나를 서로 다른 config로 재사용한다.
//   config.fixedType       - 'concept' | 'entity' | undefined(사용자가 고름)
//   config.fixedCarrier    - {slug, title} | undefined(사용자가 고름)
//   config.carrierOptions  - fixedCarrier가 없을 때 고를 논문 목록 [{slug, title}]
//   config.fixedConcept    - {label, slug} | undefined(entity일 때 사용자가 고름)
//   config.needsConcepts   - true면 concept 목록을 미리 fetch해 entity 연결 드롭다운에 씀
//   config.orphan          - true면 "연결할 논문" 행 자체를 숨기고 POST /api/nodes로 만든다
//                             (그래프 배경 우클릭 진입점 - 나중에 드래그로 따로 연결)
//   config.orphanAnchorId  - orphan일 때, 생성 당시 가장 가까웠던 노드의 id. 서버에
//                             같이 저장해뒀다가 새로고침/재시작 후에도 그 근처에
//                             나타나게 한다(renderGraph 참고)
//   config.prefillLabel    - 이름 입력칸 초깃값(텍스트 선택 우클릭 진입점이 씀)
//   config.onCreated       - 생성 성공 시 호출. {slug, label, type}을 받는다(orphan
//                             생성 직후 그래프에서 하이라이트하려면 label/type이 필요).
function openCreateNodePanel(container, config) {
  let concepts = [];

  // 지금 패널이 "연결할 논문"으로 실제로 쓰고 있는(또는 쓸) 논문 slug. 고정
  // carrier면 그 값, 아니면 지금 렌더된 <select id="cnCarrier">의 값(아직 렌더
  // 전이면 목록의 첫 항목 - <select>가 별도 selected 없이 첫 옵션을 기본
  // 선택하는 것과 맞춘다).
  const currentCarrierSlug = () => {
    if (config.orphan) return null;
    if (config.fixedCarrier) return config.fixedCarrier.slug;
    return document.getElementById('cnCarrier')?.value || config.carrierOptions?.[0]?.slug || null;
  };

  // entity 생성 시 고를 수 있는 concept은 항상 "지금 이 carrier 논문에 실제로
  // 연결된 concept"으로만 제한한다 - 무관한 concept을 고르면 entity의
  // concept_slug는 그 concept을 가리키는데 concept 자신의 sources엔 이 논문이
  // 없는 비일관 상태(그래프에 논문↔concept 에지가 없는데 concept↔entity
  // 에지만 생김)가 된다.
  const loadConceptsFor = async (carrierSlug) => {
    if (!config.needsConcepts || config.fixedConcept || !carrierSlug) return [];
    try {
      const res = await fetch(`/api/concepts?paper_slug=${encodeURIComponent(carrierSlug)}`);
      if (res.ok) return (await res.json()).concepts || [];
    } catch { /* 못 불러오면 빈 목록으로 진행 - entity를 논문에 직접 연결하는 것까진 여전히 가능 */ }
    return [];
  };

  const render = () => {
    const type = config.fixedType || (document.getElementById('cnType')?.value ?? 'concept');
    container.innerHTML = `
      <div class="create-node-panel">
        ${!config.fixedType ? `
          <div class="create-node-row">
            <label>타입</label>
            <select id="cnType">
              <option value="concept"${type === 'concept' ? ' selected' : ''}>개념 (concept)</option>
              <option value="entity"${type === 'entity' ? ' selected' : ''}>엔티티 (entity)</option>
            </select>
          </div>
        ` : ''}
        <div class="create-node-row">
          <label>이름</label>
          <input type="text" id="cnLabel" placeholder="예: Self-Attention" value="${escapeHtml(config.prefillLabel || '')}">
        </div>
        ${type === 'concept' ? `
          <div class="create-node-row">
            <label>카테고리</label>
            <select id="cnCategory">
              ${CONCEPT_CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join('')}
            </select>
          </div>
        ` : `
          <div class="create-node-row">
            <label>연결할 개념 (선택)</label>
            ${config.fixedConcept
              ? `<input type="text" value="${escapeHtml(config.fixedConcept.label)}" disabled>`
              : `<select id="cnConcept">
                  <option value="">(없음 - 논문에 직접 연결)</option>
                  ${concepts.map((c) => `<option value="${escapeHtml(c.slug)}">${escapeHtml(c.label)}</option>`).join('')}
                </select>`
            }
          </div>
        `}
        ${!config.orphan ? `
          <div class="create-node-row">
            <label>연결할 논문</label>
            ${config.fixedCarrier
              ? `<input type="text" value="${escapeHtml(config.fixedCarrier.title)}" disabled>`
              : `<select id="cnCarrier">
                  ${config.carrierOptions.map((p) => `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.title)}</option>`).join('')}
                </select>`
            }
          </div>
        ` : ''}
        <div class="create-node-actions">
          <button class="graph-btn" id="cnSubmit">생성</button>
          <button class="graph-btn" id="cnCancel">취소</button>
          <span class="node-view-edit-status" id="cnStatus"></span>
        </div>
      </div>
    `;
    document.getElementById('cnCancel').addEventListener('click', () => { container.innerHTML = ''; });
    document.getElementById('cnType')?.addEventListener('change', render);
    document.getElementById('cnSubmit').addEventListener('click', submit);

    // 연결할 논문을 바꾸면 그 논문 기준으로 concept 목록을 다시 불러와 concept
    // <select>만 갱신한다(패널 전체를 다시 그리면 이미 입력한 이름 등이
    // 지워지므로, 그 select만 targeted하게 바꾼다).
    document.getElementById('cnCarrier')?.addEventListener('change', async (event) => {
      concepts = await loadConceptsFor(event.target.value);
      const conceptSelect = document.getElementById('cnConcept');
      if (conceptSelect) {
        conceptSelect.innerHTML = `<option value="">(없음 - 논문에 직접 연결)</option>` +
          concepts.map((c) => `<option value="${escapeHtml(c.slug)}">${escapeHtml(c.label)}</option>`).join('');
      }
    });
  };

  const submit = async () => {
    const type = config.fixedType || document.getElementById('cnType').value;
    const label = document.getElementById('cnLabel').value.trim();
    const statusEl = document.getElementById('cnStatus');
    if (!label) { statusEl.textContent = '이름을 입력하세요.'; return; }
    const carrierSlug = config.orphan
      ? null
      : (config.fixedCarrier ? config.fixedCarrier.slug : document.getElementById('cnCarrier').value);
    if (!config.orphan && !carrierSlug) { statusEl.textContent = '연결할 논문을 선택하세요.'; return; }

    let url, body;
    if (config.orphan) {
      body = { type, label, anchor_id: config.orphanAnchorId || null };
      if (type === 'concept') body.category = document.getElementById('cnCategory').value;
      url = '/api/nodes';
    } else if (type === 'concept') {
      body = { label, category: document.getElementById('cnCategory').value };
      url = `/api/papers/${encodeURIComponent(carrierSlug)}/concepts`;
    } else {
      const conceptSlug = config.fixedConcept ? config.fixedConcept.slug : (document.getElementById('cnConcept').value || null);
      body = { label, concept_slug: conceptSlug };
      url = `/api/papers/${encodeURIComponent(carrierSlug)}/entities`;
    }

    statusEl.textContent = '생성 중...';
    try {
      const result = await postWithDuplicateCheck(url, body);
      if (!result) { statusEl.textContent = '만들지 않았습니다.'; return; } // 중복 확인에서 사용자가 취소
      container.innerHTML = '';
      config.onCreated?.({ ...result, label, type });
    } catch (err) {
      statusEl.textContent = err.message || '생성에 실패했습니다.';
    }
  };

  (async () => {
    concepts = await loadConceptsFor(currentCarrierSlug());
    render();
  })();
}

// renderMarkdown()은 mermaid 코드펜스를 <pre class="mermaid"> 텍스트로만 만들어둔다 -
// 실제 다이어그램 SVG로 그리는 건 mermaid.js가 그 요소를 보고 나서야 할 수 있으므로,
// DOM에 innerHTML로 끼워넣은 "다음"에 별도로 호출해야 한다.
function renderMermaidBlocks() {
  if (typeof mermaid === 'undefined') return;
  const blocks = document.querySelectorAll('#nodeModeBody pre.mermaid');
  if (blocks.length) mermaid.run({ nodes: blocks });
}

// Obsidian처럼 렌더링된 메모를 클릭하면 그 화면 자체가 바로 편집 가능한 상태
// (contenteditable)로 바뀐다 - 이미지·굵게·위키링크가 그대로 보이는 채로 타이핑할
// 수 있다. Ctrl+S를 누르면 그 시점의 DOM을 다시 우리 마크다운 문법으로
// 직렬화해(serializeToMarkdown) 저장하고 읽기 전용 렌더 모드로 돌아간다. Esc는
// 마지막 저장 상태로 되돌리고 취소한다. userMarkdown은 클로저에 들고 있다가
// 저장 성공 시에만 갱신한다.
function wireUserNotesEditing(type, slug, userMarkdown, links) {
  const renderedEl = document.getElementById('nodeViewUserNotesRendered');
  const statusEl = document.getElementById('nodeViewUserNotesStatus');
  let editing = false;

  const emptyHtml = '<p class="node-view-usernotes-empty">클릭해서 메모를 남기세요 (Ctrl+S로 저장, Esc로 취소)</p>';
  const renderRendered = () => {
    renderedEl.innerHTML = userMarkdown.trim() ? renderMarkdown(userMarkdown, links) : emptyHtml;
  };

  const enterEdit = () => {
    if (editing) return;
    editing = true;
    if (!userMarkdown.trim()) renderedEl.innerHTML = '';
    renderedEl.contentEditable = 'true';
    renderedEl.classList.add('node-view-usernotes-editing');
    // Enter가 <div> 대신 <p>를 만들게 해서 serializeToMarkdown이 기대하는
    // 블록 구조와 브라우저 편집 결과가 최대한 어긋나지 않게 한다.
    try { document.execCommand('defaultParagraphSeparator', false, 'p'); } catch { /* 구형 브라우저는 기본 동작 유지 */ }
    renderedEl.focus();
    placeCursorAtEnd(renderedEl);
  };

  const exitEdit = () => {
    editing = false;
    renderedEl.contentEditable = 'false';
    renderedEl.classList.remove('node-view-usernotes-editing');
    renderRendered();
  };

  const save = async () => {
    const markdown = serializeToMarkdown(renderedEl);
    statusEl.textContent = '저장 중...';
    try {
      const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/notes`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_notes_markdown: markdown }),
      });
      if (!res.ok) throw new Error('save failed');
      userMarkdown = markdown;
      exitEdit();
      statusEl.textContent = '저장됨';
      setTimeout(() => { if (statusEl.textContent === '저장됨') statusEl.textContent = ''; }, 1500);
    } catch {
      statusEl.textContent = '저장 실패 (Ctrl+S로 재시도)';
    }
  };

  renderedEl.addEventListener('click', (event) => {
    // 편집 중엔 본문 안의 위키링크를 눌러도 (저장 안 된 편집 내용을 잃어버리며)
    // 그래프/다른 노드로 이동해버리지 않도록, 상위 위키링크 클릭 위임으로
    // 이벤트가 번지는 것을 막는다 - 커서만 그 자리로 옮겨진다.
    if (editing) { event.stopPropagation(); return; }
    enterEdit();
  });
  renderedEl.addEventListener('keydown', (event) => {
    if (!editing) return;
    if ((event.ctrlKey || event.metaKey) && event.key === 's') {
      event.preventDefault();
      save();
    } else if (event.key === 'Escape') {
      exitEdit();
    }
  });
  renderedEl.addEventListener('paste', (event) => {
    if (!editing) return; // 편집 중이 아닐 때(contenteditable=false)는 붙여넣기를 무시한다
    handleRichPaste(event, type, slug, links);
  });
}

// 붙여넣기 종류에 따라 분기한다:
// 1) 블로그 등에서 복사한 서식 있는 HTML(text/html)이 있으면, 그 자리에서 바로
//    Turndown으로 우리 마크다운으로 변환한 뒤 renderMarkdown()으로 다시 그려서
//    끼워넣는다 - 붙여넣는 순간부터 이미 "우리 스타일"이라, Ctrl+S로 저장했다가
//    다시 열어도 붙여넣은 직후 모습과 항상 똑같다(서식이 저장 시점에만 깨지는
//    문제를 애초에 만들지 않음).
// 2) HTML 없이 이미지 하나만 있으면(스크린샷 등) 기존처럼 업로드 후 <img> 삽입.
// 3) 둘 다 없으면(순수 텍스트 등) 기본 붙여넣기 동작에 맡긴다.
async function handleRichPaste(event, type, slug, links) {
  const html = event.clipboardData?.getData('text/html');
  if (html && html.trim()) {
    event.preventDefault();
    const markdown = htmlToMarkdown(html);
    document.execCommand('insertHTML', false, renderMarkdown(markdown, links));
    return;
  }
  await handleRichImagePaste(event, type, slug);
}

let _turndownService = null;
function htmlToMarkdown(html) {
  // CDN 로드가 실패했으면(오프라인 등) 서식 변환은 포기하고 태그만 걷어낸 텍스트로
  // 대신 삽입한다 - 이 시점엔 이미 preventDefault()가 호출된 뒤라 브라우저 기본
  // 붙여넣기로는 못 돌아가므로, 완전히 잃는 것보단 텍스트라도 보존한다.
  if (typeof TurndownService === 'undefined') return html.replace(/<[^>]+>/g, ' ').trim();
  if (!_turndownService) {
    _turndownService = new TurndownService({ headingStyle: 'atx', bulletListMarker: '-' });
  }
  return _turndownService.turndown(html);
}

function placeCursorAtEnd(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

async function handleRichImagePaste(event, type, slug) {
  const items = event.clipboardData?.items;
  if (!items) return;
  const imageItem = Array.from(items).find((item) => item.type.startsWith('image/'));
  if (!imageItem) return; // 이미지가 아니면 기본 붙여넣기(서식 있는 텍스트 등)를 그대로 둔다

  event.preventDefault();
  const file = imageItem.getAsFile();
  if (!file) return;

  const previewUrl = URL.createObjectURL(file);
  const placeholderId = `pending-img-${Math.random().toString(36).slice(2, 8)}`;
  document.execCommand('insertHTML', false, `<img class="md-img" id="${placeholderId}" src="${previewUrl}" alt="업로드 중...">`);
  const imgEl = document.getElementById(placeholderId);

  const ext = imageItem.type.split('/')[1] || 'png';
  const formData = new FormData();
  formData.append('file', file, `pasted.${ext}`);

  try {
    const res = await fetch(`/api/nodes/${type}/${encodeURIComponent(slug)}/attachments`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('upload failed');
    const uploaded = await res.json();
    if (imgEl) {
      imgEl.src = attachmentUrl(uploaded.path);
      imgEl.dataset.mdSrc = uploaded.path;
      imgEl.alt = '';
      imgEl.removeAttribute('id');
    }
  } catch {
    if (imgEl) imgEl.alt = '이미지 업로드 실패';
  } finally {
    URL.revokeObjectURL(previewUrl);
  }
}

// contenteditable로 편집된 DOM을 다시 마크다운 문법으로 직렬화한다. renderMarkdown()이
// 만드는 태그(md-h2/h3, md-ul, md-blockquote, md-table-wrap, md-code, mermaid, img,
// wikilink)는 정확히 원래 문법으로 되돌리고, 그 외 브라우저가 타이핑 중에 끼워넣는
// <div>/<p> 등은 문단으로, 알 수 없는 태그는 서식 없이 텍스트만 보존한다(데이터 손실은
// 없지만 그 태그의 서식은 잃을 수 있음).
function serializeToMarkdown(root) {
  const inline = (node) => {
    let out = '';
    node.childNodes.forEach((child) => { out += inlineNode(child); });
    return out;
  };

  const inlineNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/ /g, ' '); // 편집 중 브라우저가 끼워넣는 nbsp를 일반 공백으로
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    const tag = node.tagName.toLowerCase();
    if (tag === 'br') return '\n';
    if (tag === 'strong' || tag === 'b') return `**${inline(node)}**`;
    if (tag === 'img') {
      const src = node.dataset.mdSrc || node.getAttribute('src') || '';
      return `![${node.getAttribute('alt') || ''}](${src})`;
    }
    if (node.classList?.contains('md-wikilink')) {
      const label = inline(node);
      const target = node.dataset.wikilinkTarget || label;
      return target === label ? `[[${target}]]` : `[[${target}|${label}]]`;
    }
    if (tag === 'a') {
      const href = node.getAttribute('href') || '';
      const label = inline(node);
      return label === href ? href : `[${label}](${href})`; // 그냥 붙여넣은 맨 URL은 다시 맨 URL로
    }
    return inline(node); // em/u 등 지원 안 하는 서식은 내용만 보존
  };

  // <li> 하나를 "이 항목 자체의 텍스트"와 "그 아래 중첩된 하위 목록(있다면)"으로
  // 나눠 되돌린다 - inline()에 그냥 넘기면 중첩 <ul>/<ol>의 <li>들까지 구분 없이
  // 한 줄로 뭉개지므로, 목록 자식은 따로 떼어 listToMarkdown()으로 재귀 처리한다.
  const isListEl = (node) =>
    node.nodeType === Node.ELEMENT_NODE &&
    (['ul', 'ol'].includes(node.tagName.toLowerCase()) || node.classList?.contains('md-ul') || node.classList?.contains('md-ol'));

  const liToMarkdown = (li, depth) => {
    let text = '';
    let nested = '';
    li.childNodes.forEach((child) => {
      if (isListEl(child)) nested += '\n' + listToMarkdown(child, depth + 1);
      else text += inlineNode(child);
    });
    return text + nested;
  };

  const listToMarkdown = (listEl, depth) => {
    const isOl = listEl.tagName.toLowerCase() === 'ol' || listEl.classList?.contains('md-ol');
    const indent = '   '.repeat(depth); // 절대 폭은 의미 없고, 부모보다 깊기만 하면 renderMarkdown이 중첩으로 인식한다.
    return Array.from(listEl.children)
      .map((li, i) => `${indent}${isOl ? `${i + 1}. ` : '- '}${liToMarkdown(li, depth)}`)
      .join('\n');
  };

  const blockquoteToMarkdown = (bq) => {
    const lines = [];
    bq.childNodes.forEach((child) => {
      if (child.nodeType === Node.ELEMENT_NODE) lines.push(inline(child));
      else if (child.nodeType === Node.TEXT_NODE && child.textContent.trim()) lines.push(child.textContent.trim());
    });
    return lines.map((line) => `> ${line}`).join('\n');
  };

  const tableToMarkdown = (table) => {
    const rows = Array.from(table.querySelectorAll('tr')).map((tr) =>
      Array.from(tr.children).map((cell) => inline(cell).replace(/\|/g, '\\|'))
    );
    if (!rows.length) return '';
    const rowLine = (cells) => `| ${cells.join(' | ')} |`;
    return [rowLine(rows[0]), rowLine(rows[0].map(() => '---')), ...rows.slice(1).map(rowLine)].join('\n');
  };

  const blocks = [];
  root.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.trim();
      if (text) blocks.push(text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;

    const tag = node.tagName.toLowerCase();
    if (node.classList.contains('md-h2')) { blocks.push(`## ${inline(node)}`); return; }
    if (node.classList.contains('md-h3')) { blocks.push(`### ${inline(node)}`); return; }
    if (tag === 'hr' || node.classList.contains('md-hr')) { blocks.push('---'); return; }
    if (isListEl(node)) {
      // 원래 번호를 보존하려 하지 않고 항상 1부터 순서대로 다시 매긴다(브라우저
      // 편집 중 항목이 추가/삭제돼도 항상 유효한 번호가 나오게).
      blocks.push(listToMarkdown(node, 0));
      return;
    }
    if (node.classList.contains('md-blockquote')) { blocks.push(blockquoteToMarkdown(node)); return; }
    if (node.classList.contains('md-table-wrap')) {
      const table = node.querySelector('table');
      if (table) blocks.push(tableToMarkdown(table));
      return;
    }
    if (tag === 'pre' && node.classList.contains('mermaid')) { blocks.push('```mermaid\n' + node.textContent + '\n```'); return; }
    if (tag === 'pre' && node.classList.contains('md-code')) { blocks.push('```\n' + node.textContent + '\n```'); return; }
    if (tag === 'img') { blocks.push(inlineNode(node)); return; }

    const text = inline(node).trim();
    if (text) blocks.push(text);
  });

  return blocks.join('\n\n');
}

document.getElementById('btnBackToGraph').addEventListener('click', () => {
  document.body.classList.remove('node-mode');
  // 노드 화면에서 메모/첨부 이미지를 편집했을 수 있으니, 뒤에 깔려 있던(편집 전
  // 상태로 멈춰있는) 그래프를 새로고침해서 방금 바뀐 노드/에지가 바로 보이게 한다.
  loadGraph(currentFocusSlugs, currentFocusSlugs.length > 0);
});

// 노드 본문은 매번 innerHTML을 통째로 새로 그리므로(openNodeView), 위키링크마다
// 개별로 리스너를 다는 대신 안 바뀌는 부모 컨테이너에 이벤트 위임 하나만 걸어둔다.
document.getElementById('nodeModeBody').addEventListener('click', (event) => {
  const link = event.target.closest('.md-wikilink-clickable');
  if (!link) return;
  openNodeView(link.dataset.nodeType, link.dataset.nodeSlug, link.textContent);
});

loadGraph([]);
