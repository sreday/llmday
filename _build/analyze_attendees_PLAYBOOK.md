# Attendee Analysis Playbook

This document explains how to update the attendee profile stats on the sponsorship pages.

## Overview

`analyze_attendees.py` reads approved guest exports from Luma, classifies attendees
by role, seniority and company size, scores talk topics from `talks.csv` files across
all event folders, and patches the sponsorship template in place. No data is stored
in the repo.

## When to run this

After each event season, or when you have enough new attendee data to make the
numbers meaningfully different from what is currently shown.

## What you need

Approved guest list exports from Luma, one CSV per event. These stay on your local
machine and are never committed to the repo. The `.gitignore` has `*.csv` as a
safety net.

## How to run it

From the repo root, point the script at your local CSV files:

```bash
python3 _build/analyze_attendees.py \
  /path/to/event1.csv \
  /path/to/event2.csv \
  /path/to/event3.csv
```

On Windows:

```bash
python3 _build/analyze_attendees.py "C:\Users\you\Desktop\event1.csv" "C:\Users\you\Desktop\event2.csv"
```

You can pass as many CSVs as you like. Attendees appearing in multiple files are
deduplicated so each person is counted once.

## What the script produces

- Patches `_event_template/_templates/sponsorship.html` in place with new stats
- Writes `_build/attendee_stats.json` with the full breakdown for reference
- Prints a human-readable summary to stdout including any unclassified job titles

## After running

1. Verify the output looks right in the printed summary
2. Commit the two files the script touched:

```bash
git add _event_template/_templates/sponsorship.html _build/attendee_stats.json
git commit -m "chore: update attendee stats"
git push
```

3. Check one live sponsorship page looks correct (expand "I need more stats")
4. Propagate to all existing event folders:

```bash
# Claude Code prompt:
# Copy _event_template/_templates/sponsorship.html to every 20*/_templates/sponsorship.html
# then git add -A && git commit -m "chore: propagate attendee stats to all events" && git push
```

## How the template patching works

The script finds the static attendee profile block in the sponsorship template using
two sentinel comments. Do not remove or rename these:

```
{# ── ATTENDEE PROFILE (static - update by running _build/analyze_attendees.py) ── #}
...
{# ── SPEAKER COMPANIES (dynamic ...
```

Everything between those two markers gets rewritten on each run.

## Updating the TLDR

The TLDR string at the top of `analyze_attendees.py` is the one-line audience
description shown on the sponsorship page. Update it manually when the audience
composition shifts significantly between seasons. Keep it factual and focused on
what attendees work on, not who holds budget.

## Classifier maintenance

Job titles that don't match any rule end up as "Other". The script prints these at
runtime. If "Other" exceeds 5% of total attendees, add keyword rules to `ROLE_RULES`
in the script to bring it down.

Similarly, well-known companies not in `KNOWN_ENTERPRISE` or `KNOWN_SCALEUP` will
fall through to "Startup". Add them to the appropriate set when you spot them in
the output.

## Topic keywords

Talk topics are scored by matching titles and abstracts against `WORKING_ON_KEYWORDS`.
These are a starting point, not ground truth. Review the printed topic rankings each
season and add new keyword rules when genuinely new themes emerge in the programme.
