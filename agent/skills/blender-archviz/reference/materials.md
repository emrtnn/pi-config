# Materials for photoreal archviz

Imported BIM materials are flat colours with a few generic textures. Realism comes from: correct albedo,
real-world texture scale, roughness variation, bump/normal detail, bevelled edges, and large-scale
variation (no two square metres identical).

## Pipeline

```python
rep = R.apply(dry_run=True)                       # classification report, nothing changed
rep["by_category"], rep["unmatched"]
rep = R.apply(overrides={"Material label": "brick", "Helper": "invisible", "Keep me": "skip"})
rep["errors"]                                     # e.g. a texture id that failed to download
```
- Only materials carrying `src_label` are touched (importer-tagged) – downloaded assets keep their shaders.
- Classification = multilingual regex rules (`R.RULES`) on the label; Revit-style "Family - Material"
  labels are classified by the last segment first. Extend `R.RULES` / `R.TEXTURES` per project.
- `uv_mode="auto"` uses UVs when they are ≈ metres, else BOX projection in object space.
- The source colour (`src_color`, linear) is used as **tint**: the texture keeps its detail but its mean
  albedo becomes the architect's colour (`tint_strength` < 1 keeps some natural texture colour).

## Builders (`avz_materials`)

| Builder | Use |
|---|---|
| `pbr(mat, tex, width_m, tint=, tint_strength=, rough=(min,max), normal=, height=, rot_deg=, mode=UV/OBJECT/BOX, uvmap=, macro=, ao=, bevel=, coat=)` | any image PBR set; `tex` = Poly Haven id (auto-download) or folder; `width_m` = real width of one tile (height from pixel aspect) |
| `solid(mat, color, rough, metal, coat, bevel, noise_bump, macro, sheen)` | paints, plastics, metals, ceramics |
| `glass(mat, tint, rough, ior, thin)` | glazing; transparent to shadow/diffuse rays → daylight enters interiors without caustic noise |
| `water(mat)` / `mosaic(mat, size, c1, c2, grout)` | pools |
| `slab_mix(mat, top_builder, side_color, bottom_color)` | slabs/sidewalks: texture on top, plain edges/soffits (world normal test) |
| `image_material(mat, path, width_m)` | a swatch image shipped with the model, at real scale |
| `facade_procedural(name, …)` | background buildings (windows + random shutters, object space) |
| `slope_uvs(mesh)` | roof/ramp UVs: U along contour, V up the slope, metres |
| `uv_units_per_metre(objs)` | 1.0 = metre UVs; 3.28 = feet; varied = arbitrary |
| `clamp_albedo(c)` | keep albedo within 0.02–0.85 |

## Reference values (linear albedo / roughness)

| Surface | Albedo | Roughness | Notes |
|---|---|---|---|
| White paint / white render | 0.70–0.80 | 0.8–0.95 | never 1.0 – kills contrast, looks CG |
| Beige / ochre render (monocapa) | 0.45–0.65 | 0.8–0.95 | fine stucco texture 1.5–2 m tile, normal 0.6–1.0 |
| Exposed concrete | 0.25–0.40 | 0.6–0.9 | formwork joints, macro variation |
| Red/brown clay roof tiles | 0.15–0.30 | 0.55–0.9 | slope UVs, tile ~2.5 m texture, AO multiply 0.5 |
| Brick | 0.15–0.35 | 0.7–0.95 | real brick 24×5–7 cm courses |
| Asphalt | 0.05–0.10 | 0.6–0.95 | 3 m tile, strong macro variation, cracks |
| Concrete pavers / sidewalk | 0.25–0.40 | 0.5–0.9 | joints aligned with the building frame (`rot_deg`) |
| Grass (from far) | 0.08–0.15 | 0.6–0.95 | close-up: GN-scattered grass models |
| Wood floor / oak | 0.20–0.45 | 0.3–0.6 | planks 1.2–2 m long |
| Polished stone / worktop | 0.3–0.7 | 0.08–0.3 | coat 0.2 |
| Sanitary ceramic | 0.75–0.8 | 0.05–0.1 | coat 0.5 |
| Black window frames / RAL 7016 | 0.03–0.05 | 0.35–0.45 | painted metal, small bevel |
| Stainless / aluminium | metal, 0.55–0.9 | 0.2–0.35 | `metal=1` |
| Glass | tint ~ (0.88, 0.94, 0.93) | 0 | IOR 1.52; architect glass colours are usually too saturated |

## Pitfalls & fixes

- **Texture scale wrong** → UV units (feet/inches), or mapping applied twice; check with a close preview of
  something with known size (tile, brick, door).
- **Stretching** on extruded BIM solids → `mode="BOX"` (object space box projection, blend 0.25).
- **Non-square textures** (e.g. 4:1 strips) – `pbr` derives the height from the image aspect; the Poly Haven
  API `dimensions` can disagree with the image (`polyhaven_fetch.py tex` warns) – trust the image.
- **Flat uniform walls** → `macro` 0.05–0.1, bevel 0.01, slightly rough normal; dirt/AO only where justified.
- **Dark interiors behind glass** → `glass()` (shadow-transparent), interior lights for dusk, exposure.
- **Glass looks like a mirror/black** → nothing behind it (empty rooms render black): keep interiors and
  furniture visible, or add simple curtains/backdrops; slightly rough reflections help.
- **Helper geometry rendered** (clearance zones, analytic volumes, insulation foam) → category `invisible`.
- **Pink/missing textures** → `SC.audit()["images_missing"]` (packed images are fine).
