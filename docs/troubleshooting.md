# Worker troubleshooting

MangaSensei keeps browser/API error responses and persisted job details intentionally sanitized. When a page-analysis job fails inside the worker, use the worker process log as the operator-facing diagnostic channel:

```sh
docker compose logs worker
```

Pipeline failures are emitted as a single `worker_pipeline_failed` event. The event contains only operational metadata designed for correlation and source-location diagnosis:

- `stage`: the pipeline operation that was active, such as `load_image`, `ocr`, `persist_ocr`, `linguistics`, `persist_linguistics`, `gemini`, or a persistence step;
- `job_id`, `attempt_no`, and `fencing_token`: database/queue correlation values, not page capability tokens;
- `error_code`: the same stable public error classification used by the job state;
- `exception_type`: the Python exception class name;
- `traceback`: a bounded chain of exception class names and `filename:line:function` locations.

The diagnostic deliberately does **not** log exception messages, frame locals, source lines, uploaded image bytes, OCR text, linguistic tokens or meanings, Gemini prompts/responses, capability tokens, API keys, database URLs/passwords, or arbitrary request objects. This means the source location is available for debugging without turning operator logs into a copy of user content or credentials.

The public/API contract remains sanitized. A job may still expose a generic code such as `processing_failed` and the generic failure detail while the worker log identifies the internal stage and source location.

Operator logs can still reveal service topology, numeric database identifiers, and implementation locations. Restrict access to them as operational data and do not publish them as user-facing diagnostics.

For a recent failure, a focused view is usually enough:

```sh
docker compose logs --tail=200 worker
```

Search for `worker_pipeline_failed`, then correlate `job_id`, `attempt_no`, and `fencing_token` with the job/attempt records when deeper database inspection is required.
