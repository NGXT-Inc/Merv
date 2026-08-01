# Workflow and review

Research Core owns two reviewed workflows: one for each experiment and one for
project-wide reflection. Their executable declarations are
`research_core/experiment_workflow.py` and `reflection_workflow.py`.
`workflow.status_and_next` is the agent-facing read of current state, gates,
allowed actions, and the next action.

## Experiment workflow

```text
planned -> design_review -> ready_to_run -> running -> experiment_review -> complete
            |                                      |
            +-> planned                            +-> running
                                                   +-> planned

failed and abandoned are explicit terminal exits.
```

The backward paths are deliberate:

- a rejected design returns to `planned` with revision context;
- an execution review returns to `running` when the plan stands but execution
  or the conclusion needs work;
- it returns to `planned` and starts a new attempt when the plan itself is
  flawed.

The forward gates are:

1. **Plan** — a submitted, size-bounded plan with the required sections.
2. **Design review** — a passing independent design review pinned to that plan.
3. **Execution evidence** — current-attempt result artifacts.
4. **Report and graph** — a valid report plus an authored DAG-shaped logic
   graph; when a metrics exhibit exists, the report must interpret it.
5. **Experiment review** — a passing independent review of the exact submitted
   attempt snapshot.

Transitions seal the current Artifact composition in the same database
transaction as the state change. Editing a checkout file has no effect until
the revised file is submitted again.

A project may have at most seven non-terminal experiments. Experiment creation
is also blocked when project-level reflection has become mandatory.

## Reflection workflow

```text
reflecting -> synthesizing -> reflection_review -> consolidating -> published
    ^               ^                |
    |               +----------------+  return_to=synthesizing
    +--------------------------------+  return_to=reflecting

abandoned is the explicit terminal exit.
```

One reflection wave may be open per project. Its gates are:

1. **Roster** — exactly five lenses: `amplify`, `avoid`, `entropy`, and two
   wave-specific lenses with distinct charters.
2. **Lens coverage** — one current-attempt `reflection_lens_doc` per lens.
3. **Synthesis** — a project graph, concise reflection document, and
   materializable claim/experiment change spec.
4. **Reflection review** — a passing independent review pinned to that exact
   synthesis.

Passing reflection review makes the research decision authoritative and enters
code consolidation. One immutable proposal must account for every experiment;
a separate `consolidation_reviewer` checks it. The runner then
compare-and-swaps the exact proposal into Merv's central Git ref. Publishing is
atomic only after that receipt: it records the graph version, applies approved
claim changes, creates the approved experiment wave, and records the event.

A rejection to `synthesizing` retains the lens work. A rejection to
`reflecting` advances the attempt and repeats the fan-out.

## Project-level state

The project has no independent mutable status column. Its effective state is
derived from open reflections, active experiments, and reflection drift:

- an open reflection takes priority in project-level guidance;
- otherwise active experiments determine the current work;
- after three newly terminal experiments, or a claim becoming contradicted,
  reflection is suggested;
- when the project is idle and has new completed work, reflection is
  recommended;
- after five newly terminal experiments, a published reflection is required
  before another experiment can be created.

Experiment-scoped status calls remain focused on that experiment and carry any
project reflection signal alongside it.

## Review boundary

The four workflow roles are `design_reviewer`, `experiment_reviewer`,
`reflection_reviewer`, and `consolidation_reviewer`.

1. The producer calls `review.request`.
2. Research Core pins the target snapshot and returns a short-lived capability
   once, together with a reviewer handoff prompt.
3. A distinct reviewer session calls `review.start` and receives the pinned
   evidence plus bounded context.
4. The reviewer submits one verdict and synopsis through `review.submit`.
5. Submission rechecks that the request and snapshot are still current before
   routing a rejection or satisfying a gate.

Capabilities are stored only as hashes. Declared producer and reviewer session
IDs must differ, but those caller-supplied strings are a workflow separation,
not cryptographic proof of independent execution. See
[REVIEW_IDENTITY.md](REVIEW_IDENTITY.md) for the security boundary and
[MCP_SERVER_CONTRACT.md](MCP_SERVER_CONTRACT.md) for wire shapes.

## Ownership

- Research Core declares states, transitions, gates, attempts, and transaction
  invariants.
- Artifacts owns submitted evidence and immutable sealing.
- Application combines Research facts with Sandbox, Feed, MLflow, and other
  modules to format guidance.
- Surface owns authentication, authorization, MCP/HTTP schemas, and response
  presentation.
- Skills tell agents how to perform the work; they do not define legal state
  transitions.
