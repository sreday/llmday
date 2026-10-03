#!/usr/bin/env python3
"""Optimize images in static/ directory for web deployment.

Content-addressed cache (2026-09-30): an image whose bytes and settings were
optimized before is not re-encoded. The optimized bytes are kept in
.cache/images/<sha256 of this script, the settings, the Pillow version and the
source bytes>, so
a replaced image (even under the same name) always misses, and the 35 copies
of each sponsor logo cost one encode. CI keeps .cache/ between runs with
actions/cache; --prune drops entries unused for 14 days.
"""

import hashlib
import os
import sys
from pathlib import Path
import PIL
from PIL import Image

STATIC_DIR = Path("static")

MAX_PROFILE_SIZE = (400, 400)   # speakers, ambassadors
MAX_LOGO_WIDTH = 400            # sponsor logos
MAX_PHOTO_WIDTH = 1200          # event photos, venue
MAX_CARD_WIDTH = 800            # event card images
MAX_STICKER = (600, 600)        # brand stickers/logos (shown at <= 300 px)
MAX_IMAGE_WIDTH = 1200          # other site images (mascots, ambassadorship art, host logos)
MAX_HERO_WIDTH = 1600           # hero/slideshow photos and painted backgrounds (darkened, full-width)
JPEG_QUALITY = 85
WEBP_QUALITY = 78               # WebP siblings; kept only when >= 10% smaller than the original
WEBP_QUALITY_HERO = 72          # photos/: big backgrounds under a dark overlay
WEBP_SKIP = ("favicon", "apple-touch", "android-chrome", "/teasers/")
MIN_FILE_SIZE = 10 * 1024       # skip files under 10KB
# SREday 2022-2024 archives are frozen: the steps added for page weight (resizes, WebP, posters)
# leave their static/ output alone (rewrite_assets.py skips them too)
FROZEN_EVENTS = ("2022-", "2023-", "2024-")


def frozen(path):
    return path.relative_to(STATIC_DIR).parts[0].startswith(FROZEN_EVENTS)

CACHE_DIR = Path(os.environ.get("SITE_CACHE_DIR", ".cache")) / "images"
# Any change to this script (sizes, quality, code) invalidates every entry.
SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
_used = set()
_stats = {"hit": 0, "miss": 0}


def cached(settings, optimize):
    """Wrap an in-place optimizer so identical input + settings reuse the stored result."""
    def run(path):
        if path.stat().st_size < MIN_FILE_SIZE:
            return
        data = path.read_bytes()
        key = hashlib.sha256(("%s|%s|%s|" % (SCRIPT_HASH, settings, PIL.__version__)).encode() + data).hexdigest()
        entry = CACHE_DIR / key
        _used.add(key)
        if entry.exists():
            path.write_bytes(entry.read_bytes())
            os.utime(entry)  # last used now: --prune keeps it
            _stats["hit"] += 1
            return
        optimize(path)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_bytes(path.read_bytes())
        tmp.replace(entry)
        _stats["miss"] += 1
    return run


PRUNE_DAYS = 14  # --prune removes entries not used for this long


def prune_cache():
    """Delete cache entries unused for PRUNE_DAYS (not merely unused by this run, so a Pillow upgrade
    rolling out unevenly across runners doesn't wipe the other version's entries every run)."""
    if not CACHE_DIR.exists():
        return
    import time
    cutoff = time.time() - PRUNE_DAYS * 86400
    removed = 0
    for entry in CACHE_DIR.iterdir():
        if entry.name not in _used and entry.stat().st_mtime < cutoff:
            entry.unlink()
            removed += 1
    print(f"Image cache: pruned {removed} entries unused for {PRUNE_DAYS} days")


def optimize_png(path, max_size, keep_alpha=True):
    """Resize and optimize a PNG file in-place."""
    if path.stat().st_size < MIN_FILE_SIZE:
        return
    try:
        img = Image.open(path)
        original_size = path.stat().st_size
        img.thumbnail(max_size, Image.LANCZOS)
        img.save(path, "PNG", optimize=True)
        new_size = path.stat().st_size
        print(f"  {path}: {original_size // 1024}KB -> {new_size // 1024}KB")
    except Exception as e:
        print(f"  WARNING: {path}: {e}")


def optimize_jpeg(path, max_width):
    """Resize and optimize a JPEG file in-place."""
    if path.stat().st_size < MIN_FILE_SIZE:
        return
    try:
        img = Image.open(path)
        original_size = path.stat().st_size
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
        new_size = path.stat().st_size
        print(f"  {path}: {original_size // 1024}KB -> {new_size // 1024}KB")
    except Exception as e:
        print(f"  WARNING: {path}: {e}")


def make_webp(path):
    """Write <name>.webp next to an optimized PNG/JPEG (cached like the in-place steps).

    _build/rewrite_assets.py then points src/srcset/url() references at the .webp; the
    original stays for og:image tags and old links. No .webp when it isn't >= 10% smaller.
    """
    if path.stat().st_size < MIN_FILE_SIZE or any(s in str(path) for s in WEBP_SKIP):
        return
    data = path.read_bytes()
    quality = WEBP_QUALITY_HERO if "photos" in path.parts else WEBP_QUALITY
    key = hashlib.sha256(("%s|webp|%s|%s|" % (SCRIPT_HASH, quality, PIL.__version__)).encode() + data).hexdigest()
    entry = CACHE_DIR / key
    _used.add(key)
    out = path.with_suffix(".webp")
    if entry.exists():
        os.utime(entry)
        _stats["hit"] += 1
    else:
        try:
            img = Image.open(path)
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "PA") else "RGB")
            import io
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=quality, method=4)
            webp = buf.getvalue()
        except Exception as e:
            print(f"  WARNING: {path}: {e}")
            return
        # an empty entry records "WebP not worth it" so it isn't retried every run
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_bytes(webp if len(webp) <= 0.9 * len(data) else b"")
        tmp.replace(entry)
        _stats["miss"] += 1
    webp = entry.read_bytes()
    if webp:
        out.write_bytes(webp)
    elif out.exists():
        out.unlink()


def make_poster(gif):
    """<name>.poster.webp (first frame, alpha kept) for a GIF that has a .mp4 or .webm sibling:
    rewrite_assets.py turns such GIFs into lazy <video> loops and shows this until the video plays."""
    if not (gif.with_suffix(".mp4").exists() or gif.with_suffix(".webm").exists()):
        return
    data = gif.read_bytes()
    key = hashlib.sha256(("%s|poster|%s|" % (SCRIPT_HASH, PIL.__version__)).encode() + data).hexdigest()
    entry = CACHE_DIR / key
    _used.add(key)
    if entry.exists():
        os.utime(entry)
        _stats["hit"] += 1
    else:
        import io
        img = Image.open(gif)
        img.seek(0)
        frame = img.convert("RGBA")
        frame.thumbnail((480, 480), Image.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(entry)
        _stats["miss"] += 1
    gif.with_name(gif.stem + ".poster.webp").write_bytes(entry.read_bytes())


def process_files(pattern, handler, label, skip_frozen=False):
    """Find files matching glob pattern under STATIC_DIR and process them."""
    files = sorted(f for f in STATIC_DIR.glob(pattern) if not (skip_frozen and frozen(f)))
    if not files:
        return 0, 0
    print(f"\n{label} ({len(files)} files)...")
    before = sum(f.stat().st_size for f in files)
    for f in files:
        handler(f)
    after = sum(f.stat().st_size for f in files)
    return before, after


def main():
    if not STATIC_DIR.exists():
        print("No static/ directory found, skipping optimization")
        return

    total_before = 0
    total_after = 0

    groups = [
        ("speakers/*.png",                   cached("png|400x400", lambda f: optimize_png(f, MAX_PROFILE_SIZE)), "Speaker photos"),
        ("ambassadors/*.png",                cached("png|400x400", lambda f: optimize_png(f, MAX_PROFILE_SIZE)), "Ambassador photos"),
        ("sponsors/*.png",                   cached("png|400x9999", lambda f: optimize_png(f, (MAX_LOGO_WIDTH, 9999))), "Sponsor logos"),
        ("20*/sponsors/*.png",               cached("png|400x9999", lambda f: optimize_png(f, (MAX_LOGO_WIDTH, 9999))), "Per-event sponsor logos"),
        ("assets/images/events/*.png",       cached("png|800x9999", lambda f: optimize_png(f, (MAX_CARD_WIDTH, 9999))), "Event card images (png)"),
        ("assets/images/events/*.jpg",       cached("jpeg|800|q85", lambda f: optimize_jpeg(f, MAX_CARD_WIDTH)), "Event card images (jpg)"),
        ("assets/images/events/*.jpeg",      cached("jpeg|800|q85", lambda f: optimize_jpeg(f, MAX_CARD_WIDTH)), "Event card images (jpeg)"),
        ("20*/assets/images/venue/*.jpg",    cached("jpeg|1200|q85", lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH)), "Venue photos (jpg)"),
        ("20*/assets/images/venue/*.jpeg",   cached("jpeg|1200|q85", lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH)), "Venue photos (jpeg)"),
        # Brand stickers and logos were served at full size (sreday_sticker.png 2000x2000, 3-5 MB)
        ("assets/images/*sticker*.png",      cached("png|600x600", lambda f: optimize_png(f, MAX_STICKER)), "Stickers"),
        ("assets/images/*logo*.png",         cached("png|600x600", lambda f: optimize_png(f, MAX_STICKER)), "Logos"),
        ("20*/assets/images/*sticker*.png",  cached("png|600x600", lambda f: optimize_png(f, MAX_STICKER)), "Per-event stickers"),
        ("20*/assets/images/*logo*.png",     cached("png|600x600", lambda f: optimize_png(f, MAX_STICKER)), "Per-event logos"),
        ("assets/images/profiles/*.png",     cached("png|400x400", lambda f: optimize_png(f, MAX_PROFILE_SIZE)), "Profile photos"),
        ("assets/images/*/*.png",            cached("png|1200x9999", lambda f: optimize_png(f, (MAX_IMAGE_WIDTH, 9999))), "Site images (png)"),
        ("assets/images/*.png",              cached("png|1200x9999", lambda f: optimize_png(f, (MAX_IMAGE_WIDTH, 9999))), "Other site images (png)"),
        ("photos/*.png",                     cached("png|1600x9999", lambda f: optimize_png(f, (MAX_HERO_WIDTH, 9999))), "Painted backgrounds (png)"),
        ("photos/*.jpg",                     cached("jpeg|1600|q85", lambda f: optimize_jpeg(f, MAX_HERO_WIDTH)), "Hero photos resized (jpg)"),
        ("photos/*.jpeg",                    cached("jpeg|1600|q85", lambda f: optimize_jpeg(f, MAX_HERO_WIDTH)), "Hero photos resized (jpeg)"),
        ("assets/images/*.jpg",              cached("jpeg|1600|q85", lambda f: optimize_jpeg(f, MAX_HERO_WIDTH)), "Site images (jpg)"),
        ("20*/assets/images/*.jpg",          cached("jpeg|1600|q85", lambda f: optimize_jpeg(f, MAX_HERO_WIDTH)), "Per-event images (jpg)"),
    ]

    for pattern, handler, label in groups:
        # per-event sponsor logos and venue photos were optimized before the archives froze; the newer steps skip them
        legacy = pattern.startswith("20*/sponsors/") or "/venue/" in pattern
        before, after = process_files(pattern, handler, label, skip_frozen=not legacy)
        total_before += before
        total_after += after

    webp_sources = [f for ext in ("png", "jpg", "jpeg") for f in STATIC_DIR.rglob(f"*.{ext}") if not frozen(f)]
    print(f"\nWebP siblings ({len(webp_sources)} candidates)...")
    for f in webp_sources:
        make_webp(f)

    for gif in STATIC_DIR.rglob("*.gif"):
        if not frozen(gif):
            make_poster(gif)

    print(f"\nImage cache: {_stats['hit']} reused, {_stats['miss']} optimized")
    if "--prune" in sys.argv:
        prune_cache()

    if total_before > 0:
        saved = total_before - total_after
        print(f"\n{'=' * 60}")
        print(f"Total: {total_before // 1024 // 1024}MB -> {total_after // 1024 // 1024}MB "
              f"(saved {saved // 1024 // 1024}MB, {saved * 100 // total_before}%)")
    else:
        print("\nNo images found to optimize.")


if __name__ == "__main__":
    main()
