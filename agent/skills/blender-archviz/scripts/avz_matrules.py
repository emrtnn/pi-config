"""avz_matrules - first-pass automatic PBR upgrade of imported (BIM/CAD) materials.  Runs INSIDE Blender.

Workflow:
    import avz_matrules as R
    R.tag_source_materials(objs)          # only needed for non-Datasmith imports (FBX/OBJ/glTF/IFC...)
    rep = R.apply(dry_run=True)           # review rep['by_category'] / rep['unmatched']
    rep = R.apply(overrides={"Some material label": "brick", "Other": "skip"})

Classification = multilingual (EN/ES/FR/IT/DE/PT) regex rules on the material label; for Revit-style labels
"Family - Material" the last segment is tried first.  Builders keep the architect's colour (src_color) as tint
so design intent survives while texture/roughness/bump add realism.  Assets (BlenderKit/Poly Haven models)
are never touched because only materials carrying `src_label` are processed.
"""
import re

import bpy
import numpy as np

import avz_materials as M

# category -> (Poly Haven texture id, tile width in metres).  Swap ids per project if needed.
TEXTURES = {
    "plaster_ext": ("white_stucco", 1.6),
    "paint_int": ("white_plaster_02", 1.0),
    "concrete": ("concrete_wall_008", 2.7),
    "concrete_floor": ("concrete_floor_worn_001", 3.0),
    "brick": ("red_brick", 1.4),
    "roof_clay": ("clay_roof_tiles_03", 2.6),
    "roof_grey": ("grey_roof_tiles_02", 1.5),
    "stone": ("marble_01", 1.5),
    "tile_floor": ("floor_tiles_06", 3.0),
    "wood_floor": ("laminate_floor_02", 1.7),
    "wood": ("oak_veneer_01", 1.83),
    "fabric": ("fabric_pattern_07", 0.4),
    "leather": ("brown_leather", 0.4),
    "grass": ("leafy_grass", 2.0),
    "gravel": ("gravel_floor_02", 2.0),
    "asphalt": ("asphalt_02", 3.0),
    "paving": ("concrete_pavement", 1.8),
    "soil": ("dry_ground_rocks", 4.0),
}

_C = r"(solid.?colou?r|lacquer|lacad|paint|pintad|\bral\b|ral\d|white|blanc|black|negr|anthrac|antrac|grey|gray|gris|bronze)"
RULES = [  # (category, include regex, exclude regex) - first match wins
    ("invisible", r"clearance|zona.?(de.?)?(paso|libre|maniobra)|analytic|anal[ií]tic|helper|massing|barrido|hidden", None),
    ("water", r"\bwater\b|\bagua\b|l[aá]mina.?de.?agua", None),
    ("mirror", r"mirror|espejo|miroir|specchio|spiegel", None),
    ("frosted", r"frosted|esmerilad|satin.?glass|cristal.?mate|vidrio.?mate|translucid|opal", None),
    ("glass", r"glass|glaz|vidrio|cristal|verre|vetro|\bglas\b|vitrage|acristal|skirt", None),
    ("screen", r"screen|pantalla|tv.?image|display.?image", None),
    ("chrome", r"chrome|cromad|\bcromo|polished.?(steel|metal)|pulid", None),
    ("painted_metal", r"\bral\s?\d{4}|lacad|thermolac|powder.?coat|painted.?metal|metal.?-?.?paint", None),
    ("painted_metal", r"(alumin|metal|steel|acero|hierro|iron).*" + _C, r"stainless|inox"),
    ("stone", r"worktop|encimera|countertop|bancada|silestone|dekton|compac", None),
    ("metal", r"stainless|inox|acero|acciaio|\bacier|stahl|steel|\bmetal|alumin|zinc|cobre|copper|brass|lat[oó]n|bronce|"
              r"\biron\b|hierro|corten|ferro\b", None),
    ("roof", r"roof.?tile|\btejas?\b|teja.?|tuile|tegola|ziegel|slate|pizarra|shingle", None),
    ("brick", r"brick|ladrillo|klinker|cara.?vista|mattone|brique", r"trasdosad"),
    ("paint_int", r"trasdosad|pladur|placa.?de.?yeso|drywall|gypsum", None),
    ("stone", r"stone|piedra|granit|marble|m[aá]rmol|travert|limestone|caliza|quartz|cuarzo", None),
    ("tile_floor", r"\btiles?\b|\btiled\b|azulejo|baldosa|\bgres\b|porcel[aá]nic|terraz+o|mosaic|gresite|fliese|carrel", None),
    ("ceramic", r"sanitar|\bwc\b|inodoro|lavabo|bidet|ba[nñ]era|bathtub|basin|sink|fregadero|toilet|urinal|"
                r"\broca\b|porcelain|vitreous|loza|cer[aá]mic|ceramic", None),
    ("wood_floor", r"parquet|tarima|laminate.?floor|floor.?board|(suelo|floor).*(madera|wood)|(madera|wood).*(suelo|floor)", None),
    ("leather", r"leather|cuero|\bpiel\b", None),
    ("fabric", r"fabric|textil|tela\b|tejido|upholster|tapicer|linen|\blino\b|cotton|algod|wool|\blana\b|velvet|"
               r"terciopelo|bed.?(cover|linen)|curtain|cortina|cushion|coj[ií]n|carpet|alfombra|moqueta|tessut", None),
    ("wood", r"wood|madera|timber|\boak|roble|\bpine|\bpino|walnut|nogal|\bash\b|fresno|cherry|cerezo|teak|teca|beech|"
             r"\bhaya|birch|abedul|maple|\barce|casta[nñ]|chestnut|veneer|chapa|melamin|\bmdf|plywood|contrachapado|"
             r"listel|lumber|holz|bois|legno", None),
    ("rubber", r"rubber|caucho|silicon|sellad|gasket|junta|neopren|epdm", None),
    ("plastic", r"plastic|pl[aá]stic|\bpvc|acryl|acr[ií]lic|polycarb|policarb|\babs\b|vinyl|vinilo", None),
    ("grass", r"grass|c[eé]sped|lawn|turf|pradera", None),
    ("asphalt", r"asphalt|asfalto|bitumen|bet[uú]n", None),
    ("gravel", r"gravel|grava|gravilla|pebble", None),
    ("soil", r"\bsoil|tierra|earth\b|terreno|dirt", None),
    ("paving", r"paving|pavement|acera|sidewalk|adoqu|paver|cobble", None),
    ("concrete_floor", r"(concrete|hormig[oó]n).*(floor|suelo|solera|slab|losa)|(floor|suelo|solera).*(concrete|hormig)", None),
    ("concrete", r"concrete|hormig[oó]n|beton|b[eé]ton|cement|cemento|precast|prefabricad", None),
    ("plaster_ext", r"render|stucco|enfoscad|monocapa|revoco|mortero|mortar|\bsate\b|etics|fachada|facade|"
                    r"muro|\bwall\b|pared|tabique|mauer", None),
    ("paint_int", r"plaster|yeso|enlucid|\bpaint|pintura|ceiling|techo|crepi|intonaco|putz", None),
    ("floor", r"\bsuelos?\b|\bfloors?\b|pavimento|solado|\bslab|\blosa|forjado", None),
]


# ----------------------------------------------------------------------------- source info
def tag_source_materials(objs=None, overwrite=False):
    """Record the imported look of materials (label, base colour, texture, opacity) before rebuilding.
    Datasmith imports do this automatically; call it after FBX/OBJ/glTF/USD/IFC imports."""
    objs = objs or [o for o in bpy.context.scene.objects if o.type == "MESH"]
    seen = 0
    for m in {s.material for o in objs for s in o.material_slots if s.material}:
        if m.get("src_label") and not overwrite:
            continue
        m["src_label"] = m.name
        col, tex, alpha, metal, rough = tuple(m.diffuse_color[:3]), None, 1.0, 0.0, 0.5
        if m.use_nodes and m.node_tree:
            b = next((n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
            if b is not None:
                bc = b.inputs["Base Color"]
                if bc.is_linked:
                    src = bc.links[0].from_node
                    if src.bl_idname == "ShaderNodeTexImage" and src.image:
                        tex = bpy.path.abspath(src.image.filepath)
                else:
                    col = tuple(bc.default_value[:3])
                alpha = b.inputs["Alpha"].default_value if not b.inputs["Alpha"].is_linked else 1.0
                metal = b.inputs["Metallic"].default_value
                rough = b.inputs["Roughness"].default_value
                if b.inputs["Transmission Weight"].default_value > 0.5:
                    alpha = min(alpha, 0.1)
        m["src_color"] = list(col)
        m["src_opacity"] = float(alpha)
        m["src_kind"] = "translucent" if alpha < 0.99 else "opaque"
        m["src_metal"] = float(metal)
        m["src_roughness"] = float(rough)
        if tex:
            m["src_texture"] = tex
        seen += 1
    return seen


def src_color(m):
    c = m.get("src_color")
    return tuple(c[:3]) if c else tuple(m.diffuse_color[:3])


def classify(label):
    segs = re.split(r"_-_| - ", label)
    tries = ([segs[-1]] if len(segs) > 1 else []) + [label]
    for text in tries:
        t = text.replace("_", " ")
        for cat, inc, exc in RULES:
            if re.search(inc, t, re.I) and not (exc and re.search(exc, t, re.I)):
                return cat
    return None


def _uv_mode(m, uv_mode):
    if uv_mode != "auto":
        return uv_mode
    users = [o for o in bpy.data.objects if o.type == "MESH" and any(s.material == m for s in o.material_slots)][:20]
    r = M.uv_units_per_metre(users) if users else None
    return "UV" if r and 0.6 < r["median"] < 1.6 else "BOX"


def _is_reddish(c):
    return c[0] > c[1] * 1.25 and c[0] > c[2] * 1.4


def _lift_metal(c):
    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    k = max(0.55, min(0.92, lum)) / max(lum, 1e-3)
    return tuple(min(0.95, x * k) for x in c)


# ----------------------------------------------------------------------------- builders
def _pbr(cat, m, c, mode, **kw):
    tid, w = TEXTURES[cat]
    return M.pbr(m, tid, w, mode=mode, **kw)


def b_glass(m, c, mode, rough=0.0):
    # Datasmith says explicitly whether a material is translucent; an *opaque* "glass" there is a glossy panel
    # (oven door, black glass splashback...).  Other formats often lose transparency, so trust the label.
    if m.get("src_kind_explicit") and m.get("src_kind") == "opaque":
        return M.solid(m, c, rough=0.05, coat=1.0)
    tint = tuple(0.9 * 0.85 + 0.15 * x for x in c)  # architect glass colours are usually far too saturated
    return M.glass(m, tint=tint, rough=rough)


def b_fabric(m, c, mode):
    b = _pbr("fabric", m, c, mode, tint=c, tint_strength=1.0, rough=(0.8, 1.0), normal=0.6)
    b.inputs["Sheen Weight"].default_value = 0.5
    return b


def b_roof(m, c, mode):
    meshes = {o.data for o in bpy.data.objects if o.type == "MESH" and any(s.material == m for s in o.material_slots)}
    uvname = None
    for me in meshes:
        uvname = M.slope_uvs(me)
    cat = "roof_clay" if _is_reddish(c) or max(c) > 0.25 else "roof_grey"
    return _pbr(cat, m, c, "UV", uvmap=uvname, tint=c, tint_strength=0.45, rough=(0.55, 0.9), normal=1.3,
                macro=0.12, ao=0.6)


def b_generic(m, c, mode):
    if m.get("src_kind") == "translucent" or m.get("src_opacity", 1.0) < 0.99:
        return M.glass(m)
    rough = m.get("src_roughness", 0.5)
    if m.get("src_metal", 0.0) > 0.5:
        return M.solid(m, _lift_metal(c), rough=max(0.15, rough), metal=1.0, bevel=0.002)
    return M.solid(m, c, rough=max(0.2, rough), bevel=0.002)


def b_floor(m, c, mode):
    M.slab_mix(m, lambda mm: _pbr("tile_floor", mm, c, "OBJECT", tint=c, tint_strength=0.6, rough=(0.25, 0.55),
                                  normal=0.6, macro=0.04),
               side_color=(0.78, 0.77, 0.74), bottom_color=(0.8, 0.8, 0.78))


BUILDERS = {
    "skip": lambda m, c, mode: None,
    "invisible": lambda m, c, mode: M.invisible(m),
    "water": lambda m, c, mode: M.water(m),
    "mirror": lambda m, c, mode: M.solid(m, (0.95, 0.95, 0.95), rough=0.0, metal=1.0),
    "frosted": lambda m, c, mode: b_glass(m, c, mode, rough=0.35),
    "glass": b_glass,
    "screen": lambda m, c, mode: M.solid(m, (0.005, 0.005, 0.006), rough=0.05, coat=1.0),
    "chrome": lambda m, c, mode: M.solid(m, (0.9, 0.9, 0.9), rough=0.04, metal=1.0),
    "painted_metal": lambda m, c, mode: M.solid(m, c, rough=0.35, coat=0.1, bevel=0.002),
    "metal": lambda m, c, mode: M.solid(m, _lift_metal(c), rough=0.3, metal=1.0, bevel=0.002),
    "roof": b_roof,
    "brick": lambda m, c, mode: _pbr("brick", m, c, mode, tint=c, tint_strength=0.35 if _is_reddish(c) else 0.7,
                                     rough=(0.7, 0.95), normal=1.0, height=0.004, macro=0.08),
    "stone": lambda m, c, mode: _pbr("stone", m, c, mode, tint=c, tint_strength=0.6, rough=(0.08, 0.3), coat=0.2,
                                     normal=0.3, bevel=0.003),
    "tile_floor": lambda m, c, mode: _pbr("tile_floor", m, c, mode, tint=c, tint_strength=0.6, rough=(0.15, 0.45),
                                          normal=0.6, macro=0.04),
    "ceramic": lambda m, c, mode: M.solid(m, c, rough=0.08 if m.get("src_roughness", 0.3) <= 0.5 else 0.45,
                                          coat=0.5 if m.get("src_roughness", 0.3) <= 0.5 else 0.0, bevel=0.002),
    "wood_floor": lambda m, c, mode: _pbr("wood_floor", m, c, mode, tint=c, tint_strength=0.5, rough=(0.3, 0.6)),
    "wood": lambda m, c, mode: _pbr("wood", m, c, mode, tint=c, tint_strength=0.6, rough=(0.35, 0.6), normal=0.5,
                                    bevel=0.002),
    "leather": lambda m, c, mode: _pbr("leather", m, c, mode, tint=c, tint_strength=0.8),
    "fabric": b_fabric,
    "rubber": lambda m, c, mode: M.solid(m, (0.02, 0.02, 0.02), rough=0.6),
    "plastic": lambda m, c, mode: M.solid(m, c, rough=0.3, bevel=0.002),
    "grass": lambda m, c, mode: _pbr("grass", m, c, mode, rough=(0.6, 0.95)),
    "asphalt": lambda m, c, mode: _pbr("asphalt", m, c, mode, tint=(0.075, 0.075, 0.073), tint_strength=0.6,
                                       rough=(0.55, 0.95), macro=0.12),
    "gravel": lambda m, c, mode: _pbr("gravel", m, c, mode),
    "soil": lambda m, c, mode: _pbr("soil", m, c, mode, macro=0.1),
    "paving": lambda m, c, mode: _pbr("paving", m, c, mode, tint=c, tint_strength=0.5, rough=(0.5, 0.9)),
    "concrete_floor": lambda m, c, mode: _pbr("concrete_floor", m, c, mode, tint=c, tint_strength=0.7,
                                              rough=(0.6, 0.9), normal=0.8, macro=0.06),
    "concrete": lambda m, c, mode: _pbr("concrete", m, c, mode, tint=c, tint_strength=0.7, rough=(0.65, 0.95),
                                        normal=0.8, bevel=0.01, macro=0.06),
    "plaster_ext": lambda m, c, mode: _pbr("plaster_ext", m, c, mode, tint=c, rough=(0.78, 0.95), normal=0.9,
                                           bevel=0.012, macro=0.07),
    "paint_int": lambda m, c, mode: _pbr("paint_int", m, c, mode, tint=c, rough=(0.82, 0.95), normal=0.35,
                                         macro=0.03),
    "floor": b_floor,
    "generic": b_generic,
}


def apply(materials=None, overrides=None, dry_run=False, uv_mode="auto"):
    """Classify + rebuild materials.  Default set: every material with a `src_label` (imported model).
    overrides: {label_or_name: category}; category 'skip' leaves a material untouched.
    uv_mode: 'auto' (UV if UVs are ~metres else BOX projection), 'UV', 'BOX', 'OBJECT'."""
    overrides = overrides or {}
    mats = materials or [m for m in bpy.data.materials if m.get("src_label") and m.users > 0]
    rep = {"by_category": {}, "unmatched": [], "errors": {}, "count": len(mats)}
    for m in mats:
        label = m.get("src_label") or m.name
        cat = overrides.get(label) or overrides.get(m.name) or classify(label) or "generic"
        rep["by_category"].setdefault(cat, []).append(label)
        if cat == "generic":
            rep["unmatched"].append(label)
        if dry_run or cat == "skip":
            continue
        try:
            BUILDERS[cat](m, src_color(m), _uv_mode(m, uv_mode))
            m["avz_category"] = cat
        except Exception as e:  # noqa: BLE001
            rep["errors"][label] = "%s: %s" % (type(e).__name__, e)
    return rep
