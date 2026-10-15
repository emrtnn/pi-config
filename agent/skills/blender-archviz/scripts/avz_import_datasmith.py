"""avz_import_datasmith - import Unreal Datasmith scenes (.udatasmith) into Blender.  Runs INSIDE Blender.

    import avz_import_datasmith as DS
    rep = DS.import_datasmith("/path/model.udatasmith")       # converts meshes on first run (uv + udsmesh_convert.py)

Handles: StaticMesh (via the npz cache), MaterialInstance / MasterMaterial (key-value materials of Revit,
ArchiCAD...), UEPbrMaterial (expression graphs of SketchUp/Rhino/3ds Max... flattened heuristically),
Texture, Actor / ActorMesh (nested, world-space transforms), ActorHierarchicalInstancedStaticMesh,
Camera, Light (basic), MetaData (geolocation / true-north hints).  Collections are created per actor
`layer` (in Revit = category: walls, windows, furniture...).  Every material gets src_* custom properties
(colour, texture, opacity, ...) consumed by avz_matrules.apply().
"""
import json
import math
import os
import re
import xml.etree.ElementTree as ET

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

import avz_common as C

CAM_FIX = Matrix(((0, 0, -1, 0), (-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 1)))   # UE camera(+X fwd) -> Blender(-Z)
MESH_ACTORS = ("ActorMesh", "ActorHierarchicalInstancedStaticMesh", "ActorInstancedStaticMesh")


# ----------------------------------------------------------------------------- parsing helpers
def parse_xml(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    txt = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", txt)   # exporters leave raw '&'
    return ET.fromstring(txt)


def color(s, default=(0.8, 0.8, 0.8, 1.0)):
    m = re.findall(r"([RGBA])=([-0-9.eE]+)", s or "")
    if len(m) >= 3:
        d = {k: float(v) for k, v in m}
        return (d.get("R", 0.8), d.get("G", 0.8), d.get("B", 0.8), d.get("A", 1.0))
    return default


def ue_matrix(tr):
    """Datasmith world transform (UE: cm, left-handed, Z up) -> Blender matrix (m, right-handed)."""
    g = lambda k, d=0.0: float(tr.get(k, d))  # noqa: E731
    loc = Vector((g("tx") * 0.01, -g("ty") * 0.01, g("tz") * 0.01))
    q = Quaternion((g("qw", 1.0), -g("qx"), g("qy"), -g("qz")))
    return Matrix.LocRotScale(loc, q, Vector((g("sx", 1.0), g("sy", 1.0), g("sz", 1.0))))


def _estimate_uv_units(cache_dir, index, sample=150):
    """Fallback for caches written without the global estimate."""
    rats = []
    for key in list(index.get("index", {}))[:sample]:
        z = np.load(os.path.join(cache_dir, key + ".npz"))
        if "uv0" not in z or not len(z["f"]):
            continue
        P, U = z["v"][z["f"]], z["uv0"].reshape(-1, 3, 2)
        e3, e2 = np.linalg.norm(P[:, 1] - P[:, 0], axis=1), np.linalg.norm(U[:, 1] - U[:, 0], axis=1)
        ok = (e3 > 0.05) & (e2 > 1e-6)
        if ok.any():
            rats.append(float(np.median(e2[ok] / e3[ok])))
    out = {"median": None, "unit": "unknown", "factor": 1.0}
    if rats:
        r = np.array(rats)
        med = float(np.median(r))
        out["median"] = med
        for unit, k in (("metre", 1.0), ("feet", 3.280839895), ("inch", 39.37007874), ("cm", 100.0), ("mm", 1000.0)):
            if abs(med / k - 1) < 0.03 and (np.percentile(r, 90) - np.percentile(r, 10)) / med < 0.1:
                out.update(unit=unit, factor=k)
    return out


def ensure_cache(assets_dir, cache_dir, force=False):
    idx = os.path.join(cache_dir, "index.json")
    if os.path.exists(idx) and not force:
        index = json.load(open(idx))
        if "uv_units_per_m" not in index:
            index["uv_units_per_m"] = _estimate_uv_units(cache_dir, index)
        return index
    r = C.run_tool("udsmesh_convert.py", assets_dir, cache_dir, timeout=3600)
    if r.returncode != 0 or not os.path.exists(idx):
        raise RuntimeError("udsmesh_convert failed:\n" + (r.stderr[-1500:] or r.stdout[-1500:]))
    return json.load(open(idx))


# ----------------------------------------------------------------------------- materials
def _kv(el):
    return {kv.get("name"): kv.get("val") for kv in el.findall("KeyValueProperty")}


def _first(props, *keys):
    for k in keys:
        if props.get(k) not in (None, ""):
            return props[k]
    return None


def _image_node(nt, path, scale=(1.0, 1.0), offset=(0.0, 0.0), rot_deg=0.0, non_color=False):
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path, check_existing=True)
    if non_color:
        tex.image.colorspace_settings.name = "Non-Color"
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale[0], scale[1], 1.0)
    mp.inputs["Location"].default_value = (offset[0], offset[1], 0.0)
    mp.inputs["Rotation"].default_value[2] = math.radians(rot_deg)
    uvn = nt.nodes.new("ShaderNodeUVMap")
    nt.links.new(uvn.outputs["UV"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], tex.inputs["Vector"])
    return tex


def _setup_material(name, label, base, rough, metal, opacity, tex_path, tex_scale, tex_off, tex_rot, uv_factor,
                    explicit_kind, extra=None):
    mat = bpy.data.materials.new((label or name)[:60])
    mat.use_nodes = True
    nt = mat.node_tree
    b = nt.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = base
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if opacity < 0.99:
        b.inputs["Transmission Weight"].default_value = 1.0
        b.inputs["Roughness"].default_value = 0.0
        mat.diffuse_color = (base[0], base[1], base[2], 0.35)
    else:
        mat.diffuse_color = base
    if tex_path:
        tex = _image_node(nt, tex_path, (tex_scale[0] * uv_factor, tex_scale[1] * uv_factor), tex_off, tex_rot)
        nt.links.new(tex.outputs["Color"], b.inputs["Base Color"])
        mat["src_texture"] = tex_path
    mat["ds_name"] = name
    mat["src_label"] = label or name
    mat["src_color"] = list(base[:3])
    mat["src_opacity"] = float(opacity)
    mat["src_kind"] = "translucent" if opacity < 0.99 else "opaque"
    mat["src_kind_explicit"] = bool(explicit_kind)
    mat["src_metal"] = float(metal)
    mat["src_roughness"] = float(rough)
    for k, v in (extra or {}).items():
        mat[k] = v
    return mat


def _kv_material(el, textures, uv_factor):
    """MaterialInstance / MasterMaterial (Revit, ArchiCAD, ... key-value parameter sets)."""
    p = _kv(el)
    base = color(_first(p, "DiffuseColor", "Color", "BaseColor", "Diffuse"))
    gloss = _first(p, "Glossiness")
    rough = 0.08 + 0.84 * (1 - float(gloss)) if gloss is not None else float(_first(p, "Roughness") or 0.5)
    rough = max(0.04, min(0.95, rough))
    metal = 1.0 if str(_first(p, "IsMetal", "Metal") or "").lower() == "true" else float(_first(p, "Metallic") or 0)
    tr = _first(p, "Transparency")
    opacity = 1.0 - float(tr) if tr is not None else float(_first(p, "Opacity") or 1.0)
    typ = el.get("Type")
    if typ == "2":
        opacity = min(opacity, 0.5)
    tname = _first(p, "DiffuseMap", "Texture", "BaseColorMap", "DiffuseTexture")
    tpath = textures.get(tname) if tname else None
    sx = float(_first(p, "DiffuseMap_UVScaleX", "TextureScale", "TextureScaleX") or 1.0)
    sy = float(_first(p, "DiffuseMap_UVScaleY", "TextureScale", "TextureScaleY") or 1.0)
    ox = float(_first(p, "DiffuseMap_UVOffsetX") or 0.0)
    oy = float(_first(p, "DiffuseMap_UVOffsetY") or 0.0)
    rot = float(_first(p, "DiffuseMap_UVWAngle") or 0.0)
    extra = {}
    lum = _first(p, "SelfIlluminationLuminance")
    if lum and float(lum) > 0:
        extra["src_emission_luminance"] = float(lum)
    if _first(p, "BumpMap"):
        extra["src_bump"] = textures.get(_first(p, "BumpMap")) or ""
    return _setup_material(el.get("name"), el.get("label"), base, rough, metal, opacity, tpath, (sx, sy), (ox, oy),
                           rot, uv_factor, typ in ("1", "2"), extra)


def _pbr_material(el, textures, uv_factor):
    """UEPbrMaterial: flatten the expression graph -> first texture / colour / scalar feeding each channel."""
    exprs = []
    E = el.find("Expressions")
    for x in (E if E is not None else []):
        inputs = [int(ch.get("expression")) for ch in x if ch.get("expression") is not None]
        tex = x.get("PathName") or x.get("Texture")
        for ch in x:
            if ch.tag == "Input" and (ch.get("type") == "Texture" or ch.get("name") == "Texture") and ch.get("val"):
                tex = ch.get("val")
        tiling = None
        if x.tag == "TextureCoordinate" or x.get("UTiling"):
            tiling = (float(x.get("UTiling", 1)), float(x.get("VTiling", 1)))
        exprs.append({"tag": x.tag, "attr": dict(x.attrib), "inputs": inputs, "tex": tex, "tiling": tiling})
    chans = {}
    for ch in el:
        tag = ch.get("Name") if ch.tag == "Input" else ch.tag
        if ch.get("expression") is not None and tag:
            chans[tag] = int(ch.get("expression"))

    def walk(i):
        res = {"tex": None, "color": None, "scalar": None, "tiling": None}
        stack, seen = [i], set()
        while stack:
            k = stack.pop()
            if k in seen or not (0 <= k < len(exprs)):
                continue
            seen.add(k)
            e = exprs[k]
            if e["tex"] and res["tex"] is None:
                res["tex"] = e["tex"]
            if e["tag"] == "Color" and res["color"] is None:
                res["color"] = color(e["attr"].get("constant"))
            if e["tag"] == "Scalar" and res["scalar"] is None:
                res["scalar"] = float(e["attr"].get("constant", 0) or 0)
            if e["tiling"] and res["tiling"] is None:
                res["tiling"] = e["tiling"]
            stack.extend(reversed(e["inputs"]))
        return res

    bc = walk(chans["BaseColor"]) if "BaseColor" in chans else {}
    ro = walk(chans["Roughness"]) if "Roughness" in chans else {}
    me = walk(chans["Metallic"]) if "Metallic" in chans else {}
    op = walk(chans["Opacity"]) if "Opacity" in chans else {}
    base = bc.get("color") or (0.8, 0.8, 0.8, 1.0)
    rough = ro.get("scalar") if ro.get("scalar") is not None else 0.5
    metal = me.get("scalar") or 0.0
    opacity = op.get("scalar") if op.get("scalar") is not None else 1.0
    tpath = textures.get(bc.get("tex")) if bc.get("tex") else None
    tiling = bc.get("tiling") or (1.0, 1.0)
    mat = _setup_material(el.get("name"), el.get("label"), base, max(0.04, min(0.95, rough)), metal, opacity, tpath,
                          tiling, (0, 0), 0.0, uv_factor, "Opacity" in chans)
    if "Normal" in chans:
        nt = walk(chans["Normal"]).get("tex")
        if nt and textures.get(nt):
            mat["src_normal_map"] = textures[nt]
    return mat


# ----------------------------------------------------------------------------- meshes
def _build_mesh(sm, cache_dir, mats, uv_factor):
    name, label = sm.get("name"), sm.get("label") or sm.get("name")
    fe = sm.find("file")
    key = os.path.splitext(os.path.basename(fe.get("path")))[0] if fe is not None and fe.get("path") else name
    f = os.path.join(cache_dir, key + ".npz")
    if not os.path.exists(f):
        return None, None
    z = np.load(f)
    v, F, m = z["v"], z["f"], z["m"]
    slots = [str(s) for s in z["slots"]]
    me = bpy.data.meshes.new(label[:60])
    me["ds_name"] = name
    nv, nt = len(v), len(F)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", v.ravel())
    me.loops.add(nt * 3)
    me.loops.foreach_set("vertex_index", F.ravel())
    me.polygons.add(nt)
    me.polygons.foreach_set("loop_start", np.arange(0, nt * 3, 3, dtype=np.int32))
    sm_mats = {}
    for e in sm.findall("Material"):
        sm_mats[e.get("id") or str(len(sm_mats))] = e.get("name")
    # slot names are usually material ids ("0","1"); some exporters use material names
    ids = list(sm_mats.keys())
    names = {v_: k for k, v_ in sm_mats.items()}
    slot_ids = []
    for s in slots:
        sid = s if s in sm_mats else names.get(s, s)
        if sid not in slot_ids:
            slot_ids.append(sid)
    for sid in ids:
        if sid not in slot_ids:
            slot_ids.append(sid)
    for sid in slot_ids:
        me.materials.append(mats.get(sm_mats.get(sid)))
    idx_of = {sid: i for i, sid in enumerate(slot_ids)}
    pg = np.array([idx_of.get(s if s in sm_mats else names.get(s, s), 0) for s in slots] or [0], np.int32)
    me.polygons.foreach_set("material_index", pg[np.clip(m, 0, len(pg) - 1)])
    for k in range(4):
        if f"uv{k}" in z:
            uvl = me.uv_layers.new(name="UVMap" if k == 0 else f"UVMap{k}")
            uvl.data.foreach_set("uv", (z[f"uv{k}"] / uv_factor).ravel())
    me.update(calc_edges=True)
    if "n" in z:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))
        me.normals_split_custom_set(z["n"].reshape(-1, 3))
    return me, slot_ids


# ----------------------------------------------------------------------------- lights
def _light(el, coll):
    typ = (el.get("type") or el.tag).lower()
    kind = "SPOT" if "spot" in typ else "AREA" if ("area" in typ or "rect" in typ) else "SUN" if "direction" in typ \
        else "POINT"
    ld = bpy.data.lights.new(el.get("label") or el.get("name"), kind)
    ch = {c.tag: c for c in el}
    if "Color" in ch:
        c = ch["Color"]
        if c.get("usetemp") == "1" and c.get("temperature"):
            ld["src_temperature_K"] = float(c.get("temperature"))
        ld.color = tuple(float(c.get(k, 1)) for k in ("R", "G", "B"))
    val = float(ch["Intensity"].get("value", 1)) if "Intensity" in ch else 1.0
    units = ch["IntensityUnits"].get("value", "") if "IntensityUnits" in ch else ""
    lumens = val * 4 * math.pi if "andela" in units else val
    ld.energy = 3.0 if kind == "SUN" else lumens / 683.0      # approx. lm -> Blender radiant W
    ld["src_intensity"] = "%s %s" % (val, units)
    if kind == "SPOT" and "OuterConeAngle" in ch:
        ld.spot_size = math.radians(2 * float(ch["OuterConeAngle"].get("value", 45)))
    if kind == "AREA":
        ld.shape = "RECTANGLE"
        ld.size = float(ch["Width"].get("value", 50)) * 0.01 if "Width" in ch else 0.5
        ld.size_y = float(ch["Length"].get("value", 50)) * 0.01 if "Length" in ch else 0.5
    ob = bpy.data.objects.new(el.get("label") or el.get("name"), ld)
    ob.matrix_world = ue_matrix(el.find("Transform").attrib) @ CAM_FIX
    coll.objects.link(ob)
    return ob


# ----------------------------------------------------------------------------- main
def import_datasmith(path, cache_dir=None, collection=None, metadata=False, lights=True, force_convert=False):
    path = os.path.abspath(os.path.expanduser(path))
    base = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    root = parse_xml(path)
    rep = {"file": path, "warnings": []}
    # --- assets + mesh cache
    mesh_files = [e.get("path") for e in root.iter("file") if e.get("path", "").endswith(".udsmesh")]
    assets_dir = os.path.dirname(os.path.join(base, mesh_files[0])) if mesh_files else base
    cache_dir = cache_dir or os.path.join(base, stem + "_meshcache")
    index = ensure_cache(assets_dir, cache_dir, force_convert)
    uvu = index.get("uv_units_per_m", {})
    uv_factor = float(uvu.get("factor") or 1.0)
    rep["uv_units"] = uvu
    rep["mesh_errors"] = len(index.get("errors", {}))
    # --- textures
    textures, missing = {}, []
    for t in root.findall("Texture"):          # top level only (UEPbrMaterial also has <Texture> expressions)
        f = t.get("file") or ""
        if not f or not t.get("name"):
            continue
        p = os.path.join(base, f)
        if not os.path.exists(p):
            alt = os.path.join(assets_dir, os.path.basename(f))
            p = alt if os.path.exists(alt) else None
        if p:
            textures[t.get("name")] = p
        else:
            missing.append(os.path.basename(f))
    rep["textures_missing"] = sorted(set(missing))
    # --- materials
    mats = {}
    for el in root:
        try:
            if el.tag in ("MaterialInstance", "MasterMaterial"):
                mats[el.get("name")] = _kv_material(el, textures, uv_factor)
            elif el.tag == "UEPbrMaterial":
                mats[el.get("name")] = _pbr_material(el, textures, uv_factor)
            elif el.tag in ("Material", "UEPbrMaterialInstance", "DecalMaterial"):
                mats[el.get("name")] = _setup_material(el.get("name"), el.get("label"), (0.7, 0.7, 0.7, 1), 0.5, 0, 1,
                                                       None, (1, 1), (0, 0), 0, uv_factor, False)
        except Exception as e:  # noqa: BLE001
            rep["warnings"].append("material %s: %s" % (el.get("label"), e))
    # --- meshes
    meshes, slot_maps = {}, {}
    for sm in root.iter("StaticMesh"):
        me, slots = _build_mesh(sm, cache_dir, mats, uv_factor)
        if me is not None:
            meshes[sm.get("name")], slot_maps[sm.get("name")] = me, slots
    # --- scene objects
    scene = bpy.context.scene
    top = C.collection(collection or f"DS_{stem}"[:60])
    layer_cols, stats = {}, {"objects": 0, "instances": 0, "overrides": 0, "missing_mesh": 0, "cameras": [],
                             "lights": 0}

    def col_for(layer):
        layer = layer or "Unlayered"
        if layer not in layer_cols:
            layer_cols[layer] = C.collection(layer[:60], parent=top)
        return layer_cols[layer]

    def new_obj(actor, me, M, mname):
        ob = bpy.data.objects.new((actor.get("label") or actor.get("name"))[:60], me)
        ob.matrix_world = M
        ob["ds_name"] = actor.get("name")
        ob["ds_label"] = actor.get("label") or ""
        ob["ds_layer"] = actor.get("layer") or ""
        tags = [t.get("value") for t in actor.findall("tag")]
        for t in tags:
            if t.startswith("Revit.Element.Id."):
                ob["revit_id"] = int(t.rsplit(".", 1)[1])
        if tags:
            ob["ds_tags"] = ";".join(tags)[:1000]
        col_for(actor.get("layer")).objects.link(ob)
        ovr = {e.get("id"): e.get("name") for e in actor.findall("material")}
        if ovr:
            for i, sid in enumerate(slot_maps[mname]):
                mn = ovr.get(sid)
                if mn in mats and me.materials[i] != mats[mn]:
                    ob.material_slots[i].link = "OBJECT"
                    ob.material_slots[i].material = mats[mn]
                    stats["overrides"] += 1
        return ob

    def visit(el):
        for ch in el:
            if ch.tag in MESH_ACTORS:
                mref = ch.find("mesh")
                mname = mref.get("name") if mref is not None else None
                me = meshes.get(mname)
                if me is None:
                    stats["missing_mesh"] += 1
                else:
                    inst = ch.find("Instances")
                    if inst is not None and len(inst):
                        for k, t in enumerate(inst.findall("Transform")):
                            new_obj(ch, me, ue_matrix(t.attrib), mname)
                            stats["instances"] += 1
                    else:
                        new_obj(ch, me, ue_matrix(ch.find("Transform").attrib), mname)
                        stats["objects"] += 1
            elif ch.tag == "Camera":
                cam = bpy.data.cameras.new(ch.get("label") or "Camera")
                cam.sensor_fit = "HORIZONTAL"
                g = lambda tag, d: float(ch.find(tag).get("value")) if ch.find(tag) is not None else d  # noqa: E731
                cam.sensor_width = g("SensorWidth", 36.0)
                cam.lens = g("FocalLength", 35.0)
                cam.clip_start, cam.clip_end = 0.05, 10000.0
                dof = ch.find("DepthOfField")
                if dof is not None and dof.get("enabled") == "1":
                    cam.dof.use_dof = True
                    cam.dof.focus_distance = g("FocusDistance", 1000.0) * 0.01
                    cam.dof.aperture_fstop = g("FStop", 5.6)
                co = bpy.data.objects.new("CAM_src_" + (ch.get("label") or "camera")[:40], cam)
                co.matrix_world = ue_matrix(ch.find("Transform").attrib) @ CAM_FIX
                co["sensor_aspect"] = g("SensorAspectRatio", 1.7778)
                C.collection("Cameras").objects.link(co)
                stats["cameras"].append({"name": co.name, "lens": cam.lens, "aspect": co["sensor_aspect"]})
                if scene.camera is None:
                    scene.camera = co
            elif ch.tag == "Light" or ch.tag.endswith("Light"):
                if lights:
                    try:
                        _light(ch, col_for("Lights"))
                        stats["lights"] += 1
                    except Exception as e:  # noqa: BLE001
                        rep["warnings"].append("light %s: %s" % (ch.get("label"), e))
            kids = ch.find("children")
            if kids is not None:
                visit(kids)

    visit(root)
    rep.update(stats)
    rep.update({"materials": len(mats), "meshes": len(meshes), "layers": sorted(layer_cols)})
    # --- metadata: geolocation / north hints (+ optional per-object metadata)
    hints = {}
    by_actor = {}
    site_like = {}
    for a in root.iter():
        if a.tag in ("Actor",) + MESH_ACTORS and a.get("name"):
            tags = " ".join(t.get("value", "") for t in a.findall("tag"))
            txt = (a.get("label", "") + " " + tags).lower()
            if any(w in txt for w in ("sitelocation", "site_location", "basepoint", "base_point", "surveypoint",
                                      "survey_point", "geolocation")):
                site_like[a.get("name")] = a.get("label") or a.get("name")
    for md in root.iter("MetaData"):
        ref = (md.get("reference") or "").split(".", 1)[-1]
        kv = _kv(md)
        for k, v in kv.items():
            kl = k.lower()
            if ref in site_like or any(w in kl for w in ("north", "norte", "nord", "latitude", "longitude")):
                if len(hints) < 60:
                    hints.setdefault("%s | %s" % (site_like.get(ref, "")[:30], k), v)
        if metadata:
            by_actor[ref] = kv
    geo = root.find("Geolocation")
    if geo is not None and geo.attrib:
        hints["Geolocation"] = dict(geo.attrib)
    rep["geo_hints"] = hints
    if metadata and by_actor:
        for ob in top.all_objects:
            kv = by_actor.get(ob.get("ds_name"))
            if kv:
                ob["ds_meta"] = json.dumps(kv)[:8000]
    scene["avz_import"] = json.dumps({k: rep[k] for k in ("file", "uv_units", "materials", "meshes", "objects")})
    return rep
