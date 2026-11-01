#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
profile_dir="${HERMES_HOME:-${HOME}/.hermes}"
plugin_dir="${profile_dir}/plugins"
link_path="${plugin_dir}/hermes-flow"

if command -v hermes >/dev/null 2>&1; then
    hermes_cmd="$(command -v hermes)"
else
    hermes_cmd=""
    for candidate in /opt/hermes/bin/hermes /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes; do
        if [[ -x "$candidate" ]]; then
            hermes_cmd="$candidate"
            break
        fi
    done
fi

if [[ -z "$hermes_cmd" ]]; then
    echo "Hermes was not found. Install Hermes and authenticate first." >&2
    exit 1
fi

mkdir -p "$plugin_dir"
if [[ -e "$link_path" || -L "$link_path" ]]; then
    link_target="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$link_path" 2>/dev/null || true)"
    if [[ -L "$link_path" && "$link_target" == "$repo_dir" ]]; then
        echo "Existing hermes-flow link is already correct: $link_path"
    else
        echo "Refusing to replace existing path: $link_path" >&2
        exit 1
    fi
else
    ln -s -- "$repo_dir" "$link_path"
    echo "Linked $repo_dir -> $link_path"
fi

"$hermes_cmd" plugins validate "$repo_dir"
if enable_output=$("$hermes_cmd" plugins enable hermes-flow 2>&1); then
    printf '%s\n' "$enable_output"
else
    printf '%s\n' "$enable_output" >&2
    if ! grep -q 'Errno 17' <<<"$enable_output" || ! grep -q 'share/terminfo' <<<"$enable_output"; then
        exit 1
    fi
    enabled_json=$("$hermes_cmd" config get plugins.enabled --json 2>/dev/null || printf '[]')
    enabled_json=$(python3 -c 'import json,sys; v=json.loads(sys.argv[1]); v.append("hermes-flow") if "hermes-flow" not in v else None; print(json.dumps(v))' "$enabled_json")
    "$hermes_cmd" config set plugins.enabled "$enabled_json"
    echo "Enabled hermes-flow through native config after the dependency-free terminfo collision."
fi

"$hermes_cmd" flow demo --open
