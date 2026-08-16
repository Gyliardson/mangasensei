# Reading Order v2 qualification on GitHub Actions

Reading Order v2 qualification execution can run entirely on GitHub-hosted infrastructure. A local workstation is not required. The manual [`Reading Order v2 Qualification`](../.github/workflows/reading-order-v2-qualification.yml) workflow is the execution boundary for future, separately authorized qualifications.

The workflow does **not** change the frozen experiment methodology. It transports the already committed runner to an ephemeral GitHub-hosted runner, captures provenance before execution, runs the exact frozen command, and uploads durable artifacts associated with the workflow run.

## Safety boundary

The workflow is `workflow_dispatch` only and has `contents: read` permission. A caller must provide:

- a new qualification identity;
- the exact 40-character execution commit SHA;
- its expected tree SHA;
- the expected frozen corpus manifest SHA-256;
- the expected corpus-design SHA-256;
- an explicit new-run authorization boolean.

The requested execution SHA must be the current canonical `main` SHA when the job validates its checkout. This prevents qualification of an unmerged branch or a stale repository state. If `main` changes after authorization but before execution, the job fails closed and a new authorization decision is required.

The workflow refuses the already observed Stage 1 v1.0.0 execution SHA `78838d21e9657c7b854178b1d2d7c73d56bcbc57`. It must not be used as a replay mechanism after a quality result has been observed.

Before execution it checks out the requested SHA with persisted credentials disabled, verifies the SHA/tree/current-main/corpus identities, requires a clean tracked tree, installs the repository-pinned Python environment with `uv sync --frozen --extra ocr`, validates the design and corpus, and starts with an empty ignored experiment-output directory.

## Provenance

The workflow records the exact command before execution:

```text
uv run python -m scripts.reading_order_v2.run_heldout
```

It captures the GitHub runner class, platform, Python implementation/version, virtual-environment state, uv version, and relevant installed distribution versions from that same `uv run` interpreter. Absolute private interpreter/workspace paths are not serialized into the deterministic evidence bundle.

The evidence packager snapshots the output-affecting Reading Order v2 source, the actual vendored `TextBlock` and helper module used by fixture construction, `pyproject.toml`, and `uv.lock`, with repository-relative paths, byte counts, SHA-256 values, and Git blob identities.

## Artifacts

Every workflow attempt uploads an execution artifact containing any generated raw/summary output, pre-execution provenance, the exact-command record, requested identities, the qualification log when execution starts, and an output SHA-256 inventory. This upload uses `if: always()` so an early validation or harness failure still leaves durable evidence for diagnosis.

A successful run additionally builds and validates the deterministic evidence bundle with [`build_evidence.py`](../scripts/reading_order_v2/build_evidence.py), verifies the evidence checksum contract, writes the deterministic ZIP with the committed evidence helper, and uploads the ZIP plus its SHA-256 as a separate artifact.

Artifacts are retained for 90 days by the workflow. GitHub run/job logs remain the authoritative record of the workflow invocation and exact checkout inputs.

## Execution policy

Adding this workflow does not authorize a new experiment by itself. A new qualification still requires a separately frozen implementation/corpus identity and explicit authorization under the Reading Order v2 methodology. Do not use workflow reruns to seek a different quality result after an outcome has been observed.
