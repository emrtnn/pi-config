"""avz_common - shared helpers for the blender-archviz skill.  Runs INSIDE Blender (needs bpy).

Bootstrap (MCP execute_blender_code or `blender -b -P`):
    import sys, os
    SK = os.path.expanduser("~/.pi/agent/skills/blender-archviz/scripts")
    sys.path.insert(0, SK) if SK not in sys.path else None
    import avz_common; avz_common.reload_all()          # (re)loads every avz_* module after edits
    avz_common.set_project("/path/to/project")          # where previews/, renders/, assets/ live

Conventions used by all avz_* modules
  * Blender units = metres, Z up.  Compass bearings: 0 = +Y (model north), 90 = +X (east), clockwise.
  * Frame: a building-aligned 2-D frame (u along the main facade direction, v perpendicular).
"""
import importlib
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

SKILL_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SKILL_SCRIPTS)
MODULES = ["avz_common", "avz_materials", "avz_matrules", "avz_world", "avz_scene", "avz_site", "avz_assets",
           "avz_import_datasmith"]


# ----------------------------------------------------------------------------- module management
def reload_all():
    """(Re)import all avz_* modules in dependency order (call after editing any script)."""
    if SKILL_SCRIPTS not in sys.path:
        sys.path.insert(0, SKILL_SCRIPTS)
    loaded = []
    for name in MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
        loaded.append(name)
    return loaded


# ----------------------------------------------------------------------------- project paths
def set_project(path):
    """Store the project root in the scene (persists in the .blend)."""
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    bpy.context.scene["avz_project"] = path
    return path


def project_dir():
    p = bpy.context.scene.get("avz_project")
    if p:
        return p
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    return os.getcwd()


def subdir(*parts):
    d = os.path.join(project_dir(), *parts)
    os.makedirs(d, exist_ok=True)
    return d


def tool_path(name):
    """Absolute path of a CLI tool shipped with the skill (e.g. 'polyhaven_fetch.py')."""
    return os.path.join(SKILL_SCRIPTS, name)


def clean_env():
    """Environment for subprocesses launched FROM Blender: drop Blender's PYTHONPATH/PYTHONHOME etc.,
    otherwise uv's interpreter imports Blender's (wrong-ABI) numpy and crashes."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and k != "_UDSMESH_LIBFIX"}


def find_uv():
    import shutil
    for p in (shutil.which("uv"), "/etc/profiles/per-user/%s/bin/uv" % os.environ.get("USER", ""),
              os.path.expanduser("~/.local/bin/uv"), os.path.expanduser("~/.cargo/bin/uv"),
              "/run/current-system/sw/bin/uv", "/usr/local/bin/uv", "/usr/bin/uv"):
        if p and os.path.exists(p):
            return p
    return None


def run_tool(name, *args, timeout=3600):
    """Run one of the skill's uv scripts from inside Blender. Returns CompletedProcess."""
    import subprocess
    uv = find_uv()
    if uv is None:
        raise RuntimeError("uv not found - run %s %s from a shell" % (name, " ".join(map(str, args))))
    return subprocess.run([uv, "run", "--script", tool_path(name), *map(str, args)], capture_output=True, text=True,
                          timeout=timeout, env=clean_env())


# ----------------------------------------------------------------------------- data helpers
def collection(name, parent=None, exclude=False, hide_render=False):
    """Get/create a collection linked under `parent` (default: scene root)."""
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    par = parent if parent is not None else bpy.context.scene.collection
    if isinstance(par, str):
        par = collection(par)
    if c.name not in [ch.name for ch in par.children]:
        par.children.link(c)
    c.hide_render = hide_render
    if exclude:
        lc = find_layer_collection(c.name)
        if lc is not None:
            lc.exclude = True
    return c


def find_layer_collection(name, lc=None):
    lc = lc or bpy.context.view_layer.layer_collection
    if lc.collection.name == name:
        return lc
    for ch in lc.children:
        r = find_layer_collection(name, ch)
        if r is not None:
            return r
    return None


def clear_collection(coll):
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def mesh_object(name, verts, faces, coll, materials=(), replace=True, smooth=False):
    """Create a mesh object from python lists/arrays (data API, fast, no operators)."""
    if replace and name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.validate()
    me.update()
    for m in materials:
        me.materials.append(m)
    if smooth:
        me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), bool))
    ob = bpy.data.objects.new(name, me)
    if isinstance(coll, str):
        coll = collection(coll)
    coll.objects.link(ob)
    return ob


def world_vertices(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    M = np.array(ob.matrix_world)
    return co @ M[:3, :3].T + M[:3, 3]


def bbox_points(objs):
    """8 world-space bound-box corners per object -> (N,3) array (cheap proxy of the geometry)."""
    pts = []
    for o in objs:
        M = o.matrix_world
        pts.extend((M @ Vector(c))[:] for c in o.bound_box)
    return np.array(pts) if pts else np.zeros((0, 3))


def bbox(objs):
    P = bbox_points(objs)
    return Vector(P.min(0)), Vector(P.max(0))


SUPPORT_COLLECTIONS = ("Site", "Context", "Entourage", "AVZ_Library", "Lighting", "Cameras")


def tag_model(objs):
    """Mark imported building objects (FBX/OBJ/glTF/IFC...).  Datasmith imports are tagged automatically."""
    for o in objs:
        o["avz_model"] = True
    return len(objs)


def model_objects(scene=None):
    """The architect's model: objects tagged by an importer (ds_name / avz_model); fallback = every mesh that
    is not in the support collections created by the skill (Site, Context, Entourage, ...)."""
    meshes = mesh_objects(scene)
    tagged = [o for o in meshes if o.get("ds_name") or o.get("avz_model")]
    if tagged:
        return tagged
    return [o for o in meshes if not any(c.name in SUPPORT_COLLECTIONS or c.name.startswith("AVZ")
                                         for c in o.users_collection)]


def mesh_objects(scene=None, collections=None, visible_only=True):
    scene = scene or bpy.context.scene
    objs = [o for o in scene.objects if o.type == "MESH"]
    if visible_only:
        objs = [o for o in objs if not o.hide_render and o.visible_get()]
    if collections:
        names = set(collections)
        objs = [o for o in objs if any(c.name in names for c in o.users_collection)]
    return objs


# ----------------------------------------------------------------------------- building frame
class Frame:
    """Building-aligned 2-D frame. u axis at `angle_deg` CCW from +X, origin at world (x0, y0)."""

    def __init__(self, angle_deg=0.0, origin=(0.0, 0.0)):
        self.angle_deg = float(angle_deg)
        self.a = math.radians(self.angle_deg)
        self.c, self.s = math.cos(self.a), math.sin(self.a)
        self.x0, self.y0 = float(origin[0]), float(origin[1])

    def to_world(self, u, v, z=0.0):
        return Vector((self.x0 + u * self.c - v * self.s, self.y0 + u * self.s + v * self.c, z))

    def xy(self, u, v):
        return (self.x0 + u * self.c - v * self.s, self.y0 + u * self.s + v * self.c)

    def from_world(self, x, y):
        dx, dy = x - self.x0, y - self.y0
        return (dx * self.c + dy * self.s, -dx * self.s + dy * self.c)

    def uvz(self, P):
        P = np.asarray(P, float)
        dx, dy = P[:, 0] - self.x0, P[:, 1] - self.y0
        return np.stack([dx * self.c + dy * self.s, -dx * self.s + dy * self.c, P[:, 2]], 1)

    def u_bearing(self):
        """Compass bearing of the +u direction (0=+Y north, clockwise)."""
        return (90.0 - self.angle_deg) % 360.0

    def outward_bearing(self, side):
        """Compass bearing a facade faces. side in {'u-','u+','v-','v+'}."""
        ang = {"u+": self.angle_deg, "u-": self.angle_deg + 180, "v+": self.angle_deg + 90,
               "v-": self.angle_deg - 90}[side]
        return (90.0 - ang) % 360.0

    def as_dict(self):
        return {"angle_deg": round(self.angle_deg, 3), "origin": [round(self.x0, 3), round(self.y0, 3)]}

    @classmethod
    def from_dict(cls, d):
        return cls(d["angle_deg"], d["origin"])

    def store(self, key="avz_frame"):
        bpy.context.scene[key] = self.as_dict()

    @classmethod
    def load(cls, key="avz_frame"):
        d = bpy.context.scene.get(key)
        return cls.from_dict(dict(d)) if d else None


def detect_frame(objs=None, store=True):
    """Dominant horizontal orientation from vertical faces (area-weighted circular mean of 4*theta).

    Returns (Frame, extents) where the frame origin is the (u_min, v_min) corner of the geometry and
    extents = dict(u=(0,len_u), v=(0,len_v), z=(zmin,zmax), strength=0..1).  strength < 0.5 means
    the plan is not orthogonal (curved / multiple grids) -> pick the angle manually.
    """
    objs = objs or model_objects()
    acc = 0j
    tot = 0.0
    for o in objs:
        me = o.data
        n = len(me.polygons)
        if n == 0:
            continue
        nrm = np.empty(n * 3, np.float32)
        me.polygons.foreach_get("normal", nrm)
        area = np.empty(n, np.float32)
        me.polygons.foreach_get("area", area)
        R = np.array(o.matrix_world.to_3x3().normalized())
        N = nrm.reshape(-1, 3) @ R.T
        vert = np.abs(N[:, 2]) < 0.2
        if not vert.any():
            continue
        ang = np.arctan2(N[vert, 1], N[vert, 0])
        w = area[vert] * float(abs(o.matrix_world.determinant())) ** (2 / 3)
        acc += np.sum(w * np.exp(4j * ang))
        tot += float(w.sum())
    if tot == 0:
        return Frame(0.0), {}
    # face normals are perpendicular to walls -> wall direction = normal angle + 90 (same mod 90)
    angle = (math.degrees(np.angle(acc) / 4.0)) % 90.0
    fr = Frame(angle)
    P = np.concatenate([world_vertices(o) for o in objs])
    U = fr.uvz(P)
    umin, vmin = float(U[:, 0].min()), float(U[:, 1].min())
    fr = Frame(angle, fr.xy(umin, vmin))
    U = fr.uvz(P)
    ext = {"u": (0.0, round(float(U[:, 0].max()), 3)), "v": (0.0, round(float(U[:, 1].max()), 3)),
           "z": (round(float(U[:, 2].min()), 3), round(float(U[:, 2].max()), 3)),
           "strength": round(float(abs(acc) / tot), 3)}
    if store:
        fr.store()
        bpy.context.scene["avz_extents"] = {k: list(v) if isinstance(v, tuple) else v for k, v in ext.items()}
    return fr, ext


def side_profile(frame, objs=None, step=2.0, band=0.6):
    """Lowest geometry z along each footprint side (helps to infer street/terrain grades).

    Returns {side: [(u_or_v_center, zmin), ...]} for sides 'v-' (v=vmin), 'v+', 'u-', 'u+'.
    Filter `objs` (e.g. only doors/windows/plinth) to read sills and ground contact lines.
    """
    objs = objs or model_objects()
    P = frame.uvz(np.concatenate([world_vertices(o) for o in objs]))
    out = {}
    lo, hi = P.min(0), P.max(0)
    for side, axis, val, along in (("v-", 1, lo[1], 0), ("v+", 1, hi[1], 0), ("u-", 0, lo[0], 1), ("u+", 0, hi[0], 1)):
        sel = np.abs(P[:, axis] - val) < band
        rows = []
        a0, a1 = lo[along], hi[along]
        t = a0
        while t < a1:
            s2 = sel & (P[:, along] >= t) & (P[:, along] < t + step)
            if s2.any():
                rows.append((round(float(t + step / 2), 2), round(float(P[s2, 2].min()), 3)))
            t += step
        out[side] = rows
    return out
