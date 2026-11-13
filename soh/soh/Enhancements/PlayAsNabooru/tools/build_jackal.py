"""Builds the jackal, an enemy of the Play as Nabooru mod: model, skeleton and animations, made in Blender and
exported with Fast64 into a mod archive for SoH's `mods` folder.

Everything about the jackal is original and described right here (shape, textures, rig, animations), so the model can
be rebuilt and adjusted by changing numbers; the .blend this writes is for looking at it and for hand-editing.

Run by Blender (5.x), with the HarbourMasters fork of Fast64 (github.com/HarbourMasters/fast64: the upstream addon only
writes C), which does not have to be installed, only present on disk:

  blender --background --factory-startup --python build_jackal.py -- [--fast64 DIR containing fast64_hm]
          [--out x64/Debug/mods/jackal.o2r] [--blend FILE] [--preview DIR] [--no-export]

--preview renders contact sheets of the model and of every animation; set the environment variable JACKAL_REPORT to
an animation's name (Idle, Walk, Run, ...) to print what each leg does on each of its frames.

In the style of the N64 game: about 400 triangles, one flex skeleton of 21 limbs with every vertex on a single limb,
two small 16-bit textures (fur 32x64, face 64x32, each within the console's 4 KB texture memory), lit and fogged like
the game's own creatures. Sizes below are in game units (Nabooru is about 60 tall); x is forward here ("f"), up is "u",
"s" is to the animal's left.
"""
import argparse
import math
import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

REPO = Path(__file__).resolve().parents[5]
OBJECT = "objects/object_jackal"  # where the game finds the jackal's resources
SKELETON = "gJackalSkel"
UNIT = 0.1  # Blender units per game unit
MODEL_SCALE = 100  # model units per game unit, as in the game's own creatures: the actor is drawn at scale 0.01
FPS = 20  # the game animates at 20 frames a second

# ---------------------------------------------------------------- shape
SIDE = 3.0  # how far the legs stand from the middle

# body, back to front: (f, top, bottom, half width); the first is closed off under the tail
TORSO = [(-16.0, 21.8, 17.2, 2.0), (-12.5, 24.0, 15.4, 4.1), (-4.0, 24.3, 16.6, 3.4), (4.0, 25.2, 13.0, 4.4),
         (10.5, 25.6, 12.6, 4.3)]
TORSO_LIMBS = ["Root", "Root", "Root", "Chest", "Chest"]
# chest front, neck, back of the skull: (f, u) of the middle, tilt of the ring in degrees, half height, half width
NECK = [((13.2, 20.4), 30, 5.2, 3.3, "Chest"), ((16.0, 25.0), 55, 3.8, 3.1, "Neck"), ((18.0, 28.6), 45, 3.4, 3.1, "Head")]
# how far down the fur texture the top of each of those rings starts (the dark saddle ends in a point up the neck)
NECK_FUR = [0.12, 0.3, 0.36]
# head: skull, brow, then the upper half of the muzzle down to the nose
HEAD = [((20.4, 30.4), 8, 3.8, 3.9), ((24.0, 29.8), -6, 2.8, 2.9), ((27.8, 29.5), -10, 1.25, 1.7),
        ((31.2, 28.75), -10, 0.9, 1.05)]
NOSE_TIP = (32.0, 28.8)
# lower jaw: (f, top, bottom, half width); its top is the line of the mouth
JAW = [(23.2, 28.5, 26.5, 2.2), (27.8, 28.3, 27.1, 1.45), (31.0, 27.9, 27.4, 0.8)]
# ears (left): three points at the base and the tip, as (f, u, s)
EAR = {"front": (22.0, 32.4, 1.8), "outer": (19.0, 31.0, 3.4), "inner": (18.6, 32.8, 1.2), "tip": (19.4, 41.0, 4.2)}
# tail: (f, u) of the middle and radius; the last is the tip
TAIL = [((-15.2, 21.4), 1.3, "Tail1"), ((-18.6, 18.4), 2.3, "Tail1"), ((-21.2, 14.0), 2.6, "Tail2"),
        ((-22.6, 9.6), 1.8, "Tail2"), ((-23.0, 6.4), 0.0, "Tail2")]
# legs (left): a point inside the body, then rings (f, u, half depth, half width) down to the ground
FRONT_LEG = {"top": (10.4, 21.0, 2.3), "rings": [(10.0, 18.0, 2.7, 1.7, "A"), (8.8, 13.4, 1.75, 1.35, "A"),
                                                  (9.5, 5.6, 1.15, 1.05, "B"), (10.0, 1.9, 1.2, 1.1, "C")],
             "paw": (13.0, 9.0, 1.45), "limbs": ("FrontLeg%sA", "FrontLeg%sB", "FrontLeg%sC")}
HIND_LEG = {"top": (-10.9, 21.6, 2.2), "rings": [(-10.2, 18.6, 3.8, 1.8, "A"), (-7.2, 13.2, 2.0, 1.4, "A"),
                                                  (-13.2, 7.8, 1.15, 1.05, "B"), (-12.3, 1.9, 1.2, 1.1, "C")],
            "paw": (-9.4, -13.4, 1.45), "limbs": ("HindLeg%sA", "HindLeg%sB", "HindLeg%sC")}

# skeleton: limb -> (parent, joint (f, u, s), where its bone points). Limbs come out in the game in this order:
# every limb's children sorted by name (ROOT first).
BONES = {
    "Root": (None, (-10.0, 20.4, 0), (-2.0, 20.6, 0)),
    "Chest": ("Root", (-2.0, 20.6, 0), (12.0, 21.5, 0)),
    "Neck": ("Chest", (12.2, 22.4, 0), (17.2, 28.2, 0)),
    "Head": ("Neck", (17.2, 28.2, 0), (24.5, 29.6, 0)),
    "EarL": ("Head", (19.9, 32.2, 2.2), (19.4, 41.0, 4.2)),
    "EarR": ("Head", (19.9, 32.2, -2.2), (19.4, 41.0, -4.2)),
    "Jaw": ("Head", (22.2, 27.8, 0), (31.0, 27.6, 0)),
    "FrontLegLA": ("Chest", (10.4, 19.4, SIDE), (8.8, 13.4, SIDE)),
    "FrontLegLB": ("FrontLegLA", (8.8, 13.4, SIDE), (9.5, 5.6, SIDE)),
    "FrontLegLC": ("FrontLegLB", (9.5, 5.6, SIDE), (10.4, 0.8, SIDE)),
    "FrontLegRA": ("Chest", (10.4, 19.4, -SIDE), (8.8, 13.4, -SIDE)),
    "FrontLegRB": ("FrontLegRA", (8.8, 13.4, -SIDE), (9.5, 5.6, -SIDE)),
    "FrontLegRC": ("FrontLegRB", (9.5, 5.6, -SIDE), (10.4, 0.8, -SIDE)),
    "HindLegLA": ("Root", (-10.8, 19.8, SIDE), (-7.2, 13.2, SIDE)),
    "HindLegLB": ("HindLegLA", (-7.2, 13.2, SIDE), (-13.2, 7.8, SIDE)),
    "HindLegLC": ("HindLegLB", (-13.2, 7.8, SIDE), (-12.0, 0.8, SIDE)),
    "HindLegRA": ("Root", (-10.8, 19.8, -SIDE), (-7.2, 13.2, -SIDE)),
    "HindLegRB": ("HindLegRA", (-7.2, 13.2, -SIDE), (-13.2, 7.8, -SIDE)),
    "HindLegRC": ("HindLegRB", (-13.2, 7.8, -SIDE), (-12.0, 0.8, -SIDE)),
    "Tail1": ("Root", (-15.0, 21.6, 0), (-19.9, 16.3, 0)),
    "Tail2": ("Tail1", (-19.9, 16.3, 0), (-23.0, 6.4, 0)),
}

# ---------------------------------------------------------------- textures
FUR_SIZE = (32, 64)
FACE_SIZE = (64, 32)
FUR_TEXELS = 2.0  # fur texture pixels per game unit, along the body
# face texture: the head seen from the side, from (f, u) top left to bottom right
FACE_FROM, FACE_TO = (17.4, 34.6), (32.4, 26.2)
EYE = (23.0, 31.25)  # middle of the eye

BLACK, DARK, GREY = (30, 26, 26), (66, 56, 50), (132, 122, 108)
TAN, RUST, GOLD = (196, 150, 90), (176, 118, 66), (214, 172, 106)
CREAM, PALE = (232, 212, 172), (244, 232, 204)
IRIS, IRIS_DARK = (255, 190, 40), (206, 120, 16)


def noise(*key):
    """a repeatable random number in 0..1 for a pixel"""
    h = 0x9E3779B9
    for k in key:
        h = (h ^ (int(k) & 0xFFFFFFFF)) * 0x85EBCA6B & 0xFFFFFFFF
        h ^= h >> 13
    h = h * 0xC2B2AE35 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 0xFFFF


def mix(a, b, t):
    t = min(1.0, max(0.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def fur_pixel(x, y):
    """the coat from the back (top of the texture) to the belly: grizzled dark saddle, tan flanks, pale underside.
    Repeats sideways; the hairs run that way."""
    w, h = FUR_SIZE
    streak = noise(x // 2 + (y % 2), y, 1)  # short hairs, two pixels long
    fine = noise(x, y, 2)
    saddle_end = 13 + 3.0 * noise(x // 3, 7) + 2.0 * noise(x, 8)  # ragged edge
    belly_start = 47 + 3.0 * noise(x // 4, 9) + 2.0 * noise(x, 10)
    if y < saddle_end:
        tone = DARK if streak < 0.5 else (GREY if streak < 0.82 else TAN)
        tone = mix(tone, BLACK, 0.5 * fine if y < 6 else 0.25 * fine)
        return mix(tone, TAN, max(0.0, (y - saddle_end + 4) / 4) * 0.6)
    if y < belly_start:
        tone = mix(TAN, RUST, streak * 0.55) if streak < 0.7 else mix(TAN, GOLD, 0.7)
        tone = mix(tone, DARK, 0.5) if (y < saddle_end + 4 and fine < 0.3) else tone  # stray dark hairs under the saddle
        return mix(tone, CREAM, max(0.0, (y - belly_start + 6) / 6) * 0.7)
    return mix(CREAM, PALE, streak) if fine > 0.15 else mix(CREAM, TAN, 0.4)


def face_at(f, u):
    """face texture pixel position of a point of the head"""
    return ((f - FACE_FROM[0]) / (FACE_TO[0] - FACE_FROM[0]) * FACE_SIZE[0],
            (u - FACE_FROM[1]) / (FACE_TO[1] - FACE_FROM[1]) * FACE_SIZE[1])


def face_pixel(x, y):
    """the head from the side (both sides use it): tan with a pale cheek and throat, dark lips and nose, amber eye"""
    f = FACE_FROM[0] + (x + 0.5) / FACE_SIZE[0] * (FACE_TO[0] - FACE_FROM[0])
    u = FACE_FROM[1] + (y + 0.5) / FACE_SIZE[1] * (FACE_TO[1] - FACE_FROM[1])
    streak, fine = noise(x // 2, y, 11), noise(x, y, 12)
    tone = mix(TAN, RUST, streak * 0.5) if streak < 0.75 else mix(TAN, GOLD, 0.6)
    skull_u, brow_f, nose_f, nose_u = HEAD[0][0][1], HEAD[1][0][0], HEAD[3][0][0], HEAD[3][0][1]
    # forehead and the back of the head take up the neck's grizzle; the muzzle's top is rusty
    if u > skull_u + 2.0 - 0.25 * (f - FACE_FROM[0]) and f < brow_f and fine < 0.4:
        tone = mix(tone, DARK, 0.55)
    if f > brow_f + 1:
        tone = mix(tone, RUST, 0.45)
    # pale cheek, throat, lips and chin: below a line that rises towards the nose
    edge = skull_u - 1.0 + 0.5 * math.sin((f - FACE_FROM[0]) * 0.5) + 0.5 * (noise(x, 13) - 0.5)
    edge -= 0.25 * (f - brow_f - 1) if f > brow_f + 1 else 0
    edge -= (FACE_FROM[0] + 2.5 - f) * 0.5 if f < FACE_FROM[0] + 2.5 else 0
    if u < edge:
        tone = mix(tone, mix(CREAM, PALE, streak), min(1.0, (edge - u) / 0.6))
    # mouth: dark lips where muzzle and jaw meet
    (f0, top0, _, _), (f1, top1, _, _), (f2, top2, _, _) = JAW
    lips = top0 + (top1 - top0) * (f - f0) / (f1 - f0) if f < f1 else top1 + (top2 - top1) * (f - f1) / (f2 - f1)
    if f > f0 + 0.6 and abs(u - lips) < 0.2:
        tone = mix(tone, BLACK, 0.85)
    # nose
    if f > nose_f - 0.05 - 0.5 * (u - nose_u + 0.55) and u > lips + 0.25:
        tone = BLACK if fine > 0.1 else DARK
    # eye: slanted almond, dark rim running on towards the muzzle, amber iris, slit of a pupil, a highlight
    ex, ey = f - EYE[0], u - EYE[1]
    ex, ey = ex * 0.94 - ey * 0.34, ex * 0.34 + ey * 0.94  # the inner corner is lower
    almond = (ex / 1.05) ** 2 + (ey / 0.56) ** 2
    if almond < 1.9 or (0 < ex < 2.2 and abs(ey + 0.12 * ex) < 0.22):
        tone = mix(tone, BLACK, 0.9)
    if almond < 1.0:
        tone = mix(IRIS, IRIS_DARK, min(1.0, almond * 1.1))
        if abs(ex) < 0.26 and abs(ey) < 0.5:
            tone = BLACK
        if -0.75 < ex < -0.3 and 0.05 < ey < 0.36:
            tone = (255, 255, 240)
    # brow: a darker stroke over the eye, sloping down towards the nose
    if abs(ex) < 1.5 and 0.85 < ey + 0.1 * ex < 1.15:
        tone = mix(tone, DARK, 0.6)
    return tone


def make_image(name, size, pixel):
    image = bpy.data.images.new(name, size[0], size[1], alpha=True)
    values = []
    for row in range(size[1] - 1, -1, -1):  # Blender images start at the bottom
        for x in range(size[0]):
            r, g, b = pixel(x, row)
            # 5 bits a channel, as the console stores them
            values += [round(r / 255 * 31) / 31, round(g / 255 * 31) / 31, round(b / 255 * 31) / 31, 1.0]
    image.pixels.foreach_set(values)
    image.pack()
    return image


# ---------------------------------------------------------------- mesh
def at(f, u, s=0.0):
    """game units, forward / up / left -> Blender (the model faces -Y there, as Fast64 expects)"""
    return Vector((s * UNIT, -f * UNIT, u * UNIT))


class Mesh:
    """vertices are shared by position and limb, so surfaces are smooth across limbs"""

    def __init__(self):
        self.points, self.limbs, self.index, self.faces = [], [], {}, []

    def vertex(self, f, u, s, limb):
        key = (round(f, 3), round(u, 3), round(s, 3), limb)
        if key not in self.index:
            self.index[key] = len(self.points)
            self.points.append(at(f, u, s))
            self.limbs.append(limb)
        return self.index[key]

    def face(self, corners, material):
        """corners: [(vertex, (texture u, v from the top))]"""
        if len({v for v, _ in corners}) >= 3:
            self.faces.append(([v for v, _ in corners], [(a, 1.0 - b) for _, (a, b) in corners], material))

    def loft(self, ring_a, ring_b, material, closed=True):
        """quads between two rings of (vertex, uv)"""
        n = len(ring_a)
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            self.face([ring_a[i], ring_a[j], ring_b[j], ring_b[i]], material)


FUR, FACE = 0, 1
# an eight-sided ring, as fractions of (half width, half height): from the top, down the left side, up the right
OCTAGON = [(0.0, 1.0), (0.74, 0.72), (1.0, 0.05), (0.62, -0.72), (0.0, -1.0), (-0.62, -0.72), (-1.0, 0.05), (-0.74, 0.72)]
OCTAGON_FUR = [0.02, 0.2, 0.5, 0.8, 0.98, 0.8, 0.5, 0.2]  # where each lies on the coat, from back to belly


def tilted_ring(mesh, centre, tilt, half_height, half_width, limb):
    """an octagon around a point of the animal's middle plane, leaning forward by `tilt` degrees"""
    t = math.radians(tilt)
    up = (-math.sin(t), math.cos(t))
    return [mesh.vertex(centre[0] + up[0] * half_height * b, centre[1] + up[1] * half_height * b, half_width * a, limb)
            for a, b in OCTAGON]


def fur_ring(vertices, along, top=0.0):
    """coat texture coordinates for a ring: `along` the body, and from `top` (0 = the back's dark saddle) to the belly"""
    return [(v, (along * FUR_TEXELS / FUR_SIZE[0], top + (1 - top) * c)) for v, c in zip(vertices, OCTAGON_FUR)]


def face_ring(mesh, vertices):
    out = []
    for v in vertices:
        p = mesh.points[v]
        x, y = face_at(-p.y / UNIT, p.z / UNIT)
        out.append((v, (x / FACE_SIZE[0], y / FACE_SIZE[1])))
    return out


def build_mesh():
    mesh = Mesh()

    # --- body and neck: one skin from the rump to the back of the skull
    rings = []
    for (f, top, bottom, width), limb in zip(TORSO, TORSO_LIMBS):
        ring = tilted_ring(mesh, (f, (top + bottom) / 2), 0, (top - bottom) / 2, width, limb)
        rings.append(fur_ring(ring, f))
    along = TORSO[-1][0]
    previous = (TORSO[-1][0], (TORSO[-1][1] + TORSO[-1][2]) / 2)
    skull = None
    for (centre, tilt, half_height, half_width, limb), top in zip(NECK, NECK_FUR):
        along += math.dist(centre, previous)
        previous = centre
        skull = tilted_ring(mesh, centre, tilt, half_height, half_width, limb)
        rings.append(fur_ring(skull, along, top))
    mesh.face(rings[0], FUR)  # under the tail
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, FUR)

    # --- head: skull and the upper half of the muzzle
    rings = [face_ring(mesh, skull)]
    for centre, tilt, half_height, half_width in HEAD:
        rings.append(face_ring(mesh, tilted_ring(mesh, centre, tilt, half_height, half_width, "Head")))
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, FACE)
    tip = face_ring(mesh, [mesh.vertex(NOSE_TIP[0], NOSE_TIP[1], 0, "Head")])[0]
    for i in range(8):
        mesh.face([rings[-1][i], rings[-1][(i + 1) % 8], tip], FACE)

    # --- lower jaw: a wedge under the muzzle
    rings = []
    for f, top, bottom, width in JAW:
        rings.append(face_ring(mesh, [mesh.vertex(f, top, width, "Jaw"), mesh.vertex(f, bottom, 0, "Jaw"),
                                      mesh.vertex(f, top, -width, "Jaw")]))
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, FACE)
    mesh.face(rings[-1][::-1], FACE)
    mesh.face(rings[0], FACE)

    # --- ears: open towards the front and the side
    for side, limb in ((1, "EarL"), (-1, "EarR")):
        p = {k: mesh.vertex(f, u, s * side, limb) for k, (f, u, s) in EAR.items()}
        back = [(p["outer"], (0.2, 0.52)), (p["inner"], (0.8, 0.52)), (p["tip"], (0.5, 0.03))]
        inside = [(p["front"], (0.3, 0.97)), (p["outer"], (0.7, 0.97)), (p["tip"], (0.5, 0.7))]
        front = [(p["inner"], (0.2, 0.5)), (p["front"], (0.8, 0.5)), (p["tip"], (0.5, 0.05))]
        for tri in (back, inside, front):
            mesh.face(tri if side > 0 else tri[::-1], FUR)

    # --- tail: bushy, dark towards the tip (five-sided; the coat's dark top is used for the tip)
    rings = []
    count = len(TAIL) - 1
    for i, ((f, u), radius, limb) in enumerate(TAIL):
        (pf, pu), (nf, nu) = TAIL[max(i - 1, 0)][0], TAIL[min(i + 1, count)][0]
        length = math.hypot(nf - pf, nu - pu)
        up = ((nu - pu) / length, -(nf - pf) / length)  # across the tail, on its upper side
        coat = 0.55 - 0.53 * i / count
        ring = []
        for k, (a, b) in enumerate([(0, 1), (0.95, 0.31), (0.59, -0.81), (-0.59, -0.81), (-0.95, 0.31)]):
            v = mesh.vertex(f + up[0] * radius * b, u + up[1] * radius * b, radius * a, limb)
            ring.append((v, ((0.0, 0.25, 0.5, 0.5, 0.25)[k], coat)))
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        mesh.loft(b, a, FUR)

    # --- legs: four-sided, an edge to the front
    for leg in (FRONT_LEG, HIND_LEG):
        for side, letter in ((1, "L"), (-1, "R")):
            names = {k: name % letter for k, name in zip("ABC", leg["limbs"])}
            centre = SIDE * side
            f, u, s = leg["top"]
            top = (mesh.vertex(f, u, s * side, names["A"]), (f * FUR_TEXELS / FUR_SIZE[0] / 2, 0.5))
            rings = []
            length = 0.0
            previous = (f, u)
            for f, u, depth, width, limb in leg["rings"]:
                length += math.dist((f, u), previous)
                previous = (f, u)
                along = length * FUR_TEXELS / FUR_SIZE[0]
                # front, outside, back, inside; the inside of the leg is paler
                ring = [(mesh.vertex(f + depth, u, centre, names[limb]), (along, 0.56)),
                        (mesh.vertex(f, u, centre + width * side, names[limb]), (along, 0.44)),
                        (mesh.vertex(f - depth, u, centre, names[limb]), (along, 0.6)),
                        (mesh.vertex(f, u, centre - width * side, names[limb]), (along, 0.74))]
                rings.append(ring if side > 0 else [ring[0], ring[3], ring[2], ring[1]])
            front, back, width = leg["paw"]
            along += 2.4 * FUR_TEXELS / FUR_SIZE[0]
            middle = (front + back) / 2 - 0.3
            paw = [(mesh.vertex(front, 0, centre, names["C"]), (along, 0.56)),
                   (mesh.vertex(middle, 0, centre + width * side, names["C"]), (along, 0.44)),
                   (mesh.vertex(back, 0, centre, names["C"]), (along, 0.6)),
                   (mesh.vertex(middle, 0, centre - width * side, names["C"]), (along, 0.74))]
            rings.append(paw if side > 0 else [paw[0], paw[3], paw[2], paw[1]])
            for i in range(4):
                mesh.face([top, rings[0][(i + 1) % 4], rings[0][i]], FUR)
            for a, b in zip(rings, rings[1:]):
                mesh.loft(b, a, FUR)
            mesh.face(rings[-1], FUR)  # sole
    return mesh


def make_objects(mesh, materials):
    data = bpy.data.meshes.new("JackalMesh")
    data.from_pydata([tuple(p) for p in mesh.points], [], [f[0] for f in mesh.faces])
    uv = data.uv_layers.new(name="UVMap")
    loop = 0
    for polygon, (_, uvs, material) in zip(data.polygons, mesh.faces):
        polygon.material_index = material
        polygon.use_smooth = True
        for corner in uvs:
            uv.data[loop].uv = corner
            loop += 1
    for material in materials:
        data.materials.append(material)
    data.update()
    # every face outwards, whichever way round it was listed
    bm = bmesh.new()
    bm.from_mesh(data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new("Jackal", data)
    bpy.context.collection.objects.link(obj)

    armature = bpy.data.objects.new(SKELETON, bpy.data.armatures.new("JackalArmature"))
    bpy.context.collection.objects.link(armature)
    armature.ootActorScale = MODEL_SCALE
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    lateral = Vector((1, 0, 0))
    for name, (parent, head, tail) in BONES.items():
        bone = armature.data.edit_bones.new(name)
        bone.head, bone.tail = at(*head), at(*tail)
        # every bone's own x axis is the animal's left-right axis (for the ears as nearly as can be)
        bone.align_roll(lateral.cross(bone.tail - bone.head))
        if parent:
            bone.parent = armature.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")

    for name in BONES:
        obj.vertex_groups.new(name=name)
    for i, limb in enumerate(mesh.limbs):
        obj.vertex_groups[limb].add([i], 1.0, "REPLACE")
    obj.parent = armature
    obj.modifiers.new("Armature", "ARMATURE").object = armature
    return obj, armature


# ---------------------------------------------------------------- Fast64
def load_fast64(folder):
    sys.path.insert(0, str(folder))
    import fast64_hm

    fast64_hm.register()
    scene = bpy.context.scene
    scene.gameEditorMode = "OOT"
    assert scene.fast64.oot.feature_set == "hm64", "this Fast64 does not write SoH resources"
    return fast64_hm


def make_material(fast64, name, image, clamp_s, clamp_t):
    from fast64_hm.fast64_internal.f3d.f3d_material import createF3DMat

    material = createF3DMat(None, preset="oot_shaded_texture")
    material.name = name
    f3d = material.f3d_mat
    with bpy.context.temp_override(material=material):
        f3d.tex0.tex = image
        f3d.tex0.tex_format = "RGBA16"
        f3d.tex0.S.clamp, f3d.tex0.T.clamp = clamp_s, clamp_t
        f3d.tex0.autoprop = True
    return material


def export_skeleton(armature, folder):
    """-> the resources Fast64 wrote, as {path in the game's archive: file}"""
    scene = bpy.context.scene
    settings = scene.fast64.oot.skeletonExportSettings
    settings.mode, settings.folder, settings.customPath = "Generic", OBJECT, str(folder)
    scene.saveTextures = False  # texture data goes into the resources
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    assert bpy.ops.object.oot_export_skeleton() == {"FINISHED"}, "Fast64 could not export the skeleton"
    return {path.relative_to(folder).as_posix(): path for path in Path(folder).rglob("*") if path.is_file()}


# ---------------------------------------------------------------- animation
# Poses are described the way the animal moves: where the body is, how each part is turned from its rest pose
# (degrees: pitch = nose up / a hanging part swings forward, yaw = to the left, roll = the top leans left) and where
# each paw is; the legs are then bent to reach their paws.
# leg -> (the limb it hangs from, which way it bends, how far its upper part may swing back and forward: any
# further and its top would come out of the body)
LEGS = {"FrontLegL": ("Chest", -1, (-62, 40)), "FrontLegR": ("Chest", -1, (-62, 40)),
        "HindLegL": ("Root", 1, (-38, 46)), "HindLegR": ("Root", 1, (-38, 46))}


def plane(point):
    """(f, u) of a point"""
    return (point[0], point[1])


def turned(v, degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def direction(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub2(a, b):
    return (a[0] - b[0], a[1] - b[1])


class Pose:
    def __init__(self):
        self.move = [0.0, 0.0, 0.0]  # of the whole animal: forward, up, left
        self.turn = {}  # limb -> (pitch, yaw, roll) from its rest pose, relative to its parent
        self.paws = {}  # leg -> ((f, u) of the paw relative to where it stands at rest, pitch of the paw)
        self.swing = {}  # (worked out) leg -> how far its upper part is swung from rest, against the body
        self.wanted = {}  # (worked out) leg -> (the swing it would take without limits, where its paw is)
        self.slip = 0.0  # (worked out) degrees a leg on the ground would have had to swing beyond its limits
        self.free = False  # its paws are not standing on the ground (leaping, lying): nothing to report about them

    def solve(self):
        """-> ({limb: rotation from rest, in the armature's axes}, how far out of reach the worst paw is)"""
        out = {}
        for limb, (pitch, yaw, roll) in self.turn.items():
            out[limb] = (Quaternion((0, 0, 1), math.radians(yaw)) @ Quaternion((1, 0, 0), math.radians(-pitch))
                         @ Quaternion((0, 1, 0), math.radians(roll)))
        root = add(plane(BONES["Root"][1]), self.move[:2])
        root_pitch = self.turn.get("Root", (0, 0, 0))[0]
        chest = add(root, turned(sub2(plane(BONES["Chest"][1]), plane(BONES["Root"][1])), root_pitch))
        chest_pitch = root_pitch + self.turn.get("Chest", (0, 0, 0))[0]
        frames = {"Root": (root, plane(BONES["Root"][1]), root_pitch), "Chest": (chest, plane(BONES["Chest"][1]), chest_pitch)}
        short = 0.0
        for leg, (parent, bend, swing) in LEGS.items():
            hip, knee, ankle = (plane(BONES[leg + part][1]) for part in "ABC")
            paw = (BONES[leg + "C"][2][0], 0.0)
            origin, rest_origin, parent_pitch = frames[parent]
            (offset, paw_pitch) = self.paws.get(leg, ((0.0, 0.0), 0.0))
            hip_now = add(origin, turned(sub2(hip, rest_origin), parent_pitch))
            ankle_now = sub2(add(paw, offset), turned(sub2(paw, ankle), paw_pitch))
            upper, lower = math.dist(hip, knee), math.dist(knee, ankle)
            reach = math.dist(hip_now, ankle_now)
            short = max(short, reach - (upper + lower))
            reach = min(max(reach, abs(upper - lower) * 1.02), (upper + lower) * 0.999)
            inside = math.degrees(math.acos((upper * upper + reach * reach - lower * lower) / (2 * upper * reach)))
            upper_dir = direction(hip_now, ankle_now) + bend * inside
            ankle_now = add(hip_now, turned((reach, 0.0), direction(hip_now, ankle_now)))
            # keep the upper part within its swing; the lower part then points at where the ankle should be
            rest_dir = direction(hip, knee) + parent_pitch
            limited = rest_dir + min(max((upper_dir - rest_dir + 180) % 360 - 180, swing[0]), swing[1])
            self.wanted[leg] = ((upper_dir - rest_dir + 180) % 360 - 180, offset)
            if abs(limited - upper_dir) % 360 > 0.01 and offset[1] <= 0:
                self.slip = max(self.slip, abs((limited - upper_dir + 180) % 360 - 180))
            upper_dir = limited
            knee_now = add(hip_now, turned((upper, 0.0), upper_dir))
            ankle_now = add(knee_now, turned((lower, 0.0), direction(knee_now, ankle_now)))
            pitches = [upper_dir - direction(hip, knee), direction(knee_now, ankle_now) - direction(knee, ankle), paw_pitch]
            self.swing[leg] = (pitches[0] - parent_pitch + 180) % 360 - 180
            for part, pitch, before in zip("ABC", pitches, [parent_pitch] + pitches):
                pitch = (pitch - before + 180) % 360 - 180
                out[leg + part] = Quaternion((1, 0, 0), math.radians(-pitch))
        return out, short


def wave(t, phase=0.0, cycles=1):
    return math.sin(2 * math.pi * (cycles * t - phase))


def ease(q):
    return q * q * (3 - 2 * q)


def step(pose, leg, phase, duty, reach, lift, flat, fold):
    """One leg's part in a gait. The paw is on the ground for `duty` of the cycle, moving back from reach[0] to
    reach[1] ahead of where it stands at rest, then swings forward `lift` high. On the ground the paw goes from
    flat[0] (landing) to flat[1] (pushing off) degrees; in the air it folds back by `fold` degrees."""
    phase %= 1.0
    if phase < duty:
        q = phase / duty
        pose.paws[leg] = ((reach[0] + (reach[1] - reach[0]) * q, 0.0), flat[0] + (flat[1] - flat[0]) * q)
    else:
        q = (phase - duty) / (1 - duty)
        f = reach[1] + (reach[0] - reach[1]) * ease(q)
        pitch = flat[1] + (flat[0] - flat[1]) * ease(q) - fold * math.sin(math.pi * min(1.0, q * 1.25)) ** 2
        pose.paws[leg] = ((f, lift * math.sin(math.pi * q)), pitch)


def pulse(t, at, width):
    """0..1..0 around `at`, `width` of the cycle wide"""
    q = ((t - at + 0.5) % 1.0 - 0.5) / width
    return math.cos(math.pi * q) ** 2 if abs(q) < 0.5 else 0.0


def idle_pose(t):
    """standing watch: breathing, looking around, the odd flick of an ear, the tail swaying"""
    pose = Pose()
    breath = wave(t, 0, 2)
    pose.move = [0.0, 0.12 * breath, 0.25 * wave(t, 0.1)]
    pose.turn["Root"] = (0.0, 0.0, 0.8 * wave(t, 0.1))
    pose.turn["Chest"] = (0.7 * breath, 0.0, 0.0)
    look = wave(t, 0.05) + 0.25 * wave(t, 0.3, 2)
    pose.turn["Neck"] = (2.0 * wave(t, 0.4), 7.0 * look, 0.0)
    pose.turn["Head"] = (-1.5 * breath + 2.0 * wave(t, 0.55), 11.0 * look, 3.0 * wave(t, 0.2))
    pose.turn["EarL"] = (0.0, 24.0 * pulse(t, 0.31, 0.09) - 10 * pulse(t, 0.37, 0.07), -10.0 * pulse(t, 0.31, 0.09))
    pose.turn["EarR"] = (0.0, -24.0 * pulse(t, 0.74, 0.09) + 10 * pulse(t, 0.80, 0.07), 10.0 * pulse(t, 0.74, 0.09))
    pose.turn["Jaw"] = (0.0, 0.0, 0.0)
    pose.turn["Tail1"] = (2.0 * wave(t, 0.2, 2), 7.0 * wave(t, 0.35), 0.0)
    pose.turn["Tail2"] = (3.0 * wave(t, 0.3, 2), 9.0 * wave(t, 0.5), 0.0)
    return pose


WALK_SPEED, WALK_FRAMES = 1.2, 16  # game units a frame, frames a cycle
RUN_SPEED, RUN_FRAMES = 7.0, 8


def walk_pose(t):
    """a low, prowling walk: each paw in turn (left hind, left front, right hind, right front)"""
    pose = Pose()
    duty = 0.64
    travel = WALK_SPEED * WALK_FRAMES * duty  # how far a paw is carried back while it is on the ground
    pose.move = [0.0, -1.5 + 0.22 * wave(t, 0.08, 2), 0.35 * wave(t, 0.3)]
    pose.turn["Root"] = (-1.0 + 0.8 * wave(t, 0.2, 2), 2.2 * wave(t, 0.05), 1.6 * wave(t, 0.3))
    pose.turn["Chest"] = (0.8 * wave(t, 0.45, 2), -3.6 * wave(t, 0.05), -2.4 * wave(t, 0.3))
    pose.turn["Neck"] = (-16.0 + 2.0 * wave(t, 0.3, 2), 1.4 * wave(t, 0.05), 0.0)
    pose.turn["Head"] = (13.0 - 1.5 * wave(t, 0.3, 2), 0.0, 0.8 * wave(t, 0.3))
    pose.turn["EarL"] = (4.0 + 2.0 * wave(t, 0.4, 2), 0.0, 0.0)
    pose.turn["EarR"] = (4.0 + 2.0 * wave(t, 0.4, 2), 0.0, 0.0)
    pose.turn["Jaw"] = (0.0, 0.0, 0.0)
    pose.turn["Tail1"] = (-6.0 + 2.0 * wave(t, 0.1, 2), 9.0 * wave(t, 0.2), 0.0)
    pose.turn["Tail2"] = (-4.0 + 3.0 * wave(t, 0.25, 2), 12.0 * wave(t, 0.38), 0.0)
    for leg, phase, front in (("HindLegL", 0.0, False), ("FrontLegL", 0.25, True), ("HindLegR", 0.5, False),
                              ("FrontLegR", 0.75, True)):
        if front:
            step(pose, leg, t - phase, duty, (travel * 0.52, -travel * 0.48), 2.6, (5, -16), 42)
        else:
            step(pose, leg, t - phase, duty, (travel * 0.56, -travel * 0.44), 2.2, (4, -14), 24)
    return pose


def run_pose(t):
    """a gallop: both hind paws push off, a stretched leap, both front paws land, the body gathers again"""
    pose = Pose()
    duty = 0.3
    travel = RUN_SPEED * RUN_FRAMES * duty
    stretch = wave(t, 0.2)  # +1 stretched out in the air after the hind paws push off, -1 gathered
    pose.move = [0.0, -1.2 + 1.4 * wave(t, 0.12), 0.0]
    pose.turn["Root"] = (5.0 * wave(t, 0.93), 0.0, 0.0)
    pose.turn["Chest"] = (-7.0 + 11.0 * stretch, 0.0, 0.0)
    pose.turn["Neck"] = (-24.0 - 6.0 * stretch, 0.0, 0.0)
    pose.turn["Head"] = (16.0 - 4.0 * wave(t, 0.1), 0.0, 0.0)
    pose.turn["EarL"] = (30.0 + 6.0 * wave(t, 0.3), -8.0, 0.0)
    pose.turn["EarR"] = (30.0 + 6.0 * wave(t, 0.3), 8.0, 0.0)
    pose.turn["Jaw"] = (-10.0 - 3.0 * wave(t, 0.4), 0.0, 0.0)
    pose.turn["Tail1"] = (-34.0 - 8.0 * wave(t, 0.35), 0.0, 0.0)
    pose.turn["Tail2"] = (-22.0 - 12.0 * wave(t, 0.5), 0.0, 0.0)
    for leg, phase, front in (("HindLegL", 0.0, False), ("HindLegR", 0.1, False), ("FrontLegR", 0.48, True),
                              ("FrontLegL", 0.58, True)):
        if front:
            step(pose, leg, t - phase, duty, (travel * 0.5, -travel * 0.5), 5.5, (14, -30), 62)
        else:
            step(pose, leg, t - phase, duty, (travel * 0.46, -travel * 0.54), 4.0, (10, -24), 34)
    return pose


# ---- the poses of a jackal that has noticed someone, and of a fight. These are built from a few key poses.
def key(move=(0.0, 0.0, 0.0), paws=None, free=False, **turns):
    """a pose: where the body is, the paws (front, hind or each leg by name) and how the limbs are turned"""
    pose = Pose()
    pose.move = list(move)
    pose.free = free
    pose.turn = {limb: tuple(turn) for limb, turn in turns.items()}
    for name, paw in (paws or {}).items():
        for leg in LEGS:
            if leg.startswith(name):
                pose.paws[leg] = paw
    return pose


def between(a, b, q):
    """pose a, moved q of the way to pose b"""
    def mid(x, y):
        return x + (y - x) * q

    pose = Pose()
    pose.move = [mid(x, y) for x, y in zip(a.move, b.move)]
    pose.free = a.free or b.free
    for limb in set(a.turn) | set(b.turn):
        pose.turn[limb] = tuple(mid(x, y) for x, y in zip(a.turn.get(limb, (0, 0, 0)), b.turn.get(limb, (0, 0, 0))))
    for leg in LEGS:
        (a_at, a_pitch), (b_at, b_pitch) = a.paws.get(leg, ((0.0, 0.0), 0.0)), b.paws.get(leg, ((0.0, 0.0), 0.0))
        pose.paws[leg] = ((mid(a_at[0], b_at[0]), mid(a_at[1], b_at[1])), mid(a_pitch, b_pitch))
    return pose


def keyed(keys, frame):
    """the pose on a frame of [(frame, pose, how it is reached)]: "smooth", "in" (gathering speed, as in a fall),
    "out" (losing speed) or "even"."""
    for (f0, a, _), (f1, b, how) in zip(keys, keys[1:]):
        if frame <= f1:
            q = (frame - f0) / (f1 - f0)
            return between(a, b, {"smooth": ease(q), "in": q * q, "out": 1 - (1 - q) ** 2, "even": q}[how])
    return keys[-1][1]


EARS_BACK = {"EarL": (32.0, -9.0, 0.0), "EarR": (32.0, 9.0, 0.0)}


def alert_stance():
    """it has seen someone: drawn up, head high, ears pricked, tail held out"""
    return key(move=(-0.5, 0.1, 0.0), Root=(1.0, 0, 0), Chest=(2.0, 0, 0), Neck=(13.0, 0, 0), Head=(-9.0, 0, 0),
               EarL=(-9.0, 5.0, 0), EarR=(-9.0, -5.0, 0), Jaw=(0, 0, 0), Tail1=(-16.0, 0, 0), Tail2=(-12.0, 0, 0))


def alert_pose(t):
    """staring, all but still: only its breath and the end of its tail move"""
    pose = alert_stance()
    breath = wave(t, 0, 2)
    pose.move[1] += 0.08 * breath
    pose.turn["Chest"] = (2.0 + 0.6 * breath, 0.0, 0.0)
    pose.turn["Head"] = (-9.0 - 0.5 * breath, 0.0, 0.0)
    pose.turn["Tail2"] = (-12.0 + 1.5 * wave(t, 0.2, 2), 2.5 * wave(t, 0.1), 0.0)
    return pose


HOWL_FRAMES = 40


def howl_pose(t):
    """calling the others: the head thrown back, the muzzle to the sky"""
    def call(sway):
        return key(move=(-1.2, -0.4, 0.0), Root=(3.0, 0, 0), Chest=(5.0, 0, 0), Neck=(20.0 + sway, 0, 0),
                   Head=(36.0 + sway, 0, 0), Jaw=(-20.0 - 2 * sway, 0, 0), Tail1=(-8.0, 0, 0), Tail2=(-6.0, 0, 0),
                   **EARS_BACK)

    gather = key(move=(-0.8, -0.8, 0.0), Root=(1.0, 0, 0), Chest=(0.0, 0, 0), Neck=(-6.0, 0, 0), Head=(4.0, 0, 0),
                 Jaw=(-3.0, 0, 0), Tail1=(-12.0, 0, 0), Tail2=(-10.0, 0, 0), EarL=(10.0, 0, 0), EarR=(10.0, 0, 0))
    last = HOWL_FRAMES - 1
    return keyed([(0, alert_stance(), "even"), (4, gather, "smooth"), (10, call(-2.0), "smooth"),
                  (20, call(2.0), "smooth"), (31, call(-1.0), "smooth"), (last, alert_stance(), "smooth")], t * last)


def threat_stance():
    """about to attack: crouched over its front paws, head low, ears flat, teeth bared, tail stiff"""
    return key(move=(1.0, -2.6, 0.0), paws={"Front": ((2.0, 0.0), 0.0), "Hind": ((-1.0, 0.0), 0.0)},
               Root=(-5.0, 0, 0), Chest=(-3.0, 0, 0), Neck=(-30.0, 0, 0), Head=(24.0, 0, 0), Jaw=(-13.0, 0, 0),
               Tail1=(-30.0, 0, 0), Tail2=(-16.0, 0, 0), **EARS_BACK)


def threat_pose(t):
    """snarling: its jaw trembles, its sides heave, its tail jerks"""
    pose = threat_stance()
    breath, shake = wave(t, 0, 2), wave(t, 0, 5)
    pose.move = [1.0 + 0.25 * breath, -2.6 + 0.15 * breath, 0.3 * wave(t, 0.2)]
    pose.turn["Chest"] = (-3.0 + 0.8 * breath, 0.0, -1.2 * wave(t, 0.2))
    pose.turn["Head"] = (24.0 + 0.8 * shake, 0.0, 1.5 * wave(t, 0.45))
    pose.turn["Jaw"] = (-13.0 - 2.5 * shake, 0.0, 0.0)
    pose.turn["Tail1"] = (-30.0, 4.0 * wave(t, 0.1, 2), 0.0)
    pose.turn["Tail2"] = (-16.0, 6.0 * wave(t, 0.25, 2), 0.0)
    return pose


# The actor moves the jackal through its leap (EnJackal.cpp): it crouches until ATTACK_LEAP, is in the air until
# about ATTACK_LAND and bites as it lands.
ATTACK_FRAMES, ATTACK_LEAP, ATTACK_LAND = 17, 5, 10


def attack_pose(t):
    """a leap at its prey: crouching back, flying with jaws wide, snapping as it lands"""
    crouch = key(move=(-3.0, -4.6, 0.0), paws={"Front": ((1.5, 0.0), 0.0), "Hind": ((2.5, 0.0), 0.0)},
                 Root=(6.0, 0, 0), Chest=(-9.0, 0, 0), Neck=(-27.0, 0, 0), Head=(23.0, 0, 0), Jaw=(-4.0, 0, 0),
                 Tail1=(-12.0, 0, 0), Tail2=(-6.0, 0, 0), **EARS_BACK)
    flight = key(move=(4.0, 1.5, 0.0), free=True,
                 paws={"Front": ((9.0, 5.0), -36.0), "Hind": ((-8.0, 3.0), -32.0)},
                 Root=(5.0, 0, 0), Chest=(2.0, 0, 0), Neck=(-18.0, 0, 0), Head=(10.0, 0, 0), Jaw=(-38.0, 0, 0),
                 Tail1=(-38.0, 0, 0), Tail2=(-24.0, 0, 0), **EARS_BACK)
    bite = key(move=(3.0, -3.2, 0.0), free=True, paws={"Front": ((3.5, 0.0), 6.0), "Hind": ((-3.5, 1.6), -22.0)},
               Root=(-8.0, 0, 0), Chest=(-4.0, 0, 0), Neck=(-31.0, 0, 0), Head=(30.0, 0, 0), Jaw=(-5.0, 0, 0),
               Tail1=(-24.0, 0, 0), Tail2=(-20.0, 0, 0), **EARS_BACK)
    landed = key(move=(1.6, -3.4, 0.0), paws={"Front": ((2.5, 0.0), 0.0), "Hind": ((-1.0, 0.0), 0.0)},
                 Root=(-6.0, 0, 0), Chest=(-3.0, 0, 0), Neck=(-33.0, 0, 0), Head=(26.0, 0, 0), Jaw=(0.0, 0, 0),
                 Tail1=(-26.0, 0, 0), Tail2=(-12.0, 0, 0), **EARS_BACK)
    return keyed([(0, threat_stance(), "even"), (ATTACK_LEAP - 1, crouch, "out"), (ATTACK_LEAP + 3, flight, "out"),
                  (ATTACK_LAND, bite, "in"), (ATTACK_LAND + 2, landed, "smooth"),
                  (ATTACK_FRAMES - 1, threat_stance(), "smooth")], t * (ATTACK_FRAMES - 1))


HIT_FRAMES = 10


def hit_pose(t):
    """struck: its head is thrown up and aside, it staggers back and finds its feet again"""
    recoil = key(move=(-2.5, -1.0, 1.0),
                 paws={"FrontLegL": ((-1.0, 2.6), -22.0), "FrontLegR": ((-1.5, 0.0), 0.0), "Hind": ((1.5, 0.0), 0.0)},
                 Root=(3.0, 0, 7.0), Chest=(2.0, -10.0, 0), Neck=(6.0, -24.0, 0), Head=(4.0, -16.0, 16.0),
                 Jaw=(-26.0, 0, 0), Tail1=(18.0, 0, 0), Tail2=(14.0, 0, 0), **EARS_BACK)
    stagger = key(move=(-3.5, -3.2, -0.6), paws={"Front": ((-2.5, 0.0), 0.0), "Hind": ((0.5, 0.0), 0.0)},
                  Root=(2.0, 0, -4.0), Chest=(-2.0, 4.0, 0), Neck=(-12.0, 6.0, 0), Head=(8.0, 4.0, -6.0),
                  Jaw=(-10.0, 0, 0), Tail1=(8.0, 0, 0), Tail2=(6.0, 0, 0), **EARS_BACK)
    return keyed([(0, threat_stance(), "even"), (2, recoil, "out"), (5, stagger, "smooth"),
                  (HIT_FRAMES - 1, threat_stance(), "smooth")], t * (HIT_FRAMES - 1))


DIE_FRAMES = 28
LYING = 4.6 - BONES["Root"][1][1]  # how far its body comes down to lie on its side


def die_pose(t):
    """thrown back by the last blow, its legs give way and it falls on its left side and lies still"""
    def lying(drop, roll, fold, jaw, neck):
        # (its legs go with its body: the paws are given from where the body is)
        f, u = -3.5, LYING + drop
        return key(move=(f, u, 9.0), free=True,
                   paws={"Front": ((f + 3.0 + fold, u + 2.0 + fold), -24.0), "Hind": ((f - 3.0, u + 2.0 + fold), -28.0)},
                   Root=(0.0, 3.0, roll), Chest=(0.0, -4.0, 2.0), Neck=(neck, 4.0, 0), Head=(6.0, 0, 0),
                   Jaw=(jaw, 0, 0), Tail1=(14.0, 0, 0), Tail2=(12.0, 0, 0), EarL=(20.0, 0, 0), EarR=(20.0, 0, 0))

    thrown = key(move=(-2.0, 0.1, 0.5), paws={"Front": ((-1.0, 5.0), -30.0), "Hind": ((1.5, 0.0), 0.0)},
                 Root=(10.0, 0, 6.0), Chest=(10.0, 0, 0), Neck=(24.0, 10.0, 0), Head=(14.0, 10.0, 10.0),
                 Jaw=(-32.0, 0, 0), Tail1=(-10.0, 0, 0), Tail2=(-4.0, 0, 0), **EARS_BACK)
    giving = key(move=(-3.0, -6.5, 3.5), free=True, paws={"Front": ((-2.0, -3.0), -16.0), "Hind": ((0.0, -2.5), -12.0)},
                 Root=(1.0, 0, 38.0), Chest=(-4.0, 0, 6.0), Neck=(8.0, 12.0, 0), Head=(4.0, 8.0, 8.0),
                 Jaw=(-22.0, 0, 0), Tail1=(4.0, 0, 0), Tail2=(6.0, 0, 0), **EARS_BACK)
    last = DIE_FRAMES - 1
    return keyed([(0, threat_stance(), "even"), (4, thrown, "out"), (9, giving, "in"),
                  (13, lying(0.0, 86.0, 0.0, -16.0, -10.0), "in"), (16, lying(1.3, 81.0, 1.0, -20.0, -4.0), "out"),
                  (19, lying(0.0, 87.0, 0.0, -12.0, -16.0), "in"), (last, lying(0.0, 88.0, 1.2, -9.0, -20.0), "smooth")],
                 t * last)


# animation -> (frames, pose at 0..1, whether it goes round: the others play once, from the first frame to the last)
ANIMATIONS = {"Idle": (60, idle_pose, True), "Walk": (WALK_FRAMES, walk_pose, True), "Run": (RUN_FRAMES, run_pose, True),
              "Alert": (40, alert_pose, True), "Howl": (HOWL_FRAMES, howl_pose, False),
              "Threat": (20, threat_pose, True), "Attack": (ATTACK_FRAMES, attack_pose, False),
              "Hit": (HIT_FRAMES, hit_pose, False), "Die": (DIE_FRAMES, die_pose, False)}


def make_actions(armature):
    """keys every limb on every frame of each animation; -> {name: action}"""
    rest = {bone.name: bone.matrix_local.to_quaternion() for bone in armature.data.bones}
    armature.animation_data_create()
    actions = {}
    for name, (frames, pose_at, loops) in ANIMATIONS.items():
        action = bpy.data.actions.new(name)
        action.use_fake_user = True
        armature.animation_data.action = action
        before = {}
        worst = 0.0
        swings = {}
        for frame in range(frames):
            pose = pose_at(frame / frames if loops else frame / (frames - 1))
            turns, short = pose.solve()
            if not pose.free:
                worst = max(worst, short, pose.slip)
            if os.environ.get("JACKAL_REPORT") == name:  # for tuning a gait: every leg on every frame
                print(f"jackal: {frame:2}: " + "  ".join(
                    f"{leg[:1]}{leg[-1]} {'ground' if o[1] <= 0 else 'air   '} f{o[0]:6.1f} swing {w:5.0f}" for leg, (w, o) in pose.wanted.items()))
            for leg, swing in pose.swing.items():
                low, high = swings.get(leg[:-1], (0.0, 0.0))
                swings[leg[:-1]] = (min(low, swing), max(high, swing))
            for bone in armature.pose.bones:
                turn = turns.get(bone.name, Quaternion())
                # the same turn, in the bone's own axes
                q = rest[bone.name].inverted() @ turn @ rest[bone.name]
                if bone.name in before and before[bone.name].dot(q) < 0:
                    q.negate()
                before[bone.name] = q
                bone.rotation_mode = "QUATERNION"
                bone.rotation_quaternion = q
                bone.keyframe_insert("rotation_quaternion", frame=frame)
                if bone.parent is None:
                    f, u, s = pose.move
                    bone.location = rest[bone.name].inverted() @ (Vector((s, -f, u)) * UNIT)
                    bone.keyframe_insert("location", frame=frame)
        if worst > 0.1:
            print(f"jackal: {name}: a paw on the ground is {worst:.2f} out of its leg's reach")
        # (the tops of the legs come out of the body if they swing too far)
        print(f"jackal: {name}: " + ", ".join(f"{leg} swings {low:.0f} to {high:.0f}" for leg, (low, high) in swings.items()))
        actions[name] = action
    return actions


def show(armature, action, frame=0):
    armature.animation_data.action = action
    if armature.animation_data.action_slot is None:
        armature.animation_data.action_slot = action.slots[0]
    bpy.context.scene.frame_set(frame)


RESOURCE_HEADER = struct.pack("<I4sIQ", 0, b"MNAO", 0, 0xDEADBEEFDEADBEEF) + bytes(64 - 20)


def export_animation(armature, action):
    """-> the animation as an SoH resource. Fast64 works the limb rotations out (it only writes them as C)."""
    from fast64_hm.fast64_internal.z64.exporter.animation import ootExportNonLinkAnimation
    from fast64_hm.fast64_internal.z64.utility import getOOTScale

    show(armature, action)
    scale = Matrix.Scale(getOOTScale(armature.ootActorScale), 4) @ Matrix.Diagonal(armature.scale).to_4x4()
    anim = ootExportNonLinkAnimation(armature, scale, SKELETON, action.name)
    frames = anim.frameCount
    channels = []  # translation x y z, then each limb's rotation x y z: a value per frame
    for joint in range(-1, len(BONES)):
        for start in anim.indices[joint]:
            constant = start < anim.limit
            channels.append([anim.values[start if constant else start + frame] & 0xFFFF for frame in range(frames)])

    def near(a, b):
        return abs((a - b + 0x8000) % 0x10000 - 0x8000)

    # the same rotation can be written two ways: keep to the one nearest the frame before, so that the game's
    # blending from frame to frame (and between animations) never goes the long way round
    for joint in range(1, len(channels) // 3):
        x, y, z = channels[joint * 3 : joint * 3 + 3]
        for frame in range(1, frames):
            other = ((x[frame] + 0x8000) & 0xFFFF, (0x8000 - y[frame]) & 0xFFFF, (z[frame] + 0x8000) & 0xFFFF)
            same = (x[frame], y[frame], z[frame])
            before = (x[frame - 1], y[frame - 1], z[frame - 1])
            if sum(near(a, b) for a, b in zip(other, before)) < sum(near(a, b) for a, b in zip(same, before)):
                x[frame], y[frame], z[frame] = other
    still = sorted({channel[0] for channel in channels if len(set(channel)) == 1})
    values, indices = list(still), []
    for channel in channels:
        if len(set(channel)) == 1:
            indices.append(still.index(channel[0]))
        else:
            indices.append(len(values))
            values += channel
    body = struct.pack("<Ih", 0, frames)
    body += struct.pack("<I", len(values)) + struct.pack(f"<{len(values)}H", *values)
    body += struct.pack("<I", len(indices) // 3) + struct.pack(f"<{len(indices)}H", *indices)
    body += struct.pack("<h", len(still))
    return RESOURCE_HEADER + body


# ---------------------------------------------------------------- preview
def render_previews(folder, views, columns=2):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in {e.identifier for e in type(scene.render).bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = 560, 420
    scene.render.film_transparent = False
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.5, 0.75, 1)
    camera = bpy.data.objects.new("PreviewCamera", bpy.data.cameras.new("PreviewCamera"))
    bpy.context.collection.objects.link(camera)
    scene.camera = camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 7.6
    ground = bpy.data.objects.new("PreviewGround", bpy.data.meshes.new("PreviewGround"))
    ground.data.from_pydata([(-9, -9, 0), (9, -9, 0), (9, 9, 0), (-9, 9, 0)], [], [(0, 1, 2, 3)])
    bpy.context.collection.objects.link(ground)
    sun = bpy.data.objects.new("PreviewSun", bpy.data.lights.new("PreviewSun", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = (math.radians(50), 0, math.radians(-40))
    bpy.context.collection.objects.link(sun)
    target = Vector((0, -0.4, 2.0))
    sheets = {}
    for name, azimuth, elevation, frame in views:
        distance = 11
        scene.frame_set(frame)
        a, e = math.radians(azimuth), math.radians(elevation)
        camera.location = target + distance * Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
        camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(Path(folder) / name)
        bpy.ops.render.render(write_still=True)
        sheets.setdefault(name.split("_")[0], []).append(scene.render.filepath + ".png")
    for helper in (camera, sun, ground):
        bpy.data.objects.remove(helper)
    # contact sheets: the pictures whose names start alike, side by side in rows of `columns`
    import numpy as np

    w, h = scene.render.resolution_x, scene.render.resolution_y
    for name, files in sheets.items():
        if len(files) < 2:
            continue
        rows = (len(files) + columns - 1) // columns
        sheet = np.zeros((rows * h, columns * w, 4), np.float32)
        for i, path in enumerate(files):
            image = bpy.data.images.load(path)
            pixels = np.empty(w * h * 4, np.float32)
            image.pixels.foreach_get(pixels)
            row, column = rows - 1 - i // columns, i % columns
            sheet[row * h : (row + 1) * h, column * w : (column + 1) * w] = pixels.reshape(h, w, 4)
            bpy.data.images.remove(image)
            os.remove(path)
        out = bpy.data.images.new("sheet", columns * w, rows * h, alpha=True)
        out.pixels.foreach_set(sheet.ravel())
        out.filepath_raw, out.file_format = str(Path(folder) / (name + ".png")), "PNG"
        out.save()
        bpy.data.images.remove(out)


# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fast64", type=Path, default=Path.home() / "oot-fast64" / "addons",
                    help="folder that contains the HarbourMasters Fast64 fork as `fast64_hm`")
    ap.add_argument("--out", type=Path, default=REPO / "x64" / "Debug" / "mods" / "jackal.o2r")
    ap.add_argument("--blend", type=Path, help="also save the Blender file here")
    ap.add_argument("--preview", type=Path, help="render pictures of the model into this folder")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args(argv)

    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.cameras, bpy.data.lights):
        for item in list(collection):
            collection.remove(item)
    fast64 = load_fast64(args.fast64)

    fur = make_material(fast64, "JackalFur", make_image("gJackalFurTex", FUR_SIZE, fur_pixel), False, True)
    face = make_material(fast64, "JackalFace", make_image("gJackalFaceTex", FACE_SIZE, face_pixel), True, True)
    mesh = build_mesh()
    obj, armature = make_objects(mesh, [fur, face])
    triangles = sum(len(f[0]) - 2 for f in mesh.faces)
    print(f"jackal: {len(mesh.points)} vertices, {triangles} triangles, {len(BONES)} limbs")

    actions = make_actions(armature)

    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        show(armature, actions["Idle"])
        render_previews(args.preview, [("model_side", 90, 4, 0), ("model_front", 25, 12, 0), ("model_back", 150, 25, 0),
                                       ("model_head", 0, 5, 0)])
        for name, action in actions.items():
            frames = ANIMATIONS[name][0]
            show(armature, action)
            render_previews(args.preview, [(f"{name.lower()}_{frame:02}", 90, 4, frame)
                                           for frame in range(0, frames, max(1, frames // 8))], columns=4)
        show(armature, actions["Run"])
        render_previews(args.preview, [(f"runfront_{frame:02}", 30, 14, frame) for frame in range(0, 8, 2)], columns=4)
        show(armature, actions["Die"])
        render_previews(args.preview, [(f"diefront_{frame:02}", 30, 14, frame) for frame in (4, 9, 13, 27)], columns=4)
    show(armature, actions["Idle"])
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend))
    if args.no_export:
        return

    with tempfile.TemporaryDirectory() as folder:
        files = {path: file.read_bytes() for path, file in export_skeleton(armature, Path(folder)).items()}
    assert sum("SkelLimb_" in path for path in files) == len(BONES), "the skeleton lost limbs"
    for name, action in actions.items():
        files[f"{OBJECT}/gJackal{name}Anim"] = export_animation(armature, action)
    show(armature, actions["Idle"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    print(f"{args.out}: {len(files)} resources")


main()
