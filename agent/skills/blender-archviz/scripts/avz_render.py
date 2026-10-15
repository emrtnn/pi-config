"""Batch final renders in a BACKGROUND Blender (no MCP timeouts, GUI session stays safe).

usage:
  blender -b PROJECT.blend -P avz_render.py -- [--cams CAM_A,CAM_B | --cams all] [--res 3840x2160] [--pct 100]
          [--samples 512] [--noise 0.01] [--device CPU|HIP|HIPRT|CUDA|OPTIX|METAL|ONEAPI] [--out DIR]
          [--prefix NAME_] [--exr] [--jpg] [--look "AgX - Medium High Contrast"] [--exposure 0.0]
          [--time-limit SECONDS]

Defaults: all cameras of collection "Cameras" (names starting with "_" or "AVZ_" are skipped),
resolution/samples from the .blend, output PROJECT/renders/.  Per-camera overrides via custom properties on
the camera object: avz_res = "2160x3840", avz_samples = 1024, avz_exposure = 0.3.
Writes PNG 16-bit (+ optional multilayer EXR / JPG 92) and appends a JSON line per image to
renders/render_log.jsonl.  Run it with `nohup ... &` for long batches and poll the log.
"""
import argparse
import datetime
import json
import os
import sys
import time

import bpy

SK = os.path.dirname(os.path.abspath(__file__))
if SK not in sys.path:
    sys.path.insert(0, SK)


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="avz_render")
    p.add_argument("--cams", default="all")
    p.add_argument("--res", default=None)
    p.add_argument("--pct", type=int, default=None)
    p.add_argument("--samples", type=int, default=None)
    p.add_argument("--noise", type=float, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--prefix", default="")
    p.add_argument("--exr", action="store_true")
    p.add_argument("--jpg", action="store_true")
    p.add_argument("--look", default=None)
    p.add_argument("--exposure", type=float, default=None)
    p.add_argument("--time-limit", type=float, default=None)
    return p.parse_args(argv)


def main():
    a = parse()
    scene = bpy.context.scene
    proj = scene.get("avz_project") or os.path.dirname(bpy.data.filepath)
    out = os.path.abspath(a.out or os.path.join(proj, "renders"))
    os.makedirs(out, exist_ok=True)
    if a.device:
        import avz_world
        print("DEVICE", avz_world.configure_device(a.device))
    scene.render.engine = "CYCLES"
    if a.samples:
        scene.cycles.samples = a.samples
    if a.noise is not None:
        scene.cycles.adaptive_threshold = a.noise
    if a.time_limit:
        scene.cycles.time_limit = a.time_limit
    if a.pct:
        scene.render.resolution_percentage = a.pct
    if a.look:
        scene.view_settings.look = a.look
    if a.cams == "all":
        coll = bpy.data.collections.get("Cameras")
        cams = [o for o in (coll.all_objects if coll else scene.objects) if o.type == "CAMERA"
                and not o.name.startswith(("_", "AVZ_"))]
    else:
        cams = [bpy.data.objects[n] for n in a.cams.split(",")]
    base_res = (scene.render.resolution_x, scene.render.resolution_y)
    base_samples, base_exp = scene.cycles.samples, scene.view_settings.exposure
    for cam in sorted(cams, key=lambda o: o.name):
        res = cam.get("avz_res") or a.res
        rx, ry = (int(x) for x in res.lower().split("x")) if res else base_res
        scene.render.resolution_x, scene.render.resolution_y = rx, ry
        scene.cycles.samples = int(cam.get("avz_samples", base_samples))
        scene.view_settings.exposure = float(cam.get("avz_exposure", a.exposure if a.exposure is not None else base_exp))
        scene.camera = cam
        stem = os.path.join(out, f"{a.prefix}{cam.name}_{rx}x{ry}")
        t0 = time.time()
        bpy.ops.render.render(write_still=False)
        img = bpy.data.images["Render Result"]
        fs = scene.render.image_settings
        files = []
        alpha = "RGBA" if scene.render.film_transparent else "RGB"
        fs.file_format, fs.color_depth, fs.color_mode = "PNG", "16", alpha
        img.save_render(stem + ".png", scene=scene)
        files.append(stem + ".png")
        if a.jpg:
            fs.file_format, fs.color_mode, fs.color_depth, fs.quality = "JPEG", "RGB", "8", 92
            img.save_render(stem + ".jpg", scene=scene)
            files.append(stem + ".jpg")
        if a.exr:                                  # Blender 5.x: multilayer is a media_type, 4.x: a file_format
            if hasattr(fs, "media_type"):
                fs.media_type = "MULTI_LAYER_IMAGE"
            fs.file_format, fs.color_mode, fs.color_depth, fs.exr_codec = "OPEN_EXR_MULTILAYER", "RGBA", "32", "ZIP"
            img.save_render(stem + ".exr", scene=scene)
            files.append(stem + ".exr")
            if hasattr(fs, "media_type"):
                fs.media_type = "IMAGE"
        fs.file_format, fs.color_depth = "PNG", "16"
        dt = time.time() - t0
        rec = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "camera": cam.name,
               "res": [rx, ry], "samples": scene.cycles.samples, "device": scene.cycles.device,
               "seconds": round(dt, 1), "files": files, "look": scene.view_settings.look,
               "exposure": scene.view_settings.exposure}
        with open(os.path.join(out, "render_log.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print("RENDERED", json.dumps(rec), flush=True)


main()
