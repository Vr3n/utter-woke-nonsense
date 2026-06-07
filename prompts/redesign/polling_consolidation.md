# Polling Consolidation & SSE Removal — Execution Plan

## Problem

1. **Fake SSE blocks WSGI workers** — `StreamingHttpResponse` with `while True: sleep(0.5)` ties up a worker per browser tab.
2. **Three parallel 2s polls during pipeline execution** — upload, bronze, silver each poll independently reading the same pipeline state.
3. **DatasetEvent is dead code** — 15 write sites (in tasks), zero consumers on the frontend.

## Design

### Shared data layer

```
get_pipeline_status(upload_id)
├── upload (prefetched task_rows)
├── bronze (latest BronzeIngestion)
├── silver (latest SilverIngestion)
├── last_error (most recent failed LandingZoneTask)
└── is_terminal (all three stages terminal → stops polling)
```

Location: `landing/pipeline.py`

### Three poll scenarios

| Page | Mechanism | Interval | Stops when |
|---|---|---|---|
| Upload snapshot | `pipeline_status.html` — single `hx-trigger="every 2s"` | 2s | `is_terminal` |
| Dataset detail | `pipeline_detail.html` — single `hx-trigger="every 2s"` | 2s | `is_terminal` |
| Dataset listing | Per-row `hx-trigger="every 4s"` on non-terminal rows | 4s | `status=="failed"` or `silver_status=="completed"` |

### Listing row movement

Non-terminal rows poll themselves every 4s. When a row reaches a terminal state, the poll response fires `HX-Trigger: datasets-changed`. The `#listing-sections` container re-fetches from `dataset_list_sections`, rows re-sort into correct categories. Guard: `throttle:2s` prevents rapid re-renders.

## Phase order

| Phase | What |
|---|---|
| 1 | Shared `get_pipeline_status` + snapshot consolidated poll |
| 2 | Detail consolidated poll |
| 3 | Listing per-row polling |
| 4 | Remove DatasetEvent |
| 5 | Remove SSE views/templates/JS |
| 6 | Remove old poll views/templates |
| 7 | Tests |

## File changes by phase

### Phase 1

| Action | Details |
|---|---|
| CREATE `landing/pipeline.py` | `get_pipeline_status(upload_id)` — shared data fetcher |
| CREATE `landing/partials/pipeline_status.html` | One `hx-trigger="every 2s"`, omitted when `is_terminal` |
| CREATE `pipeline_status_poll` (landing/views.py) | Calls `get_pipeline_status()`, renders `pipeline_status.html`, sets `HX-Trigger: datasource-uploaded` on completion |
| ADD URL | `hx/<slug:save_slug>/<str:snapshot_type>/pipeline-status/<int:upload_id>/` |
| EDIT `upload_snapshot` | Return `pipeline_status.html` instead of `upload_status.html` |

### Phase 2

| Action | Details |
|---|---|
| CREATE `datasets/partials/pipeline_detail.html` | Graph + bronze + silver + retry button. One `hx-trigger="every 2s"`. |
| CREATE `dataset_pipeline_poll` (datasets/views.py) | Calls `get_pipeline_status()`, builds graph context, renders `pipeline_detail.html` |
| ADD URL | `hx/datasets/<slug:save_slug>/pipeline-status/<int:upload_id>/` |
| EDIT `datasets/detail.html` | Remove SSE wrapper. Stats panel outside poll container. |

### Phase 3

| Action | Details |
|---|---|
| CREATE `datasets/partials/listing_sections.html` | 4 sections with `hx-trigger="load, datasets-changed from:body throttle:2s"` |
| CREATE `dataset_list_sections` (datasets/views.py) | Extracts section-building logic into `_get_listing_sections(save)` helper |
| ADD URL | `hx/datasets/sections/<slug:save_slug>/` |
| EDIT `listing_row.html` | Wrap in `<div>` with conditional `hx-trigger="every 4s"` for non-terminal rows |
| EDIT `listing_row_partial` | Add `HX-Trigger: datasets-changed` for terminal rows |
| EDIT `datasets/list.html` | Replace inline sections with include of `listing_sections.html`. Remove SSE. |

### Phase 4

| Action | Details |
|---|---|
| DELETE `DatasetEvent` model class | dataset/models.py |
| RUN `makemigrations` | `0002_remove_datasetevent` |
| REMOVE event writes | landing/tasks.py (8), bronze/tasks.py (4), silver/tasks.py (4) |

### Phase 5

| Action | Details |
|---|---|
| REMOVE SSE views | `_event_generator`, `event_stream`, `listing_event_stream` |
| REMOVE SSE URLs | 2 routes in datasets/urls.py |
| DELETE `static/js/htmx-sse.js` | Entire file |
| EDIT `base.html` | Remove script reference |

### Phase 6

| Action | Details |
|---|---|
| DELETE templates | `upload_status.html`, `progress_fragment.html`, `bronze_status.html`, `silver_status.html` |
| DELETE views | `upload_status_poll`, `upload_progress_fragment`, `bronze_status_poll`, `silver_status_poll` |
| DELETE URLs | Corresponding routes in landing/bronze/silver urls.py. Keep dispatch endpoints. |

### Phase 7

| Action | Details |
|---|---|
| REMOVE event/SSE tests | ~680 lines from datasets/tests.py |
| ADD tests | `get_pipeline_status`, `pipeline_status_poll`, `dataset_pipeline_poll`, `dataset_list_sections`, listing row HX-Trigger |
| UPDATE tests | landing/bronze/silver test references to removed views |
