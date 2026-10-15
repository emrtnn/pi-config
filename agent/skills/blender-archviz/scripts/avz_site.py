"""avz_site - ground, streets, sidewalks with curbs, road markings, background buildings, distant terrain.
Runs INSIDE Blender.

Everything is laid out in a building Frame (avz_common.Frame / detect_frame) with a user supplied
z-function zfun(u, v) -> height, so sloped streets are easy:

    fr = avz_common.Frame.load()                       # stored by detect_frame()
    def street(u, v): return 0.0 if v > 30 else 0.05*(30 - v)     # example grade
    mats = avz_site.site_materials()
    avz_site.terrain("SITE_road", fr, -150, 150, -100, 150, lambda u, v: street(u, v) - 0.15, mats["asphalt"],
                     mask=lambda u, v: not (0 < u < 40 and 0 < v < 20))          # hole for the building
    avz_site.slab("SITE_sidewalk", fr, -3, 43, 20, 23, street, mats["pavers"])   # curb drop = 0.2 m
"""
import math
import random

import bpy
import numpy as np

import avz_common as C
import avz_materials as M


# ----------------------------------------------------------------------------- grids
def axis(a0, a1, step, extra=()):
    n = max(1, int(math.ceil((a1 - a0) / step)))
    vals = set(np.round(np.linspace(a0, a1, n + 1), 4).tolist())
    vals.update(round(e, 4) for e in extra if a0 < e < a1)
    return np.array(sorted(vals))


def grid(frame, us, vs, zfun, mask=None):
    nu = len(us)
    verts = [(*frame.xy(u, v), zfun(u, v)) for v in vs for u in us]
    faces = []
    for j in range(len(vs) - 1):
        for i in range(nu - 1):
            if mask is not None and not mask((us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2):
                continue
            a = j * nu + i
            faces.append((a, a + 1, a + nu + 1, a + nu))
    return verts, faces


def terrain(name, frame, u0, u1, v0, v1, zfun, mat, step=2.0, mask=None, extra_u=(), extra_v=(), coll="Site"):
    """Open surface (roads, lawns, terrain). `mask(u,v)` False -> no face (holes for buildings).
    Put footprint edges in extra_u/extra_v so holes have straight borders."""
    verts, faces = grid(frame, axis(u0, u1, step, extra_u), axis(v0, v1, step, extra_v), zfun, mask)
    return C.mesh_object(name, verts, faces, coll, [mat] if mat else [])


def slab(name, frame, u0, u1, v0, v1, zfun, mat, step=0.5, drop=0.2, coll="Site"):
    """Rectangular paved slab whose top follows zfun, with vertical edge faces dropping `drop` metres
    (= the curb seen from the road).  Use a slab_mix material: paving on top, concrete on the curb."""
    us, vs = axis(u0, u1, step), axis(v0, v1, step)
    verts, faces = grid(frame, us, vs, zfun)
    nu, nv = len(us), len(vs)
    ring = [(i, 0) for i in range(nu)] + [(nu - 1, j) for j in range(1, nv)] + \
           [(i, nv - 1) for i in range(nu - 2, -1, -1)] + [(0, j) for j in range(nv - 2, 0, -1)]
    base = len(verts)
    for (i, j) in ring:
        x, y, z = verts[j * nu + i]
        verts.append((x, y, z - drop))
    m = len(ring)
    for k in range(m):
        i0, j0 = ring[k]
        i1, j1 = ring[(k + 1) % m]
        faces.append((j1 * nu + i1, j0 * nu + i0, base + k, base + (k + 1) % m))
    return C.mesh_object(name, verts, faces, coll, [mat] if mat else [])


def round_corner(name, frame, cu, cv, r, a0_deg, a1_deg, zfun, mat, seg=16, drop=0.2, coll="Site"):
    """Quarter (or any arc) sidewalk piece centred at (cu, cv) - for street corners."""
    pts = [(cu, cv)] + [(cu + r * math.cos(math.radians(a0_deg + (a1_deg - a0_deg) * k / seg)),
                         cv + r * math.sin(math.radians(a0_deg + (a1_deg - a0_deg) * k / seg))) for k in range(seg + 1)]
    verts = [(*frame.xy(u, v), zfun(u, v)) for (u, v) in pts] + [(*frame.xy(u, v), zfun(u, v) - drop) for (u, v) in pts]
    n = len(pts)
    faces = [tuple(range(n))] + [(k + 1, k, n + k, n + k + 1) for k in range(1, n - 1)]
    return C.mesh_object(name, verts, faces, coll, [mat] if mat else [])


def polygon_slab(name, pts_xy, z, mat, drop=0.2, coll="Site"):
    """Flat slab from an arbitrary simple polygon in world XY (CCW), e.g. a plaza or terrace."""
    n = len(pts_xy)
    verts = [(x, y, z) for x, y in pts_xy] + [(x, y, z - drop) for x, y in pts_xy]
    faces = [tuple(range(n))] + [((k + 1) % n, k, n + k, n + (k + 1) % n) for k in range(n)]
    return C.mesh_object(name, verts, faces, coll, [mat] if mat else [])


# ----------------------------------------------------------------------------- road markings
def strip(frame, p0, p1, width, zfun, step=1.0, lift=0.004):
    """Thin quad strip from p0=(u,v) to p1 following zfun -> (verts, faces) piece."""
    (u0, v0), (u1, v1) = p0, p1
    L = math.hypot(u1 - u0, v1 - v0)
    du, dv = (u1 - u0) / L, (v1 - v0) / L
    nu_, nv_ = -dv * width / 2, du * width / 2
    n = max(1, int(L / step))
    verts, faces = [], []
    for k in range(n + 1):
        t = k / n
        u, v = u0 + (u1 - u0) * t, v0 + (v1 - v0) * t
        for s in (-1, 1):
            uu, vv = u + s * nu_, v + s * nv_
            verts.append((*frame.xy(uu, vv), zfun(uu, vv) + lift))
    for k in range(n):
        a = 2 * k
        faces.append((a, a + 2, a + 3, a + 1))
    return verts, faces


def dashed(frame, p0, p1, zfun, width=0.12, dash=3.0, gap=4.0):
    (u0, v0), (u1, v1) = p0, p1
    L = math.hypot(u1 - u0, v1 - v0)
    pieces, s = [], 0.0
    while s < L:
        e = min(L, s + dash)
        a, b = s / L, e / L
        pieces.append(strip(frame, (u0 + (u1 - u0) * a, v0 + (v1 - v0) * a), (u0 + (u1 - u0) * b, v0 + (v1 - v0) * b),
                            width, zfun))
        s += dash + gap
    return pieces


def zebra(frame, center, direction, road_width, zfun, bars=None, bar=0.5, gap=0.5, length=4.0):
    """Pedestrian crossing centred at `center` (u,v); `direction`=(du,dv) unit vector ACROSS the road."""
    cu, cv = center
    du, dv = direction
    pu, pv = -dv, du                                         # along the road
    n = bars or int(road_width / (bar + gap))
    pieces = []
    for k in range(n):
        off = -road_width / 2 + (k + 0.5) * (bar + gap)
        a = (cu + du * off - pu * length / 2, cv + dv * off - pv * length / 2)
        b = (cu + du * off + pu * length / 2, cv + dv * off + pv * length / 2)
        pieces.append(strip(frame, a, b, bar, zfun, step=0.5))
    return pieces


def markings(name, pieces, mat=None, coll="Site"):
    V, F = [], []
    for verts, faces in pieces:
        off = len(V)
        V.extend(verts)
        F.extend(tuple(i + off for i in f) for f in faces)
    return C.mesh_object(name, V, F, coll, [mat or paint_material()])


def paint_material(name="AVZ_road_paint"):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    nt, out = M.reset(mat)
    b = M.principled(nt)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 3.0
    nz.inputs["Detail"].default_value = 8.0
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.35
    mr.inputs["From Max"].default_value = 0.65
    nt.links.new(nz.outputs["Fac"], mr.inputs["Value"])
    nt.links.new(M.mix_rgb(nt, "MIX", mr.outputs["Result"], (0.78, 0.78, 0.74), (0.45, 0.45, 0.43)),
                 b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.65
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    return mat


def site_materials(prefix="AVZ", rot_deg=0.0, asphalt="asphalt_02", paving="concrete_pavement", soil="dry_ground_rocks",
                   grass="leafy_grass"):
    """Standard site materials (Poly Haven, auto-downloaded). rot_deg aligns paving joints with the Frame
    (pass frame.angle_deg).  Object-space mapping -> no UVs needed on generated site meshes."""
    def mat(n):
        return bpy.data.materials.get(f"{prefix}_{n}") or bpy.data.materials.new(f"{prefix}_{n}")
    mats = {k: mat(k) for k in ("asphalt", "pavers", "soil", "grass")}
    M.pbr(mats["asphalt"], asphalt, 3.0, mode="OBJECT", rot_deg=rot_deg, rough=(0.55, 0.95), macro=0.12,
          tint=(0.075, 0.075, 0.073), tint_strength=0.6)
    M.slab_mix(mats["pavers"], lambda m: M.pbr(m, paving, 1.8, mode="OBJECT", rot_deg=rot_deg, rough=(0.5, 0.9),
                                               macro=0.08, tint=(0.36, 0.35, 0.33), tint_strength=0.5),
               (0.33, 0.33, 0.32), (0.2, 0.2, 0.2), side_rough=0.8)
    M.pbr(mats["soil"], soil, 4.0, mode="OBJECT", rough=(0.7, 1.0), macro=0.1)
    M.pbr(mats["grass"], grass, 2.0, mode="OBJECT", rough=(0.6, 0.95), macro=0.12)
    mats["paint"] = paint_material(f"{prefix}_road_paint")
    return mats


# ----------------------------------------------------------------------------- background buildings
def context_block(name, frame, u0, u1, v0, v1, height, mat, base_z=0.0, roof_mat=None, coll="Context"):
    """Box building aligned with the Frame; object space == facade space (for facade_procedural)."""
    w, d = u1 - u0, v1 - v0
    verts = [(0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0), (0, 0, height), (w, 0, height), (w, d, height), (0, d, height)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    ob = C.mesh_object(name, verts, faces, coll, [mat] + ([roof_mat] if roof_mat else []))
    if roof_mat:
        ob.data.polygons[1].material_index = 1
    x, y = frame.xy(u0, v0)
    ob.location = (x, y, base_z)
    ob.rotation_euler = (0, 0, frame.a)
    return ob


def facade_set(n=5, seed=7, palette=None):
    rnd = random.Random(seed)
    palette = palette or [(0.80, 0.79, 0.75), (0.78, 0.72, 0.60), (0.80, 0.78, 0.72), (0.72, 0.62, 0.48),
                          (0.82, 0.81, 0.78)]
    return [M.facade_procedural(f"AVZ_facade_{i}", wall=palette[i % len(palette)], bay=rnd.uniform(3.0, 3.8),
                                win_w=rnd.uniform(1.0, 1.5), win_h=rnd.uniform(1.2, 1.6), seed=float(i)) for i in range(n)]


def block_row(prefix, frame, along, a0, a1, front, away, zfun, depth=(11, 16), width=(11, 22),
              heights=(6.4, 9.4, 9.4, 12.4), mats=None, roof_mat=None, seed=1, coll="Context"):
    """Row of background buildings along a street.
    along='u' -> blocks span u in [a0,a1], street-facing facade at v=front, extending towards `away` (+1/-1).
    along='v' -> same with axes swapped."""
    rnd = random.Random(seed)
    mats = mats or facade_set()
    if roof_mat is None:
        roof_mat = bpy.data.materials.get("AVZ_flat_roof") or bpy.data.materials.new("AVZ_flat_roof")
        M.solid(roof_mat, (0.35, 0.33, 0.31), rough=0.9)
    made, t, i = [], a0, 0
    while t < a1 - 3:
        w = min(rnd.uniform(*width), a1 - t)
        d = rnd.uniform(*depth)
        lo, hi = sorted((front, front + away * d))
        if along == "u":
            u0, u1, v0, v1 = t, t + w - 0.05, lo, hi
        else:
            u0, u1, v0, v1 = lo, hi, t, t + w - 0.05
        base = min(zfun(u, v) for u in (u0, u1) for v in (v0, v1)) - 0.3
        made.append(context_block(f"{prefix}_{i}", frame, u0, u1, v0, v1, rnd.choice(heights), rnd.choice(mats),
                                  base, roof_mat, coll))
        t += w
        i += 1
    return made


# ----------------------------------------------------------------------------- distant terrain
def hills_ring(name="AVZ_hills", center=(0.0, 0.0), r_inner=350.0, r_outer=4000.0, high_bearing=None, max_h=380.0,
               base_h=40.0, spread_deg=55.0, z0=-5.0, seed=3, coll="Context"):
    """Low-poly terrain ring for the horizon (mountains towards `high_bearing`, gentle elsewhere).
    Casts no shadows.  Set high_bearing=None for flat-ish surroundings."""
    nr, na = 60, 240
    rs = np.geomspace(r_inner, r_outer, nr)
    ang = np.linspace(0, 2 * math.pi, na, endpoint=False)
    ph = np.random.default_rng(seed).uniform(0, 2 * math.pi, 8)
    verts = []
    for r in rs:
        for a in ang:
            bearing = (90 - math.degrees(a)) % 360
            if high_bearing is None:
                k = 0.0
            else:
                db = ((bearing - high_bearing) + 180) % 360 - 180
                k = math.exp(-(db / spread_deg) ** 2)
            n = sum(math.sin(a * (j + 2) + ph[j]) / (j + 2) for j in range(8)) * 0.5 + 0.5
            ramp = min(1.0, (r - r_inner) / (r_outer - r_inner) * 3.0)
            h = (base_h + max_h * k) * (0.35 + 0.65 * n) * ramp + 25 * math.sin(r / 140 + ph[0]) * ramp
            verts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a), z0 + max(-2.0, h)))
    faces = [(i * na + j, i * na + (j + 1) % na, (i + 1) * na + (j + 1) % na, (i + 1) * na + j)
             for i in range(nr - 1) for j in range(na)]
    mat = bpy.data.materials.get("AVZ_hills") or bpy.data.materials.new("AVZ_hills")
    nt, out = M.reset(mat)
    b = M.principled(nt)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 0.004
    nz.inputs["Detail"].default_value = 10.0
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    cr = nt.nodes.new("ShaderNodeValToRGB")
    cr.color_ramp.elements[0].color = (0.10, 0.11, 0.06, 1)
    cr.color_ramp.elements[1].color = (0.30, 0.24, 0.16, 1)
    cr.color_ramp.elements[0].position = 0.42
    cr.color_ramp.elements[1].position = 0.62
    nt.links.new(nz.outputs["Fac"], cr.inputs["Fac"])
    nt.links.new(cr.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 1.0
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    ob = C.mesh_object(name, verts, faces, coll, [mat], smooth=True)
    ob.visible_shadow = False
    return ob
