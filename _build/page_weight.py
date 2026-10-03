#!/usr/bin/env python3
"""Page weight report for built pages (stdlib only, ~1 s).

Sizes are transfer estimates: text (HTML/CSS/JS/SVG) gzip-compressed like the CDN
does, images/fonts/video as stored.

For each HTML page it sums the local files the browser would download:
"first view" (everything not lazy-loaded) and "full scroll" (everything),
and lists referenced local files that don't exist.

Usage (from the repo root, after `make generate` and the image step):
    python3 _build/page_weight.py static/index.html static/2026-san-francisco-q4/index.html ...

URLs are resolved against static/, the deployed site root.
External URLs (fonts, analytics, Luma, YouTube) are not counted.
Exits 1 if a page references a local file that doesn't exist.
"""
import os
import posixpath
import gzip
import re
import sys
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

STATIC = os.path.join(os.getcwd(), 'static')
WARN_FIRST_VIEW = 500 * 1024


class Refs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []          # (url, kind, lazy)
        self._picture = None    # webp source of the current <picture>
        self._in_style = False

    def _add(self, url, kind, lazy):
        if url:
            self.refs.append((url.strip(), kind, lazy))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        lazy = a.get('loading') == 'lazy'
        if tag == 'picture':
            self._picture = ''
        elif tag == 'source' and self._picture is not None:
            if 'webp' in (a.get('type') or '') and a.get('srcset'):
                self._picture = a['srcset'].split(',')[-1].split()[0]
        elif tag == 'img':
            src = self._picture or a.get('src')
            self._add(src, 'img', lazy)
        elif tag == 'link' and a.get('rel') in ('stylesheet', 'preload', 'icon'):
            self._add(a.get('href'), 'css' if a.get('rel') == 'stylesheet' else 'link', False)
        elif tag == 'script' and a.get('src'):
            self._add(a['src'], 'js', False)
        elif tag == 'video':
            lazy = a.get('preload') == 'none' or a.get('data-lazy') is not None
            self._add(a.get('poster'), 'img', a.get('data-lazy') is not None)
            self._add(a.get('src'), 'video', lazy)
            self._video_lazy = lazy
        elif tag == 'source' and a.get('src'):
            self._add(a['src'], 'video', getattr(self, '_video_lazy', False))
        elif tag == 'style':
            self._in_style = True
        style = a.get('style') or ''
        # inactive carousel slides are display:none until shown: browsers fetch them later
        cls = (a.get('class') or '').split()
        later = 'carousel-item' in cls and 'active' not in cls
        for u in re.findall(r'url\(["\']?([^"\')]+)', style):
            self._add(u, 'img', later)

    def handle_endtag(self, tag):
        if tag == 'picture':
            self._picture = None
        elif tag == 'style':
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            for u in re.findall(r'url\(["\']?([^"\')]+)', data):
                self._add(u, 'img', False)


def resolve(page, url):
    """Map a URL found on `page` to a file under static/, None if external, '' if missing."""
    parts = urlsplit(url)
    if parts.scheme or url.startswith(('//', '#')):
        return None
    path = unquote(parts.path)
    if not path:
        return None
    if not path.startswith('/'):
        page_dir = os.path.relpath(os.path.dirname(os.path.abspath(page)), STATIC).replace(os.sep, '/')
        path = posixpath.join('/' + ('' if page_dir == '.' else page_dir), path)
    # resolve like a browser: ".." never climbs above the site root
    full = os.path.join(STATIC, posixpath.normpath(path).lstrip('/'))
    if os.path.isdir(full):
        full = os.path.join(full, 'index.html')
    return full if os.path.isfile(full) else ''


def css_refs(css_file, seen):
    """Fonts/images referenced from a local stylesheet (one level)."""
    out = []
    try:
        text = open(css_file, encoding='utf-8', errors='ignore').read()
    except OSError:
        return out
    for u in re.findall(r'url\(["\']?([^"\')]+)', text):
        if u.startswith(('data:', 'http', '//', '#')):
            continue
        f = os.path.normpath(os.path.join(os.path.dirname(css_file), unquote(u.split('?')[0].split('#')[0])))
        # Only fonts are always fetched (latin-ext only when a page uses those characters);
        # CSS background images load only if used.
        if f.endswith(('.woff2', '.woff')) and '-ext' not in f and os.path.isfile(f) and f not in seen:
            seen.add(f)
            out.append(f)
    return out


TEXT = ('.html', '.css', '.js', '.svg', '.json', '.xml')


def wire_size(path):
    """Bytes on the wire: text compressed (the CDN serves gzip/brotli), binaries as is."""
    if path.endswith(TEXT):
        with open(path, 'rb') as f:
            return len(gzip.compress(f.read(), 6))
    return os.path.getsize(path)


def report(page):
    p = Refs()
    p.feed(open(page, encoding='utf-8', errors='ignore').read())
    html = wire_size(page)
    first = lazy = 0
    missing, seen = [], set()
    by_kind = {}
    for url, kind, is_lazy in p.refs:
        f = resolve(page, url)
        if f is None:
            continue
        if f == '':
            missing.append(url)
            continue
        if f in seen:
            continue
        seen.add(f)
        size = wire_size(f)
        by_kind[kind] = by_kind.get(kind, 0) + size
        if is_lazy:
            lazy += size
        else:
            first += size
        if kind == 'css':
            for font in css_refs(f, seen):
                seen.add(font)
                by_kind['font'] = by_kind.get('font', 0) + wire_size(font)
                first += wire_size(font)
    return html, first, lazy, by_kind, missing


def fmt(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def main(pages):
    status = 0
    for page in pages:
        html, first, lazy, by_kind, missing = report(page)
        kinds = ', '.join(f"{k} {fmt(v)}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]))
        flag = '  <-- first view over 500 KB' if first > WARN_FIRST_VIEW else ''
        print(f"{os.path.relpath(page, STATIC)}\n  html {fmt(html)} | first view {fmt(first + html)}"
              f" | full scroll {fmt(first + lazy + html)}{flag}\n  {kinds}")
        for m in missing[:20]:
            print(f"  MISSING: {m}")
        if missing:
            status = 1
    return status


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
