"""Draws hello-deck/brands/world-dots.png: the dotted world map behind the upcoming-events slides (white dots on
transparent, tinted per brand in the page with a CSS mask). Run once; the PNG is committed.

  curl -sLo land-110m.json https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json   # Natural Earth, public domain
  python3 hello-deck/tools/make_world_dots.py land-110m.json hello-deck/brands/world-dots.png

The projection (Miller, longitude -130..110, latitude -38..64) must match MAP in _build/hello.py, which places the pins.
"""
import json
import math
import random
import sys

from PIL import Image, ImageDraw

LON0, LON1, LAT0, LAT1 = -130.0, 110.0, -38.0, 64.0   # the Americas to India: where the events are
W = 1600                    # the map box on the 1920x1080 slide (drawn at 2x); H keeps the projection undistorted
SCALE = 2
STEP = 9                    # px between dots at 1x


def miller(lat):
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


H = round(W * (miller(LAT1) - miller(LAT0)) / math.radians(LON1 - LON0))


def project(lon, lat):
    if lon < LON0:
        lon += 360
    x = (lon - LON0) / (LON1 - LON0) * W
    y = (miller(LAT1) - miller(max(min(lat, 85), -85))) / (miller(LAT1) - miller(LAT0)) * H
    return x, y


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
    mask = Image.new('L', (W * SCALE, H * SCALE), 0)
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
                    xy = [(((lon + shift - LON0) / (LON1 - LON0) * W), project(lon, lat)[1]) for lon, lat in pts]
                    dr.polygon([(x * SCALE, y * SCALE) for x, y in xy], fill=0 if k else 255)
    random.seed(42)
    img = Image.new('RGBA', (W * SCALE, H * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = 2.6 * SCALE
    for gy in range(STEP // 2, H, STEP):
        for gx in range(STEP // 2, W, STEP):
            if mask.getpixel((gx * SCALE, gy * SCALE)):
                a = random.randint(110, 255)
                cx, cy = gx * SCALE, gy * SCALE
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, a))
    img.save(out, optimize=True)
    print(out, img.size)


if __name__ == '__main__':
    main(*sys.argv[1:3])
