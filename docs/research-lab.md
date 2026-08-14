# MangaSensei Research Lab

The Research Lab is a logical subsystem inside the canonical MangaSensei repository. It is not a second repository and it is not a production OCR pipeline.

Its purpose is to let bounded engineering research accumulate durable evidence across separate scheduled ChatGPT runs without repeatedly rediscovering the same state.

## Activation status

**Not ready for unattended scheduling yet.**

GitHub's `issue_comment` event only starts a workflow when the workflow file exists on the default branch. The Research Lab workflow is therefore not exercisable through its real comment bus while it exists only on an unmerged implementation branch. The implementation PR can validate the command parser, experiment runner, evidence files, workflow contract and security boundaries, but the real command -> workflow -> artifact -> result-comment proof is a post-merge gate.

Official platform references:

- [GitHub Actions: events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [GitHub Actions: `GITHUB_TOKEN`](https://docs.github.com/en/actions/concepts/security/github_token)
- [GitHub Actions: workflow token permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
- [GitHub Actions: concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub Actions: workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)
- [GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [ChatGPT Scheduled Tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt)
- [ChatGPT Work and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex)

Scheduled Tasks can use connected apps when the account/workspace permissions allow them, but a Scheduled Task created in a Project cannot read that Project's uploaded files. The durable Research Lab control issue is therefore the scheduler-facing source of research state; Project-only files must never be required to interpret the ledger.

## Branch and promotion architecture

`main` remains the only permanent production trunk.

```text
research/<issue>-<hypothesis>
        |
        | durable evidence supports a production solution
        v
candidate/<issue>-<solution>   (fresh from current main)
        |
        | production gates pass and maintainer approves merge
        v
main
```

A research branch may contain instrumentation, temporary algorithms and experimental architecture. It is disposable after its evidence is durable. It is never promoted wholesale just because an experiment worked.

When research reaches `READY_FOR_CANDIDATE`, refresh current `main`, create a fresh candidate branch, port only the minimal evidence-backed solution, remove research-only instrumentation, add regressions/docs/config and run the ordinary production gates. Do not maintain a permanently diverging `tests` branch.

Research branches can be deleted after the control issue contains enough evidence to reconstruct what ran: baseline SHA, frozen spec identity, result hashes, Actions run/artifact identifiers and the conclusion. Candidate branches follow the normal PR lifecycle.

## Scheduler command bus

The durable bus is GitHub issue [#132](https://github.com/Gyliardson/mangasensei/issues/132).

```text
Scheduled researcher
  -> read control issue and current GitHub state
  -> post one validated command comment
  -> issue_comment: created
  -> Research Lab workflow from default branch
  -> event/repository/issue/actor/baseline validation
  -> duplicate command-id check
  -> allowlisted deterministic plugin
  -> results.json + provenance.json + checksums.sha256
  -> immutable Actions artifact
  -> result comment with run/artifact IDs and SHA-256 digests
  -> later scheduled run reviews evidence
```

The workflow reads untrusted comment text only through `GITHUB_EVENT_PATH`. It does not interpolate the comment body into a shell command.

The checkout uses `persist-credentials: false`. The validation/idempotency and comment-posting steps receive `GITHUB_TOKEN`; the experiment step does not. The workflow grants only `contents: read` and `issues: write`.

GitHub documents that events caused by a workflow's own `GITHUB_TOKEN` generally do not recursively create new workflow runs. The Research Lab still rejects comments from actors other than the explicit allowlist, so workflow-authored checkpoint comments are not command-capable.

## Command protocol v1

A command comment is exactly a sentinel followed by one JSON object:

```text
MANGASENSEI_RESEARCH_COMMAND_V1
{"command_id":"plumbing-proof-20260811-01","experiment_id":"framework-smoke-v1","baseline_sha":"<40-char-current-main-sha>","spec_version":"mangasensei-research-experiment-spec-v1","parameters":{"repeat":3}}
```

The v1 schema is [`scripts/research_lab/schemas/command-v1.schema.json`](../scripts/research_lab/schemas/command-v1.schema.json).

The implementation rejects unknown top-level fields. The command cannot select an executable, shell fragment, local path, URL, git ref, model repository, model revision, corpus path or secret. `experiment_id` resolves through the tracked catalog and then to a compiled symbolic plugin allowlist.

The initial command boundary additionally requires:

- repository `Gyliardson/mangasensei`;
- control issue `#132`;
- issue comment, not PR comment;
- actor `Gyliardson`;
- event action `created`;
- exact full `baseline_sha` equal to the default-branch SHA represented by `GITHUB_SHA` for that run.

A stale command is rejected rather than silently rebased.

## Idempotency and concurrency

Every command has a bounded lowercase `command_id`. Before execution, validation scans the bounded control-issue comment history for prior Research Lab status/result markers with the same ID. If one exists, the command is recognized as a duplicate and is not executed again.

The command job also uses one Research Lab concurrency group with `cancel-in-progress: false`. This prevents two accepted commands from executing the experiment body simultaneously. The scheduler contract still permits at most one new experiment per scheduled iteration and should not intentionally queue parallel dependent experiments.

A failed run is evidence. Do not reuse its `command_id` to make it disappear; analyze the run, record the failure state, then use a new command ID only if a retry is justified by an explicit next step.

## Durable research state

The control issue is the durable ledger. The state vocabulary is:

| State | Meaning |
|---|---|
| `QUEUED` | Valid next experiment has been requested but has not started. |
| `RUNNING` | One bounded experiment is active. |
| `RESULT_AVAILABLE` | Machine-readable evidence exists. |
| `NEEDS_ANALYSIS` | The scheduled researcher must review/recompute important evidence before another dependent experiment. |
| `CONTINUE` | Evidence supports a predeclared next bounded step. |
| `PROMISING` | Evidence is positive but insufficient for production promotion. |
| `BLOCKED` | Missing prerequisite, invalid command, platform/resource failure or unresolved dependency prevents progress. |
| `NO_GO` | Frozen evidence falsifies the current approach or crosses a stop rule. |
| `READY_FOR_CANDIDATE` | Evidence supports a fresh production candidate branch. |
| `COMPLETE` | The research thread has reached its defined decision/stop condition. |

Normally only one experiment is active per research thread. `RUNNING` or `NEEDS_ANALYSIS` blocks another dependent experiment.

## Experiment catalog and frozen contracts

The allowlist is [`scripts/research_lab/catalog.json`](../scripts/research_lab/catalog.json). Each entry freezes:

- experiment ID and spec version;
- research question;
- decision informed;
- hypothesis;
- positive/negative controls;
- frozen input identity and SHA-256;
- allowed parameters;
- scoring definition;
- success gate;
- stop condition;
- maximum cases;
- maximum runtime envelope.

The schema is [`scripts/research_lab/schemas/experiment-spec-v1.schema.json`](../scripts/research_lab/schemas/experiment-spec-v1.schema.json).

Tracked catalog entries are still configuration, not executable paths. `implementation` is a symbolic ID that must exist in the runner's compiled mapping. `fixture_id` is also resolved by a compiled mapping. Adding a new plugin therefore requires a reviewed repository change, not a comment parameter.

## First deterministic experiment

`framework-smoke-v1` is intentionally cheap and synthetic. It hashes a project-authored frozen fixture repeatedly and compares it with a fixed negative-control mutation.

It proves the executor contract only:

- the frozen input checksum matches;
- every bounded repeat produces the same SHA-256;
- the negative control produces a distinct SHA-256;
- the stop count is obeyed;
- result serialization is deterministic;
- provenance and checksums are emitted.

It makes no OCR, model-quality or product claim.

## Evidence bundle

An accepted experiment creates:

- `results.json` — deterministic raw result and explainable counts;
- `provenance.json` — runtime/run identity, exact baseline, catalog/spec/result hashes and timing;
- `checksums.sha256` — SHA-256 values for the two JSON files.

The result contract is [`scripts/research_lab/schemas/result-v1.schema.json`](../scripts/research_lab/schemas/result-v1.schema.json), and provenance is described by [`scripts/research_lab/schemas/provenance-v1.schema.json`](../scripts/research_lab/schemas/provenance-v1.schema.json).

The Actions artifact is retained for 30 days. The result comment is the durable index and contains the exact Actions run ID/attempt, artifact ID/name/URL, the upload action's artifact SHA-256 digest and the individual evidence-file SHA-256 digests. Important conclusions must be copied into a durable control-issue checkpoint before the artifact expires.

Artifacts are evidence containers, not long-term scheduler memory.

## Threat analysis

### Command injection

**Threat:** a comment attempts to execute shell, select an arbitrary path/URL/ref/model or exploit shell interpolation.

**Controls:** exact sentinel, strict top-level keys, allowlisted experiment IDs, experiment-specific parameter allowlists, compiled symbolic implementation/fixture mappings, event-file parsing and no comment-body interpolation into `run` commands.

### Stale or confused-deputy execution

**Threat:** a command prepared against an older production state runs against newer code, or a similar command is posted in a fork/PR/other issue.

**Controls:** exact repository, issue, issue-not-PR, actor and full baseline SHA checks. `issue_comment` supplies the default-branch SHA; the trusted checkout explicitly uses that SHA.

### Token/secrets exposure

**Threat:** an experiment reads a write-capable checkout credential, repository secret or command-carried secret.

**Controls:** minimal workflow permissions, `persist-credentials: false`, token only on GitHub-control steps, no production/model secrets, no arbitrary command fields, no secret-bearing artifacts/log design. The experiment step has no `GITHUB_TOKEN` environment variable.

### Duplicate or concurrent execution

**Threat:** the same experiment command executes twice, creating ambiguous evidence or wasting resources.

**Controls:** durable command IDs, prior-marker scan, one-at-a-time concurrency group and the scheduler's single-experiment rule.

### Artifact substitution or loss

**Threat:** later analysis consumes the wrong/expired artifact.

**Controls:** run ID, artifact ID/name/URL, upload-artifact SHA-256 digest, file-level SHA-256 manifest and durable issue checkpoint. Artifact expiry never erases the conclusion identity.

### Untrusted research code reaching production

**Threat:** a successful research branch accumulates instrumentation/hacks and is merged directly.

**Controls:** research branches are disposable; promotion requires a fresh candidate branch from current `main`, minimal reimplementation and ordinary production gates plus explicit maintainer merge approval.

### Third-party/private data leakage

**Threat:** a cloud experiment uploads Black Jack or other source material without the separately reviewed permission, provenance, visibility and execution decision merely because the data exists in a repository or local test corpus.

**Controls:** GitHub-hosted experiments remain limited to inputs explicitly frozen by the reviewed catalog/implementation. MangaSensei-owned public corpus data and synthetic/project-authored fixtures are eligible only when the experiment contract allows them. The Black Jack corpus is a third-party authorized corpus under Sato Manga Works' published secondary-use terms, not private data; however, that rights clearance does not automatically place it on the Research Lab execution allowlist. Any GitHub-hosted Black Jack experiment still requires an explicit reviewed input/provenance/visibility decision and must preserve all normal command-bus controls.

## Data boundary

The Research Lab preserves MangaSensei's local-first/privacy-first invariants. The initial plugin consumes only a project-authored text fixture.

Future GitHub-hosted experiments may use `assets/public-demo/**` only when the experiment spec explicitly freezes the exact public corpus revision/hashes and the cloud visibility is appropriate. The current rights review also supports GitHub-hosted processing/automated OCR of the official Black Jack data as a reasonable application of Sato Manga Works' broad secondary-use grant; the holder's terms do not contain a cloud-compute-specific clause. This rights conclusion is separate from Research Lab activation: Black Jack remains outside a cloud experiment unless the exact use is added through the normal reviewed allowlist/catalog path.

## CPU model smoke

No model smoke belongs in the foundation proof.

Current GitHub documentation lists standard public `ubuntu-latest` runners as 4 CPU / 16 GB RAM / 14 GB SSD, which is enough to justify researching some very small CPU-only model candidates later, but it does not prove inference feasibility. A model smoke is gated on the real deterministic command-bus proof succeeding first.

If that gate passes, any model smoke must be a separate catalog addition with one synthetic/project-owned image, a pinned upstream revision and consumed-file checksums, reviewed runtime/license, concurrency 1, strict timeout, no arbitrary downloads, and measured time/RSS. It is a feasibility/cost measurement, not a #106 OCR-quality benchmark.

## #106 boundary

The Research Lab does not reopen #106.

Durable #106 findings remain authoritative for that thread: the tested replacement/disagreement/reviewer variants did not justify integration, candidate injection into a VLM is undesirable, and any future VLM work should prefer blinded visual observation followed by deterministic comparison/policy. Do not keep tuning the same small calibration set; larger-model claims require a new frozen/held-out benchmark.

## Post-merge end-to-end proof

After explicit maintainer approval merges the implementation PR:

1. Refresh `main` and record the exact new SHA.
2. Confirm required CI on that exact SHA.
3. Post one valid `framework-smoke-v1` command to #132 using that exact SHA and a unique command ID.
4. Confirm the `Research Lab` run was triggered by the command comment and checked out the default-branch SHA.
5. Confirm one `RUNNING` checkpoint appears.
6. Confirm the run uploads exactly one immutable artifact containing `results.json`, `provenance.json` and `checksums.sha256`.
7. Recompute the file digests from the downloaded artifact and compare them with `checksums.sha256` and the durable result comment.
8. Compare the result comment's `artifact_digest` with the Actions artifact digest.
9. Confirm the final comment is `MANGASENSEI_RESEARCH_RESULT_V1` with `state=NEEDS_ANALYSIS` and exact run/artifact identifiers.
10. Repost the exact same command ID. Confirm it is recognized as a duplicate and no second experiment artifact is produced.
11. Post a malformed/unknown-field command from the allowed actor. Confirm it fails closed and produces no experiment artifact.
12. Post a command with the prior baseline SHA after `main` advances, or otherwise exercise the stale-baseline unit/contract evidence. Confirm it is rejected.

Only after these checks is the scheduler activation gate satisfied.

## Scheduled researcher prompt

Do not activate this prompt until the post-merge proof above succeeds.

```text
You are the scheduled MangaSensei Research Lab researcher.

Canonical repository: Gyliardson/mangasensei.
Control ledger: GitHub issue #132.
GitHub current state is always authoritative.

Perform exactly one bounded research iteration, then stop.

1. Read issue #132 and its recent Research Lab status/result/checkpoint comments.
2. Refresh current main, open PRs/issues and any active Research Lab Actions evidence relevant to the current thread.
3. If a Research Lab command is RUNNING, inspect its current run only. Do not post another command.
4. If evidence is RESULT_AVAILABLE or NEEDS_ANALYSIS, inspect/download the exact artifact identified by the result comment, verify its hashes, recompute important metrics/counts from raw machine-readable output, and write a durable analysis checkpoint before choosing another experiment.
5. If the current thread has a predeclared next step whose prerequisite evidence is satisfied, choose at most that one bounded step. Otherwise select the highest-value research-ready problem from current GitHub state; #93, #99, #100, #101, #106 or newer benchmark findings are candidates, not automatic priorities.
6. Research current primary/official sources when a dependency, runtime, model, GitHub/ChatGPT behavior or other unstable technical fact materially affects the experiment.
7. Freeze the decision, hypothesis, controls, baseline SHA, input/corpus identities and hashes, scoring/normalization, success/failure gates, stop condition, max cases and resource/runtime envelope before requesting an expensive experiment.
8. Use only an experiment already present in the allowlisted Research Lab catalog. If the required experiment type is not implemented, do not invent a command or arbitrary parameter. Record BLOCKED and propose the focused repository change needed to add that plugin safely.
9. For a runnable step, post at most one exact MANGASENSEI_RESEARCH_COMMAND_V1 comment to issue #132. Use a new bounded command_id, the exact current main SHA, the catalog's exact experiment/spec version and only allowlisted parameters.
10. Never use command fields to pass shell, executable paths, URLs, git refs, model repositories, corpus paths, secrets or unreviewed source material.
11. After requesting one experiment, record/update the durable checkpoint state and stop. Do not wait-loop, prompt-tune indefinitely or request another dependent experiment in the same scheduled iteration.

State discipline:
- RUNNING or NEEDS_ANALYSIS blocks another dependent experiment.
- CONTINUE means only the next predeclared bounded step is justified.
- PROMISING is not production approval.
- BLOCKED records a missing prerequisite/platform/resource/implementation dependency.
- NO_GO ends the current hypothesis unless new independent evidence justifies a new thread.
- READY_FOR_CANDIDATE requires frozen evidence; production work must start from a fresh candidate/<issue>-<solution> branch based on current main.
- COMPLETE means the research decision/stop condition is satisfied.

Security/privacy:
- Do not merge to main, tag or release.
- Do not weaken CI/security or modify production behavior silently.
- Do not download arbitrary models/dependencies.
- GitHub-hosted experiments may use only inputs explicitly permitted by the reviewed catalog and current provenance/visibility policy.
- Black Jack is rights-cleared with conditions under Sato Manga Works' terms, but cloud research use still requires a separately reviewed catalog/input/visibility decision; do not infer execution permission merely from repository presence.
- Raw OCR and research evidence remain auditable; repeated identical errors are stability evidence, not correctness.
- Do not tune repeatedly against the same tiny calibration set until it passes.

Promotion:
Research success never means merge the research branch. When evidence reaches READY_FOR_CANDIDATE, freeze the checkpoint, refresh current main, create a fresh candidate branch, port only the minimal evidence-backed production change, remove research-only instrumentation, add regressions/docs/config, run normal gates and open a focused PR. Main merge always requires explicit user approval.
```

## Recommended cadence

Start at **every 4 hours** after the activation gate passes. This is slow enough that normal short Actions runs should have settled before the next scheduled analysis, while still allowing several cumulative research iterations per day. The task must still obey the state machine: if an experiment remains running or evidence remains unanalyzed, the next run inspects only and does not enqueue more work.

For experiments that routinely approach multi-hour duration, reduce the cadence rather than creating parallel dependent experiments. Scheduled Tasks cannot run more frequently than once per hour, but Research Lab safety should be determined by evidence readiness, not by the platform's maximum frequency.

## Autonomy boundary

After activation, the scheduled researcher may inspect GitHub/primary sources, update the ledger, post allowlisted commands, inspect run logs/artifacts and propose/create focused research work when permissions permit.

It must not autonomously merge `main`, release/tag, weaken security/CI, expose secrets, silently change production behavior, repeatedly tune one benchmark, fetch arbitrary models/dependencies or use any source material in cloud experiments outside the reviewed catalog/provenance/visibility boundary. Black Jack rights clearance is not an exception to those execution controls.
