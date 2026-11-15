"""Builds the camel of the Play as Nabooru mod, Nabooru's mount: model, skeleton and animations, made in Blender and
exported with Fast64 into a mod archive for SoH's `mods` folder. It also makes Nabooru's own animations for climbing
on, riding and getting off (see camel_rider.py), posed against the camel in the same Blender scene.

Everything about the camel is original and described right here (shape, textures, rig, animations), so the model can
be rebuilt and adjusted by changing numbers; the .blend this writes is for looking at it and for hand-editing.

Run by Blender (5.x), with the HarbourMasters fork of Fast64 (github.com/HarbourMasters/fast64: the upstream addon only
writes C), which does not have to be installed, only present on disk:

  blender --background --factory-startup --python build_camel.py -- [--fast64 DIR containing fast64_hm]
          [--game oot.o2r] [--nabooru play_as_nabooru.o2r] [--no-rider]
          [--out x64/Debug/mods/camel.o2r] [--blend FILE] [--preview DIR] [--no-export]

--preview renders contact sheets of the model and of every animation; set the environment variable CREATURE_REPORT
to an animation's name (Idle, Walk, Pace, ...) to print what each leg does on each of its frames.

In the style of the N64 game: about 650 triangles, one flex skeleton of 26 limbs with every vertex on a single limb,
three small 16-bit textures (coat 32x64, face 64x32, saddle cloth 32x32, each within the console's 4 KB texture
memory), lit and fogged like the game's own creatures. The game plays these animations in place of Epona's
(z_en_horse.c): those that Link's hidden riding animation is stepped along with must not be longer than hers.

Numbers below are in the camel's own units, SIZE game units each (Nabooru is about 60 game units tall); x is forward
here ("f"), up is "u", "s" is to the animal's left.
"""
import argparse
import math
import sys
import tempfile
import zipfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_creature as bc  # noqa: E402
from blender_creature import Foot, Pose, ease, keyed, mix, noise, pulse, wave  # noqa: E402

REPO = Path(__file__).resolve().parents[5]
OBJECT = "objects/object_camel"  # where the game finds the camel's resources
SKELETON = "gCamelSkel"
SIZE = 0.9  # game units per unit of the numbers below

# ---------------------------------------------------------------- shape
SIDE = 8.0  # how far the legs stand from the middle

# body, back to front: (f, top, bottom, half width); the first is closed off under the tail. A camel's: a deep chest,
# the belly tucked up towards the hind legs, the rump sloping down to a low tail.
TORSO = [(-37.0, 63.0, 51.0, 5.5), (-30.0, 72.0, 49.0, 11.5), (-12.0, 76.0, 44.0, 13.5), (10.0, 75.0, 37.0, 13.0),
         (23.0, 72.0, 40.0, 10.0)]
# chest front, the long neck, back of the skull: (f, u) of the middle, tilt of the ring in degrees, half height, half
# width, limb, how far down the coat texture the top of the ring starts (the dark wool of its back thins out up the
# neck). The neck leaves the chest low, dips, and rises straight up to a head carried high.
NECK = [((27.0, 54.0), 5, 11.5, 7.5, "Root", 0.0), ((36.0, 50.5), 8, 8.2, 5.8, "Neck1", 0.05),
        ((44.5, 53.5), 45, 6.2, 4.8, "Neck1", 0.08), ((49.5, 65.0), 80, 5.2, 4.2, "Neck2", 0.1),
        ((50.5, 79.0), 92, 4.5, 3.8, "Neck2", 0.12), ((50.0, 92.0), 85, 4.3, 3.7, "Head", 0.16)]
# head, its nose a little in the air: crown, eyes, then the muzzle, whose lower part is the jaw's (MUZZLE rings)
HEAD = [((53.5, 96.8), 40, 5.2, 4.4), ((58.5, 97.4), 12, 4.6, 3.8)]
MUZZLE = [((64.0, 97.6), 4, 3.7, 3.1), ((68.5, 97.6), -2, 3.2, 2.8)]
LIPS = -0.3  # where the mouth runs on a muzzle ring, between its middle (0) and its underside (-1)
NOSE_TIP, CHIN_TIP = (70.8, 96.95), (70.4, 96.45)
# ears (left): three points at the base and the tip, as (f, u, s)
EAR = {"front": (52.5, 100.4, 3.0), "outer": (50.2, 99.2, 4.2), "inner": (49.8, 100.8, 2.4), "tip": (48.6, 104.6, 5.2)}
# tail: (f, u) of the middle and radius; the last is the tip of its tuft
TAIL = [((-37.0, 61.0), 1.3, "Tail1"), ((-39.5, 54.0), 1.3, "Tail1"), ((-40.5, 46.0), 1.1, "Tail2"),
        ((-41.0, 39.0), 1.8, "Tail2"), ((-41.0, 34.5), 0.0, "Tail2")]
# legs (left): a point inside the body, then rings (f, u, half depth, half width, limb) down to the pad of the foot:
# thin, with knobs for knees and hocks, on broad pads
FRONT_LEG = {"top": (18.0, 62.0, SIDE),
             "rings": [(17.5, 54.0, 5.2, 3.8, "A"), (16.8, 44.0, 3.4, 2.9, "B"), (17.1, 31.5, 2.4, 2.2, "B"),
                       (17.3, 27.0, 3.3, 2.9, "B"), (17.5, 22.5, 2.2, 2.0, "C"), (17.6, 6.5, 2.0, 1.9, "C"),
                       (18.8, 3.2, 4.4, 3.9, "D")],
             "sole": (24.6, 13.8, 4.2), "limbs": "FrontLeg%s%s"}
HIND_LEG = {"top": (-28.0, 64.0, SIDE),
            "rings": [(-27.0, 57.0, 7.0, 4.2, "A"), (-22.5, 46.0, 4.6, 3.3, "A"), (-24.0, 40.0, 3.4, 2.7, "B"),
                      (-30.5, 29.5, 2.3, 2.1, "B"), (-32.4, 25.5, 3.0, 2.6, "B"), (-31.5, 21.0, 2.1, 1.9, "C"),
                      (-30.5, 6.5, 2.0, 1.9, "C"), (-29.2, 3.2, 4.3, 3.8, "D")],
            "sole": (-23.6, -34.0, 4.1), "limbs": "HindLeg%s%s"}
# the hump: (f, height of its top) along the back; the first and last lie on the back itself
HUMP = [(-24.0, None), (-18.0, 85.0), (-12.0, 91.5), (-6.0, 94.0), (0.0, 91.5), (6.0, 85.0), (11.0, None)]
# the saddle: a rug over the top of the hump, hanging down the flanks to its fringe, between these places along it
SADDLE = [-13.0, -9.5, -6.0, -2.5, 1.0]
# its two horns, front and back: (f, u) of the foot and of the top, half thickness at each
HORNS = [((2.6, 88.6), (4.2, 98.5), 1.4, 0.9), ((-14.6, 88.6), (-16.2, 98.0), 1.4, 0.9)]
# where the rider sits: the middle of the seat (the game and camel_rider.py place Nabooru by this)
SEAT = (-6.0, 94.7)

# skeleton: limb -> (parent, joint (f, u, s), where its bone points). Limbs come out in the game in LIMB_ORDER (every
# limb's children sorted by name), which the enum in Camel.cpp repeats.
BONES = {
    "Root": (None, (-28.0, 62.0, 0), (10.0, 62.0, 0)),
    "Neck1": ("Root", (26.0, 54.0, 0), (45.0, 53.0, 0)),
    "Neck2": ("Neck1", (46.0, 55.0, 0), (50.0, 91.0, 0)),
    "Head": ("Neck2", (50.0, 91.5, 0), (63.0, 96.0, 0)),
    "EarL": ("Head", (50.8, 100.2, 3.2), (48.6, 104.6, 5.2)),
    "EarR": ("Head", (50.8, 100.2, -3.2), (48.6, 104.6, -5.2)),
    "Jaw": ("Head", (58.5, 94.4, 0), (69.5, 95.2, 0)),
    "FrontLegLA": ("Root", (19.0, 58.0, SIDE), (16.5, 46.0, SIDE)),
    "FrontLegLB": ("FrontLegLA", (16.5, 46.0, SIDE), (17.3, 27.0, SIDE)),
    "FrontLegLC": ("FrontLegLB", (17.3, 27.0, SIDE), (17.6, 6.5, SIDE)),
    "FrontLegLD": ("FrontLegLC", (17.6, 6.5, SIDE), (21.0, 1.0, SIDE)),
    "FrontLegRA": ("Root", (19.0, 58.0, -SIDE), (16.5, 46.0, -SIDE)),
    "FrontLegRB": ("FrontLegRA", (16.5, 46.0, -SIDE), (17.3, 27.0, -SIDE)),
    "FrontLegRC": ("FrontLegRB", (17.3, 27.0, -SIDE), (17.6, 6.5, -SIDE)),
    "FrontLegRD": ("FrontLegRC", (17.6, 6.5, -SIDE), (21.0, 1.0, -SIDE)),
    "HindLegLA": ("Root", (-28.0, 64.0, SIDE), (-21.0, 45.0, SIDE)),
    "HindLegLB": ("HindLegLA", (-21.0, 45.0, SIDE), (-32.0, 26.0, SIDE)),
    "HindLegLC": ("HindLegLB", (-32.0, 26.0, SIDE), (-30.5, 6.5, SIDE)),
    "HindLegLD": ("HindLegLC", (-30.5, 6.5, SIDE), (-27.0, 1.0, SIDE)),
    "HindLegRA": ("Root", (-28.0, 64.0, -SIDE), (-21.0, 45.0, -SIDE)),
    "HindLegRB": ("HindLegRA", (-21.0, 45.0, -SIDE), (-32.0, 26.0, -SIDE)),
    "HindLegRC": ("HindLegRB", (-32.0, 26.0, -SIDE), (-30.5, 6.5, -SIDE)),
    "HindLegRD": ("HindLegRC", (-30.5, 6.5, -SIDE), (-27.0, 1.0, -SIDE)),
    # no geometry: this limb stands where the model's own axes are while the camel is at rest, and is carried along
    # by its body. The game takes the rider's place from it (a bone pointing up has the game's model axes).
    "Seat": ("Root", (0.0, 0.0, 0), (0.0, 10.0, 0)),
    "Tail1": ("Root", (-37.0, 61.5, 0), (-40.2, 50.0, 0)),
    "Tail2": ("Tail1", (-40.2, 50.0, 0), (-41.0, 34.5, 0)),
}
LIMB_ORDER = ["Root", "FrontLegLA", "FrontLegLB", "FrontLegLC", "FrontLegLD", "FrontLegRA", "FrontLegRB", "FrontLegRC",
              "FrontLegRD", "HindLegLA", "HindLegLB", "HindLegLC", "HindLegLD", "HindLegRA", "HindLegRB", "HindLegRC",
              "HindLegRD", "Neck1", "Neck2", "Head", "EarL", "EarR", "Jaw", "Seat", "Tail1", "Tail2"]
# leg -> (the limb it hangs from, which way it bends, how far its upper part may swing back and forward, its limbs)
LEGS = {"FrontLegL": ("Root", -1, (-70, 45), tuple("FrontLegL" + part for part in "ABCD")),
        "FrontLegR": ("Root", -1, (-70, 45), tuple("FrontLegR" + part for part in "ABCD")),
        "HindLegL": ("Root", 1, (-40, 50), tuple("HindLegL" + part for part in "ABCD")),
        "HindLegR": ("Root", 1, (-40, 50), tuple("HindLegR" + part for part in "ABCD"))}

RIG = bc.Rig("Camel", SKELETON, OBJECT, BONES, LEGS, size=SIZE)

# ---------------------------------------------------------------- textures
COAT_SIZE = (32, 64)
FACE_SIZE = (64, 32)
CLOTH_SIZE = (32, 32)
COAT_TEXELS = 0.8  # coat texture pixels per unit, along the body
# face texture: the head seen from the side, from (f, u) top left to bottom right
FACE_FROM, FACE_TO = (45.0, 104.0), (72.0, 90.0)
EYE = (58.8, 99.3)  # middle of the eye
NOSTRIL = (69.0, 98.7)
HALTER = (64.3, 96.8)  # its ring, on the cheek

BLACK, UMBER, BROWN = (34, 26, 22), (92, 64, 40), (140, 100, 60)
MOUTH = (70, 26, 30)  # the inside of its mouth
MOUTH_AT = (1.5, 1.5)  # the corner of the face texture kept for it
TAN, SAND, GOLD = (188, 146, 92), (208, 170, 114), (222, 188, 128)
CREAM, PALE = (234, 212, 166), (244, 230, 196)
# the saddle's colours are the Gerudo's
RED, DARK_RED, PURPLE, BRASS, LEATHER = (190, 44, 40), (128, 26, 34), (96, 52, 132), (236, 190, 70), (104, 62, 36)


def coat_pixel(x, y):
    """the coat from the back (top of the texture) to the belly: dark wool along the back, sandy flanks, a pale
    underside. Repeats sideways; the hairs run that way."""
    streak = noise(x // 3 + (y % 2), y, 1)  # short hairs, three pixels long
    fine = noise(x, y, 2)
    wool_end = 9 + 3.0 * noise(x // 3, 7) + 2.0 * noise(x, 8)  # ragged edge
    belly_start = 47 + 3.0 * noise(x // 4, 9) + 2.0 * noise(x, 10)
    if y < wool_end:
        tone = UMBER if streak < 0.45 else (BROWN if streak < 0.85 else TAN)
        tone = mix(tone, BLACK, 0.35 * fine if y < 4 else 0.15 * fine)
        return mix(tone, TAN, max(0.0, (y - wool_end + 4) / 4) * 0.7)
    if y < belly_start:
        tone = mix(SAND, TAN, streak * 0.8) if streak < 0.72 else mix(SAND, GOLD, 0.7)
        tone = mix(tone, BROWN, 0.45) if (y < wool_end + 5 and fine < 0.3) else tone  # stray dark hairs
        return mix(tone, CREAM, max(0.0, (y - belly_start + 7) / 7) * 0.7)
    return mix(CREAM, PALE, streak) if fine > 0.15 else mix(CREAM, SAND, 0.5)


def face_at(f, u):
    """face texture pixel position of a point of the head"""
    return ((f - FACE_FROM[0]) / (FACE_TO[0] - FACE_FROM[0]) * FACE_SIZE[0],
            (u - FACE_FROM[1]) / (FACE_TO[1] - FACE_FROM[1]) * FACE_SIZE[1])


def face_pixel(x, y):
    """the head from the side (both sides use it): sandy, a woolly crown, a pale soft muzzle with a slit of a nostril,
    a big dark heavy-lidded eye with long lashes, and a red Gerudo halter with a brass ring. (The mouth is not painted:
    it is where the jaw meets the head, see build_mesh.)"""
    if x < 3 and y < 3:
        return MOUTH
    f = FACE_FROM[0] + (x + 0.5) / FACE_SIZE[0] * (FACE_TO[0] - FACE_FROM[0])
    u = FACE_FROM[1] + (y + 0.5) / FACE_SIZE[1] * (FACE_TO[1] - FACE_FROM[1])
    streak, fine = noise(x // 2, y, 11), noise(x, y, 12)
    tone = mix(SAND, TAN, streak * 0.8) if streak < 0.75 else mix(SAND, GOLD, 0.6)
    # the crown and the back of the head take up the neck's dark wool
    crown = 100.7 - 0.05 * (f - 50.0) - (max(0.0, f - 56.5)) * 0.9
    if u > crown + 0.5 * (noise(x, 13) - 0.5) and f < 59.5:
        tone = mix(UMBER, BROWN, streak)
    if f < 49.5:
        tone = mix(tone, BROWN, 0.5 * min(1.0, (49.5 - f) / 2.0))
    # pale muzzle, lips and chin
    if f > 62.5:
        tone = mix(tone, mix(CREAM, PALE, streak), min(1.0, (f - 62.5) / 3.0) * 0.85)
    if u < 95.6 and f > 58.0:
        tone = mix(tone, CREAM, 0.5)
    # nostril: a slanted slit
    nx, ny = f - NOSTRIL[0], u - NOSTRIL[1]
    if abs(ny - 0.45 * nx) < 0.3 and abs(nx) < 1.0:
        tone = mix(tone, BLACK, 0.85)
    # halter: a band round the muzzle, a strap along the cheek to behind the ear, a ring where they meet
    band = abs(f - HALTER[0] - 0.1 * (u - HALTER[1])) < 0.5
    strap = 49.0 < f < HALTER[0] and abs(u - (HALTER[1] + 0.2 + (HALTER[0] - f) * 0.22)) < 0.42
    if band or strap:
        tone = RED if fine > 0.2 else DARK_RED
    if (f - HALTER[0]) ** 2 + ((u - HALTER[1]) * 1.1) ** 2 < 0.8:
        tone = BRASS if fine > 0.25 else (160, 120, 40)
    # eye: a dark almond under a heavy pale lid with a fringe of lashes, a glint
    ex, ey = f - EYE[0], u - EYE[1]
    almond = (ex / 1.45) ** 2 + (ey / 0.85) ** 2
    if almond < 1.7 and ey > -0.2:
        tone = mix(tone, PALE, 0.55)  # the lid
    if almond < 1.0:
        tone = BLACK if ey < 0.3 else mix(UMBER, BLACK, 0.5)
        if -0.8 < ex < -0.3 and -0.05 < ey < 0.35:
            tone = (250, 250, 240)
    if abs(ey - 0.35 + 0.11 * ex * ex) < 0.24 and abs(ex) < 1.8:
        tone = mix(tone, BLACK, 0.85)  # lashes along the lid's edge
    if abs(ex) < 1.7 and 1.25 < ey + 0.1 * ex < 1.6:
        tone = mix(tone, BROWN, 0.5)  # brow
    return tone


def cloth_pixel(x, y):
    """the saddle rug from the seat (top of the texture) down to its fringe; repeats sideways. Leather seat, a red
    field with brass diamonds, a purple band between brass lines, and a fringe of tassels."""
    fine = noise(x, y, 21)
    if y < 5:  # leather (the horns use it too)
        return mix(LEATHER, BLACK, 0.3 * fine) if y < 4 else mix(BRASS, LEATHER, 0.3)
    if y < 21:  # field
        dx, dy = abs((x % 16) - 7.5), abs(y - 12.5)
        diamond = dx / 6.0 + dy / 6.5
        if diamond < 0.38:
            return PURPLE
        if diamond < 0.62:
            return BRASS if fine > 0.15 else (176, 130, 40)
        if 0.95 < diamond < 1.12:
            return mix(BRASS, RED, 0.25)
        return mix(RED, DARK_RED, 0.5 * fine + (0.35 if (x + y) % 2 else 0.0))
    if y in (21, 26):
        return BRASS
    if y < 26:  # band, a zigzag through it
        return BRASS if (y - 22 == abs((x % 8) - 3.5) // 1.2) else mix(PURPLE, BLACK, 0.25 * fine)
    # fringe: tassels two pixels wide, shorter and longer
    tassel = (x // 2) % 2
    if y > 30 - tassel and y >= 30:
        return mix(DARK_RED, BLACK, 0.4)
    return (BRASS if tassel else RED) if x % 2 == 0 else mix(BRASS if tassel else RED, BLACK, 0.3)


# ---------------------------------------------------------------- mesh
COAT, FACE, CLOTH = 0, 1, 2


def coat_ring(vertices, along, top=0.0):
    """coat texture coordinates for a ring: `along` the body, and from `top` (0 = the dark wool of the back) to the
    belly"""
    return [(v, (along * COAT_TEXELS / COAT_SIZE[0], top + (1 - top) * c)) for v, c in zip(vertices, bc.OCTAGON_COAT)]


def face_ring(mesh, vertices):
    out = []
    for v in vertices:
        f, u, _ = mesh.points[v]
        x, y = face_at(f, u)
        out.append((v, (x / FACE_SIZE[0], y / FACE_SIZE[1])))
    return out


def torso_at(f):
    """-> (top, bottom, half width) of the body at f"""
    for (f0, t0, b0, w0), (f1, t1, b1, w1) in zip(TORSO, TORSO[1:]):
        if f <= f1:
            q = (f - f0) / (f1 - f0)
            return t0 + (t1 - t0) * q, b0 + (b1 - b0) * q, w0 + (w1 - w0) * q
    return TORSO[-1][1:]


def build_mesh():
    mesh = bc.Mesh(RIG)

    # --- body and neck: one skin from the rump to the back of the skull
    rings = []
    for f, top, bottom, width in TORSO:
        ring = mesh.tilted_ring((f, (top + bottom) / 2), 0, (top - bottom) / 2, width, "Root")
        rings.append(coat_ring(ring, f))
    along = TORSO[-1][0]
    previous = (TORSO[-1][0], (TORSO[-1][1] + TORSO[-1][2]) / 2)
    skull = None
    for centre, tilt, half_height, half_width, limb, top in NECK:
        along += math.dist(centre, previous)
        previous = centre
        skull = mesh.tilted_ring(centre, tilt, half_height, half_width, limb)
        rings.append(coat_ring(skull, along, top))
    mesh.face(rings[0], COAT)  # under the tail
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, COAT)

    # --- head. Its muzzle is one skin too, but the underside of it belongs to the jaw: where the two meet runs the
    # mouth, a narrow dark strip that opens when the jaw drops (and the cheeks behind it stretch).
    rings = [face_ring(mesh, skull)]
    for centre, tilt, half_height, half_width in HEAD:
        rings.append(face_ring(mesh, mesh.tilted_ring(centre, tilt, half_height, half_width, "Head")))
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, FACE)
    inside = (MOUTH_AT[0] / FACE_SIZE[0], MOUTH_AT[1] / FACE_SIZE[1])
    # each ring so far, split like a muzzle ring: over the top from the right lip to the left, and under from left to right
    eyes = rings[-1]
    upper, lower = [eyes[6], eyes[7], eyes[0], eyes[1], eyes[2]], [eyes[2], eyes[3], eyes[4], eyes[5], eyes[6]]
    for (cf, cu), tilt, half_height, half_width in MUZZLE:
        t = math.radians(tilt)
        up = (-math.sin(t), math.cos(t))

        def at(a, b, limb, drop=0.0):
            return mesh.vertex(cf + up[0] * half_height * b, cu + up[1] * half_height * b - drop, half_width * a, limb)

        top = face_ring(mesh, [at(-0.96, LIPS, "Head"), at(-0.74, 0.72, "Head"), at(0.0, 1.0, "Head"),
                               at(0.74, 0.72, "Head"), at(0.96, LIPS, "Head")])
        under = face_ring(mesh, [at(0.94, LIPS, "Jaw", 0.3), at(0.62, -0.72, "Jaw"), at(0.0, -1.0, "Jaw"),
                                 at(-0.62, -0.72, "Jaw"), at(-0.94, LIPS, "Jaw", 0.3)])
        mesh.loft(upper, top, FACE, closed=False)
        mesh.loft(lower, under, FACE, closed=False)
        if upper[0][0] != lower[4][0]:  # (between muzzle rings: the mouth, on both sides)
            mesh.face([(upper[4][0], inside), (top[4][0], inside), (under[0][0], inside), (lower[0][0], inside)], FACE)
            mesh.face([(lower[4][0], inside), (under[4][0], inside), (top[0][0], inside), (upper[0][0], inside)], FACE)
        upper, lower = top, under
    nose = face_ring(mesh, [mesh.vertex(NOSE_TIP[0], NOSE_TIP[1], 0, "Head")])[0]
    chin = face_ring(mesh, [mesh.vertex(CHIN_TIP[0], CHIN_TIP[1], 0, "Jaw")])[0]
    for i in range(4):
        mesh.face([upper[i], upper[i + 1], nose], FACE)
        mesh.face([lower[i], lower[i + 1], chin], FACE)
    mesh.face([(upper[4][0], inside), (nose[0], inside), (chin[0], inside), (lower[0][0], inside)], FACE)
    mesh.face([(lower[4][0], inside), (chin[0], inside), (nose[0], inside), (upper[0][0], inside)], FACE)

    # --- ears: small, open towards the front and the side
    for side, limb in ((1, "EarL"), (-1, "EarR")):
        p = {k: mesh.vertex(f, u, s * side, limb) for k, (f, u, s) in EAR.items()}
        back = [(p["outer"], (0.2, 0.5)), (p["inner"], (0.8, 0.5)), (p["tip"], (0.5, 0.3))]
        inside = [(p["front"], (0.3, 0.97)), (p["outer"], (0.7, 0.97)), (p["tip"], (0.5, 0.8))]
        front = [(p["inner"], (0.2, 0.5)), (p["front"], (0.8, 0.5)), (p["tip"], (0.5, 0.3))]
        for tri in (back, inside, front):
            mesh.face(tri if side > 0 else tri[::-1], COAT)

    # --- tail: thin, ending in a dark tuft (five-sided; the coat's dark top is used for the tuft)
    rings = []
    count = len(TAIL) - 1
    for i, ((f, u), radius, limb) in enumerate(TAIL):
        (pf, pu), (nf, nu) = TAIL[max(i - 1, 0)][0], TAIL[min(i + 1, count)][0]
        length = math.hypot(nf - pf, nu - pu)
        up = ((nu - pu) / length, -(nf - pf) / length)  # across the tail, on its upper side
        coat = 0.5 if i < 2 else (0.3 if i == 2 else 0.04)
        ring = []
        for k, (a, b) in enumerate([(0, 1), (0.95, 0.31), (0.59, -0.81), (-0.59, -0.81), (-0.95, 0.31)]):
            v = mesh.vertex(f + up[0] * radius * b, u + up[1] * radius * b, radius * a, limb)
            ring.append((v, ((0.0, 0.25, 0.5, 0.5, 0.25)[k], coat)))
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        mesh.loft(b, a, COAT)

    # --- legs: four-sided, an edge to the front; a broad dark pad for a foot
    for leg in (FRONT_LEG, HIND_LEG):
        for side, letter in ((1, "L"), (-1, "R")):
            names = {part: leg["limbs"] % (letter, part) for part in "ABCD"}
            centre = SIDE * side
            f, u, s = leg["top"]
            top = (mesh.vertex(f, u, s * side, names["A"]), (f * COAT_TEXELS / COAT_SIZE[0] / 2, 0.5))
            rings = []
            length = 0.0
            previous = (f, u)
            for f, u, depth, width, limb in leg["rings"]:
                length += math.dist((f, u), previous)
                previous = (f, u)
                along = length * COAT_TEXELS / COAT_SIZE[0]
                dark = limb == "D"
                # front, outside, back, inside; the inside of the leg is paler
                ring = [(mesh.vertex(f + depth, u, centre, names[limb]), (along, 0.1 if dark else 0.56)),
                        (mesh.vertex(f, u, centre + width * side, names[limb]), (along, 0.08 if dark else 0.44)),
                        (mesh.vertex(f - depth, u, centre, names[limb]), (along, 0.1 if dark else 0.6)),
                        (mesh.vertex(f, u, centre - width * side, names[limb]), (along, 0.12 if dark else 0.74))]
                rings.append(ring if side > 0 else [ring[0], ring[3], ring[2], ring[1]])
            front, back, width = leg["sole"]
            along += 3.0 * COAT_TEXELS / COAT_SIZE[0]
            middle = (front + back) / 2 - 0.4
            sole = [(mesh.vertex(front, 0, centre, names["D"]), (along, 0.03)),
                    (mesh.vertex(middle, 0, centre + width * side, names["D"]), (along, 0.02)),
                    (mesh.vertex(back, 0, centre, names["D"]), (along, 0.03)),
                    (mesh.vertex(middle, 0, centre - width * side, names["D"]), (along, 0.04))]
            rings.append(sole if side > 0 else [sole[0], sole[3], sole[2], sole[1]])
            for i in range(4):
                mesh.face([top, rings[0][(i + 1) % 4], rings[0][i]], COAT)
            for a, b in zip(rings, rings[1:]):
                mesh.loft(b, a, COAT)
            mesh.face(rings[-1], COAT)  # sole

    # --- hump: a woolly mound on the back
    def hump_at(f):
        """-> height of the hump's top at f"""
        for (f0, p0), (f1, p1) in zip(HUMP, HUMP[1:]):
            if f <= f1:
                p0 = torso_at(f0)[0] if p0 is None else p0
                p1 = torso_at(f1)[0] if p1 is None else p1
                return p0 + (p1 - p0) * (f - f0) / (f1 - f0)
        return torso_at(f)[0]

    def hump_points(f, out=0.0):
        """-> its cross-section at f, left side: (s, u) at its foot on the back and halfway up; and its top"""
        top, _, width = torso_at(f)
        peak = max(hump_at(f), top + 0.4)
        return [(0.66 * width + out, top - 1.4), (0.5 * width + out, top + 0.74 * (peak - top) + out * 0.5)], peak + out

    rings = []
    for f, _ in HUMP:
        (foot, half), peak = hump_points(f)
        along = f * COAT_TEXELS / COAT_SIZE[0]
        rings.append([(mesh.vertex(f, foot[1], foot[0], "Root"), (along, 0.2)),
                      (mesh.vertex(f, half[1], half[0], "Root"), (along, 0.1)),
                      (mesh.vertex(f, peak, 0.0, "Root"), (along, 0.02)),
                      (mesh.vertex(f, half[1], -half[0], "Root"), (along, 0.1)),
                      (mesh.vertex(f, foot[1], -foot[0], "Root"), (along, 0.2))])
    for a, b in zip(rings, rings[1:]):
        mesh.loft(a, b, COAT, closed=False)

    # --- saddle: a rug over the top of the hump, hanging down the flanks (an open sheet: only its outside is seen)
    stations = []
    for f in SADDLE:
        top, bottom, width = torso_at(f)
        centre, height = (top + bottom) / 2, (top - bottom) / 2
        (foot, half), peak = hump_points(f, 0.7)
        column = (f - SADDLE[0]) / (SADDLE[-1] - SADDLE[0]) * 2.0  # the pattern repeats twice along the rug
        left = [(width + 1.3, centre - 0.02 * height, 1.0), (0.74 * width + 1.3, centre + 0.72 * height + 0.5, 0.76),
                (foot[0] + 0.5, foot[1] + 0.5, 0.58), (half[0], half[1], 0.36)]
        station = [(mesh.vertex(f, u, s, "Root"), (column, v)) for s, u, v in left]
        station.append((mesh.vertex(f, peak, 0.0, "Root"), (column, 0.17)))
        station += [(mesh.vertex(f, u, -s, "Root"), (column, v)) for s, u, v in reversed(left)]
        stations.append(station)
    for a, b in zip(stations, stations[1:]):
        mesh.loft(a, b, CLOTH, closed=False)
    # its horns
    for (bf, bu), (tf, tu), wide, narrow in HORNS:
        foot = [(mesh.vertex(bf + a * wide, bu, b * wide, "Root"), (0.1 + 0.2 * i, 0.14))
                for i, (a, b) in enumerate([(1, 0), (0, 1), (-1, 0), (0, -1)])]
        head = [(mesh.vertex(tf + a * narrow, tu, b * narrow, "Root"), (0.1 + 0.2 * i, 0.02))
                for i, (a, b) in enumerate([(1, 0), (0, 1), (-1, 0), (0, -1)])]
        mesh.loft(foot, head, CLOTH)
        mesh.face(head, CLOTH)
    return mesh


# ---------------------------------------------------------------- animation
def key(move=(0.0, 0.0, 0.0), feet=None, free=False, **turns):
    """a pose: where the body is, the feet (Front, Hind or each leg by name) and how the limbs are turned"""
    pose = Pose(RIG)
    pose.move = list(move)
    pose.free = free
    pose.turn = {limb: tuple(turn) for limb, turn in turns.items()}
    for name, foot in (feet or {}).items():
        for leg in LEGS:
            if leg.startswith(name):
                pose.feet[leg] = foot
    return pose


def step(pose, leg, phase, duty, reach, lift, push, hang, fold):
    """One leg's part in a gait. The foot is on the ground for `duty` of the cycle, moving back from reach[0] to
    reach[1] ahead of where it stands at rest, flat until its heel comes up to `push` degrees as it leaves; then it
    is carried forward `lift` high, hanging by `hang` degrees, the joint above it folded by up to `fold`."""
    phase %= 1.0
    if phase < duty:
        q = phase / duty
        heel = max(0.0, (q - 0.7) / 0.3)
        pose.feet[leg] = Foot((reach[0] + (reach[1] - reach[0]) * q, 0.0), push * heel * heel)
    else:
        q = (phase - duty) / (1 - duty)
        f = reach[1] + (reach[0] - reach[1]) * ease(q)
        pitch = push * (1 - ease(min(1.0, q * 2.5))) - hang * math.sin(math.pi * min(1.0, q * 1.2)) ** 2
        pose.feet[leg] = Foot((f, lift * math.sin(math.pi * q)), pitch, fold * math.sin(math.pi * min(1.0, q * 1.15)) ** 2)


def travel(speed, cycle, duty):
    """how far a foot is carried back while it is on the ground: `speed` in game units a frame, `cycle` in game
    frames"""
    return speed * cycle * duty / SIZE


# The game plays the gaits faster the faster the camel goes (z_en_horse.c): each at the pace of its cycle below at
# its speed below, so the feet do not slide.
WALK_FRAMES, WALK_SPEED, WALK_CYCLE = 35, 3.0, 20  # frames of the animation; game units a frame; game frames a cycle
PACE_FRAMES, PACE_SPEED, PACE_CYCLE = 29, 6.0, 14
GALLOP_FRAMES, GALLOP_SPEED, GALLOP_CYCLE = 24, 13.0, 9


def chew(t, cycles):
    """the jaw going round as it chews: (pitch, yaw)"""
    return (-2.5 - 2.0 * wave(t, 0.0, cycles), 5.0 * wave(t, 0.25, cycles))


def idle_pose(t):
    """standing, in no hurry: breathing, chewing, a slow look to the side, the odd flick of an ear, the tail swinging.
    Its body barely moves: a rider climbs on and sits by where it is at rest."""
    pose = Pose(RIG)
    breath = wave(t, 0, 3)
    pose.move = [0.0, 0.12 * breath, 0.0]
    look = ease(pulse(t, 0.45, 0.6))
    pose.turn["Neck1"] = (1.2 * breath - 2.0 * look, 6.0 * look, 0.0)
    pose.turn["Neck2"] = (-0.8 * breath, 9.0 * look, 0.0)
    pose.turn["Head"] = (-1.0 * breath + 3.0 * look, 12.0 * look, -3.0 * look)
    jaw = chew(t, 9)
    busy = 1.0 - pulse(t, 0.45, 0.3)  # it stops chewing while it looks
    pose.turn["Jaw"] = (jaw[0] * busy, jaw[1] * busy, 0.0)
    pose.turn["EarL"] = (0.0, 26.0 * pulse(t, 0.2, 0.06) - 10 * pulse(t, 0.25, 0.05), -10.0 * pulse(t, 0.2, 0.06))
    pose.turn["EarR"] = (0.0, -26.0 * pulse(t, 0.7, 0.06) + 10 * pulse(t, 0.75, 0.05), 10.0 * pulse(t, 0.7, 0.06))
    swish = pulse(t, 0.3, 0.2) * wave(t, 0.0, 10) + pulse(t, 0.85, 0.2) * wave(t, 0.0, 10)
    pose.turn["Tail1"] = (1.5 * breath, 14.0 * swish, 0.0)
    pose.turn["Tail2"] = (2.0 * breath, 22.0 * swish, 0.0)
    return pose


GRUMBLE_FRAMES = 36


def grumble_pose(t):
    """a camel's complaint (played where Epona whinnies): head up, mouth wide, a shake of the head"""
    def call(yaw, jaw):
        return key(Neck1=(5.0, yaw * 0.4, 0), Neck2=(4.0, yaw * 0.6, 0), Head=(-16.0, yaw, yaw * 0.4),
                   Jaw=(jaw, 0, 0), EarL=(14.0, 0, 0), EarR=(14.0, 0, 0), Tail1=(6.0, 0, 0), Tail2=(8.0, 0, 0))

    last = GRUMBLE_FRAMES - 1
    return keyed([(0, key(), "even"), (7, call(0.0, -26.0), "smooth"), (13, call(13.0, -30.0), "smooth"),
                  (19, call(-13.0, -22.0), "smooth"), (25, call(6.0, -28.0), "smooth"), (last, key(), "smooth")],
                 t * last)


def walk_pose(t):
    """an ambling walk: the two legs of a side follow close on each other (left hind, left front, right hind, right
    front), the body swaying over them, the head nodding"""
    pose = Pose(RIG)
    duty = 0.62
    reach = travel(WALK_SPEED, WALK_CYCLE, duty)
    sway = wave(t, 0.36)  # +1: over its left legs
    pose.move = [0.0, -3.0 + 0.7 * wave(t, 0.2, 2), 1.7 * sway]
    pose.turn["Root"] = (0.7 * wave(t, 0.33, 2), 1.6 * wave(t, 0.1), 3.2 * sway)
    nod = wave(t, 0.4, 2)  # its neck pumps back and forth with every step, its head staying level
    pose.turn["Neck1"] = (-2.0 + 5.0 * nod, -1.0 * wave(t, 0.1), -1.8 * sway)
    pose.turn["Neck2"] = (-3.0 - 7.0 * nod, 0.0, -1.2 * sway)
    pose.turn["Head"] = (5.0 + 2.0 * nod, 0.0, 0.0)
    pose.turn["Jaw"] = (-1.0, 0.0, 0.0)
    pose.turn["EarL"] = (3.0 * wave(t, 0.45, 2), 0.0, 0.0)
    pose.turn["EarR"] = (3.0 * wave(t, 0.45, 2), 0.0, 0.0)
    pose.turn["Tail1"] = (-2.0 + 2.0 * wave(t, 0.1, 2), 8.0 * wave(t, 0.45), 0.0)
    pose.turn["Tail2"] = (-2.0 + 3.0 * wave(t, 0.25, 2), 12.0 * wave(t, 0.6), 0.0)
    for leg, phase, front in (("HindLegL", 0.0, False), ("FrontLegL", 0.12, True), ("HindLegR", 0.5, False),
                              ("FrontLegR", 0.62, True)):
        if front:
            step(pose, leg, t - phase, duty, (reach * 0.52, -reach * 0.48), 5.0, -32, 34, 42)
        else:
            step(pose, leg, t - phase, duty, (reach * 0.54, -reach * 0.46), 4.0, -30, 22, 30)
    return pose


def pace_pose(t):
    """the camel's own gait (played where Epona trots): both legs of a side swing together, the body rolling from
    side to side over them"""
    pose = Pose(RIG)
    duty = 0.46
    reach = travel(PACE_SPEED, PACE_CYCLE, duty)
    sway = wave(t, 0.27)  # +1: over its left legs
    pose.move = [0.0, -3.4 + 1.1 * wave(t, 0.16, 2), 2.3 * sway]
    pose.turn["Root"] = (0.8 * wave(t, 0.3, 2), 1.8 * wave(t, 0.05), 4.6 * sway)
    nod = wave(t, 0.38, 2)
    pose.turn["Neck1"] = (-7.0 + 5.5 * nod, 0.0, -2.8 * sway)
    pose.turn["Neck2"] = (-8.0 - 7.0 * nod, 0.0, -1.6 * sway)
    pose.turn["Head"] = (13.0 + 2.0 * wave(t, 0.5, 2), 0.0, 0.0)
    pose.turn["Jaw"] = (-2.0, 0.0, 0.0)
    pose.turn["EarL"] = (10.0 + 4.0 * wave(t, 0.45, 2), 0.0, 0.0)
    pose.turn["EarR"] = (10.0 + 4.0 * wave(t, 0.45, 2), 0.0, 0.0)
    pose.turn["Tail1"] = (-8.0 + 4.0 * wave(t, 0.2, 2), 6.0 * wave(t, 0.4), 0.0)
    pose.turn["Tail2"] = (-6.0 + 6.0 * wave(t, 0.35, 2), 10.0 * wave(t, 0.55), 0.0)
    for leg, phase, front in (("HindLegL", 0.0, False), ("FrontLegL", 0.05, True), ("HindLegR", 0.5, False),
                              ("FrontLegR", 0.55, True)):
        if front:
            step(pose, leg, t - phase, duty, (reach * 0.52, -reach * 0.48), 8.0, -36, 40, 58)
        else:
            step(pose, leg, t - phase, duty, (reach * 0.54, -reach * 0.46), 6.5, -34, 26, 40)
    return pose


def gallop_pose(t):
    """a gallop: the hind legs push off one after the other, the long neck reaches out, the front legs land and pull
    the body over them"""
    pose = Pose(RIG)
    duty = 0.27
    reach = travel(GALLOP_SPEED, GALLOP_CYCLE, duty)
    stretch = wave(t, 0.2)  # +1 stretched out in the air after the hind feet push off, -1 gathered
    pose.move = [0.0, -4.0 + 2.6 * wave(t, 0.12), 0.0]
    pose.turn["Root"] = (5.5 * wave(t, 0.95), 0.0, 1.6 * wave(t, 0.3))
    pose.turn["Neck1"] = (-10.0 - 7.0 * stretch, 0.0, 0.0)
    pose.turn["Neck2"] = (-38.0 - 9.0 * stretch, 0.0, 0.0)
    pose.turn["Head"] = (40.0 + 6.0 * stretch, 0.0, 0.0)
    pose.turn["Jaw"] = (-7.0 - 3.0 * wave(t, 0.4), 0.0, 0.0)
    pose.turn["EarL"] = (30.0 + 6.0 * wave(t, 0.3), -8.0, 0.0)
    pose.turn["EarR"] = (30.0 + 6.0 * wave(t, 0.3), 8.0, 0.0)
    pose.turn["Tail1"] = (-42.0 - 8.0 * wave(t, 0.35), 0.0, 0.0)
    pose.turn["Tail2"] = (-20.0 - 12.0 * wave(t, 0.5), 0.0, 0.0)
    for leg, phase, front in (("HindLegL", 0.0, False), ("HindLegR", 0.11, False), ("FrontLegL", 0.46, True),
                              ("FrontLegR", 0.57, True)):
        if front:
            step(pose, leg, t - phase, duty, (reach * 0.56, -reach * 0.44), 13.0, -40, 46, 78)
        else:
            step(pose, leg, t - phase, duty, (reach * 0.52, -reach * 0.48), 9.0, -38, 30, 52)
    return pose


# The game plays this one and a half times as fast and has the camel standing still from frame HALT_STILL
# (z_en_horse.c repeats that number).
HALT_FRAMES, HALT_STILL = 60, 44


def halt_pose(t):
    """reined in from a run (played where Epona digs her hooves in): it sits back on braced legs, head thrown up,
    then finds its feet"""
    braced = key(move=(-5.0, -6.5, 0.0), feet={"Front": Foot((11.0, 0.0), 4.0), "Hind": Foot((9.0, 0.0), 0.0)},
                 Root=(6.5, 0, 0), Neck1=(12.0, 0, 0), Neck2=(4.0, 0, 0), Head=(-12.0, 0, 0), Jaw=(-16.0, 0, 0),
                 EarL=(24.0, 0, 0), EarR=(24.0, 0, 0), Tail1=(-30.0, 0, 0), Tail2=(-16.0, 0, 0))
    sliding = key(move=(-3.5, -5.0, 0.0), feet={"Front": Foot((8.0, 0.0), 2.0), "Hind": Foot((6.5, 0.0), 0.0)},
                  Root=(4.5, 0, 0), Neck1=(8.0, 0, 0), Neck2=(2.0, 0, 0), Head=(-6.0, 0, 0), Jaw=(-8.0, 0, 0),
                  EarL=(14.0, 0, 0), EarR=(14.0, 0, 0), Tail1=(-16.0, 0, 0), Tail2=(-8.0, 0, 0))
    # it steps back under itself, one side then the other
    gather = key(move=(-1.2, -1.6, 0.0),
                 feet={"FrontLegL": Foot((3.0, 3.0), -14.0, 24.0), "FrontLegR": Foot((6.0, 0.0)),
                       "HindLegR": Foot((2.0, 2.2), -10.0, 16.0), "HindLegL": Foot((5.0, 0.0))},
                 Root=(1.5, 0, 0), Neck1=(-3.0, 0, 0), Neck2=(-2.0, 0, 0), Head=(5.0, 0, 0), Jaw=(-2.0, 0, 0))
    settle = key(move=(-0.4, -0.5, 0.0),
                 feet={"FrontLegR": Foot((2.0, 2.2), -10.0, 18.0), "HindLegL": Foot((1.5, 1.6), -8.0, 12.0)},
                 Root=(0.5, 0, 0), Neck1=(-1.0, 0, 0))
    last = HALT_FRAMES - 1
    return keyed([(0, key(move=(0.0, -3.0, 0.0), Neck1=(-8.0, 0, 0), Neck2=(-16.0, 0, 0), Head=(20.0, 0, 0)), "even"),
                  (9, braced, "out"), (30, sliding, "smooth"), (HALT_STILL - 3, gather, "smooth"),
                  (HALT_STILL + 6, settle, "smooth"), (last, key(), "smooth")], t * last)


# The game sounds the feet coming down on frame 25 of this one (Epona landing from rearing up).
BALK_FRAMES, BALK_STAMP = 33, 25


def balk_pose(t):
    """it will not go on (played where Epona rears): rocking back with a bellow, tossing its head, a stamp of a
    forefoot. Its feet stay where they are and its body moves little: its rider holds on."""
    def toss(yaw, jaw, foot):
        return key(move=(-2.6, -1.4, 0.0), feet={"FrontLegL": foot}, Root=(2.6, 0, 0),
                   Neck1=(11.0, yaw * 0.5, 0), Neck2=(7.0, yaw * 0.7, 0), Head=(-18.0, yaw, yaw * 0.5),
                   Jaw=(jaw, 0, 0), EarL=(22.0, 0, 0), EarR=(22.0, 0, 0), Tail1=(-24.0, 0, 0), Tail2=(-14.0, 0, 0))

    stamp = key(move=(-1.0, -2.2, 0.0), feet={"FrontLegL": Foot((3.0, 0.0))}, Root=(0.8, 0, 0), Neck1=(2.0, 0, 0),
                Neck2=(1.0, 0, 0), Head=(-2.0, 0, 0), Jaw=(-6.0, 0, 0), Tail1=(-8.0, 0, 0))
    last = BALK_FRAMES - 1
    return keyed([(0, key(), "even"), (6, toss(0.0, -26.0, Foot()), "out"),
                  (13, toss(14.0, -30.0, Foot((2.0, 7.0), -30.0, 56.0)), "smooth"),
                  (20, toss(-14.0, -24.0, Foot((4.0, 10.0), -36.0, 64.0)), "smooth"),
                  (BALK_STAMP, stamp, "in"), (last, key(), "smooth")], t * last)


# ---- lying down and getting up. A camel waits lying on its folded legs, and lets its rider on and off there: it
# gets up hind legs first, from its knees, and lies down front first, onto them.
DROP = 33.0  # how far its body comes down


def ankle_at(leg, ankle, pitch, fold):
    """a foot by where its ankle is, (f, u) from the ground under the camel's middle at rest"""
    limbs = LEGS[leg][3]
    rest, sole = BONES[limbs[-1]][1][:2], (BONES[limbs[-1]][2][0], 0.0)
    reach = bc.turned(bc.sub(sole, rest), pitch)
    return Foot((ankle[0] + reach[0] - sole[0], ankle[1] + reach[1] - sole[1]), pitch, fold)


def folded(front=True, hind=True):
    """its legs folded under it: the forelegs on their knees, the shins tucked back; the hind legs on their hocks,
    the shins lying forward"""
    feet = {}
    if front:
        feet.update({leg: ankle_at(leg, (7.5, 6.5), -100.0, 150.0) for leg in ("FrontLegL", "FrontLegR")})
    if hind:
        feet.update({leg: ankle_at(leg, (-22.0, 5.5), 60.0, 122.0) for leg in ("HindLegL", "HindLegR")})
    return feet


def lying(**turns):
    return key(move=(0.0, -DROP, 0.0), free=True, feet=folded(), **turns)


def on_knees(lift=0.0):
    """down on its knees in front, its rump still up"""
    feet = {leg: ankle_at(leg, (3.0, 5.0), -96.0, 102.0) for leg in ("FrontLegL", "FrontLegR")}
    return key(move=(1.5, -12.0 + lift, 0.0), free=True, feet=feet, Root=(-17.0, 0, 0), Neck1=(9.0, 0, 0),
               Neck2=(6.0, 0, 0), Head=(2.0, 0, 0), Tail1=(-10.0, 0, 0))


def couched_pose(t):
    """lying at rest: chewing, the odd flick of an ear"""
    jaw = chew(t, 7)
    breath = wave(t, 0, 2)
    return lying(Neck1=(1.0 * breath, 0, 0), Neck2=(-0.6 * breath, 0, 0), Jaw=(jaw[0], jaw[1], 0.0),
                 EarL=(0.0, 24.0 * pulse(t, 0.3, 0.08), 0.0), EarR=(0.0, -24.0 * pulse(t, 0.75, 0.08), 0.0),
                 Tail2=(0.0, 14.0 * pulse(t, 0.5, 0.2) * wave(t, 0.0, 8), 0.0))


KNEEL_FRAMES, RISE_FRAMES = 30, 34  # (Camel.h has these numbers too)


def kneel_pose(t):
    """lying down: onto its knees, its hindquarters let down behind, settling"""
    last = KNEEL_FRAMES - 1
    sinking = key(move=(-1.0, -27.0, 0.0), free=True, feet=folded(), Root=(-4.0, 0, 0), Neck1=(4.0, 0, 0))
    return keyed([(0, key(), "even"), (11, on_knees(), "smooth"), (22, sinking, "smooth"), (last, lying(), "out")], t * last)


def rise_pose(t):
    """getting up: its rump goes up first, tipping its rider forward, then it straightens its forelegs"""
    last = RISE_FRAMES - 1
    heaving = key(move=(0.5, -25.0, 0.0), free=True, feet=folded(hind=False), Root=(-10.0, 0, 0), Neck1=(6.0, 0, 0))
    nearly = key(move=(0.0, -2.5, 0.0), Root=(2.5, 0, 0), Neck1=(-4.0, 0, 0), Neck2=(-3.0, 0, 0),
                 feet={"FrontLegL": Foot((2.0, 2.0), -10.0, 20.0)})
    return keyed([(0, lying(), "even"), (8, heaving, "smooth"), (17, on_knees(1.0), "smooth"), (27, nearly, "smooth"),
                  (last, key(), "smooth")], t * last)


# What the camel does while its rider gets on and off (camel_rider.py poses her against these; the game puts the
# same together from the animations above, see Camel.cpp): lying while she climbs on, then getting up; and lying down
# under her, then lying while she gets off.
MOUNT_FRAMES, STEP_ON_FRAMES, DISMOUNT_FRAMES = 60, 22, 52


def for_mount_pose(t):
    frame = t * (MOUNT_FRAMES - 1)
    if frame < STEP_ON_FRAMES:
        return lying()
    return rise_pose(min(1.0, (frame - STEP_ON_FRAMES) / (RISE_FRAMES - 1)))


def for_dismount_pose(t):
    return kneel_pose(min(1.0, t * (DISMOUNT_FRAMES - 1) / (KNEEL_FRAMES - 1)))


PICTURES_ONLY = {"ForMount": (MOUNT_FRAMES, for_mount_pose, False), "ForDismount": (DISMOUNT_FRAMES, for_dismount_pose, False)}

# animation -> (frames, pose at 0..1, whether it goes round: the others play once, from the first frame to the last)
ANIMATIONS = {"Idle": (120, idle_pose, True), "Grumble": (GRUMBLE_FRAMES, grumble_pose, False),
              "Halt": (HALT_FRAMES, halt_pose, False), "Balk": (BALK_FRAMES, balk_pose, False),
              "Walk": (WALK_FRAMES, walk_pose, True), "Pace": (PACE_FRAMES, pace_pose, True),
              "Gallop": (GALLOP_FRAMES, gallop_pose, True), "Couched": (80, couched_pose, True),
              "Kneel": (KNEEL_FRAMES, kneel_pose, False), "Rise": (RISE_FRAMES, rise_pose, False)}
# Epona's animations that Link's hidden riding animation is stepped along with: ours may not have more frames
EPONA_FRAMES = {"Halt": 65, "Balk": 33, "Walk": 35, "Pace": 29, "Gallop": 24}


# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fast64", type=Path, default=Path.home() / "oot-fast64" / "addons",
                    help="folder that contains the HarbourMasters Fast64 fork as `fast64_hm`")
    ap.add_argument("--game", type=Path, default=REPO / "x64" / "Debug" / "oot.o2r",
                    help="the game's archive: Nabooru is imported from it for her riding animations")
    ap.add_argument("--no-rider", action="store_true", help="the camel alone")
    ap.add_argument("--nabooru", type=Path, default=REPO / "x64" / "Debug" / "mods" / "play_as_nabooru.o2r",
                    help="the mod's archive of her animations (her riding starts from her standing pose)")
    ap.add_argument("--out", type=Path, default=REPO / "x64" / "Debug" / "mods" / "camel.o2r")
    ap.add_argument("--blend", type=Path, help="also save the Blender file here")
    ap.add_argument("--preview", type=Path, help="render pictures of the model into this folder")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args(argv)

    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.cameras, bpy.data.lights):
        for item in list(collection):
            collection.remove(item)
    bc.load_fast64(args.fast64)
    assert RIG.limb_order() == LIMB_ORDER, RIG.limb_order()
    for name, limit in EPONA_FRAMES.items():
        assert ANIMATIONS[name][0] <= limit, f"{name} is longer than Link's riding animation for it"

    coat = bc.make_material("CamelCoat", bc.make_image("gCamelCoatTex", COAT_SIZE, coat_pixel), False, True)
    face = bc.make_material("CamelFace", bc.make_image("gCamelFaceTex", FACE_SIZE, face_pixel), True, True)
    cloth = bc.make_material("CamelCloth", bc.make_image("gCamelClothTex", CLOTH_SIZE, cloth_pixel), False, True)
    mesh = build_mesh()
    obj, armature = bc.make_objects(RIG, mesh, [coat, face, cloth])
    print(f"camel: {len(mesh.points)} vertices, {mesh.triangles()} triangles, {len(BONES)} limbs")
    print(f"camel: seat at {SEAT[0] * SIZE:.1f} forward, {SEAT[1] * SIZE:.1f} up (game units)")

    actions = bc.make_actions(RIG, armature, {**ANIMATIONS, **PICTURES_ONLY})

    rider = None
    if not args.no_rider:
        import camel_rider

        rider = camel_rider.Rider(args.game, args.nabooru, sys.modules[__name__], armature, actions)
        rider.make_actions()

    look = dict(target=(0.0, -1.0, 4.6), span=15.0)
    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        bc.show(armature, actions["Idle"])
        if rider:
            rider.hide(True)
        bc.render_previews(args.preview, [("model_side", 90, 4, 0), ("model_front", 25, 12, 0),
                                          ("model_back", 150, 25, 0), ("model_top", 60, 55, 0)], **look)
        bc.render_previews(args.preview, [("head_side", 90, 4, 0), ("head_front", 20, 8, 0)],
                           target=(0.0, -4.6, 8.4), span=4.5)
        for name in ANIMATIONS:
            frames, action = ANIMATIONS[name][0], actions[name]
            bc.show(armature, action)
            bc.render_previews(args.preview, [(f"{name.lower()}_{frame:02}", 90, 4, frame)
                                              for frame in range(0, frames, max(1, frames // 8))], columns=4, **look)
        bc.show(armature, actions["Pace"])
        bc.render_previews(args.preview, [(f"pacefront_{frame:02}", 20, 10, frame) for frame in range(0, 29, 7)],
                           columns=4, **look)
        if rider:
            rider.hide(False)
            rider.render_previews(args.preview)
    bc.show(armature, actions["Idle"])
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend))
    if args.no_export:
        return

    with tempfile.TemporaryDirectory() as folder:
        files = {path: file.read_bytes() for path, file in bc.export_skeleton(armature, OBJECT, Path(folder)).items()}
    assert sum("SkelLimb_" in path for path in files) == len(BONES), "the skeleton lost limbs"
    for name in ANIMATIONS:
        files[f"{OBJECT}/gCamel{name}Anim"] = bc.export_animation(armature, actions[name], SKELETON)
    bc.show(armature, actions["Idle"])
    if rider:
        files.update(rider.export())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    print(f"{args.out}: {len(files)} resources")


if __name__ == "__main__":
    main()
