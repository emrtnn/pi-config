#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow"]
# ///
"""Combine renders/previews into one labelled overview image (one `read` to review many images).

usage: contact_sheet.py OUT.jpg IMG [IMG ...] [--cols 3] [--width 640]
"""
import argparse
import os

from PIL import Image, ImageDraw


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("images", nargs="+")
    p.add_argument("--cols", type=int, default=3)
    p.add_argument("--width", type=int, default=640)
    a = p.parse_args()
    tiles = []
    for f in a.images:
        im = Image.open(f).convert("RGB")
        h = int(im.height * a.width / im.width)
        im = im.resize((a.width, h), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, a.width, 16], fill=(0, 0, 0))
        d.text((4, 2), os.path.basename(f)[:90], fill=(255, 255, 0))
        tiles.append(im)
    rows = (len(tiles) + a.cols - 1) // a.cols
    row_h = [max(t.height for t in tiles[r * a.cols:(r + 1) * a.cols]) for r in range(rows)]
    sheet = Image.new("RGB", (a.cols * a.width, sum(row_h)), (25, 25, 25))
    y = 0
    for r in range(rows):
        for c, t in enumerate(tiles[r * a.cols:(r + 1) * a.cols]):
            sheet.paste(t, (c * a.width, y))
        y += row_h[r]
    sheet.save(a.out, quality=88)
    print(a.out)


if __name__ == "__main__":
    main()
