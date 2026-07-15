# Scientific Runtime contract v1

This directory contains the P0 contract for the smallest supported vertical
slice:

```text
registered marmousi_94_288 -> confirmed parameters -> Deepwave FWI -> artifacts
```

The seven public objects are `DatasetRef`, `AlgorithmManifest`, `TaskDraft`,
`PlanGraph`, `ApprovalDecision`, `RunEvent`, and `ArtifactManifest`. Shared
definitions live in `common.schema.json` and are not an eighth public object.
Every public object uses `schema_version: "1.0.0"`, rejects unknown top-level
fields, and exposes only a namespaced `extensions` object for controlled
growth.

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
