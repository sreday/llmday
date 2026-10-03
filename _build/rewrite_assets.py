#!/usr/bin/env python3
"""Point built pages at WebP images and content-versioned asset URLs.

Runs after optimize_images.py (which writes <name>.webp next to an image when
WebP is >= 10% smaller) and render_teasers.py. For every static/**/*.html and
static/**/*.css it rewrites local references in src, srcset, poster,
<link href> and CSS url():

  1. foo.png / foo.jpg -> foo.webp, when static/.../foo.webp exists
  2. appends ?v=<first 10 hex of sha1(file)>, so a changed file always gets a new
     URL (safe to cache for a year) and an unchanged one is never re-downloaded

Animated GIFs that have a .mp4 sibling (one-off ffmpeg conversions committed next
to the GIF: ~250 KB instead of ~10 MB) and autoplaying <video src> loops become
(a transparent GIF gets a .mov + .webm pair instead: both keep the alpha channel, see gif())
<video class="lazy-loop">: nothing is downloaded until the video scrolls into view,
and it pauses when it leaves (a tiny script is added before </body>; templates can
use class="lazy-loop" data-src="..." too). With Save-Data or a 2G/3G connection
(Chrome on Android reports it) only the poster shows.

It also lazy-loads: <img> tags without a loading attribute get
loading="lazy" decoding="async", except the first EAGER_IMAGES of the page (logo,
hero) and any with fetchpriority; <iframe>s (Luma tickets, maps, video) get
loading="lazy".

og:image and other content= attributes are left alone: social crawlers want
PNG/JPG. Links to pages (<a href>), external URLs and URLs that already carry a
query string or fragment keep their versioning as is. HTML itself is never
cached long: the schedule must always be fresh.
"""
import hashlib
import os
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import datetime
import json

STATIC = Path("static")
WEBP_FROM = (".png", ".jpg", ".jpeg")
_hash_cache = {}
_stats = {"webp": 0, "versioned": 0, "files": 0, "videos": 0}

ATTR_RE = re.compile(r'(\s(?:src|poster)\s*=\s*)(["\'])(.*?)\2', re.S | re.I)
SRCSET_RE = re.compile(r'(\ssrcset\s*=\s*)(["\'])(.*?)\2', re.S | re.I)
LINK_RE = re.compile(r'<link\b[^>]*>', re.I)
HREF_RE = re.compile(r'(\shref\s*=\s*)(["\'])(.*?)\2', re.S | re.I)
GIF_IMG_RE = re.compile(r'<img\b([^>]*?)\ssrc\s*=\s*(["\'])([^"\']+?\.gif)\2([^>]*)>', re.I)
AUTOPLAY_VIDEO_RE = re.compile(r'<video\b([^>]*?)\ssrc\s*=\s*(["\'])([^"\']+)\2([^>]*\bautoplay\b[^>]*|[^>]*)>', re.I)
LAZY_LOOP_JS = ("<script>/* rewrite_assets.py: loops load and play only while on screen */(function(){"
                "var vs=document.querySelectorAll('video.lazy-loop');if(!vs.length)return;"
                "var c=navigator.connection;if(c&&(c.saveData||/2g|3g/.test(c.effectiveType)))return;"
                "function go(v){if(!v.dataset.on){v.dataset.on=1;if(v.dataset.src){v.src=v.dataset.src}else{"
                "v.querySelectorAll('source[data-src]').forEach(function(s){s.src=s.dataset.src});v.load()}}"
                "var p=v.play();p&&p.catch&&p.catch(function(){})}"
                "if(!('IntersectionObserver'in window)){vs.forEach(go);return}"
                "var io=new IntersectionObserver(function(es){es.forEach(function(e){e.isIntersecting?go(e.target):e.target.pause()})},"
                "{rootMargin:'300px'});vs.forEach(function(v){io.observe(v)})})()</script>")
IMG_RE = re.compile(r'<(img|iframe)\b([^>]*)>', re.I)
EAGER_IMAGES = 3  # header logo + hero images stay eager; everything below loads as you scroll
# Sales come first (Marek 2026-10-03): nothing that sells sponsorship (or tickets) waits for a scroll.
# Sponsor / partner logos (any src under sponsors/), the Calendly and Luma iframes are never made lazy,
# and the sponsorship-facing pages get no lazy images or lazy video loops at all.
EAGER_SRC = re.compile(r'(?:^|/)sponsors/|calendly\.com|lu\.ma/|luma\.com/', re.I)
PRIORITY_PAGES = ("sponsorship.html", "host/index.html", "onboardsponsor/index.html")
URL_RE = re.compile(r'url\(\s*(["\']?)([^"\')]+?)\1\s*\)')
# SREday 2022-2024 archives are frozen: their built pages are deployed exactly as generated
FROZEN_EVENTS = ("2022-", "2023-", "2024-")


def frozen(path):
    return path.relative_to(STATIC).parts[0].startswith(FROZEN_EVENTS)


def file_hash(path):
    h = _hash_cache.get(path)
    if h is None:
        h = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
        _hash_cache[path] = h
    return h


def local_path(base_dir, url):
    """static/ file a URL points to, or None for external/non-file URLs."""
    if url.startswith(("data:", "#", "//", "{{")) or urlsplit(url).scheme:
        return None
    path = unquote(urlsplit(url).path)
    if not path:
        return None
    if not path.startswith("/"):
        path = posixpath.join("/" + base_dir.relative_to(STATIC).as_posix(), path)
    # resolve like a browser: ".." never climbs above the site root
    full = STATIC / posixpath.normpath(path).lstrip("/")
    return full if full.is_file() else None


def rewrite_url(base_dir, url):
    url = url.strip()
    target = local_path(base_dir, url)
    if target is None:
        return url
    parts = urlsplit(url)
    path = parts.path
    if target.suffix.lower() in WEBP_FROM:
        webp = target.with_suffix(".webp")
        if webp.is_file():
            stem, _ = os.path.splitext(path)
            path = stem + ".webp"
            target = webp
            _stats["webp"] += 1
    if parts.query or parts.fragment:
        return path + ("?" + parts.query if parts.query else "") + ("#" + parts.fragment if parts.fragment else "")
    _stats["versioned"] += 1
    return f"{path}?v={file_hash(target)}"


def rewrite_srcset(base_dir, value):
    out = []
    for candidate in value.split(","):
        bits = candidate.strip().split(None, 1)
        if not bits:
            continue
        bits[0] = rewrite_url(base_dir, bits[0])
        out.append(" ".join(bits))
    return ", ".join(out)


def rewrite_css_urls(base_dir, text):
    return URL_RE.sub(lambda m: f"url({m.group(1)}{rewrite_url(base_dir, m.group(2))}{m.group(1)})", text)


def lazy_videos(base, text):
    """GIF <img> with a .mp4 sibling, and autoplay <video src>, -> <video class="lazy-loop">."""
    def gif(m):
        target = local_path(base, m.group(3))
        if not target:
            return m.group(0)
        mp4, mov, webm = (target.with_suffix(x) for x in (".mp4", ".mov", ".webm"))
        alpha = mov.is_file() and webm.is_file()
        if not alpha and not mp4.is_file():
            return m.group(0)
        stem = os.path.splitext(urlsplit(m.group(3)).path)[0]
        attrs = (m.group(1) + m.group(4)).rstrip().rstrip("/")
        alt = re.search(r'\salt\s*=\s*(["\'])(.*?)\1', attrs)
        attrs = re.sub(r'\s(alt|loading|decoding)\s*=\s*(["\']).*?\2', "", attrs)
        poster = target.with_name(target.stem + ".poster.webp")
        poster_attr = f' poster="{stem}.poster.webp?v={file_hash(poster)}"' if poster.is_file() else ""
        label = f' aria-label="{alt.group(2)}"' if alt else ""
        _stats["videos"] += 1
        if alpha:
            # transparent GIF (footer mascots on the brand colour): an MP4 has no alpha channel, so it ships as
            # HEVC-with-alpha .mov (Safari) + VP9-with-alpha .webm (Chrome, Firefox: they skip video/quicktime)
            return (f'<video class="lazy-loop"{poster_attr}{label} muted loop playsinline preload="none"{attrs}>'
                    f'<source data-src="{stem}.mov?v={file_hash(mov)}" type=\'video/quicktime; codecs="hvc1"\'>'
                    f'<source data-src="{stem}.webm?v={file_hash(webm)}" type="video/webm"></video>')
        return (f'<video class="lazy-loop" data-src="{stem}.mp4?v={file_hash(mp4)}"{poster_attr}{label}'
                f' muted loop playsinline preload="none"{attrs}></video>')

    def autoplay(m):
        attrs = m.group(1) + m.group(4)
        if "autoplay" not in attrs.lower() or "lazy-loop" in attrs:
            return m.group(0)
        target = local_path(base, m.group(3))
        if not target:
            return m.group(0)
        attrs = re.sub(r'\s(autoplay|preload\s*=\s*(["\']).*?\2)', "", attrs)
        cls = re.search(r'\sclass\s*=\s*(["\'])(.*?)\1', attrs)
        if cls:
            attrs = attrs.replace(cls.group(0), f' class="{cls.group(2)} lazy-loop"')
        else:
            attrs += ' class="lazy-loop"'
        _stats["videos"] += 1
        return f'<video{attrs} data-src="{rewrite_url(base, m.group(3))}" preload="none">'

    new = GIF_IMG_RE.sub(gif, text)
    new = AUTOPLAY_VIDEO_RE.sub(autoplay, new)
    if "lazy-loop" in new and "video.lazy-loop" not in new:
        new = new.replace("</body>", LAZY_LOOP_JS + "\n</body>", 1) if "</body>" in new else new + LAZY_LOOP_JS
    return new


def add_lazy(text):
    seen = {"img": 0}

    def tag(m):
        name, attrs = m.group(1).lower(), m.group(2)
        if re.search(r'\sloading\s*=', attrs, re.I):
            return m.group(0)
        src = re.search(r'\ssrc\s*=\s*(["\'])(.*?)\1', attrs)
        if src and EAGER_SRC.search(src.group(2)):
            return m.group(0)
        if name == "img":
            seen["img"] += 1
            if seen["img"] <= EAGER_IMAGES or "fetchpriority" in attrs.lower():
                return m.group(0)
            extra = ' loading="lazy" decoding="async"'
        else:
            extra = ' loading="lazy"'
        closing = "/" if attrs.rstrip().endswith("/") else ""
        attrs = attrs.rstrip().rstrip("/")
        return f"<{m.group(1)}{attrs}{extra}{' /' if closing else ''}>"
    return IMG_RE.sub(tag, text)


def rewrite_html(path):
    base = path.parent
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    priority = path.relative_to(STATIC).as_posix().endswith(PRIORITY_PAGES)
    new = text if priority else lazy_videos(base, text)
    new = ATTR_RE.sub(lambda m: m.group(1) + m.group(2) + rewrite_url(base, m.group(3)) + m.group(2), new)
    new = SRCSET_RE.sub(lambda m: m.group(1) + m.group(2) + rewrite_srcset(base, m.group(3)) + m.group(2), new)
    new = LINK_RE.sub(lambda t: HREF_RE.sub(
        lambda m: m.group(1) + m.group(2) + rewrite_url(base, m.group(3)) + m.group(2), t.group(0)), new)
    new = rewrite_css_urls(base, new)
    if not priority:
        new = add_lazy(new)
    new = add_fresh(new)
    if new != text:
        path.write_text(new, encoding="utf-8", errors="surrogateescape")
        _stats["files"] += 1


def rewrite_css(path):
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    new = rewrite_css_urls(path.parent, text)
    if new != text:
        path.write_text(new, encoding="utf-8", errors="surrogateescape")
        _stats["files"] += 1
        _hash_cache.pop(path, None)


# Always-fresh pages (Marek 2026-10-03: attendees keep checking the ever-changing schedule). HTML is never
# cached on purpose, but GitHub Pages sends max-age=600 and a tab left open all day never re-asks. Every page
# gets <meta name="build"> and a small script; static/build.json holds the current build id. When a newer
# build exists, a page that is opened or comes back to the foreground reloads itself (once per build), unless
# the visitor is typing in a form or has a talk open: then, like the 5-minute check while it stays open,
# it shows a "Refresh" bar instead. No service worker, nothing cached.
BUILD_ID = (os.environ.get("GITHUB_SHA", "")[:10] + "-" if os.environ.get("GITHUB_SHA") else "") + \
    datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
FRESH_JS = "<script>/* rewrite_assets.py: reload when a newer build is out */" + '(function(){var m=document.querySelector(\'meta[name="build"]\');if(!m||!window.fetch)return;var mine=m.getAttribute(\'content\'),busy=0,bar=null;function engaged(){var a=document.activeElement;if(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))return true;var f=document.querySelectorAll(\'input,textarea\');for(var i=0;i<f.length;i++){var e=f[i];if(/^(checkbox|radio)$/.test(e.type)?e.checked!==e.defaultChecked:(e.type!==\'hidden\'&&e.value!==e.defaultValue))return true}return !!document.querySelector(\'.modal.show\')}function offer(){if(bar||!document.body)return;bar=document.createElement(\'div\');bar.setAttribute(\'role\',\'status\');bar.style.cssText=\'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:2000;display:flex;gap:12px;align-items:center;max-width:calc(100% - 32px);background:#111;color:#fff;padding:10px 12px 10px 18px;border-radius:999px;font:600 14px/1.3 Montserrat,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.3)\';var t=document.createElement(\'span\');t.textContent=\'This page was just updated (schedule, speakers or sponsors)\';bar.appendChild(t);var b=document.createElement(\'button\');b.type=\'button\';b.textContent=\'Refresh\';b.style.cssText=\'border:0;border-radius:999px;padding:6px 14px;font:inherit;cursor:pointer;background:#fff;color:#111\';b.onclick=function(){location.reload()};bar.appendChild(b);document.body.appendChild(bar)}function check(auto){if(busy||document.visibilityState===\'hidden\')return;busy=1;fetch(\'/build.json?t=\'+Date.now(),{cache:\'no-store\'}).then(function(r){return r.ok?r.json():null}).then(function(j){busy=0;if(!j||!j.build||j.build===mine)return;var again=false;try{again=sessionStorage.getItem(\'fresh-reload\')===j.build;sessionStorage.setItem(\'fresh-reload\',j.build)}catch(e){}if(auto&&!again&&!engaged())location.reload();else offer()})[\'catch\'](function(){busy=0})}check(true);document.addEventListener(\'visibilitychange\',function(){if(document.visibilityState===\'visible\')check(true)});addEventListener(\'pageshow\',function(e){if(e.persisted)check(true)});setInterval(function(){check(false)},300000)})()' + "</script>"


def add_fresh(text):
    if 'name="build"' in text or "</head>" not in text or "</body>" not in text:
        return text
    text = text.replace("</head>", '<meta name="build" content="%s">\n</head>' % BUILD_ID, 1)
    return text.replace("</body>", FRESH_JS + "\n</body>", 1)


def main():
    if not STATIC.is_dir():
        print("No static/ directory found, skipping")
        return 0
    # CSS first: its own ?v= hash must reflect the rewritten content
    for css in sorted(STATIC.rglob("*.css")):
        if not frozen(css):
            rewrite_css(css)
    for html in sorted(STATIC.rglob("*.html")):
        if not frozen(html):
            rewrite_html(html)
    (STATIC / "build.json").write_text(json.dumps({"build": BUILD_ID}), encoding="utf-8")
    print(f"Rewrote {_stats['files']} files: {_stats['webp']} references to WebP, "
          f"{_stats['versioned']} URLs versioned with ?v=, {_stats['videos']} loops made lazy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
