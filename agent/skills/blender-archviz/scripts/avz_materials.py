"""avz_materials - photoreal shader builders for archviz (Cycles/EEVEE, Blender 4.2+/5.x).  Runs INSIDE Blender.

Every builder takes an existing bpy.types.Material and REBUILDS its node tree in place, so object/slot
assignments made by the importer are preserved.

Texture sets: a folder containing maps named like  <id>_diff_2k.jpg / _nor_gl_ / _rough_ / _disp_ / _ao_
(Poly Haven), or *_Color / *_NormalGL / *_Roughness / *_Displacement / *_AmbientOcclusion (ambientCG), or
generic basecolor/albedo/normal/roughness names.  `tex_set(id_or_dir)` finds them; if an id is not on disk
and looks like a Poly Haven id it is downloaded with polyhaven_fetch.py into <project>/assets/polyhaven/tex.
"""
import math
import os
import re

import bpy
import numpy as np

import avz_common as C

_LUM_CACHE = {}
MAP_PATTERNS = {
    "diff": r"(_diff(use)?|_albedo|_basecolor|_base_color|_color|_col)(_|\.|$)",
    "nor_gl": r"(_nor_gl|_normalgl|_normal_gl)(_|\.|$)",
    "nor_dx": r"(_nor_dx|_normaldx|_normal_dx|_normal)(_|\.|$)",
    "rough": r"(_rough(ness)?)(_|\.|$)",
    "disp": r"(_disp(lacement)?|_height)(_|\.|$)",
    "ao": r"(_ao|_ambientocclusion|_occlusion)(_|\.|$)",
    "metal": r"(_metal(ness|lic)?)(_|\.|$)",
}
IMG_EXT = (".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff", ".webp")


# ----------------------------------------------------------------------------- texture sets
def tex_roots():
    return [C.subdir("assets", "polyhaven", "tex"), C.subdir("assets", "textures")]


def find_maps(folder):
    files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(IMG_EXT)]
    maps = {}
    for key, pat in MAP_PATTERNS.items():
        for f in files:
            if re.search(pat, os.path.splitext(f.lower())[0] + ".", re.I):
                if key == "nor_dx" and "nor_gl" in maps and maps["nor_gl"] == os.path.join(folder, f):
                    continue
                maps.setdefault(key, os.path.join(folder, f))
    return maps


def ensure_polyhaven(asset_id, res="2k"):
    """Download a Poly Haven texture set (CC0) into the project if missing. Returns its folder."""
    folder = os.path.join(C.subdir("assets", "polyhaven", "tex"), asset_id)
    if os.path.isdir(folder) and find_maps(folder).get("diff"):
        return folder
    r = C.run_tool("polyhaven_fetch.py", "--root", C.project_dir(), "tex", asset_id, "--res", res, timeout=900)
    if r.returncode != 0 or not os.path.isdir(folder):
        raise RuntimeError("polyhaven download failed: %s" % (r.stderr[-800:] or r.stdout[-800:]))
    return folder


def tex_set(tex, fetch=True):
    """Resolve a texture set: absolute folder, or id searched in the project's texture roots (auto-fetch)."""
    if os.path.isdir(tex):
        return find_maps(tex)
    for root in tex_roots():
        d = os.path.join(root, tex)
        if os.path.isdir(d):
            m = find_maps(d)
            if m.get("diff"):
                return m
    if fetch:
        return find_maps(ensure_polyhaven(tex))
    raise FileNotFoundError(tex)


# ----------------------------------------------------------------------------- node helpers
def img(path, non_color=False):
    im = bpy.data.images.load(path, check_existing=True)
    im.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    return im


def mean_lum(im):
    """Mean linear luminance of an image (cached)."""
    key = im.filepath
    if key not in _LUM_CACHE:
        px = np.empty(len(im.pixels), np.float32)
        im.pixels.foreach_get(px)
        px = px.reshape(-1, 4)[::7, :3]
        _LUM_CACHE[key] = float((px @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean())
    return max(_LUM_CACHE[key], 1e-3)


def reset(mat):
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    return nt, out


def sock(node, name, kind=None, out=False):
    coll = node.outputs if out else node.inputs
    cands = [s for s in coll if s.name == name and (kind is None or s.type == kind)]
    for s in cands:
        if getattr(s, "enabled", True):
            return s
    if cands:
        return cands[0]
    raise KeyError("%s.%s" % (node.bl_idname, name))


def mix_rgb(nt, blend, fac, a, b):
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.blend_type = blend
    if isinstance(fac, (int, float)):
        sock(m, "Factor", "VALUE").default_value = fac
    else:
        nt.links.new(fac, sock(m, "Factor", "VALUE"))
    for name, v in (("A", a), ("B", b)):
        s = sock(m, name, "RGBA")
        if isinstance(v, (tuple, list)):
            s.default_value = tuple(v) + ((1.0,) if len(v) == 3 else ())
        else:
            nt.links.new(v, s)
    return sock(m, "Result", "RGBA", out=True)


def math_node(nt, op, a, b=None, clamp=False):
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = op
    m.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = v
        else:
            nt.links.new(v, m.inputs[i])
    return m.outputs[0]


def coords(nt, mode="UV", scale=(1.0, 1.0, 1.0), rot_deg=0.0, uvmap=None, offset=(0, 0, 0)):
    """Texture vector. mode: 'UV' (metre UVs), 'OBJECT' (object space), 'GENERATED'."""
    N = nt.nodes
    if mode == "UV":
        src = N.new("ShaderNodeUVMap")
        if uvmap:
            src.uv_map = uvmap
        vec = src.outputs["UV"]
    else:
        tc = N.new("ShaderNodeTexCoord")
        vec = tc.outputs["Object" if mode in ("OBJECT", "BOX") else "Generated"]
    mp = N.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = scale
    mp.inputs["Rotation"].default_value[2] = math.radians(rot_deg)
    mp.inputs["Location"].default_value = offset
    nt.links.new(vec, mp.inputs["Vector"])
    return mp.outputs["Vector"]


def tex_node(nt, vec, path, non_color=False, box=False):
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = img(path, non_color)
    if box:
        t.projection = "BOX"
        t.projection_blend = 0.25
    nt.links.new(vec, t.inputs["Vector"])
    return t


def macro_variation(nt, color, amount=0.08, scale=0.35, seed=0.0):
    """Low-frequency value variation (object space) that kills CG uniformity on large surfaces."""
    N = nt.nodes
    tc = N.new("ShaderNodeTexCoord")
    nz = N.new("ShaderNodeTexNoise")
    nz.noise_dimensions = "4D"
    nz.inputs["Scale"].default_value = scale
    nz.inputs["Detail"].default_value = 3.0
    nz.inputs["W"].default_value = seed
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    mr = N.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.3
    mr.inputs["From Max"].default_value = 0.7
    mr.inputs["To Min"].default_value = 1.0 - amount
    mr.inputs["To Max"].default_value = 1.0 + amount * 0.5
    nt.links.new(nz.outputs["Fac"], mr.inputs["Value"])
    comb = N.new("ShaderNodeCombineColor")
    for i in range(3):
        nt.links.new(mr.outputs["Result"], comb.inputs[i])
    return mix_rgb(nt, "MULTIPLY", 1.0, color, comb.outputs["Color"])


def principled(nt):
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    b.location = (500, 0)
    return b


def clamp_albedo(c, lo=0.02, hi=0.85):
    """Physically plausible albedo: no pure white/black surfaces."""
    c = [float(x) for x in c[:3]]
    m = max(c)
    if m > hi:
        c = [x * hi / m for x in c]
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    if lum < lo:
        c = [x + (lo - lum) for x in c]
    return tuple(c)


# ----------------------------------------------------------------------------- image-based PBR
def pbr(mat, tex, width_m=2.0, *, tint=None, tint_strength=1.0, rough=None, normal=1.0, height=0.0,
        rot_deg=0.0, mode="UV", uvmap=None, macro=0.06, sat=None, val=1.0, ao=0.0, bevel=0.0, spec=0.5,
        metallic=0.0, coat=0.0):
    """Image PBR material with REAL-WORLD scale.

    tex      : texture-set id (Poly Haven id, auto-downloaded) or folder.
    width_m  : real width of one texture tile in metres (height follows the image pixel aspect).
    mode     : 'UV' (UVs must be in metres - the importers guarantee this), 'OBJECT', 'BOX' (box
               projection in object space; use for meshes with useless UVs).
    tint     : target mean linear albedo (keeps texture detail, matches the architect's colour);
               tint_strength 0..1 blends original texture colour -> tinted.
    rough    : (min, max) remap of the roughness map;  height > 0 adds bump from the displacement map.
    """
    maps = tex_set(tex)
    nt, out = reset(mat)
    diff_img = img(maps["diff"])
    w_px, h_px = diff_img.size[:] if diff_img.size[0] else (1, 1)
    height_m = width_m * h_px / max(w_px, 1)
    box = mode == "BOX"
    vec = coords(nt, "OBJECT" if box else mode, (1.0 / width_m, 1.0 / height_m, 1.0 / width_m), rot_deg, uvmap)
    diff = tex_node(nt, vec, maps["diff"], box=box)
    col = diff.outputs["Color"]
    if tint is not None:
        lum = mean_lum(diff.image)
        bw = nt.nodes.new("ShaderNodeRGBToBW")
        nt.links.new(col, bw.inputs["Color"])
        detail = math_node(nt, "MULTIPLY", bw.outputs["Val"], 1.0 / lum)
        comb = nt.nodes.new("ShaderNodeCombineColor")
        for i in range(3):
            nt.links.new(detail, comb.inputs[i])
        tinted = mix_rgb(nt, "MULTIPLY", 1.0, comb.outputs["Color"], clamp_albedo(tint))
        col = mix_rgb(nt, "MIX", tint_strength, col, tinted)
    if sat is not None or val != 1.0:
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Saturation"].default_value = 1.0 if sat is None else sat
        hsv.inputs["Value"].default_value = val
        nt.links.new(col, hsv.inputs["Color"])
        col = hsv.outputs["Color"]
    if ao > 0 and maps.get("ao"):
        aot = tex_node(nt, vec, maps["ao"], True, box=box)
        col = mix_rgb(nt, "MULTIPLY", ao, col, aot.outputs["Color"])
    if macro > 0:
        col = macro_variation(nt, col, macro)
    bsdf = principled(nt)
    nt.links.new(col, bsdf.inputs["Base Color"])
    if maps.get("rough"):
        rgh = tex_node(nt, vec, maps["rough"], True, box=box)
        r = rgh.outputs["Color"]
        if rough is not None:
            mr = nt.nodes.new("ShaderNodeMapRange")
            mr.inputs["To Min"].default_value = rough[0]
            mr.inputs["To Max"].default_value = rough[1]
            nt.links.new(r, mr.inputs["Value"])
            r = mr.outputs["Result"]
        nt.links.new(r, bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = rough[1] if rough else 0.6
    bsdf.inputs["Specular IOR Level"].default_value = spec
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Coat Weight"].default_value = coat
    n_out = None
    nmap = maps.get("nor_gl") or maps.get("nor_dx")
    if nmap and normal > 0:
        nor = tex_node(nt, vec, nmap, True, box=box)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.inputs["Strength"].default_value = normal
        if nmap == maps.get("nor_dx") and not maps.get("nor_gl"):   # DirectX normal: flip green
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            comb = nt.nodes.new("ShaderNodeCombineColor")
            nt.links.new(nor.outputs["Color"], sep.inputs["Color"])
            nt.links.new(sep.outputs[0], comb.inputs[0])
            nt.links.new(math_node(nt, "SUBTRACT", 1.0, sep.outputs[1]), comb.inputs[1])
            nt.links.new(sep.outputs[2], comb.inputs[2])
            nt.links.new(comb.outputs["Color"], nm.inputs["Color"])
        else:
            nt.links.new(nor.outputs["Color"], nm.inputs["Color"])
        n_out = nm.outputs["Normal"]
    if height > 0 and maps.get("disp"):
        dt = tex_node(nt, vec, maps["disp"], True, box=box)
        bp = nt.nodes.new("ShaderNodeBump")
        bp.inputs["Distance"].default_value = height
        bp.inputs["Strength"].default_value = 1.0
        nt.links.new(dt.outputs["Color"], bp.inputs["Height"])
        if n_out is not None:
            nt.links.new(n_out, bp.inputs["Normal"])
        n_out = bp.outputs["Normal"]
    if bevel > 0:
        bv = nt.nodes.new("ShaderNodeBevel")
        bv.samples = 4
        bv.inputs["Radius"].default_value = bevel
        if n_out is not None:
            nt.links.new(n_out, bv.inputs["Normal"])
        n_out = bv.outputs["Normal"]
    if n_out is not None:
        nt.links.new(n_out, bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat.diffuse_color = tuple(clamp_albedo(tint)) + (1.0,) if tint else (0.6, 0.6, 0.6, 1.0)
    mat["avz_builder"] = "pbr:%s" % os.path.basename(os.path.dirname(maps["diff"]))
    return bsdf


# ----------------------------------------------------------------------------- simple shaders
def solid(mat, color, rough=0.5, metal=0.0, coat=0.0, bevel=0.0, spec=0.5, noise_bump=0.0, macro=0.0,
          sheen=0.0, clamp=True):
    nt, out = reset(mat)
    bsdf = principled(nt)
    c = tuple(clamp_albedo(color) if (clamp and metal < 0.5) else tuple(color[:3])) + (1.0,)
    if macro > 0:
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = c
        nt.links.new(macro_variation(nt, rgb.outputs[0], macro, 2.0), bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = c
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Specular IOR Level"].default_value = spec
    bsdf.inputs["Sheen Weight"].default_value = sheen
    n_out = None
    if noise_bump > 0:
        tc = nt.nodes.new("ShaderNodeTexCoord")
        nz = nt.nodes.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 180.0
        nz.inputs["Detail"].default_value = 6.0
        nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
        bp = nt.nodes.new("ShaderNodeBump")
        bp.inputs["Strength"].default_value = noise_bump
        bp.inputs["Distance"].default_value = 0.002
        nt.links.new(nz.outputs["Fac"], bp.inputs["Height"])
        n_out = bp.outputs["Normal"]
    if bevel > 0:
        bv = nt.nodes.new("ShaderNodeBevel")
        bv.samples = 4
        bv.inputs["Radius"].default_value = bevel
        if n_out is not None:
            nt.links.new(n_out, bv.inputs["Normal"])
        n_out = bv.outputs["Normal"]
    if n_out is not None:
        nt.links.new(n_out, bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat.diffuse_color = c
    mat["avz_builder"] = "solid"
    return bsdf


def glass(mat, tint=(0.90, 0.95, 0.94), rough=0.0, ior=1.52, thin=False, reflect=1.0):
    """Architectural glazing.  Camera/glossy rays see refractive glass with reflections; shadow and
    diffuse rays pass through tinted-transparent so sun and skylight reach the interiors (no caustic noise).
    thin=True for single-plane glass (Principled 'Thin Wall' when available)."""
    nt, out = reset(mat)
    N = nt.nodes
    g = principled(nt)
    g.inputs["Base Color"].default_value = tuple(tint) + (1.0,)
    g.inputs["Transmission Weight"].default_value = 1.0
    g.inputs["Roughness"].default_value = rough
    g.inputs["IOR"].default_value = ior
    g.inputs["Specular IOR Level"].default_value = 0.5 * reflect
    if thin and "Thin Wall" in g.inputs:
        g.inputs["Thin Wall"].default_value = True
    tr = N.new("ShaderNodeBsdfTransparent")
    tr.inputs["Color"].default_value = tuple(tint) + (1.0,)
    lp = N.new("ShaderNodeLightPath")
    f = math_node(nt, "MAXIMUM", lp.outputs["Is Shadow Ray"], lp.outputs["Is Diffuse Ray"])
    mx = N.new("ShaderNodeMixShader")
    nt.links.new(f, mx.inputs[0])
    nt.links.new(g.outputs["BSDF"], mx.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = tuple(tint) + (0.3,)
    mat["avz_builder"] = "glass"
    return g


def invisible(mat):
    """For helper geometry exported by BIM tools (clearance zones, analytic volumes...)."""
    nt, out = reset(mat)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.inputs["Color"].default_value = (1, 1, 1, 1)
    nt.links.new(tr.outputs["BSDF"], out.inputs["Surface"])
    mat.diffuse_color = (1, 1, 1, 0.0)
    mat["avz_builder"] = "invisible"


def water(mat, depth_color=(0.25, 0.75, 0.85), density=0.6, ripple=0.25):
    """Pool / pond water: refractive surface, procedural ripples, volume absorption for depth colour."""
    nt, out = reset(mat)
    N = nt.nodes
    b = principled(nt)
    b.inputs["Base Color"].default_value = (0.9, 0.98, 1.0, 1.0)
    b.inputs["Transmission Weight"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.015
    b.inputs["IOR"].default_value = 1.333
    tc = N.new("ShaderNodeTexCoord")
    nz = N.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 0.8
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    vor = N.new("ShaderNodeTexVoronoi")
    vor.feature = "SMOOTH_F1"
    vor.inputs["Scale"].default_value = 1.6
    nt.links.new(nz.outputs["Color"], vor.inputs["Vector"])
    bp = N.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = ripple
    bp.inputs["Distance"].default_value = 0.02
    nt.links.new(vor.outputs["Distance"], bp.inputs["Height"])
    nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    vol = N.new("ShaderNodeVolumeAbsorption")
    vol.inputs["Color"].default_value = tuple(depth_color) + (1.0,)
    vol.inputs["Density"].default_value = density
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    mat.diffuse_color = (0.3, 0.6, 0.8, 0.6)
    mat["avz_builder"] = "water"


def mosaic(mat, size=0.025, c1=(0.36, 0.62, 0.72), c2=(0.30, 0.55, 0.66), grout=(0.75, 0.78, 0.78), rough=0.12):
    """Procedural square mosaic / tiles in object space (pools, bathrooms)."""
    nt, out = reset(mat)
    N = nt.nodes
    tc = N.new("ShaderNodeTexCoord")
    br = N.new("ShaderNodeTexBrick")
    br.inputs["Scale"].default_value = 1.0 / size
    br.inputs["Mortar Size"].default_value = 0.06
    br.inputs["Brick Width"].default_value = 1.0
    br.inputs["Row Height"].default_value = 1.0
    br.offset = 0.0
    br.inputs["Color1"].default_value = tuple(c1) + (1,)
    br.inputs["Color2"].default_value = tuple(c2) + (1,)
    br.inputs["Mortar"].default_value = tuple(grout) + (1,)
    nt.links.new(tc.outputs["Object"], br.inputs["Vector"])
    b = principled(nt)
    nt.links.new(br.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    bp = N.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = 0.4
    bp.invert = True
    nt.links.new(br.outputs["Fac"], bp.inputs["Height"])
    nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    mat["avz_builder"] = "mosaic"


def slab_mix(mat, top_builder, side_color, bottom_color, side_rough=0.85, bottom_rough=0.9):
    """Horizontal slabs/sidewalks: `top_builder(mat)` on up-facing faces, plain colours on edges/soffits.
    (World-space normal test, so it works for any slab object without separate material slots.)"""
    top_builder(mat)
    nt = mat.node_tree
    out = [n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"][0]
    top_link = out.inputs["Surface"].links[0].from_socket
    N = nt.nodes
    side = N.new("ShaderNodeBsdfPrincipled")
    side.inputs["Base Color"].default_value = tuple(clamp_albedo(side_color)) + (1,)
    side.inputs["Roughness"].default_value = side_rough
    bot = N.new("ShaderNodeBsdfPrincipled")
    bot.inputs["Base Color"].default_value = tuple(clamp_albedo(bottom_color)) + (1,)
    bot.inputs["Roughness"].default_value = bottom_rough
    geo = N.new("ShaderNodeNewGeometry")
    sep = N.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Normal"], sep.inputs["Vector"])
    is_top = math_node(nt, "GREATER_THAN", sep.outputs["Z"], 0.5)
    is_bot = math_node(nt, "LESS_THAN", sep.outputs["Z"], -0.5)
    m1 = N.new("ShaderNodeMixShader")
    nt.links.new(is_bot, m1.inputs[0])
    nt.links.new(side.outputs["BSDF"], m1.inputs[1])
    nt.links.new(bot.outputs["BSDF"], m1.inputs[2])
    m2 = N.new("ShaderNodeMixShader")
    nt.links.new(is_top, m2.inputs[0])
    nt.links.new(m1.outputs[0], m2.inputs[1])
    nt.links.new(top_link, m2.inputs[2])
    nt.links.new(m2.outputs[0], out.inputs["Surface"])
    mat["avz_builder"] = "slab_mix"


def image_material(mat, path, width_m=1.0, rough=0.45, tint=None, rot_deg=0.0, coat=0.0, mode="UV"):
    """Single colour image (e.g. a wood/fabric swatch shipped with the model) at real-world scale."""
    nt, out = reset(mat)
    im = img(path)
    w, h = im.size[:] if im.size[0] else (1, 1)
    vec = coords(nt, "OBJECT" if mode == "BOX" else mode, (1.0 / width_m, w / (h * width_m), 1.0), rot_deg)
    t = tex_node(nt, vec, path, box=(mode == "BOX"))
    b = principled(nt)
    col = t.outputs["Color"]
    if tint:
        col = mix_rgb(nt, "MULTIPLY", 1.0, col, tuple(tint))
    nt.links.new(col, b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
    mat["avz_builder"] = "image"
    return b


def emission(mat, color=(1.0, 0.85, 0.65), strength=8.0):
    nt, out = reset(mat)
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = tuple(color) + (1,)
    e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs["Emission"], out.inputs["Surface"])
    mat["avz_builder"] = "emission"


def facade_procedural(name, wall=(0.78, 0.76, 0.71), bay=3.3, floor_h=3.0, win_w=1.25, win_h=1.45, sill=0.95,
                      z0=0.6, shutter=(0.55, 0.52, 0.46), seed=0.0):
    """Cheap background-building facade (windows + random roller shutters) in OBJECT space.
    Use on axis-aligned boxes whose local X/Y run along the facades (see avz_site.context_block)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    nt, out = reset(mat)
    N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord")
    sep = N.new("ShaderNodeSeparateXYZ")
    L.new(tc.outputs["Object"], sep.inputs["Vector"])
    nsep = N.new("ShaderNodeSeparateXYZ")
    L.new(tc.outputs["Normal"], nsep.inputs["Vector"])
    ax = math_node(nt, "ABSOLUTE", nsep.outputs["X"])
    ay = math_node(nt, "ABSOLUTE", nsep.outputs["Y"])
    face_x = math_node(nt, "GREATER_THAN", ax, ay)
    h = math_node(nt, "ADD", math_node(nt, "MULTIPLY", sep.outputs["X"], math_node(nt, "SUBTRACT", 1.0, face_x)),
                  math_node(nt, "MULTIPLY", sep.outputs["Y"], face_x))
    hb = math_node(nt, "DIVIDE", h, bay)
    fu = math_node(nt, "FRACT", hb)
    cu = math_node(nt, "FLOOR", hb)
    zf = math_node(nt, "DIVIDE", sep.outputs["Z"], floor_h)
    fz = math_node(nt, "FRACT", zf)
    cz = math_node(nt, "FLOOR", zf)
    du = math_node(nt, "ABSOLUTE", math_node(nt, "SUBTRACT", fu, 0.5))
    in_u = math_node(nt, "LESS_THAN", du, win_w / (2 * bay))
    in_z1 = math_node(nt, "GREATER_THAN", fz, sill / floor_h)
    in_z2 = math_node(nt, "LESS_THAN", fz, (sill + win_h) / floor_h)
    above = math_node(nt, "GREATER_THAN", sep.outputs["Z"], z0)
    vert = math_node(nt, "LESS_THAN", math_node(nt, "ABSOLUTE", nsep.outputs["Z"]), 0.5)
    win = math_node(nt, "MULTIPLY", math_node(nt, "MULTIPLY", in_u, in_z1),
                    math_node(nt, "MULTIPLY", math_node(nt, "MULTIPLY", in_z2, above), vert))
    comb = N.new("ShaderNodeCombineXYZ")
    L.new(cu, comb.inputs["X"])
    L.new(cz, comb.inputs["Y"])
    comb.inputs["Z"].default_value = seed
    wn = N.new("ShaderNodeTexWhiteNoise")
    wn.noise_dimensions = "3D"
    L.new(comb.outputs["Vector"], wn.inputs["Vector"])
    rnd = wn.outputs["Value"]
    close = math_node(nt, "MULTIPLY", math_node(nt, "GREATER_THAN", rnd, 0.45), rnd)
    rel = math_node(nt, "DIVIDE", math_node(nt, "SUBTRACT", fz, sill / floor_h), win_h / floor_h)
    shut = math_node(nt, "MULTIPLY", math_node(nt, "GREATER_THAN", rel, math_node(nt, "SUBTRACT", 1.0, close)), win)
    glass_only = math_node(nt, "MULTIPLY", win, math_node(nt, "SUBTRACT", 1.0, shut))
    wall_b = N.new("ShaderNodeBsdfPrincipled")
    rgb = N.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = tuple(clamp_albedo(wall)) + (1,)
    L.new(macro_variation(nt, rgb.outputs[0], 0.08, 0.5, seed), wall_b.inputs["Base Color"])
    wall_b.inputs["Roughness"].default_value = 0.9
    glass_b = N.new("ShaderNodeBsdfPrincipled")
    glass_b.inputs["Base Color"].default_value = (0.012, 0.014, 0.016, 1)
    glass_b.inputs["Roughness"].default_value = 0.04
    glass_b.inputs["Specular IOR Level"].default_value = 0.7
    sh_b = N.new("ShaderNodeBsdfPrincipled")
    sh_b.inputs["Base Color"].default_value = tuple(shutter) + (1,)
    sh_b.inputs["Roughness"].default_value = 0.5
    m1 = N.new("ShaderNodeMixShader")
    L.new(glass_only, m1.inputs[0])
    L.new(wall_b.outputs["BSDF"], m1.inputs[1])
    L.new(glass_b.outputs["BSDF"], m1.inputs[2])
    m2 = N.new("ShaderNodeMixShader")
    L.new(shut, m2.inputs[0])
    L.new(m1.outputs[0], m2.inputs[1])
    L.new(sh_b.outputs["BSDF"], m2.inputs[2])
    bp = N.new("ShaderNodeBump")
    bp.inputs["Strength"].default_value = 0.6
    bp.inputs["Distance"].default_value = 0.08
    bp.invert = True
    L.new(win, bp.inputs["Height"])
    for b in (wall_b, glass_b, sh_b):
        L.new(bp.outputs["Normal"], b.inputs["Normal"])
    L.new(m2.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = tuple(clamp_albedo(wall)) + (1,)
    mat["avz_builder"] = "facade_procedural"
    return mat


# ----------------------------------------------------------------------------- UV utilities
def slope_uvs(me, name="UVslope"):
    """Planar UVs per face in metres: U along the contour line, V up the slope (roof tiles, ramps)."""
    from mathutils import Vector
    uv = me.uv_layers.get(name) or me.uv_layers.new(name=name)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", lv)
    out = np.zeros((len(me.loops), 2), np.float32)
    z = Vector((0, 0, 1))
    for p in me.polygons:
        n = p.normal
        t = Vector((1, 0, 0)) if abs(n.z) > 0.999 else z.cross(n).normalized()
        b = n.cross(t).normalized()
        if b.z < 0:
            b = -b
        for li in range(p.loop_start, p.loop_start + p.loop_total):
            v = Vector(co[lv[li]])
            out[li] = (v.dot(t), v.dot(b))
    uv.data.foreach_set("uv", out.ravel())
    return uv.name


def uv_units_per_metre(objs, sample=200):
    """Median UV length / world length over triangle edges (1.0 = metre UVs, 3.2808 = feet, ...)."""
    rats = []
    for o in objs[:sample]:
        me = o.data
        if not me.uv_layers or len(me.loops) < 3:
            continue
        uvl = me.uv_layers.active.data
        uvs = np.empty(len(uvl) * 2, np.float32)
        uvl.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        lv = np.empty(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", lv)
        for p in list(me.polygons)[:200]:
            if p.loop_total < 2:
                continue
            a, b = p.loop_start, p.loop_start + 1
            d3 = np.linalg.norm(co[lv[b]] - co[lv[a]]) * o.matrix_world.to_scale()[0]
            d2 = np.linalg.norm(uvs[b] - uvs[a])
            if d3 > 0.05 and d2 > 1e-6:
                rats.append(d2 / d3)
    if not rats:
        return None
    r = np.array(rats)
    return {"median": float(np.median(r)), "p10": float(np.percentile(r, 10)), "p90": float(np.percentile(r, 90))}
