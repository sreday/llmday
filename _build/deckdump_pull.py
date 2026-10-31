#!/usr/bin/env python3
"""Deck dump pull (Marek 2026-10-30): ships the slides speakers uploaded on /deckdump/ to their talk pages, the day after
the event, with nobody involved. Run by .github/workflows/deckdump.yml (byte-identical in sreday, llmday, platformday
and PEC), a few times a day.

For every event folder of this repo (top level and past/) whose last day ended (midnight after it, in the event's own
timezone) less than WINDOW_DAYS ago:
  1. reads the manifest from DECKDUMP_FEED ("Deck dump" Apps Script, llmday/_build/deckdump-form.gs: exec URL +
     ?list=1&token=...): every finished upload + a short-lived Drive access token;
  2. skips decks already shipped (<event>/slides/.deckdump.json remembers Drive id + modified time; a newer upload
     replaces the shipped PDF);
  3. matches each deck to a talk by its key (the talk page file name, generate_talk_url in the event build); a talk
     retitled since the upload is found again by the speaker name; a deck whose talk left the lineup is reported;
  4. downloads it straight from Drive, converts PPTX/ODP to PDF (LibreOffice), reports Keynote files (no converter);
  5. fits it under 5 MB (Drive keeps the full-quality upload): a lossless pass first (qpdf, then Ghostscript without
     resampling), then images resampled at 300 / 220 / 150 / 110 dpi, keeping the FIRST step that fits; text, vectors
     and links stay. When 5 MB cannot be reached, the best version under 10 MB is shipped (Marek 2026-10-31); only
     beyond that are the pages flattened to images (flagged). The page count is checked every time;
  6. writes <event>/slides/<key>.pdf. The event build shows "Download slides" on the talk page from the same moment
     (_slides_open in _event_template/_build/generate.py) and copies the PDF into static/ only then.
Then it writes one report per event (shipped, needs a hand, speakers with no deck yet) to the Actions job summary.
No email (Marek 2026-10-31: "I don't want to get notifications on deck dumps to my email"); /deckdump shows the same.

Usage: python _build/deckdump_pull.py [--dry-run] [--today YYYY-MM-DD] [--event <folder>]
       python _build/deckdump_pull.py --selftest     (CI: proves the 5 MB ladder on generated decks; needs Pillow)
Exit code 0 always unless the feed itself is broken (a bad deck never stops the others).
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RECENT_PAST = 3                  # the most recent finished events always shipped (late uploads), = DECKDUMP_PAST
WINDOW_DAYS = 14                 # same as DECKDUMP_DAYS in home/_build/generate.py (how long /deckdump/ lists an event)
MAX_BYTES = 5 * 1024 * 1024
FALLBACK_BYTES = 10 * 1024 * 1024   # when 5 MB cannot be reached without flattening (Marek 2026-10-31)
DPI_STEPS = (300, 220, 150, 110)
FLAT_STEPS = (150, 110, 96)      # last resort: pages as images (text no longer selectable), only when nothing else fits
BRAND_KEYS = {"SREday": "sreday", "LLMday": "llmday", "PLATFORMday": "platformday", "Prompt Engineering Conference": "pec"}
LIVE_KINDS = ("talk", "keynote", "workshop")
TEST_FOLDER = "2026-deckdump-test"   # fake event for --selftest with real decks (upload them with this folder)


# ---- the lineup (same rules as the event and home builds) --------------------------------

def talk_kind(status):                                     # = _talk_kind in home/_build/generate.py
    s = str(status or "").strip().lower()
    if re.search(r"\bdraft\b", s):
        return "draft"
    if "keynote" in s:
        return "keynote"
    if re.search(r"\bworkshop\b", s):
        return "workshop"
    if "confirmed" in s or re.search(r"\btalk\b", s):
        return "talk"
    return None


def talk_url(talk):                                        # = generate_talk_url in _event_template/_build/generate.py
    url = "{name1}{name2}{company}{title}".format(
        name1=talk.get("name", "").replace(" ", "_"),
        name2=("_" + talk.get("co-speaker", "").replace(" ", "_")) if talk.get("co-speaker") else "",
        company=("_" + talk.get("organization", "").replace(" ", "_")) if talk.get("organization") else "",
        title=("_" + talk.get("title", "").replace(",", "_").replace(" ", "_")) if talk.get("title") else "",
    )
    url = ''.join(filter(lambda x: x in string.printable, url))
    url = re.sub('[\\W]+', '', url)
    return url[:100]


def norm_name(v):
    t = unicodedata.normalize("NFD", str(v or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").replace("ł", "l")
    return re.sub(r"[^a-z]+", " ", t).strip()


def lineup(folder_path):
    talks, seen = [], set()
    try:
        with open(os.path.join(folder_path, "_db", "talks.csv"), encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if talk_kind(row.get("status")) not in LIVE_KINDS or not (row.get("name") or "").strip():
                    continue
                k = talk_url(row)
                if k in seen:
                    continue
                seen.add(k)
                talks.append({"key": k, "name": row["name"].strip(), "title": (row.get("title") or "").strip()})
    except OSError:
        pass
    return talks


def event_window(meta, today):
    """(opens, closes) dates: the day after the last day, and WINDOW_DAYS later; None when start_time is unusable."""
    try:
        start = datetime.datetime.fromisoformat(str(meta.get("start_time") or ""))
    except ValueError:
        return None
    tz = start.tzinfo or datetime.timezone.utc
    ends = datetime.datetime.combine(start.date() + datetime.timedelta(days=max(1, int(meta.get("days") or 1))), datetime.time(0, 0), tz)
    now = today or datetime.datetime.now(tz)
    return ends, ends + datetime.timedelta(days=WINDOW_DAYS), now


# ---- feed, Drive, report -------------------------------------------------------------------

def http_json(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "text/plain;charset=utf-8"} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def download(file_id, token, dest):
    req = urllib.request.Request("https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true" % urllib.parse.quote(file_id),
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as out:
        shutil.copyfileobj(r, out, 1 << 20)


# ---- conversion + the 5 MB ladder ------------------------------------------------------------

def have(tool):
    return shutil.which(tool) is not None


def apt(*pkgs):
    """CI only: install what a deck needs, the first time it is needed (LibreOffice ~1 min)."""
    missing = [p for p in pkgs if not have({"libreoffice-impress": "soffice", "ghostscript": "gs", "qpdf": "qpdf"}[p])]
    if missing and os.environ.get("GITHUB_ACTIONS"):
        print("Installing %s" % " ".join(missing), flush=True)
        subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "--no-install-recommends"] + missing, check=False, stdout=subprocess.DEVNULL)


def soffice():
    for c in ("soffice", "libreoffice", r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if shutil.which(c) or os.path.isfile(c):
            return c
    return None


def to_pdf(src, workdir):
    apt("libreoffice-impress")
    exe = soffice()
    if not exe:
        raise RuntimeError("LibreOffice not available")
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", workdir, src], check=True, timeout=600,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = os.path.join(workdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if not os.path.isfile(out):
        raise RuntimeError("LibreOffice produced no PDF")
    return out


def pages(path):
    r = subprocess.run(["qpdf", "--show-npages", path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


GS = ["gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER",
      "-dDetectDuplicateImages=true", "-dCompressFonts=true", "-dSubsetFonts=true"]


def gs_lossless(src, dst):
    flags = ["-dPassThroughJPEGImages=true", "-dPassThroughJPXImages=true"]
    for kind in ("Color", "Gray", "Mono"):
        flags += ["-dDownsample%sImages=false" % kind]
    for kind in ("Color", "Gray"):
        flags += ["-dAutoFilter%sImages=false" % kind, "-d%sImageFilter=/FlateEncode" % kind]
    subprocess.run(GS + flags + ["-sOutputFile=" + dst, src], check=True, timeout=900)


def gs_dpi(src, dst, dpi):
    flags = ["-dPassThroughJPEGImages=false", "-dJPEGQ=85"]
    for kind in ("Color", "Gray"):
        flags += ["-dDownsample%sImages=true" % kind, "-d%sImageDownsampleType=/Bicubic" % kind, "-d%sImageResolution=%d" % (kind, dpi),
                  "-d%sImageDownsampleThreshold=1.0" % kind, "-dAutoFilter%sImages=false" % kind, "-d%sImageFilter=/DCTEncode" % kind]
    flags += ["-dDownsampleMonoImages=true", "-dMonoImageResolution=%d" % max(300, dpi * 2)]
    subprocess.run(GS + flags + ["-sOutputFile=" + dst, src], check=True, timeout=900)


def gs_flat(src, dst, dpi):
    """Last resort: every page drawn as one JPEG (q85). Always small; text is no longer selectable."""
    subprocess.run(["gs", "-sDEVICE=pdfimage24", "-r%d" % dpi, "-sCompression=JPEG", "-dJPEGQ=85", "-dNOPAUSE", "-dBATCH", "-dQUIET",
                    "-dSAFER", "-sOutputFile=" + dst, src], check=True, timeout=900)


def fit(src, workdir):
    """-> (path, step): the highest-quality version with the same page count that fits. Target MAX_BYTES (5 MB); when no
    normal step gets there, the highest-quality one under FALLBACK_BYTES (10 MB, Marek 2026-10-31: "if 5MB fails, allow
    10MB"); only when even that fails are the pages flattened (sharpest that fits 10 MB). Steps, best quality first:
    'original', 'lossless', '<dpi> dpi', 'flattened <dpi> dpi'; ', over 10 MB' when nothing fits (the smallest is used)."""
    apt("qpdf", "ghostscript")
    n = pages(src)
    tried = [(src, "original")]                            # every valid version, best quality first

    def add(path, step):
        if os.path.isfile(path) and pages(path) == n:
            tried.append((path, step))

    def first_under(limit):
        for path, step in tried:
            if os.path.getsize(path) <= limit:
                return path, step
        return None

    q = os.path.join(workdir, "qpdf.pdf")
    subprocess.run(["qpdf", "--object-streams=generate", "--compress-streams=y", "--recompress-flate", "--compression-level=9",
                    src, q], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.isfile(q) and os.path.getsize(q) < os.path.getsize(src):
        tried.insert(0, (q, "lossless"))                   # same quality, smaller: preferred over the original
    if first_under(MAX_BYTES):
        return first_under(MAX_BYTES)
    base = tried[0][0]
    g = os.path.join(workdir, "gs-lossless.pdf")
    try:
        gs_lossless(base, g)
        if os.path.isfile(g) and os.path.getsize(g) < os.path.getsize(base) and pages(g) == n:
            tried.insert(0, (g, "lossless"))
            base = g
    except (subprocess.SubprocessError, OSError) as e:
        print("  ghostscript lossless pass failed: %s" % e)
    if first_under(MAX_BYTES):
        return first_under(MAX_BYTES)
    for dpi in DPI_STEPS:
        out = os.path.join(workdir, "gs-%d.pdf" % dpi)
        try:
            gs_dpi(base, out, dpi)
        except (subprocess.SubprocessError, OSError) as e:
            print("  ghostscript %d dpi failed: %s" % (dpi, e))
            continue
        add(out, "%d dpi" % dpi)
        if os.path.isfile(out) and os.path.getsize(out) <= MAX_BYTES and pages(out) == n:
            return out, "%d dpi" % dpi
    if first_under(FALLBACK_BYTES):                        # nothing under 5 MB: the best version under 10 MB
        path, step = first_under(FALLBACK_BYTES)
        return path, step + ", 5-10 MB"
    # still over 10 MB: heavy vector art (no images to resample) or giant photos. Flatten the pages, sharpest first.
    for dpi in FLAT_STEPS:
        out = os.path.join(workdir, "flat-%d.pdf" % dpi)
        try:
            gs_flat(src, out, dpi)
        except (subprocess.SubprocessError, OSError) as e:
            print("  ghostscript flatten %d dpi failed: %s" % (dpi, e))
            continue
        add(out, "flattened %d dpi" % dpi)
        if os.path.isfile(out) and os.path.getsize(out) <= FALLBACK_BYTES and pages(out) == n:
            return out, "flattened %d dpi" % dpi
    path, step = min(tried, key=lambda t: os.path.getsize(t[0]))
    return path, step + ", over 10 MB"


# ---- one event ----------------------------------------------------------------------------------

def mb(n):
    return "%.1f MB" % (n / 1048576.0)


def process_event(folder_path, meta, files, token, args):
    folder = os.path.basename(folder_path)
    talks = lineup(folder_path)
    by_key = {t["key"]: t for t in talks}
    slides_dir = os.path.join(folder_path, "slides")
    state_path = os.path.join(slides_dir, ".deckdump.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    shipped_state = state.setdefault("shipped", {})
    reported = set(state.setdefault("reported", []))
    shipped, problems = [], []

    # newest upload per talk; keys of retitled talks re-matched by speaker name
    chosen = {}
    for f in sorted(files, key=lambda x: x.get("modified") or ""):
        m = f.get("meta") or {}
        key = m.get("short_url") or ""
        if key not in by_key:
            hits = [t for t in talks if norm_name(t["name"]) == norm_name(m.get("speaker"))]
            if len(hits) == 1:
                print("  %s: talk key changed since the upload, matched by name -> %s" % (m.get("speaker"), hits[0]["key"]))
                key = hits[0]["key"]
            else:
                pid = "%s@%s" % (f["id"], f.get("modified"))
                if pid not in reported:
                    problems.append({"pid": pid, "speaker": m.get("speaker"), "title": m.get("title"), "file": f.get("name"),
                                     "why": "talk is not in the lineup any more (or renamed and the speaker name changed too)"})
                continue
        chosen[key] = f

    for key, f in sorted(chosen.items(), key=lambda kv: by_key[kv[0]]["name"].lower()):
        m = f.get("meta") or {}
        prev = shipped_state.get(key) or {}
        if prev.get("id") == f["id"] and prev.get("modified") == f.get("modified") and os.path.isfile(os.path.join(slides_dir, key + ".pdf")):
            continue
        if m.get("converted_from") and prev and os.path.isfile(os.path.join(slides_dir, key + ".pdf")):
            continue                                       # a PDF copy of a deck already on the site (v3 one-off convert)
        talk = by_key[key]
        pid = "%s@%s" % (f["id"], f.get("modified"))
        ext = (m.get("ext") or os.path.splitext(f.get("name") or "")[1].lstrip(".")).lower()
        if ext == "key":
            if pid not in reported:
                problems.append({"pid": pid, "speaker": talk["name"], "title": talk["title"], "file": f.get("name"),
                                 "why": "Keynote file: please export it to PDF and drop it in %s/slides/%s.pdf" % (folder, key)})
            continue
        if args.dry_run:
            print("  would ship %s (%s, %s) -> slides/%s.pdf" % (talk["name"], f.get("name"), mb(int(f.get("size") or 0)), key))
            shipped.append({"speaker": talk["name"], "title": talk["title"], "step": "dry run", "bytes": int(f.get("size") or 0), "src": ext})
            continue
        work = tempfile.mkdtemp(prefix="deckdump-")
        try:
            raw = os.path.join(work, "deck." + ext)
            download(f["id"], token, raw)
            pdf = raw if ext == "pdf" else to_pdf(raw, work)
            out, step = fit(pdf, work)
            os.makedirs(slides_dir, exist_ok=True)
            shutil.copyfile(out, os.path.join(slides_dir, key + ".pdf"))
            size = os.path.getsize(out)
            shipped_state[key] = {"id": f["id"], "modified": f.get("modified"), "file": f.get("name"), "step": step, "bytes": size,
                                  "shipped": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            shipped.append({"speaker": talk["name"], "title": talk["title"], "step": step, "bytes": size, "src": ext})
            print("  shipped %s: %s %s -> %s (%s)" % (talk["name"], ext, mb(int(f.get("size") or 0)), mb(size), step))
            if step.startswith("flattened"):
                problems.append({"pid": pid + ":flat", "speaker": talk["name"], "title": talk["title"], "file": f.get("name"),
                                 "why": "pages flattened to images to fit 5 MB (%s): text is not selectable on the site copy" % step})
            if "over 10 MB" in step:
                problems.append({"pid": pid + ":big", "speaker": talk["name"], "title": talk["title"], "file": f.get("name"),
                                 "why": "still %s after every step, shipped anyway; a lighter export would help" % mb(size)})
        except Exception as e:   # one bad deck never stops the others
            print("  FAILED %s: %s" % (talk["name"], e))
            if pid not in reported:
                problems.append({"pid": pid, "speaker": talk["name"], "title": talk["title"], "file": f.get("name"), "why": "could not process: %s" % e})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    on_site = [t for t in talks if os.path.isfile(os.path.join(slides_dir, t["key"] + ".pdf"))]
    missing = [t for t in talks if t["key"] not in chosen and t not in on_site]
    if not args.dry_run and (shipped or problems):
        state["reported"] = sorted(reported | {p["pid"] for p in problems})
        os.makedirs(slides_dir, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, ensure_ascii=False, sort_keys=True)
    return {"folder": folder, "talks": len(talks), "shipped": shipped, "problems": problems, "missing": missing,
            "with_deck": len(on_site) + (len(shipped) if args.dry_run else 0)}


def compose(event_name, site, folder, r):
    lines = ["Deck dump for %s: %d of %d talks have slides on the site now." % (event_name, r["with_deck"], r["talks"]), ""]
    if r["shipped"]:
        lines.append("Shipped this run:")
        for s in r["shipped"]:
            lines.append("- %s: %s (%s, %s)" % (s["speaker"], s["title"], mb(s["bytes"]), s["step"] + ("" if s["src"] == "pdf" else ", from " + s["src"].upper())))
        lines.append("")
    if r["problems"]:
        lines.append("Needs a hand:")
        for p in r["problems"]:
            lines.append("- %s (%s): %s" % (p["speaker"], p["file"], p["why"]))
        lines.append("")
    if r["missing"]:
        lines.append("No deck yet (%d):" % len(r["missing"]))
        for t in r["missing"]:
            lines.append("- %s: %s" % (t["name"], t["title"]))
        lines.append("")
        lines.append("Upload page: https://%s/deckdump/?event=%s" % (site, folder))
    return "\n".join(lines).rstrip() + "\n"


def selftest():
    """A photo-heavy deck must land under 5 MB at the highest dpi step that fits, same pages, text kept;
    a light deck must stay as it is apart from the lossless pass."""
    import random
    from PIL import Image, ImageDraw, ImageFilter
    work = tempfile.mkdtemp(prefix="deckdump-selftest-")
    rnd = random.Random(7)
    slides = []
    for i in range(24):                                       # 16:9 slides with big noisy "photos" (barely compressible)
        im = Image.effect_noise((1600, 900), 60).convert("RGB").filter(ImageFilter.GaussianBlur(1.2))
        im = Image.blend(im, Image.new("RGB", im.size, (rnd.randrange(255), rnd.randrange(255), rnd.randrange(255))), .4)
        ImageDraw.Draw(im).text((80, 80), "Slide %d - selectable text is checked on the vector deck" % (i + 1), fill=(255, 255, 255))
        slides.append(im)
    heavy = os.path.join(work, "heavy.pdf")
    slides[0].save(heavy, save_all=True, append_images=slides[1:], resolution=72, quality=97)
    light = os.path.join(work, "light.pdf")
    slides[0].resize((320, 180)).save(light, resolution=72)
    ok = True
    for name, path in (("heavy", heavy), ("light", light)):
        before, n = os.path.getsize(path), pages(path)
        d = tempfile.mkdtemp(dir=work)
        out, step = fit(path, d)
        after = os.path.getsize(out)
        good = after <= FALLBACK_BYTES and pages(out) == n
        ok &= good
        print("selftest %s: %s -> %s, %d pages -> %d, step %s: %s" % (name, mb(before), mb(after), n, pages(out), step, "OK" if good else "FAIL"))
    # real decks: whatever was uploaded on /deckdump/ to the fake event folder TEST_FOLDER (never a real event, so the
    # pull job never ships them); each is shrunk like a real one and kept in deckdump-selftest/ for the run's artifact
    feed = os.environ.get("DECKDUMP_FEED", "").strip()
    if feed:
        res = http_json(feed, timeout=120)
        real = [f for f in (res.get("files") or []) if (f.get("meta") or {}).get("folder") == TEST_FOLDER]
        print("selftest: %d real deck(s) in %s" % (len(real), TEST_FOLDER))
        keep = os.path.join(ROOT, "deckdump-selftest")
        os.makedirs(keep, exist_ok=True)
        for f in real:
            m, d = f["meta"], tempfile.mkdtemp(dir=work)
            ext = (m.get("ext") or "pdf").lower()
            try:
                raw = os.path.join(d, "deck." + ext)
                download(f["id"], res.get("access_token") or "", raw)
                pdf = raw if ext == "pdf" else to_pdf(raw, d)
                n = pages(pdf)
                out, step = fit(pdf, d)
                good = os.path.getsize(out) <= FALLBACK_BYTES and pages(out) == n
                ok &= good
                name = re.sub(r"[^\w .&-]+", "", m.get("original_name") or f["name"])
                shutil.copyfile(out, os.path.join(keep, "shrunk - " + os.path.splitext(name)[0] + ".pdf"))
                print("selftest real %s: %s -> %s, %d pages -> %d, step %s: %s" % (name, mb(int(f.get("size") or 0)), mb(os.path.getsize(out)),
                                                                                 n, pages(out), step, "OK" if good else "FAIL"))
            except Exception as e:
                ok = False
                print("selftest real %s: FAILED %s" % (f.get("name"), e))
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


def events_all(today):
    """{folder: (path, ends)} for every 2025+ event folder of this repo."""
    out = {}
    for path in glob.glob(os.path.join(ROOT, "20*")) + glob.glob(os.path.join(ROOT, "past", "20*")):
        folder = os.path.basename(path)
        if folder[:4] < "2025":
            continue
        try:
            with open(os.path.join(path, "metadata.yml"), encoding="utf-8") as f:
                w = event_window(yaml.load(f, Loader=yaml.FullLoader) or {}, today)
        except (OSError, yaml.YAMLError):
            continue
        if w:
            out[folder] = (path, w[0], w[2])
    return out


def cleanup(feed, brand, events, args):
    """Quarterly (Marek 2026-10-31: "wipe the drive every 3 months, after checking that all uploaded decks are in the
    repo"): every upload of this brand whose event ended more than WINDOW_DAYS ago AND whose deck is on the site
    (<event>/slides/<key>.pdf in the repo) goes to the Drive trash (30 days to undo). Anything not on the site stays
    and is listed. The fake test folder and other brands' uploads are left alone."""
    res = http_json(feed, timeout=120)
    if not res.get("ok"):
        print("Deck dump feed refused: %s" % res.get("error"))
        return 1
    token = res.get("access_token") or ""
    if token and os.environ.get("GITHUB_ACTIONS"):
        print("::add-mask::" + token)
    trashed, kept = [], []
    for f in res.get("files") or []:
        m = f.get("meta") or {}
        if m.get("brand") != brand or m.get("folder") not in events:
            continue
        path, ends, now = events[m["folder"]]
        if now < ends + datetime.timedelta(days=WINDOW_DAYS):
            continue                                       # still in its shipping window
        state = {}
        try:
            with open(os.path.join(path, "slides", ".deckdump.json"), encoding="utf-8") as fh:
                state = json.load(fh).get("shipped") or {}
        except (OSError, ValueError):
            pass
        keys = [k for k, v in state.items() if v.get("id") == f["id"]] + [m.get("short_url") or ""]
        if any(k and os.path.isfile(os.path.join(path, "slides", k + ".pdf")) for k in keys):
            if not args.dry_run:
                req = urllib.request.Request("https://www.googleapis.com/drive/v3/files/%s?supportsAllDrives=true" % urllib.parse.quote(f["id"]),
                                             data=b'{"trashed": true}', method="PATCH",
                                             headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60).read()
            trashed.append(f["name"])
        else:
            kept.append("%s (%s)" % (f["name"], "not on the site: check it"))
    lines = ["Deck dump cleanup%s: %d upload(s) on the site %s, %d kept" % (
        " (dry run)" if args.dry_run else "", len(trashed),
        "would go to the Drive trash" if args.dry_run else "moved to the Drive trash", len(kept))]
    lines += ["- trashed: %s" % n for n in trashed] + ["- kept: %s" % n for n in kept]
    text = "\n".join(lines) + "\n"
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("## Deck dump cleanup\n\n```\n" + text + "```\n")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="read the feed and show what would ship; download and write nothing")
    ap.add_argument("--today", help="pretend it is this date (YYYY-MM-DD, noon UTC), for tests")
    ap.add_argument("--event", help="only this event folder (ignores the 14-day window); 'all' = every event that has ended")
    ap.add_argument("--cleanup", action="store_true", help="quarterly: trash Drive uploads that are on the site already")
    ap.add_argument("--selftest", action="store_true", help="run the 5 MB ladder on generated decks and exit")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    feed = os.environ.get("DECKDUMP_FEED", "").strip()
    if not feed:
        print("DECKDUMP_FEED is not set: nothing to do")
        return 0
    with open(os.path.join(ROOT, "home", "metadata.yml"), encoding="utf-8") as f:
        home = yaml.load(f, Loader=yaml.FullLoader) or {}
    brand = BRAND_KEYS.get(str(home.get("brand_name") or ""), "")
    site = re.sub(r"^https?://(www\.)?", "", str(home.get("base_path") or "")).strip("/")
    today = None
    if args.today:
        today = datetime.datetime.fromisoformat(args.today + "T12:00:00+00:00")

    # events in their shipping window (nothing to fetch when none is)
    events, finished = [], []
    for path in sorted(glob.glob(os.path.join(ROOT, "20*")) + glob.glob(os.path.join(ROOT, "past", "20*"))):
        folder = os.path.basename(path)
        if folder[:4] < "2025" or (args.event and args.event != "all" and folder != args.event):
            continue                                      # frozen sreday 2022-2024 folders are never touched
        try:
            with open(os.path.join(path, "metadata.yml"), encoding="utf-8") as f:
                meta = yaml.load(f, Loader=yaml.FullLoader) or {}
        except (OSError, yaml.YAMLError):
            continue
        w = event_window(meta, today)
        if not w:
            continue
        opens, closes, now = w
        if (args.event and args.event != "all") or (args.event == "all" and opens <= now) or opens <= now <= closes:
            events.append((path, meta))
        elif opens <= now:
            finished.append((opens, path, meta))
    # the 3 most recent finished events stay open for late uploads, like /deckdump/ (DECKDUMP_PAST in home/_build/generate.py)
    if not args.event:
        shown = {p for p, _ in events}
        events += [(p, m) for _, p, m in sorted(finished, key=lambda x: x[0], reverse=True)[:RECENT_PAST] if p not in shown]
    if args.cleanup:
        return cleanup(feed, brand, events_all(today), args)
    if not events:
        print("Deck dump: no %s event in its shipping window" % (brand or "?"))
        return 0
    print("Deck dump: %s" % ", ".join(os.path.basename(p) for p, _ in events))

    res = http_json(feed, timeout=120)
    if not res.get("ok"):
        print("Deck dump feed refused: %s" % res.get("error"))
        return 1
    token = res.get("access_token") or ""
    if token and os.environ.get("GITHUB_ACTIONS"):
        print("::add-mask::" + token)
    files = [f for f in (res.get("files") or []) if (f.get("meta") or {}).get("brand") == brand]

    summary = []
    for path, meta in events:
        folder = os.path.basename(path)
        mine = [f for f in files if f["meta"].get("folder") == folder]
        print("%s: %d uploads" % (folder, len(mine)))
        r = process_event(path, meta, mine, token, args)
        event_name = next((str(e.get("name")) for e in (home.get("events") or []) + (home.get("events_past") or [])
                           if str((e or {}).get("url") or "").strip("./").rstrip("/") == folder), folder)
        text = compose(event_name, site, folder, r)
        print(text)
        summary.append("### %s\n\n```\n%s```\n" % (event_name, text))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Deck dump\n\n" + "\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
