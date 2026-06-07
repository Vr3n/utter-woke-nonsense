# Fix: Silver task errors stuck showing "Connecting to postgres"

## Root cause

`duckdb.ConversionException` (and any DuckDB exception not in the explicit lists) falls through to the generic `except Exception` handler. On early attempts (`retries=0 < max_retries=3`), `record_failure` sets `status=RETRYING`, but Celery never retries because the exception isn't in `autoretry_for`. The `SilverIngestion` stays `RETRYING` permanently, and the frontend renders the `{% else %}` (processing) branch with the last `parse_progress_description`.

The same vulnerability exists in bronze's `ingest_to_bronze`.

## Changes (3 files)

### 1. `apps/core/tasks.py` — `PipelineTask.record_failure`

Add `retryable=True` parameter. Only set RETRYING when `retryable and self.request.retries < self.max_retries`. Otherwise set FAILED.

```python
def record_failure(self, obj, task_row, exc, retryable=True):
    if retryable and self.request.retries < self.max_retries:
        obj.status = PipelineStatus.RETRYING
        task_row.status = TaskStatus.RETRYING
        pipeline_task_retries.labels(task_name=self.name).inc()
    else:
        obj.status = PipelineStatus.FAILED
        task_row.status = TaskStatus.FAILED
        pipeline_task_failures.labels(
            task_name=self.name,
            error_type=type(exc).__name__,
        ).inc()
    obj.error_type = type(exc).__name__
    obj.error_message = str(exc)
    obj.save(update_fields=["status", "error_type", "error_message"])
    task_row.error_type = type(exc).__name__
    task_row.error_message = str(exc)
    task_row.finished_at = timezone.now()
    task_row.save(
        update_fields=["status", "error_type", "error_message", "finished_at"]
    )
```

### 2. `apps/silver/tasks.py` — `transform_to_silver`

**a) Add `duckdb.ConversionException` to the non-retryable tuple** (line ~721):

```python
    except (
        duckdb.CatalogException,
        duckdb.ConstraintException,
        duckdb.InvalidInputException,
        duckdb.NotImplementedException,
        duckdb.ConversionException,
        SchemaDriftError,
    ) as exc:
```

**b) Add `parse_progress_description` to the non-retryable handler's save** (after line ~734):

```python
    ingestion.parse_progress_description = "Failed"
    ingestion.save(
        update_fields=[
            "status", "error_type", "error_message", "parse_progress_description"
        ]
    )
```

(was just `ingestion.save(update_fields=["status", "error_type", "error_message"])`)

**c) Fix the generic `except Exception` handler** (line ~760):

Replace:
```python
    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.warning(
                "Retrying (attempt %d): %s", attempt, exc, extra=log_ctx
            )
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(ingestion, task_row, exc)
```

With:
```python
    except Exception as exc:
        retryable = isinstance(exc, tuple(self.autoretry_for))
        if not retryable:
            logger.error(
                "Non-retryable: %s — investigate", type(exc).__name__,
                extra=log_ctx,
            )
        elif self.request.retries < self.max_retries:
            logger.warning(
                "Retrying (attempt %d): %s", attempt, exc, extra=log_ctx
            )
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(ingestion, task_row, exc, retryable=retryable)
```

### 3. `apps/bronze/tasks.py` — `ingest_to_bronze`

Same three changes as silver:

**a) Add `duckdb.ConversionException`** to the non-retryable tuple (line ~219).

**b) Add `parse_progress_description = "Failed"`** to the non-retryable handler's save fields.

**c) Fix the generic `except Exception` handler** (line ~257) with the same `retryable` check + `record_failure(..., retryable=retryable)` pattern.

## Verification

After applying, run:
```
uv run pytest apps/silver/tests.py apps/bronze/tests.py -x
```
