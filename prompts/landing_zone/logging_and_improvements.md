Good reads. Here's the full addendum to drop into the plan:

---

## Logging & Progress — Addendum

### Logging

Use `get_task_logger(__name__)` from `celery.utils.log` — not the standard `logging.getLogger`. The task logger automatically injects `task_id` and `task_name` into every log line, which makes it indispensable when multiple tasks are running concurrently.

```python
# tasks.py
from celery.utils.log import get_task_logger
logger = get_task_logger(__name__)
```

Set up a custom `TaskFormatter` via the `after_setup_task_logger` signal. You must use `celery.app.log.TaskFormatter` instead of `logging.Formatter` to get access to `task_id` and `task_name` — it injects them by calling `celery._state.get_current_task` at runtime.

Wire this up in `celery.py` (your Celery app config), not in `tasks.py`:

```python
# celery.py
from celery.signals import after_setup_task_logger
from celery.app.log import TaskFormatter

@after_setup_task_logger.connect
def setup_task_logger(logger, **kwargs):
    for handler in logger.handlers:
        handler.setFormatter(TaskFormatter(
            "%(asctime)s [%(levelname)s] %(task_name)s[%(task_id)s] "
            "upload=%(upload_id)s step=%(step)s — %(message)s"
        ))
```

The `upload_id` and `step` fields are not automatic — pass them as `extra=` on every log call inside the tasks:

```python
log_ctx = {"upload_id": upload_id, "step": "parse"}

logger.info("Starting parse", extra=log_ctx)
logger.info("Strategy selected: %s", strategy.__class__.__name__, extra=log_ctx)
logger.warning("Retrying: %s", str(exc), extra=log_ctx)
logger.error("Exhausted retries: %s", str(exc), extra=log_ctx)
```

This gives you log lines like:

```
2026-05-23 14:02:11 [INFO]  landing.tasks.parse_file[a3f1...] upload=42 step=parse — Starting parse
2026-05-23 14:02:11 [INFO]  landing.tasks.parse_file[a3f1...] upload=42 step=parse — Strategy selected: CsvConversionStrategy
2026-05-23 14:02:14 [ERROR] landing.tasks.parse_file[a3f1...] upload=42 step=parse — Exhausted retries: EmptyDataError(...)
```

**Log at these points — no more, no less:**

| Point | Level | Message |
|---|---|---|
| Task starts | `INFO` | `"Starting {step}"` |
| Strategy selected | `INFO` | `"Strategy: {cls}"` |
| File written / moved | `INFO` | `"Staging written"` / `"Moved to final path"` |
| Retrying | `WARNING` | `"Retrying (attempt {n}): {exc}"` |
| Retries exhausted | `ERROR` | `"Failed permanently: {exc}"` |
| Non-retryable exception | `ERROR` | `"Non-retryable: {exc_type} — investigate"` |

Don't log inside `record_failure` on `PipelineTask` — let each task log at its own call site with its own `log_ctx`. `record_failure` is a DB write helper, not a logging helper.

---

### Progress (`celery-progress`)

`ProgressRecorder` uses Celery's result backend to store current/total progress and a description string. The frontend polls a URL that reads this state via `AsyncResult(task_id)` and returns it as JSON.

Progress lives inside `parse_file` only (as established in the plan). `store_parquet` is a file move — milliseconds, no progress bar needed. The frontend infers pipeline-level state from `LandingUpload.status`.

```python
from celery_progress.backend import ProgressRecorder

@shared_task(bind=True, base=PipelineTask, ...)
def parse_file(self, upload_id: int) -> int:
    recorder = ProgressRecorder(self)

    recorder.set_progress(0, 3, description="Uploading")
    # ... create LandingZoneTask row, set running ...

    recorder.set_progress(1, 3, description="Parsing the file")
    df = strategy.parse(source_path)

    recorder.set_progress(2, 3, description="Converting to Parquet")
    # converter writes staging parquet

    recorder.set_progress(3, 3, description="Done")
    # ... set task_row succeeded ...
    return upload_id
```

The descriptions — `"Uploading"`, `"Parsing the file"`, `"Converting to Parquet"`, `"Done"` — are the exact strings. The frontend receives them verbatim from the progress endpoint and renders them as status text beneath whatever indicator it uses (spinner, step list, etc). The agent should not alter these strings.

**Frontend polling** — `celery-progress` ships a JS helper that handles polling automatically. Wire it to `parse_file`'s task ID, which is returned by `dispatch_pipeline` and stored on `LandingUpload` via a new field:

```python
# Add to LandingUpload
parse_task_id = models.CharField(max_length=255, blank=True, default="")
```

```python
# In dispatch_pipeline
def dispatch_pipeline(upload_id: int):
    LandingUpload.objects.filter(id=upload_id).update(
        status=LandingUpload.Status.PROCESSING
    )
    result = chain(
        parse_file.s(upload_id),
        store_parquet.s(upload_id)
    ).delay()
    # result.id is parse_file's task ID — the one celery-progress polls
    LandingUpload.objects.filter(id=upload_id).update(
        parse_task_id=result.id
    )
```

The status endpoint already returns `LandingUpload` fields — include `parse_task_id` in its response so the frontend knows which task ID to hand to the `celery-progress` JS helper.

---

### Agent notes

Add to `AGENTS.md`:

```markdown
## Logging

Use get_task_logger(__name__) from celery.utils.log in tasks.py.
TaskFormatter is configured via after_setup_task_logger signal in celery.py.
Every log call inside a task must include extra={"upload_id": ..., "step": ...}.
Do not log inside PipelineTask.record_failure — log at the task call site.

## Progress descriptions

The exact strings for ProgressRecorder descriptions are:
  — "Uploading"
  — "Parsing the file"
  — "Converting to Parquet"
  — "Done"
Do not change these strings. The frontend renders them verbatim.

## parse_task_id

LandingUpload.parse_task_id stores the Celery task ID of parse_file.
It is set by dispatch_pipeline after chain().delay() returns.
It is included in the /api/uploads/{id}/status/ response so the frontend
can hand it to the celery-progress JS helper for progress polling.
```
