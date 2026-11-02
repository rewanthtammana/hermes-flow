// One node per agent invocation, with shared branch points and nested tools.
const denseGraph = data.agents.length > 8 || data.interactions.length > 10;
let graphPage = 0, graphTools = !denseGraph, graphZoom = 1;
let graphInteractionMode = denseGraph ? (data.comparison?.deviations ? 'deviations' : 'none') : 'all';
function drawGraph(view) {
  view.append(make('h3', 'Agent flow graph'), make('p',
    'Forward arrows show lineage or correlated handoffs. Optional interaction lines show recorded requests, results, or messages; grey reverse lines show session endings where result delivery is unverified. Dashed tool edges show usage. Click an agent to focus its subtree; the interaction table below remains available.', 'muted'));
  if (!selected && (children.get(null) || []).length > 1) {
    view.append(make('p', `This report contains ${children.get(null).length} separate session roots. Select an agent in the sidebar to follow one flow.`, 'muted'));
  }
  const controls = make('div', undefined, 'toolbar');
  const toggle = make('button', graphTools ? 'Hide tools' : 'Show tools', 'secondary');
  toggle.onclick = () => { graphTools = !graphTools; draw(); };
  const smaller = make('button', '−', 'secondary'), larger = make('button', '+', 'secondary');
  smaller.setAttribute('aria-label', 'Zoom out'); larger.setAttribute('aria-label', 'Zoom in');
  smaller.onclick = () => { graphZoom = Math.max(.25, graphZoom - .25); draw(); };
  larger.onclick = () => { graphZoom = Math.min(2, graphZoom + .25); draw(); };
  controls.append(toggle, smaller, make('span', Math.round(graphZoom * 100) + '%'), larger);
  const interactionSelect = make('select'); interactionSelect.setAttribute('aria-label', 'Interaction lines');
  for (const [value, label] of [['none', 'No interaction lines'], ['deviations', 'New connections only'], ['all', 'All interaction lines']]) {
    if (value === 'deviations' && !data.comparison) continue;
    const option = make('option', label); option.value = value; interactionSelect.append(option);
  }
  interactionSelect.value = graphInteractionMode;
  interactionSelect.onchange = () => { graphInteractionMode = interactionSelect.value; draw(); };
  controls.append(interactionSelect);
  const fit = make('button', 'Fit width', 'secondary');
  fit.onclick = () => { graphZoom = Math.max(.1, Math.min(1, (wrap.clientWidth - 12) / width)); draw(); };
  controls.append(fit);
  if (selected) {
    const back = make('button', 'All agents', 'secondary'); back.onclick = () => choose(null); controls.append(back);
    const parent = byId.get(selected).parent;
    if (byId.has(parent)) { const up = make('button', 'Parent: ' + byId.get(parent).name, 'secondary'); up.onclick = () => choose(parent); controls.append(up); }
  }
  view.append(controls);
  const roots = selected ? [byId.get(selected)] : (children.get(null) || []).slice(graphPage * 50, (graphPage + 1) * 50);
  const nodes = [], rootNodes = [], pending = roots.slice().reverse().map(a => ({a, parent: null, depth: 0}));
  let agentCount = 0, omitted = 0;
  while (pending.length) {
    const {a, parent, depth} = pending.pop();
    if (agentCount >= 200) { omitted++; if (parent) parent.hidden = true; continue; }
    agentCount++;
    const n = {key: a.id, agent: a, label: a.name, detail: a.id, bad: a.connection_status === 'deviation', depth, children: [], parent};
    nodes.push(n); if (parent) parent.children.push(n); else rootNodes.push(n);
    if (graphTools) {
      const counts = new Map();
      for (const e of grouped.get(a.id)) {
        if (!counts.has(e.tool)) counts.set(e.tool, {count: 0, bad: false});
        const t = counts.get(e.tool); t.count++; t.bad ||= e.connection_status === 'deviation';
      }
      for (const [tool, value] of Array.from(counts).slice(0, 12)) {
        const t = {key: a.id + ':tool:' + tool, label: tool, detail: value.count + ' calls · tool usage', bad: value.bad, depth: depth + 1, parent: n, children: []};
        n.children.push(t); nodes.push(t);
      }
      if (counts.size > 12) n.hiddenTools = counts.size - 12;
    }
    // Child agents and tool leaves share a single connected tree.
    for (const child of (children.get(a.id) || []).slice().reverse()) pending.push({a: child, parent: n, depth: depth + 1});
  }
  // Post-order layout assigns each leaf a distinct row; parents sit between children.
  const order = [], stack = rootNodes.slice().reverse();
  while (stack.length) { const n = stack.pop(); order.push(n); for (const c of n.children.slice().reverse()) stack.push(c); }
  let row = 0;
  for (const n of order.slice().reverse()) {
    n.x = 24 + n.depth * 300;
    if (!n.children.length) n.y = 30 + row++ * 100;
    else n.y = (n.children[0].y + n.children[n.children.length - 1].y) / 2;
  }
  // Reverse traversal allocates leaf rows bottom-up. Reflect so source order reads top-down.
  for (const n of nodes) n.y = Math.max(30, row * 100 - n.y);
  const width = Math.max(600, ...nodes.map(n => n.x + 290)), height = Math.max(160, row * 100 + 120);
  const ns = 'http://www.w3.org/2000/svg';
  function svgEl(tag, attrs = {}) { const el = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; }
  const wrap = make('div'); wrap.style.overflow = 'auto'; wrap.style.maxHeight = '850px'; wrap.style.border = '1px solid var(--line)'; wrap.style.borderRadius = '8px';
  const svg = svgEl('svg', {width: width * graphZoom, height: height * graphZoom, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Nested agent and tool graph'});
  const defs = svgEl('defs'), marker = svgEl('marker', {id: 'flow-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse'});
  marker.append(svgEl('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#647973'})); defs.append(marker); svg.append(defs);
  const replyMarker = svgEl('marker', {id: 'interaction-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto'});
  replyMarker.append(svgEl('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#2463a0'})); defs.append(replyMarker);
  const deviationMarker = svgEl('marker', {id: 'deviation-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto'});
  deviationMarker.append(svgEl('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#c62828'})); defs.append(deviationMarker);
  for (const n of nodes) {
    if (!n.parent) continue;
    const p = n.parent, x1 = p.x + 230, y1 = p.y + 30, x2 = n.x, y2 = n.y + 30;
    const kind = n.agent ? (n.agent.handoff ? 'handoff' : 'lineage') : 'uses';
    const edge = svgEl('path', {d: `M ${x1} ${y1} C ${x1+35} ${y1}, ${x2-35} ${y2}, ${x2} ${y2}`, fill: 'none', stroke: '#647973', 'stroke-width': 1.5, 'marker-end': 'url(#flow-arrow)', 'data-source': p.key, 'data-target': n.key, 'data-kind': kind});
    edge.setAttribute('data-status', data.comparison ? (n.bad ? 'deviation' : 'trusted') : 'uncompared');
    if (n.bad) { edge.setAttribute('stroke', '#c62828'); edge.setAttribute('stroke-width', '2.5'); edge.setAttribute('marker-end', 'url(#deviation-arrow)'); }
    if (!n.agent) edge.setAttribute('stroke-dasharray', '5 4');
    const title = svgEl('title'); title.textContent = (n.bad ? 'New connection · deviation: ' : '') + (n.agent?.handoff ? 'Correlated call: ' + n.agent.handoff.call_id : kind); edge.append(title); svg.append(edge);
    if (n.agent || n.bad) { const label = svgEl('text', {x: x2 - 5, y: y2 - 9, 'font-size': 10, 'text-anchor': 'end', fill: n.bad ? '#c62828' : '#647973'}); label.textContent = n.bad ? 'New connection' : kind; svg.append(label); }
  }
  const links = new Map();
  for (const interaction of data.interactions || []) {
    const key = JSON.stringify([interaction.source, interaction.target, interaction.kind]);
    if (!links.has(key)) links.set(key, {...interaction, count: 0});
    const link = links.get(key); link.count++; link.bad ||= interaction.connection_status === 'deviation';
  }
  if (graphInteractionMode !== 'none') {
    const visible = new Map(nodes.filter(n => n.agent).map(n => [n.key, n]));
    let lane = 0;
    for (const link of links.values()) {
      if (graphInteractionMode === 'deviations' && !link.bad) continue;
      const from = visible.get(link.source), to = visible.get(link.target);
      if (!from || !to) continue;
      const completion = link.kind === 'completion', color = link.bad ? '#c62828' : completion ? '#7b858b' : '#2463a0';
      let route, labelX, labelY, anchor = 'middle';
      if (from.x === to.x) {
        // Sibling communication runs beside the boxes, never through another node.
        const x = from.x + 230, y1 = from.y + 45, y2 = to.y + 18, bend = x + 40 + (lane++ % 2) * 6;
        route = `M ${x} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x} ${y2}`;
        labelX = bend + 3; labelY = (y1+y2)/2; anchor = 'start';
      } else {
        const forward = from.x < to.x, x1 = from.x + (forward ? 230 : 0), x2 = to.x + (forward ? 0 : 230);
        const y1 = from.y + 48, y2 = to.y + 48;
        if (forward) {
          route = `M ${x1} ${y1} C ${x1+35} ${y1}, ${x2-35} ${y2}, ${x2} ${y2}`;
          labelX = (x1+x2)/2; labelY = (y1+y2)/2 + 24;
        } else {
          // A single arc below the two boxes keeps reverse communication
          // distinct from the forward lineage in the gap between columns.
          const startX = from.x + 115, endX = to.x + 115;
          const startY = from.y + 64, endY = to.y + 64;
          const bendY = Math.max(startY, endY) + 48 + (lane++ % 3) * 18;
          route = `M ${startX} ${startY} C ${startX} ${bendY}, ${endX} ${bendY}, ${endX} ${endY}`;
          labelX = (startX + endX) / 2;
          labelY = (startY + endY) / 8 + bendY * .75 - 9;
        }
      }
      const path = svgEl('path', {d: route,
        fill: 'none', stroke: color, 'stroke-width': 1.8, 'marker-end': completion ? 'url(#flow-arrow)' : 'url(#interaction-arrow)',
        'data-source': link.source, 'data-target': link.target, 'data-kind': link.kind});
      path.setAttribute('data-status', data.comparison ? (link.bad ? 'deviation' : 'trusted') : 'uncompared');
      if (link.bad) { path.setAttribute('marker-end', 'url(#deviation-arrow)'); path.setAttribute('stroke-width', '2.5'); }
      if (completion) path.setAttribute('stroke-dasharray', '3 5');
      const title = svgEl('title'); title.textContent = (link.bad ? 'New connection · deviation: ' : '') + `${link.kind} x${link.count}: ${link.evidence}`; path.append(title); svg.append(path);
      const label = svgEl('text', {x: labelX, y: labelY, 'font-size': 10, fill: color, 'text-anchor': anchor});
      label.textContent = (link.bad ? 'New connection · ' : '') + (completion ? 'ended · delivery unverified' : link.kind) + (link.count > 1 ? ' x' + link.count : ''); svg.append(label);
    }
  }
  for (const n of nodes) {

    const g = svgEl('g', {'data-node-id': n.key, 'data-node-type': n.agent ? 'agent' : 'tool', transform: `translate(${n.x},${n.y})`});
    const title = svgEl('title'); title.textContent = n.label + '\n' + n.detail + (n.agent?.parent && !byId.has(n.agent.parent) ? '\nMissing parent: ' + n.agent.parent : '');
    g.append(title, svgEl('rect', {width: 230, height: 64, rx: n.agent ? 10 : 3, fill: n.agent ? '#e3eee5' : '#f4f7fa', stroke: n.agent ? '#176557' : '#a8bac5', 'stroke-width': n.agent ? 2 : 1}));
    for (const [text, y, size] of [[n.label, 25, 13], [n.detail, 45, 10]]) { const t = svgEl('text', {x: 10, y, 'font-size': size, fill: '#193632'}); t.textContent = text.length > 29 ? text.slice(0,26) + '…' : text; g.append(t); }
    if (n.hidden || n.hiddenTools) { const note = svgEl('text', {x: 4, y: 82, 'font-size': 10, fill: '#943d29'}); note.textContent = n.hidden ? 'More branches: select this agent' : `${n.hiddenTools} more tools: see Activity`; g.append(note); }
    if (n.agent) { g.style.cursor = 'pointer'; g.setAttribute('role', 'button'); g.setAttribute('tabindex', '0'); g.setAttribute('aria-label', 'Focus agent ' + n.label); g.onclick = () => choose(n.agent.id); g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(n.agent.id); } }; }
    svg.append(g);
  }
  wrap.append(svg); view.append(wrap);
  if (links.size) {
    view.append(make('h3', 'Agent interactions'));
    const table = make('table'), head = make('tr');
    for (const text of ['From → to', 'Recorded interaction', 'Count']) head.append(make('th', text));
    const thead = make('thead'); thead.append(head); table.append(thead);
    const body = make('tbody');
    const scope = new Set(nodes.filter(n => n.agent).map(n => n.key));
    for (const link of links.values()) {
      if (!scope.has(link.source) && !scope.has(link.target)) continue;
      const row = make('tr'), endpoints = make('td');
      for (const [i, id] of [link.source, link.target].entries()) {
        if (i) endpoints.append(document.createTextNode(' → '));
        const a = byId.get(id), button = make('button', a ? a.name : id, 'secondary');
        button.disabled = !a; button.onclick = () => choose(id); endpoints.append(button);
      }
      row.append(endpoints, make('td', (link.bad ? 'New connection · deviation · ' : '') + (link.kind === 'completion' ? 'Session ended; result delivery unverified' : link.kind + ' · ' + link.evidence), link.bad ? 'deviation' : ''), make('td', link.count)); body.append(row);
    }
    table.append(body); view.append(table);
    view.append(make('p', 'Interactions with agents outside the visible subtree remain listed here. Arrows describe recorded metadata; they do not show message contents or prove what data was transferred.', 'muted'));
  }
  if (omitted) view.append(make('p', 'Graph limited to 200 agents. Additional branches are marked; select their parent to explore. Full activity remains available.', 'notice'));
  if (!selected) {
    const total = (children.get(null) || []).length, pager = make('div', undefined, 'pager');
    const prev = make('button', '← Previous', 'secondary'), next = make('button', 'Next →', 'secondary');
    prev.disabled = graphPage === 0; next.disabled = (graphPage + 1) * 50 >= total;
    prev.onclick = () => { graphPage--; draw(); }; next.onclick = () => { graphPage++; draw(); };
    pager.append(prev, make('span', `Roots ${graphPage*50+1}–${Math.min((graphPage+1)*50,total)} / ${total} · ${agentCount} agents shown`), next); view.append(pager);
  }
}
