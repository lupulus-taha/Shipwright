#!/usr/bin/env python3
"""Builds the "Jackal Refuge" scene of the Play as Nabooru mod into a mod archive for SoH's `mods` folder.

No game data is stored in the repository: the pieces taken from the game (the dried-up oasis pit of the Desert
Colossus, its light settings, the cracked-surface texture) are read from the player's own `oot.o2r` when this runs.

The area is a first mockup: a flat patch of dry, cracked land in the late afternoon, the oasis pit on the right and
the placeholder of a new dungeon (a box with a row of pillars) on the left.

usage: build_jackal_refuge.py [--oot x64/Debug/oot.o2r] [--out x64/Debug/mods/jackal_refuge.o2r]
"""
import argparse
import math
import struct
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]

# ---------------------------------------------------------------- layout (game units; north is -z, Nabooru ~60 tall)
SCENE = "scenes/shared/jackal_refuge_scene/jackal_refuge_"

TILE = 160  # world size of one repetition of the ground texture
CELL = 800  # ground grid cell (a whole number of texture repetitions)
FINE_HALF = 4800  # the ground is made of CELL squares up to here...
FAR_CELL, FAR_HALF = 3200, 30400  # ...and of bigger ones out to well beyond the draw distance, at any screen width
WALL = 3200  # invisible walls around the playable part

PIT_CENTER = (1600, 0)  # x, z of the middle of the pit
PIT_BLOCK = (800, -800, 2400, 800)  # ground squares replaced by the pit and the ground fitted around it
PIT_RIM_LEVEL = 40  # height above the old water level at which the pit's uneven rim is flattened into the ground

BOX = (-2400, -1200, -800, 400)  # dungeon main building: x0, z0, x1, z1
BOX_HEIGHT = 1000
PILLAR_RADIUS, PILLAR_HEIGHT, PILLAR_SIDES = 130, 940, 16
PILLARS = [(BOX[2] + PILLAR_RADIUS - 20, z) for z in (-1000, -600, -200, 200)]  # along the building's east face

SPAWN = (100, 0, 1900)
SPAWN_YAW = 0x8000  # facing north: dungeon to the left, pit to the right

TIME = (17, 20)  # late afternoon; time stands still here

# ground colours: the texture is a greyscale crack pattern, blended from ENV (surface) to PRIM (cracks)
GROUND_SURFACE = (240, 210, 162)
GROUND_CRACKS = (60, 40, 26)
# the pattern's faint lines are stretched to full contrast: these percentiles of its values become 0 and 1
CRACKS_LEVELS = (30, 97)
DUNGEON_COLOR = (205, 205, 210)

# ---------------------------------------------------------------- sources in oot.o2r
COLOSSUS = "scenes/shared/spot11_scene/spot11_scene"
COLOSSUS_ROOM = "scenes/shared/spot11_scene/spot11_room_0"
# Lakeside Laboratory: greyscale cracked surface (the name carries its ROM offset: other game versions may differ)
CRACKS_SOURCE = "scenes/shared/hylia_labo_scene/hylia_labo_sceneTex_010090"
CRACKS_TEX = SCENE + "sceneCrackedEarthTex"
CRACKS_TEX_SIZE = 32

# ---------------------------------------------------------------- resource plumbing
M64 = (1 << 64) - 1
CRC_TABLE = []
for _i in range(256):
    _c = _i << 56
    for _ in range(8):
        _c = ((_c << 1) ^ 0x42F0E1EBA9EA3693) & M64 if _c >> 63 else (_c << 1) & M64
    CRC_TABLE.append(_c)


def crc64(path):
    """libultraship's resource path hash"""
    c = M64
    for b in path.encode():
        c = CRC_TABLE[(c >> 56) ^ b] ^ ((c << 8) & M64)
    return c


def header(kind):
    return struct.pack("<I4sIQ", 0, kind[::-1].encode(), 0, 0xDEADBEEFDEADBEEF) + bytes(64 - 20)


def string(s):
    return struct.pack("<I", len(s)) + s.encode()


class Archive:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.names = {crc64(n): n for n in self.zip.namelist()}

    def read(self, name):
        try:
            return self.zip.read(name)
        except KeyError:
            raise SystemExit(f"{name} is missing from the game archive")

    def name(self, words):
        return self.names[(words[0] << 32) | words[1]]


class Reader:
    def __init__(self, data, pos=64):
        self.data, self.pos = data, pos

    def get(self, fmt):
        out = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return out if len(out) > 1 else out[0]

    def skip(self, size, count_fmt="I"):
        """skips a counted list of fixed-size entries"""
        count = self.get(count_fmt)
        self.pos += size * count

    def string(self):
        n = self.get("I")
        self.pos += n
        return self.data[self.pos - n : self.pos].decode()


# ---------------------------------------------------------------- display lists
G_VTX, G_TRI1, G_TRI2, G_QUAD, G_CULLDL, G_DL, G_ENDDL = 0x01, 0x05, 0x06, 0x07, 0x03, 0xDE, 0xDF
G_SETTIMG_OTR, G_DL_OTR, G_VTX_OTR, G_MARKER = 0x20, 0x31, 0x32, 0x33
TWO_PART = {G_SETTIMG_OTR, G_DL_OTR, G_VTX_OTR, G_MARKER, 0x35, 0x36, 0x42}
G_TEXTURE, G_GEOMETRYMODE, G_SETOTHERMODE_L, G_SETOTHERMODE_H = 0xD7, 0xD9, 0xE2, 0xE3
G_RDPLOADSYNC, G_RDPPIPESYNC, G_LOADTLUT, G_SETTILESIZE, G_LOADBLOCK, G_SETTILE = 0xE6, 0xE7, 0xF0, 0xF2, 0xF3, 0xF5
G_SETPRIMCOLOR, G_SETENVCOLOR, G_SETCOMBINE = 0xFA, 0xFB, 0xFC
G_CULL_BACK, G_FOG, G_LIGHTING, G_TEXTURE_GEN, G_TEXTURE_GEN_LINEAR = 0x400, 0x10000, 0x20000, 0x40000, 0x80000
# colour combiner inputs
CC_COMBINED, CC_TEXEL0, CC_PRIMITIVE, CC_SHADE, CC_ENVIRONMENT = 0, 1, 3, 4, 5
PIPESYNC = (G_RDPPIPESYNC << 24, 0, None)


def read_dl(data):
    """-> [(w0, w1, second word pair of a two-part command or None)]"""
    pos, out = 0x48, []
    while True:
        w0, w1 = struct.unpack_from("<II", data, pos)
        pos += 8
        extra = None
        if w0 >> 24 in TWO_PART:
            extra = struct.unpack_from("<II", data, pos)
            pos += 8
        out.append((w0, w1, extra))
        if w0 >> 24 == G_ENDDL:
            return out


def read_vertices(data):
    r = Reader(data)
    assert r.get("I") == 0x19, "not a vertex array"
    out = []
    for _ in range(r.get("I")):
        v = r.get("3hH2h4B")
        out.append(v[:3] + v[4:])  # x y z, s t, colour or normal, alpha
    return out


def material_at(state):
    """The material in effect after a run of state commands, without what later commands replaced."""
    single, geometry, tiles, load = {}, [], {}, None
    for cmd in state:
        op = cmd[0] >> 24
        if op == G_SETTIMG_OTR:
            load = [cmd]
        elif op in (G_SETTILE, G_RDPLOADSYNC, G_LOADBLOCK, G_LOADTLUT) or (op == G_RDPPIPESYNC and load):
            assert load is not None, "texture load without an image"
            load.append(cmd)
        elif op == G_SETTILESIZE:
            tiles[cmd[1] >> 24 & 7] = load + [cmd]
            load = None
        elif op == G_GEOMETRYMODE:
            geometry.append(cmd)
        elif op in (G_SETOTHERMODE_L, G_SETOTHERMODE_H):
            single[cmd[0]] = cmd
        elif op in (G_TEXTURE, G_SETPRIMCOLOR, G_SETENVCOLOR, G_SETCOMBINE):
            single[op] = cmd
        elif op != G_RDPPIPESYNC:
            raise SystemExit(f"unexpected display list command {op:#x} in a game material")
    out = [PIPESYNC] + geometry + list(single.values())
    for tile in sorted(tiles):
        out += tiles[tile]
    return out


def read_triangles(archive, name):
    """-> [(material, [vertex, vertex, vertex])] of one of the game's display lists"""
    vertex_cache, state, buffer, out = {}, [], [None] * 64, []

    def corner(index):
        return buffer[index // 2]

    for w0, w1, extra in read_dl(archive.read(name)):
        op = w0 >> 24
        if op == G_VTX_OTR:
            count = w0 >> 12 & 0xFF
            first = (w0 >> 1 & 0x7F) - count
            res = archive.name(extra)
            if res not in vertex_cache:
                vertex_cache[res] = read_vertices(archive.read(res))
            for i in range(count):
                buffer[first + i] = vertex_cache[res][w1 // 16 + i]
        elif op in (G_TRI1, G_TRI2, G_QUAD):
            for word in (w0, w1)[: 1 if op == G_TRI1 else 2]:
                out.append((len(state), [corner(word >> s & 0xFF) for s in (16, 8, 0)]))
        elif op in (G_CULLDL, G_MARKER, G_ENDDL):
            pass
        elif op in (G_DL, G_DL_OTR):
            return []  # (lists that only call others: not what we are after)
        else:
            state.append((w0, w1, extra))
    materials = {}
    return [(materials.setdefault(n, material_at(state[:n])), tri) for n, tri in out]


def write_dl(path, groups):
    """groups: [(material commands, [triangle = 3 vertex tuples])] -> {resource path: bytes} (list + its vertices)"""
    vtx_path = path + "Vtx"
    vtx_hash = crc64(vtx_path)
    cmds, vertices = [(G_MARKER << 24, 0xBEEFBEEF, (crc64(path) >> 32, crc64(path) & 0xFFFFFFFF))], []
    for material, tris in groups:
        cmds += material
        todo = list(tris)
        while todo:
            slots, batch = {}, []
            while todo and len(slots) + len(set(todo[0]) - set(slots)) <= 32:
                for v in todo[0]:
                    slots.setdefault(v, len(slots))
                batch.append([slots[v] * 2 for v in todo.pop(0)])
            w0 = G_VTX_OTR << 24 | len(slots) << 12 | len(slots) << 1
            cmds.append((w0, len(vertices) * 16, (vtx_hash >> 32, vtx_hash & 0xFFFFFFFF)))
            vertices += list(slots)
            for i in range(0, len(batch) - 1, 2):
                a, b = batch[i], batch[i + 1]
                cmds.append((G_TRI2 << 24 | a[0] << 16 | a[1] << 8 | a[2], b[0] << 16 | b[1] << 8 | b[2], None))
            if len(batch) % 2:
                a = batch[-1]
                cmds.append((G_TRI1 << 24 | a[0] << 16 | a[1] << 8 | a[2], 0, None))
    cmds.append((G_ENDDL << 24, 0, None))
    dl = header("ODLT") + b"\x04" + b"\xff" * 7
    for w0, w1, extra in cmds:
        dl += struct.pack("<II", w0, w1) + (struct.pack("<II", *extra) if extra else b"")
    vtx = header("OARR") + struct.pack("<II", 0x19, len(vertices))
    for v in vertices:
        vtx += struct.pack("<3hH2h4B", v[0], v[1], v[2], 0, *v[3:])
    return {path: dl, vtx_path: vtx}


def combine(c1, a1, c2, a2):
    """SetCombine of a 2-cycle material; each argument is (a, b, c, d) of (a - b) * c + d, None = 0"""

    def pick(v, zero):
        return zero if v is None else v

    (a, b, c, d), (aa, ab, ac, ad), (a_2, b_2, c_2, d_2), (aa_2, ab_2, ac_2, ad_2) = c1, a1, c2, a2
    w0 = (G_SETCOMBINE << 24 | pick(a, 15) << 20 | pick(c, 31) << 15 | pick(aa, 7) << 12 | pick(ac, 7) << 9
          | pick(a_2, 15) << 5 | pick(c_2, 31))
    w1 = (pick(b, 15) << 28 | pick(b_2, 15) << 24 | pick(aa_2, 7) << 21 | pick(ac_2, 7) << 18 | pick(d, 7) << 15
          | pick(ab, 7) << 12 | pick(ad, 7) << 9 | pick(d_2, 7) << 6 | pick(ab_2, 7) << 3 | pick(ad_2, 7))
    return (w0, w1, None)


ALPHA_ONE, ALPHA_COMBINED = (None, None, None, 6), (None, None, None, 0)
# what the game's own lit, fogged, opaque scenery uses
LIT_OPAQUE = [
    PIPESYNC,
    (G_GEOMETRYMODE << 24 | (~(G_TEXTURE_GEN | G_TEXTURE_GEN_LINEAR) & 0xFFFFFF), 0, None),
    (G_GEOMETRYMODE << 24 | 0xFFFFFF, G_FOG | G_LIGHTING | G_CULL_BACK, None),
    (0xE3001001, 0, None),  # no texture palette
    (0xE200001C, 0xC8112078, None),  # fog, anti-aliased z-buffered opaque surface
]


def color(op, rgb):
    return (op << 24, rgb[0] << 24 | rgb[1] << 16 | rgb[2] << 8 | 0xFF, None)


def ground_material():
    size, fmt_i = CRACKS_TEX_SIZE, 4
    mask = size.bit_length() - 1
    tex = crc64(CRACKS_TEX)
    tile = mask << 14 | mask << 4  # repeat both ways
    return LIT_OPAQUE + [
        (G_TEXTURE << 24 | 2, 0xFFFFFFFF, None),
        # an 8-bit intensity texture, loaded the way the game does (as 16-bit texels)
        (G_SETTIMG_OTR << 24 | fmt_i << 21 | 2 << 19, 0, (tex >> 32, tex & 0xFFFFFFFF)),
        (G_SETTILE << 24 | fmt_i << 21 | 2 << 19, 7 << 24 | tile, None),
        (G_RDPLOADSYNC << 24, 0, None),
        (G_LOADBLOCK << 24, 7 << 24 | (size * size // 2 - 1) << 12 | (2048 + size // 8 - 1) // (size // 8), None),
        PIPESYNC,
        (G_SETTILE << 24 | fmt_i << 21 | 1 << 19 | (size // 8) << 9, tile, None),
        (G_SETTILESIZE << 24, (size - 1) << 14 | (size - 1) << 2, None),
        combine((CC_PRIMITIVE, CC_ENVIRONMENT, CC_TEXEL0, CC_ENVIRONMENT), ALPHA_ONE,
                (CC_COMBINED, None, CC_SHADE, None), ALPHA_COMBINED),
        color(G_SETPRIMCOLOR, GROUND_CRACKS),
        color(G_SETENVCOLOR, GROUND_SURFACE),
    ]


def plain_material(rgb):
    return LIT_OPAQUE + [
        (G_TEXTURE << 24, 0, None),
        combine((CC_PRIMITIVE, None, CC_SHADE, None), ALPHA_ONE, (None, None, None, CC_COMBINED), ALPHA_COMBINED),
        color(G_SETPRIMCOLOR, rgb),
    ]


def cracked_earth_texture(archive):
    """the Lakeside Laboratory's cracked surface (8-bit intensity), with its contrast stretched"""
    data = archive.read(CRACKS_SOURCE)
    version, (kind, width, height) = struct.unpack_from("<I", data, 8)[0], struct.unpack_from("<3I", data, 64)
    assert (version, kind, width, height) == (0, 6, CRACKS_TEX_SIZE, CRACKS_TEX_SIZE), "unexpected crack texture"
    pixels = data[80 : 80 + width * height]
    ranked = sorted(pixels)
    lo, hi = (ranked[len(ranked) * p // 100] for p in CRACKS_LEVELS)
    return data[:80] + bytes(min(255, max(0, round((v - lo) * 255 / (hi - lo)))) for v in pixels)


# ---------------------------------------------------------------- geometry helpers
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def normal(a, b, c):
    """front faces are counter-clockwise, as in the game's own meshes and collision"""
    n = cross(sub(b, a), sub(c, a))
    length = math.sqrt(dot(n, n))
    assert length > 0, "degenerate triangle"
    return (n[0] / length, n[1] / length, n[2] / length)


def facing(tri, towards):
    """the triangle, wound so that its front faces `towards`"""
    return tri if dot(normal(*tri), towards) > 0 else (tri[0], tri[2], tri[1])


def vertex(pos, uv=(0, 0), n=(0, 1, 0), alpha=255):
    assert all(-32768 <= v <= 32767 for v in (*pos, *uv)), f"vertex out of range: {pos} {uv}"
    return (*pos, *uv, *(round(c * 127) & 0xFF for c in n), alpha)


def ground_uv(x, z, x0, z0):
    return (round((x - x0) * 1024 * CRACKS_TEX_SIZE / 32 / TILE), round((z - z0) * 1024 * CRACKS_TEX_SIZE / 32 / TILE))


def flat_quad(x0, z0, x1, z1):
    """two ground triangles (positions only)"""
    a, b, c, d = (x0, 0, z0), (x0, 0, z1), (x1, 0, z1), (x1, 0, z0)
    return [(a, b, c), (a, c, d)]


def box_faces(x0, z0, x1, z1, height):
    """-> [(outward normal, quad corners)] of the walls and the top"""
    lo, hi = 0, height
    return [
        ((0, 0, 1), [(x0, lo, z1), (x1, lo, z1), (x1, hi, z1), (x0, hi, z1)]),
        ((0, 0, -1), [(x1, lo, z0), (x0, lo, z0), (x0, hi, z0), (x1, hi, z0)]),
        ((1, 0, 0), [(x1, lo, z1), (x1, lo, z0), (x1, hi, z0), (x1, hi, z1)]),
        ((-1, 0, 0), [(x0, lo, z0), (x0, lo, z1), (x0, hi, z1), (x0, hi, z0)]),
        ((0, 1, 0), [(x0, hi, z1), (x1, hi, z1), (x1, hi, z0), (x0, hi, z0)]),
    ]


def ring(cx, cz, radius, sides):
    return [(round(cx + radius * math.sin(2 * math.pi * i / sides)), round(cz + radius * math.cos(2 * math.pi * i / sides)),
             (math.sin(2 * math.pi * i / sides), 0, math.cos(2 * math.pi * i / sides))) for i in range(sides)]


# ---------------------------------------------------------------- the pit, lifted from the Desert Colossus
def read_scene(archive):
    """-> (light settings as stored, the oasis' water box) of the Desert Colossus"""
    r = Reader(archive.read(COLOSSUS))
    collision = lights = None
    for _ in range(r.get("I")):
        cmd = r.get("I")
        if cmd == 0x18:  # alternate headers
            for _ in range(r.get("I")):
                r.string()
        elif cmd == 0x15:  # sound
            r.get("3b")
        elif cmd == 0x04:  # rooms
            for _ in range(r.get("i")):
                r.string(), r.get("2i")
        elif cmd == 0x19:  # camera, world map
            r.get("bi")
        elif cmd == 0x03:
            collision = r.string()
        elif cmd == 0x06:  # entrances
            r.skip(2, "I")
        elif cmd == 0x07:  # special objects
            r.get("bh")
        elif cmd == 0x00:  # spawns
            r.skip(16, "I")
        elif cmd == 0x11:  # skybox
            r.get("4b")
        elif cmd == 0x13:  # exits
            r.skip(2, "I")
        elif cmd == 0x0F:
            count = r.get("I")
            lights = struct.pack("<I", count) + r.data[r.pos : r.pos + 22 * count]
            r.pos += 22 * count
        elif cmd == 0x14:
            break
        else:
            raise SystemExit(f"unexpected scene command {cmd:#x} in the Desert Colossus")
    assert collision and lights, "the Desert Colossus has no collision or lights?"
    c = Reader(archive.read(collision))
    c.pos += 12
    c.skip(6, "i")
    c.skip(16, "I")
    c.skip(8, "I")
    c.skip(8, "I")
    c.skip(6, "i")
    assert c.get("i") >= 1, "the Desert Colossus has no oasis?"
    return lights, c.get("5h")  # water box: x, surface y, z, x length, z length


def room_display_lists(archive):
    """-> ([opaque list paths], [translucent list paths]) of the Desert Colossus"""
    r = Reader(archive.read(COLOSSUS_ROOM))
    opa, xlu = [], []
    for _ in range(r.get("I")):
        cmd = r.get("I")
        if cmd == 0x18:  # alternate headers
            for _ in range(r.get("I")):
                r.string()
        elif cmd in (0x16, 0x08, 0x12, 0x10, 0x05):  # echo, behaviour, skybox, time, wind
            r.get({0x16: "b", 0x08: "bi", 0x12: "2b", 0x10: "3b", 0x05: "4b"}[cmd])
        elif cmd == 0x0A:
            assert r.get("2b") == (0, 2), "unexpected room mesh type in the Desert Colossus"
            for _ in range(r.get("b")):
                r.get("b4h")  # (flag, centre, radius)
                a, b = r.string(), r.string()
                opa += [a] if a else []
                xlu += [b] if b else []
            return opa, xlu
        else:
            break
    raise SystemExit("could not find the Desert Colossus' room mesh")


def read_pit(archive):
    """-> (bottom [(material, tri)], rim decal [(material, tri)], water level): the dried-up oasis, as the game has it.

    The bottom is a fan around the lowest point under the oasis' water; the grass around it is a translucent ring
    (opaque at the water's edge, fading out over the sand) lying on the slope up to the desert floor.
    """
    lights, (wx, wy, wz, wlx, wlz) = read_scene(archive)
    opa, xlu = room_display_lists(archive)

    def inside(v):
        return wx - 200 <= v[0] <= wx + wlx + 200 and wz - 200 <= v[2] <= wz + wlz + 200

    bottom = []
    for path in opa:
        tris = [t for t in read_triangles(archive, path) if all(inside(v) for v in t[1])]
        bottom += [t for t in tris if min(v[1] for v in t[1]) < wy and max(v[1] for v in t[1]) <= wy]
    assert bottom, "could not find the bottom of the oasis"
    shore = {v[:3] for _, tri in bottom for v in tri if v[1] == wy}
    rim = []
    for path in xlu:
        for material, tri in read_triangles(archive, path):
            if any(v[:3] in shore for v in tri) and all(v[1] >= wy for v in tri) and {v[8] for v in tri} == {0, 255}:
                rim.append((material, tri))
    assert rim, "could not find the grass around the oasis"
    for _, tri in bottom + rim:
        assert normal(*(v[:3] for v in tri))[1] > 0, "the pit should face up"
    return lights, bottom, rim, wy


# ---------------------------------------------------------------- collision
SURFACE_DIRT, SURFACE_SAND, SURFACE_STONE = 0, 1, 2


class Collision:
    def __init__(self):
        self.vertices, self.polys = {}, []

    def add(self, surface, tri):
        n = normal(*tri)
        idx = [self.vertices.setdefault(tuple(p), len(self.vertices)) for p in tri]
        self.polys.append(struct.pack("<4H3hh", surface, *idx, *(round(c * 32767) for c in n), round(-dot(n, tri[0]))))

    def data(self):
        vs = list(self.vertices)
        out = header("OCOL")
        out += struct.pack("<6h", *(min(v[i] for v in vs) for i in range(3)), *(max(v[i] for v in vs) for i in range(3)))
        out += struct.pack("<i", len(vs)) + b"".join(struct.pack("<3h", *v) for v in vs)
        out += struct.pack("<I", len(self.polys)) + b"".join(self.polys)
        # surface types (indexed by the SURFACE_ constants): second word (material) first, then the first word
        out += struct.pack("<I", 3) + b"".join(struct.pack("<II", material, 0) for material in range(3))
        out += struct.pack("<IHhi", 1, 0, 0, 0)  # one camera entry: the scene's default camera
        out += struct.pack("<i", 0)  # camera positions
        out += struct.pack("<i", 0)  # water boxes
        return out


# ---------------------------------------------------------------- the scene
def build(archive):
    lights, pit_bottom, pit_rim, water_level = read_pit(archive)
    files = {}
    collision = Collision()

    # --- the pit: moved to its place, its rim (which follows the desert's dunes) pressed flat into the ground
    px = [v[0] for _, tri in pit_rim for v in tri]
    pz = [v[2] for _, tri in pit_rim for v in tri]
    dx = PIT_CENTER[0] - (min(px) + max(px)) // 2
    dz = PIT_CENTER[1] - (min(pz) + max(pz)) // 2

    def place(v):
        y = 0 if v[1] > water_level else v[1] - water_level - PIT_RIM_LEVEL
        return (v[0] + dx, y, v[2] + dz)

    def placed(tris):
        return [(material, [(*place(v), *v[3:]) for v in tri]) for material, tri in tris]

    def by_material(tris):
        groups = []
        for material, tri in tris:
            if not groups or groups[-1][0] is not material:
                groups.append((material, []))
            groups[-1][1].append(tuple(tri))
        return groups

    bx0, bz0, bx1, bz1 = PIT_BLOCK
    assert all(v % CELL == 0 for v in PIT_BLOCK) and CELL % TILE == 0 and FINE_HALF % CELL == 0, "off the ground grid"
    rim_edge = {place(v) for _, tri in pit_rim for v in tri if v[1] > water_level}
    assert all(bx0 < x < bx1 and bz0 < z < bz1 for x, _, z in rim_edge), "the pit does not fit its ground block"
    files.update(write_dl(SCENE + "room_0PitDL", by_material(placed(pit_bottom))))
    files.update(write_dl(SCENE + "room_0PitRimDL", by_material(placed(pit_rim))))
    for _, tri in placed(pit_bottom):
        collision.add(SURFACE_SAND, [v[:3] for v in tri])

    # --- ground
    ground = []

    def ground_tri(tri, origin, normals=None):
        tri = facing(tri, (0, 1, 0))
        ground.append(tuple(vertex(p, ground_uv(p[0], p[2], *origin), normals[p] if normals else (0, 1, 0)) for p in tri))

    def squares(half, size):
        return [(x, z) for x in range(-half, half, size) for z in range(-half, half, size)]

    for x, z in squares(FINE_HALF, CELL):
        if bx0 <= x < bx1 and bz0 <= z < bz1:
            continue
        for tri in flat_quad(x, z, x + CELL, z + CELL):
            ground_tri(tri, (x, z))
            if -WALL <= x < WALL and -WALL <= z < WALL:
                collision.add(SURFACE_DIRT, tri)
    for x, z in squares(FAR_HALF, FAR_CELL):
        if not (-FINE_HALF <= x < FINE_HALF and -FINE_HALF <= z < FINE_HALF):
            for tri in flat_quad(x, z, x + FAR_CELL, z + FAR_CELL):
                ground_tri(tri, (x, z))

    # the slope under the grass ring: the same triangles, so the ring lies exactly on it
    for _, tri in placed(pit_rim):
        normals = {v[:3]: tuple(((c + 128) % 256 - 128) / 127 for c in v[5:8]) for v in tri}
        ground_tri([v[:3] for v in tri], (bx0, bz0), normals)
        collision.add(SURFACE_SAND, [v[:3] for v in tri])

    # between the pit's edge and its ground block: walk around both outlines at once, by angle
    def angle(p):
        return math.atan2(p[2] - PIT_CENTER[1], p[0] - PIT_CENTER[0])

    edge = [(x, 0, z) for x in range(bx0, bx1 + 1, CELL) for z in range(bz0, bz1 + 1, CELL)
            if x in (bx0, bx1) or z in (bz0, bz1)]
    around = sorted(rim_edge | set(edge), key=angle)
    start = next(i for i, p in enumerate(around) if p in rim_edge and around[i - 1] not in rim_edge)
    inner, outer = around[start], around[start - 1]
    area = 0
    for p in around[start + 1 :] + around[: start + 1]:
        tri = (inner, outer, p)
        area += abs(cross(sub(tri[1], tri[0]), sub(tri[2], tri[0]))[1]) / 2
        ground_tri(tri, (bx0, bz0))
        collision.add(SURFACE_DIRT, facing(tri, (0, 1, 0)))
        if p in rim_edge:
            inner = p
        else:
            outer = p
    hole = sorted(rim_edge, key=angle)
    hole_area = abs(sum(a[0] * b[2] - b[0] * a[2] for a, b in zip(hole, hole[1:] + hole[:1]))) / 2
    assert abs(area + hole_area - (bx1 - bx0) * (bz1 - bz0)) < 1, "the ground around the pit has gaps or overlaps"
    files.update(write_dl(SCENE + "room_0GroundDL", [(ground_material(), ground)]))
    files[CRACKS_TEX] = cracked_earth_texture(archive)

    # --- placeholder dungeon: a box and a row of pillars
    dungeon = []
    for n, quad in box_faces(*BOX, BOX_HEIGHT):
        for tri in ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])):
            tri = facing(tri, n)
            dungeon.append(tuple(vertex(p, n=n) for p in tri))
            collision.add(SURFACE_STONE, tri)
    for cx, cz in PILLARS:
        around = ring(cx, cz, PILLAR_RADIUS, PILLAR_SIDES)
        for (x0, z0, n0), (x1, z1, n1) in zip(around, around[1:] + around[:1]):
            a, b, c, d = (x0, 0, z0), (x1, 0, z1), (x1, PILLAR_HEIGHT, z1), (x0, PILLAR_HEIGHT, z0)
            ns = {a: n0, b: n1, c: n1, d: n0}
            for tri in ((a, b, c), (a, c, d)):
                tri = facing(tri, n0)
                dungeon.append(tuple(vertex(p, n=ns[p]) for p in tri))
            top = facing(((cx, PILLAR_HEIGHT, cz), d, c), (0, 1, 0))
            dungeon.append(tuple(vertex(p) for p in top))
        # collision: half the sides are plenty
        coarse = around[::2]
        for (x0, z0, n0), (x1, z1, _) in zip(coarse, coarse[1:] + coarse[:1]):
            a, b, c, d = (x0, 0, z0), (x1, 0, z1), (x1, PILLAR_HEIGHT, z1), (x0, PILLAR_HEIGHT, z0)
            for tri in ((a, b, c), (a, c, d)):
                collision.add(SURFACE_STONE, facing(tri, n0))
            collision.add(SURFACE_STONE, facing(((cx, PILLAR_HEIGHT, cz), d, c), (0, 1, 0)))
    files.update(write_dl(SCENE + "room_0DungeonDL", [(plain_material(DUNGEON_COLOR), dungeon)]))

    # --- invisible walls around the playable part
    for n, quad in box_faces(-WALL, -WALL, WALL, WALL, 3000)[:4]:
        quad = [(x, y - 400, z) for x, y, z in quad]
        for tri in ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])):
            collision.add(SURFACE_DIRT, facing(tri, (-n[0], 0, -n[2])))
    files[SCENE + "sceneCollisionHeader"] = collision.data()

    # --- room: mesh, and time standing still in the late afternoon
    room = header("OROM") + struct.pack("<I", 6)
    room += struct.pack("<Ib", 0x16, 0)  # echo
    room += struct.pack("<Ibi", 0x08, 0, 0)  # room behaviour
    room += struct.pack("<I2b", 0x12, 0, 0)  # skybox and sun/moon shown
    room += struct.pack("<I3b", 0x10, *TIME, 0)  # time of day, not advancing
    meshes = [("GroundDL", ""), ("DungeonDL", ""), ("PitDL", "PitRimDL")]
    room += struct.pack("<I3b", 0x0A, 0, 0, len(meshes))
    for opa, xlu in meshes:
        room += struct.pack("<b", 0) + string(SCENE + "room_0" + opa) + string(SCENE + "room_0" + xlu if xlu else "")
    room += struct.pack("<I", 0x14)
    files[SCENE + "room_0"] = room

    # --- scene
    scene = header("OROM") + struct.pack("<I", 10)
    scene += struct.pack("<I3b", 0x15, 0, 0x13, 0x7F)  # no music, no ambience
    scene += struct.pack("<Ii", 0x04, 1) + string(SCENE + "room_0") + struct.pack("<2i", 0, 0)
    scene += struct.pack("<Ibi", 0x19, 0, 0x0B)  # default camera; on the world map: at the Desert Colossus
    scene += struct.pack("<I", 0x03) + string(SCENE + "sceneCollisionHeader")
    scene += struct.pack("<II2b", 0x06, 1, 0, 0)  # entrance 0: spawn 0, room 0
    scene += struct.pack("<Ibh", 0x07, 1, 2)  # overworld Navi hints, gameplay_field_keep
    scene += struct.pack("<IIH6hH", 0x00, 1, 0, *SPAWN, 0, SPAWN_YAW - 0x10000, 0, 0x0FFF)
    scene += struct.pack("<I4b", 0x11, 0, 1, 0, 0)  # the open sky
    scene += struct.pack("<I", 0x0F) + lights
    scene += struct.pack("<I", 0x14)
    files[SCENE + "scene"] = scene
    return files, len(ground), len(collision.polys)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oot", type=Path, default=REPO / "x64" / "Debug" / "oot.o2r", help="the game archive SoH made")
    ap.add_argument("--out", type=Path, default=REPO / "x64" / "Debug" / "mods" / "jackal_refuge.o2r")
    args = ap.parse_args()
    files, ground, polys = build(Archive(args.oot))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    print(f"{args.out}: {len(files)} resources, {ground} ground triangles, {polys} collision polygons")


if __name__ == "__main__":
    main()
