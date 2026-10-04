"""Render the /<event>/teasers/ cards to 1500x1500 PNGs with headless Chrome (2026-09-24).

Runs after `make generate` (and after optimize_images.py) in the GitHub build, from the repo root:
    python _build/render_teasers.py [--all] [--root static]
For every static/<event>/teasers/index.html of an upcoming event (start_time in static/<event>/metadata.yml
not older than yesterday; --all renders past events too) it opens the page in headless Chrome with
#sheet-<start>-<count> (the page then shows only those cards, stacked at their true 1200 px size), takes
one screenshot at device scale 1.25 and slices it into 1500x1500 files named as the page's data-file
attributes, next to index.html. The cards therefore need no browser-side rendering library, and every
card has a stable URL: /<event>/teasers/<brand>-<event>-<speaker>.png.
Never fails the build: problems are printed as WARN and the page keeps working without its PNGs.

Content-addressed cache (2026-09-30): each card's PNG is kept in .cache/teasers/<key>.png, where the key
hashes this script, Chrome's major version, the card's own HTML, the rest of the page (head, styles, badge
inputs, scripts; everything outside the card list) and the bytes of every local file either refers to.
Only cards whose key is new are screenshotted; the rest are copied. Not in the key: remote resources
(Google Fonts). CI keeps .cache/ between runs with actions/cache; --prune drops entries unused for 14 days.
"""
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("WARN render_teasers: Pillow missing, no PNGs rendered")
    sys.exit(0)

CARD = 1200          # the card's CSS size
SCALE = 1.25         # 1200 * 1.25 = 1500 px output
CHUNK = 8            # cards per screenshot (8 * 1500 = 12000 px tall, under Chrome's surface limit)
BUDGET_MS = 12000    # virtual time for fonts + images to settle before the screenshot

CACHE_DIR = os.path.join(os.environ.get("SITE_CACHE_DIR", ".cache"), "teasers")
PRUNE_DAYS = 14      # --prune removes entries not used for this long
with open(__file__, "rb") as _f:
    SCRIPT_HASH = hashlib.sha256(_f.read()).hexdigest()[:16]
REF_RE = re.compile(r'(?:src|href)="([^"]+)"|url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')


def find_chrome():
    for env in ("CHROME_BIN", "CHROME_PATH"):
        if os.environ.get(env) and os.path.exists(os.environ[env]):
            return os.environ[env]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if os.path.exists(p):
            return p
    return None


def event_is_upcoming(event_dir):
    meta = os.path.join(event_dir, "metadata.yml")
    try:
        with open(meta, encoding="utf-8") as f:
            m = re.search(r'^start_time:\s*"?(\d{4}-\d{2}-\d{2})', f.read(), re.M)
        if not m:
            return True
        day = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return day >= datetime.now(timezone.utc) - timedelta(days=1)
    except OSError:
        return True


def card_files(index_html):
    with open(index_html, encoding="utf-8") as f:
        return re.findall(r'class="tz-card" id="tz-card-\d+" data-file="([^"]+)"', f.read())


def page_parts(index_html):
    """Split the teaser page into (context, [card html, ...]): each card is its .tz-slot block (data- attributes since 2026-10-04) up to the
    next one; the context is everything else (head, styles, controls, scripts)."""
    with open(index_html, encoding="utf-8") as f:
        html = f.read()
    starts = [m.start() for m in re.finditer(r'<div class="tz-slot"[ >]', html)]
    if not starts:
        return html, []
    end = html.find("<script", starts[-1])
    end = len(html) if end < 0 else end
    bounds = starts + [end]
    cards = [re.sub(r' id="tz-card-\d+"', "", html[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]
    return html[:starts[0]] + html[end:], cards


def refs_digest(text, base_dir, h, outputs=()):
    """Feed the bytes of every local file the text refers to into h. Remote and data: refs, and the
    cards' own output PNGs (each card links to its file for download), count by name only."""
    for m in REF_RE.finditer(text):
        ref = (m.group(1) or m.group(2) or "").split("#")[0].split("?")[0]
        if not ref or ref.startswith(("http:", "https:", "data:", "//", "mailto:")) or os.path.basename(ref) in outputs:
            h.update(ref.encode())
            continue
        path = os.path.normpath(os.path.join(base_dir, ref))
        h.update(ref.encode())
        try:
            with open(path, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"missing")


def card_keys(index_html, chrome_version):
    context, cards = page_parts(index_html)
    outputs = set(card_files(index_html))
    base_dir = os.path.dirname(index_html)
    ctx = hashlib.sha256()
    ctx.update(("%s|%s|%d|%s|%d|" % (SCRIPT_HASH, chrome_version, CARD, SCALE, BUDGET_MS)).encode())
    ctx.update(context.encode())
    refs_digest(context, base_dir, ctx, outputs)
    keys = []
    for card in cards:
        h = ctx.copy()
        h.update(card.encode())
        refs_digest(card, base_dir, h, outputs)
        keys.append(h.hexdigest())
    return keys


def runs_of(indices):
    """Consecutive runs of card indices, each at most CHUNK long (one screenshot each)."""
    runs = []
    for i in indices:
        if runs and runs[-1][-1] == i - 1 and len(runs[-1]) < CHUNK:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def shoot(chrome, url, height, out_png):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox", "--disable-dev-shm-usage",
           "--allow-file-access-from-files", "--force-device-scale-factor=%s" % SCALE,
           "--window-size=%d,%d" % (CARD, height), "--virtual-time-budget=%d" % BUDGET_MS,
           "--screenshot=%s" % out_png, url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:  # one stuck screenshot skips its cards, not the whole run
        return False, ["timed out after 180 s"]
    return os.path.exists(out_png), (r.stderr or "").strip().splitlines()[-1:]


def main():
    root = "static"
    if "--root" in sys.argv:
        root = sys.argv[sys.argv.index("--root") + 1]
    render_all = "--all" in sys.argv
    chrome = find_chrome()
    if not chrome:
        print("WARN render_teasers: no Chrome/Chromium found, no PNGs rendered")
        return
    pages = sorted(glob.glob(os.path.join(root, "20*", "teasers", "index.html")))
    total, events = 0, 0
    rendered_total, reused_total, used = 0, 0, set()
    r = subprocess.run([chrome, "--version"], capture_output=True, text=True)
    # Only the major version goes into the key: GitHub updates its runner (and Chrome's build number)
    # every few days, while a new major version is what could change how a card renders.
    m = re.search(r"(\d+)\.", r.stdout or r.stderr or "")
    chrome_version = "Chrome %s" % (m.group(1) if m else "unknown")
    side = int(round(CARD * SCALE))
    for page in pages:
        event_dir = os.path.dirname(os.path.dirname(page))
        event = os.path.basename(event_dir)
        if not render_all and not event_is_upcoming(event_dir):
            continue
        events += 1
        files = card_files(page)
        if not files:
            print("render_teasers %s: no cards" % event)
            continue
        url = "file:///" + os.path.abspath(page).replace("\\", "/")
        out_dir = os.path.dirname(page)
        keys = card_keys(page, chrome_version)
        if len(keys) != len(files):  # page layout not understood: render everything, cache nothing
            print("WARN render_teasers %s: %d cards but %d slots, not caching" % (event, len(files), len(keys)))
            keys = [None] * len(files)
        done, reused, missing = 0, 0, []
        for i, (name, key) in enumerate(zip(files, keys)):
            cached_png = key and os.path.join(CACHE_DIR, key + ".png")
            if key:
                used.add(key + ".png")
            if cached_png and os.path.exists(cached_png):
                shutil.copyfile(cached_png, os.path.join(out_dir, name))
                os.utime(cached_png)  # last used now: --prune keeps it
                reused += 1
                done += 1
            else:
                missing.append(i)
        with tempfile.TemporaryDirectory() as tmp:
            for run in runs_of(missing):
                start = run[0]
                shot = os.path.join(tmp, "sheet-%d.png" % start)
                ok, err = shoot(chrome, "%s#sheet-%d-%d" % (url, start, len(run)), CARD * len(run), shot)
                if not ok:
                    print("WARN render_teasers %s: screenshot failed for cards %d-%d %s" % (event, start, run[-1], err))
                    continue
                img = Image.open(shot)
                if img.width < side or img.height < side * len(run):
                    print("WARN render_teasers %s: screenshot %dx%d smaller than expected %dx%d" % (event, img.width, img.height, side, side * len(run)))
                for j, i in enumerate(run):
                    out = os.path.join(out_dir, files[i])
                    tile = img.crop((0, j * side, side, (j + 1) * side)).convert("RGB")
                    tile.save(out, "PNG", optimize=True)
                    done += 1
                    if keys[i]:
                        os.makedirs(CACHE_DIR, exist_ok=True)
                        shutil.copyfile(out, os.path.join(CACHE_DIR, keys[i] + ".png"))
        rendered_total += done - reused
        reused_total += reused
        total += done
        print("render_teasers %s: %d/%d cards -> PNG (%d reused, %d rendered)" % (event, done, len(files), reused, done - reused))
    print("render_teasers: %d PNGs in %d upcoming event(s), %d teaser page(s) found; %d reused from cache, %d rendered"
          % (total, events, len(pages), reused_total, rendered_total))
    if "--prune" in sys.argv and os.path.isdir(CACHE_DIR):
        # Drop entries unused for PRUNE_DAYS, not merely unused by this run: while GitHub rolls out a new
        # runner image, runs alternate between Chrome versions and both sets of entries must survive.
        cutoff = datetime.now().timestamp() - PRUNE_DAYS * 86400
        stale = [f for f in os.listdir(CACHE_DIR)
                 if f not in used and os.path.getmtime(os.path.join(CACHE_DIR, f)) < cutoff]
        for f in stale:
            os.remove(os.path.join(CACHE_DIR, f))
        print("render_teasers: pruned %d cache entries unused for %d days" % (len(stale), PRUNE_DAYS))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the deploy over a picture
        print("WARN render_teasers: %s: %s" % (type(e).__name__, e))
