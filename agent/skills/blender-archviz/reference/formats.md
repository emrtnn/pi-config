# Input formats – how to get an architect's model into Blender

Rule 1: never edit the architect's originals; work in a project folder next to them.
Rule 2: after every import run `SC.audit()` + `SC.analysis_views()` and look at the pictures before
anything else (scale, orientation, missing parts, materials).

## Decision table

| What you received | Best route | Keeps | Notes |
|---|---|---|---|
| `.udatasmith` + `<name>_Assets/` | `avz_import_datasmith.import_datasmith(path)` | instancing, BIM categories, materials, textures, cameras, lights, metadata | see "Datasmith" below |
| `.ifc` | extension `bonsai` (BIM authoring, heavy) or `ifc_mesh_importer` (light) | classes, storeys, materials, georeference | IfcSite RefLatitude/RefLongitude, TrueNorth in IfcGeometricRepresentationContext |
| `.fbx` | `bpy.ops.wm.fbx_import(filepath=…)` (Blender 5 C++), fallback `bpy.ops.import_scene.fbx` | hierarchy, materials (basic), cameras | check unit scale (cm/inch exports) and axes |
| `.glb/.gltf` | `bpy.ops.import_scene.gltf(filepath=…)` | PBR materials, metres, Y-up handled | good from SketchUp/Rhino/Twinmotion |
| `.obj` (+ .mtl) | `bpy.ops.wm.obj_import(filepath=…, forward_axis=…, up_axis=…)` | geometry, basic materials | often Y-up and unknown units |
| `.usd/.usdz/.usdc` | `bpy.ops.wm.usd_import(filepath=…)` | hierarchy, UsdPreviewSurface, instancing | metersPerUnit honoured |
| `.skp` | extension `openskp_import_export` | geometry, materials, components | test first; else ask for Datasmith/glTF/FBX from SketchUp |
| `.3dm` (Rhino) | ask for Datasmith / glTF / FBX / OBJ (Rhino exports all) | | no maintained importer on extensions.blender.org |
| `.max` / `.3ds` | extensions `io_scene_max` / `autodesk_3ds_format` | meshes, materials (basic) | better: FBX or Datasmith from Max |
| `.dae` | extension `collada_support` | | COLLADA was removed from Blender 5 core |
| `.dxf` (2D site plan) | extensions `cad2cube` or `import_autocad_dxf_format_dxf` | layers, blocks | use as a tracing reference for streets/plots |
| `.step/.stp/.iges` | extensions `step_importer` / `stepper_reborn` | NURBS → mesh | furniture / facade products |
| `.e57/.las` point clouds | extension `point_cloud_io` | | survey context |
| `.rvt`, `.pln`, `.dwg` 3D, `.vwx`, `.nwd` | not readable | | ask for Datasmith (Revit/ArchiCAD/SketchUp/Rhino plug-in, free) > IFC > USD > FBX/glTF |

What to ask the architect for (in this order): Datasmith export of the 3D view → IFC 4 → USD → FBX/glTF.
Also ask for: site plan (DXF/PDF), location (address or coordinates), true north, material/finish list,
reference images / mood board, the views they want, and photos of the plot for photomontages.

Install an extension from extensions.blender.org:
```python
import bpy
p = bpy.context.preferences
i = [k for k, r in enumerate(p.extensions.repos) if r.module == "blender_org"][0]
bpy.ops.extensions.repo_sync(repo_index=i)
bpy.ops.extensions.package_install(repo_index=i, pkg_id="bonsai", enable_on_install=True)
```
Catalog (search ids/taglines): `curl -sL https://extensions.blender.org/api/v1/extensions/ | python3 -m json.tool`.

## Units, axes, origin – checks for every format

- Size: a door ≈ 2.1 m high, storey ≈ 2.8–3.2 m. If the model is 100× (cm) or 39.37× (inch) off, scale the
  imported root (or importer `global_scale`) and apply scale.
- Axes: Z must be up. OBJ/FBX from Y-up tools may need `up_axis`/`axis_up` options.
- Origin: BIM files are often georeferenced (coordinates of 10⁵–10⁶ m) → float precision artefacts
  (jitter, shadow acne). Recentre: parent all to an empty at the model centre and move it to 0, or offset
  the root; keep the offset in the README for later re-imports.
- Mirrored families (negative scale) are normal in BIM exports – Cycles handles them.
- Duplicated coplanar faces (z-fighting) appear where two elements overlap (floor finish + slab): offset or
  delete the hidden one.

## Datasmith internals (what `avz_import_datasmith` + `udsmesh_convert.py` handle)

File set: `name.udatasmith` (XML) + `name_Assets/` with one `.udsmesh` per unique mesh (hash name) and the
textures. Unreal Engine is not needed.

XML pitfalls: some exporters leave raw `&` in file paths (invalid XML) → the parser escapes them.

Elements:
- `StaticMesh name label` → `<file path>` (mesh), `<Material id name>` slot table.
- `MaterialInstance` / `MasterMaterial` (key-value parameter sets; Revit, ArchiCAD): `DiffuseColor`
  (linear), `DiffuseMap` + `DiffuseMap_UVScaleX/Y`, `_UVOffsetX/Y`, `_UVWAngle`, `Glossiness` (→ roughness ≈
  0.08 + 0.84·(1−g)), `IsMetal`, `Transparency`, `RefractionIndex`, `BumpMap`/`BumpAmount`,
  `SelfIllumination*`; `Type="2"` = translucent. Older exporters use `Color`, `Texture`, `Opacity`.
- `UEPbrMaterial` (SketchUp, Rhino, 3ds Max…): `<Expressions>` graph (Texture PathName, TextureCoordinate
  UTiling/VTiling, Color constant, Scalar constant, Generic/Multiply/Lerp…) and channels `BaseColor`,
  `Roughness`, `Metallic`, `Normal`, `Opacity` pointing to expression indices. The importer flattens it
  (first texture/colour/scalar found upstream of each channel) – fine for archviz; complex graphs (blends,
  masks) need manual fixes.
- `Texture name file srgb` – sometimes the file is missing from `_Assets` (exporter used external paths):
  report lists them; ask for the files or replace with library textures.
- Actors: `Actor` (group/level, may have `<children>`), `ActorMesh` (`<mesh name>`, optional per-actor
  `<material id name>` overrides), `ActorHierarchicalInstancedStaticMesh` (`<Instances>` list of transforms),
  `Camera` (SensorWidth, SensorAspectRatio, FocalLength, DepthOfField/FocusDistance/FStop), `Light`
  (Point/Spot/Area/Directional; Intensity + IntensityUnits). Transforms are **world space** even for nested
  children. `layer` = Revit category / CAD layer → the importer makes one collection per layer.
- `MetaData reference="Actor.<name>"` – all BIM parameters (huge). The importer extracts hints for
  site/base-point actors and any north/latitude/longitude key → `rep["geo_hints"]`; `metadata=True`
  attaches everything as JSON to objects (`ob["ds_meta"]`).
- Coordinates: UE is left-handed, Z-up, centimetres. Blender = (x, −y, z)·0.01; quaternion (w, −x, y, −z);
  cameras/lights additionally need the forward-axis fix (UE +X forward → Blender −Z).

`.udsmesh` formats (auto-detected by `udsmesh_convert.py`):
- **UE5 packed** (Datasmith SDK ≥ 5.x, all current exporters): header string `FDatasmithPackedMeshes`,
  custom-version container, then an Oodle **Kraken**-compressed buffer (`pyooz` decompresses it) holding
  `FMeshDescription` element containers: Vertices(Position) / VertexInstances(VertexIndex, Normal,
  Tangent, BinormalSign, Color, TextureCoordinate[n]) / Edges / Triangles(VertexInstanceIndex,
  PolygonGroupIndex) / Polygons / PolygonGroups(ImportedMaterialSlotName).
- **UE4 legacy FRawMesh**: `u32 1, u32 size, name, "SourceModels", "StructProperty", "DatasmithMeshSourceModel"`,
  then FaceMaterialIndices, SmoothingMasks, VertexPositions, WedgeIndices, TangentX/Y/Z, 8 UV channels,
  WedgeColors.
- Winding: after the Y mirror the stored order is already Blender's CCW; the converter verifies it against
  the stored normals and flips per mesh if needed (`winding_flipped_meshes`).
- UV units: Revit writes UVs in **feet** (3.2808 per m). The converter measures UV-length / world-length and
  records `uv_units_per_m`; the importer converts UVs to metres and scales material UV tiling accordingly, so
  `avz_materials.pbr(mode="UV")` gets true metre UVs.
- No Blender add-on (as of 2026) reads the UE5 packed format; botero-dev/bl_datasmith only reads legacy
  meshes. Unreal Engine (import → export USD/FBX) is the heavyweight alternative.

## After import – tagging so the rest of the pipeline works

- Datasmith: automatic (`ob["ds_name"]`, `ob["ds_layer"]`, materials carry `src_label`, `src_color`,
  `src_texture`, `src_kind` (+ `src_kind_explicit`), `src_opacity`, `src_metal`, `src_roughness`).
- Other formats: `C.tag_model(objs)` (marks the building so cameras/analysis/frames ignore site and assets)
  and `R.tag_source_materials(objs)` (records label/colour/texture/opacity before rebuilding materials).
