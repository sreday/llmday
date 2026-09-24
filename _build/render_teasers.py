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
"""
import glob
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


def shoot(chrome, url, height, out_png):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox", "--disable-dev-shm-usage",
           "--allow-file-access-from-files", "--force-device-scale-factor=%s" % SCALE,
           "--window-size=%d,%d" % (CARD, height), "--virtual-time-budget=%d" % BUDGET_MS,
           "--screenshot=%s" % out_png, url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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
        done = 0
        with tempfile.TemporaryDirectory() as tmp:
            for start in range(0, len(files), CHUNK):
                chunk = files[start:start + CHUNK]
                shot = os.path.join(tmp, "sheet-%d.png" % start)
                ok, err = shoot(chrome, "%s#sheet-%d-%d" % (url, start, len(chunk)), CARD * len(chunk), shot)
                if not ok:
                    print("WARN render_teasers %s: screenshot failed for cards %d-%d %s" % (event, start, start + len(chunk) - 1, err))
                    continue
                img = Image.open(shot)
                if img.width < side or img.height < side * len(chunk):
                    print("WARN render_teasers %s: screenshot %dx%d smaller than expected %dx%d" % (event, img.width, img.height, side, side * len(chunk)))
                for i, name in enumerate(chunk):
                    tile = img.crop((0, i * side, side, (i + 1) * side)).convert("RGB")
                    tile.save(os.path.join(os.path.dirname(page), name), "PNG", optimize=True)
                    done += 1
        total += done
        print("render_teasers %s: %d/%d cards -> PNG" % (event, done, len(files)))
    print("render_teasers: %d PNGs in %d upcoming event(s), %d teaser page(s) found" % (total, events, len(pages)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the deploy over a picture
        print("WARN render_teasers: %s: %s" % (type(e).__name__, e))
