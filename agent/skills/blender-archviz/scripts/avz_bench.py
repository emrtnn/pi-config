"""Render-device benchmark / crash test in a BACKGROUND Blender (never test a new GPU backend in the GUI).

usage:
  blender -b PROJECT.blend -P avz_bench.py -- --device CPU|HIP|HIPRT|CUDA|OPTIX|METAL|ONEAPI
          [--samples 64] [--res 960x540] [--cam NAME] [--out /tmp/bench.png]

Prints "BENCH {...json...}" on success.  Exit code 134/139 or 'HSA_STATUS_ERROR' / 'illegal' in the log
means the GPU backend crashes on this machine -> use CPU (check `journalctl -k | grep amdgpu` on Linux).
Nothing is saved to the .blend.
"""
import argparse
import json
import os
import sys
import time

import bpy

SK = os.path.dirname(os.path.abspath(__file__))
if SK not in sys.path:
    sys.path.insert(0, SK)
import avz_world  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
p = argparse.ArgumentParser(prog="avz_bench")
p.add_argument("--device", default="CPU")
p.add_argument("--samples", type=int, default=64)
p.add_argument("--res", default="960x540")
p.add_argument("--cam", default=None)
p.add_argument("--out", default="/tmp/avz_bench.png")
a = p.parse_args(argv)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
info = avz_world.configure_device(a.device)
scene.cycles.samples = a.samples
scene.cycles.use_adaptive_sampling = False
if hasattr(scene.cycles, "denoising_use_gpu"):
    scene.cycles.denoising_use_gpu = a.device != "CPU"
scene.render.resolution_x, scene.render.resolution_y = (int(x) for x in a.res.split("x"))
scene.render.resolution_percentage = 100
if a.cam:
    scene.camera = bpy.data.objects[a.cam]
if scene.camera is None:
    print("BENCH_ERROR no camera")
    sys.exit(2)
scene.render.filepath = a.out
t = time.time()
bpy.ops.render.render(write_still=True)
print("BENCH " + json.dumps({"device": a.device, "devices": info.get("devices"), "samples": a.samples,
                             "res": a.res, "seconds": round(time.time() - t, 1), "out": a.out}), flush=True)
