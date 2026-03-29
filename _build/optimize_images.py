#!/usr/bin/env python3
"""Optimize images in static/ directory for web deployment."""

import sys
from pathlib import Path
from PIL import Image

STATIC_DIR = Path("static")

MAX_PROFILE_SIZE = (400, 400)   # speakers, ambassadors
MAX_LOGO_WIDTH = 400            # sponsor logos
MAX_PHOTO_WIDTH = 1200          # event photos, venue
MAX_CARD_WIDTH = 800            # event card images
JPEG_QUALITY = 85
MIN_FILE_SIZE = 10 * 1024       # skip files under 10KB


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
        ("speakers/*.png",                   lambda f: optimize_png(f, MAX_PROFILE_SIZE), "Speaker photos"),
        ("ambassadors/*.png",                lambda f: optimize_png(f, MAX_PROFILE_SIZE), "Ambassador photos"),
        ("sponsors/*.png",                   lambda f: optimize_png(f, (MAX_LOGO_WIDTH, 9999)), "Sponsor logos"),
        ("20*/sponsors/*.png",               lambda f: optimize_png(f, (MAX_LOGO_WIDTH, 9999)), "Per-event sponsor logos"),
        ("assets/images/events/*.png",       lambda f: optimize_png(f, (MAX_CARD_WIDTH, 9999)), "Event card images (png)"),
        ("assets/images/events/*.jpg",       lambda f: optimize_jpeg(f, MAX_CARD_WIDTH), "Event card images (jpg)"),
        ("assets/images/events/*.jpeg",      lambda f: optimize_jpeg(f, MAX_CARD_WIDTH), "Event card images (jpeg)"),
        ("photos/*.jpg",                     optimize_jpeg_quality_only, "Hero/slideshow photos (jpg)"),
        ("photos/*.jpeg",                    optimize_jpeg_quality_only, "Hero/slideshow photos (jpeg)"),
        ("20*/assets/images/venue/*.jpg",    lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH), "Venue photos (jpg)"),
        ("20*/assets/images/venue/*.jpeg",   lambda f: optimize_jpeg(f, MAX_PHOTO_WIDTH), "Venue photos (jpeg)"),
    ]

    for pattern, handler, label in groups:
        before, after = process_files(pattern, handler, label)
        total_before += before
        total_after += after

    if total_before > 0:
        saved = total_before - total_after
        print(f"\n{'=' * 60}")
        print(f"Total: {total_before // 1024 // 1024}MB -> {total_after // 1024 // 1024}MB "
              f"(saved {saved // 1024 // 1024}MB, {saved * 100 // total_before}%)")
    else:
        print("\nNo images found to optimize.")


if __name__ == "__main__":
    main()
