"""avz_assets - bring downloaded assets (.blend from Poly Haven / BlenderKit / own library) into the scene as
lightweight collection instances, and scatter them.  Runs INSIDE Blender.

    tree = avz_assets.append_blend("/proj/assets/polyhaven/models/tree_small_02/tree_small_02_2k.blend")
    avz_assets.instance(tree, (10, 4, 0), rot_z_deg=30)
    avz_assets.scatter_line([tree], p0, p1, spacing=8, snap_to=[road_obj])          # street trees
    avz_assets.scatter_area([bush1, bush2], polygon_xy, density=0.3, min_dist=1.2)  # planting beds
    avz_assets.gn_scatter(lawn_obj, grass_coll, density=400)                          # lawns / ground cover
"""
import math
import os
import random

import bpy
import numpy as np
from mathutils import Vector

import avz_common as C
import avz_materials as M

KEEP_TYPES = ("MESH", "EMPTY", "CURVES", "CURVE", "POINTCLOUD")


def append_blend(path, name=None, library="AVZ_Library", types=KEEP_TYPES, max_dim=None):
    """Append all objects of `path` into a hidden collection (excluded from the view layer) for instancing.
    instance_offset = bottom-centre of the asset, so instances stand exactly on their location.
    Returns the collection; coll['avz_dims'] = real size in metres (CHECK IT - some assets are mis-scaled)."""
    name = name or os.path.splitext(os.path.basename(path))[0]
    lib = C.collection(library)
    lc = C.find_layer_collection(lib.name)
    if lc is not None:
        lc.exclude = True
    coll = bpy.data.collections.get(name)
    if coll is not None and len(coll.all_objects):
        return coll
    coll = coll or bpy.data.collections.new(name)
    if coll.name not in [c.name for c in lib.children]:
        lib.children.link(coll)
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = list(src.objects)
    for ob in dst.objects:
        if ob is None:
            continue
        if ob.type not in types:
            bpy.data.objects.remove(ob, do_unlink=True)
            continue
        coll.objects.link(ob)
    meshes = [o for o in coll.objects if o.type == "MESH" and not o.hide_render]
    if meshes:
        mn, mx = C.bbox(meshes)
        coll.instance_offset = ((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z)
        coll["avz_dims"] = [round(x, 3) for x in (mx - mn)]
        if max_dim and max(mx - mn) > max_dim:
            coll["avz_warning"] = "asset larger than max_dim - check units"
    coll["avz_source"] = path
    return coll


def instance(coll, location, rot_z_deg=0.0, scale=1.0, name=None, target="Entourage"):
    ob = bpy.data.objects.new(name or f"{coll.name}_i", None)
    ob.instance_type = "COLLECTION"
    ob.instance_collection = coll
    ob.location = Vector(location)
    ob.rotation_euler = (0.0, 0.0, math.radians(rot_z_deg))
    ob.scale = (scale,) * 3 if isinstance(scale, (int, float)) else scale
    ob.empty_display_size = 0.5
    C.collection(target).objects.link(ob)
    return ob


def ground_z(x, y, snap_to=None, z_from=2000.0, default=None):
    """Height of the first surface below (x, y).  snap_to: list of objects to accept (others are skipped)."""
    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    allowed = {o.name for o in snap_to} if snap_to else None
    origin = Vector((x, y, z_from))
    for _ in range(64):
        hit, loc, _n, _i, ob, _m = scene.ray_cast(dg, origin, Vector((0, 0, -1)))
        if not hit:
            return default
        if allowed is None or (ob is not None and ob.name in allowed):
            return loc.z
        origin = loc - Vector((0, 0, 0.001))
    return default


def _place(assets, xy, rnd, scale, snap_to, target, prefix, i, rot=None):
    z = ground_z(xy[0], xy[1], snap_to, default=0.0) if snap_to is not None else 0.0
    coll = rnd.choice(assets)
    return instance(coll, (xy[0], xy[1], z), rot if rot is not None else rnd.uniform(0, 360), rnd.uniform(*scale),
                    f"{prefix}_{i:03d}", target)


def scatter_line(assets, p0, p1, spacing, jitter=0.3, scale=(0.9, 1.15), seed=0, snap_to=None, target="Entourage",
                 prefix="line", start_offset=None):
    """Trees along a street etc.  p0/p1 world (x, y).  Returns the instances."""
    rnd = random.Random(seed)
    p0, p1 = np.array(p0[:2], float), np.array(p1[:2], float)
    L = float(np.linalg.norm(p1 - p0))
    d = (p1 - p0) / L
    t = spacing / 2 if start_offset is None else start_offset
    out, i = [], 0
    while t <= L:
        q = p0 + d * (t + rnd.uniform(-jitter, jitter))
        out.append(_place(assets, q, rnd, scale, snap_to, target, prefix, i))
        t += spacing
        i += 1
    return out


def _inside(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def scatter_area(assets, polygon_xy, density=None, count=None, min_dist=1.0, scale=(0.8, 1.2), seed=0,
                 snap_to=None, target="Entourage", prefix="area", max_tries=20000):
    """Dart-throwing (Poisson-like) scatter inside a world-space polygon.  density = items per m2."""
    rnd = random.Random(seed)
    P = np.array(polygon_xy, float)
    (x0, y0), (x1, y1) = P.min(0), P.max(0)
    area = 0.5 * abs(np.dot(P[:, 0], np.roll(P[:, 1], 1)) - np.dot(P[:, 1], np.roll(P[:, 0], 1)))
    n = count if count is not None else int(round((density or 0.2) * area))
    pts, tries = [], 0
    while len(pts) < n and tries < max_tries:
        tries += 1
        q = (rnd.uniform(x0, x1), rnd.uniform(y0, y1))
        if not _inside(q, polygon_xy):
            continue
        if any((q[0] - a) ** 2 + (q[1] - b) ** 2 < min_dist ** 2 for a, b in pts):
            continue
        pts.append(q)
    return [_place(assets, q, rnd, scale, snap_to, target, prefix, i) for i, q in enumerate(pts)]


def gn_scatter(target, coll, density=100.0, scale=(0.7, 1.3), seed=0, name=None, selection_attr=None,
               keep_surface=True, viewport=True):
    """Geometry-Nodes instancing of `coll` (each child = one variant) on the faces of `target`.
    density = instances per m2 (grass tufts 200-800, ground cover 5-50).  selection_attr = float/bool
    attribute or vertex-group name that masks where to scatter."""
    name = name or f"AVZ_scatter_{coll.name}"
    ng = bpy.data.node_groups.get(name) or bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.nodes.clear()
    for it in list(ng.interface.items_tree):
        ng.interface.remove(it)
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gin, gout = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    dist = N.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "RANDOM"
    dist.inputs["Density"].default_value = density
    dist.inputs["Seed"].default_value = seed
    L.new(gin.outputs[0], dist.inputs["Mesh"])
    if selection_attr:
        na = N.new("GeometryNodeInputNamedAttribute")
        na.data_type = "FLOAT"
        na.inputs["Name"].default_value = selection_attr
        L.new(M.sock(na, "Attribute", "VALUE", out=True), dist.inputs["Selection"])
    ci = N.new("GeometryNodeCollectionInfo")
    ci.transform_space = "ORIGINAL"
    ci.inputs["Collection"].default_value = coll
    ci.inputs["Separate Children"].default_value = True
    ci.inputs["Reset Children"].default_value = True
    iop = N.new("GeometryNodeInstanceOnPoints")
    iop.inputs["Pick Instance"].default_value = True
    L.new(dist.outputs["Points"], iop.inputs["Points"])
    L.new(ci.outputs["Instances"], iop.inputs["Instance"])
    rr = N.new("FunctionNodeRandomValue")
    rr.data_type = "FLOAT_VECTOR"
    M.sock(rr, "Min", "VECTOR").default_value = (0.0, 0.0, 0.0)
    M.sock(rr, "Max", "VECTOR").default_value = (0.0, 0.0, 2 * math.pi)
    M.sock(rr, "Seed", "INT").default_value = seed + 1
    L.new(M.sock(rr, "Value", "VECTOR", out=True), iop.inputs["Rotation"])
    rs = N.new("FunctionNodeRandomValue")
    rs.data_type = "FLOAT"
    M.sock(rs, "Min", "VALUE").default_value = scale[0]
    M.sock(rs, "Max", "VALUE").default_value = scale[1]
    M.sock(rs, "Seed", "INT").default_value = seed + 2
    L.new(M.sock(rs, "Value", "VALUE", out=True), iop.inputs["Scale"])
    if keep_surface:
        j = N.new("GeometryNodeJoinGeometry")
        L.new(gin.outputs[0], j.inputs[0])
        L.new(iop.outputs["Instances"], j.inputs[0])
        L.new(j.outputs[0], gout.inputs[0])
    else:
        L.new(iop.outputs["Instances"], gout.inputs[0])
    mod = target.modifiers.get(name) or target.modifiers.new(name, "NODES")
    mod.node_group = ng
    mod.show_viewport = viewport
    return mod
