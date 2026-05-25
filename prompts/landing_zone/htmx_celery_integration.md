The key insight from this doc is the **polling termination pattern** — instead of `HX-Trigger: done` headers, simply omit `hx-trigger` from the fragment when complete. That's cleaner than our previous recommendation because it requires zero client-side event handling. Adopt this, drop the `HX-Trigger: done` approach.

Also note: HTTP 286 is the htmx-native way to stop polling — the server returns status 286 and htmx halts automatically. Either omit-trigger or 286 works; omit-trigger is more explicit in the HTML, 286 is more semantic. Use **omit-trigger** since the fragment template is easier to reason about.

---

Add this section to `AGENTS.md`:

```markdown
## HTMX Progress Polling — implementation pattern

The progress UI uses HTMX partial polling. No JavaScript. No celery-progress JS helper.
The DB fields (parse_progress_current, parse_progress_total, parse_progress_description)
are the single source of truth the poll view reads. ProgressRecorder writes to the
Celery result backend in parallel — but the htmx poll reads Django DB only.

### Two-template structure

landing/_progress_polling.html   — active state, contains hx-trigger
landing/_progress_done.html      — terminal state, no hx-trigger, no hx-get

The view returns _progress_polling.html while current < total.
When current >= total (or status is COMPLETED / FAILED), return _progress_done.html.
Removing hx-trigger stops the poll — no teardown logic needed on the client.

### The poll view

def upload_progress_fragment(request, upload_id):
    upload = LandingUpload.objects.only(
        "parse_progress_current",
        "parse_progress_total",
        "parse_progress_description",
        "status",
    ).get(id=upload_id, save_master__user=request.user)

    is_terminal = (
        upload.parse_progress_current >= upload.parse_progress_total
        or upload.status in (
            LandingUpload.Status.COMPLETED,
            LandingUpload.Status.FAILED,
        )
    )

    template = (
        "landing/_progress_done.html"
        if is_terminal
        else "landing/_progress_polling.html"
    )
    return render(request, template, {"upload": upload})

### _progress_polling.html (active — htmx keeps polling)

<div id="upload-progress-{{ upload.id }}"
     hx-get="{% url 'landing:upload_progress' upload.id %}"
     hx-trigger="every 600ms"
     hx-swap="outerHTML">
    <p class="upload-status-label">{{ upload.parse_progress_description }}</p>
    <!-- agent: render a progress indicator here matching project aesthetic.
         use upload.parse_progress_current and upload.parse_progress_total.
         no bootstrap. no external CSS. match existing component style. -->
</div>

### _progress_done.html (terminal — no hx-trigger, polling stops)

<div id="upload-progress-{{ upload.id }}">
    {% if upload.status == "completed" %}
        <p class="upload-status-label">Done</p>
        <!-- agent: render a success state matching project aesthetic -->
    {% else %}
        <p class="upload-status-label upload-status-label--error">
            {{ upload.parse_progress_description }}
        </p>
        <!-- agent: render a failure state. description will contain the last
             known step before failure e.g. "Parsing the file" -->
    {% endif %}
</div>

### What parse_file writes at each step

Every recorder.set_progress() call is paired with a DB write.
Both happen together — never one without the other.

Step 0/3: description="Uploading"          current=0
Step 1/3: description="Parsing the file"   current=1
Step 2/3: description="Converting to Parquet" current=2
Step 3/3: description="Done"               current=3

On failure: do NOT update parse_progress_description to the error message.
Leave description at the last known step. The _progress_done.html template
reads upload.status == FAILED to decide whether to render error state.
The error detail lives in LandingZoneTask.error_message, not on the upload.

### Wiring

The initial upload view (202 response) must include the polling partial
in its response — or return the upload.id so the parent template can
render the partial immediately after submission. The poll URL needs
upload.id, which is available as soon as the LandingUpload record is created.

### What NOT to do

- Do not use celery-progress JS helper for the htmx poll. DB fields only.
- Do not use HX-Trigger: done response headers. Omit hx-trigger instead.
- Do not return HTTP 286 — omit-trigger is used for clarity.
- Do not poll /api/progress/{task_id}/. That endpoint is for celery-progress
  JS only (parse_task_id stored on upload). The htmx poll is separate.
- Do not put error_message text into parse_progress_description.
- Do not render progress UI in a full-page response. Partials only.
```
