#!/usr/bin/env python3
"""Red flag check: "Did you just nuke SREday London 2026 Q3?" (Marek 2026-09-21)

Every talks.csv change is a GitHub web upload, one event per commit. When the wrong file lands in the wrong
event folder, the whole lineup of that event is swapped by a single push (2026-09-21: SREday London Q3 received
the San Francisco Q4 file three days before the event). This module spots that shape and names the likely source.

Byte-identical in sreday, llmday, platformday and PEC; stdlib only (yaml is optional, for event names).
  * import, every home build (home/_build/generate.py, STATUS block): active_flags() = the flags that are still
    true. They become the red bar on top of /status/, the first error of the event's Data checks, and
    /status/redflags.json. The "Red flag alert" Gmail script (llmday/_build/redflag-alert.gs) reads that json every
    10 minutes and emails "Did you just nuke <event>?" once per flag. No secrets, no webhook, no workflow step.
  * CLI, by hand, prints only:
        python _build/redflag.py                      flags that are true right now (all 2025+ folders)
        python _build/redflag.py --range A..B         flags raised by those commits, e.g. 3a98fc0b5~1..3a98fc0b5
It never fails a build: every problem degrades to a log line and exit code 0.

Rules (CSV-parsed snapshots from git, never line diffs: abstracts contain newlines; confirmed/keynote rows only):
  swap     one commit drops more than 5 speakers AND over 40% of a lineup of 6+ (the /status/ speaker log's
           _REMOVED_MASS), and 40%+ of the new lineup is new. Nothing new coming in = "mass removal".
  source   the new lineup is compared with every other 2025+ event folder: identical file = "exact copy of",
           60%+ shared names/titles = "looks like it has the lineup of".
  twin     two events share 80%+ of their lineups; catches a wrong file uploaded into a brand-new folder.
  restore  a swap whose result matches one of the file's own older snapshots is the FIX, never a flag.
  path     a talks.csv uploaded outside <event>/_db/ (the site ignores it).
Not flags: a new event folder cloned from an old one (metadata.yml arrives in the same commit), and emptying such
a cloned lineup afterwards.
An intentional big change: add the short commit id to `redflag_ack:` in home/metadata.yml to clear the red bar.
"""
import csv
import datetime
import difflib
import io
import json
import os
import re
import subprocess
import sys

try:
    import yaml
except ImportError:                     # names fall back to the folder name
    yaml = None

SAME = 0.85              # difflib ratio at/above which two names are one speaker (same as _ADDED_SAME on /status/)
MASS = (5, 0.4)          # more than 5 gone AND over 40% of the lineup (same as _REMOVED_MASS on /status/)
MIN_LINEUP = 6           # smaller lineups change wholesale all the time
ADDED_SHARE = 0.4        # share of the new lineup that must be new for a "swap" (else: mass removal)
LOOKS_LIKE = 0.6         # share of the new lineup found in another event = "has the lineup of"
TWIN = 0.8               # two events sharing this much, both ways = twins
RESTORE = 0.8            # the new lineup matching an older snapshot of the same file this well = the fix
RESTORE_DEPTH = 6        # older snapshots of the file to compare with
ACTIVE_COMMITS = 8       # /status/ bar: how far back a still-unfixed swap is looked for
ACTIVE_DAYS = 45            # = the history window CI fetches for /status/
EVENT_RE = re.compile(r"^20(2[5-9]|[3-9]\d)[\w.-]*$")       # never the frozen 2022-2024 folders
TALKS_RE = re.compile(r"^([^/]+)/_db/talks\.csv$")


# ---- git + snapshots ----------------------------------------------------------

def git(root, *args):
    """stdout of a git command run in <root>, None when it fails."""
    try:
        out = subprocess.run(["git", "-C", root, "-c", "core.quotepath=off"] + list(args), capture_output=True)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", errors="replace")


def norm(s):
    return " ".join(str(s or "").casefold().split())


def parse(text):
    """{'rows': {normalized name: {name, title, tkey}}, 'titles': set, 'raw': text} of confirmed/keynote rows."""
    text = (text or "").replace("\x00", "")
    rows = {}
    try:
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("name") or "").strip()
            status = (row.get("status") or "").lower()
            if not name or name.startswith("_") or not ("confirmed" in status or "keynote" in status):
                continue
            title = (row.get("title") or "").strip()
            tkey = re.sub(r"\W+", "", title.lower())
            rows[norm(name)] = {"name": name, "title": title, "tkey": tkey if len(tkey) >= 12 else ""}
    except csv.Error:
        pass
    return {"rows": rows, "titles": set(r["tkey"] for r in rows.values() if r["tkey"]),
            "raw": text.replace("\r\n", "\n").strip()}


def snapshot(root, commit, path):
    """Lineup of <path> at <commit> (None = working tree). None when the commit cannot be read (shallow clone),
    an empty lineup when the file does not exist there."""
    if commit is None:
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace", newline="") as f:
                return parse(f.read())
        except OSError:
            return parse("")
    if git(root, "cat-file", "-e", commit + "^{commit}") is None:
        return None
    return parse(git(root, "show", "%s:%s" % (commit, path)) or "")


def same(a, b):
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= SAME


def churn(before, after):
    """(gone, added) name keys between two snapshots of one file; a respelled name is neither."""
    b, a = before["rows"], after["rows"]
    gone = [k for k in b if k not in a]
    added = [k for k in a if k not in b]
    gone2 = [k for k in gone if not any(same(k, x) for x in added)]
    added2 = [k for k in added if not any(same(k, x) for x in gone)]
    return gone2, added2


def overlap(a, b):
    """Share of lineup <a> found in <b> by exact name or exact talk title (a copied csv is verbatim)."""
    if not a["rows"]:
        return 0.0
    hit = sum(1 for k, r in a["rows"].items() if k in b["rows"] or (r["tkey"] and r["tkey"] in b["titles"]))
    return hit / float(len(a["rows"]))


def overlap_fuzzy(a, b):
    """Like overlap(), names compared fuzzily too: for two snapshots of the SAME file (typo fixes in between)."""
    if not a["rows"]:
        return 0.0
    hit = 0
    for k, r in a["rows"].items():
        if k in b["rows"] or (r["tkey"] and r["tkey"] in b["titles"]) or any(same(k, x) for x in b["rows"]):
            hit += 1
    return hit / float(len(a["rows"]))


# ---- repo facts ------------------------------------------------------------------

def event_folders(root):
    out = []
    for d in sorted(os.listdir(root)):
        if EVENT_RE.match(d) and os.path.isfile(os.path.join(root, d, "_db", "talks.csv")):
            out.append(d)
    return out


_facts_cache = {}


def facts(root):
    """brand key, site root, GitHub slug, event names and acknowledged commits of the repo at <root>."""
    root = os.path.abspath(root)
    if root in _facts_cache:
        return _facts_cache[root]
    meta = {}
    if yaml is not None:
        try:
            with open(os.path.join(root, "home", "metadata.yml"), encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        except Exception:
            meta = {}
    names = {}
    for key in ("events", "events_past"):
        for ev in (meta.get(key) or []):
            if isinstance(ev, dict):
                folder = str(ev.get("url") or "").strip("./").rstrip("/")
                if folder and ev.get("name"):
                    names[folder] = str(ev["name"])
    slug = os.environ.get("GITHUB_REPOSITORY") or ""
    if not slug:
        m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", git(root, "remote", "get-url", "origin") or "")
        slug = m.group(1) if m else "sreday/" + os.path.basename(root)
    repo = slug.split("/")[-1].lower()
    brand = repo if repo in ("sreday", "llmday", "platformday") else "pec"
    site = str(meta.get("base_path") or "").strip() or "https://%s.com/" % repo
    out = {"brand": brand, "brand_name": str(meta.get("brand_name") or repo), "site": site.rstrip("/") + "/",
           "slug": slug, "names": names,
           "ack": set(str(x).strip().lower()[:7] for x in (meta.get("redflag_ack") or []) if str(x).strip())}
    _facts_cache[root] = out
    return out


def event_name(root, folder):
    f = facts(root)
    return f["names"].get(folder) or "%s %s" % (f["brand_name"], folder)


def _when(iso):
    """'Mon 21 Sep, 00:15' in UK time (UTC when tzdata is missing, e.g. a local Windows build)."""
    try:
        dt = datetime.datetime.fromisoformat(iso.strip())
        tz = ""
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Europe/London"))
        except Exception:
            dt, tz = dt.astimezone(datetime.timezone.utc), " UTC"
        return dt.strftime("%a %d %b, %H:%M").replace(" 0", " ", 1) + tz
    except Exception:
        return iso


# ---- rules -------------------------------------------------------------------------

def _source(root, folder, after, commits):
    """Best other event for lineup <after>: (folder, share, identical) or (None, 0, False). Other events are read
    at each of <commits> (None = working tree), so a source uploaded before or after the accident both count."""
    best = (None, 0.0, False)
    for other in event_folders(root):
        if other == folder:
            continue
        for c in commits:
            snap = snapshot(root, c, other + "/_db/talks.csv")
            if not snap or len(snap["rows"]) < 3:
                continue
            identical = bool(after["raw"]) and snap["raw"] == after["raw"]
            share = 1.0 if identical else overlap(after, snap)
            if (identical, share) > (best[2], best[1]):
                best = (other, share, identical)
    if best[0] and (best[2] or best[1] >= LOOKS_LIKE):
        return best
    return (None, 0.0, False)


def _flag(root, folder, kind, sha, iso, author, **kw):
    f = facts(root)
    path = folder + "/_db/talks.csv" if kind != "path" else kw.get("path", "")
    flag = {"brand": f["brand"], "folder": folder, "event_name": event_name(root, folder), "kind": kind,
            "suspect_folder": "", "suspect_name": "", "identical": False, "overlap_pct": 0,
            "gone": 0, "before_n": 0, "added": 0, "after_n": 0, "gone_names": [], "added_names": [],
            "sha": sha or "", "when": _when(iso) if iso else "", "iso": iso or "", "author": author or "", "path": path,
            "commit_url": "https://github.com/%s/commit/%s" % (f["slug"], sha) if sha else "",
            "file_url": "https://github.com/%s/commits/main/%s" % (f["slug"], path),
            "event_url": f["site"] + folder + "/", "status_url": f["site"] + "status/"}
    flag.update(kw)
    flag["headline"] = headline(flag)
    flag["short"] = short(flag)
    return flag


def headline(flag):
    ev, other = flag["event_name"], flag.get("suspect_name") or ""
    if flag["kind"] == "path":
        return "%s: talks.csv was uploaded to %s, outside _db/, so the site ignores it" % (ev, flag.get("path") or "the wrong folder")
    if flag["kind"] == "twin":
        return "%s and %s share %d%% of their lineups" % (ev, other, flag["overlap_pct"])
    if other and flag.get("identical"):
        return "%s is an exact copy of the %s lineup" % (ev, other)
    if other:
        return "%s looks like it has the lineup of %s (%d%% match)" % (ev, other, flag["overlap_pct"])
    if flag["kind"] == "removal":
        return "%s lost %d of %d speakers in one upload" % (ev, flag["gone"], flag["before_n"])
    return "%s had %d of %d speakers replaced in one upload, source unknown" % (ev, flag["gone"], flag["before_n"])


def short(flag):
    """The headline without the event name in front, for places that already show it (the /status/ bar)."""
    if flag["kind"] == "twin":
        return "Shares %d%% of its lineup with %s" % (flag["overlap_pct"], flag.get("suspect_name") or "another event")
    text = headline(flag)
    for lead in (flag["event_name"] + ": ", flag["event_name"] + " "):
        if text.startswith(lead):
            text = text[len(lead):]
            break
    if text.startswith("is "):
        text = "it " + text
    return text[:1].upper() + text[1:]


def check_commit(root, folder, sha, iso="", author="", cloned=False):
    """Flag dict when commit <sha> swapped the lineup of <folder>, else None. <cloned>: the commit created the
    whole event folder, so a lineup inherited from the folder it was copied from is expected."""
    path = folder + "/_db/talks.csv"
    after = snapshot(root, sha, path)
    before = snapshot(root, sha + "~1", path)
    if after is None or before is None:
        return None                                              # outside the shallow window
    if len(before["rows"]) < MIN_LINEUP:
        return _check_twin_at(root, folder, sha, iso, author, after) if not before["rows"] and not cloned else None
    gone, added = churn(before, after)
    if not (len(gone) > MASS[0] and len(gone) > MASS[1] * len(before["rows"])):
        return None
    # the fix looks like a swap too: it brings back one of the file's own older lineups
    older = (git(root, "log", "-n", str(RESTORE_DEPTH + 1), "--format=%H", sha + "~1", "--", path) or "").split()
    for old in older[1:]:                                        # older[0] is the commit that produced <before>
        snap = snapshot(root, old, path)
        if snap and len(snap["rows"]) >= MIN_LINEUP and overlap_fuzzy(after, snap) >= RESTORE:
            return None
    swap = len(after["rows"]) > 0 and len(added) >= ADDED_SHARE * len(after["rows"])
    if not swap:                                                 # clearing the lineup a cloned folder came with
        for other in event_folders(root):
            snap = snapshot(root, sha + "~1", other + "/_db/talks.csv") if other != folder else None
            if snap and len(snap["rows"]) >= MIN_LINEUP and overlap(before, snap) >= TWIN:
                return None
    src = _source(root, folder, after, [sha + "~1", None]) if swap else (None, 0.0, False)
    return _flag(root, folder, "swap" if swap else "removal", sha, iso, author,
                 suspect_folder=src[0] or "", suspect_name=event_name(root, src[0]) if src[0] else "",
                 identical=src[2], overlap_pct=int(round(src[1] * 100)),
                 gone=len(gone), before_n=len(before["rows"]), added=len(added), after_n=len(after["rows"]),
                 gone_names=[before["rows"][k]["name"] for k in gone[:5]],
                 added_names=[after["rows"][k]["name"] for k in added[:5]])


def _check_twin_at(root, folder, sha, iso, author, after):
    """A talks.csv that starts its life (or grows from a stub) as another event's lineup."""
    if len(after["rows"]) < MIN_LINEUP:
        return None
    for other in event_folders(root):
        if other == folder:
            continue
        snap = snapshot(root, sha, other + "/_db/talks.csv")
        if snap and len(snap["rows"]) >= MIN_LINEUP and min(overlap(after, snap), overlap(snap, after)) >= TWIN:
            return _flag(root, folder, "twin", sha, iso, author, suspect_folder=other,
                         suspect_name=event_name(root, other), identical=snap["raw"] == after["raw"],
                         overlap_pct=int(round(overlap(after, snap) * 100)), after_n=len(after["rows"]))
    return None


def flags_for_range(root, before_sha, after_sha):
    """Flags raised by the commits of one push, oldest first."""
    after_sha = after_sha or "HEAD"
    rng = [after_sha, "-1"]
    if before_sha and not re.match(r"^0+$", before_sha) and git(root, "cat-file", "-e", before_sha + "^{commit}") is not None:
        rng = ["%s..%s" % (before_sha, after_sha)]
    log = git(root, "log", "--reverse", "--no-merges", "--format=%x1e%H%x1f%cI%x1f%an", "--name-status", *rng)
    if log is None:
        print("red flag: cannot read the pushed commits (%s), skipped" % " ".join(rng))
        return []
    flags = []
    for chunk in log.split("\x1e")[1:]:
        head, _, body = chunk.partition("\n")
        sha, iso, author = (head.split("\x1f") + ["", ""])[:3]
        born = set(x.split("\t")[-1].split("/")[0] for x in body.splitlines() if x[:1] == "A" and x.endswith("/metadata.yml"))
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or parts[0][:1] not in ("A", "M"):
                continue                                          # renames (a folder rename) and deletions are not uploads
            path = parts[-1]
            m = TALKS_RE.match(path)
            if m and EVENT_RE.match(m.group(1)):
                flag = check_commit(root, m.group(1), sha, iso, author, cloned=m.group(1) in born)
                if flag:
                    flags.append(flag)
            elif parts[0][:1] == "A" and os.path.basename(path).lower() == "talks.csv" and EVENT_RE.match(path.split("/")[0]) \
                    and "/static/" not in "/" + path:
                flags.append(_flag(root, path.split("/")[0], "path", sha, iso, author, path=path))
    acked = facts(root)["ack"]
    return [f for f in flags if f["sha"][:7].lower() not in acked]


def active_flags(root, folders):
    """Flags that are still true for the working tree: an unfixed swap among the last commits of an upcoming
    event's talks.csv, or two upcoming events being twins. For the red bar on /status/."""
    flags, acked = [], facts(root)["ack"]
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=ACTIVE_DAYS)).strftime("%Y-%m-%d")
    for folder in folders:
        path = folder + "/_db/talks.csv"
        now = snapshot(root, None, path)
        log = git(root, "log", "-n", str(ACTIVE_COMMITS), "--since=" + since, "--format=%H%x1f%cI%x1f%an", "--", path)
        for line in (log or "").splitlines():
            sha, iso, author = (line.split("\x1f") + ["", ""])[:3]
            if sha[:7].lower() in acked:
                continue
            flag = check_commit(root, folder, sha, iso, author)
            if not flag:
                continue
            wrong = snapshot(root, sha, path)
            if wrong and overlap_fuzzy(now, wrong) >= RESTORE:    # the page still shows what that commit brought in
                flags.append(flag)
            break                                                 # only the most recent swap matters
    for folder in folders:                                        # a talks.csv next to _db/ instead of inside it
        stray = folder + "/talks.csv"
        if os.path.isfile(os.path.join(root, stray)):
            last = (git(root, "log", "-n", "1", "--format=%H%x1f%cI%x1f%an", "--", stray) or "").strip()
            sha, iso, author = (last.split("\x1f") + ["", ""])[:3]
            if sha[:7].lower() not in acked:
                flags.append(_flag(root, folder, "path", sha, iso, author, path=stray))
    flagged = set(f["folder"] for f in flags if f["kind"] != "path")
    snaps = dict((d, snapshot(root, None, d + "/_db/talks.csv")) for d in folders)
    for i, a in enumerate(folders):
        for b in folders[i + 1:]:
            if a in flagged or b in flagged or min(len(snaps[a]["rows"]), len(snaps[b]["rows"])) < MIN_LINEUP:
                continue
            if min(overlap(snaps[a], snaps[b]), overlap(snaps[b], snaps[a])) < TWIN:
                continue
            last = dict((d, (git(root, "log", "-n", "1", "--format=%H%x1f%cI%x1f%an", "--", d + "/_db/talks.csv") or "").strip()) for d in (a, b))
            newer, older = (a, b) if last[a].split("\x1f")[1:2] >= last[b].split("\x1f")[1:2] else (b, a)
            sha, iso, author = (last[newer].split("\x1f") + ["", ""])[:3]
            if sha[:7].lower() in acked:
                continue
            flags.append(_flag(root, newer, "twin", sha, iso, author, suspect_folder=older,
                               suspect_name=event_name(root, older), identical=snaps[a]["raw"] == snaps[b]["raw"],
                               overlap_pct=int(round(overlap(snaps[newer], snaps[older]) * 100)),
                               after_n=len(snaps[newer]["rows"])))
            flagged.update((a, b))
    return flags


# ---- CLI (prints only; the email is the Gmail script's job) -----------------------

def preview(flag):
    lines = ["Did you just nuke %s?" % flag["event_name"], "  " + flag["headline"]]
    if flag["kind"] in ("swap", "removal"):
        lines.append("  %d of %d speakers gone, %d of %d new, %s by %s" % (flag["gone"], flag["before_n"], flag["added"],
                                                                        flag["after_n"], flag["when"], flag["author"]))
        lines.append("  gone: %s" % ", ".join(flag["gone_names"]))
        lines.append("  new:  %s" % ", ".join(flag["added_names"]))
    lines.append("  " + flag["commit_url"])
    return "\n".join(lines)


def main(argv):
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if "--range" in argv:
        before, _, after = argv[argv.index("--range") + 1].partition("..")
        flags = flags_for_range(root, before, after)
    else:
        flags = active_flags(root, event_folders(root))
    print("RED FLAGS: %d" % len(flags))
    for flag in flags:
        print(preview(flag))


if __name__ == "__main__":
    main(sys.argv[1:])
