// AutoNote 그래프 뷰: Obsidian 그래프 뷰와 같은 로직(노트=노드, [[위키링크]]/공통 tag=에지)으로
// /api/graph 결과를 d3-force로 렌더링한다. index.html/papers.js에서
// `loadGraph(titleSlug, onlyFocus)`만 호출하면 되도록 전역 함수로 노출한다.

const graphSvg = d3.select('#graphSvg');
const graphTitleEl = document.getElementById('graphTitle');
const btnFullGraph = document.getElementById('btnFullGraph');
const toggleTagsInput = document.getElementById('toggleTags');
let simulation = null;
let currentFocus = null;
let currentGraphData = null;
let hideTagNodes = !toggleTagsInput.checked;

btnFullGraph.addEventListener('click', () => loadGraph(null, false));
toggleTagsInput.addEventListener('change', () => {
  hideTagNodes = !toggleTagsInput.checked;
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
    d3.zoom().scaleExtent([0.3, 3]).on('zoom', (event) => g.attr('transform', event.transform))
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
    .attr('class', (d) => `node-${d.type}`)
    .call(drag(simulation));

  node.append('title').text((d) => d.label);

  const label = g.append('g')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .attr('class', 'graph-label')
    .attr('dx', 12)
    .attr('dy', 4)
    .text((d) => d.label);

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

loadGraph(null);
