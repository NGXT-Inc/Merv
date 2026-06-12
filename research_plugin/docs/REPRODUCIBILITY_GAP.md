# The Reproducibility Gap

_Status: problem statement only. Solution design is deliberately out of scope
for this document._

Reproducible experiments are what this project stands for. Today the system
cannot answer the question every researcher eventually faces:

> "Exactly what produced the number in this report — and could we run it
> again?"

This document records why, grounded in the current code.

## 1. There is no "run" in the durable model

The durable model is claim / experiment / resource. But the unit of empirical
ML work is the **run**: one command, one code state, one configuration, one
seed, one environment, producing one set of metrics. That entity does not
exist anywhere in the schema (`backend/state/store.py`), the tool contracts
(`backend/contracts.py`), or the UI.

Consequences:

- An experiment's results cannot be traced to the specific invocation that
  produced them. Evidence lives as prose in `report.md` and as files whose
  provenance is asserted, not recorded.
- Two result files in the same experiment folder may come from different code
  states, different commands, or different sandbox generations — nothing
  distinguishes them.
- Reviews certify experiment-level prose. The experiment-review gate has no
  mechanical way to check that a reported number corresponds to any execution
  that actually happened.

## 2. Run records are created today — and then destroyed

The in-sandbox SSH `ForceCommand` wrapper
(`backend/execution/bootstrap_tools.py`) already writes a per-command record:
`cmd`, a full `env` dump, streamed `out`, and `exit_code`, under
`$RP_SANDBOX_DATA_DIR/.rp_runs/<run_id>/`.

That directory is in the **unsynced scratch area**. No daemon code reads,
pulls, or ingests `.rp_runs` (the only reference to it in the codebase is the
wrapper that writes it). When the sandbox is released or reaped, every run
record dies with the VM. The raw material for reproducibility is being
captured and then deleted, by design of the sync boundary.

A related defect: the wrapper's `export -p > env` dump includes every secret
exported into the session (`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`), so the one
environment capture that does exist is also a plaintext credential record on
the VM.

## 3. The execution environment is never recorded

Nothing attaches to a result:

- the image / instance type / GPU the command ran on (the daemon knows these
  per sandbox, but they are not stamped onto any output);
- installed package versions (`pip freeze`), Python version, CUDA and driver
  versions;
- the contents of the environment beyond the unsynced, secret-laden dump
  above.

Sandboxes are provisioned from a baseline package list
(`ML_PYTHON_PACKAGES` in `bootstrap_tools.py`) plus whatever the agent
installs ad hoc over SSH. Two runs of the same script weeks apart can resolve
entirely different dependency sets, and the difference is unobservable after
the fact.

## 4. The code state at execution time is not captured

Resources record a content sha256 and a git commit **at registration time**
(`backend/services/resources.py`). But execution does not register anything:
the experiment folder is rsync-mirrored to the VM and continuously mutated
while the sandbox lives. There is no manifest of what the folder contained at
the moment a command ran. A script can be edited between (or during) runs and
every result still looks like it came from "the" experiment code. The
`--delete` mirror semantics make this worse: the local copy is overwritten
while the sandbox lives, so the repo's git history does not reliably contain
the intermediate states that produced intermediate results.

## 5. Seeds and configuration are invisible

No seed is generated, recorded, or required anywhere in the system. Whether a
run was seeded — and with what — exists only if the researcher's own code
happened to log it. Configuration has the same status: it is whatever
happened to be in the folder and on the command line, with no captured,
queryable record per run.

## 6. Metrics are archived but not joined to executions

The daemon archives each sandbox generation's MLflow store and TensorBoard
events into `.research_plugin/sessions/<experiment>/<sandbox_id>/`. This
preserves the numbers, but the join stops at the sandbox: an MLflow run
cannot be connected to the specific command, code state, or environment that
produced it. Within one sandbox's lifetime — many commands, many code edits —
all metrics are effectively co-mingled.

## 7. Input data has no provenance

Datasets are deliberately kept outside the synced experiment folder (under
`/workspace/data`) and die with the VM. There is no content hash, no version
record, and no link between an experiment and the bytes it trained or
evaluated on. Two experiments can both claim to use "the dataset" and
silently use different ones — or the same experiment can, across sandbox
generations, re-download a dataset that has changed upstream.

## Net effect

Every layer that reproducibility needs — what ran, on what code, in what
environment, with what seed, on what data, producing which metrics — is
either uncaptured, captured-then-deleted, or captured-but-unjoined. The
review gates and the claim/experiment/resource model give the *workflow*
discipline, but the chain of custody from command to reported number does not
exist. Until it does, "reproducible" describes the project's intent, not its
guarantee.
