BEGIN TRANSACTION;
CREATE TABLE artifact_figures (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL,
  link_path TEXT NOT NULL,
  content_sha256 TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  upload_token TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  FOREIGN KEY(artifact_id) REFERENCES artifacts(id)
);
INSERT INTO "artifact_figures" VALUES('fig_contract_v40','art_contract_v40','figures/curve.png','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',7,'complete','',NULL);
CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  role TEXT NOT NULL,
  attempt_index INTEGER NOT NULL DEFAULT 0,
  lens_id TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  content_sha256 TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0,
  content_type TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  upload_token TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  created_by TEXT NOT NULL DEFAULT 'agent',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  created_seq INTEGER NOT NULL DEFAULT 0,
  submission_id TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
INSERT INTO "artifacts" VALUES('art_contract_v40','proj_contract_v40','experiment','exp_contract_v40','plan',1,'','plan.md','Released plan','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',17,'text/markdown; charset=utf-8','complete','',NULL,'agent','2026-07-28T00:00:00+00:00','2026-07-28T00:00:00+00:00',1,'sub_contract_v40');
INSERT INTO "artifacts" VALUES('art_pending_v40','proj_contract_v40','experiment','exp_contract_v40','report',1,'','report.md','Pending report','',0,'','pending','release-v40-token','2099-01-01T00:00:00+00:00','agent','2026-07-28T00:00:00+00:00','2026-07-28T00:00:00+00:00',2,'');
CREATE TABLE claims (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  statement TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  confidence TEXT NOT NULL DEFAULT 'medium',
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  type TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
INSERT INTO "events" VALUES(100,'proj_contract_v40','artifact.submitted','experiment','exp_contract_v40','{"artifact_id":"art_contract_v40","attempt_index":1,"path":"plan.md","role":"plan"}','2026-07-28T00:00:00+00:00');
CREATE TABLE experiment_claims (
  experiment_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  PRIMARY KEY(experiment_id, claim_id)
);
CREATE TABLE experiments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  intent TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_index INTEGER NOT NULL DEFAULT 1,
  revision_context TEXT NOT NULL DEFAULT '',
  conclusion TEXT NOT NULL DEFAULT '',
  mlflow_run_id TEXT NOT NULL DEFAULT '',
  mlflow_run_name TEXT NOT NULL DEFAULT '',
  mlflow_run_status TEXT NOT NULL DEFAULT '',
  mlflow_run_artifact_uri TEXT NOT NULL DEFAULT '',
  mlflow_run_created_at TEXT,
  mlflow_run_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
INSERT INTO "experiments" VALUES('exp_contract_v40','proj_contract_v40','Contract experiment','Preserve released artifact data','planned',1,'','','','','','',NULL,'','2026-07-28T00:00:00+00:00','2026-07-28T00:00:00+00:00');
CREATE TABLE feed_upload_tokens (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  post_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  media_kind TEXT NOT NULL,
  media_path TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  in_reply_to TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE litreview_sections (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('summary','section')),
  title TEXT NOT NULL,
  tldr TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL DEFAULT '',
  created_seq INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, kind, title),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE oauth_authorization_codes (
  code_digest TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  -- Carries the consent decision through to the minted key (see
  -- project_api_keys.grant_scope).
  grant_scope TEXT NOT NULL DEFAULT 'project'
    CHECK (grant_scope IN ('project', 'account')),
  code_challenge TEXT NOT NULL,
  resource TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT NOT NULL,
  redirect_uris_json TEXT NOT NULL,
  grant_types_json TEXT NOT NULL,
  metadata_fingerprint TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE oauth_refresh_tokens (
  id TEXT PRIMARY KEY,
  family_id TEXT NOT NULL,
  secret_digest TEXT NOT NULL UNIQUE,
  client_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  -- Preserved across every rotation so a refreshed key keeps the scope the
  -- user consented to (see project_api_keys.grant_scope).
  grant_scope TEXT NOT NULL DEFAULT 'project'
    CHECK (grant_scope IN ('project', 'account')),
  resource TEXT NOT NULL,
  current_key_id TEXT NOT NULL,
  parent_token_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  revoked_at TEXT,
  FOREIGN KEY(client_id) REFERENCES oauth_clients(client_id),
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(current_key_id) REFERENCES project_api_keys(id),
  FOREIGN KEY(parent_token_id) REFERENCES oauth_refresh_tokens(id)
);
CREATE TABLE paper_links (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  paper_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('litreview_section','experiment','claim')),
  target_id TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(project_id, paper_id, target_type, target_id),
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(paper_id) REFERENCES papers(id)
);
CREATE TABLE papers (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  norm_key TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  authors_json TEXT NOT NULL DEFAULT '[]',
  year TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL CHECK (source_kind IN ('arxiv','doi','url')),
  fetch_status TEXT NOT NULL CHECK (fetch_status IN ('fetched','manual','failed')),
  created_by TEXT NOT NULL DEFAULT '',
  created_seq INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, norm_key),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE project_api_keys (
  id TEXT PRIMARY KEY,
  secret_digest TEXT NOT NULL UNIQUE,
  owner_user_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  -- 'project' confines the credential to project_id. 'account' authorizes
  -- every project the owner is a member of, and project_id is then only the
  -- home project the key is administered from (listed and revoked under the
  -- existing /api/projects/{id}/keys routes), never a limit on its reach.
  grant_scope TEXT NOT NULL DEFAULT 'project'
    CHECK (grant_scope IN ('project', 'account')),
  -- OAuth access keys bind this to their full RFC 8707 resource URI. Direct
  -- project keys keep NULL and retain their existing REST + MCP authority.
  audience TEXT,
  -- Stable grant identity for OAuth access-key rotations. Direct project keys
  -- keep NULL and use their immutable key id for idempotency instead.
  oauth_family_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  revoked_at TEXT,
  parent_key_id TEXT,
  sandbox_seconds_ceiling BIGINT CHECK (sandbox_seconds_ceiling IS NULL OR sandbox_seconds_ceiling >= 0),
  blob_bytes_ceiling BIGINT CHECK (blob_bytes_ceiling IS NULL OR blob_bytes_ceiling >= 0),
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(parent_key_id) REFERENCES project_api_keys(id)
);
CREATE TABLE project_members (
  -- Access layer for authenticated (hosted) mode: user_id is a Supabase
  -- auth.users UUID; a row grants full member access to the project. The
  -- local surface carries no user_id, so membership never filters it.
  project_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (project_id, user_id),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  -- Per-project policy knobs (e.g. require_verified_reviews), JSON dict.
  settings_json TEXT NOT NULL DEFAULT '{}',
  -- Tenancy (cloud plan Phase 6): ownership lives on the project row; every
  -- other table reaches its tenant through project_id. The current private
  -- deployment uses the fixed 'local' tenant until real user auth lands.
  tenant_id TEXT NOT NULL DEFAULT 'local',
  created_at TEXT NOT NULL
);
INSERT INTO "projects" VALUES('proj_contract_v40','Release v40 contract','Step-0 compatibility fixture','active','{}','local','2026-07-28T00:00:00+00:00');
CREATE TABLE reflection_claim_changes (
  reflection_id TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  op TEXT NOT NULL,
  claim_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY(reflection_id, claim_id),
  FOREIGN KEY(reflection_id) REFERENCES reflections(id),
  FOREIGN KEY(claim_id) REFERENCES claims(id)
);
CREATE TABLE reflection_experiments (
  reflection_id TEXT NOT NULL,
  experiment_id TEXT NOT NULL,
  proposal_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY(reflection_id, experiment_id),
  FOREIGN KEY(reflection_id) REFERENCES reflections(id),
  FOREIGN KEY(experiment_id) REFERENCES experiments(id)
);
CREATE TABLE reflections (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  attempt_index INTEGER NOT NULL DEFAULT 1,
  revision_context TEXT NOT NULL DEFAULT '',
  -- The declared reflection roster: 5 lenses (3 core + 2 wave-authored), each
  -- {id, title, charter, core, why_distinct}. JSON list, fixed at create.
  roster_json TEXT NOT NULL DEFAULT '[]',
  -- The corpus snapshot taken at create: terminal experiments (id + attempt +
  -- status) and claim statuses at that moment. The reflection review judges the
  -- story against this fixed corpus, and staleness is computed against it.
  corpus_json TEXT NOT NULL DEFAULT '{}',
  published_at TEXT,
  -- Version id of the project logic graph association at publish time, so the
  -- single living graph file still yields an immutable per-wave history.
  published_graph_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  -- Insertion-order column replacing rowid ordering (cloud plan Phase 6).
  created_seq INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE review_requests (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  role TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  -- Capability hardening (cloud plan Phase 7): the reviewer capability is
  -- stored HASHED (sha256 of the minted token), never in plaintext. The
  -- plaintext is returned once to the caller at request time; review.start
  -- resolves the request by hashing the presented token and comparing with a
  -- constant-time check. Replaces the pre-Phase-7 plaintext `capability`
  -- column (legacy DBs converge in _ensure_forward_schema).
  capability_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  target_snapshot_id TEXT NOT NULL,
  producer_session_id TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  -- Insertion-order column replacing rowid ordering (cloud plan Phase 6).
  created_seq INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
INSERT INTO "review_requests" VALUES('rr_contract_v40','proj_contract_v40','experiment','exp_contract_v40','experiment_design','Fixture review','cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc','pending','exp_contract_v40:1:art_contract_v40','producer-contract','2099-01-01T00:00:00+00:00','2026-07-28T00:00:00+00:00',1);
CREATE TABLE review_sessions (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  declared_agent TEXT NOT NULL DEFAULT '',
  caller_session_id TEXT NOT NULL DEFAULT '',
  -- Principal binding (cloud plan Phase 7): the authenticated tenant that
  -- started the session, so cross-tenant review hijacking is rejected at
  -- start. Local mode (single tenant, auth off) writes the 'local' tenant —
  -- a no-op. Empty on legacy rows that predate the column.
  tenant_id TEXT NOT NULL DEFAULT '',
  independence TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(request_id) REFERENCES review_requests(id)
);
CREATE TABLE reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  target_snapshot_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  role TEXT NOT NULL,
  verdict TEXT NOT NULL,
  return_to TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  -- Researcher-facing TLDR (July 2026): 1-3 plain sentences, the first thing
  -- the human reads on the experiment page. Required on new submissions;
  -- empty on rows that predate the column (legacy DBs converge below).
  synopsis TEXT NOT NULL DEFAULT '',
  findings_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  -- Insertion-order column replacing rowid ordering (cloud plan Phase 6).
  created_seq INTEGER NOT NULL DEFAULT 0,
  -- The sealed submission this verdict graded ('' on rows predating the
  -- column, and on reviews of a target that never sealed one). It is what
  -- lets the figure draw round 2 of a report review as a step after round 1
  -- instead of a sibling hanging off the attempt.
  submission_id TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(request_id) REFERENCES review_requests(id),
  FOREIGN KEY(session_id) REFERENCES review_sessions(id)
);
CREATE TABLE sandbox_attachments (
  sandbox_uid TEXT NOT NULL,
  experiment_id TEXT NOT NULL,
  attached_at TEXT NOT NULL,
  detached_at TEXT,
  FOREIGN KEY(sandbox_uid) REFERENCES sandboxes(sandbox_uid)
);
CREATE TABLE sandbox_generations (
  id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT 'local',
  sandbox_id TEXT NOT NULL DEFAULT '',
  -- Owning compute provider (empty = pre-multi-provider row / default backend).
  provider TEXT NOT NULL DEFAULT '',
  instance_type TEXT NOT NULL DEFAULT '',
  gpu TEXT NOT NULL DEFAULT '',
  price_usd_per_hour REAL NOT NULL DEFAULT 0,
  -- Provisioning credential attribution (agent-anywhere spend). NULL for every
  -- JWT/rr_sk_/local write; set to the project_api_keys.id that provisioned it.
  key_id TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  created_seq INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sandbox_runs (
  sandbox_uid TEXT NOT NULL,
  label TEXT NOT NULL,
  command TEXT NOT NULL DEFAULT '',
  pid INTEGER,
  exit_code INTEGER,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_event_emitted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sandbox_uid, label),
  FOREIGN KEY(sandbox_uid) REFERENCES sandboxes(sandbox_uid)
);
CREATE TABLE sandboxes (
  sandbox_uid TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT 'local',
  sandbox_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'none',
  gpu TEXT NOT NULL DEFAULT '',
  cpu REAL NOT NULL DEFAULT 0,
  memory INTEGER NOT NULL DEFAULT 0,
  -- Compute provider that owns this sandbox (the backend's capabilities.name).
  -- Empty on rows that predate multi-provider support and means "the
  -- configured default backend" at read time.
  provider TEXT NOT NULL DEFAULT '',
  -- Provider-bundled machine SKU + datacenter, for backends (Lambda Labs) that
  -- procure a fixed instance type rather than composing cpu/memory. Empty for
  -- Modal, which sets gpu/cpu/memory above instead.
  instance_type TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT '',
  -- Provider price quote at provision (cloud plan Phase 7): captured from the
  -- catalog option (Lambda has it; Modal leaves 0). Recorded on the row AND
  -- appended to sandbox_generations so per-generation spend is reconstructable
  -- even though the row itself only retains its current generation.
  price_usd_per_hour REAL NOT NULL DEFAULT 0,
  time_limit INTEGER NOT NULL DEFAULT 0,
  ssh_host TEXT NOT NULL DEFAULT '',
  ssh_port INTEGER NOT NULL DEFAULT 0,
  ssh_user TEXT NOT NULL DEFAULT 'root',
  workdir TEXT NOT NULL DEFAULT '',
  sync_dir TEXT NOT NULL DEFAULT '',
  unsynced_dir TEXT NOT NULL DEFAULT '',
  sandbox_data_dir TEXT NOT NULL DEFAULT '',
  -- Management keypair reference (cloud plan Phase 5, fixed decision 4):
  -- non-empty when a control-plane management key was minted for this
  -- sandbox. A key-store reference (the sandbox_uid) — never key material.
  mgmt_key_ref TEXT NOT NULL DEFAULT '',
  -- User SSH key custody source: caller supplied an OpenSSH public key, or the
  -- local data plane used the managed fallback keypair.
  public_key_source TEXT NOT NULL DEFAULT 'managed',
  volume_name TEXT NOT NULL DEFAULT '',
  sandbox_name TEXT NOT NULL DEFAULT '',
  phase TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  provision_started_at TEXT,
  requested_at TEXT,
  expires_at TEXT,
  last_seen_at TEXT,
  idle_since TEXT,
  heartbeat_snapshot_json TEXT NOT NULL DEFAULT '{}',
  last_command_id TEXT NOT NULL DEFAULT '',
  last_command_text TEXT NOT NULL DEFAULT '',
  last_command_started_at TEXT,
  last_command_status TEXT NOT NULL DEFAULT '',
  last_command_exit_code INTEGER,
  last_command_finished_at TEXT,
  last_command_output_tail TEXT NOT NULL DEFAULT '',
  last_command_snapshot_at TEXT,
  -- Set when a receipt read SUCCEEDED while the row was still active, on the
  -- way to terminal. It is what separates "we looked and the run was not
  -- there" (lost) from "we never got to look" (unknown): reconcile_row
  -- reports a dead channel, a timeout and genuine no-news identically, so
  -- without this stamp every unfinished run on a dead box reads as lost.
  runs_final_observed_at TEXT,
  terminated_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  -- Insertion-order column replacing rowid ordering (cloud plan Phase 6).
  created_seq INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
INSERT INTO "schema_migrations" VALUES(1,'drop_legacy_jobs_table','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(2,'add_sandbox_tenant_id','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(3,'add_sandbox_heartbeat_columns','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(4,'migrate_sandbox_uid_identity','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(5,'drop_sandboxes_experiment_unique','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(6,'backfill_sandbox_mgmt_key_refs','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(7,'allow_sandbox_attachment_history','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(8,'drop_sandboxes_experiment_id','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(9,'drop_metrics_snapshots','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(10,'normalize_storage_missing_status','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(11,'add_project_settings_json','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(12,'add_experiment_mlflow_run_columns','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(13,'add_review_synopsis','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(14,'add_sandbox_public_key_source','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(15,'rename_syntheses_to_reflections','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(16,'add_sandbox_last_command_columns','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(17,'reactivate_hard_stopped_projects','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(18,'add_sandbox_provider_columns','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(19,'unify_synthesis_to_reflection','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(20,'add_litreview_sections','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(21,'add_litreview_papers','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(22,'add_litreview_paper_links','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(23,'add_litreview_summary_unique_index','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(24,'add_artifacts_tables','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(25,'drop_resource_tables','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(26,'add_project_api_keys','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(27,'add_sandbox_generation_key_id','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(28,'add_oauth_clients','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(29,'add_oauth_authorization_codes','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(30,'add_oauth_refresh_tokens','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(31,'add_user_hf_tokens','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(32,'add_feed_upload_tokens','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(33,'add_storage_completion_tokens','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(34,'add_grant_scope','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(35,'add_runs_final_observed_at','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(36,'add_submission_attempts','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(37,'add_tool_call_ledger','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(38,'add_oauth_client_fingerprint','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(39,'add_events_target_index','2026-07-28T22:53:41Z');
INSERT INTO "schema_migrations" VALUES(40,'add_tracking_deliveries','2026-07-28T22:53:41Z');
CREATE TABLE spend_kill_switches (
  scope TEXT PRIMARY KEY,
  tripped INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  tripped_at TEXT
);
CREATE TABLE storage_completion_tokens (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  upload_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id),
  FOREIGN KEY(object_id) REFERENCES storage_objects(id)
);
CREATE TABLE storage_objects (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  kind TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  namespace TEXT NOT NULL,
  status TEXT NOT NULL,
  upload_id TEXT,
  expires_at TEXT,
  created_by TEXT NOT NULL DEFAULT 'codex',
  producing_experiment_id TEXT NOT NULL DEFAULT '',
  producing_run TEXT NOT NULL DEFAULT '',
  source_uri TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_accessed_at TEXT,
  created_seq INTEGER NOT NULL DEFAULT 0,
  UNIQUE(project_id, name, version),
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE submissions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  attempt_index INTEGER NOT NULL DEFAULT 0,
  transition TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  created_seq INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
INSERT INTO "submissions" VALUES('sub_contract_v40','proj_contract_v40','experiment','exp_contract_v40',1,'experiment.start','2026-07-28T00:00:00+00:00',1);
CREATE TABLE tenant_quotas (
  tenant_id TEXT PRIMARY KEY,
  max_concurrent_sandboxes INTEGER,
  max_time_limit_seconds INTEGER,
  max_price_usd_per_hour REAL,
  gpu_hours_budget REAL,
  usd_budget REAL,
  blob_bytes_budget INTEGER
);
CREATE TABLE tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  -- Correlates every row a single HTTP request produced (X-RP-Request-Id).
  request_id TEXT NOT NULL DEFAULT '',
  -- Non-secret caller identity: key:<project_api_keys.id>, user:<uuid>,
  -- 'local', or 'open'. Never a token, never a digest of one.
  principal_id TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL DEFAULT '',
  target_type TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'rejected')),
  error_code TEXT NOT NULL DEFAULT '',
  -- First line of the failure, secret-scrubbed and capped: enough to group
  -- errors, never enough to reconstruct a payload.
  error_head TEXT NOT NULL DEFAULT '',
  duration_ms INTEGER NOT NULL DEFAULT 0,
  sent_chars INTEGER NOT NULL DEFAULT 0,
  received_chars INTEGER NOT NULL DEFAULT 0,
  -- sha256 prefix of the redacted arguments: a retry loop repeats one digest.
  args_digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE tracking_deliveries (
  project_id TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT '',
  target_id TEXT NOT NULL DEFAULT '',
  delivery_id INTEGER NOT NULL,
  event_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE user_hf_tokens (
  user_id TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX litreview_one_summary
  ON litreview_sections(project_id) WHERE kind = 'summary';
CREATE INDEX idx_submissions_target ON submissions(target_type, target_id, attempt_index, created_seq);
CREATE INDEX idx_artifacts_submission ON artifacts(target_type, target_id, attempt_index, submission_id);
CREATE INDEX idx_tool_calls_project ON tool_calls(project_id, id);
CREATE INDEX idx_tool_calls_status ON tool_calls(status, id);
CREATE INDEX idx_tool_calls_tool ON tool_calls(tool, id);
CREATE INDEX idx_events_project ON events(project_id, id);
CREATE INDEX idx_storage_objects_content  ON storage_objects(namespace, content_sha256, status);
CREATE INDEX idx_storage_objects_latest  ON storage_objects(project_id, status, name, version DESC);
CREATE INDEX idx_storage_objects_producer  ON storage_objects(project_id, producing_experiment_id, status);
CREATE INDEX idx_storage_objects_upload  ON storage_objects(project_id, upload_id);
CREATE INDEX idx_sandbox_generations_tenant  ON sandbox_generations(tenant_id, started_at);
CREATE INDEX idx_sandbox_generations_project  ON sandbox_generations(project_id, created_seq);
CREATE UNIQUE INDEX idx_oauth_clients_fingerprint  ON oauth_clients(metadata_fingerprint);
CREATE INDEX idx_oauth_codes_client  ON oauth_authorization_codes(client_id);
CREATE INDEX idx_oauth_refresh_tokens_client  ON oauth_refresh_tokens(client_id);
CREATE INDEX idx_events_target  ON events(project_id, target_type, target_id, id);
CREATE UNIQUE INDEX idx_tracking_deliveries_key  ON tracking_deliveries(project_id, target_type, target_id, delivery_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',100);
COMMIT;
