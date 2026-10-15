"""avz_world - sky, sun, colour management and Cycles render presets.  Runs INSIDE Blender.

Bearings are compass bearings in MODEL space: 0 = +Y, 90 = +X, clockwise.  If the model's +Y is not true
north, pass north_deg = model-space bearing of TRUE north (from Revit 'Angle to True North', IFC TrueNorth...)
to the helpers that take true bearings (solar_position based ones).
"""
import datetime as _dt
import math
import os

import bpy
import numpy as np
from mathutils import Euler

import avz_common as C


# ----------------------------------------------------------------------------- solar geometry
def solar_position(lat, lon, year, month, day, hour, minute=0, utc_offset=0.0):
    """NOAA solar position. Returns (elevation_deg, true_bearing_deg) for local clock time.
    utc_offset: hours east of UTC incl. DST (Spain summer = +2, winter = +1)."""
    t = _dt.datetime(year, month, day, 0, 0) + _dt.timedelta(hours=hour + minute / 60.0 - utc_offset)
    jd = (t - _dt.datetime(2000, 1, 1, 12)).total_seconds() / 86400.0 + 2451545.0
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + math.sin(2 * mr) * (0.019993 - 0.000101 * jc)
         + math.sin(3 * mr) * 0.000289)
    om = math.radians(125.04 - 1934.136 * jc)
    app = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(om))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(om))
    decl = math.asin(math.sin(eps) * math.sin(app))
    y = math.tan(eps / 2) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    tst = (t.hour * 60 + t.minute + t.second / 60 + eot + 4 * lon) % 1440
    ha = tst / 4 - 180 if tst / 4 >= 0 else tst / 4 + 180
    latr, har = math.radians(lat), math.radians(ha)
    cz = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(har)
    zen = math.acos(max(-1.0, min(1.0, cz)))
    elev = 90 - math.degrees(zen)
    if elev > -0.575:   # atmospheric refraction (NOAA approximation)
        te = math.tan(math.radians(elev))
        if elev > 5:
            elev += (58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5) / 3600
        elif elev > -0.575:
            elev += (1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)))) / 3600
    den = math.cos(latr) * math.sin(zen)
    if abs(den) < 1e-9:
        az = 180.0
    else:
        ca = max(-1.0, min(1.0, (math.sin(latr) * math.cos(zen) - math.sin(decl)) / den))
        az = (math.degrees(math.acos(ca)) + 180) % 360 if ha > 0 else (540 - math.degrees(math.acos(ca))) % 360
    return elev, az


def bearing_to_azimuth(bearing_deg):
    """Compass bearing (from +Y, clockwise) -> math azimuth (from +X, CCW), radians."""
    return math.radians(90.0 - bearing_deg)


# ----------------------------------------------------------------------------- HDRI sky
def hdri_sun(im):
    """(azimuth_from_+X_rad, elevation_rad, peak) of the brightest pixel of an equirect image."""
    w, h = im.size
    px = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(px)
    lum = px.reshape(h, w, 4)[:, :, :3].sum(axis=2)
    y, x = divmod(int(np.argmax(lum)), w)
    u, v = (x + 0.5) / w, (y + 0.5) / h            # rows bottom -> top
    return (0.5 - u) * 2 * math.pi, (v - 0.5) * math.pi, float(lum[y, x])   # Cycles equirect convention


def setup_hdri(path, sun_bearing=None, rotation_deg=None, strength=1.0, camera_strength=None, saturation=1.0,
               world_name="AVZ_Sky"):
    """HDRI world.  sun_bearing: model-space compass bearing where the HDRI sun must end up (auto-rotates
    the map).  The HDRI fixes the sun ELEVATION - choose an HDRI whose sun height matches the wanted time of
    day ('puresky' Poly Haven HDRIs have no ground, ideal when you model your own site).
    camera_strength: different sky brightness seen directly by the camera (lighting unchanged)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.get(world_name) or bpy.data.worlds.new(world_name)
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord")
    mp = N.new("ShaderNodeMapping")
    env = N.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(os.path.abspath(path), check_existing=True)
    az, elev, peak = hdri_sun(env.image)
    if rotation_deg is not None:
        rot = math.radians(rotation_deg)
    elif sun_bearing is not None:
        rot = az - bearing_to_azimuth(sun_bearing)    # lookup = Rz(rot) * world_dir
    else:
        rot = 0.0
    mp.inputs["Rotation"].default_value[2] = rot
    L.new(tc.outputs["Generated"], mp.inputs["Vector"])
    L.new(mp.outputs["Vector"], env.inputs["Vector"])
    src = env.outputs["Color"]
    if saturation != 1.0:
        hs = N.new("ShaderNodeHueSaturation")
        hs.inputs["Saturation"].default_value = saturation
        L.new(src, hs.inputs["Color"])
        src = hs.outputs["Color"]
    bg = N.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    L.new(src, bg.inputs["Color"])
    out = N.new("ShaderNodeOutputWorld")
    if camera_strength is not None:
        bg2 = N.new("ShaderNodeBackground")
        bg2.inputs["Strength"].default_value = camera_strength
        L.new(src, bg2.inputs["Color"])
        lp = N.new("ShaderNodeLightPath")
        mx = N.new("ShaderNodeMixShader")
        L.new(lp.outputs["Is Camera Ray"], mx.inputs[0])
        L.new(bg.outputs["Background"], mx.inputs[1])
        L.new(bg2.outputs["Background"], mx.inputs[2])
        L.new(mx.outputs[0], out.inputs["Surface"])
    else:
        L.new(bg.outputs["Background"], out.inputs["Surface"])
    final_bearing = (90.0 - math.degrees(az - rot)) % 360.0
    info = {"hdri": os.path.basename(path), "sun_elevation_deg": round(math.degrees(elev), 1),
            "sun_bearing_deg": round(final_bearing, 1), "rotation_deg": round(math.degrees(rot), 1),
            "peak": round(peak, 1)}
    world["avz_sky"] = info
    return info


def setup_sky(sun_elevation_deg, sun_bearing_deg, model="MULTIPLE_SCATTERING", strength=1.0, sun_lamp=False,
              lamp_strength=4.0, altitude=0.0, air=1.0, aerosol=1.0, ozone=1.0, world_name="AVZ_Sky"):
    """Physical procedural sky (any sun position/time of day).  Blender 5.x models: SINGLE_SCATTERING,
    MULTIPLE_SCATTERING, PREETHAM, HOSEK_WILKIE.  sun_rotation of the node == compass bearing.
    sun_lamp=True adds a matching Sun light (crisper shadows / EEVEE)."""
    scene = bpy.context.scene
    world = bpy.data.worlds.get(world_name) or bpy.data.worlds.new(world_name)
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = model
    el, br = math.radians(sun_elevation_deg), math.radians(sun_bearing_deg)
    for prop, val in (("sun_elevation", el), ("sun_rotation", br), ("altitude", altitude), ("air_density", air),
                      ("aerosol_density", aerosol), ("ozone_density", ozone)):
        if hasattr(sky, prop):
            setattr(sky, prop, val)
    if hasattr(sky, "sun_direction"):
        sky.sun_direction = (math.sin(br) * math.cos(el), math.cos(br) * math.cos(el), math.sin(el))
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    if sun_lamp:
        add_sun_lamp(sun_elevation_deg, sun_bearing_deg, lamp_strength)
    world["avz_sky"] = {"model": model, "sun_elevation_deg": sun_elevation_deg, "sun_bearing_deg": sun_bearing_deg}
    return world["avz_sky"].to_dict()


def add_sun_lamp(elevation_deg, bearing_deg, strength=4.0, angle_deg=0.53, name="AVZ_Sun"):
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, bpy.data.lights.new(name, "SUN"))
        C.collection("Lighting").objects.link(ob)
    ob.data.energy = strength
    ob.data.angle = math.radians(angle_deg)
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = Euler((math.radians(elevation_deg) - math.pi / 2, 0.0, -math.radians(bearing_deg)))
    return ob


# ----------------------------------------------------------------------------- devices & render
def configure_device(kind="CPU"):
    """kind: CPU | HIP | CUDA | OPTIX | METAL | ONEAPI.  Sets user prefs + scene device.
    WARNING: test GPU backends with avz_bench.py in a *background* process first - a driver crash
    takes the interactive Blender (and the MCP session) down with it."""
    scene = bpy.context.scene
    prefs = bpy.context.preferences.addons["cycles"].preferences
    if kind == "CPU":
        scene.cycles.device = "CPU"
        return {"device": "CPU"}
    prefs.compute_device_type = kind.replace("HIPRT", "HIP")
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == prefs.compute_device_type
    if hasattr(prefs, "use_hiprt"):
        prefs.use_hiprt = kind == "HIPRT"
    scene.cycles.device = "GPU"
    return {"device": kind, "devices": [(d.name, d.type, d.use) for d in prefs.devices]}


def setup_render(samples=256, noise=0.02, res=(1920, 1080), pct=100, denoise=True, look="AgX - Medium High Contrast",
                 exposure=0.0, clamp_indirect=8.0, device=None, film_transparent=False, motion_blur=False):
    """Archviz Cycles preset: adaptive sampling + OIDN, light tree, clamped indirect, AgX view transform."""
    scene = bpy.context.scene
    r = scene.render
    r.engine = "CYCLES"
    c = scene.cycles
    if device is not None:
        configure_device(device)
    c.samples = samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = noise
    c.use_denoising = denoise
    c.denoiser = "OPENIMAGEDENOISE"
    if hasattr(c, "denoising_use_gpu"):
        c.denoising_use_gpu = c.device == "GPU"
    c.max_bounces = 12
    c.diffuse_bounces = 4
    c.glossy_bounces = 4
    c.transmission_bounces = 12
    c.transparent_max_bounces = 16
    c.volume_bounces = 1
    c.sample_clamp_direct = 0.0
    c.sample_clamp_indirect = clamp_indirect
    c.blur_glossy = 0.6
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.use_light_tree = True
    r.resolution_x, r.resolution_y = res
    r.resolution_percentage = pct
    r.film_transparent = film_transparent
    r.use_persistent_data = True
    r.use_motion_blur = motion_blur
    vs = scene.view_settings
    scene.display_settings.display_device = "sRGB"
    vs.view_transform = "AgX"
    try:
        vs.look = look
    except TypeError:
        vs.look = "None"
    vs.exposure = exposure
    vs.gamma = 1.0
    r.image_settings.file_format = "PNG"
    r.image_settings.color_depth = "16"
    r.image_settings.compression = 15
    return {"engine": r.engine, "device": c.device, "samples": samples, "res": list(res), "look": vs.look}
