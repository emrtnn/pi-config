#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests", "pillow"]
# ///
"""Poly Haven (CC0 - free for commercial use, no attribution required) browser/downloader.

usage:
  polyhaven_fetch.py [--root PROJECT] list {hdris,textures,models} [--cat CATEGORY] [--filter a,b] [--limit 24]
        -> contact sheet JPG (read it to choose visually) + JSON list on stdout
  polyhaven_fetch.py [--root PROJECT] hdri  <id> [--res 4k] [--fmt exr]
  polyhaven_fetch.py [--root PROJECT] tex   <id> [--res 2k]     (diff, nor_gl, rough, disp, ao, arm)
  polyhaven_fetch.py [--root PROJECT] model <id> [--res 2k]     (.blend + its textures)
  polyhaven_fetch.py info <id>

Files go to PROJECT/assets/polyhaven/{hdri,tex/<id>,models/<id>}; sheets to PROJECT/previews/assets/.
Useful categories: hdris: skies (ids ending in _puresky = sky only, no ground), outdoor, sunrise-sunset,
urban; textures: plaster, concrete, brick, roofing, wood, floor, tiles, asphalt, cobblestone, terrain;
models: plants, trees, furniture, outdoor.
"""
import argparse
import io
import json
import os
import sys

import requests
from PIL import Image, ImageDraw

API = "https://api.polyhaven.com"
S = requests.Session()
S.headers["User-Agent"] = "blender-archviz-skill"


def dl(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    with S.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path + ".part", "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    os.replace(path + ".part", path)
    return path


def sheet(items, fn, thumb_url, cols=6):
    thumbs = []
    for i, (k, _v) in enumerate(items):
        try:
            im = Image.open(io.BytesIO(S.get(thumb_url(k), timeout=30).content)).convert("RGB").resize((256, 256))
        except Exception:  # noqa: BLE001
            im = Image.new("RGB", (256, 256), (60, 60, 60))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 256, 16], fill=(0, 0, 0))
        d.text((3, 2), f"{i}: {k[:36]}", fill=(255, 255, 0))
        thumbs.append(im)
    rows = max(1, (len(thumbs) + cols - 1) // cols)
    out = Image.new("RGB", (cols * 256, rows * 256), (30, 30, 30))
    for i, im in enumerate(thumbs):
        out.paste(im, ((i % cols) * 256, (i // cols) * 256))
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    out.save(fn, quality=85)
    return fn


def cmd_list(a):
    params = {"t": a.type}
    if a.cat:
        params["c"] = a.cat
    d = S.get(f"{API}/assets", params=params, timeout=60).json()
    items = [(k, v) for k, v in d.items() if not a.filter or any(f in k for f in a.filter.split(","))]
    items.sort(key=lambda kv: -kv[1].get("download_count", 0))
    items = items[: a.limit]
    fn = os.path.join(a.root, "previews", "assets", f"ph_{a.type}_{a.cat or 'all'}_{(a.filter or '').replace(',', '+')[:30]}.jpg")
    sheet(items, fn, lambda k: f"https://cdn.polyhaven.com/asset_img/thumbs/{k}.png?width=256&height=256")
    rows = [{"i": i, "id": k, "name": v.get("name"), "dims_mm": v.get("dimensions"), "evs": v.get("evs_cap"),
             "tags": v.get("tags", [])[:6]} for i, (k, v) in enumerate(items)]
    print(json.dumps({"sheet": fn, "results": rows}))


def files(asset):
    r = S.get(f"{API}/files/{asset}", timeout=60)
    r.raise_for_status()
    return r.json()


def cmd_hdri(a):
    f = files(a.id)["hdri"]
    res = a.res if a.res in f else sorted(f, key=lambda s: int(s[:-1]))[-1]
    url = f[res][a.fmt]["url"]
    p = dl(url, os.path.join(a.root, "assets", "polyhaven", "hdri", os.path.basename(url)))
    print(json.dumps({"file": p, "license": "CC0"}))


def cmd_tex(a):
    f = files(a.id)
    want = {"Diffuse": "diff", "nor_gl": "nor_gl", "Rough": "rough", "Displacement": "disp", "AO": "ao", "arm": "arm"}
    if "Diffuse" not in f:              # colour variants (fabrics...): col_1, col_2, col_03 ...
        variants = sorted(k for k in f if k.lower().startswith("col"))
        if variants:
            pick = a.variant if a.variant in variants else variants[0]
            want = {pick: "diff", **{k: v for k, v in want.items() if k != "Diffuse"}}
    out = {}
    for key in want:
        if key in f:
            r = a.res if a.res in f[key] else sorted(f[key], key=lambda s: int(s[:-1]))[0]
            fmt = "jpg" if "jpg" in f[key][r] else ("png" if "png" in f[key][r] else list(f[key][r])[0])
            url = f[key][r][fmt]["url"]
            out[want[key]] = dl(url, os.path.join(a.root, "assets", "polyhaven", "tex", a.id, os.path.basename(url)))
    info = S.get(f"{API}/info/{a.id}", timeout=60).json()
    dims = info.get("dimensions")
    aspect = None
    if "diff" in out:
        with Image.open(out["diff"]) as im:
            aspect = round(im.size[0] / im.size[1], 3)
    warn = None
    if dims and aspect and abs((dims[0] / dims[1]) / aspect - 1) > 0.05:
        warn = "metadata dimensions do not match the image aspect - trust the image aspect"
    print(json.dumps({"asset": a.id, "maps": out, "dims_mm": dims, "image_aspect_w_h": aspect, "warning": warn,
                      "license": "CC0"}))


def cmd_model(a):
    b = files(a.id)["blend"]
    r = a.res if a.res in b else sorted(b, key=lambda s: int(s[:-1]))[0]
    entry = b[r]["blend"]
    base = os.path.join(a.root, "assets", "polyhaven", "models", a.id)
    main = dl(entry["url"], os.path.join(base, os.path.basename(entry["url"])))
    for rel, inc in entry.get("include", {}).items():
        dl(inc["url"], os.path.join(base, rel))
    info = S.get(f"{API}/info/{a.id}", timeout=60).json()
    print(json.dumps({"asset": a.id, "blend": main, "dims_mm": info.get("dimensions"), "license": "CC0"}))


def cmd_info(a):
    print(json.dumps(S.get(f"{API}/info/{a.id}", timeout=60).json()))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.getcwd(), help="project directory (default: cwd)")
    sp = p.add_subparsers(dest="cmd", required=True)
    pl = sp.add_parser("list")
    pl.add_argument("type", choices=["hdris", "textures", "models"])
    pl.add_argument("--cat", default="")
    pl.add_argument("--filter", default="")
    pl.add_argument("--limit", type=int, default=24)
    ph = sp.add_parser("hdri")
    ph.add_argument("id")
    ph.add_argument("--res", default="4k")
    ph.add_argument("--fmt", default="exr", choices=["exr", "hdr"])
    pt = sp.add_parser("tex")
    pt.add_argument("id")
    pt.add_argument("--res", default="2k")
    pt.add_argument("--variant", default="", help="colour variant for multi-colour sets (col_1, col_2...)")
    pm = sp.add_parser("model")
    pm.add_argument("id")
    pm.add_argument("--res", default="2k")
    pi = sp.add_parser("info")
    pi.add_argument("id")
    a = p.parse_args()
    a.root = os.path.abspath(os.path.expanduser(a.root))
    try:
        {"list": cmd_list, "hdri": cmd_hdri, "tex": cmd_tex, "model": cmd_model, "info": cmd_info}[a.cmd](a)
    except requests.HTTPError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
