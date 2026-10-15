"""avz_scene - cameras, quick previews, analysis renders and scene audit.  Runs INSIDE Blender.

Archviz camera rules baked in:
  * Exterior eye-level shots are LEVEL cameras (pitch 90 deg, no roll) -> verticals stay vertical
    ("two-point perspective"); framing is done with lens shift, never by tilting.
  * fit_level_camera() solves yaw / focal length / shift so a set of points (building bbox + anything you
    want in frame) fits with a margin.
"""
import math
import os

import bpy
import numpy as np
from mathutils import Vector

import avz_common as C


# ----------------------------------------------------------------------------- cameras
def get_camera(name, collection_name="Cameras"):
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        C.collection(collection_name).objects.link(ob)
    cam = ob.data
    cam.sensor_fit = "HORIZONTAL"
    cam.clip_start = 0.05
    cam.clip_end = 10000.0
    return ob


def look_at(ob, target):
    d = Vector(target) - ob.location
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def level_camera(name, eye, target, lens=24.0, sensor=36.0, shift=(0.0, 0.0)):
    """Level (two-point perspective) camera at `eye` looking horizontally towards `target`."""
    ob = get_camera(name)
    ob.location = Vector(eye)
    d = Vector(target) - ob.location
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = (math.pi / 2, 0.0, math.atan2(d.y, d.x) - math.pi / 2)
    ob.data.lens = lens
    ob.data.sensor_width = sensor
    ob.data.shift_x, ob.data.shift_y = shift
    return ob


def orbit_camera(name, target, bearing_deg, elevation_deg, distance, lens=35.0):
    """Aerial / bird's-eye camera: placed at compass `bearing_deg` FROM the target, looking at it."""
    b, e = math.radians(bearing_deg), math.radians(elevation_deg)
    t = Vector(target)
    ob = get_camera(name)
    ob.location = t + distance * Vector((math.sin(b) * math.cos(e), math.cos(b) * math.cos(e), math.sin(e)))
    look_at(ob, t)
    ob.data.lens = lens
    ob.data.shift_x = ob.data.shift_y = 0.0
    return ob


def fit_level_camera(cam, objs=None, points=None, margin=0.05, lens=None, lens_range=(14.0, 135.0), recenter=True,
                     res=None):
    """Solve yaw (centre horizontally), focal length and shift_y so `points` (default: bound-box corners of
    `objs`, default all visible meshes) fill the frame with `margin` (fraction per side).
    Keeps the camera level.  Returns lens/shift and warnings (shift > 0.5 or lens < 16 look distorted)."""
    scene = bpy.context.scene
    rx, ry = res or (scene.render.resolution_x, scene.render.resolution_y)
    P = np.asarray(points, float) if points is not None else C.bbox_points(objs or C.model_objects())
    eye = np.array(cam.location[:])
    cam.rotation_mode = "XYZ"
    yaw = cam.rotation_euler.z
    for _ in range(3):
        fwd = np.array([-math.sin(yaw), math.cos(yaw), 0.0])
        right = np.array([math.cos(yaw), math.sin(yaw), 0.0])
        d = P - eye
        zc = d @ fwd
        if (zc <= 0.05).any():
            return {"error": "some points are behind/at the camera - move it back or pass fewer points",
                    "behind": int((zc <= 0.05).sum())}
        ax, ay = (d @ right) / zc, d[:, 2] / zc
        th = np.arctan(ax)
        c = (th.max() + th.min()) / 2
        if not recenter or abs(c) < 1e-4:
            break
        yaw -= c
    W = cam.data.sensor_width
    H = W * ry / rx
    need_w, need_h = ax.max() - ax.min(), ay.max() - ay.min()
    f = lens if lens is not None else min(W * (1 - 2 * margin) / max(need_w, 1e-6),
                                          H * (1 - 2 * margin) / max(need_h, 1e-6))
    warn = []
    if lens is None and f < lens_range[0]:
        warn.append("needs %.1fmm (< %.0fmm): move camera back or accept cropping" % (f, lens_range[0]))
    f = min(max(f, lens_range[0]), lens_range[1])
    cam.rotation_euler = (math.pi / 2, 0.0, yaw)
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.lens = f
    cam.data.shift_x = f * (ax.max() + ax.min()) / 2 / W
    cam.data.shift_y = f * (ay.max() + ay.min()) / 2 / W
    if abs(cam.data.shift_y) > 0.5:
        warn.append("large vertical shift %.2f: raise the camera or move it back" % cam.data.shift_y)
    return {"lens": round(f, 2), "shift_x": round(cam.data.shift_x, 3), "shift_y": round(cam.data.shift_y, 3),
            "yaw_deg": round(math.degrees(yaw), 2), "warnings": warn}


def frame_points(frame, u0, u1, v0, v1, z0, z1):
    """Box corners in a building Frame - handy `points` for fit_level_camera."""
    return np.array([frame.to_world(u, v, z)[:] for u in (u0, u1) for v in (v0, v1) for z in (z0, z1)])


# ----------------------------------------------------------------------------- renders
def _swap(scene, **kw):
    old = {}
    for k, v in kw.items():
        obj, attr = k.split("__")
        tgt = {"render": scene.render, "cycles": scene.cycles, "scene": scene}[obj]
        old[k] = getattr(tgt, attr)
        setattr(tgt, attr, v)
    return old


def _restore(scene, old):
    for k, v in old.items():
        obj, attr = k.split("__")
        tgt = {"render": scene.render, "cycles": scene.cycles, "scene": scene}[obj]
        setattr(tgt, attr, v)


def cycles_preview(path, cam, res=(960, 540), samples=32, noise=0.05):
    """Small Cycles render with the scene's look/world. Keep < ~1 min (MCP calls are synchronous)."""
    scene = bpy.context.scene
    cam = bpy.data.objects[cam] if isinstance(cam, str) else cam
    old = _swap(scene, scene__camera=cam, render__resolution_x=res[0], render__resolution_y=res[1],
                render__resolution_percentage=100, cycles__samples=samples, cycles__adaptive_threshold=noise,
                render__filepath=path, render__engine="CYCLES")
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        _restore(scene, old)
    return path


def workbench(path, cam, res=(1280, 720), shadows=False, cavity=True, color_type="MATERIAL"):
    """Fast solid-colour analysis render.  shadows=False by default: Workbench stencil shadows produce huge
    fake 'light fans' with open/non-manifold BIM meshes (glazing, railings)."""
    scene = bpy.context.scene
    cam = bpy.data.objects[cam] if isinstance(cam, str) else cam
    sh = scene.display.shading
    keep = (sh.light, sh.color_type, sh.show_shadows, sh.show_cavity)
    old = _swap(scene, render__engine="BLENDER_WORKBENCH", scene__camera=cam, render__resolution_x=res[0],
                render__resolution_y=res[1], render__resolution_percentage=100, render__filepath=path)
    sh.light, sh.color_type, sh.show_shadows, sh.show_cavity = "STUDIO", color_type, shadows, cavity
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        _restore(scene, old)
        sh.light, sh.color_type, sh.show_shadows, sh.show_cavity = keep
    return path


def analysis_views(objs=None, out_dir=None, res=(1000, 700), prefix="an"):
    """Top orthographic + 4 axonometric workbench views around the model -> list of PNG paths."""
    objs = objs or C.model_objects()
    out_dir = out_dir or C.subdir("previews")
    mn, mx = C.bbox(objs)
    ctr = (mn + mx) / 2
    size = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z)
    cam = get_camera("AVZ_analysis")
    paths = []
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = size * 1.1
    cam.location = (ctr.x, ctr.y, mx.z + size)
    cam.rotation_euler = (0, 0, 0)
    paths.append(workbench(os.path.join(out_dir, f"{prefix}_top.png"), cam, res))
    cam.data.type = "PERSP"
    for name, b in (("ne", 45), ("se", 135), ("sw", 225), ("nw", 315)):
        orbit_camera("AVZ_analysis", ctr, b, 30, size * 1.6, lens=35)
        paths.append(workbench(os.path.join(out_dir, f"{prefix}_{name}.png"), cam, res))
    return paths


# ----------------------------------------------------------------------------- audit
def audit():
    """Quick health report of the scene (call after import and before final renders)."""
    scene = bpy.context.scene
    objs = list(scene.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    by_type = {}
    for o in objs:
        by_type[o.type] = by_type.get(o.type, 0) + 1
    rep = {"objects": by_type, "mesh_data": len({o.data for o in meshes}),
           "triangles_unique": int(sum(len(me.loops) - 2 * len(me.polygons) for me in {o.data for o in meshes})),
           "materials": len([m for m in bpy.data.materials if m.users]),
           "images_missing": [i.name for i in bpy.data.images if i.source == "FILE" and i.filepath
                              and not i.packed_file and not os.path.exists(bpy.path.abspath(i.filepath))],
           "cameras": {o.name: round(o.data.lens, 1) for o in objs if o.type == "CAMERA"},
           "lights": by_type.get("LIGHT", 0)}
    if meshes:
        mn, mx = C.bbox(meshes)
        rep["bbox_min"] = [round(x, 2) for x in mn]
        rep["bbox_max"] = [round(x, 2) for x in mx]
        far = max(abs(x) for x in list(mn) + list(mx))
        if far > 5000:
            rep.setdefault("warnings", []).append("geometry %.0f m from origin: recentre (float precision)" % far)
        if (mx - mn).length > 20000 or (mx - mn).length < 1:
            rep.setdefault("warnings", []).append("odd model size %.1f m: check unit scale" % (mx - mn).length)
    neg = [o.name for o in meshes if o.matrix_world.determinant() < 0]
    if neg:
        rep["mirrored_objects"] = len(neg)    # normal for mirrored BIM families; Cycles handles them
    nonuni = [o.name for o in meshes if max(o.scale) - min(o.scale) > 1e-3]
    if nonuni:
        rep["non_uniform_scale"] = len(nonuni)
    no_mat = [o.name for o in meshes if not any(s.material for s in o.material_slots)]
    if no_mat:
        rep["objects_without_material"] = len(no_mat)
    r = scene.render
    rep["render"] = {"engine": r.engine, "res": [r.resolution_x, r.resolution_y, r.resolution_percentage],
                     "device": scene.cycles.device if r.engine == "CYCLES" else None,
                     "samples": scene.cycles.samples, "view": scene.view_settings.view_transform,
                     "look": scene.view_settings.look, "world": scene.world.name if scene.world else None}
    return rep
