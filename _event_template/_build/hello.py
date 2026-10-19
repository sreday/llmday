"""/<event>/hello/ - the opening deck shown on the venue screen before the first talk (Marek 2026-10-19, after the
PLATFORMday Austin deck): Hello <city>, the team, WhatsApp + LinkedIn, the event's partners, the sister brands,
the open-source projects (cfp.ninja, certified.fyi,
Reliaburger), the sister brands' upcoming events, then this brand's mascot sticker with its upcoming events. Full-screen button + clicker keys in the page.

Everything comes from data, nothing is typed per event:
  - this event's metadata.yml (city, sponsors, brand colour, LinkedIn) and the folder name
  - home/metadata.yml `events` + each listed folder's metadata.yml (this brand's upcoming events and their cards)
  - hello-deck/hello.yaml at the repo root (team, WhatsApp, sister brands with their logo, mascot, theme and site)
  - the sister sites' published metadata.yml (https://llmday.com/metadata.yml and /<event>/metadata.yml),
    fetched once per build run and cached 30 min in ../.cache/hello-sisters.json (stale copy if the fetch fails;
    SKIP_CFP_CHECK=1 or SKIP_HELLO_FETCH=1 = offline build, cache only)
  - panoramas/<city_name>.jpg tinted in the brand's colours (the City view picture) behind slides 1 and 4

The QR codes are drawn here as inline SVG (qr_svg below), so the page works on venue wifi with nothing to fetch but
its own images. Identical file in sreday, llmday, platformday and PEC; called near the end of generate.py, never
breaks the build (a failure prints WARN hello and skips the page). Not in the sitemap, noindex.
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

DECK_DIR = '../hello-deck'
CACHE = '../.cache/hello-sisters.json'
CACHE_TTL = 30 * 60


# ── QR code (byte mode, error correction M, versions 1-10, after Nayuki's reference implementation) ──────────────
_ECC_M = [None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]   # ECC codewords per block
_BLOCKS_M = [None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]          # number of blocks


def _raw_modules(ver):
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        n -= (25 * na - 10) * na - 55
        if ver >= 7:
            n -= 36
    return n


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data, div):
    res = [0] * len(div)
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _gf_mul(c, f)
    return res


def qr_matrix(text):
    """text -> list of rows of bools (True = dark), no quiet zone"""
    data = text.encode('utf-8')
    for ver in range(1, 11):
        cap = _raw_modules(ver) // 8 - _ECC_M[ver] * _BLOCKS_M[ver]
        if 4 + (8 if ver <= 9 else 16) + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError('text too long for a QR code: %r' % text)
    bits = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in reversed(range(n)))
    put(4, 4)
    put(len(data), 8 if ver <= 9 else 16)
    for b in data:
        put(b, 8)
    put(0, min(4, cap * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    words = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # error correction, interleaved
    nb, ecl = _BLOCKS_M[ver], _ECC_M[ver]
    raw = _raw_modules(ver) // 8
    nshort, shortlen = nb - raw % nb, raw // nb
    div = _rs_divisor(ecl)
    blocks, k = [], 0
    for i in range(nb):
        dat = words[k:k + shortlen - ecl + (0 if i < nshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = [blk[i] for i in range(len(blocks[0])) for j, blk in enumerate(blocks) if i != shortlen - ecl or j >= nshort]
    # function patterns
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < size and 0 <= cy + dy < size:
                    setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    if ver > 1:
        na = ver // 7 + 2
        step = (ver * 4 + na * 2 + 1) // (na * 2 - 2) * 2
        pos = list(reversed([size - 7 - i * step for i in range(na - 1)] + [6]))
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, na - 1), (na - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = (0 << 3) | mask                                  # ECC level M = 0b00
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = ((d << 10) | rem) ^ 0x5412
        bit = lambda i: (b >> i) & 1 == 1                    # noqa: E731
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6))
        setf(8, 8, bit(7))
        setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)
    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        b = (ver << 12) | rem
        for i in range(18):
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, (b >> i) & 1 == 1)
            setf(c, a, (b >> i) & 1 == 1)
    # data
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(final) * 8:
                    mod[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2
    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]

    def apply(m):
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and masks[m](x, y):
                    mod[y][x] = not mod[y][x]

    def penalty():
        p = 0
        for grid in (mod, [list(c) for c in zip(*mod)]):
            for row in grid:
                run, prev = 0, None
                for v in row:
                    run = run + 1 if v == prev else 1
                    prev = v
                    if run == 5:
                        p += 3
                    elif run > 5:
                        p += 1
                s = ''.join('1' if v else '0' for v in row)
                p += 40 * (len(re.findall('(?=00001011101)', s)) + len(re.findall('(?=10111010000)', s)))
        for y in range(size - 1):
            for x in range(size - 1):
                if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                    p += 3
        dark = sum(map(sum, mod))
        p += abs(dark * 20 - size * size * 10) // (size * size) * 10
        return p
    best = None
    for m in range(8):
        apply(m)
        draw_format(m)
        sc = penalty()
        if best is None or sc < best[0]:
            best = (sc, m)
        apply(m)                                             # XOR again = undo
    apply(best[1])
    draw_format(best[1])
    return mod


def qr_svg(text, quiet=2):
    """inline SVG, dark modules on white, one <path>; scales to its box"""
    m = qr_matrix(text)
    n = len(m) + 2 * quiet
    d = ''.join('M%d %dh1v1h-1z' % (x + quiet, y + quiet) for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return ('<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges" role="img" '
            'aria-label="QR code: %s"><rect width="%d" height="%d" fill="#fff"/><path d="%s" fill="#000"/></svg>'
            % (n, n, text.replace('"', '&quot;'), n, n, d))


# ── data ─────────────────────────────────────────────────────────────────────────────────────────────────────────
_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
           'november', 'december')


def _short_date(s):
    """'November 6, 2026' -> 'NOV 6, 2026'; 'December 2-3, 2026' -> 'DEC 2-3, 2026'"""
    s = str(s or '').strip()
    return re.sub(r'[A-Za-z]+', lambda m: m.group(0)[:3] if m.group(0).lower() in _MONTHS else m.group(0), s).upper()


def _country(location):
    parts = [p.strip() for p in str(location or '').split(',') if p.strip()]
    c = parts[-1] if parts else ''
    return {'United States': 'USA', 'United Kingdom': 'UK', 'US': 'USA'}.get(c, c)


def _event_row(meta, home_ev, folder, base):
    start = str(meta.get('start_time') or '')
    return {'folder': folder, 'start': start[:10], 'city': str(meta.get('city_name') or '').strip(),
            'date': _short_date(meta.get('date_string')), 'country': _country((home_ev or {}).get('location') or meta.get('location_string')),
            'url': base.rstrip('/') + '/' + folder + '/', 'card': str((home_ev or {}).get('photo_url') or '')}


def _span(rows):
    """'Q4 2026' or 'Q4 2026 - Q1 2027' for the events' quarters"""
    qs = sorted({(int(r['start'][:4]), (int(r['start'][5:7]) - 1) // 3 + 1) for r in rows})
    if not qs:
        return ''
    a, b = 'Q%d %d' % qs[0][::-1], 'Q%d %d' % qs[-1][::-1]
    return a if a == b else ('Q%d & Q%d %d' % (qs[0][1], qs[-1][1], qs[0][0]) if qs[0][0] == qs[-1][0] else a + ' - ' + b)


def _tentative(brand, rows, today):
    """hello.yaml `tentative` entries as grey rows, minus those a published event already covers (same city + month)"""
    have = {(r['city'].lower(), r['start'][:7]) for r in rows}
    out = []
    for t in brand.get('tentative') or []:
        m, city = str(t.get('month') or ''), str(t.get('city') or '').strip()
        if not re.match(r'^\d{4}-\d{2}$', m) or not city or m < today[:7] or (city.lower(), m) in have:
            continue
        out.append({'folder': '', 'start': m + '-01', 'city': city, 'country': str(t.get('country') or ''), 'url': '', 'card': '',
                    'date': '%s %s' % (_MONTHS[int(m[5:]) - 1][:3].upper(), m[:4]), 'tentative': True})
    return out


def _with_tentative(brand, rows, today, cap):
    """published events first in line, then the tentative ones fill up to `cap`, all in date order"""
    rows = rows[:cap]
    return sorted(rows + _tentative(brand, rows, today)[:max(0, cap - len(rows))], key=lambda r: r['start'])


def _upcoming(rows, today):
    rows = [r for r in rows if r['start'] and r['start'] >= today and r['city']]
    return sorted(rows, key=lambda r: r['start'])


def _local_events(home, base):
    rows = []
    for ev in (home or {}).get('events') or []:
        folder = str((ev or {}).get('url') or '').strip('./').rstrip('/')
        for path in ('../%s/metadata.yml' % folder, '../past/%s/metadata.yml' % folder):
            if folder and os.path.exists(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        rows.append(_event_row(yaml.safe_load(f) or {}, ev, folder, base))
                except Exception as e:                       # noqa: BLE001
                    print('WARN hello: %s unreadable (%s)' % (path, e))
                break
    return rows


def _fetch_yaml(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'hello-deck-build'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return yaml.safe_load(r.read().decode('utf-8')) or {}


def _remote_events(site):
    home = _fetch_yaml(site + 'metadata.yml')
    evs = [e for e in (home.get('events') or []) if (e or {}).get('url')]

    def one(ev):
        folder = str(ev['url']).strip('./').rstrip('/')
        try:
            return _event_row(_fetch_yaml(site + folder + '/metadata.yml'), ev, folder, site)
        except Exception:                                    # noqa: BLE001 - one missing event must not drop the brand
            return None
    with ThreadPoolExecutor(8) as ex:
        return [r for r in ex.map(one, evs) if r]


def _sister_events(brands):
    """{brand key: [event rows]} for the sister brands with a `site`, from the cache when it is fresh"""
    sites = {b['key']: b['site'] for b in brands if b.get('site')}
    cached = {}
    try:
        with open(CACHE, encoding='utf-8') as f:
            cached = json.load(f)
    except (OSError, ValueError):
        pass
    if cached.get('sites') == sites and time.time() - cached.get('at', 0) < CACHE_TTL:
        return cached['events']
    if os.environ.get('SKIP_CFP_CHECK') or os.environ.get('SKIP_HELLO_FETCH'):
        return cached.get('events') or {}
    out = {}
    with ThreadPoolExecutor(4) as ex:
        futs = {k: ex.submit(_remote_events, s) for k, s in sites.items()}
        for k, fu in futs.items():
            try:
                out[k] = fu.result()
            except Exception as e:                           # noqa: BLE001
                print('WARN hello: %s unreachable (%s), using the cached list' % (sites[k], e))
                out[k] = (cached.get('events') or {}).get(k, [])
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, 'w', encoding='utf-8') as f:
            json.dump({'at': time.time(), 'sites': sites, 'events': out}, f)
    except OSError:
        pass
    return out


# ── the upcoming-events map ───────────────────────────────────────────────────────────────────────────────────────
# hello-deck/brands/world-dots.png (hello-deck/tools/make_world_dots.py): Miller projection of this box, placed on the
# 1920x1080 slide at MAP_X/MAP_Y. Change both together.
MAP = {'lon0': -130.0, 'lon1': 110.0, 'lat0': -38.0, 'lat1': 64.0, 'w': 1600}
MAP_X, MAP_Y = 160, 306
_KEEP_OUT = [(40, 810, 290, 1060), (1540, 690, 1920, 1080), (0, 0, 1920, 290), (600, 1010, 1320, 1080)]   # QR, mascot, heading, "many more"


def _miller(lat):
    import math
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


def _map_h():
    import math
    return round(MAP['w'] * (_miller(MAP['lat1']) - _miller(MAP['lat0'])) / math.radians(MAP['lon1'] - MAP['lon0']))


def _map_xy(lat, lon):
    x = (lon - MAP['lon0']) / (MAP['lon1'] - MAP['lon0']) * MAP['w']
    y = (_miller(MAP['lat1']) - _miller(lat)) / (_miller(MAP['lat1']) - _miller(MAP['lat0'])) * _map_h()
    return MAP_X + x, MAP_Y + y


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _map_layout(rows, cities, brand):
    """rows (date order) -> {'stops': pins with their label boxes, 'arcs': route segments}; a city appears once with all
    its dates. Labels are placed greedily, most crowded pin first, next to the pin or further out with a leader line."""
    known = {str(k).lower(): v for k, v in (cities or {}).items()}
    stops, order = {}, []
    for r in rows:
        k = r['city'].lower()
        if k not in known:
            print('WARN hello: no map position for %s (%s) - add it under cities: in hello-deck/hello.yaml' % (r['city'], brand))
            continue
        if k not in stops:
            x, y = _map_xy(*known[k])
            stops[k] = {'city': r['city'], 'x': x, 'y': y, 'dates': [], 'tentative': True}
        stops[k]['dates'].append({'date': r['date'], 'tentative': bool(r.get('tentative'))})
        stops[k]['tentative'] = stops[k]['tentative'] and bool(r.get('tentative'))
        order.append((k, bool(r.get('tentative'))))
    pins = list(stops.values())
    for p in pins:
        p['w'] = max(len(p['city']) * 25, max(len(d['date']) for d in p['dates']) * 19) + 12
        p['h'] = 48 + 38 * len(p['dates'])
    placed = []
    crowd = lambda p: -sum(1 for q in pins if q is not p and abs(q['x'] - p['x']) < 260 and abs(q['y'] - p['y']) < 200)  # noqa: E731
    for p in sorted(pins, key=crowd):
        x, y, w, h = p['x'], p['y'], p['w'], p['h']
        best = None
        for dist in (30, 75, 130, 200):
            d2 = dist * .7
            for side, bx, by in (('r', x + dist, y - h / 2), ('l', x - dist - w, y - h / 2), ('a', x - w / 2, y - dist - h),
                                 ('b', x - w / 2, y + dist), ('r', x + d2, y - d2 - h), ('l', x - d2 - w, y - d2 - h),
                                 ('r', x + d2, y + d2), ('l', x - d2 - w, y + d2)):
                box = (bx, by, bx + w, by + h)
                score = dist * 3 + (0 if side == 'r' else 8)
                score += sum(_overlap(box, q) for q in placed) * 20
                score += sum(_overlap(box, (q['x'] - 22, q['y'] - 22, q['x'] + 22, q['y'] + 22)) for q in pins) * 40
                score += sum(_overlap(box, k) for k in _KEEP_OUT) * 30
                if bx < 10 or by < 10 or bx + w > 1910 or by + h > 1070:
                    score += 1e7
                if best is None or score < best[0]:
                    best = (score, box, side, dist)
        _, box, side, dist = best
        placed.append(box)
        p.update(lx=round(box[0]), ly=round(box[1]), side=side, leader=dist > 30)
        if p['leader']:                                      # line from the pin to the nearest point of the label
            p['tx'] = round(min(max(x, box[0]), box[2]))
            p['ty'] = round(min(max(y, box[1]), box[3]))
    arcs = []
    for (a, _), (b, tb) in zip(order, order[1:]):
        if a == b:
            continue
        pa, pb = stops[a], stops[b]
        mx, my = (pa['x'] + pb['x']) / 2, (pa['y'] + pb['y']) / 2
        dx, dy = pb['x'] - pa['x'], pb['y'] - pa['y']
        nx, ny = -dy * .28, dx * .28                         # bow the arc sideways, towards the top of the map
        if ny > 0:
            nx, ny = -nx, -ny
        arcs.append({'d': 'M%.1f %.1f Q%.1f %.1f %.1f %.1f' % (pa['x'], pa['y'], mx + nx, my + ny, pb['x'], pb['y']), 'grey': tb})
    for p in pins:
        p['x'], p['y'] = round(p['x']), round(p['y'])
    return {'stops': pins, 'arcs': arcs}


# ── images: copied/derived into <event>/hello/img/ under content-hashed names ───────────────────────────────────
class _Img:
    def __init__(self, out):
        self.out = out
        os.makedirs(out, exist_ok=True)

    def _write(self, stem, ext, data):
        name = '%s-%s.%s' % (stem, hashlib.md5(data).hexdigest()[:10], ext)
        with open(os.path.join(self.out, name), 'wb') as f:
            f.write(data)
        return 'img/' + name

    def copy(self, path):
        """a hello-deck file as it is (already sized in the repo)"""
        if not path or not os.path.exists(path):
            return ''
        stem, ext = os.path.splitext(os.path.basename(path))
        with open(path, 'rb') as f:
            return self._write(re.sub(r'[^a-z0-9]+', '-', stem.lower()), ext.lstrip('.').lower(), f.read())

    def panorama(self, city, lo, hi):
        """panoramas/<city>.* -> 1920x1080 duotone in the brand's colours, or '' when the city has none"""
        if not city or not os.path.isdir('../panoramas'):
            return ''
        src = next((f for f in sorted(os.listdir('../panoramas')) if os.path.splitext(f)[0].strip().lower() == city.lower()
                    and os.path.splitext(f)[1].lower() in ('.png', '.jpg', '.jpeg', '.webp')), None)
        if not src:
            return ''
        from PIL import Image, ImageOps
        im = Image.open('../panoramas/' + src).convert('L')
        im = ImageOps.fit(im, (1920, 1080), Image.LANCZOS, centering=(0.5, 0.3))
        im = ImageOps.colorize(ImageOps.autocontrast(im, cutoff=1), black=lo, white=hi)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=80, optimize=True, progressive=True)
        return self._write('city', 'jpg', buf.getvalue())


# ── the page ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _brand_key(context):
    k = str(context.get('brand_key') or context.get('brand_name') or '').strip().lower()
    return {'prompt engineering conference': 'pec'}.get(k, k)


def build(context, env, base_folder):
    try:
        _build(context, env, base_folder)
    except Exception as e:                                   # noqa: BLE001 - the deck never breaks the site build
        import traceback
        traceback.print_exc()
        print('WARN hello: /hello/ not written (%s: %s)' % (type(e).__name__, e))


def _build(context, env, base_folder):
    cfg_path = os.path.join(DECK_DIR, 'hello.yaml')
    if not os.path.exists(cfg_path):
        print('WARN hello: %s missing, no /hello/ page' % cfg_path)
        return
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    key = _brand_key(context)
    brands = [dict(b) for b in cfg.get('brands') or []]
    me = next((b for b in brands if b['key'] == key), None) or {'key': key, 'name': context.get('brand_name', ''), 'theme': {}}
    theme = dict(cfg.get('default_theme') or {}, **(me.get('theme') or {}))
    theme.setdefault('accent', context.get('brand_color') or '#888')
    out = os.path.join(base_folder, 'hello')
    shutil.rmtree(out, ignore_errors=True)
    img = _Img(os.path.join(out, 'img'))
    asset = lambda p: img.copy(os.path.join(DECK_DIR, p)) if p else ''   # noqa: E731

    slug = os.path.basename(os.getcwd())
    base = str(context.get('base_path') or 'https://%s/' % context.get('brand_domain', ''))
    city = str(context.get('city_name') or '').strip()
    event_url = base + slug + '/'
    linkedin = str(context.get('linkedin_url') or '')
    whatsapp = str(cfg.get('whatsapp_url') or '')
    today = datetime.date.today().isoformat()

    for b in brands:
        b['logo_src'] = asset(b.get('logo'))
        b['mascot_src'] = asset(b.get('mascot'))
        if b['key'] == key:                                  # the host's Hello mascot (404 one) and closing sticker
            b['hello_src'] = asset(b.get('hello_mascot')) or b['mascot_src']
            b['sticker_src'] = asset(b.get('sticker')) or b['mascot_src']
        b['qr'] = qr_svg(b['url']) if b.get('url') else ''
        b['theme'] = dict(cfg.get('default_theme') or {}, **(b.get('theme') or {}))
    me = next((b for b in brands if b['key'] == key), me)
    for k in ('logo_src', 'mascot_src', 'hello_src', 'sticker_src'):
        me.setdefault(k, '')

    # this brand's upcoming events (this one included) from the repo; the sisters' from their sites
    home = {}
    if os.path.exists('../home/metadata.yml'):
        with open('../home/metadata.yml', encoding='utf-8') as f:
            home = yaml.safe_load(f) or {}
    own = _upcoming(_local_events(home, base), today)
    if not any(r['folder'] == slug for r in own):           # the deck still opens with this event on its day
        own.insert(0, _event_row(context, None, slug, base))
    for r in own:
        r['card'] = '/' + r['card'][2:] if r['card'].startswith('./') else r['card']
    sisters = _sister_events([b for b in brands if b['key'] != key and b.get('tour', True)])
    tours = []
    for b in brands:
        rows = _with_tentative(b, _upcoming(sisters.get(b['key']) or [], today), today, 10)
        if b['key'] != key and b.get('tour', True) and rows:
            tours.append(dict(b, events=rows, span=_span(rows), map=_map_layout(rows, cfg.get('cities'), b['key'])))

    sponsors = []
    for s in context.get('sponsors') or []:
        logo = str((s or {}).get('logo') or '').strip()
        if logo:
            sponsors.append({'src': '../sponsors/' + logo, 'name': os.path.splitext(logo)[0]})

    deck = {
        'key': key, 'brand': me, 'theme': theme, 'city': city or slug, 'slug': slug,
        'event_name': (context.get('onboarding_event') or {}).get('event_name') or '%s %s' % (context.get('brand_name', ''), city),
        'date': _short_date(context.get('date_string')),
        'event_url': event_url, 'event_qr': qr_svg(event_url),
        'whatsapp_url': whatsapp, 'whatsapp_qr': qr_svg(whatsapp) if whatsapp else '',
        'linkedin_url': linkedin, 'linkedin_qr': qr_svg(linkedin) if linkedin else '',
        'home_url': base, 'home_qr': qr_svg(base),
        'city_bg': img.panorama(city, theme.get('pano_lo', '#000000'), theme.get('pano_hi', '#888888')),
        'team': [dict(t, src=asset(t.get('photo'))) for t in cfg.get('team') or []],
        'sponsors': sponsors,
        'family': [b for b in brands if b['key'] != key and b.get('family', True)],
        'open_source': [dict(o, logo_src=asset(o.get('logo')), qr=qr_svg(o['url'])) for o in cfg.get('open_source') or [] if o.get('url')],
        'tours': tours,
        'own_events': _with_tentative(me, own, today, 6),
        'icons': {k: asset(v) for k, v in (cfg.get('icons') or {}).items()},
        'map_src': asset('brands/world-dots.png') if tours else '',
        'map_box': {'x': MAP_X, 'y': MAP_Y, 'w': MAP['w'], 'h': _map_h()},
    }
    html = env.get_template('hello.html').render(page='hello.html', deck=deck, **context)
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print('Writing out hello/index.html (hidden, not in sitemap): %d team, %d partners, %d sister tours (%s), %d own events'
          % (len(deck['team']), len(sponsors), len(tours), ', '.join('%s %d' % (t['key'], len(t['events'])) for t in tours) or '-', len(own)))
