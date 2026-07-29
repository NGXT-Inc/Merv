# Feed module

## Responsibility and boundary

`FeedService` owns the project-scoped editorial stream: author registration,
short posts, replies, reactions, media/link presentation, pagination, and
non-blocking posting advisories. Posts are observations for humans, not
research artifacts or workflow state. `FeedAdvisory` is the deliberately narrow
capability consumed by Application after committed experiment transitions.
`__init__.py` exports only `FeedService` and that protocol.

The service depends inward on `BaseStateStore` for project resolution,
transactions, sequence allocation, and event recording. It delegates bytes to
`EvidenceBlobStore`, outbound URL inspection to `WebPreview`, image/HTML
validation and embed wrapping to shared feed helpers, and exposes stored data
only through normalized views. HTTP/MCP routing, authentication, UI behavior,
workflow policy, and blob implementation remain outside this package.

## Write flow

1. `register` validates a handle and role, resolves the project, and upserts the
   `(project_id, handle)` identity. A live handle cannot be claimed by a
   different nonempty session; registration emits `feed.author_registered`.
2. `post` validates and resolves a `PostIntent`: the author must already be
   registered in that project, text is stripped and limited to 280 characters,
   `kind` and entity-reference prefixes are allowlisted, and a reply target must
   exist in the same project.
3. Text/link-only posts call `_create_post` immediately. Image or HTML posts
   instead persist a 15-minute one-use upload token and return a shell-quoted
   `curl` command; the transport asks `get_upload_limit` before buffering and
   passes bytes to `complete_upload`.
4. Completion revalidates the saved intent, sniffs and size-checks bytes, stores
   them in the project blob namespace, then atomically claims the token, inserts
   the post, advances `created_seq`, updates the author's last-post time, and
   emits `feed.post_created`. Concurrent or replayed uploads cannot create the
   preallocated post twice. Expired tokens are swept in their own transaction.
5. Link unfurling is best-effort: ordinary preview failures preserve a plain
   HTTP(S) link plus error metadata; non-web schemes store no clickable URL.
   Preview images are rehosted only for serveable sniffed types, excluding SVG.

`researcher_reply` idempotently creates the fixed `Researcher` identity and
uses the normal validated post path. `set_reaction` idempotently toggles one of
`fire`, `eyes`, or `question`; the schema permits one reaction of each kind per
project/post because a project has one researcher.

## Read and advisory flow

`list_posts` reads one project in reverse `created_seq` order, clamps page size
to 1–100, fetches one extra row to derive the exclusive `before_seq` cursor,
loads all page reactions in one query, and strips internal blob hashes from
post/link views. Only page one may include researcher-attention summaries and a
soft cadence nudge. Media readers re-check project ownership before loading
blobs; embeds are returned CSP-wrapped, while images retain their sniffed media
type (legacy unknown link-image types fall back to non-renderable
`application/octet-stream`).

Cadence counts non-feed events since the latest non-researcher post. A nudge
appears only after at least eight such events and, when a prior agent post
exists, six hours; it never gates work. `transition_advisory` is also read-only
and best-effort: after a committed transition it suggests posting only when no
post in that project references or literally mentions the experiment.

## Persistence and invariants

`persistence.py` owns four tables: immutable `posts`, project-local
`feed_authors`, idempotent `post_reactions`, and pending `feed_upload_tokens`.
`install_feed_schema` runs at service construction and converges legacy stores
by probing and adding later post columns; only a verified concurrent-ALTER win
is suppressed. All externally supplied project IDs pass through
`require_project_id`, reply/media/reaction lookups include project scope, blob
access uses the same project namespace, and exposed post order is the monotonic
`created_seq`, not timestamp ordering.
