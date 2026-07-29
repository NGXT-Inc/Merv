# Authentication & project membership

The hosted research suite authenticates against the **same Supabase project as
RapidReview** — same accounts, same `rr_sk_` API keys. Localhost is auth-free:
`build_local_server` passes no verifier, so the local brain never reads
`SUPABASE_*` env, never imports PyJWT, and serves every request as the
implicit local principal exactly as before.

## How it works

One `Authorization: Bearer <credential>` header, three credential shapes,
dispatched by prefix (RapidReview's contract, reimplemented in
`src/merv/brain/surface/auth.py`):

- **Supabase session JWT** — browser sign-in via supabase-js in the UI.
  Verified locally (HS256, `SUPABASE_JWT_SECRET`, audience `authenticated`);
  anonymous sessions are rejected. No Supabase round-trip per request.
- **`rr_sk_` API key** — RapidReview-minted, owner-scoped; everything headless
  (direct `/mcp` clients, agents, curl). sha256-hashed and looked up in
  the shared `api_keys` table over PostgREST (`SUPABASE_SERVICE_KEY`), cached 60s.
  These keys are minted/revoked in RapidReview.
- **`mk_` key** — minted/revoked **in this repo** via the key-mint UI and
  stored in the `project_api_keys` table. Its `grant_scope` is immutable and is
  one of two shapes:
  - `project` — binds one project. The gateway rejects any request whose
    `project_id` argument does not equal that project.
  - `account` — reaches every project its owner is a member of. It carries no
    project confinement, so membership is the only gate; its `project_id`
    column names the *home* project it is listed and revoked under, which is
    never a limit on its reach.

  Either way the key is external, so it can never create projects or touch
  operator diagnostics. OAuth (DCR + PKCE) mints audience-confined `mk_`
  access tokens (+ `mrt_` refresh) for cloud platforms (Codex, Replit); the
  consent screen chooses the scope, and every rotation inherits it.

Enforcement lives in the `attach_principal` middleware
(`src/merv/brain/surface/transport/api/app.py`): OPTIONS, `/health`, `/api/meta`, and
the token-bearing upload routes stay open; the 426 version floor runs before auth so
stale clients get "upgrade", not "login". A verified credential becomes
`Principal(user_id=<supabase sub>)`.

**Project membership** is the authorization layer: `project_members`
(project_id, user_id) in the research store. Authenticated requests see only
member projects — enforced at two funnels: the HTTP path gate
(`/api/projects/{id}/...` → 404 for non-members; `/api/activity` +
`/api/debug/*` additionally require `?project_id=`) and the MCP funnel
(`route_call_tool`, including review tools via their resolved project).
Creating a project records the creator as its first member. Share/assign via:

```
POST   /api/projects/{id}/members   {"user_id": "<supabase auth.users uuid>"}
DELETE /api/projects/{id}/members/{user_id}
GET    /api/projects/{id}/members
```

Any member can manage members (two-trusted-users model; no roles).

## Client setup

- **Web UI**: `/api/meta` advertises `auth: {required, supabase_url,
  supabase_anon_key}`; the AuthGate then shows sign-in (email/password or
  Google). Nothing is baked into the bundle; local backends advertise
  `required: false` and the UI never loads supabase-js.
- **MCP clients** (local Claude Code, cloud Codex, Replit, browser-driven):
  every agent connects directly to the brain's `POST /mcp` endpoint. Sign in
  at [rapidreview.io/merv](https://rapidreview.io/merv), open a project's
  **MCP keys**, mint a key (scope **All my projects** unless you deliberately
  want it confined), and export it as `MERV_MCP_KEY`. The committed `.mcp.json` uses `type: "http"`,
  `url: "https://experiments.rapidreview.io/mcp"`, and
  `headers.Authorization: "Bearer ${MERV_MCP_KEY}"` — the key is read from the
  env var and is **never** inlined into a committed file (it is
  bearer-equivalent to full access to everything it is scoped to, so export it in
  your shell and keep any local key file `.gitignore`d). `merv-client
  configure` writes the machine config and `merv-client env` prints that
  `.mcp.json` snippet; see the
  [hosted client quickstart](HOSTED_CLIENT_QUICKSTART.md) for the full
  walkthrough. The agent passes `project_id` explicitly on every call: an
  account-scoped key discovers the ids with `project(action="list")`, and a
  project-scoped key may only pass its one bound project. Project membership
  controls authorization in both cases.
## Hosted configuration

Set `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_KEY`,
`SUPABASE_ANON_KEY`, and `MERV_REQUIRE_AUTH=1`. Existing databases must contain
one `project_members` row for each authorized user/project pair. Users then
sign in through the UI or mint a scoped key and export it as `MERV_MCP_KEY`.

Keep Supabase secrets and service credentials in managed secret storage. Rotate
them through the Supabase and deployment runbooks, not through application
code.

## Notes

- SSE under auth: EventSource cannot send the header; the hosted stream 401s
  and the UI's ETag-polling fallback carries updates (~3s latency). Stream
  tickets are a known follow-up if realtime matters.
- `/api/admin/*` is an operator surface and should remain network-restricted
  even when authentication is enabled.
- Same accounts ≠ SSO: users sign in once per product origin.
