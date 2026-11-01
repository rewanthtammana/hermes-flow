# Live interaction demo

This demo starts four real Hermes agents and needs an authenticated
`openai-codex` provider. For a quick no-model preview of Hermes Flow instead,
run `python3 -m hermes_flow demo --open` from the repository root.

If Hermes package sync hits the case-insensitive `share/terminfo` copy error,
the runner falls back to Hermes config commands for its dependency-free capture
plugin only. Other sync errors still stop the run.

To use this checkout as a Hermes plugin during development, symlink it into the
active profile (run from the repository root):

```bash
PLUGIN_DIR="${HERMES_HOME:-$HOME/.hermes}/plugins"
mkdir -p "$PLUGIN_DIR"
ln -s "$PWD" "$PLUGIN_DIR/hermes-flow"
hermes plugins enable hermes-flow
```

Do not overwrite an existing `hermes-flow` entry. Remove only the symlink with
`rm "$PLUGIN_DIR/hermes-flow"` when finished.

Run the four-agent demo and inspect its captured session:

```bash
python3 examples/live_interactions/run.py
hermes sessions list
FLOW_SMALL_SESSION='PASTE_ROOT_SESSION_ID_HERE'
hermes flow baseline create --session-id "${FLOW_SMALL_SESSION}" --out "trusted-paths-${FLOW_SMALL_SESSION}.json"
hermes flow --session-id "${FLOW_SMALL_SESSION}" --open
hermes flow --session-id "${FLOW_SMALL_SESSION}" --compare-with-trusted-paths "trusted-paths-${FLOW_SMALL_SESSION}.json" --open
```
