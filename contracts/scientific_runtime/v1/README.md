# Scientific Runtime contract v1

This directory contains the P0 contract for the smallest supported vertical
slice:

```text
registered marmousi_94_288 -> confirmed parameters -> Deepwave FWI -> artifacts
```

The seven public objects are `DatasetRef`, `AlgorithmManifest`, `TaskDraft`,
`PlanGraph`, `ApprovalDecision`, `RunEvent`, and `ArtifactManifest`. Shared
definitions live in `common.schema.json` and are not an eighth public object.
Every public object rejects unknown top-level fields and exposes only a
namespaced `extensions` object for controlled growth.

The original P0 documents remain valid at `schema_version: "1.0.0"`. A
backward-compatible contract minor, `1.1.0`, is also accepted for
`AlgorithmManifest`, `TaskDraft`, and `PlanGraph` when the fixed FWI parameter
set includes `optimizer` and `learning_rate_milli`. The other four public
objects remain at `1.0.0`. A `1.0.0` FWI draft/plan has exactly the original
four parameters; a `1.1.0` FWI draft/plan has exactly the six parameters, so a
consumer never has to guess which shape was hashed or approved.

P3 adds a dormant `PlanGraph`-only minor, `1.2.0`. It preserves dataset-bound
inputs and additionally permits a node input to name an exact upstream node,
output port, and data type. The source must be a direct dependency and must
match one unambiguous declared output; the whole logical edge remains covered
by `plan_hash`. A separate pure binding step verifies an `ArtifactManifest`
against the source plus the actual artifact byte hash and size, then derives a
canonical binding-document hash. This contract does not change the current
Guided `1.1.0` plan, coerce artifacts into DatasetRefs, persist node state, or
authorize node admission/dispatch. Current ArtifactManifest lineage still
describes DatasetIdentity inputs; downstream artifact-lineage evolution is a
later P3 boundary.

`learning_rate_milli` is an integer fixed-point value (`learning_rate * 1000`),
not a JSON float. For example, Adam learning rate `10` is represented as
`10000`. This preserves the canonical plan-hash rule, whose cross-language
input domain intentionally rejects JSON floats. The browser/API may accept a
strict decimal string for usability, but the persisted Draft/Plan and approval
hash use only the scaled integer. The current
`deepwave.acoustic_fwi@1.3.0` manifest uses this contract minor. Immutable
`1.0.0` and `1.1.0` four-parameter snapshots and the immutable `1.2.0`
six-parameter snapshot remain strictly read-compatible; none is rewritten or
selected for a new Guided dispatch. The current `1.3.0` manifest is the new
submission identity whose declaration is internally consistent: it advertises
only `acoustic_fwi_2d` with `fwi_smoke|fwi_demo`, bounds inversion iterations
to `1..10000` and seed to `0..2147483647`, and applies optimizer-specific
conditional bounds to `learning_rate_milli`. Legacy Worker/MCP `forward`
remains outside the standard Algorithm/Adapter capability and cannot be
selected by a current Guided plan.

The schemas use JSON Schema Draft-07 so the repository's existing
`jsonschema==3.2.0` installation can verify them without adding a dependency.
This choice does not weaken contract versioning: the scientific runtime schema
version is independent from the JSON Schema dialect.

Reference validation, canonical plan hashing, and the deterministic pre-queue
gate are implemented in `scientific_runtime_contracts/validation.py`. They do
not persist state, submit workers, or implement the P1 TaskService.

Run the contract suite from the repository root:

```bash
python3 -m unittest tests.test_scientific_runtime_contracts -v
```

The normative behavior, API draft, Adapter protocol, threat model, migration
rules, and legacy-field audit are documented in
`docs/architecture/SCIENTIFIC_RUNTIME_P0_CONTRACTS.md`.
