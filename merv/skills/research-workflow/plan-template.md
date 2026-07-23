<!--
  Experiment plan template (PRD-style).

  This file is the FACE of the experiment: it is what the user reads in the UI
  to understand what the experiment is, and the artifact the design reviewer
  evaluates. Copy it to the experiment plan (e.g.
  experiments/<name>/plan.md), fill it in, then submit it with
  artifact.submit (role "plan") and run the returned upload command.

  REQUIRED spine — `experiment.transition(submit_design)` is blocked until each
  of these has real content (the lint strips these HTML comments, so a section
  left as just guidance counts as empty):
    - Summary
    - Objective & hypothesis
    - Evaluation

  RECOMMENDED — not lint-enforced, but the design reviewer judges whether they
  are sufficient for this experiment and can return needs_changes if not:
    - Method
    - Outputs
    - Risks & confounders

  Figures are supported: relative image links (e.g. figures/diagram.png) are
  captured when the plan is submitted and rendered in the UI. Every link must
  resolve to a local file under 5 MB, or the upload is rejected.

  Keep the title line (`# ...`) to one line; it is the headline. The durable
  `intent` you pass to experiment.create should match it. Delete a RECOMMENDED
  heading only if it genuinely does not apply.
-->

# <Experiment title — one line>

## Summary
<!-- 2–3 plain-language sentences: what this experiment does and why it
     matters. Written for someone scanning the UI — no jargon, no setup. This
     is the face of the experiment. -->

## Objective & hypothesis
<!--
  - What we're testing: the claim(s) and the specific question.
  - Hypothesis: what we expect, and the direction.
  - Why it matters: what decision this informs / why we believe it / what
    changes if we're right or wrong.
-->

## Evaluation
<!--
  How we will judge the experiment once it runs. This is the contract the
  experiment reviewer later grades the conclusion against.
  - Metric(s): what we measure.
  - Baseline / comparison: what we measure against.
  - Decision rule: result X ⇒ supports the claim; result Y ⇒ weakens it.
  - Success threshold: the concrete bar that counts as success.
  - Invalidation: what would make this experiment uninformative.
-->

## Method
<!-- RECOMMENDED. Inputs/data, procedure, and what code runs. Scale the depth
     to the experiment; the design reviewer decides if it is enough. -->

## Outputs
<!-- RECOMMENDED. Named result files this experiment will produce and later
     retain and submit as result artifacts, e.g.
     experiments/<name>/results.json. -->

## Risks & confounders
<!-- RECOMMENDED. What could bias the result or break the run. -->

## Attempt log
<!-- OPTIONAL, yours to keep: a short note per attempt if you find it useful.
     The workflow itself carries prior-attempt context forward in the
     experiment record (`revision_context` from review feedback) — it never
     writes to this file, so nothing here is required. -->
