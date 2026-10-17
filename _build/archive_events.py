#!/usr/bin/env python3
""" Move finished events into past/ (Marek 2026-10-17): the repo's front page lists only upcoming and running events.

`make archive` moves every top-level event that is over, `make archive DRY=1` only lists them. Over = the same rule
as _event_over() in _event_template/_build/generate.py: event_state "after", or 24 h past the event's last day in
its own timezone. A past event keeps every feature - it is still built on every deploy and can be edited in place
(talks.csv, videos, metadata toggles) - and its URL does not change: the root Makefile builds all events side by side
in .build/, so nothing inside an event folder depends on where it lives. """
import datetime
import glob
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def event_over(folder, meta):
    if str(meta.get('event_state') or '').strip() == 'after':
        return True
    if int(folder[:4]) < datetime.date.today().year:      # 2022-2023 folders predate start_time / event_state
        return True
    try:
        start = datetime.datetime.fromisoformat(str(meta.get('start_time') or ''))
    except ValueError:
        return False
    tz = start.tzinfo or datetime.timezone.utc
    days = max(1, int(meta.get('days') or 1))
    ends = datetime.datetime.combine(start.date() + datetime.timedelta(days=days), datetime.time(0, 0), tz)
    return datetime.datetime.now(tz) >= ends + datetime.timedelta(hours=24)


def main():
    dry = '--dry-run' in sys.argv
    over = []
    for meta_path in sorted(glob.glob(os.path.join(ROOT, '20*', 'metadata.yml'))):
        with open(meta_path, encoding='utf-8') as f:
            meta = yaml.safe_load(f) or {}
        folder = os.path.basename(os.path.dirname(meta_path))
        if event_over(folder, meta):
            over.append(folder)
    if not over:
        print('Nothing to archive: every top-level event is upcoming or running.')
        return
    for folder in over:
        if os.path.exists(os.path.join(ROOT, 'past', folder)):
            sys.exit('%s is both at the top and in past/ - move its new files into past/%s first' % (folder, folder))
    for folder in over:
        print(('would move' if dry else 'moving') + ' %s -> past/%s' % (folder, folder))
        if not dry:
            os.makedirs(os.path.join(ROOT, 'past'), exist_ok=True)
            subprocess.run(['git', 'mv', folder, 'past/' + folder], cwd=ROOT, check=True)
    if not dry:
        print('Moved %d event(s). Commit and push; the URLs stay the same.' % len(over))


if __name__ == '__main__':
    main()
