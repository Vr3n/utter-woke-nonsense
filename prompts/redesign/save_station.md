# Save Station — Landing Page Redesign

## Overview

Redesign the landing page (`/`) from a static list+form into an interactive **Save Station** using HTMX and vanilla JS. Saves are grouped by FM version, filterable via live search, and created through a modal overlay — all without full-page reloads.

---

## Aesthetic Direction

Lean harder into the existing **dark terminal / cyberpunk** aesthetic with subtle atmospheric depth:

| Element            | Detail                                                                                |
| ------------------ | ------------------------------------------------------------------------------------- |
| **Background**     | Keep `#050505` — add SVG noise texture at 3% opacity via `body::after`                |
| **Scan lines**     | Ultra-subtle repeating horizontal gradient via `body::before` (2px lines at 8% black) |
| **Card entrance**  | Staggered `fade-slide-up` animation (0.4s ease-out, delays incremented by row/column) |
| **Modal backdrop** | `bg-black/70` with `backdrop-blur-sm`                                                 |
| **Color palette**  | Unchanged: violet primary, cyan info, pink highlight, zinc for chrome                 |
| **Typography**     | Unchanged: uppercase, `tracking-[0.2em]` / `tracking-widest` throughout               |

---

## Layout: Version-Grouped Responsive Grid

```
┌──────────────────────────────────────────────────────────┐
│ [VR3N]                          v1.0.0 | TERMINAL MODE   │
├──────────────────────────────────────────────────────────┤
│ SAVE STATION                           ┌────────────────┐│
│                                         │  + Create Save ││
│ ┌─────────────────────────────────────┐ └────────────────┘│
│ │ 🔍  filter saves...                │ ← hx-get on keyup │
│ ├─────────────────────────────────────┤                   │
│ │  ═══ FM26 ═══                       │                   │
│ │  ┌─────────────┐ ┌─────────────┐   │                   │
│ │  │ malaga_rtg  │ │ lyon_2024   │   │ ← 2-col grid     │
│ │  │ Malaga CF   │ │ Lyon        │   │   (md:grid-cols-2)│
│ │  │ Since 07/24 │ │ Since 08/24 │   │                   │
│ │  └─────────────┘ └─────────────┘   │                   │
│ │                                     │                   │
│ │  ═══ FM24 ═══                       │                   │
│ │  ┌─────────────┐                    │                   │
│ │  │ arsenal_24  │                    │                   │
│ │  │ Arsenal     │                    │                   │
│ │  │ Since 01/25 │                    │                   │
│ │  └─────────────┘                    │                   │
│ └─────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────┘

             ↓ Click "Create Save" → modal loads via HTMX
```

**Layout rules:**

- Saves grouped by `game_version` (e.g. FM26 section, FM24 section)
- Version sections ordered most-recent-first (descending by version name)
- Each section has a divider header with the version name
- Save cards in a responsive grid: `grid-cols-1 md:grid-cols-2`
- Mobile: single column stacking

---

## HTMX Interactions

| #   | Trigger                                     | Element          | Target | Endpoint                       | Behavior                                                                      |
| --- | ------------------------------------------- | ---------------- | ------ | ------------------------------ | ----------------------------------------------------------------------------- |
| 1   | Page load                                   | `#save-list`     | itself | `GET /partials/saves/`         | Full save list, version-grouped                                               |
| 2   | `keyup changed delay:200ms` on search input | `#save-list`     | itself | `GET /partials/saves/?q=`      | Filtered list, triggers `htmx:afterSettle` for animation                      |
| 3   | Click "Create Save" button                  | `#modal-content` | itself | `GET /partials/saves/create/`  | Loads form into modal, `afterSwap` → `openModal()`                            |
| 4   | Form submit                                 | `#modal-content` | itself | `POST /partials/saves/create/` | 422 → errors swap in-place. 204 → modal closes, toast appears, list refreshes |
| 5   | Custom event `save-created` on document     | `#save-list`     | itself | `GET /partials/saves/`         | Re-fetches full list (new save appears, grouped correctly)                    |

---

## Modal Pattern (adapted from CRM codebase)

Static modal shell in `base.html` (not per-page), content loaded dynamically via HTMX:

```
┌──────────────────────────────────────────────────────────┐
│  ░░░░░░░░░░░  MODAL OVERLAY  ░░░░░░░░░░░░░░░░░░░░░░░░░  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  CREATE SAVE                                 [×]   │  │
│  ├────────────────────────────────────────────────────┤  │
│  │  Save Name    [____________________________]       │  │
│  │  Start Date   [____________________________]       │  │
│  │  Version      [FM24                        ▼]      │  │
│  │  Description  [____________________________]       │  │
│  │  ─────────────────────────────────────             │  │
│  │  TEAM SELECTION                                    │  │
│  │  ○ Select existing  ● Create new                   │  │
│  │  [____________________________]                    │  │
│  │                           ┌──────────┐             │  │
│  │                           │  Create  │             │  │
│  │                           └──────────┘             │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**Modal behavior (vanilla JS):**

- `openModal()` — removes `.hidden` class from `#modal-overlay`
- `closeModal()` — adds `.hidden` + clears `#modal-content` innerHTML
- Click outside (on overlay backdrop) → close
- ESC key → close
- `htmx:beforeSwap` → allow 422/400 to swap (validation errors); 204 on `#modal-content` → close modal + suppress swap
- `htmx:afterSwap` on `#modal-content` → `openModal()`
- Clear content on close (manual cleanup in closeModal)

---

## Toast Notification Pattern (replaces SweetAlert2)

Vanilla JS toast, no dependencies:

```javascript
function showToast(message, level = "info") {
  const colors = {
    success: "border-cyan-800 bg-cyan-950/50 text-cyan-300",
    error: "border-red-800 bg-red-950/50 text-red-300",
    info: "border-zinc-700 bg-zinc-900 text-zinc-300",
  };
  const toast = document.createElement("div");
  toast.className = `fixed top-6 right-6 z-[100] border px-6 py-4 text-sm uppercase tracking-[0.2em] shadow-2xl transition-all duration-500 ${colors[level]}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 500);
  }, 3500);
}
```

Position: `fixed top-6 right-6`, auto-dismiss after 3.5s with fade-out.

---

## Model Change

One change: make `game_version` required on `SaveMaster`.

**`apps/core/models.py`:**

```python
game_version = models.ForeignKey(
    FootballManagerVersionMaster,
    on_delete=models.PROTECT,
    related_name="saves",
)
```

`PROTECT` ensures no one accidentally deletes a version that has saves attached to it.

**`apps/core/forms.py`:**

- Set `game_version` field to `required=True`, `empty_label=None`
- Add Tailwind CSS classes to all widget attrs to match `<c-ui.input>` styling

---

## Template Directory Structure (Review Item #2)

Use the module-based structure requested:

```
templates/
├── save/
│   ├── table/
│   │   └── list.html          ← HTMX fragment: version-grouped save card grid
│   ├── form/
│   │   └── create.html        ← HTMX fragment: create save modal form
│   └── fragment/
│       └── created_toast.html ← HTMX fragment: success toast after creation
├── cotton/
│   └── save/
│       └── card.html           ← Cotton component: single save card
│   ├── ui/
│   │   ├── input.html          ← Existing (enhanced with <c-vars>)
│   │   └── button.html         ← Existing
│   └── layout/
│       └── app_shell.html      ← Existing
└── pages/
    └── home.html               ← Full page (restructured)
```

Rationale: This mirrors the existing `templates/cotton/` convention while keeping HTMX partials in a clear module/form/fragment structure under the relevant domain (`save/`).

---

## django-htmx Features Used (Review Item #3)

| Feature                  | Where                                 | How                                                                                                                 |
| ------------------------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `request.htmx` bool      | `save_list_partial` view              | Detect HTMX vs full requests (not strictly needed since these are dedicated partial endpoints, but used for safety) |
| `trigger_client_event()` | `save_create_partial` view on success | Replaces manual `response['HX-Trigger'] = json.dumps(...)`. Sends `save-created` event with message + level payload |
| `HtmxMiddleware`         | Already installed                     | Provides `request.htmx` throughout                                                                                  |
| `{% htmx_script %}`      | Already in base.html                  | Renders vendored htmx 2.x                                                                                           |
| CSRF via `hx-headers`    | Already on `<body>`                   | `hx-headers='{"x-csrftoken": "{{ csrf_token }}"}'`                                                                  |

**Before (manual):**

```python
response['HX-Trigger'] = json.dumps({"save-created": {"message": "ok", "level": "success"}})
```

**After (using django-htmx):**

```python
from django_htmx.http import trigger_client_event
response = trigger_client_event(response, "save-created", {"message": "ok", "level": "success"})
```

---

## django-cotton Components (Review Item #4)

### Enhance `c-ui.input` with `<c-vars>`

Add `type` default and error display support (following [cotton form fields pattern](https://django-cotton.com/docs/form-fields)):

```html
<c-vars type="text" errors />

<input
  type="{{ type }}"
  {{
  attrs
  }}
  class="w-full border px-4 py-3 text-zinc-100 placeholder:text-zinc-600
              outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20
              {% if errors %}border-red-500{% endif %}"
/>

{% if errors %}
<p class="text-red-400 text-xs mt-1">{{ errors.0 }}</p>
{% endif %}
```

### Create `c-save.card` component

Extract save card into a reusable cotton component at `templates/cotton/save/card.html`:

```html
<c-vars save />

<a
  href="{% url 'save_detail' save.slug %}"
  class="group block border border-zinc-800 bg-zinc-950 p-6 transition-all
          hover:border-violet-700 hover:bg-zinc-900 animate-fade-slide-up"
  {{
  attrs
  }}
>
  <div
    class="text-lg font-bold text-violet-400 group-hover:text-violet-300 transition-colors"
  >
    {{ save.name }}
  </div>
  {% with active=save.managed_teams.filter.is_active.first %}
  <div class="mt-2 text-sm text-zinc-400">
    {{ active.team.name|default:"No team set" }}
  </div>
  {% endwith %}
  <div class="mt-1 text-xs text-zinc-600 uppercase tracking-widest">
    Since {{ save.start_date }}
  </div>
</a>
```

This encapsulates the card markup once and is reused for every save in both the initial render and HTMX swaps.

---

## Files to Create

### 1. `static/js/app.js`

Vanilla JS utilities (~80 lines):

| Function                    | Purpose                                                                                                                          |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `showToast(message, level)` | Append animated toast, auto-dismiss after 3.5s                                                                                   |
| `openModal()`               | Show modal overlay                                                                                                               |
| `closeModal()`              | Hide modal overlay + clear content                                                                                               |
| HTMX event listeners        | `htmx:beforeSwap` (422/400 passthrough, 204 modal close), `htmx:afterSwap` (modal open), `htmx:afterSettle` (stagger animations) |
| DOM event listeners         | Overlay click → close, ESC → close                                                                                               |
| Custom event handler        | `save-created` → close modal + toast + trigger `#save-list` refresh                                                              |

### 2. `templates/save/table/list.html`

HTMX fragment. Receives `version_groups` (OrderedDict keyed by version name).

```html
{% for key, group in version_groups.items %}
<div class="mb-10">
  <!-- Version divider header -->
  <div class="flex items-center gap-4 mb-6">
    <span class="text-xs uppercase tracking-[0.3em] text-zinc-600 font-mono"
      >{{ key }}</span
    >
    <span class="flex-1 h-px bg-zinc-800"></span>
  </div>
  <!-- Save cards grid -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    {% for save in group.saves %}
    <c-save.card
      :save="save"
      style="animation-delay: {% widthratio forloop.counter0 1 100 %}ms"
    />
    {% endfor %}
  </div>
</div>
{% empty %}
<div
  class="border border-dashed border-zinc-800 bg-zinc-950/50 p-12 text-center"
>
  <p class="text-zinc-600 uppercase tracking-[0.2em] text-sm">No saves found</p>
</div>
{% endfor %}
```

### 3. `templates/save/form/create.html`

HTMX fragment. The form rendered inside the modal.

Key details:

- Form fields use the stylized widget attrs (added via forms.py)
- Radio toggle: "Select existing" / "Create new team" — toggles visibility of respective field via vanilla JS
- Submit button uses `<c-ui.button>` component
- On errors, Django renders with `status=422` so `beforeSwap` allows the swap

Template skeleton:

```html
<form
  hx-post="/partials/saves/create/"
  hx-target="#modal-content"
  class="space-y-5"
>
  {% csrf_token %} {% if form.non_field_errors %}
  <div
    class="border border-red-800 bg-red-950/50 px-4 py-3 text-red-400 text-sm"
  >
    {{ form.non_field_errors }}
  </div>
  {% endif %}

  <div class="space-y-1">
    <label class="text-sm uppercase tracking-[0.2em] text-zinc-500"
      >Save Name</label
    >
    {{ form.name }} {% if form.name.errors %}
    <p class="text-red-400 text-xs mt-1">{{ form.name.errors.0 }}</p>
    {% endif %}
  </div>

  <div class="grid grid-cols-2 gap-4">
    <div class="space-y-1">
      <label class="text-sm uppercase tracking-[0.2em] text-zinc-500"
        >Start Date</label
      >
      {{ form.start_date }} {% if form.start_date.errors %}
      <p class="text-red-400 text-xs mt-1">{{ form.start_date.errors.0 }}</p>
      {% endif %}
    </div>
    <div class="space-y-1">
      <label class="text-sm uppercase tracking-[0.2em] text-zinc-500"
        >Version</label
      >
      {{ form.game_version }} {% if form.game_version.errors %}
      <p class="text-red-400 text-xs mt-1">{{ form.game_version.errors.0 }}</p>
      {% endif %}
    </div>
  </div>

  <div class="space-y-1">
    <label class="text-sm uppercase tracking-[0.2em] text-zinc-500"
      >Description</label
    >
    {{ form.description }}
  </div>

  <hr class="border-zinc-800" />

  <!-- Team selection: radio toggle (vanilla JS) -->
  <div class="space-y-3" id="team-selection">
    <label class="text-sm uppercase tracking-[0.2em] text-zinc-500">Team</label>
    <div class="flex gap-4 mb-3">
      <label
        class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer"
      >
        <input
          type="radio"
          name="team_mode"
          value="existing"
          checked
          onchange="document.getElementById('existing-team-field').classList.remove('hidden');
                        document.getElementById('new-team-field').classList.add('hidden');"
        />
        Select existing
      </label>
      <label
        class="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer"
      >
        <input
          type="radio"
          name="team_mode"
          value="new"
          onchange="document.getElementById('existing-team-field').classList.add('hidden');
                        document.getElementById('new-team-field').classList.remove('hidden');"
        />
        Create new
      </label>
    </div>
    <div id="existing-team-field">{{ form.team }}</div>
    <div id="new-team-field" class="hidden">{{ form.new_team_name }}</div>
    {% if form.team.errors %}
    <p class="text-red-400 text-xs mt-1">{{ form.team.errors.0 }}</p>
    {% endif %} {% if form.new_team_name.errors %}
    <p class="text-red-400 text-xs mt-1">{{ form.new_team_name.errors.0 }}</p>
    {% endif %}
  </div>

  <c-ui.button type="submit">Create Save</c-ui.button>
</form>
```

### 4. `templates/save/fragment/created_toast.html`

HTMX fragment (returned as the 204 response body — actually empty, since 204 has no content). This template exists for documentation / edge-case rendering but the real payload is the `HX-Trigger` event.

```html
{# Empty: 204 response triggers client-side event. This template exists for
structural clarity. #}
```

### 5. `templates/cotton/save/card.html`

Cotton component for a single save card. Documented above in the django-cotton section.

---

## Files to Modify

### 6. `templates/base.html`

**Add inside `<head>`:**

- Noise grain + scanline CSS (inline `<style>` block)

**Add before closing `</body>`:**

- Modal overlay shell (hidden by default):

```html
<div id="modal-overlay" class="fixed inset-0 z-50 hidden">
  <div class="fixed inset-0 bg-black/70 backdrop-blur-sm"></div>
  <div class="fixed inset-0 flex items-center justify-center p-4">
    <div
      class="relative w-full max-w-lg border border-zinc-800 bg-zinc-950 shadow-2xl"
    >
      <div
        class="flex items-center justify-between border-b border-zinc-800 px-6 py-4"
      >
        <span
          id="modal-title"
          class="text-sm uppercase tracking-[0.2em] text-violet-400 font-bold"
          >CREATE SAVE</span
        >
        <button
          onclick="closeModal()"
          class="text-zinc-600 hover:text-zinc-300 text-xl leading-none"
        >
          &times;
        </button>
      </div>
      <div
        id="modal-content"
        class="p-6"
        hx-swap="innerHTML"
        hx-target="this"
      ></div>
    </div>
  </div>
</div>
```

- `<script src="{% static 'js/app.js' %}"></script>`

(CSRF is already handled via `hx-headers` on `<body>` — no additional config needed.)

### 7. `templates/pages/home.html`

Complete restructure:

```html
{% extends "base.html" %} {% block body %}
<c-layout.app_shell>
  <!-- Brand header row -->
  <header class="mb-8 flex items-center justify-between">
    <div class="text-5xl font-black tracking-widest text-violet-400">
      [VR3N]
    </div>
    <button
      hx-get="/partials/saves/create/"
      hx-target="#modal-content"
      class="border border-zinc-800 bg-zinc-950 px-6 py-3 uppercase tracking-widest text-sm
                   transition-all hover:bg-zinc-900 hover:border-violet-700 active:scale-[0.98]"
    >
      + Create Save
    </button>
  </header>

  <!-- Search bar -->
  <div class="mb-8">
    <input
      type="text"
      placeholder="filter saves..."
      hx-get="/partials/saves/"
      hx-trigger="keyup changed delay:200ms"
      hx-target="#save-list"
      hx-swap="outerHTML"
      class="w-full border border-zinc-800 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600
                  outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20"
    />
  </div>

  <!-- Save list (HTMX fragment container) -->
  <div id="save-list" hx-get="/partials/saves/" hx-trigger="load">
    <div class="text-center py-12 text-zinc-600">Loading saves...</div>
  </div>
</c-layout.app_shell>
{% endblock %}
```

### 8. `apps/core/views.py`

**Modify existing `home` view:**

- Remove POST handling (modal handles creation now)
- Only render the shell page
- The save list is loaded via HTMX `hx-trigger="load"` on `#save-list`

**Add new views:**

```python
from collections import OrderedDict
from django_htmx.http import trigger_client_event

def save_list_partial(request):
    """HTMX endpoint: returns version-grouped save cards."""
    q = request.GET.get("q", "")
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    if q:
        saves = saves.filter(name__icontains=q)

    version_groups = OrderedDict()
    for save in saves:
        key = save.game_version.game_version
        if key not in version_groups:
            version_groups[key] = {"version": save.game_version, "saves": []}
        version_groups[key]["saves"].append(save)
    # Sort by version name descending (most recent first)
    version_groups = OrderedDict(sorted(version_groups.items(), reverse=True))

    return render(request, "save/table/list.html", {"version_groups": version_groups})


def save_create_partial(request):
    """HTMX endpoint: GET returns form, POST processes it."""
    if request.method == "POST":
        form = SaveCreateForm(request.POST)
        if form.is_valid():
            user = request.user if request.user.is_authenticated else None
            save = form.save(user=user)
            response = render(request, "save/fragment/created_toast.html", {"save": save})
            response.status_code = 204
            return trigger_client_event(
                response,
                "save-created",
                {"message": f"Save '{save.name}' created!", "level": "success"},
            )
        return render(request, "save/form/create.html", {"form": form}, status=422)
    form = SaveCreateForm()
    return render(request, "save/form/create.html", {"form": form})
```

Note: `trigger_client_event` replaces `response['HX-Trigger'] = json.dumps(...)` — cleaner and uses the library properly.

**Removed dependency:** No need to import `json` anymore.

### 9. `config/urls.py`

Add two new routes **before** the `/<slug:save_slug>/` catch-all:

```python
path("partials/saves/", save_list_partial, name="save_list_partial"),
path("partials/saves/create/", save_create_partial, name="save_create_partial"),
```

### 10. `static/css/theme.css`

Add:

```css
/* Noise grain overlay */
body::after {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 9999;
  pointer-events: none;
  background: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
  background-repeat: repeat;
  background-size: 256px 256px;
}

/* Scan lines */
body::before {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 9998;
  pointer-events: none;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0, 0, 0, 0.08) 2px,
    rgba(0, 0, 0, 0.08) 4px
  );
}

/* Staggered card entrance */
@keyframes fade-slide-up {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.animate-fade-slide-up {
  animation: fade-slide-up 0.4s ease-out both;
}
```

### 11. `apps/core/forms.py`

- Set `game_version` field to `required=True, empty_label=None`
- Add consistent Tailwind CSS classes to all widget attrs:
  - TextInput, DateInput: `w-full border border-zinc-800 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20`
  - Textarea: same base + `resize-y min-h-[80px]`
  - Select: `w-full border border-zinc-800 bg-zinc-950 px-4 py-3 text-zinc-100 outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20`

### 12. `apps/core/models.py`

Make `game_version` required — remove `null=True, blank=True`, use `on_delete=models.PROTECT`.

---

## Interaction Flow (Complete Walkthrough)

```
1. USER VISITS /
   → GET / → home view renders shell page
   → #save-list has hx-trigger="load" → GET /partials/saves/
   → save_list_partial returns version-grouped HTML (save/table/list.html)
   → htmx:afterSettle → JS applies staggered entrance animations

2. USER TYPES IN SEARCH
   → keyup changed delay:200ms → hx-get="/partials/saves/?q=mal"
   → #save-list outerHTML swaps with filtered results
   → htmx:afterSettle → JS re-applies entrance animations to new cards

3. USER CLICKS "CREATE SAVE"
   → hx-get="/partials/saves/create/" → hx-target="#modal-content"
   → save_create_partial returns form HTML (save/form/create.html)
   → htmx:afterSwap → JS calls openModal()
   → Modal overlay fades in, form visible

4. USER SELECTS "CREATE NEW TEAM" RADIO
   → Vanilla JS toggles visibility: hide select dropdown, show text input

5. USER SUBMITS FORM
   → hx-post="/partials/saves/create/" → hx-target="#modal-content"

   CASE A — INVALID (422):
     → beforeSwap allows 422 through
     → Form HTML swaps in with Django validation errors
     → Modal stays open

   CASE B — VALID (204):
     → Server returns 204 + HX-Trigger via trigger_client_event()
     → beforeSwap catches 204 on #modal-content → closeModal(), suppress swap
     → Custom event listener for "save-created":
       1. showToast("Save 'malaga_rtg' created!", "success")
       2. htmx.trigger("#save-list", "refresh")
     → #save-list reloads via hx-trigger="refresh" → GET /partials/saves/
     → New save appears in correct version section with staggered animation

6. USER CLICKS A SAVE CARD
   → Normal <a href="/malaga_rtg/"> navigation
   → Full page load to save_detail view
```

---

## Summary of All Changes

### Create

| #   | File                                         | Lines (est.) |
| --- | -------------------------------------------- | ------------ |
| 1   | `static/js/app.js`                           | ~80          |
| 2   | `templates/save/table/list.html`             | ~30          |
| 3   | `templates/save/form/create.html`            | ~60          |
| 4   | `templates/save/fragment/created_toast.html` | ~2           |
| 5   | `templates/cotton/save/card.html`            | ~20          |

### Modify

| #   | File                             | Change                                                       |
| --- | -------------------------------- | ------------------------------------------------------------ |
| 6   | `templates/base.html`            | +modal shell, +noise CSS, +JS script                         |
| 7   | `templates/pages/home.html`      | Complete restructure (~35 lines)                             |
| 8   | `apps/core/views.py`             | +2 new views, simplify home view, use `trigger_client_event` |
| 9   | `config/urls.py`                 | +2 routes                                                    |
| 10  | `static/css/theme.css`           | +animations, +noise, +scanlines (~40 lines)                  |
| 11  | `apps/core/forms.py`             | +Tailwind widget attrs, make game_version required           |
| 12  | `apps/core/models.py`            | Make game_version required, use PROTECT                      |
| 13  | `templates/cotton/ui/input.html` | Enhance with `<c-vars>` for type/errors                      |

### Migration

| #   | Change                                                   |
| --- | -------------------------------------------------------- |
| 14  | New migration after model change (game_version required) |
