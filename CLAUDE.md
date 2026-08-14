# LLMday — project overview for Claude Code

## What this is
A static site generator for the LLMday conference series. Each event is a separate folder (e.g. `2026-warsaw-q1/`). Python + Jinja2 renders HTML into a `static/` subfolder per event.

## How to build

Build one event:
```bash
cd 2026-warsaw-q1
make generate
```

Build all events:
```bash
for d in 2026-*/; do (cd "$d" && make generate); done
```

Output lands in `<event>/static/` — that's the deployable folder.

## Folder structure

```
_event_template/        ← master template — changes here propagate to all events
  _build/generate.py    ← the build script
  _templates/           ← Jinja2 HTML templates

_assets/template_v1/    ← shared CSS, JS, images (copied into static/ at build time)
  assets/images/        ← shared images, including llama-gif.gif

sponsors/               ← sponsor logo images (copied into static/ at build time)
sponsorship.yaml        ← pricing tiers definition
home/metadata.yml       ← single source of truth for all events list

2026-warsaw-q1/         ← one event folder (all event folders have identical structure)
  _build/generate.py    ← copy of _event_template/_build/generate.py
  _templates/           ← copy of _event_template/_templates/
  _db/talks.csv         ← speaker/talk data for this event
  _db/sponsors.csv      ← sponsor data for this event (if present)
  metadata.yml          ← event-specific config (city, date, capacity, etc.)
  assets/               ← event-specific images
  static/               ← BUILD OUTPUT (git-ignored)
```

## The golden rule
**Always edit `_event_template/` first, then copy to all event folders.**

After changing `_event_template/_build/generate.py` or any file in `_event_template/_templates/`, propagate with:
```bash
for event in 20*/; do
  cp _event_template/_build/generate.py $event/_build/generate.py
  cp _event_template/_templates/_base.html $event/_templates/_base.html
  cp _event_template/_templates/sponsorship.html $event/_templates/sponsorship.html
done
```

## Key templates
- `_templates/sponsorship.html` — sponsorship page; pricing tiers + expandable stats panel
- `_templates/index.html` — event homepage
- `_templates/_base.html` — shared layout; its og:image/twitter:image use the event's `photo_url` card image from `home/metadata.yml` (computed as `og_image_url` in generate.py, falling back to the first hero photo with a build warning)

## Stats panel (sp-stats-inner)
The "I need more stats" expandable section on the sponsorship page has two kinds of content:
- **Dynamic** (from generate.py): total_attendees, total_speakers, total_events, total_countries, global_top_companies (top 10 speaker orgs across all events), global_sponsors (deduplicated sponsors), timeline_events
- **Static** (hardcoded, derived from attendee CSV analysis): role breakdown, seniority, company size, top attendee companies

## Sister repos
Same structure: `sreday`. Changes to shared templates/logic often need to be applied to both.

## Dependencies
```bash
pip install jinja2 markdown pyyaml --break-system-packages
```
If `jinja-markdown` fails to install (network proxy), a local stub exists at `_build/jinja_markdown.py` — copy it alongside `generate.py` before running the build.
