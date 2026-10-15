# Assets and add-ons (state 2026)

## Free asset sources (used by the skill's tools)

| Source | Tool | Licence | Good for |
|---|---|---|---|
| Poly Haven (api.polyhaven.com) | `polyhaven_fetch.py` | CC0 (no attribution needed) | HDRI skies (`*_puresky`), PBR textures (plaster, concrete, brick, roofing, wood, tiles, asphalt, paving, terrain), ~500 models (trees, shrubs, grass, furniture, street lamps) |
| BlenderKit / Blendkit (blenderkit.com public API) | `blenderkit_fetch.py` | free assets: `royalty_free` (commercial use OK, no asset redistribution) or `cc_zero` | trees & shrubs (olive, oleander, palms, abelia…), cars, furniture, materials. Search in English; check `faces` and `dims_m` |
| ambientCG | extension `ambientcg_material_importer` | CC0 | extra PBR materials |
| IKEA catalogue | extension `ikea_browser` | IKEA terms | interior furniture |

Record every downloaded asset (name, source, licence, author) in the project README.

Asset handling: `A.append_blend(path)` appends into a hidden library collection (`AVZ_Library`, excluded
from the view layer, bottom-centred `instance_offset`); place with collection instances (`A.instance`,
`A.scatter_line`, `A.scatter_area`) or Geometry Nodes (`A.gn_scatter`) – never duplicate heavy meshes.
Appended relative texture paths are rebased by Blender; BlenderKit blends often pack their textures.

## Add-ons

Install from extensions.blender.org via `bpy.ops.extensions.package_install(repo_index=i, pkg_id=…)`
(see formats.md), from a zip via
`bpy.ops.extensions.package_install_files(filepath=zip, repo="user_default", enable_on_install=True)`.

Free, worth installing for archviz:
| id | What |
|---|---|
| `sun_position` | geographic sun/sky (NOAA); `avz_world.solar_position` is the scriptable equivalent |
| `Bagapie` | scattering, ivy, arrays, architecture helpers (interactive) |
| `node_wrangler` (core) | shader editing shortcuts |
| BlenderKit add-on (`BlenderKit/Blendkit` GitHub releases zip) | in-UI asset browser; its Go client needs nix-ld on NixOS |
| `bonsai` / `ifc_mesh_importer` | IFC |
| `photometric_light_plus` | IES/EULUMDAT luminaires for interiors |
| `modular_tree`, `sapling_tree_gen`, `ivygen` | procedural trees/ivy when no asset fits |
| `fit_the_camera` (5.2+) | camera matching to site photos (photomontage) |
| `srtm_terrain_importer` | real terrain elevation around the site |
| `multiple_camera_render`, `per_camera_resolution` | interactive batch rendering (the skill uses `avz_render.py`) |

Paid industry standards (need the user's purchase/licence – suggest, don't assume):
| Product | Why studios use it |
|---|---|
| Geo-Scatter (free "Biome-Reader" variant) | best scattering/biomes for vegetation and large sites |
| Botaniq / Vegetation library add-ons | large, scale-correct plant libraries with seasons |
| Physical Starlight & Atmosphere | physical sky/sun/atmosphere with animation |
| Extreme PBR Nexus | big one-click PBR material library |
| Human Generator | realistic people (free people models are scarce) |
| Archipack | parametric walls/windows/stairs when you must model or fix architecture |

Choose free routes first; mention paid ones to the user when they would clearly raise quality (people,
dense vegetation).
