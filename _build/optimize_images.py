#!/usr/bin/env python3
"""Optimize images in static/ directory for web deployment.

Content-addressed cache (2026-09-30): an image whose bytes and settings were
optimized before is not re-encoded. The optimized bytes are kept in
.cache/images/<sha256 of this script, the settings, the Pillow version and the
source bytes>, so
a replaced image (even under the same name) always misses, and the 35 copies
of each sponsor logo cost one encode. CI keeps .cache/ between runs with
actions/cache; --prune drops entries this run didn't use.
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
JPEG_QUALITY = 85
MIN_FILE_SIZE = 10 * 1024       # skip files under 10KB

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
            _stats["hit"] += 1
            return
        optimize(path)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(".tmp")
        tmp.write_bytes(path.read_bytes())
        tmp.replace(entry)
        _stats["miss"] += 1
    return run


def prune_cache():
    """Delete cache entries this run didn't use, so the cache holds only the current site."""
    if not CACHE_DIR.exists():
        return
    removed = 0
    for entry in CACHE_DIR.iterdir():
        if entry.name not in _used:
            entry.unlink()
            removed += 1
    print(f"Image cache: pruned {removed} unused entries")


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


def optimize_jpeg_quality_only(path):
    """Reduce JPEG quality without resizing (used for hero/slideshow images)."""
    if path.stat().st_size < MIN_FILE_SIZE:
        return
    try:
        img = Image.open(path)
        original_size = path.stat().st_size
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
        new_size = path.stat().st_size
        print(f"  {path}: {original_size // 1024}KB -> {new_size // 1024}KB")
    except Exception as e:
        print(f"  WARNING: {path}: {e}")


def process_files(pattern, handler, label):
    """Find files matching glob pattern under STATIC_DIR and process them."""
    files = sorted(STATIC_DIR.glob(pattern))
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
        ("photos/*.jpg",                     cached("jpegq|q85", optimize_jpeg_quality_only), "Hero/slideshow photos (jpg)"),
        ("photos/*.jpeg",                    cached("jpegq|q85", optimize_jpeg_quality_only), "Hero/slideshow photos (jpeg)"),
        ("20*/assets/images/venue/*.jpg",    cached("jpeg|1200|q85", lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH)), "Venue photos (jpg)"),
        ("20*/assets/images/venue/*.jpeg",   cached("jpeg|1200|q85", lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH)), "Venue photos (jpeg)"),
    ]

    for pattern, handler, label in groups:
        before, after = process_files(pattern, handler, label)
        total_before += before
        total_after += after

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
