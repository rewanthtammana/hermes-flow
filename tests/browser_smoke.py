"""Optional browser checks; requires Playwright and Chromium."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from hermes_flow.cli import observe, render
from hermes_flow.prototype import execute
from hermes_flow.source import read_file
from hermes_flow import baseline


def main():
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        directory = execute(Path(tmp) / "run")
        html = directory / "flow.html"
        html.write_text(render(observe(read_file(directory / "events.jsonl"))))
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors, requests = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        page.goto(html.as_uri())
        assert page.locator('.tab').all_text_contents() == ['Graph', 'Activity']
        assert page.locator('[data-node-type="agent"]').count() == 5
        assert page.locator('path[data-kind="handoff"]').count() == 4
        assert page.locator('path[data-source="orchestrator"][data-kind="handoff"]').count() == 2
        assert page.locator('[data-node-type="tool"]').count() == 12
        payload = json.loads(page.locator('#flow-data').text_content())
        assert not {"status", "ideal", "findings"} & payload.keys()
        page.get_by_role('button', name='Hide tools', exact=True).click()
        page.get_by_role('button', name='Fit width', exact=True).click()
        page.screenshot(path='/tmp/hermes-flow-browser-smoke.png', full_page=True)
        page.locator('[data-node-id="orchestrator-researcher"]').click()
        assert page.locator('[data-node-type="agent"]').count() == 3
        page.get_by_role('button', name='Show tools', exact=True).click()
        assert page.locator('[data-node-type="tool"]').count() == 8
        assert page.locator('path[data-kind="uses"]').count() == 8
        page.get_by_role('button', name='Activity', exact=True).click()
        assert page.locator('tbody tr').count() == 3
        page.get_by_label('Filter activity').fill('delegate_task')
        assert page.locator('tbody tr').count() == 1
        page.locator('#agents').get_by_role('button', name='All agents', exact=True).click()
        page.get_by_label('Filter activity').fill('')
        assert page.locator('tbody tr').count() == 13
        page.get_by_role('button', name='Graph', exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Explicit requests/results include a sibling route, aggregated without message bodies.
        rows = read_file(Path(__file__).resolve().parents[1] / 'examples/agent_interactions.jsonl')
        rows.append({**rows[3], 'call_id': 'b-d-again'})
        html.write_text(render(observe(rows)))
        page.goto(html.as_uri())
        assert page.locator('path[data-kind="request"]').count() == 2
        assert page.locator('path[data-kind="result"]').count() == 2
        assert page.locator('path[data-source="b"][data-target="d"][data-kind="result"]').count() == 1
        assert 'result x2' in page.locator('svg').text_content()
        page.set_viewport_size({"width": 1440, "height": 1050})
        page.get_by_role('button', name='Hide tools', exact=True).click()
        page.get_by_role('button', name='Fit width', exact=True).click()
        page.screenshot(path='/tmp/hermes-flow-agent-interactions.png', full_page=True)
        page.locator('[data-node-id="b"]').click()
        assert 'Writer D' in page.locator('table').text_content()  # outside subtree, still navigable
        page.get_by_role('button', name='Hide interactions', exact=True).click()
        assert page.locator('path[data-kind="result"]').count() == 0
        page.get_by_role('button', name='Show interactions', exact=True).click()
        native = [{"id": "p", "ended_at": 10, "messages": []},
                  {"id": "c", "parent_session_id": "p", "ended_at": 5, "messages": []}]
        html.write_text(render(observe(native)))
        page.goto(html.as_uri())
        assert page.locator('path[data-kind="completion"]').count() == 1
        assert page.locator('path[data-kind="result"]').count() == 0
        assert 'result delivery unverified' in page.locator('table').text_content()
        # Lifecycle metadata yields actual native request/result pairs, including
        # a result edge that can be independently removed from trusted paths.
        native = []
        for sid, parent, calls in [('a', None, [('ab', 'b')]), ('b', 'a', [('bc', 'c'), ('bd', 'd')]),
                                   ('c', 'b', []), ('d', 'b', [])]:
            parent_call = {'b': 'ab', 'c': 'bc', 'd': 'bd'}.get(sid)
            config = {'_hermes_flow_handoff': {'schema': 1, 'parent_session_id': parent, 'parent_call_id': parent_call}} if parent else {}
            messages = []
            for index, (cid, child) in enumerate(calls):
                messages.extend([{'role': 'assistant', 'timestamp': index * 2 + 1, 'tool_calls': [
                    {'id': cid, 'function': {'name': 'delegate_task'}}]},
                    {'role': 'tool', 'tool_call_id': cid, 'timestamp': index * 2 + 2}])
            native.append({'id': sid, 'title': sid.upper(), 'parent_session_id': parent,
                           'ended_at': 10, 'model_config': config, 'messages': messages})
        flow = observe(native)
        html.write_text(render(flow))
        page.goto(html.as_uri())
        assert page.locator('path[data-kind="handoff"]').count() == 3
        assert page.locator('path[data-kind="request"]').count() == 3
        assert page.locator('path[data-kind="result"]').count() == 3
        trusted_native = baseline.create(flow)
        c = next(g for g in trusted_native['connections'] if g['source'] == 'agent:C')
        c['target']['agents'] = []
        html.write_text(render(baseline.compare(observe(native), trusted_native, 'native.json')))
        page.goto(html.as_uri())
        red = page.locator('path[data-status="deviation"]')
        assert red.count() == 1
        assert red.get_attribute('data-source') == 'c'
        assert red.get_attribute('data-target') == 'b'
        assert red.get_attribute('data-kind') == 'result'
        # Compare all graph edge types; the editable baseline remains unchanged.
        rows = read_file(Path(__file__).resolve().parents[1] / 'examples/agent_interactions.jsonl')
        trusted_path = directory / 'trusted.json'
        baseline.write(trusted_path, baseline.create(observe(rows)))
        trusted = baseline.read(trusted_path)
        before = trusted_path.read_bytes()
        html.write_text(render(baseline.compare(observe(rows), trusted, trusted_path)))
        page.goto(html.as_uri())
        assert page.locator('path[data-status="deviation"]').count() == 0
        assert '0 new connections' in page.locator('#comparison').text_content()
        rows.append({'session_id': 'new', 'agent_role': 'new agent', 'parent_session_id': 'a',
                     'tool': 'new_tool', 'interaction': {'target_session_id': 'd', 'kind': 'message'}})
        html.write_text(render(baseline.compare(observe(rows), trusted, trusted_path)))
        page.goto(html.as_uri())
        assert '3 new connections' in page.locator('#comparison').text_content()
        assert page.locator('#comparison').bounding_box()['y'] < page.locator('.workspace').bounding_box()['y']
        assert page.locator('#coverage').bounding_box()['y'] > page.locator('.workspace').bounding_box()['y']
        assert page.locator('#coverage details').evaluate_all('(items) => items.every(item => !item.open)')
        assert 'Baseline:' not in page.locator('#comparison').text_content()
        assert 'New connections' not in page.locator('#coverage').inner_text()
        red = page.locator('path[data-status="deviation"]')
        assert red.count() == 3
        assert sorted(red.evaluate_all('(edges) => edges.map(e => e.dataset.kind)')) == ['lineage', 'message', 'uses']
        assert all(c == '#c62828' for c in red.evaluate_all('(edges) => edges.map(e => e.getAttribute("stroke"))'))
        assert all('deviation-arrow' in c for c in red.evaluate_all('(edges) => edges.map(e => e.getAttribute("marker-end"))'))
        page.screenshot(path='/tmp/hermes-flow-trusted-paths.png', full_page=True)
        page.get_by_role('button', name='Activity', exact=True).click()
        assert 'New connection · deviation' in page.locator('tbody').text_content()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert trusted_path.read_bytes() == before
        # Outcome filters combine with search, and durations require real result times.
        rows = [{"id": "native", "title": "shared", "ended_at": 4, "messages": [
                    {"role": "assistant", "timestamp": 1, "tool_calls": [
                        {"id": "returned", "function": {"name": "read_file"}},
                        {"id": "missing", "function": {"name": "write_file"}}]},
                    {"role": "tool", "tool_call_id": "returned", "timestamp": 1.25}]},
                {"session_id": "adapter", "agent_role": "shared", "tool": "failed_tool", "status": "error"},
                {"session_id": "adapter", "agent_role": "shared", "tool": "good_tool", "status": "ok"}]
        flow = observe(rows)
        html.write_text(render(baseline.compare(flow, baseline.create(flow), 'shared.json')))
        page.goto(html.as_uri())
        assert 'shared by 2 sessions' in page.locator('#comparison').text_content()
        page.get_by_role('button', name='Activity', exact=True).click()
        assert page.locator('tbody tr').count() == 4
        assert '250 ms' in page.locator('tbody').text_content()
        for status, tool in [('unknown', 'write_file'), ('error', 'failed_tool'),
                             ('returned', 'read_file'), ('ok', 'good_tool')]:
            page.get_by_label('Filter outcome').select_option(status)
            assert page.locator('tbody tr').count() == 1
            assert tool in page.locator('tbody').text_content()
        page.get_by_label('Filter activity').fill('read_file')
        assert page.locator('tbody tr').count() == 0
        page.get_by_label('Filter outcome').select_option('all')
        assert page.locator('tbody tr').count() == 1
        page.get_by_label('Filter activity').fill('')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Scale and untrusted labels remain observable without external requests.
        rows = [{"session_id": f"a{a}", "agent_role": 'agent</script><img src=x>', "tool": f"tool-{i}",
                 "timestamp": i, "status": "error"} for a in range(500) for i in range(20)]
        html.write_text(render(observe(rows)))
        page.goto(html.as_uri())
        assert page.locator('[data-node-type="agent"]').count() == 50
        page.get_by_role('button', name='Next →', exact=True).click()
        assert 'Roots 51–100 / 500' in page.locator('.pager').inner_text()
        page.get_by_role('button', name='Activity', exact=True).click()
        assert page.locator('tbody tr').count() == 100
        page.get_by_label('Filter activity').fill('tool-19')
        assert '500 calls' in page.locator('.pager').inner_text()
        assert page.locator('img').count() == 0
        assert not errors, errors
        assert not [r for r in requests if not r.startswith('file:')], requests
        browser.close()
        print('Browser checks passed: directed requests/results, aggregation, completion evidence, connected graph, subtree, outcome/text filters, duration, identity warnings, mobile, scale, escaping, no network.')


if __name__ == '__main__':
    main()
