'use strict';
const data = JSON.parse(document.getElementById('flow-data').textContent), $ = id => document.getElementById(id);
const make = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
const byId = new Map(data.agents.map(a => [a.id, a])), grouped = new Map(), children = new Map();
for (const a of data.agents) {
  grouped.set(a.id, []);
  const parent = byId.has(a.parent) ? a.parent : null;
  if (!children.has(parent)) children.set(parent, []);
  children.get(parent).push(a);
}
for (const e of data.events) grouped.get(e.session).push(e);
let selected = null, tab = 'graph', query = '', outcome = 'all', page = 0;
const pageSize = 100;
$('subtitle').textContent = data.demo ? 'Synthetic example · no agents launched' : 'Recorded agents, handoffs, and tool activity';
for (const [value, label] of [[data.agents.length, 'Agents / sessions'], [data.events.length, 'Tool calls'], [new Set(data.events.map(e => e.tool)).size, 'Distinct tools'], [data.agents.filter(a => a.handoff).length, 'Correlated handoffs']]) {
  const card = make('div', undefined, 'metric'); card.append(make('b', value), make('span', label)); $('metrics').append(card);
}
if (data.coverage.length) {
  const details = make('details'), list = make('ul'); details.append(make('summary', `Capture notes (${data.coverage.length})`));
  for (const note of data.coverage) list.append(make('li', note));
  details.append(list); $('coverage').append(details);
}
$('footer').textContent = data.note + ' No transcript bodies or general tool arguments. No external resources.';
if (data.comparison) {
  const c = data.comparison, panel = make('div', undefined, 'notice'), list = make('ul', undefined, 'comparison-list');
  panel.append(make('h3', `${c.deviations} new connection${c.deviations === 1 ? '' : 's'} · trusted path comparison`));
  for (const link of c.connections.filter(l => l.status === 'deviation')) {
    list.append(make('li', `${link.source} → ${link.target} · ${link.kind} · ${link.count} observation${link.count === 1 ? '' : 's'}`, 'deviation'));
  }
  if (c.deviations) panel.append(list);
  for (const warning of c.identity_warnings || []) panel.append(make('p', warning, 'deviation'));
  $('comparison').append(panel);
  const details = make('details'), absent = make('ul');
  details.append(make('summary', 'Comparison details'));
  details.append(make('p', `Baseline: ${c.baseline} · SHA256 ${c.sha256}`, 'mono'));
  details.append(make('p', 'Red edges labeled “New connection” are outside the trusted-paths baseline. Matching uses exact agent labels (with configured aliases), direction, tool names, and connection type. This is a post-run check of individual connections, not execution order or safety.'));
  details.append(make('h3', `${c.missing.length} trusted connections not observed`));
  for (const link of c.missing) absent.append(make('li', `${link.source} → ${link.target} · ${link.kind}`));
  if (c.missing.length) details.append(absent);
  $('coverage').prepend(details);
}
function choose(id) { selected = id; page = 0; drawAgents(); draw(); }
function drawAgents() {
  const root = $('agents'); root.replaceChildren();
  const all = make('button', 'All agents', 'agent' + (selected === null ? ' active' : '')); all.onclick = () => choose(null); root.append(all);
  const filter = $('agentSearch').value.toLowerCase(), stack = (children.get(null) || []).slice().reverse().map(a => [a, 0]);
  while (stack.length) {
    const [a, depth] = stack.pop();
    for (const child of (children.get(a.id) || []).slice().reverse()) stack.push([child, depth + 1]);
    const label = (a.name + ' ' + a.id).toLowerCase();
    if (filter && !label.includes(filter)) continue;
    const button = make('button', undefined, 'agent' + (selected === a.id ? ' active' : ''));
    button.style.paddingLeft = 10 + Math.min(depth, 8) * 13 + 'px';
    button.append(make('span', a.calls, 'count'), make('span', (depth ? '↳ ' : '') + a.name), make('small', a.id));
    button.onclick = () => choose(a.id); root.append(button);
  }
}
function draw() {
  document.querySelectorAll('.tab').forEach(e => e.classList.toggle('active', e.dataset.tab === tab));
  const view = $('view'); view.replaceChildren();
  if (tab === 'graph') { drawGraph(view); return; }
  const a = byId.get(selected);
  view.append(make('h3', a ? a.name : 'All recorded activity'), make('p', a ? a.id : 'Select an agent to follow its recorded calls.', 'muted'));
  const source = selected === null ? data.events : grouped.get(selected);
  const toolbar = make('div', undefined, 'toolbar'), input = make('input');
  input.placeholder = 'Filter tool, call ID, or agent…'; input.setAttribute('aria-label', 'Filter activity'); input.value = query;
  const status = make('select'); status.setAttribute('aria-label', 'Filter outcome');
  for (const [value, label] of [['all', 'All outcomes'], ['unknown', 'No recorded outcome'], ['error', 'Error'], ['returned', 'Returned'], ['ok', 'OK']]) {
    const option = make('option', label); option.value = value; status.append(option);
  }
  status.value = outcome;
  toolbar.append(input, status); view.append(toolbar); const output = make('div'); view.append(output);
  function paint() {
    output.replaceChildren();
    const q = query.toLowerCase(), events = source.filter(e => (outcome === 'all' || e.status === outcome) && `${e.tool} ${e.call_id || ''} ${byId.get(e.session).name}`.toLowerCase().includes(q));
    const pages = Math.max(1, Math.ceil(events.length / pageSize)); page = Math.min(page, pages - 1);
    const table = make('table'), head = make('thead'), hr = make('tr'), body = make('tbody');
    for (const text of ['# / agent', 'Tool / call ID', 'Outcome', 'Time (UTC)', 'Duration']) hr.append(make('th', text)); head.append(hr); table.append(head);
    for (const e of events.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = make('tr'), tool = make('td'); tool.append(make('div', e.tool, 'mono'), make('small', e.call_id || 'No call ID', 'muted'));
      if (e.child_session_id) tool.append(make('div', 'Handoff target: ' + e.child_session_id, 'mono'));
      for (const [key, value] of Object.entries(e.security || {})) tool.append(make('div', key + ': ' + value, 'mono'));
      if (e.connection_status) tool.append(make('div', e.connection_status === 'deviation' ? 'New connection · deviation' : 'Trusted connection', e.connection_status === 'deviation' ? 'deviation' : 'muted'));
      const date = e.time === null ? null : new Date(e.time * 1000);
      const duration = Number.isFinite(e.duration_seconds) ? (e.duration_seconds < 1 ? Math.round(e.duration_seconds * 1000) + ' ms' : e.duration_seconds.toFixed(2) + ' s') : 'Not recorded';
      row.append(make('td', `${e.id + 1} · ${byId.get(e.session).name}`), tool, make('td', e.status), make('td', date && !isNaN(date) ? date.toISOString() : 'Not recorded', 'mono'), make('td', duration, 'mono')); body.append(row);
    }
    table.append(body); output.append(table);
    if (!events.length) output.append(make('p', 'No calls in this selection.', 'empty'));
    const pager = make('div', undefined, 'pager'), prev = make('button', '← Previous', 'secondary'), next = make('button', 'Next →', 'secondary');
    prev.disabled = page === 0; next.disabled = page >= pages - 1; prev.onclick = () => { page--; paint(); }; next.onclick = () => { page++; paint(); };
    pager.append(prev, make('span', `${events.length} calls · page ${page + 1} / ${pages}`), next); output.append(pager);
  }
  input.oninput = () => { query = input.value; page = 0; paint(); };
  status.onchange = () => { outcome = status.value; page = 0; paint(); }; paint();
}
$('agentSearch').oninput = drawAgents;
document.querySelectorAll('.tab').forEach(e => e.onclick = () => { tab = e.dataset.tab; draw(); });
