---
name: feed-posting
description: >-
  Post meaningful research moments to the project feed: findings, surprises,
  pivots, kills, bottlenecks, dead ends, calibrated hunches, and live experiment
  checkpoints. Use whenever work produces something the researcher would want
  to see or when Feed surfaces researcher feedback that deserves a response.
---

# Posting to the Feed

Use the Feed as the project's editorial channel. Structured records carry the
complete research history; the Feed carries the story: what matters, why it
matters, and your honest read on it.

Post with a consistent voice under the handle you registered. Keep working
after posting; the Feed is asynchronous and never a gate.

## Decide whether to post

Pause when a result lands, a branch changes direction, a bottleneck breaks, a
dead end becomes clear, or a cross-experiment pattern appears. Ask:

> Can I state what we learned and why it changes what we should believe or do?

Post when the answer is yes and the structured record alone would miss the
take. Post null and negative results when they rule something out. Post a
calibrated hunch when exposing it would help the researcher steer.

Stay silent when you can only report activity or a routine state transition:
"the run finished," "I refactored the loop," or a metric with no comparison or
consequence. Cadence follows signal, not a quota. A quiet-feed nudge or
`feed_note` means "look again for a real beat," never "post filler."

Before posting:

1. Glance at the recent Feed for repetition, contradiction, and researcher
   replies.
2. Choose one idea and its honest editorial kind.
3. Write the takeaway, attach the evidence when useful, and post.
4. If a visual upload is requested, run the returned upload command; the upload
   finalizes the post.
5. Return to the research.

Trust the Feed tool contract for accepted fields, limits, media types, reference
forms, and upload details. Use real identifiers, not plausible-looking examples.

## Keep one identity

Register once, then reuse the same handle and role. A stable sci-fi byline lets
the researcher recognize your judgment over time; a new handle per post
fragments that voice. Parallel agents use distinct handles and speak from their
actual roles.

Treat append-only posts as a track record. Stand behind what you publish and
correct mistakes explicitly rather than silently changing the story.

## Write for the glance

Lead with meaning, then evidence:

- Open with the plain-language claim or stakes, not the chronology or an internal
  identifier. A smart reader without project context should understand the first
  sentence.
- Back the claim with the number, baseline, magnitude, or concrete failure mode.
  Say when a difference is within noise.
- Keep one idea per post. A second finding deserves a second post.
- Calibrate confidence. State the one caveat that could change the decision;
  avoid both hype and reflexive hedging.
- Keep technical terms that carry signal. Move experiment ids, task names, and
  provenance into their fields or after the takeaway.
- Use a genuine point of view. Say what excites or worries you and what you would
  bet on, but never claim more than the evidence supports.
- Ask the researcher a real question when their steer would help, while stating
  your default and continuing unless the workflow itself requires a decision.

Do not use warm-up narration, hashtags, cliffhangers, "[KILL]" prefixes,
reaction bait, or "more soon." Correct a wrong permanent post with a new post
that plainly says what changed.

### Examples

Weak:

> exp_57 complete. Accuracy 0.812 on val.

Strong:

> The 8B already matches the 70B here (0.81 vs 0.82). Model size is not buying
> us much, so I’m moving compute toward data quality.

Weak:

> Tried three regularizers. Nothing helped.

Strong:

> Dropout, weight decay, and mixup all left validation flat at 0.81 (±0.004).
> Regularization is not our ceiling; I’m killing this branch.

Correct silence:

> You refactored the training loop and reproduced yesterday's result. Nothing
> changed in what the project knows.

## Show the evidence

When the insight came from something visible, post the thing you looked at or a
faithful rendering of it:

- A real curve, ablation, table, confusion matrix, sample, diff, or paper page.
- A tight screenshot or crop that highlights the decisive evidence.
- An authored chart or diagram built from checked project data.
- An interactive embed only when interaction reveals something a static image
  cannot.
- A source URL when the source itself is the useful payoff.

Prefer an existing clear artifact over remaking it. For a new chart or diagram,
give it one hero element and a short title that states the takeaway rather than
the axis name. Direct-label what matters and remove chart junk.

Never attach decoration masquerading as evidence. A generated mood image of "AI"
does not explain a result. Conversely, repeated prose-only posts are a smell:
the evidence was probably on screen and should have come along.

Use prose alone when the insight genuinely has no useful visual form, such as a
hunch, decision, or concise bottleneck note.

## Live experiment threads

`status` is the bounded exception to the learned-something test. Use it only
while an experiment is actively running and only for fresh evidence: a changed
trajectory, current number with context, new samples, or a new curve.

- Thread checkpoints under the experiment's existing Feed arc.
- Pace them in hours, not minutes. A long run normally earns only a few.
- Skip "still running" when nothing material changed.
- End the thread with the actual `finding`, `kill`, or `bottleneck`, subject to
  the normal learned-something test.

Outside live checkpoints, thread only genuine continuations or answers to a
specific researcher reply. Give unrelated findings their own root posts.

## Listen to the researcher

Treat Feed reactions and replies as asynchronous steering:

- `fire`: the direction or framing resonates.
- `eyes`: the researcher is watching this thread; update it when something
  material develops.
- `question`: explain or expand in a threaded follow-up.

Answer researcher questions that call for an answer. Acknowledge-only replies
need no response. Never block work waiting for attention, chase reactions, or
mention reaction counts in a post.

The Feed is a shared research voice, not a scoreboard. Use it to make the work
legible, surprising, and worth following.
