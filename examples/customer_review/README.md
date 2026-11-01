# Five-agent customer review

```bash
# Customer-review demo: 5 agents
python3 examples/customer_review/run.py
hermes sessions list
FLOW_DEMO_SESSION='PASTE_ROOT_SESSION_ID_HERE'
hermes flow baseline create --session-id "${FLOW_DEMO_SESSION}" --out "trusted-paths-${FLOW_DEMO_SESSION}.json"
hermes flow --session-id "${FLOW_DEMO_SESSION}" --open
hermes flow --session-id "${FLOW_DEMO_SESSION}" --compare-with-trusted-paths "trusted-paths-${FLOW_DEMO_SESSION}.json" --open
```

A small project for demonstrating **Hermes Flow against real Hermes history**.
Hermes owns the agent loop, tools, delegation, and persistence. Its native
`openai-codex` provider supplies inference to every agent.

```text
coordinator
├── data analyst               SQLite lookup and aggregation
│   └── privacy specialist     SQLite lookup and masking
└── report writer              artifact reads and draft
    └── reviewer               source checks, final report, SHA256 receipts
```

Five agents total, four handoffs, six custom tools, five `SKILL.md` files.
The coordinator runs its two branches sequentially: writing depends on analysis.
Children return to their parents; sibling handoffs use files and subsequent
delegation. No synthetic session events are injected into Hermes history.

## Run

Requires Python 3.10+ and Hermes with native plugins and nested delegation.
The launcher and tools use only the Python standard library.

Authenticate first if the native provider is not already configured:

```bash
hermes auth add openai-codex --type oauth
```

From the Hermes Flow repository root:

```bash
# Seed SQLite and configure the demo without making model calls.
python3 examples/customer_review/run.py --prepare-only

# Run all five agents with your existing Codex login.
python3 examples/customer_review/run.py

# Optional: keep demo history in a separate profile instead.
python3 examples/customer_review/run.py --isolated

# Choose a model available to your account, if needed.
python3 examples/customer_review/run.py --model YOUR_CODEX_MODEL
```

The default `gpt-5.6-luna` matches the development machine's existing Hermes
configuration; availability depends on your account. Override it with `--model`
or `DEMO_CODEX_MODEL`. The launcher requests the native `openai-codex` provider;
active runs retain any fallback behavior configured in that profile.
Model calls consume your account's usage. The whole run has a 900-second timeout;
change it with `--timeout SECONDS`.

Each invocation creates a fresh `runs/<timestamp>-<id>/` directory. Pass
`--directory NEW_PATH` for a specific destination; existing directories are refused.
By default, the launcher asks `hermes config path` for the active profile and
records sessions in that profile's `state.db`. New demo sessions appear in normal
`hermes sessions list`, including when a named profile is active. Existing history
is retained. `run.json` records the database path and, after verification, the root
session ID. Verification checks only that root and its descendants, so unrelated
sessions do not affect the five-agent check.

Active-profile setup copies the six-tool `customer-review-demo` plugin and five
`demo-*` skills into the profile and enables the plugin using Hermes. For the
specific case-insensitive `share/terminfo` copy collision, it uses Hermes config
commands only because this demo plugin has no package dependencies; other sync
errors still stop. Existing identical resources are reused; conflicting files are
refused. It raises
`delegation.max_spawn_depth` to 2 only if currently lower; larger depths and other
settings are preserved. These additions remain available for subsequent runs.
`--prepare-only` stages the fixture and resources locally without installing them,
changing the profile, or making model calls.

With `--isolated`, history lives at `RUN/profiles/demo/state.db` as before. That
mode references your existing Hermes `auth.json` through `RUN/auth.json`; use
`--auth-source PATH` to select another auth store for isolated runs. Active runs
use the active profile's existing authentication. No token values are printed or
copied by the launcher. Generated artifacts and logs remain private and gitignored.

## Inspect after execution

The launcher prints the native root session ID, database path, and an exact report
command. It also prints copy/paste `hermes flow` commands bound to that exact
root session. The launcher finds Hermes on `PATH` and in the standard
`/opt/hermes` install locations, which is useful in the Hermes container. For a
default active-profile run:

```bash
hermes sessions list
hermes flow --session-id ROOT_ID --open
python3 examples/customer_review/run.py --verify RUN
```

Replace `RUN` and `ROOT_ID` with the values printed after execution. From source,
or for an isolated run, use the printed `python3 -m hermes_flow --db PATH
--session-id ROOT_ID ...` command. The standalone module uses this checkout
without installing Flow into the demo profile. For isolated runs, the printed
Hermes commands include `HERMES_HOME=...` so they read the isolated native
session database instead of your active profile.

The graph should show five sessions, nested delegation, repeated artifact tools,
and skill loads. Native calls appear as `returned`: Flow observes evidence and
does not judge safety or task quality. It excludes general tool arguments and
result bodies. It does not infer resource metadata for these custom tools,
visualize SQLite fields, or prove that masking was correct.

Active runs retain the profile's Tool Search settings; verification recognizes
both direct calls and native `tool_call` wrappers. Isolated runs disable Tool
Search deferral in their dedicated demo config.

## Replay the trusted-path comparison demo

Reuse a completed run without launching agents or making model calls:

```bash
# The saved development run (when present):
python3 examples/customer_review/compare_paths.py --run examples/customer_review/runs/live-demo-v3
# Or pass any completed run directory produced by run.py.
```

The command prints a fresh output directory containing `trusted-paths.json`,
`trusted.html` (zero deviations), and `deviation.html` (one red new connection).
It creates an editable demonstration baseline from the root session and its
descendants, then simulates a `demo_unapproved_tool` call in a separate SQLite
copy containing only this root and its descendants, not unrelated profile
history. That call is recorded fixture data only; the tool is not executed. The
original database and baseline stay unchanged. `--directory NEW_PATH`
chooses the output destination. Review/edit candidate connections before using
a real baseline for comparison; reports record its hash for traceability.

To inspect the saved original run directly:

```bash
python3 -m hermes_flow --db examples/customer_review/runs/live-demo-v3/profiles/demo/state.db --session-id 20261028_130621_a25be9 --open
```

## Evidence

- `data/customers.db`: three fictional sensitive-looking customers and invoices.
- `data/artifacts/analysis.json`: aggregate counts and balances.
- `data/artifacts/privacy.json`: masked example and masking limitations.
- `data/artifacts/draft.md`, `review.json`, `report.md`: writer/reviewer artifacts.
- `data/tool-receipts.jsonl`: supplemental tool names, times and outcomes only.
- `run.json` → `database`: native sessions, parent IDs and tool messages in the
  active profile (or `RUN/profiles/demo/state.db` with `--isolated`).
- The selected profile's `cache/delegation/`: delegation transcripts, when recorded.
- `stdout.log`, `stderr.log`, `usage.json`: execution output and model usage.
- `run.json`: model/provider, completion status and verified session IDs.

Completion requires five sessions, the intended two-level tree, all five skill
loads, all six custom tools, four delegations, every artifact, reviewer approval,
and absence of fixture emails/national IDs/account values in the final report.
Model deviations fail verification; their evidence is kept without spawning
replacement agents. Inspect the run logs if this happens.

All records are synthetic. Raw tool results enter model context and native logs,
deliberately illustrating the data path. No email, payment, external delivery,
arbitrary SQL, shell, or network tools are exposed to the demo agents. The masking
tool recognizes only fixture email, national ID and account patterns; it is not
a production privacy control.

`demo_tools.py` implements the tools; `run.py` prepares and invokes Hermes;
`skills/` holds workflow instructions. This uses Hermes's native Codex provider,
not five independent Codex CLI processes.
