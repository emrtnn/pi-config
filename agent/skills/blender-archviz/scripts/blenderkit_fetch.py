#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests", "pillow"]
# ///
"""BlenderKit / Blendkit FREE assets without the add-on or an account (public API).

usage:
  blenderkit_fetch.py [--root PROJECT] search "<query>" [--type model|material|hdr|scene] [--n 12] [--extra "..."]
        -> contact sheet JPG + JSON (id, name, faces, real dimensions, licence) on stdout
  blenderkit_fetch.py [--root PROJECT] get <assetBaseId> [--res resolution_2K|resolution_1K|blend]

Search terms are English; results are ranked by score; only is_free:true assets are returned.  Free assets are
'royalty_free' (commercial use OK, no redistribution of the asset itself) or 'cc_zero'.  Check `faces`
(1M+ face trees are heavy) and `dims_m` (real-world size) before using an asset.
Files go to PROJECT/assets/blenderkit/<name>_<id8>/; sheets to PROJECT/previews/assets/.
"""
import argparse
import io
import json
import os
import sys
import uuid

import requests
from PIL import Image, ImageDraw

API = "https://www.blenderkit.com/api/v1"
S = requests.Session()
S.headers["User-Agent"] = "blender-archviz-skill"


def search(a):
    q = f"{a.query} asset_type:{a.type} is_free:true order:-score {a.extra}".strip()
    r = S.get(f"{API}/search/", params={"query": q, "page_size": a.n}, timeout=60)
    r.raise_for_status()
    res = r.json().get("results", [])
    rows, thumbs = [], []
    for i, x in enumerate(res):
        p = x.get("dictParameters", {})
        rows.append({"i": i, "id": x["assetBaseId"], "name": x["name"], "faces": p.get("faceCount"),
                     "dims_m": [p.get("dimensionX"), p.get("dimensionY"), p.get("dimensionZ")],
                     "license": x.get("license"), "files": [f["fileType"] for f in x.get("files", [])]})
        try:
            im = Image.open(io.BytesIO(S.get(x.get("thumbnailMiddleUrl") or x.get("thumbnailSmallUrl"),
                                             timeout=30).content)).convert("RGB").resize((256, 256))
        except Exception:  # noqa: BLE001
            im = Image.new("RGB", (256, 256), (60, 60, 60))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 256, 18], fill=(0, 0, 0))
        d.text((3, 3), f"{i}: {x['name'][:38]}", fill=(255, 255, 0))
        thumbs.append(im)
    cols = 4
    sheet = Image.new("RGB", (cols * 256, max(1, (len(thumbs) + cols - 1) // cols) * 256), (30, 30, 30))
    for i, im in enumerate(thumbs):
        sheet.paste(im, ((i % cols) * 256, (i // cols) * 256))
    fn = os.path.join(a.root, "previews", "assets", "bk_" + a.query.replace(" ", "_")[:40] + f"_{a.type}.jpg")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    sheet.save(fn, quality=85)
    print(json.dumps({"sheet": fn, "results": rows}))


def get(a):
    r = S.get(f"{API}/search/", params={"query": f"asset_base_id:{a.id}"}, timeout=60)
    r.raise_for_status()
    hits = r.json().get("results", [])
    if not hits:
        print(json.dumps({"error": "asset not found", "id": a.id}))
        sys.exit(1)
    x = hits[0]
    files = {f["fileType"]: f["downloadUrl"] for f in x["files"]}
    ft = a.res if a.res in files else "blend"
    info = S.get(files[ft], params={"scene_uuid": str(uuid.uuid4())}, timeout=60).json()
    if "filePath" not in info:
        print(json.dumps({"error": "download refused (not free?)", "detail": info}))
        sys.exit(1)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in x["name"])[:50]
    d = os.path.join(a.root, "assets", "blenderkit", f"{safe}_{a.id[:8]}")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, f"{safe}_{ft}.blend")
    if not os.path.exists(fn):
        with S.get(info["filePath"], stream=True, timeout=900) as resp:
            resp.raise_for_status()
            with open(fn + ".part", "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
        os.replace(fn + ".part", fn)
    p = x.get("dictParameters", {})
    print(json.dumps({"file": fn, "name": x["name"], "type": ft, "size_mb": round(os.path.getsize(fn) / 1e6, 1),
                      "dims_m": [p.get("dimensionX"), p.get("dimensionY"), p.get("dimensionZ")],
                      "faces": p.get("faceCount"), "license": x.get("license"),
                      "author": (x.get("author") or {}).get("fullName")}))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=os.getcwd())
    sp = p.add_subparsers(dest="cmd", required=True)
    ps = sp.add_parser("search")
    ps.add_argument("query")
    ps.add_argument("--type", default="model", choices=["model", "material", "hdr", "scene", "brush"])
    ps.add_argument("--n", type=int, default=12)
    ps.add_argument("--extra", default="")
    pg = sp.add_parser("get")
    pg.add_argument("id")
    pg.add_argument("--res", default="resolution_2K")
    a = p.parse_args()
    a.root = os.path.abspath(os.path.expanduser(a.root))
    (search if a.cmd == "search" else get)(a)


if __name__ == "__main__":
    main()
