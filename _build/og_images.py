#!/usr/bin/env python3
"""Make every page's link preview (WhatsApp, LinkedIn, X, Slack, iMessage, Facebook, Teams...) show up every time (2026-10-08).

Runs after render_teasers.py (the talk pages' og:image is the talk's YouTube thumbnail it renders) and before
rewrite_assets.py, from the repo root:
    python _build/og_images.py [--root static] [--prune]

For every static/**/*.html with an og:image it:
  1. makes a preview copy of the picture: a baseline JPEG 1200 px wide (smaller pictures are scaled up, so LinkedIn and
     Facebook show the big card), at most 1200 px tall, no transparency, under 250 KB (WhatsApp drops bigger ones);
     saved as static/og/<content hash>.jpg, so a changed picture gets a new URL and every platform re-fetches it, and
     an unchanged one keeps its URL (and every cached preview stays valid);
  2. falls back to its event's picture (the event index.html's og:image), then to the brand's home page picture, when
     the picture is missing (a thumbnail render failed, a typo): a preview never points at a 404;
  3. rewrites the page's Open Graph / Twitter tags as one block right after <title> (some crawlers read only the start
     of the page): og:title, og:description, og:type, og:url, og:site_name, og:locale, og:image (+ secure_url, type,
     width, height, alt), twitter:card summary_large_image, twitter:site/creator, twitter:title, twitter:description,
     twitter:image (+ alt). Titles and descriptions come from the page's own tags; nothing else in the page changes.

Content-addressed cache: .cache/og/<key>.jpg (key = this script + the source bytes), kept between CI runs with the
rest of .cache; --prune drops entries unused for 14 days. Never fails the build: problems are printed as WARN.
"""
import glob
import hashlib
import html
import io
import os
import re
import shutil
import sys
import time
from urllib.parse import quote, unquote, urlsplit

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("WARN og_images: Pillow missing, previews left as they are")
    sys.exit(0)

WIDTH = 1200                 # LinkedIn / Facebook big-card width
MAX_H = 1200                 # taller pictures are cropped to a square (centre)
MAX_BYTES = 250 * 1024       # WhatsApp skips previews above ~300 KB
QUALITIES = (86, 82, 78, 74, 70, 65, 60, 55)
CACHE_DIR = os.path.join(os.environ.get("SITE_CACHE_DIR", ".cache"), "og")
PRUNE_DAYS = 14
SITE_NAMES = {"sreday.com": "SREday", "llmday.com": "LLMday", "platformday.com": "PLATFORMday",
              "promptengineering.rocks": "Prompt Engineering Conference"}
with open(__file__, "rb") as _f:
    SCRIPT_HASH = hashlib.sha1(_f.read()).hexdigest()[:10]

META_RE = re.compile(r'[ \t]*<meta\b[^>]*\b(?:property|name)\s*=\s*"(?:og:|twitter:|image\b)[^"]*"[^>]*>[ \t]*\n?', re.I)
TITLE_RE = re.compile(r'<title>.*?</title>[ \t]*\n?', re.I | re.S)


def attr(tag, name):
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % name, tag, re.I)
    return html.unescape(m.group(1)) if m else ""


def read_tags(head):
    """{'og:title': value, ...} from a page head (first wins); 'image' (name="image") counts as og:image."""
    tags = {}
    for m in META_RE.finditer(head):
        t = m.group(0)
        key = (attr(t, "property") or attr(t, "name")).lower()
        if key == "image":
            key = "og:image"
        tags.setdefault(key, attr(t, "content"))
    return tags


def local_file(root, url, page_dir):
    """The file under static/ an image URL points at (own domain, root-relative or page-relative), or ''."""
    if not url:
        return ""
    parts = urlsplit(url)
    path = unquote(parts.path)
    if parts.netloc:
        if parts.netloc.lower().removeprefix("www.") not in SITE_NAMES:
            return ""
        f = os.path.join(root, path.lstrip("/"))
    elif path.startswith("/"):
        f = os.path.join(root, path.lstrip("/"))
    else:
        f = os.path.normpath(os.path.join(page_dir, path))
    return f if os.path.isfile(f) else ""


def make_preview(src, out_dir):
    """static/og/<hash>.jpg for src; returns (name, width, height) or None."""
    with open(src, "rb") as f:
        data = f.read()
    key = hashlib.sha1(data + SCRIPT_HASH.encode()).hexdigest()[:16]
    cached = os.path.join(CACHE_DIR, key + ".jpg")
    if os.path.isfile(cached):
        os.utime(cached)
        with Image.open(cached) as im:
            size = im.size
        name = key + ".jpg"
        shutil.copyfile(cached, os.path.join(out_dir, name))
        return name, size[0], size[1]
    im = Image.open(io.BytesIO(data))
    im.seek(0)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (17, 17, 17))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    else:
        im = im.convert("RGB")
    w, h = im.size
    if h > w:                                                   # portrait: centre square
        top = (h - w) // 2
        im, h = im.crop((0, top, w, top + w)), w
    nh = round(h * WIDTH / w)
    im = im.resize((WIDTH, nh), Image.LANCZOS)
    if nh > MAX_H:
        top = (nh - MAX_H) // 2
        im, nh = im.crop((0, top, WIDTH, top + MAX_H)), MAX_H
    buf = b""
    for q in QUALITIES:
        out = io.BytesIO()
        im.save(out, "JPEG", quality=q, optimize=True, progressive=False)
        buf = out.getvalue()
        if len(buf) <= MAX_BYTES:
            break
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cached, "wb") as f:
        f.write(buf)
    name = key + ".jpg"
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(buf)
    return name, WIDTH, nh


def page_url(root, path, host):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if rel == "index.html":
        rel = ""
    elif rel.endswith("/index.html"):
        rel = rel[: -len("index.html")]
    elif rel.endswith(".html"):
        rel = rel[: -len(".html")]
    return "https://%s/%s" % (host, quote(rel))


def esc(s):
    return html.escape(re.sub(r"\s+", " ", s or "").strip(), quote=True)


def main():
    args = sys.argv[1:]
    root = args[args.index("--root") + 1] if "--root" in args else "static"
    if "--prune" in args and os.path.isdir(CACHE_DIR):
        cutoff = time.time() - PRUNE_DAYS * 86400
        for f in glob.glob(os.path.join(CACHE_DIR, "*.jpg")):
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
    out_dir = os.path.join(root, "og")
    os.makedirs(out_dir, exist_ok=True)
    pages = sorted(glob.glob(os.path.join(root, "**", "*.html"), recursive=True))
    heads = {}
    for p in pages:
        with open(p, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        low = txt.lower()
        end = low.find("</head>")
        if end < 0:                                             # an unclosed <head>: it ends where <body> starts
            m = re.search(r"<body[\s>]", low)
            end = m.start() if m else -1
        if end > 0:
            heads[p] = (txt, end)
    # fallbacks: each event folder's own picture, then the home page's
    home_src = ""
    if os.path.join(root, "index.html") in heads:
        t, e = heads[os.path.join(root, "index.html")]
        home_src = local_file(root, read_tags(t[:e]).get("og:image", ""), root)
    home_tags = read_tags(heads[os.path.join(root, "index.html")][0]) if os.path.join(root, "index.html") in heads else {}
    any_host = re.compile(r'https://(?:www\.)?(%s)/' % "|".join(map(re.escape, SITE_NAMES)))
    site_host = ""                                              # the repo's own domain, from any page that names it
    for txt, end in heads.values():
        m = any_host.search(txt[:end])
        if m:
            site_host = m.group(1)
            break

    def event_src(p):
        ev = os.path.join(root, os.path.relpath(p, root).split(os.sep)[0], "index.html")
        if ev in heads and ev != p:
            t, e = heads[ev]
            return local_file(root, read_tags(t[:e]).get("og:image", ""), os.path.dirname(ev))
        return ""

    # redirect stubs (renamed events: a meta refresh + script, which crawlers don't run) take their target page's tags
    refresh_re = re.compile(r'<meta\s+http-equiv="refresh"\s+content="\d+;\s*url=([^"]+)"', re.I)
    redirects = {}
    for p, (txt, end) in heads.items():
        m = refresh_re.search(txt[:end])
        if m:
            tgt = urlsplit(html.unescape(m.group(1))).path
            tf = os.path.join(root, tgt.lstrip("/")) if tgt.startswith("/") else os.path.normpath(os.path.join(os.path.dirname(p), tgt))
            if tf.endswith("/") or os.path.isdir(tf):
                tf = os.path.join(tf, "index.html")
            elif not tf.endswith(".html") and os.path.isfile(tf + ".html"):
                tf += ".html"
            redirects[p] = tf if tf in heads and tf != p else ""
    done, previews, fixed, fell_back, missing = {}, 0, 0, 0, 0
    for p in [q for q in heads if q not in redirects] + [q for q in heads if q in redirects]:
        txt, end = heads[p]
        head = txt[:end]
        tags = read_tags(head)
        url_page = p
        if redirects.get(p):
            t, e = heads[redirects[p]]
            tags, url_page = read_tags(t[:e]), redirects[p]       # already rewritten: absolute /og/ picture
        img = tags.get("og:image") or tags.get("twitter:image") or ""
        if not tags.get("og:title") and not tags.get("twitter:title"):
            m = TITLE_RE.search(head)
            if m:
                tags["og:title"] = html.unescape(re.sub(r"</?title>", "", m.group(0), flags=re.I)).strip()
        if not tags.get("og:description"):
            m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', head, re.I)
            if m:
                tags["og:description"] = html.unescape(m.group(1))
        for k in ("twitter:site", "twitter:creator"):
            tags.setdefault(k, home_tags.get(k, ""))
        host = (urlsplit(img).netloc or "").lower().removeprefix("www.")
        if host not in SITE_NAMES:
            m = any_host.search(head)
            host = m.group(1) if m else site_host
        if not host:
            continue
        src = local_file(root, img, os.path.dirname(p))
        if not src:
            src = event_src(p) or home_src
            if src and img:
                fell_back += 1
                print("WARN og_images: %s: %s missing, using %s" % (p, img, src))
            elif not src:
                missing += 1
                print("WARN og_images: %s: %s no picture to use, left as is" % (p, img or "no og:image,"))
                continue
        if src not in done and os.path.dirname(os.path.abspath(src)) == os.path.abspath(out_dir):
            with Image.open(src) as _im:                        # a redirect's target, already made: same picture, same URL
                done[src] = (os.path.basename(src),) + _im.size
        if src not in done:
            try:
                done[src] = make_preview(src, out_dir)
                previews += 1
            except Exception as e:                              # noqa: BLE001 - never fail the build
                print("WARN og_images: %s: %s" % (src, e))
                done[src] = None
        if not done[src]:
            continue
        name, w, h = done[src]
        title = tags.get("og:title") or tags.get("twitter:title") or ""
        desc = tags.get("og:description") or tags.get("twitter:description") or ""
        tw = tags.get("twitter:site") or ""
        image_url = "https://%s/og/%s" % (host, name)
        lines = [
            '<meta property="og:title" content="%s">' % esc(title),
            '<meta property="og:description" content="%s">' % esc(desc) if desc else "",
            '<meta property="og:type" content="%s">' % esc(tags.get("og:type") or "website"),
            '<meta property="og:url" content="%s">' % esc(page_url(root, url_page, host)),
            '<meta property="og:site_name" content="%s">' % esc(SITE_NAMES[host]),
            '<meta property="og:locale" content="en_US">',
            '<meta property="og:image" content="%s">' % image_url,
            '<meta property="og:image:secure_url" content="%s">' % image_url,
            '<meta property="og:image:type" content="image/jpeg">',
            '<meta property="og:image:width" content="%d">' % w,
            '<meta property="og:image:height" content="%d">' % h,
            '<meta property="og:image:alt" content="%s">' % esc(title),
            '<meta name="twitter:card" content="summary_large_image">',
            '<meta name="twitter:site" content="%s">' % esc(tw) if tw else "",
            '<meta name="twitter:creator" content="%s">' % esc(tags.get("twitter:creator") or tw) if tw else "",
            '<meta name="twitter:title" content="%s">' % esc(tags.get("twitter:title") or title),
            '<meta name="twitter:description" content="%s">' % esc(tags.get("twitter:description") or desc) if desc else "",
            '<meta name="twitter:image" content="%s">' % image_url,
            '<meta name="twitter:image:alt" content="%s">' % esc(title),
        ]
        block = "\t<!-- link previews: _build/og_images.py -->\n" + "".join("\t%s\n" % l for l in lines if l)
        new_head = META_RE.sub("", head)
        m = TITLE_RE.search(new_head)
        if m:
            new_head = new_head[: m.end()] + block + new_head[m.end():]
        else:
            new_head = re.sub(r"(<head[^>]*>\s*)", lambda x: x.group(1) + block, new_head, count=1, flags=re.I)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_head + txt[end:])
        heads[p] = (new_head + txt[end:], len(new_head))
        fixed += 1
    print("og_images: %d pages, %d preview pictures, %d fell back, %d without a picture" % (fixed, previews, fell_back, missing))


if __name__ == "__main__":
    main()
