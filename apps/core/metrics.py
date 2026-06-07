from prometheus_client import Counter, Histogram

pipeline_task_duration = Histogram(
    "pipeline_task_duration_seconds",
    "Task duration in seconds",
    ["task_name", "snapshot_type", "status"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
)

pipeline_task_retries = Counter(
    "pipeline_task_retries_total",
    "Total number of task retries",
    ["task_name"],
)

pipeline_task_failures = Counter(
    "pipeline_task_failures_total",
    "Total number of task failures",
    ["task_name", "error_type"],
)

pipeline_rows_processed = Counter(
    "pipeline_rows_processed_total",
    "Total rows ingested into a layer",
    ["layer", "snapshot_type"],
)

pipeline_rows_dropped = Counter(
    "pipeline_rows_dropped_total",
    "Total rows dropped during transformation",
    ["layer", "reason"],
)

pipeline_null_warnings = Counter(
    "pipeline_null_warnings_total",
    "Number of uploads where an expected column is NULL for every row",
    ["layer", "snapshot_type", "column_name"],
)
