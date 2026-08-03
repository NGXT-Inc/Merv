#!/bin/sh
# Install Merv skills and reviewer agents for Kilo (VS Code extension + CLI).
#
# Kilo has no declarative plugin bundle, so this script symlinks the plugin's
# canonical skills into Kilo's global skills directory and the shared
# reviewer-agent wrappers into Kilo's global agent directory. Kilo's agent
# files use the same description/mode/permission frontmatter as OpenCode's,
# so the wrappers in clients/opencode/agents/ are linked unchanged. Symlinks
# keep installs in sync with the plugin source; re-run after pulling plugin
# updates only if files were added or renamed.
set -eu

PLUGIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SKILLS_DIR="$HOME/.kilo/skills"
AGENT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/kilo/agent"

mkdir -p "$SKILLS_DIR" "$AGENT_DIR"

for skill_dir in "$PLUGIN_DIR"/skills/*/; do
  name=$(basename "$skill_dir")
  ln -sfn "$PLUGIN_DIR/skills/$name" "$SKILLS_DIR/$name"
  echo "skill   $name -> $SKILLS_DIR/$name"
done

for agent_file in "$PLUGIN_DIR"/clients/opencode/agents/*.md; do
  name=$(basename "$agent_file")
  ln -sfn "$agent_file" "$AGENT_DIR/$name"
  echo "agent   $name -> $AGENT_DIR/$name"
done

cat <<EOF

Done. Register the MCP server in your research repo's .kilo/kilo.jsonc
(or globally in ${XDG_CONFIG_HOME:-$HOME/.config}/kilo/kilo.jsonc):

{
  "mcp": {
    "merv": {
      "type": "remote",
      "url": "https://experiments.rapidreview.io/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer {env:MERV_MCP_KEY}"
      }
    }
  }
}

Export MERV_MCP_KEY before starting Kilo. For a local deployment, replace
the URL with http://127.0.0.1:8787/mcp and start $PLUGIN_DIR/bin/merv-http first.
EOF
