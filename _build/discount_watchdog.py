#!/usr/bin/env python3
"""Discount watchdog (Marek 2026-10-09): hourly, for every upcoming event, compares the discount on the live teasers page
with what a build today prints (20% 3+ weeks before the event, 40% closer, days counted from today's UTC date - the same
rule as _tz_pct in generate.py). A mismatch (a stale page, or an event that just crossed the 21-day line) queues one normal
deploy, which redraws only the cards that changed. It never builds or renders anything itself, so it takes seconds and
never slows a deploy.

No loops: a deploy already queued or running is left to land; when a deploy has started since UTC midnight and the page
is still wrong, the build itself is wrong - the job fails (GitHub emails) instead of deploying again.

    python _build/discount_watchdog.py https://sreday.com      # GH_TOKEN + GITHUB_REPOSITORY set: may queue a deploy
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = "static-build.yml"


def expected_pct(start, now):
    # exactly generate.py's rule: the event's date against the build day's UTC date
    days = (start.date() - now.date()).days
    return 20 if days >= 21 else 40


def valid_since(now):
    """When today's value became the right one: UTC midnight (the build counts days by the runner's UTC date)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def live_pcts(url):
    req = urllib.request.Request(url, headers={"User-Agent": "discount-watchdog", "Cache-Control": "no-cache"})
    page = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    return sorted({int(p) for p in re.findall(r">(\d+)% OFF<", page)})


def gh_runs():
    out = subprocess.run(["gh", "run", "list", "-R", os.environ["GITHUB_REPOSITORY"], "-w", WORKFLOW, "-L", "20",
                          "--json", "status,createdAt"], capture_output=True, text=True, check=True).stdout
    return [(r["status"], datetime.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))) for r in json.loads(out)]


def main():
    site = (sys.argv[1] if len(sys.argv) > 1 else "https://" + open(os.path.join(ROOT, "CNAME")).read().strip()).rstrip("/")
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = []   # (event, valid since)
    for meta in sorted(glob.glob(os.path.join(ROOT, "20*", "metadata.yml"))):
        m = re.search(r"^\s*start_time\s*:\s*['\"]?([^'\"\s]+)", open(meta, encoding="utf-8").read(), re.M)
        if not m:
            continue
        try:
            start = datetime.datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        if start.tzinfo is None or start.date() < now.astimezone(start.tzinfo).date():
            continue   # past events are frozen
        event = os.path.basename(os.path.dirname(meta))
        try:
            live = live_pcts("%s/%s/teasers/" % (site, event))
        except Exception as e:
            print("%s: page not readable (%s), skipped" % (event, e))
            continue
        want = expected_pct(start, now)
        if not live:
            print("%s: no discount ball (free event, not on Luma or venue TBC)" % event)
        elif live == [want]:
            print("%s: %d%% ok" % (event, want))
        else:
            print("%s: live %s, expected %d%% - stale" % (event, live, want))
            stale.append((event, valid_since(now)))
    if not stale:
        return 0
    if not os.environ.get("GITHUB_REPOSITORY"):
        print("not in CI: nothing queued")
        return 1
    runs = gh_runs()
    if any(s != "completed" for s, _ in runs):
        print("a deploy is already queued or running - it lands the fix")
        return 0
    newest = max((c for _, c in runs), default=None)
    broken = [e for e, since in stale if newest and newest >= since]
    if broken:
        print("::error::deployed since UTC midnight but still wrong: %s - the build prints the wrong discount" % ", ".join(broken))
        return 1
    subprocess.run(["gh", "workflow", "run", WORKFLOW, "-R", os.environ["GITHUB_REPOSITORY"], "--ref", "main"], check=True)
    print("queued a deploy for: %s" % ", ".join(e for e, _ in stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
