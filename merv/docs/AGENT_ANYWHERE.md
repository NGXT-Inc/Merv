# Run Merv from any agent

Every platform connects directly to
`https://experiments.rapidreview.io/mcp`. Authentication is either a
project-scoped bearer key or Merv's OAuth 2.1 browser flow. In every case, call
`project(action="current")` once and pass the returned `project_id` explicitly
on every project-scoped tool.

| Platform and surfaces | Connection | Authentication | Auto-discovered context | Known constraints |
|---|---|---|---|---|
| Claude Code — local | Install the Claude plugin bundle; its `.mcp.json` registers the HTTP MCP endpoint. | `MERV_MCP_KEY` environment key. | `skills/` and `agents/`; reviewer agents use the `merv:` namespace. | The documented background-watcher recipe is optional and local-session specific. |
| claude.ai — web | Add a custom remote connector pointed at the `/mcp` URL. | OAuth browser flow (automatic client registration). | None — the local plugin's skills/agents are not installed on web (unconfirmed otherwise). | Connectors are deduplicated by exact URL; distinct URLs are distinct connectors with independent grants. |
| Codex — CLI, ChatGPT web, and cloud tasks | Install the Codex plugin bundle; `.mcp.codex.json` registers the same endpoint. | CLI uses `MERV_MCP_KEY`; ChatGPT web can use OAuth; headless cloud tasks must expose the key through `bearer_token_env_var`. | `skills/` through the plugin manifest; review skills spawn the reviewer. | A headless task cannot complete an interactive OAuth browser flow. |
| Cursor — local | Install the Cursor plugin bundle; `mcp.json` registers the endpoint. | `MERV_MCP_KEY` environment key. | `skills/` and `agents/` from the plugin bundle. | Cursor's approximately 40-tool combined MCP ceiling leaves little room beside Merv's catalog (38 public tools with optional storage enabled; 34 without); disable unused servers or tools if entries disappear. |
| Cursor — background agents | Account-level MCP configuration at cursor.com/agents. | Static bearer-key header recommended (Cursor's cloud OAuth is a known rough edge). | Skill/agent discovery on background agents is unconfirmed. | Same combined tool ceiling applies across the account's MCP servers. |
| Replit Agent | **MCP Servers → + Add MCP server**, enter the URL, then **Test & save**. | OAuth is primary. A pasted bearer header is an explicitly unconfirmed fallback. | No Merv skills or reviewer agents are installed by the account connection. | Connections are account-scoped, templates and `.replit` cannot pre-wire them, and all MCP traffic passes Replit's security scanner. |
| OpenHands — local and cloud | Local GUI/config file: `config.toml` `shttp_servers`. Local CLI: `openhands mcp add merv --transport http --header "Authorization: Bearer <project-key>" <url>` (stores `~/.openhands/mcp.json`). Cloud: **Settings → MCP** only. | Prefer a pasted project key (`api_key` in TOML, `--header` on the CLI); attended local sessions may use OAuth (`--auth oauth`). | Root `AGENTS.md` is always on; `.agents/skills/<name>/SKILL.md` directories load as on-demand AgentSkills (keyword activation requires explicit `triggers` frontmatter). | The connection cannot ship in-repo, `api_key` env interpolation is unconfirmed, and reviewer subagents are not auto-discovered. |

OpenHands and Replit require a second session or agent for reviewer separation,
following the matching review skill and the fresh handoff from `review.request`;
when that is unavailable, perform the handoff inline and keep the reviewer
read-only.

See [CLIENTS.md](CLIENTS.md) for the bundled adapters and reviewer workflow,
[OpenHands setup](../clients/openhands/README.md), and
[Replit Agent setup](../clients/replit/README.md).
