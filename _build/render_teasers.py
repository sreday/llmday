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

YouTube thumbnails (2026-10-07): the same page also holds one 1280x720 thumbnail per talk (.th-card, second tab).
They are rendered the same way (#thumbs-<start>-<count>, device scale 1) into "<speaker>.png" (Marek 2026-10-07),
kept under YouTube's 2 MB limit. Each kind has its own cache keys: a card's key ignores the other kind's markup.
The thumbnails never expire (Marek 2026-10-07): past events get theirs rendered too (their pages no longer hold
teaser cards); the teaser cards are still rendered for upcoming events only.

Content-addressed cache (2026-09-30): each card's PNG is kept in .cache/teasers/<key>.png, where the key
hashes this script, Chrome's major version, the card's own HTML, the rest of the page (head, styles, badge
inputs, scripts; everything outside the card list) and the bytes of every local file either refers to.
Only cards whose key is new are screenshotted; the rest are copied. Not in the key: remote resources
(Google Fonts). CI keeps .cache/ between runs with actions/cache; --prune drops entries unused for 14 days.

Fast deploys (Marek 2026-10-09: a deploy takes at most 5 minutes, and a picture without its slime is never published):
  --cached-only   the deploy job: copy the PNGs whose key is cached, render nothing; a missing PNG is simply absent (the
                  page then draws that card in the browser, slime-checked) - never a stale one
  --jobs N        the render job after the deploy: N headless Chromes at once
The key covers only what draws a card: the card's own HTML, the page's styles minus the tools' block (/*tools*/ ...
/*/tools*/: Duo / Trio, Generic, Carousel, host panel - drawn in the browser, never here), the card script (/*render*/
... /*/render*/: text fitting, sheet mode) and the files they use. Past events are frozen: a thumbnail's key is its
own text (= its talks.csv row) plus its headshot's bytes, nothing else - design changes never redraw them.
Slime check: in sheet mode the page hides a card whose slime is broken (its gradient or filter id missing or not
unique); an all-black tile is rejected here, never cached and never written.
"""
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("WARN render_teasers: Pillow missing, no PNGs rendered")
    sys.exit(0)

CARD = 1200          # the card's CSS size
SCALE = 1.25         # 1200 * 1.25 = 1500 px output
CHUNK = 8            # cards per screenshot (8 * 1500 = 12000 px tall, under Chrome's surface limit)
YT_MAX = 2 * 1024 * 1024   # YouTube's thumbnail size limit
# the two picture kinds on a teaser page: the card markup, its CSS size, device scale, sheet hash, the other kind's
# markup (between these comment markers) that its cache key leaves out, and the output format
KINDS = [
    {"name": "teaser", "card_re": r'class="tz-card" id="tz-card-\d+" data-file="([^"]+)"', "w": CARD, "h": CARD, "scale": SCALE,
     "hash": "sheet", "strip": r"<!--th-->.*?<!--/th-->", "ext": ".png"},
    {"name": "youtube", "card_re": r'class="th-card[^"]*" id="th-card-\d+" data-file="([^"]+)"', "w": 1280, "h": 720, "scale": 1,
     "hash": "thumbs", "strip": r"<!--tz-->.*?<!--/tz-->", "ext": ".png", "max": YT_MAX},
    # the sponsor cards (third tab, 2026-10-07): their own slots in their own section, upcoming events only like the teasers
    {"name": "sponsor", "card_re": r'class="tz-card sp-card" id="sp-card-[a-z]+-\d+" data-file="([^"]+)"', "w": CARD, "h": CARD, "scale": SCALE,
     "hash": "spcards", "strip": r"(?!)", "ext": ".png", "slot": "sp-slot"},
]
BUDGET_MS = 12000    # virtual time for fonts + images to settle before the screenshot

CACHE_DIR = os.path.join(os.environ.get("SITE_CACHE_DIR", ".cache"), "teasers")
PRUNE_DAYS = 14      # --prune removes entries not used for this long
with open(__file__, "rb") as _f:
    SCRIPT_HASH = hashlib.sha256(_f.read()).hexdigest()[:16]
FROZEN = "frozen-v1"   # past events' thumbnail keys: text + headshot only (bump to redraw every past thumbnail once)
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


def card_files(index_html, kind=KINDS[0]):
    with open(index_html, encoding="utf-8") as f:
        return re.findall(kind["card_re"], f.read())


def page_parts(index_html, slot="tz-slot"):
    """Split the teaser page into (context, [card html, ...]): each card is its .tz-slot block (data- attributes since 2026-10-04) up to the
    next one; the context is everything else (head, styles, controls, scripts). The sponsor-card section (<!--spsec-->) is left out
    of the talk cards and their context; for slot="sp-slot" the cards are that section's .sp-slot blocks."""
    with open(index_html, encoding="utf-8") as f:
        html = f.read()
    if slot == "tz-slot":
        html = re.sub(r"<!--spsec-->.*?<!--/spsec-->", "", html, flags=re.S)
    starts = [m.start() for m in re.finditer(r'<div class="%s"[ >]' % slot, html)]
    if not starts:
        return html, []
    end = html.find("<!--/spsec-->" if slot == "sp-slot" else "<script", starts[-1])
    end = len(html) if end < 0 else end
    bounds = starts + [end]
    cards = [re.sub(r' id="(?:t[zh]-card-\d+|sp-card-[a-z]+-\d+)"', "", html[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]
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


def render_context(context):
    """What of the page outside the cards can change a picture: its styles without the tools' block, its <link>s
    (fonts) and the card script. None when the page has no /*render*/ markers (an old template: whole context)."""
    script = re.search(r"/\*render\*/(.*?)/\*/render\*/", context, re.S)
    if not script:
        return None
    styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", context, re.S))
    styles = re.sub(r"/\*tools\*/.*?/\*/tools\*/", "", styles, flags=re.S)
    links = "".join(re.findall(r"<link[^>]+>", context))
    return styles + links + script.group(1)


def frozen_key(card, base_dir, kind):
    """A past event's picture: what its talks.csv row puts on it (visible text, the speaker it is named after) and its
    headshot's bytes - nothing else. A thumbnail shows no text, so a title edit leaves it as it is (it would look the same)."""
    h = hashlib.sha256(("%s|%s|%dx%d|" % (FROZEN, kind["name"], kind["w"], kind["h"])).encode())
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", "", card, flags=re.S))).strip()
    h.update(text.encode())
    h.update("|".join(re.findall(r'data-file="([^"]+)"', card)).encode())   # the speaker it is named after (a thumbnail has no text)
    shots = " ".join('src="%s"' % m for m in re.findall(r'src="([^"]*speakers/[^"]+)"', card))
    refs_digest(shots, base_dir, h)
    return h.hexdigest()


def card_keys(index_html, chrome_version, kind=KINDS[0], frozen=False):
    context, cards = page_parts(index_html, kind.get("slot", "tz-slot"))
    cards = [re.sub(kind["strip"], "", c, flags=re.S) for c in cards]   # the other kind's markup does not change this picture
    base_dir = os.path.dirname(index_html)
    if frozen:
        return [frozen_key(c, base_dir, kind) for c in cards]
    outputs = set().union(*(card_files(index_html, k) for k in KINDS))
    if kind["name"] == "youtube":
        # a thumbnail shows the brand lettering and the speaker's headshot, nothing of the talk's text (Marek 2026-10-09:
        # "what matters is was the speaker added / removed, and what's their latest picture"): its key is its own card
        # markup (the speaker, the photo, the lettering, the background - without the slot's title / track / order data),
        # the thumbnail styles and the lettering fit, and the files it uses
        cards = [re.sub(r'^<div class="tz-slot"[^>]*>', "", c) for c in cards]
        styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", context, re.S))
        fit = re.search(r"function fitWord\(el\) \{.*?\n      \}", context, re.S)
        context = "\n".join(r for r in re.findall(r"[^{}]*\{[^{}]*\}", styles) if ".th-" in r or "th-sheet" in r) + (fit.group(0) if fit else context)
    else:
        context = render_context(context) or context
    ctx = hashlib.sha256()
    ctx.update(("%s|%s|%s|%dx%d|%s|%d|" % (SCRIPT_HASH, chrome_version, kind["name"], kind["w"], kind["h"], kind["scale"], BUDGET_MS)).encode())
    ctx.update(context.encode())
    refs_digest(context, base_dir, ctx, outputs)
    keys = []
    for card in cards:
        h = ctx.copy()
        h.update(card.encode())
        refs_digest(card, base_dir, h, outputs)
        keys.append(h.hexdigest())
    return keys


HD_NAME = "_render-hd.html"


def hd_page(page):
    """<page> with every ../../speakers/<file> it uses pointed at the original <repo>/speakers/<file> when that
    exists, written next to it as _render-hd.html (same folder, so every other relative path still works) and
    removed again by main(). Falls back to <page> when there is nothing to swap."""
    originals = os.path.abspath("speakers")
    if not os.path.isdir(originals):
        return page
    with open(page, encoding="utf-8") as f:
        html = f.read()

    def swap(m):
        name = m.group(2)
        hd = os.path.join(originals, name)
        return m.group(1) + hd.replace("\\", "/") + '"' if os.path.isfile(hd) else m.group(0)

    out = re.sub(r'(src=")\.\./\.\./speakers/([^"]+)"', swap, html)
    if out == html:
        return page
    path = os.path.join(os.path.dirname(page), HD_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return path


def runs_of(indices):
    """Consecutive runs of card indices, each at most CHUNK long (one screenshot each)."""
    runs = []
    for i in indices:
        if runs and runs[-1][-1] == i - 1 and len(runs[-1]) < CHUNK:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def save_tile(tile, out, kind):
    tile.save(out, "PNG", optimize=True)
    if kind.get("max") and os.path.getsize(out) > kind["max"]:   # YouTube's 2 MB: fall back to a 256-colour PNG
        tile.quantize(256, dither=Image.FLOYDSTEINBERG).save(out, "PNG", optimize=True)


def shoot(chrome, url, height, out_png, width=CARD, scale=SCALE):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox", "--disable-dev-shm-usage",
           "--allow-file-access-from-files", "--force-device-scale-factor=%s" % scale,
           "--window-size=%d,%d" % (width, height), "--virtual-time-budget=%d" % BUDGET_MS,
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
    cached_only = "--cached-only" in sys.argv
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 1
    missing_total = 0
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
    for page in pages:
        event_dir = os.path.dirname(os.path.dirname(page))
        event = os.path.basename(event_dir)
        upcoming = render_all or event_is_upcoming(event_dir)
        events += 1
        if not any(card_files(page, k) for k in KINDS):
            print("render_teasers %s: no cards" % event)
            continue
        out_dir = os.path.dirname(page)
        # Headshots from the full-size originals in <repo>/speakers (1000x1000), not static/speakers, which
        # optimize_images.py shrank to 400 px for the website: a card draws the photo ~585 px wide in the 1500 px
        # PNG, so the 400 px copy came out mushy (Marek 2026-10-06). The page itself keeps the light copies; only
        # this render copy of it points its photos at the originals (and the cache key hashes those bytes).
        page = hd_page(page)
        url = "file:///" + os.path.abspath(page).replace("\\", "/")
        for kind in (KINDS if upcoming else [k for k in KINDS if k["name"] == "youtube"]):   # a past event: its YouTube thumbnails only
            files = card_files(page, kind)
            if not files:
                continue
            kw, kh = int(round(kind["w"] * kind["scale"])), int(round(kind["h"] * kind["scale"]))
            keys = card_keys(page, chrome_version, kind, frozen=not upcoming)
            if len(keys) != len(files):  # page layout not understood: render everything, cache nothing
                print("WARN render_teasers %s: %d %s pictures but %d slots, not caching" % (event, len(files), kind["name"], len(keys)))
                keys = [None] * len(files)
            done, reused, missing = 0, 0, []
            for i, (name, key) in enumerate(zip(files, keys)):
                cached = key and os.path.join(CACHE_DIR, key + kind["ext"])
                if key:
                    used.add(key + kind["ext"])
                if cached and os.path.exists(cached):
                    shutil.copyfile(cached, os.path.join(out_dir, name))
                    os.utime(cached)  # last used now: --prune keeps it
                    reused += 1
                    done += 1
                else:
                    missing.append(i)
            if cached_only:          # the deploy: what is not cached yet stays absent, the render job draws it
                missing_total += len(missing)
                total += done
                reused_total += reused
                print("render_teasers %s: %d/%d %s pictures from cache, %d left for the render job" % (event, done, len(files), kind["name"], len(missing)))
                continue
            with tempfile.TemporaryDirectory() as tmp:
                runs = runs_of(missing)

                def take(run):
                    shot = os.path.join(tmp, "sheet-%d.png" % run[0])
                    return run, shot, shoot(chrome, "%s#%s-%d-%d" % (url, kind["hash"], run[0], len(run)), kind["h"] * len(run), shot,
                                            width=kind["w"], scale=kind["scale"])
                with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
                    shots = list(pool.map(take, runs))
                for run, shot, (ok, err) in shots:
                    start = run[0]
                    if not ok:
                        print("WARN render_teasers %s: %s screenshot failed for %d-%d %s" % (event, kind["name"], start, run[-1], err))
                        continue
                    img = Image.open(shot)
                    if img.width < kw or img.height < kh * len(run):
                        print("WARN render_teasers %s: screenshot %dx%d smaller than expected %dx%d" % (event, img.width, img.height, kw, kh * len(run)))
                    for j, i in enumerate(run):
                        out = os.path.join(out_dir, files[i])
                        tile = img.crop((0, j * kh, kw, (j + 1) * kh)).convert("RGB")
                        if tile.convert("L").getextrema()[1] < 12:   # the page blanked it: its slime is broken - never publish
                            print("WARN render_teasers %s: %s %s failed the slime check, not published" % (event, kind["name"], files[i]))
                            continue
                        save_tile(tile, out, kind)
                        done += 1
                        if keys[i]:
                            os.makedirs(CACHE_DIR, exist_ok=True)
                            shutil.copyfile(out, os.path.join(CACHE_DIR, keys[i] + kind["ext"]))
            rendered_total += done - reused
            reused_total += reused
            total += done
            print("render_teasers %s: %d/%d %s pictures (%d reused, %d rendered)" % (event, done, len(files), kind["name"], reused, done - reused))
        if os.path.basename(page) == HD_NAME:
            os.remove(page)
    print("render_teasers: %d pictures in %d event(s), %d teaser page(s) found; %d reused from cache, %d rendered%s"
          % (total, events, len(pages), reused_total, rendered_total, ", %d left for the render job" % missing_total if cached_only else ""))
    if os.environ.get("GITHUB_OUTPUT"):   # the render job queues one more deploy only when it drew something
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("rendered=%d\nmissing=%d\n" % (rendered_total, missing_total))
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
    finally:                # a render copy must never be deployed, whatever happened above
        _root = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else "static"
        for _left in glob.glob(os.path.join(_root, "20*", "teasers", HD_NAME)):
            os.remove(_left)
