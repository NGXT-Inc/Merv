---
name: research-workflow
description: >-
  Operate the Merv experiment workflow: select a project, follow
  workflow.status_and_next, create and run experiments, submit evidence, and
  coordinate required design or attempt reviews. Use for experiment-level
  research work and for resuming an experiment already in progress.
---

# Research Workflow

Treat Merv as the authority for research state. Local files are working
material; they affect the workflow only after submission through MCP.

## Follow the state machine

The experiment lifecycle is:

```text
plan → design review → run → submit results → attempt review → complete
 ↑          │          ↑                         │
 └──────────┘          └─────────────────────────┘
```

A rejected design returns to planning. A rejected attempt returns either to
execution when the design still stands or to planning when the design itself
was flawed. Follow the returned state and `next_action`; never infer a
transition from memory.

Operate in this loop:

1. If the project is unknown, call `project(action="list")` and select the one
   the user means. Never guess an id. Use `current` only when the credential is
   known to be bound to exactly one project.
2. Call `workflow.status_and_next(project_id, experiment_id?)`.
3. Read its context, gates, allowed actions, missing evidence, and next action.
4. Do that work locally or through the specialist skill it names.
5. Submit mutations and evidence through MCP.
6. Call `workflow.status_and_next` again after every transition or review.

Pass the selected `project_id` explicitly to every project-scoped operation.
Use `project(action="overview", project_id=...)` when you need the whole
project picture or must check that a proposed claim or experiment is not a
duplicate of settled work.

## Keep one experiment folder

Create the folder returned by `experiment.create`, normally
`experiments/<name>/`, before writing experiment files. Keep its plan, code,
configuration, compact results, figures, report, and logic graph together.
Choose a short experiment name that distinguishes it from sibling experiments
and a one-line `intent` that states what it tests.

There is no automatic synchronization between the checkout, a sandbox, and
Merv. Pull remote outputs into the experiment folder before submitting them.
Use durable object storage for large binary outputs.

## Author the experiment record

Use the bundled templates only when creating their corresponding documents:

- [plan-template.md](plan-template.md) for `plan.md`. Pre-register the
  hypothesis, method, comparison, decision rule, success threshold, and
  invalidation conditions before execution.
- [report-template.md](report-template.md) for `report.md`. Interpret the
  submitted results against the plan's decision rule, disclose deviations and
  failures, and keep conclusions within the tested scope.
- [graph-template.md](graph-template.md) for `graph.json`. Record the reasoning
  path—questions, decisions, pivots, consequences, and lessons—not a pipeline,
  event log, or generated metrics diagram.

Start the graph early and update it when reasoning changes. Write the plan,
report, and graph for a human reader; raw data and logs belong in separate
result artifacts.

## Preserve honest evidence

For quantitative work, save compact machine-readable results and the figures
used to reach the conclusion. Include enough provenance to identify the run,
configuration, data or evaluation slice, seed, metric, and metric direction.
Retain every attempted seed or configuration, including failed, partial, and
aborted runs. Never submit only the favorable rows.

Treat the plan's Evaluation section as the contract. The report interprets the
record; it does not replace it. A conclusion is ready only when the submitted
results support it under the pre-registered rule.

## Submit artifacts

Follow `artifact_guidance` and the `artifact.submit` tool contract for roles,
fields, limits, figures, and upload commands. The durable evidence is the
uploaded content, not the current local file:

1. Write or update the local file.
2. Submit its metadata with the exact target and role requested by the
   workflow.
3. Run every returned upload command, including figure uploads.
4. After any edit that should affect a gate, resubmit and upload the file.

Use artifact ids already returned in authoritative context. Batch focused
reads with `artifact.find`; request full content only when summaries cannot
answer the question.

## Route specialist work

- Load `sandbox-operation` before provisioning or operating a sandbox. Use it
  for provider selection, caller-owned keys, durable runs, observation,
  retention, recovery, extension, and release.
- Load `feed-posting` when there is a meaningful finding, surprise, dead end,
  pivot, bottleneck, or researcher reply worth sharing.
- Load `project-reflection` when project-level reflection is requested or
  `workflow.status_and_next` reports reflection work or a reflection gate.
- Keep the living literature review current when a paper materially informs a
  claim, plan, or conclusion: cite it, inspect the outline, and edit only the
  relevant section. Literature guidance is advisory, not an experiment gate.

Run lightweight safe checks locally. Use a sandbox for expensive, isolated,
long-running, data-intensive, or GPU work.

## Coordinate reviews

When the workflow requests review, use `review.request` and pass its returned
handoff unchanged to a separate read-only agent:

- `experiment-design-review` before execution.
- `experiment-attempt-review` after result submission.

The reviewer owns `review.start` and `review.submit`; the producing agent must
not review its own work. Preserve the capability long enough to hand it off,
and do not replace a still-valid request merely because review started.

After submission, call `workflow.status_and_next` and follow its return state.
Revise and resubmit the affected artifacts before retrying a rejected gate.

## Complete only through MCP

Complete an experiment only after:

- the required plan, result, report, and logic-graph evidence is submitted;
- required reviews have passed;
- the conclusion is grounded in the submitted record; and
- MCP accepts the completion transition.

If MCP rejects an action, follow its reported gate and next action. Do not work
around the state machine.
