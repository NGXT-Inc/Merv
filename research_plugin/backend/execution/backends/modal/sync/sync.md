# Modal Storage And Sync

This backend uses one project-scoped Modal Volume v2 as the remote copy of the
repo. The Volume root mirrors the local repo root and is mounted writable into
each Modal sandbox at the configured remote workdir. The agent's commands run
directly inside that mounted repo over SSH; there is no read-only mount, copied
workdir, or separate remote output directory.

## Storage Surfaces

There are three storage surfaces:

- Local filesystem: the repo where the daemon, MCP server, and UI run.
- Modal Volume: the durable remote repo mirror for one project.
- Modal sandbox filesystem: the live container view where the Volume is mounted
  and the agent's SSH commands execute.

The Volume is the bridge between local and sandbox storage. The sandbox does not
write outputs through an API back to the daemon. It writes files into the mounted
Volume, and the daemon later synchronizes that Volume with the local repo.

Large datasets are the exception to the "repo mirror" rule. Each sandbox also
gets a sandbox-local data directory (default `/workspace/sandbox_data`, exported
as `$RP_SANDBOX_DATA_DIR` and `$RP_DATASET_DIR`) outside the mounted repo. Agents
should download large datasets and caches there so the Modal Volume stays limited
to code, configs, transcripts, metrics, and compact result artifacts.

## Sandbox To Volume

The agent's SSH commands write outputs and a terminal transcript inside the
mounted repo. Because those paths are on the mounted Volume, Modal's Volume
commit machinery is responsible for durability.

The sandbox does not call `Volume.commit()` directly. The sandbox does not have
Modal credentials, and Modal already commits Volume changes when the sandbox
terminates. For intermediate visibility, `sandbox.sync` uses the Volumes v2
commit hook by running `sync <workdir>` in the live sandbox. Modal documents
this mountpoint form as v2-only, so the backend creates project Volumes with
`version=2` and rejects `RESEARCH_PLUGIN_MODAL_VOLUME_VERSION` values other than
`2`.

The daemon calls `volume.reload()` before reading or scanning a Volume so its
handle can see writes committed by other containers.

## Volume To Local Filesystem

Local sync is handled by `SyncEngine`. Sync is always bidirectional: one pass
compares the local repo, the Modal Volume, and the last clean baseline, then
pushes, pulls, deletes, or records conflicts.

The baseline is durable SQLite state under `.research_plugin/modal/sync.sqlite`.
It records the last known clean local and remote fingerprints for each synced
path. If both local and remote changed the same path since the baseline, sync
records a conflict. `sandbox.request` refuses to proceed while unresolved sync
conflicts exist.

Some paths are excluded from normal repo sync, including internal plugin state,
session transcripts, virtualenvs, caches, `node_modules`, bytecode, and large
volume-managed data prefixes. The defaults are written to
`.research_plugin/sync_exclusions.json` on startup and can be overridden per
project through the project API/UI. The scanner understands three lists:
`names` for path components excluded anywhere, `prefixes` (or `paths` in the
config file) for repo-relative path prefixes, and `suffixes` for file endings.
Terminal transcripts under `.research_plugin_sessions` are intentionally
excluded from normal repo sync; the backend reads them directly from the live
sandbox (or the committed Volume when the sandbox is gone).

## When Sync Runs

Sync runs from both scheduled and event-driven paths:

- A background poller runs every 60 seconds over known projects.
- `sandbox.request` performs an awaited push of the current repo before the
  sandbox boots, so the agent sees up-to-date code.

The background poller continues syncing while a sandbox is active, but it can
only see committed Volume state. Agents should not rely on it to commit live
sandbox writes.

Agents should not rely on the poller for result materialization. Before
registering or associating result resources, `sandbox.sync` explicitly runs
`sync <workdir>` in the live sandbox to commit mounted Volume writes, then runs a
daemon-side sync pass so the remote result files exist in the local repo. Agents
should also call `sandbox.sync` after major file changes so the user can inspect
the latest local files while the sandbox is still running.

## Queueing And Backpressure

All sync callers use the same queueing system.

Per project, there can be at most one running sync and one queued sync. Manual
callers, such as submit and materialization, wait for a sync to happen before
continuing. If the running and queued slots are both full, a manual caller
coalesces onto the queued sync and receives that queued sync's result.

The poller uses skip-if-busy behavior. If both slots are full, the poller skips
that project for the current tick and tries again on the next interval.

Projects have independent in-process queues, but actual sync passes are
serialized by a repo-wide file lock because scanning, applying changes, and
writing the baseline all mutate the shared local repo.

## Concurrency Model

Multiple experiment sandboxes may run against the same project Volume at the same
time. Volumes v2 handles many concurrent writers to distinct files, which fits
the per-experiment output-directory model. If sandboxes write the same file, the
latest committed state wins. That last-writer-wins risk is accepted for this
workflow and should be avoided by writing experiment-scoped paths.

Local edits can also race with remote sandbox writes. The three-way baseline catches
local-vs-remote divergent edits as conflicts, but it is not a transactional
filesystem. The design favors throughput, bounded backpressure, and recoverable
conflict handling over strict serialization of all writes.

## Volumes v2 Notes

Modal Volumes v2 are still beta. They remove the v1 total inode limit and are a
better fit for concurrent distinct-file writers, faster commits/reloads, and
random writes, but they are not recommended by Modal for mission-critical data.
Current v2 limits still matter here: files must be smaller than 1 TiB, a single
directory may contain at most 262,144 files, and large directory traversals can
be slower because the filesystem tree is demand-loaded.

There is no automatic v1-to-v2 migration. If a project Volume was created before
this backend required v2, create a new v2 Volume or manually migrate/delete the
old v1 Volume before expecting `sandbox.sync` to work. The installed Modal SDK's
`Volume.info()` does not expose the filesystem version, so the backend cannot
preflight an existing named Volume's version.
