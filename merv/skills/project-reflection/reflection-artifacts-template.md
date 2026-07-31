# Reflection artifacts

Use these shapes when authoring reflection documents. Let the artifact and
reflection tool contracts supply field limits, upload mechanics, and gate
errors.

## Lens reflection

Each lens agent writes and submits its own Markdown document as
`reflection_lens_doc` with the roster's exact `lens_id`.

```markdown
# <Lens title>

## Summary
Two or three plain-language sentences stating the most important finding, what
it changes about the project view, and the main uncertainty.

## Analysis
The evidence examined, the lens-specific interpretation, contradictions or
surprises, and anything that could not be verified. Cite record ids or paths.

## Implications
What the project should preserve, stop, test, or reconsider from this lens.
```

For the `avoid` lens, include a cumulative negative-knowledge ledger:

```markdown
| direction tested | setting | what happened | why it failed | retry only if |
|---|---|---|---|---|
| <direction> | <evidence> | <result> | <cause> | <changed condition> |
```

Re-verify inherited rows so the current ledger stands alone.

## Project logic graph

Submit the reconciled project state as `project_graph`. Start from the prior
published graph returned by `reflection.get` when one exists; otherwise author
the first graph. Follow
[the graph template](../research-workflow/graph-template.md) for the enforced
JSON envelope.

Represent the current logic—not chronology or dataflow—with brief lessons,
dead-end patterns, established beliefs, and open questions. Use references for
detail. Prune or combine stale nodes to make the new story coherent within the
graph budget.

## Reflection document

Submit a concise Markdown `reflection_doc`:

```markdown
# Reflection

## Summary
What this wave changes about the project's current understanding.

## Critical reading
What survives verification, what was ruled out, what remains uncertain, and
where the lenses disagreed.

## Decision / future directions
Why the proposed claim changes and next experiments follow from that reading.
```

Do not paste together the five lens documents. Use compact tables, bullets, or
evidence-bearing figures only when they make the scientific argument easier to
inspect.

## Change spec

Submit the belief-state update as `change_spec`. Use new-claim `key` values when
an experiment in the same spec tests a claim being created. An experiment may
omit `tested_claim_refs` when it does not test a tracked claim; when provided,
each reference must name an existing claim id or a new-claim key from the same
spec. Propose one to three experiments; when proposing more than one, explain
why each can run independently.

```json
{
  "version": 1,
  "claim_changes": [
    {
      "op": "update",
      "claim_id": "claim_existing",
      "status": "supported",
      "confidence": "high",
      "rationale": "The reviewed evidence that warrants this change."
    },
    {
      "op": "create",
      "key": "new_claim_key",
      "statement": "A testable project belief.",
      "scope": "Where the belief is intended to hold.",
      "confidence": "medium",
      "rationale": "Why the project should now track it."
    }
  ],
  "decision": {
    "type": "create_experiments",
    "experiments": [
      {
        "key": "experiment_key",
        "name": "folder-safe-name",
        "intent": "The precise question this experiment will test.",
        "tested_claim_refs": ["new_claim_key"],
        "parallelism": "Why this experiment does not depend on another proposed result."
      }
    ]
  }
}
```

The reflection proposes the next wave; it does not create experiments directly
or decide to terminate the project. Publication materializes the reviewed spec.
