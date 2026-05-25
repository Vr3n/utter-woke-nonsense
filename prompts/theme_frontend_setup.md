# Recommended Structure

```text
templates/
│
├── base.html
├── pages/
│   └── home.html
│
└── cotton/
    ├── layout/
    │   ├── app_shell.html
    │   ├── page_header.html
    │   └── section.html
    │
    ├── navigation/
    │   └── nav_tab.html
    │
    ├── metrics/
    │   ├── metric_card.html
    │   ├── pipeline_card.html
    │   └── status_badge.html
    │
    ├── table/
    │   ├── table.html
    │   ├── table_header.html
    │   └── table_row.html
    │
    ├── ui/
    │   ├── button.html
    │   ├── input.html
    │   ├── panel.html
    │   ├── progress.html
    │   └── badge.html
    │
    └── typography/
        ├── heading.html
        └── terminal_text.html
```

This structure works EXTREMELY well with:

- Django Cotton
- HTMX
- dashboard architecture
- reusable analytics UI
- your cyber-terminal aesthetic

---

# Theme Philosophy

Your uploaded UI has a very specific feel:

```text
Cyberpunk terminal
+
Football analytics ops center
+
Data warehouse observability
+
Football Manager save terminal
```

The important aesthetic ingredients are:

| Ingredient               | Meaning                      |
| ------------------------ | ---------------------------- |
| Dark matte backgrounds   | terminal feel                |
| Muted borders            | industrial UI                |
| Neon accents             | data system aesthetic        |
| Monospace typography     | terminal/computing           |
| Large spacing            | premium dashboard feel       |
| Thin borders             | observability tooling        |
| Cyan/Purple/Pink accents | medallion/data pipeline vibe |

You should preserve these consistently via components.

---

# Base Theme Tokens

## `static/css/theme.css`

```css
:root {
  --bg: #050505;

  --panel: #101010;
  --panel-soft: #151515;

  --border: #232323;
  --border-soft: #1a1a1a;

  --text: #f5f5f5;
  --muted: #8d8d8d;

  --purple: #b78cff;
  --cyan: #18d7ff;
  --pink: #ff77c8;
  --orange: #ff8f70;

  --success: #65ff9a;
  --danger: #ff6767;
}
```

This becomes your:

```text
single source of truth
```

for the aesthetic.

---

# Base Layout

## `templates/base.html`

```html
{% load static tailwind_tags %}

<!DOCTYPE html>
<html lang="en" class="dark">
  <head>
    <meta charset="UTF-8" />
    <title>{% block title %}VR3N{% endblock %}</title>

    {% tailwind_css %}

    <link rel="stylesheet" href="{% static 'css/theme.css' %}" />
  </head>

  <body class="min-h-screen bg-[#050505] text-zinc-100 antialiased">
    {% block body %} {% endblock %}
  </body>
</html>
```

---

# App Shell Component

## `templates/cotton/layout/app_shell.html`

```html
<div class="mx-auto max-w-[1600px] p-6">{{ slot }}</div>
```

---

# Panel Component

## `templates/cotton/ui/panel.html`

```html
<div
  class="
  border border-zinc-800
  bg-zinc-950/80
  p-6
"
>
  {{ slot }}
</div>
```

This becomes the foundational building block of the whole UI.

---

# Heading Component

## `templates/cotton/typography/heading.html`

```html
<h2
  class="
  mb-8
  text-3xl
  font-black
  uppercase
  tracking-[0.2em]
  text-violet-400
"
>
  {{ text }}
</h2>
```

---

# Navigation Tab

## `templates/cotton/navigation/nav_tab.html`

```html
<button
  class="
    border border-zinc-800
    px-8 py-4
    text-sm uppercase
    tracking-[0.2em]
    transition-all
    {% if active %}
      bg-violet-400 text-black font-bold
    {% else %}
      bg-zinc-900 text-zinc-300 hover:bg-zinc-800
    {% endif %}
  "
>
  {{ text }}
</button>
```

---

# Metric Card

## `templates/cotton/metrics/metric_card.html`

```html
<c-ui.panel>
  <div class="mb-4 uppercase text-zinc-500 tracking-widest text-sm">
    {{ label }}
  </div>

  <div
    class="
    text-5xl
    font-black

    {% if color == 'cyan' %}
      text-cyan-400
    {% elif color == 'pink' %}
      text-pink-400
    {% elif color == 'purple' %}
      text-violet-400
    {% endif %}
  "
  >
    {{ value }}
  </div>
</c-ui.panel>
```

---

# Progress Bar

## `templates/cotton/ui/progress.html`

```html
<div class="h-2 overflow-hidden bg-zinc-900">
  <div
    class="
      h-full

      {% if color == 'pink' %}
        bg-pink-400
      {% elif color == 'cyan' %}
        bg-cyan-400
      {% elif color == 'purple' %}
        bg-violet-400
      {% endif %}
    "
    style="width: {{ value }}%"
  ></div>
</div>
```

---

# Button Component

## `templates/cotton/ui/button.html`

```html
<button
  class="
    border border-zinc-800
    bg-zinc-950
    px-4 py-2

    uppercase
    tracking-widest
    text-sm

    transition-all

    hover:bg-zinc-900
    hover:border-zinc-700

    active:scale-[0.98]
  "
>
  {{ slot }}
</button>
```

---

# Input Component

## `templates/cotton/ui/input.html`

```html
<input
  {{
  attrs
  }}
  class="
    w-full
    border border-zinc-800
    bg-zinc-950

    px-4 py-3

    text-zinc-100
    placeholder:text-zinc-600

    outline-none

    focus:border-cyan-400
    focus:ring-1
    focus:ring-cyan-400/20
  "
/>
```

This maintains the terminal feel.

---

# Table Component

## `templates/cotton/table/table.html`

```html
<div class="overflow-hidden border border-zinc-900">{{ slot }}</div>
```

---

# Table Header

## `templates/cotton/table/table_header.html`

```html
<div
  class="
  grid grid-cols-5
  bg-zinc-900/80
  px-6 py-5

  text-sm
  uppercase
  tracking-widest
  text-zinc-500
"
>
  {{ slot }}
</div>
```

---

# Table Row

## `templates/cotton/table/table_row.html`

```html
<div
  class="
  grid grid-cols-5
  border-b border-white/5
  px-6 py-5

  transition-colors

  hover:bg-white/[0.02]
"
>
  {{ slot }}
</div>
```

---

# Home Page

## `templates/pages/home.html`

```html
{% extends "base.html" %} {% block body %}

<c-layout.app_shell>
  <!-- HEADER -->
  <header class="mb-8 flex items-center justify-between">
    <div class="flex items-center gap-10">
      <div class="text-5xl font-black tracking-widest text-violet-400">
        [VR3N]
      </div>

      <div class="uppercase tracking-[0.2em] text-sm">
        <span class="text-zinc-500"> ACTIVE_SAVE: </span>

        <span> Save_001 </span>
      </div>
    </div>

    <div class="text-sm uppercase tracking-[0.2em] text-zinc-500">
      v1.0.0 | TERMINAL MODE
    </div>
  </header>

  <!-- NAV -->
  <nav class="mb-8 flex gap-4">
    <c-navigation.nav_tab text="Squad" active="true" />

    <c-navigation.nav_tab text="Scouting" />
    <c-navigation.nav_tab text="Matchdays" />
    <c-navigation.nav_tab text="Saves" />
  </nav>

  <!-- METRICS -->
  <section class="grid grid-cols-4 gap-6 mb-8">
    <c-metrics.metric_card label="Total Players" value="23" color="cyan" />

    <c-metrics.metric_card label="Avg Rating" value="7.8" color="pink" />

    <c-metrics.metric_card label="Formation" value="4-3-3" color="purple" />

    <c-metrics.metric_card label="Pipeline Health" value="98.7%" color="cyan" />
  </section>
</c-layout.app_shell>

{% endblock %}
```

---

# Why This Architecture Is Powerful

This setup gives you:

| Benefit              | Result                    |
| -------------------- | ------------------------- |
| Cotton components    | reusable terminal UI      |
| HTMX                 | hypermedia interactions   |
| Tailwind             | utility-first speed       |
| Monospace aesthetic  | FM analytics feel         |
| Componentized panels | scalable dashboards       |
| Theme tokens         | consistent neon cyberpunk |

---

# Why Django-Cotton + HTMX Fits PERFECTLY

This stack is honestly ideal for your project because:

```text
Football Analytics UI
≠
SPA necessity
```

You mainly need:

- reactive fragments
- fast tables
- dashboards
- filters
- modals
- inline updates
- analytics panels

HTMX + Cotton handles this beautifully without React complexity.

---

# Recommended Next Components

You should next build:

```text
- sidebar
- command palette
- modal
- dropdown
- datatable
- stat strip
- player card
- scouting radar
- heatmap wrapper
- async loading skeleton
- htmx toast
- pagination
- query filter bar
```

All using the SAME aesthetic language.
