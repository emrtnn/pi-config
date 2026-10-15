---
name: blender-archviz
description: "Photoreal architectural visualization (archviz) in Blender driven through the Blender MCP: import an architect's 3D/BIM export (Datasmith .udatasmith, IFC, FBX, OBJ, glTF/GLB, USD, SketchUp, Rhino, 3ds Max, DAE, DXF/STEP), upgrade materials to PBR, build site and urban context, vegetation and entourage, sky/sun, two-point-perspective cameras and Cycles final renders. Use for any request to render or visualize a building, real-estate promotion, interior or masterplan from a 3D model."
---

# Blender ArchViz through the Blender MCP

Turn an architect's model into a **set of photoreal marketing stills** backed by a **reproducible, scripted
Blender scene**. Work happens from code: MCP `execute_blender_code` for the live scene, shell for long jobs,
and the helper library in `scripts/` (skill dir: `~/.pi/agent/skills/blender-archviz`).

Reference docs (read when the phase needs them):
[formats](reference/formats.md) · [materials](reference/materials.md) ·
[light-camera-render](reference/light-camera-render.md) · [assets & plugins](reference/assets-plugins.md) ·
[API signatures](reference/api.md)

## 1. Desired output (definition of done)

```
<project>/                      (next to the architect's files - never modify their originals)
  <Project>.blend               scripted scene, saved after every phase, relative paths
  renders/                      FINALS only: NN_<view>_<WxH>.png (16-bit) [+ .jpg q92] [+ .exr if asked]
  renders/render_log.jsonl      one line per image: camera, res, samples, device, seconds
  previews/                     WIP previews, analysis views, contact sheets (never delivered)
  assets/                       downloaded HDRIs, textures, models (polyhaven/, blenderkit/)
  <stem>_meshcache/             converter cache (Datasmith)
  README.md                     views, sun/time, HDRI, settings, times, asset credits/licences, open issues
```

Standard still set (adapt to the brief; ask the user only what cannot be inferred):

| # | View | Notes |
|---|------|-------|
| 01 | Hero exterior, eye level 1.5–1.7 m, 3/4 corner showing two facades | two-point perspective, 18–28 mm, sun raking the hero facade |
| 02 | Main street facade | frontal-oblique |
| 03 | Entrance / material detail | 28–50 mm, materials legible |
| 04 | Aerial / bird's-eye 30–80 m | roofs, courtyard, context, 30–40 mm |
| 05 | Architect's camera(s) found in the source file | reproduce exactly (importers bring them in) |
| 06 | Amenities (pool, garden, terraces) | if modeled |
| 07 | Interiors (living, kitchen, bedroom) | if furnished / requested |
| 08 | Variants (dusk with lit interiors, season) | optional |

Resolution: 3840×2160 hero shots, ≥ 2560×1440 others (or the source camera's aspect). Quality bar: §11.

## 2. Workflow

1. **Intake** – format, units, location (lat/lon), true north, street sides, time of day/season, views.
   Read the file's metadata first (geolocation, north angle, levels, cameras).
2. **Import** (§4) → `SC.audit()` + `SC.analysis_views()` (workbench, seconds) → save.
3. **Render device** (§10) – benchmark CPU vs GPU in *background* processes; keep the fastest that doesn't crash.
4. **Materials** (§6) – auto PBR upgrade, then hand-tune what the hero shots show.
5. **Site & context** (§5, §7) – frame, grades, ground/streets/sidewalks/markings, neighbours, horizon.
6. **Vegetation & entourage** (§7) – trees, hedges, lawns, cars, people.
7. **Sky & sun** (§8). 8. **Cameras + previews** (§9) – iterate on 960×540 previews.
9. **Finals** (§10) in background → QA (§11) → README.

After every phase: save, render a small preview, **look at it** (`read` the PNG), fix, then continue.

## 3. Driving Blender through the MCP

MCP tools (namespace `mcp__blender`, call them from `codemode`): `execute_blender_code({code})` – main tool,
assign a **dict** to `result`; `get_screenshot_of_window_as_image`; `get_objects_summary`;
`get_object_detail_summary`; `get_blendfile_summary_*`; `render_viewport_to_path`;
`search_api_docs` / `get_python_api_docs` / `search_manual_docs` (bundled docs for the running Blender);
`execute_blender_code_for_cli({blend_file, code})` (separate background Blender).

Bootstrap once per Blender session (re-run `reload_all()` after editing any `avz_*.py`):

```python
import sys, os
SK = os.path.expanduser("~/.pi/agent/skills/blender-archviz/scripts")
if SK not in sys.path: sys.path.insert(0, SK)
import avz_common as C; C.reload_all()
import avz_import_datasmith as DS, avz_matrules as R, avz_materials as M, avz_world as W
import avz_scene as SC, avz_site as SI, avz_assets as A
C.set_project("/abs/path/to/project")      # stored in the scene; previews/, renders/, assets/ live here
result = {"ok": True}
```

Rules learned the hard way:
- `result` must be a dict; return compact summaries (counts, names, numbers) – never dump the scene.
- MCP calls are synchronous and long calls get aborted: keep each call < ~1 min. Final renders, big
  downloads and conversions run from the shell (`blender -b file.blend -P script.py -- args`,
  `nohup … &`, poll the log).
- **Save before anything risky.** If the GUI Blender dies, the MCP answers "Cannot connect to Blender at
  localhost:9876"; the user must restart Blender, then `bpy.ops.wm.open_mainfile(filepath=…)`.
  Crash logs: `journalctl --user | grep -A40 'dumped core'`, kernel GPU errors: `journalctl -k`.
- Never try a GPU backend for the first time inside the GUI session – use `avz_bench.py` (§10).
- Bulk work with the data API + numpy `foreach_get/foreach_set` (thousands of objects in seconds);
  operators need context and are slow.
- Verify visually: previews go to `previews/`, then `read` them (`contact_sheet.py` merges many).
- In codemode, run independent shell/web calls in parallel (`Promise.all`); Blender calls sequentially.
- Blender 5.x API differences met so far: Principled BSDF has "Thin Wall"; sky models are
  `SINGLE_SCATTERING|MULTIPLE_SCATTERING|PREETHAM|HOSEK_WILKIE`; multilayer EXR needs
  `image_settings.media_type="MULTI_LAYER_IMAGE"`; COLLADA left core (extension `collada_support`);
  `bpy.ops.wm.fbx_import` is the new C++ FBX importer. When an attribute errors, `search_api_docs`.

## 4. Import by format (details: reference/formats.md)

| Source | Route |
|---|---|
| Datasmith `.udatasmith` + `*_Assets/*.udsmesh` (Revit, ArchiCAD, SketchUp, Rhino, 3ds Max, Vectorworks) | `DS.import_datasmith(path)` – best BIM route: instancing, categories→collections, materials (src_* props), cameras, lights, metadata. Meshes via `udsmesh_convert.py` (UE5 packed/Oodle + legacy UE4) |
| IFC | extension `bonsai` (full BIM) or `ifc_mesh_importer` (geometry/materials) |
| FBX · glTF/GLB · OBJ/PLY/STL · USD/USDZ | `bpy.ops.wm.fbx_import` · `import_scene.gltf` · `wm.obj_import`/`ply_import`/`stl_import` · `wm.usd_import` |
| SketchUp `.skp` | extension `openskp_import_export`, else ask for Datasmith/FBX/glTF |
| 3ds Max `.max`/`.3ds` · COLLADA `.dae` | `io_scene_max`/`autodesk_3ds_format` · `collada_support` |
| DXF (site plans) · STEP/IGES (manufacturer parts) | `cad2cube` / `import_autocad_dxf_format_dxf` · `step_importer` |
| Native `.rvt`, `.pln`, `.3dm`, `.dwg`, `.vwx` | not readable: ask for Datasmith (best), IFC, FBX, glTF or USD |

After any non-Datasmith import: check units (1 BU = 1 m) and axes, recentre if > 5 km from origin,
`C.tag_model(objs)`, `R.tag_source_materials()`. Then `SC.audit()` and `SC.analysis_views()`.
Install an extension: find `i` = index of `extensions.blender.org` in `bpy.context.preferences.extensions.repos`,
`bpy.ops.extensions.repo_sync(repo_index=i)`, `bpy.ops.extensions.package_install(repo_index=i, pkg_id="bonsai", enable_on_install=True)`.

## 5. Scene analysis

- `fr, ext = C.detect_frame()` → building-aligned `Frame` (u along the dominant facade direction), stored
  in the scene (`C.Frame.load()`); `ext` = footprint size; `strength < 0.5` = non-orthogonal plan → set the
  angle by hand (`C.Frame(angle_deg, origin).store()`).
- `C.side_profile(fr, objs)` → lowest z along each side. Feed it doors, basement windows, plinth objects
  (BIM categories are collections) to infer street grades: entrance sill ≈ sidewalk (+0–0.15 m), basement
  window sills stay above ground, ramps meet the street at their mouth.
- `fr.outward_bearing("v+")` = compass bearing a facade faces → plan the sun (§8). North comes from the
  model metadata (`rep["geo_hints"]`: Revit "Angle to True North", IFC TrueNorth) or the user.

## 6. Materials (details: reference/materials.md)

1. `rep = R.apply(dry_run=True)` → review `rep["by_category"]`, `rep["unmatched"]`.
2. `R.apply(overrides={"<label>": "<category>" | "skip"})`. Categories: plaster_ext, paint_int, concrete,
   concrete_floor, brick, roof, stone, tile_floor, ceramic, wood, wood_floor, fabric, leather, metal,
   painted_metal, chrome, mirror, glass, frosted, water, screen, plastic, rubber, grass, gravel, soil,
   asphalt, paving, floor, invisible, generic. Textures auto-download from Poly Haven (ids in `R.TEXTURES`).
3. Hand-tune hero materials: `M.pbr(mat, "<polyhaven_id>", width_m, tint=…)`, `M.glass`, `M.solid`,
   `M.slab_mix`, `M.water`, `M.mosaic`, `M.image_material`.

Musts: real-world texture scale (check a tile/brick/roof course in a close preview); white paint albedo
≈ 0.75–0.8 linear, never 1.0, darkest ≥ 0.02; the architect's colours are design intent → keep them as
tint and add texture, roughness and 2–12 mm bevels; neutral glass with the light-path trick (sun reaches
interiors); macro variation on large surfaces; roofs need slope UVs (`M.slope_uvs`); verify UV units
(`M.uv_units_per_metre`) – Revit exports feet (the Datasmith importer converts) – and use `mode="BOX"`
for meshes with useless UVs.

## 7. Site, context, vegetation, entourage

- Never leave the building on a void: ground, streets, sidewalks with curbs, markings, neighbours, horizon.
- `mats = SI.site_materials(rot_deg=fr.angle_deg)`; `SI.terrain` (roads/terrain with a `mask` hole for the
  footprint), `SI.slab` (sidewalk + curb drop), `SI.round_corner`, `SI.polygon_slab`,
  `SI.markings(name, SI.dashed(...) + SI.zebra(...))`, `SI.block_row` (background buildings with
  procedural facades), `SI.hills_ring(high_bearing=...)` (horizon). All take a `zfun(u, v)` for slopes.
- Real dimensions: sidewalk 2–4 m, curb 0.12–0.18 m, lane 3–3.5 m, parking strip 2.2 m, street trees
  every 8–10 m.
- Assets: `polyhaven_fetch.py` (CC0 HDRIs/textures/models) and `blenderkit_fetch.py` (free BlenderKit
  models, no account) → `A.append_blend(path)` → `A.instance`, `A.scatter_line`, `A.scatter_area`,
  `A.gn_scatter` (lawns/ground cover). Check `coll["avz_dims"]` (real size) and face counts.
- Vegetation must fit the region's climate (Mediterranean: olive, cypress, palm, oleander, lavender;
  temperate: deciduous trees, lawns, hedges…). Soften the building base with planting; add parked cars and
  a few people for scale.

## 8. Sky, sun & look

- HDRI: `W.setup_hdri(path, sun_bearing=b)` rotates the map so its sun sits at bearing `b` and reports the
  sun elevation (fixed by the HDRI – pick one whose sun height matches the wanted time; Poly Haven
  `*_puresky` = sky without ground, ideal with a modeled site).
- Any time of day: `W.solar_position(lat, lon, y, m, d, hour, minute, utc_offset)` → (elevation, true
  bearing) (NOAA, verified) → `W.setup_sky(elev, bearing + north_deg, sun_lamp=True)`.
- Good marketing light: sun 25–45° high, 30–60° off the hero facade normal; not behind the camera (flat) nor
  behind the building (silhouette), except dusk shots.
- `W.setup_render(...)` sets AgX + "AgX - Medium High Contrast"; tune `exposure` ±0.5. Never "Standard".

## 9. Cameras

- `SC.level_camera(name, eye, target, lens)` + `SC.fit_level_camera(cam, objs|points, margin=0.05)` →
  yaw, lens and vertical shift for a true two-point perspective (verticals stay vertical). Warnings tell
  when to move back (lens < 14 mm or shift > 0.5). `SC.frame_points(fr, u0, u1, v0, v1, z0, z1)` builds
  custom framing boxes; `SC.orbit_camera(name, target, bearing, elevation, distance, lens)` for aerials.
- Eye height 1.6 m street, 3–6 m elevated; exteriors 18–35 mm, details 35–70 mm, interiors 16–24 mm at
  1.2–1.5 m, also level. Rule of thirds, a foreground element for depth, sky ≤ 1/3, no cropped corners.
- Iterate with `SC.cycles_preview(path, cam, (960, 540), 32)` and `read` the result.

## 10. Rendering

- Benchmark (background, safe): `blender -b proj.blend -P scripts/avz_bench.py -- --device CPU` and
  `--device HIP|HIPRT|CUDA|OPTIX|METAL|ONEAPI`. Exit 134/139, `HSA_STATUS_ERROR…` or "Illegal opcode" in
  `journalctl -k` = backend unusable here → CPU. Then `W.setup_render(device=...)` in the scene.
- Finals, never through the MCP:
  `nohup blender -b proj.blend -P scripts/avz_render.py -- --cams all --res 3840x2160 --samples 512 --jpg > renders/render.log 2>&1 &`
  then poll `renders/render_log.jsonl`. Per-camera overrides: custom props `avz_res`, `avz_samples`,
  `avz_exposure`. Cameras named `_*`/`AVZ_*` are skipped by `--cams all`.
- Expectations: modern 12–16-core CPU – preview 960×540@32 spp 5–15 s, 1080p@256 3–8 min, 4K@512
  20–60 min per exterior with vegetation; a recent RTX GPU is 5–15× faster.

## 11. QA checklist (every final, at 100 %)

- [ ] verticals vertical, horizon level, nothing floating/sunk, contact shadows at the ground
- [ ] no missing/pink textures (`SC.audit()["images_missing"]`), plausible texture scale, no z-fighting
- [ ] glass reflects the sky and shows interiors; no pure white walls or black voids; sky not clipped
- [ ] context fills the frame (no void horizon); vegetation/cars/people at plausible scale
- [ ] same sun across views (unless variants); no noise or denoiser blotches in shadows and glass
- [ ] naming, resolution, README with settings, render times and asset credits

## 12. Tools (`scripts/`)

| Tool | Runs in | Purpose |
|---|---|---|
| `udsmesh_convert.py SRC DST` | shell (uv, executable) | Datasmith meshes → npz cache, UV-unit detection |
| `polyhaven_fetch.py [--root P] list/hdri/tex/model/info` | shell | CC0 assets + contact sheets |
| `blenderkit_fetch.py [--root P] search/get` | shell | free BlenderKit assets (licence + real size reported) |
| `contact_sheet.py OUT IMG…` | shell | review many images in one `read` |
| `gen_api_doc.py` | shell | regenerate reference/api.md after extending the library |
| `avz_render.py` · `avz_bench.py` | `blender -b f.blend -P … -- args` | batch finals · device benchmark/crash test |
| `avz_common` | Blender | bootstrap, project dirs, collections, mesh helper, `Frame`, `detect_frame`, `side_profile`, `run_tool` |
| `avz_import_datasmith` | Blender | Datasmith importer |
| `avz_materials` · `avz_matrules` | Blender | shader builders · auto classification/PBR upgrade |
| `avz_world` | Blender | HDRI/procedural sky, sun lamp, `solar_position`, devices, render preset |
| `avz_scene` | Blender | cameras + fit, previews, analysis views, audit |
| `avz_site` · `avz_assets` | Blender | ground/streets/context/horizon · asset append, instancing, scattering |

Extend the library when a project needs something reusable – keep it generic (no project names), smoke-test
it in a background Blender (`blender -b --factory-startup -P test.py`), then run `scripts/gen_api_doc.py`.

## 13. Environment notes

- Standalone tools are uv scripts (`#!/usr/bin/env -S uv run --script`). On NixOS, PyPI wheels with C
  extensions need libstdc++/libz: `udsmesh_convert.py` re-executes itself with `LD_LIBRARY_PATH` from
  /nix/store; BlenderKit's Go client binary runs through nix-ld. Subprocesses started from Blender must drop
  Blender's `PYTHONPATH` (`C.run_tool` does it).
- AMD APUs/iGPUs: Cycles HIP can abort (`HSA_STATUS_ERROR_INVALID_ISA`, kernel "Illegal opcode") even when
  the gfx kernel ships with Blender; `HSA_OVERRIDE_GFX_VERSION` does not fix it → render on CPU.
