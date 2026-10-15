#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyooz", "numpy"]
# ///
"""Convert Datasmith .udsmesh files into .npz mesh caches that Blender loads with numpy.

usage:  udsmesh_convert.py <assets_dir_with_udsmesh> <cache_dir> [--jobs N]

Supports both on-disk formats:
  * UE5 "FDatasmithPackedMeshes" (Datasmith SDK >= 5.x, e.g. Revit/ArchiCAD/SketchUp exporters 2023+):
    Oodle-Kraken compressed FMeshDescription (Vertices / VertexInstances / Triangles / PolygonGroups).
  * Legacy UE4 FRawMesh (header u32=1, "SourceModels" / "DatasmithMeshSourceModel" strings).

Output per mesh (Blender space: metres, right-handed Z-up -> x, -y, z):
  v  float32 (N,3)   positions          f  int32 (T,3) triangles      m int32 (T,) polygon-group index
  n  float32 (3T,3)  corner normals     uv0..uv3 float32 (3T,2) raw source UV units, V flipped (1-v)
  slots  str (G,)    material slot name of each polygon group (usually "0","1",.. = <Material id> in XML)
index.json: per-mesh stats + errors + global "uv_units_per_m" estimate (Revit exports feet: 3.2808).
Winding is verified against the stored normals and flipped per mesh when needed.
"""
import glob
import json
import os
import re
import struct
import sys


def _ensure_native_libs():
    """PyPI wheels with C extensions (numpy, pyooz) need libstdc++/libz, which NixOS does not expose
    globally.  If the import fails, re-exec once with LD_LIBRARY_PATH pointing into /nix/store."""
    try:
        import numpy  # noqa: F401
        import ooz  # noqa: F401
        return
    except ImportError:
        if os.environ.get("_UDSMESH_LIBFIX") or not os.path.isdir("/nix/store"):
            raise

    import platform
    machine = {"x86_64": 0x3E, "aarch64": 0xB7, "arm64": 0xB7}.get(platform.machine())

    def elf_ok(p):                     # 64-bit ELF for this CPU (the store also holds i686 libs)
        try:
            with open(p, "rb") as fh:
                h = fh.read(20)
            return h[:4] == b"\x7fELF" and h[4] == 2 and (machine is None or struct.unpack_from("<H", h, 18)[0] == machine)
        except OSError:
            return False

    def newest(pattern):
        hits = [h for h in glob.glob(pattern) if elf_ok(os.path.realpath(h))]
        key = lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p.split("/lib/")[0]).split("-", 1)[1])]
        return os.path.dirname(sorted(hits, key=key)[-1]) if hits else None

    libs = [d for d in (newest("/nix/store/*-gcc-*-lib/lib/libstdc++.so.6"),
                        newest("/nix/store/*-zlib-*/lib/libz.so.1")) if d]
    env = dict(os.environ, _UDSMESH_LIBFIX="1",
               LD_LIBRARY_PATH=":".join(libs + [p for p in [os.environ.get("LD_LIBRARY_PATH", "")] if p]))
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


_ensure_native_libs()
import numpy as np  # noqa: E402
import ooz  # noqa: E402

TYPE_DTYPE = {0: (np.float32, 4), 1: (np.float32, 3), 2: (np.float32, 2), 3: (np.float32, 1), 4: (np.int32, 1),
              5: (np.uint8, 1)}
DEFAULT_SIZE = {0: 16, 1: 12, 2: 8, 3: 4, 4: 4, 5: 4}
KNOWN_UV_UNITS = {"metre": 1.0, "feet": 3.280839895, "inch": 39.37007874, "cm": 100.0, "mm": 1000.0}


class Reader:
    def __init__(self, b, off=0):
        self.b, self.o = b, off

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def raw(self, n):
        v = self.b[self.o:self.o + n]
        if len(v) != n:
            raise EOFError(f"wanted {n} bytes at {self.o}")
        self.o += n
        return v

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            return self.raw(n)[:-1].decode("latin-1")
        return self.raw(-n * 2)[:-2].decode("utf-16-le")


# ----------------------------------------------------------------------------- UE5 packed meshes
def _attribute(r):
    atype, _extent, _num, nidx = r.u32(), r.u32(), r.i32(), r.i32()
    arrays = []
    for _ in range(nidx):
        ext = r.u32()
        if atype == 6:
            arrays.append([r.fstring() for _ in range(r.i32())])
        else:
            esize, cnt = r.i32(), r.i32()
            dt, comps = TYPE_DTYPE[atype]
            a = np.frombuffer(r.raw(esize * cnt), dtype=dt)
            if comps * ext > 1:
                a = a.reshape(-1, comps * ext)
            arrays.append(a)
    if atype == 6:
        r.fstring()
    elif atype in DEFAULT_SIZE:
        r.raw(DEFAULT_SIZE[atype])            # default value: single element (bool serialized as u32)
    else:
        raise ValueError(f"unknown attribute type {atype}")
    r.u32()                                    # flags
    return {"type": atype, "arrays": arrays}


def _mesh_description(r):
    elements = {}
    for _ in range(r.i32()):
        tname = r.fstring()
        chans = []
        for _c in range(r.i32()):
            nbits = r.i32()
            bits = np.frombuffer(r.raw(((nbits + 31) // 32) * 4), dtype=np.uint32)
            r.i32()
            num = r.i32()
            attrs = {}
            for _a in range(r.i32()):
                name = r.fstring()
                attrs[name] = _attribute(r)
            valid = np.unpackbits(bits.view(np.uint8), bitorder="little")[:nbits].astype(bool)
            chans.append({"valid": valid, "num": num, "attrs": attrs})
        elements[tname] = chans
    return elements


def read_packed(b):
    r = Reader(b)
    r.u32()
    if r.fstring() != "FDatasmithPackedMeshes":
        raise ValueError("not a packed mesh")
    r.u32()
    r.raw(1)
    r.i32(), r.i32(), r.i32()
    r.raw(r.i32() * 20)                        # custom version container
    buf = r.raw(r.i32())
    method, raw_size = buf[0], struct.unpack_from("<i", buf, 1)[0]
    data = ooz.decompress(buf[5:], raw_size) if method == 4 else buf[5:]
    if len(data) != raw_size:
        raise ValueError(f"decompressed size mismatch (method {method})")
    d = Reader(data)
    meshes = []
    for _ in range(d.i32()):
        name, collision = d.fstring(), d.u32()
        models = [_mesh_description(d) for _ in range(d.i32())]
        meshes.append({"name": name, "collision": collision, "models": models})
    m = next((m for m in meshes if not m["collision"]), meshes[0])
    md = m["models"][0]
    V, VI, T, PG = md["Vertices"][0], md["VertexInstances"][0], md["Triangles"][0], md["PolygonGroups"][0]
    pos = V["attrs"]["Position "]["arrays"][0].astype(np.float64)
    vi_vert = VI["attrs"]["VertexIndex"]["arrays"][0]
    nrm = VI["attrs"].get("Normal", {}).get("arrays", [None])[0] if VI["attrs"].get("Normal") else None
    uvs = VI["attrs"]["TextureCoordinate"]["arrays"] if VI["attrs"].get("TextureCoordinate") else []
    tri_vi = T["attrs"]["VertexInstanceIndex"]["arrays"][0].reshape(-1, 3)
    tri_pg = T["attrs"]["PolygonGroupIndex"]["arrays"][0]
    valid = T["valid"][: len(tri_vi)]
    slot_attr = PG["attrs"].get("ImportedMaterialSlotName")
    slots = slot_attr["arrays"][0] if slot_attr and slot_attr["arrays"] else [str(i) for i in range(max(1, PG["num"]))]
    return {"name": m["name"], "pos": pos, "tri_vert": vi_vert[tri_vi[valid]], "tri_corner": tri_vi[valid],
            "group": tri_pg[valid], "corner_normals": nrm, "corner_uvs": uvs, "slots": list(slots)}


# ----------------------------------------------------------------------------- legacy UE4 FRawMesh
def read_legacy(b):
    r = Reader(b)
    _ver, _size = r.u32(), r.u32()
    name = r.raw(r.u32())[:-1].decode("latin-1")
    r.raw(5)
    if r.raw(r.u32()) != b"SourceModels\x00":
        raise ValueError("legacy: SourceModels tag not found")
    r.raw(r.u32())                              # StructProperty
    r.raw(8)
    r.raw(r.u32())                              # DatasmithMeshSourceModel
    r.raw(25)
    r.u32(), r.u32()                            # raw mesh size x2
    r.raw(8)
    r.raw(8)                                    # rawmesh version + licensee version

    def arr(dtype, comps):
        n = r.u32()
        a = np.frombuffer(r.raw(n * comps * np.dtype(dtype).itemsize), dtype=dtype)
        return a.reshape(-1, comps) if comps > 1 else a

    face_mat = arr(np.int32, 1)
    arr(np.uint32, 1)                           # smoothing masks
    pos = arr(np.float32, 3).astype(np.float64)
    wedge = arr(np.int32, 1)
    arr(np.float32, 3)                          # tangent X
    arr(np.float32, 3)                          # tangent Y
    nrm = arr(np.float32, 3)                    # tangent Z (normals)
    uvs = [arr(np.float32, 2) for _ in range(8)]
    uvs = [u for u in uvs if len(u)]
    n_col = r.u32()
    r.raw(n_col * 4)
    groups, group = np.unique(face_mat, return_inverse=True)
    corner = np.arange(len(wedge)).reshape(-1, 3)
    return {"name": name, "pos": pos, "tri_vert": wedge.reshape(-1, 3), "tri_corner": corner,
            "group": group.astype(np.int32), "corner_normals": nrm if len(nrm) == len(wedge) else None,
            "corner_uvs": uvs, "slots": [str(int(g)) for g in groups]}


# ----------------------------------------------------------------------------- conversion
def convert(raw):
    tv, tc, tg = raw["tri_vert"], raw["tri_corner"], raw["group"]
    ok = (tv[:, 0] != tv[:, 1]) & (tv[:, 1] != tv[:, 2]) & (tv[:, 0] != tv[:, 2])
    tv, tc, tg = tv[ok], tc[ok], tg[ok]
    used = np.unique(tv)
    remap = np.full(len(raw["pos"]), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    P = raw["pos"][used] * 0.01
    P[:, 1] *= -1.0                             # UE left-handed cm -> Blender right-handed m
    F = remap[tv]
    out = {"v": P.astype(np.float32), "m": tg.astype(np.int32), "slots": np.array(raw["slots"])}
    order, agree = [0, 1, 2], 1.0
    if raw["corner_normals"] is not None and len(F):
        N = raw["corner_normals"][tc.reshape(-1)].astype(np.float32).copy()
        N[:, 1] *= -1.0
        a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
        g = np.cross(b - a, c - a)
        agree = float((np.einsum("ij,ij->i", g, N.reshape(-1, 3, 3).mean(axis=1)) > 0).mean())
        if agree < 0.5:
            order = [0, 2, 1]
        out["n"] = N.reshape(-1, 3, 3)[:, order].reshape(-1, 3)
    out["f"] = F[:, order].astype(np.int32)
    cvi = tc[:, order].reshape(-1)
    for k, uvarr in enumerate(raw["corner_uvs"][:4]):
        U = uvarr[cvi].astype(np.float32).copy()
        U[:, 1] = 1.0 - U[:, 1]                 # UE V-down -> Blender V-up
        out[f"uv{k}"] = U
    return out, agree


def uv_ratio(out):
    if "uv0" not in out or not len(out["f"]):
        return None
    P = out["v"][out["f"]]
    U = out["uv0"].reshape(-1, 3, 2)
    e3 = np.linalg.norm(P[:, 1] - P[:, 0], axis=1)
    e2 = np.linalg.norm(U[:, 1] - U[:, 0], axis=1)
    ok = (e3 > 0.05) & (e2 > 1e-6)
    return float(np.median(e2[ok] / e3[ok])) if ok.any() else None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(dst, exist_ok=True)
    files = sorted(glob.glob(os.path.join(src, "*.udsmesh")))
    index, errors, ratios, flipped, kinds = {}, {}, [], 0, {"packed": 0, "legacy": 0}
    for f in files:
        key = os.path.splitext(os.path.basename(f))[0]
        try:
            b = open(f, "rb").read()
            if b"FDatasmithPackedMeshes" in b[:64]:
                raw, kind = read_packed(b), "packed"
            else:
                raw, kind = read_legacy(b), "legacy"
            kinds[kind] += 1
            out, agree = convert(raw)
            flipped += agree < 0.5
            np.savez(os.path.join(dst, key + ".npz"), **out)
            rr = uv_ratio(out)
            if rr:
                ratios.append(rr)
            v = out["v"]
            index[key] = {"name": raw["name"], "kind": kind, "nv": int(len(v)), "nt": int(len(out["f"])),
                          "slots": out["slots"].tolist(), "uv_channels": sum(1 for k in out if k.startswith("uv")),
                          "winding_agreement": round(agree, 3),
                          "bbmin": v.min(0).tolist() if len(v) else [0, 0, 0],
                          "bbmax": v.max(0).tolist() if len(v) else [0, 0, 0]}
        except Exception as e:  # noqa: BLE001
            errors[key] = f"{type(e).__name__}: {e}"
    uvu = {"median": None, "unit": "unknown", "factor": 1.0}
    if ratios:
        r = np.array(ratios)
        med, p10, p90 = float(np.median(r)), float(np.percentile(r, 10)), float(np.percentile(r, 90))
        uvu.update(median=round(med, 5), p10=round(p10, 5), p90=round(p90, 5))
        for unit, k in KNOWN_UV_UNITS.items():
            if abs(med / k - 1) < 0.03 and (p90 - p10) / med < 0.1:
                uvu.update(unit=unit, factor=k)
    json.dump({"index": index, "errors": errors, "uv_units_per_m": uvu, "kinds": kinds}, open(
        os.path.join(dst, "index.json"), "w"), indent=1)
    tv = sum(x["nv"] for x in index.values())
    tt = sum(x["nt"] for x in index.values())
    print(json.dumps({"files": len(files), "ok": len(index), "errors": len(errors), "kinds": kinds,
                      "vertices": tv, "triangles": tt, "winding_flipped_meshes": int(flipped),
                      "uv_units_per_m": uvu, "first_errors": dict(list(errors.items())[:5])}))


if __name__ == "__main__":
    main()
