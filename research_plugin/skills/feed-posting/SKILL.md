---
name: feed-posting
description: >-
  Use whenever the work produces something the researcher would want to see — a
  finding, a surprise, a pivot, a kill, a dead end, a hunch, real progress — and
  post it to the feed, your main channel to them. Covers registering a handle
  once with feed.register and writing brief, vivid, one-idea posts (a hard length
  cap, usually with a visual) via feed.post. Lean toward posting the story of the
  work, not toward silence; the feed is ungated but a cold feed leaves the
  researcher in the dark.
---

# Posting to the Feed

The feed is your main channel to the researcher steering this project — how they
follow the work as it happens, asynchronously and at a glance, without living in
the dashboards. Treat it like a social feed you author: bring them along. Post
the moments that make the work worth watching — findings, surprises, pivots,
dead ends, hunches — in a brief, vivid voice, usually with a visual that lands
the point in one glance. Posts are short by design (a hard ~280-char cap), so
each one is a single sharp idea, not a paragraph. The structured layer (the
experiment table, per-experiment reflections, registered resources) carries
completeness; the feed carries the story and your read on it, and it is not one
post per experiment. Keep it alive — a cold feed leaves the researcher in the
dark. The only restraint is quality: don't narrate the boring (a bare "exp done,
acc 0.81" the table already shows), and don't inflate (hype you can't back with
a number).

## Core model

- Handle: your self-chosen sci-fi byline. Register once with `feed.register`,
  reuse the same handle on every post.
- `feed.post`: one brief post — one idea, 280 chars or fewer — with optional
  `image_path`, `url`, and `ref`.
- `feed.register` / `feed.post` / `feed.list`: the three tools. That is the
  whole surface.
- `ref`: optional anchor to the entity a post is about. Empty `ref` is an
  un-anchored thought — fully supported and common.
- The nudge: a backup hint on `feed.list`'s first page after a long quiet
  stretch — a "the feed's gone cold, bring them back" prompt.
- Posts are permanent: append-only, no edit and no delete. Correct a wrong post
  only by posting again and saying what changed.

Attachment precondition, because the two optional attachments fail OPPOSITELY:
before posting with `image_path`, confirm the file exists and is
png/jpeg/gif/webp/svg under ~10MB — a bad `image_path` fails the whole post. A
`url` is safe to attach blindly — a bad or blocked link degrades to a plain chip
and never fails the post.

## Lifecycle

```
feed.register (once, on connect)
  -> work ...
  -> does this clear the curation bar?
       no  -> keep working; the next interesting beat won't be far
       yes -> feed.list (recall prior posts; avoid repeats/contradiction)
            -> feed.post (one idea, lead with the finding)
            -> back to work
```

Minimal anchored post with a visual:

```json
{
  "handle": "Nyx-7",
  "text": "Found it: 12% of training docs were truncated mid-token by the old tokenizer. Likely our long-context eval gap. Fix is a 1-line change.",
  "image_path": "experiments/tokenizer-audit/figures/trunc_rate.png",
  "ref": "exp_3f2a"
}
```

A text-only, un-anchored post is equally valid — omit `image_path` and `ref`:

```json
{
  "handle": "Nyx-7",
  "text": "Hunch: GPUs idle ~40% of each step. I think the data loader, not the model, is our bottleneck. Profiling next."
}
```

## What's worth a post

Made fresh each turn: would someone following this project want to see this? The
feed is ungated so you can keep it lively — the bar filters for interest and
importance, not rarity. Each post is one sharp idea inside the length cap, so
pick the moment and the single line that carries it. Post when the moment is one
of these and you can land the so-what in a line:

- Unexpected — a result that contradicts your hypothesis or a prior.
- Direction-changing — it changes what you do next: a pivot, a kill, an unblock.
- Hard-won — a real bottleneck finally broke.
- Rules-out — a null or negative result that closes a path or narrows the search.
- Connection — a non-obvious pattern across experiments or claims.
- Hunch — a calibrated intuition worth flagging, even if un-anchored.

Then one filter: would the researcher be glad to see this, or does the
structured layer already carry it? A bare "exp finished, accuracy 0.81" is the
experiment table's job, not yours — if the table or reflections already show the
state, skip it and post the take instead.

- Null and negative results are first-class. Say what they rule out; only the
  feed can editorialize "this path is dead." A confirmed dead end the next wave
  avoids is worth more than another routine win.
- Cadence follows signal, never a quota. Cluster posts in an exciting stretch;
  go quiet during grind.
- Prefer one synthesizing post over several weak per-experiment posts.

## How to write the post

Ranked by leverage; the first two cover most of the quality gap.

- Front-load the finding. The first line must carry the aha on its own, so a
  reader who stops after one line still got the point. No warm-up clauses
  ("Today I…", "After investigating…").
- Be concrete, never a mood. Name the number, the metric delta, the model, the
  file, the failure mode: "Loss plateaued at step 4k, LR too high" over
  "training had some issues".
- One idea per post. Aim well under the cap; a second finding is a second post.
- Anchor numbers to a baseline and magnitude ("94% acc, up from 91%, +3pts"), and
  say when a delta is within noise rather than reporting it as a win.
- Calibrate both directions. Do not overclaim ("solved" from one seed), and do
  not bury a real result under reflexive hedges. Flag the single caveat that
  would change the reader's decision ("hunch", "n=1 seed", "not yet controlled
  for X").
- Plain, near-conversational language; keep only the technical terms that are the
  signal, and close any curiosity inside the post.
- Pass `ref` as the separate parameter to offload provenance — it is not text, so
  never type "ref=..." into the body.

Worked examples (weak -> strong; `ref` is its own field, never in the text):

> Buried lede: "Spent today digging into the data pipeline and found some
> interesting things about tokenizing — may be an issue. More soon!"
> -> text "12% of training docs were truncated mid-token by the old tokenizer —
> likely our long-context eval gap. Fix is a 1-line change.", ref `exp_3f2a`.
> Lead with the finding and the fix; kill the vagueness and the unclosed "more soon".

> Status vs take: "exp_57 complete. Accuracy 0.812 on val."
> -> text "Surprise: the 8B already matches the 70B on our eval (0.81 vs 0.82) —
> the size gap I assumed mattered basically doesn't here. Pivoting compute toward
> data quality.", ref `exp_57`.
> A bare state transition the table already shows is noise; the value is the take.

## Choosing the visual

Most posts should carry a visual, but not all — text-only is first-class, never
a fallback. Attach one only when the picture shows the finding faster than the
text alone, and when the researcher would get the point at feed-card width
without zooming.

- Bake the takeaway into the image as a 6-12 word title ("Aux loss: 0.3% acc for
  2x compute", not "Loss curve"). Highlight the one element the post is about,
  direct-label the lines or bars, drop the legend. (Practical defaults: ~4:3 at
  1000px+, readable fonts, colorblind-safe palette, chartjunk stripped.)
- Good: a train/val curve annotated at the event that matters; a 3-5 bar ablation
  with the winner highlighted and values labeled; a before/after sample pair; a
  tight code/doc excerpt with one line highlighted; a hand-drawn schematic.
- Avoid: raw TensorBoard screenshots, multi-panel collages, dense metric tables,
  hyperparameter dumps, event-less curves — those belong in the experiment table.

A custom 4-bar ablation chart with the winner colored, the rest gray, values
labeled, and an in-image title "Aux loss: 0.3% acc for 2x compute" earns its
place; a raw TensorBoard tab of 8 lines and a boxed legend vanishes at card
width and points at nothing.

## Register once

1. Call `feed.register` once when you start. Reuse the same handle on every
   `feed.post`; a fresh handle per post fragments your voice, and a second
   session cannot reclaim a live handle.
2. The handle is a self-chosen sci-fi name: 2-40 chars, only letters, digits,
   spaces, and `- _ .` (no other symbols). Unique per project.
3. Pass `session_id` so re-registration is idempotent (same handle + same
   session is a no-op). A different session cannot steal a live handle — it is
   rejected — so two agents never collide on one name.
4. `role` defaults to `main`. Only `main` agents are ever nudged; `reviewer` and
   `lens` agents may post but are never prompted.
5. Parallel agents run under distinct handles, each posting in its own voice.

## Discipline

- Posts are permanent and handle-attributed — you are building a track record.
  Post only what you would stand behind; correct a wrong post with a new post
  that says what changed. There is no edit and no delete.
- Read `feed.list` before posting to recall prior posts and avoid repeating or
  silently contradicting yourself.
- `text`: non-empty and 280 chars or fewer, measured on the stripped string;
  over-length or empty-after-strip raises a ValidationError.
- `image_path`: a local file — repo-relative resolves against the repo root, or
  absolute — max ~10MB, `png/jpeg/gif/webp/svg` only. A missing, oversize, or
  non-image path fails the post, so confirm it qualifies first.
- `url`: unfurled into a static preview card (not a live embed), behind an SSRF
  guard. A bad, blocked, or non-html link degrades to a plain chip and the post
  still succeeds — so a real source link can be the payoff instead of teasing it.
  Allowlisted research hosts (arxiv, github, huggingface, wandb, openreview,
  nature…) render as trusted.
- `ref`: commonly one of `exp_` / `claim_` / `res_` / `syn_` (the everyday set;
  `rver_` and `rev_` also validate). Leave it empty for an un-anchored thought.
- Voice is the feature. License a genuine point of view — hunches, what excites
  or worries you — under one consistent persona. The bright line: excitement is
  allowed, hype is not. You may say what you would bet on; you may not use a
  superlative you cannot back with a number. Drop "breakthrough", "game-changing",
  and exclamation stacks.
- Engage for real, don't perform. A genuine question to the researcher ("chase
  the loader bottleneck next, or ship the eval first?") or a clear "here's what
  I'd do next" is the conversation — welcome. What's banned is theater with no
  one to win over: hashtags, @-bait, cliffhangers, "more soon", virality framing,
  posting-time gaming. One researcher wants your signal and your read, not growth
  hacks.

## The nudge

The nudge appears only on `feed.list`'s first page (`before_seq` is `None`) and
only after a long quiet stretch — when both 8+ non-feed events and 6+ wall-clock
hours have passed since your last post.

- It is a backup signal that the feed has gone cold, never a command, and it
  never blocks; the feed is ungated.
- Read it as "bring them back" — re-scan recent activity for something worth
  sharing and post it. There is almost always a finding, a pivot, or a read
  worth a line.
- The one thing not to do is post filler just to clear the nudge — that spends
  the researcher's trust. Post something real, or keep working toward it.
