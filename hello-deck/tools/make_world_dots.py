"""Draws hello-deck/brands/world-dots.png: the dotted world map behind the upcoming-events slides (white dots on
transparent, tinted per brand in the page with a CSS mask). Run once; the PNG is committed.

  curl -sLo land-110m.json https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json   # Natural Earth, public domain
  python3 hello-deck/tools/make_world_dots.py land-110m.json hello-deck/brands/world-dots.png

The projection (Miller, longitude -130..110, latitude -38..64) must match MAP in _build/hello.py, which places the pins.
The PNG runs past that box to the slide's edges (PAD_*, also in hello.py) and thins out on a feathered oval: each dot is
either drawn or not, dropped more often towards the edge, with a wobbly edge so it never reads as a ring. The event
area stays whole - no dot goes inside the inner oval or within 40 px of a city in hello.yaml.
"""
import os
import json
import math
import random
import sys

from PIL import Image, ImageDraw

LON0, LON1, LAT0, LAT1 = -130.0, 110.0, -38.0, 64.0   # the Americas to India: where the events are
W = 1600                    # the map box on the 1920x1080 slide (drawn at 2x); H keeps the projection undistorted
SCALE = 2
STEP = 9                    # px between dots at 1x
PAD_L, PAD_R, PAD_T, PAD_B = 160, 160, 150, 10   # past the box to the slide edges; must match MAP_PAD in hello.py
KEEP, GONE = 0.78, 0.97     # superellipse radius: every dot inside KEEP, none past GONE, a random thinning between


def miller(lat):
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


H = round(W * (miller(LAT1) - miller(LAT0)) / math.radians(LON1 - LON0))


def project(lon, lat):
    if lon < LON0:
        lon += 360
    x = (lon - LON0) / (LON1 - LON0) * W
    y = (miller(LAT1) - miller(max(min(lat, 85), -85))) / (miller(LAT1) - miller(LAT0)) * H
    return x, y


def cities():
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), '..', 'hello.yaml')))
        return [project(lon, lat) for lat, lon in (cfg.get('cities') or {}).values()]
    except Exception:
        return []


def feather(rng):
    """keep(x, y) for a dot at map px (x, y): a superellipse over the padded image, its radius wobbled by slow noise."""
    x0, x1, y0, y1 = -PAD_L, W + PAD_R, -PAD_T, H + PAD_B
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    waves = [(rng.uniform(.004, .012), rng.uniform(.004, .012), rng.uniform(0, 6.3), rng.choice((-1, 1))) for _ in range(5)]
    pins = cities()

    def keep(x, y):
        if any((x - px) ** 2 + (y - py) ** 2 < 40 ** 2 for px, py in pins):
            return True
        d = (abs((x - cx) / rx) ** 4 + abs((y - cy) / ry) ** 4) ** 0.25
        if d >= 0.99:                                   # the wobble never brings a dot back to the image's edge
            return False
        d += 0.05 * sum(math.sin(fx * x + s * fy * y + ph) for fx, fy, ph, s in waves) / len(waves) ** 0.5
        t = min(max((d - KEEP) / (GONE - KEEP), 0), 1)
        return rng.random() >= t * t * (3 - 2 * t)
    return keep


def main(src, out):
    topo = json.load(open(src))
    sx, sy = topo['transform']['scale']
    tx, ty = topo['transform']['translate']
    arcs = []
    for arc in topo['arcs']:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        pts = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts
    IW, IH = W + PAD_L + PAD_R, H + PAD_T + PAD_B
    mask = Image.new('L', (IW * SCALE, IH * SCALE), 0)
    dr = ImageDraw.Draw(mask)
    for geom in topo['objects']['land']['geometries']:
        polys = geom['arcs'] if geom['type'] == 'MultiPolygon' else [geom['arcs']]
        for poly in polys:
            for k, r in enumerate(poly):
                pts = ring(r)
                if min(lat for _, lat in pts) < -60:           # Antarctica: off the map, its ring spans every longitude
                    continue
                for j in range(1, len(pts)):                   # unwrap rings crossing the date line (Eurasia, Fiji)
                    while pts[j][0] - pts[j - 1][0] > 180:
                        pts[j] = (pts[j][0] - 360, pts[j][1])
                    while pts[j][0] - pts[j - 1][0] < -180:
                        pts[j] = (pts[j][0] + 360, pts[j][1])
                for shift in (-360, 0, 360):                   # an unwrapped ring may sit a turn away from the map
                    xy = [(((lon + shift - LON0) / (LON1 - LON0) * W) + PAD_L, project(lon, lat)[1] + PAD_T) for lon, lat in pts]
                    dr.polygon([(x * SCALE, y * SCALE) for x, y in xy], fill=0 if k else 255)
    random.seed(42)
    keep = feather(random.Random(7))
    img = Image.new('RGBA', (IW * SCALE, IH * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = 2.6 * SCALE
    for gy in range(STEP // 2 - (PAD_T // STEP + 1) * STEP, H + PAD_B, STEP):     # the same grid as before, run outwards
        for gx in range(STEP // 2 - (PAD_L // STEP + 1) * STEP, W + PAD_R, STEP):
            if not (-PAD_L <= gx < W + PAD_R and -PAD_T <= gy < H + PAD_B):
                continue
            gx_, gy_ = gx + PAD_L, gy + PAD_T
            if mask.getpixel((gx_ * SCALE, gy_ * SCALE)) and keep(gx, gy):
                a = random.randint(110, 255)
                cx, cy = gx_ * SCALE, gy_ * SCALE
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, a))
    img.save(out, optimize=True)
    print(out, img.size)


if __name__ == '__main__':
    main(*sys.argv[1:3])
