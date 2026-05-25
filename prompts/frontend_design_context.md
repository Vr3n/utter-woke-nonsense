# Frontend Design Context

This document provides the design system and aesthetic guidelines for the VR3N Football Analytics platform. AI agents should use this to maintain consistency when adding new components or pages.

---

## Design Philosophy

The aesthetic combines:

- **Cyberpunk terminal** - dark, glowing interfaces
- **Football analytics ops center** - data-heavy, functional dashboards
- **Football Manager save terminal** - retro-gaming nostalgia
- **Data warehouse observability** - industrial, technical feel

---

## Color Palette

### Background Colors

| Token          | Hex       | Usage                 |
| -------------- | --------- | --------------------- |
| `--bg`         | `#050505` | Page background       |
| `--panel`      | `#101010` | Card/panel background |
| `--panel-soft` | `#151515` | Elevated surfaces     |

### Border Colors

| Token           | Hex       | Usage           |
| --------------- | --------- | --------------- |
| `--border`      | `#232323` | Default borders |
| `--border-soft` | `#1a1a1a` | Subtle dividers |

### Text Colors

| Token     | Hex       | Usage                  |
| --------- | --------- | ---------------------- |
| `--text`  | `#f5f5f5` | Primary text           |
| `--muted` | `#8d8d8d` | Secondary text, labels |

### Accent Colors

| Token      | Hex       | Usage                             |
| ---------- | --------- | --------------------------------- |
| `--cyan`   | `#18d7ff` | Primary accent, links, highlights |
| `--purple` | `#b78cff` | Secondary accent, headings        |
| `--pink`   | `#ff77c8` | Tertiary accent, emphasis         |
| `--orange` | `#ff8f70` | Warning, attention                |

### Status Colors

| Token       | Hex       | Usage          |
| ----------- | --------- | -------------- |
| `--success` | `#65ff9a` | Success states |
| `--danger`  | `#ff6767` | Error states   |

**Implementation**: These are defined in `static/css/theme.css` as CSS custom properties.

---

## Typography

- **Primary Font**: System sans-serif (Tailwind default)
- **Monospace Font**: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas` for terminal-style text

### Text Styles

- **Headings**: Uppercase, `tracking-[0.2em]`, font-bold/black
- **Body**: 14-16px, normal weight
- **Labels**: Uppercase, `tracking-widest`, smaller sizes (12-14px)
- **Terminal Text**: Monospace, cyan accent

---

## Component Patterns

### Panel (Base Component)

```html
<div class="border border-zinc-800 bg-zinc-950/80 p-6">
  <!-- content -->
</div>
```

- Used as the foundation for all cards, widgets, and containers

### Buttons

```html
<button
  class="border border-zinc-800 bg-zinc-950 px-4 py-2 uppercase tracking-widest text-sm transition-all hover:bg-zinc-900 hover:border-zinc-700 active:scale-[0.98]"
>
  <!-- text -->
</button>
```

### Inputs

```html
<input
  class="w-full border border-zinc-800 bg-zinc-950 px-4 py-3 text-zinc-100 placeholder:text-zinc-600 outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/20"
/>
```

### Navigation Tabs

```html
<button
  class="border border-zinc-800 px-8 py-4 text-sm uppercase tracking-[0.2em] transition-all
  {% if active %} bg-violet-400 text-black font-bold {% else %} bg-zinc-900 text-zinc-300 hover:bg-zinc-800 {% endif %}"
>
  <!-- text -->
</button>
```

### Metric Cards

```html
<c-ui.panel>
  <div class="mb-4 uppercase text-zinc-500 tracking-widest text-sm">
    {{ label }}
  </div>
  <div class="text-5xl font-black text-cyan-400">{{ value }}</div>
</c-ui.panel>
```

### Progress Bars

```html
<div class="h-2 overflow-hidden bg-zinc-900">
  <div class="h-full bg-cyan-400" style="width: {{ value }}%"></div>
</div>
```

### Tables

```html
<div class="overflow-hidden border border-zinc-900">
  <!-- header -->
  <div
    class="grid grid-cols-5 bg-zinc-900/80 px-6 py-5 text-sm uppercase tracking-widest text-zinc-500"
  ></div>
  <!-- row -->
  <div
    class="grid grid-cols-5 border-b border-white/5 px-6 py-5 hover:bg-white/[0.02]"
  ></div>
</div>
```

### Badges

```html
<span
  class="inline-flex items-center px-3 py-1 rounded text-xs uppercase tracking-widest
  {% if color == 'cyan' %} bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 {% endif %}"
>
  <!-- text -->
</span>
```

---

## Color Mapping for Components

| Component Type   | Color Token | Tailwind Class             |
| ---------------- | ----------- | -------------------------- |
| Primary metric   | `--cyan`    | `text-cyan-400`            |
| Secondary metric | `--pink`    | `text-pink-400`            |
| Tertiary metric  | `--purple`  | `text-violet-400`          |
| Active nav       | `--purple`  | `bg-violet-400 text-black` |
| Headings         | `--purple`  | `text-violet-400`          |
| Terminal text    | `--cyan`    | `text-cyan-400`            |
| Success status   | `--success` | `text-green-400`           |
| Error status     | `--danger`  | `text-red-400`             |

---

## Spacing System

- **Base unit**: 4px (Tailwind default)
- **Component padding**: 16px-24px (p-4 to p-6)
- **Section gaps**: 24px-32px (mb-8)
- **Card gaps**: 24px (gap-6)

---

## File Structure

```
templates/
├── base.html                     # Base template with dark theme
├── pages/
│   └── home.html                 # Home page example
└── cotton/                       # Reusable Cotton components
    ├── layout/
    │   ├── app_shell.html        # Main container
    │   ├── page_header.html
    │   └── section.html
    ├── navigation/
    │   └── nav_tab.html          # Tab buttons
    ├── metrics/
    │   ├── metric_card.html      # Stat display
    │   ├── pipeline_card.html
    │   └── status_badge.html
    ├── ui/
    │   ├── button.html
    │   ├── input.html
    │   ├── panel.html           # Base container
    │   ├── progress.html
    │   └── badge.html
    ├── typography/
    │   ├── heading.html
    │   └── terminal_text.html
    └── table/
        ├── table.html
        ├── table_header.html
        └── table_row.html

static/
├── css/
│   ├── dist/styles.css           # Compiled Tailwind (DO NOT EDIT)
│   └── theme.css                 # Custom properties (EDIT THIS)
```

---

## Using Cotton Components

All components are available as custom tags:

```html
<c-ui.panel>Content</c-ui.panel>
<c-ui.button>Click Me</c-ui.button>
<c-ui.input placeholder="Search..." />
<c-metrics.metric_card label="Players" value="23" color="cyan" />
<c-navigation.nav_tab text="Squad" active="true" />
<c-typography.heading text="Squad Overview" />
<c-table.table>...</c-table.table>
```

---

## Adding New Components

When creating new components:

1. Use the panel pattern as the base container
2. Use uppercase + tracking for labels
3. Use monospace or cyan for data values
4. Use violet for headings
5. Keep borders thin (1px, `--border` color)
6. Maintain dark background hierarchy
7. Test color contrast for accessibility

---

## Tailwind Notes

- Tailwind v4 is configured in standalone mode
- Source: `theme/static_src/src/styles.css`
- Output: `static/css/dist/styles.css`
- Build command: `python manage.py tailwind build`

The `@source` directive in styles.css scans all `.html` and `.py` files for Tailwind classes.
