# Rewrite contract baseline

This is the compatibility gate for simplifying Merv one module at a time. The
baseline is the released `f0439cab` SQLite state-store schema with migration 40
applied.

## Frozen behavior

The following surfaces must stay compatible while module ownership, file
layout, and implementation structure change:

- MCP tool names, public/internal profiles, feature gating, scope strategies,
  and semantic input schemas, including Artifact, Feed, and Sandbox tools.
- The six Artifact HTTP method/path pairs and their stable response fields.
- Artifact submit, document upload, figure upload, list, content, raw-file,
  and figure-read behavior across the tool and HTTP boundaries.
- Durable event names, plus the exact `artifact.submitted` and
  `artifact.pinned` payloads and their transaction boundaries.
- The callable shapes of `ArtifactSubmissions`, `Artifacts`,
  `EvidenceReader`, `SubmissionSealer`, and `AssociationTargetResolver`.
- The release-v40 database schema, indexes, constraints, migration ledger, and
  readability of released Artifact/submission/figure/review/event rows through
  current Kernel and component-owned schema installation.

The contract tests intentionally do not freeze:

- Which application object or module owns a tool handler.
- Internal classes, helper functions, source files, or package layout.
- Human-readable descriptions and error prose.
- Generated IDs, upload tokens, timestamps, hostnames, or base URLs.
- Private return values that never cross a declared module or transport
  boundary.

## Rewrite rule

Keep the current boundary as a thin compatibility adapter while moving logic
behind it. Read from the same authoritative persistence path and switch writes
atomically; do not dual-write old and new implementations. Shadow comparison is
safe for pure reads, but compatibility must ultimately be decided by the
contract suite.

An intentional public contract or schema change should update the frozen
inventory or fixture in the same change, with the migration and compatibility
reason called out explicitly. A refactor alone should make no baseline edits.

## Gate

Run the focused baseline from `merv/`:

```bash
PYTHONPATH=src python -m unittest \
  tests.structure.test_dependency_contracts \
  tests.structure.test_event_catalog \
  tests.surface.test_tool_contracts \
  tests.surface.test_artifact_flow \
  tests.application.test_component_facades \
  tests.state.test_artifact_submissions \
  tests.compat.test_release_db_compat
```

The SQL fixture at
`tests/compat/fixtures/release_f0439ca_v40.sql` is an immutable dump created by
the released code. Generate a new fixture only for an intentional new release
baseline, never merely to make a rewrite pass.

The fixture captures the released Kernel state-store schema. The compatibility
test first proves that current Kernel boot preserves it, then installs Feed's
owned schema as application composition does. Every released row must survive,
the upgrade must be idempotent, and a fresh composed database must converge to
the same schema.
