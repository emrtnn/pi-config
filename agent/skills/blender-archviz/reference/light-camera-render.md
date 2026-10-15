# Light, cameras, rendering, post

## Sun & sky

Conventions: compass bearing in model space, 0 = +Y, 90 = +X, clockwise. `north_deg` = model-space bearing
of TRUE north (0 when +Y is true north). True bearing b → model bearing b + north_deg.

- **HDRI** – `W.setup_hdri(path, sun_bearing=b, strength=1, camera_strength=None, saturation=1)`.
  Detects the brightest pixel (the sun), rotates the map so the sun sits at bearing `b`, returns the sun
  elevation (fixed by the photo). Choose the HDRI by sun height and cloud cover:
  `polyhaven_fetch.py list hdris --cat skies` (`*_puresky` = no ground); clear skies for crisp shadows,
  partly cloudy for marketing, overcast for soft interiors. `camera_strength` lets the visible sky be
  brighter/darker than the light it casts.
- **Procedural** – `W.setup_sky(elevation, bearing, model="MULTIPLE_SCATTERING", sun_lamp=True)` for any
  time; `W.add_sun_lamp(elev, bearing, strength, angle_deg)` alone for EEVEE or crisper shadows.
- **Time of day** – `W.solar_position(lat, lon, year, month, day, hour, minute, utc_offset)` → (elevation,
  true bearing). Validated against the `astral` library (±0.01°). utc_offset includes DST.
- Marketing sun: 25–45° elevation, 30–60° off the hero facade normal; check `fr.outward_bearing(side)`.
  Facades facing the pole side (N in the northern hemisphere) only get sun in summer mornings/evenings –
  pick the hero facade accordingly or accept soft light.
- Dusk / blue hour: sun −2…−6° (procedural sky), interior emission/area lights 2700–3000 K, exposure +1…+2,
  window glow from interior lights, street lamps.
- Interiors: sun + sky through windows; add area lights at window openings (same size, low power) or rely on
  more samples; exposure +1…+2.5; never fill-light with point lights everywhere.

## Cameras

- Level (two-point perspective) for everything at human height: `SC.level_camera` + `SC.fit_level_camera`.
  Framing by shift_y, not by tilting. Keep |shift_y| ≤ 0.4 (more looks stretched – move back/up).
- Eye height 1.6 m (street), 1.2–1.5 m (interiors), 3–6 m (elevated/first-floor views), 30–80 m (drone).
- Lens (36 mm sensor): exteriors 18–35 mm (min ~16), details 35–70, interiors 16–24, aerial 30–50.
- Composition: building on thirds, foreground element (tree, curb, planter, car) for depth, sky ≤ 1/3,
  margins ≥ 5 %, leading lines (street, sidewalk). One view per message (facade, entrance, garden…).
- Source cameras (Datasmith/IFC): keep their position/lens; set the render aspect to `sensor_aspect`.
- Depth of field only for details/interiors (f/5.6–11), never on hero exteriors.
- Photomontage (render into a site photo): match the camera with the extensions `fit_the_camera` (FTC,
  Blender 5.2+) or fSpy; render with `film_transparent=True` + shadow catcher ground (`ob.is_shadow_catcher`).

## Render settings (`W.setup_render`)

| Setting | Preview | Final | Why |
|---|---|---|---|
| samples / adaptive noise | 32 / 0.05 | 512–1024 / 0.01 | adaptive sampling stops early in clean areas |
| denoiser | OIDN | OIDN (CPU if GPU path is unstable) | |
| clamp indirect | 8 | 8 | removes fireflies from glossy/glass paths |
| bounces (max/diff/gloss/trans) | 12/4/4/12 | same | glass needs transmission bounces |
| caustics | off | off | huge noise, no visual gain in archviz |
| light tree | on | on | many lights (interiors) |
| view transform / look | AgX / Medium High Contrast | same | filmic highlight roll-off, no clipped skies |
| output | PNG 16 bit | PNG 16 bit (+ JPG q92, EXR multilayer for post) | |
| persistent data | on | on | faster re-renders of the same scene |

Device choice: run `avz_bench.py` in background for CPU and each GPU backend; keep the fastest that doesn't
crash. Typical: CPU 12–16 cores → 1080p@256 ≈ 3–8 min, 4K@512 ≈ 20–60 min per exterior; RTX 4070+ 5–15× faster.
Big batches: `nohup blender -b proj.blend -P avz_render.py -- … &`, poll `renders/render_log.jsonl`;
`--time-limit` caps seconds per image.

## Post-production (optional, light touch)

- In Blender compositor: subtle Glare (Fog Glow, threshold ≥ 1) for sun reflections, slight lens
  distortion/vignette 0–5 %, colour balance – or deliver the EXR to Photoshop/Affinity.
- Never over-saturate skies or vegetation; keep whites neutral; sharpen lightly for web JPGs.
