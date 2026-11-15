"""Nabooru's animations for her camel (Play as Nabooru mod): climbing on, riding, getting off. Used by
build_camel.py, in the Blender scene that holds the camel.

Her skeleton and model are imported from the game's own archive (oot.o2r) with the HarbourMasters Fast64, scaled to
the camel and attached to its seat, so the pictures show her where the game draws her: in the camel's model frame,
carried along by its body (Camel_GetSeat in Camel.cpp). She is posed there by what her body does, not by joint
angles: where her hips are and how she leans, where each hand and foot is; arms and legs are bent to reach (see Key).
The poses are keyed on her Blender armature and exported with Fast64 like the camel's.

All places are in game units in the camel's frame: x to its left, y up from the ground under it, z forward.
"""
import math
import struct
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

import blender_creature as bc
from blender_creature import ease, wave

OBJECT = "objects/object_nb"
SKELETON = "gNabooruSkel"
BASE_ANIMATION = "gPlayAsNabooruRelaxedAnim"  # her standing pose, arms at her sides
# her limbs, as in the game (object_nb)
ROOT, L_THIGH, L_SHIN, L_FOOT, R_THIGH, R_SHIN, R_FOOT, TORSO = range(8)
L_ARM, L_FOREARM, L_HAND, R_ARM, R_FOREARM, R_HAND, HEAD = range(8, 15)
LIMBS = 18
ARMS = {"L": (L_ARM, L_FOREARM, L_HAND), "R": (R_ARM, R_FOREARM, R_HAND)}
LEGS = {"L": (L_THIGH, L_SHIN, L_FOOT), "R": (R_THIGH, R_SHIN, R_FOOT)}
SIDE = {"L": 1.0, "R": -1.0}

TO_BLENDER = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))  # game axes (x left, y up, z forward) -> Blender's


def rx(degrees):
    """leaning forward / toes down"""
    return Matrix.Rotation(math.radians(degrees), 3, "X")


def ry(degrees):
    """turning to the left"""
    return Matrix.Rotation(math.radians(degrees), 3, "Y")


def rz(degrees):
    return Matrix.Rotation(math.radians(degrees), 3, "Z")


def turn(yaw, pitch, lean):
    """facing `yaw` to the left, bent `pitch` forward, leaning `lean` to the left"""
    return ry(yaw) @ rx(pitch) @ rz(-lean)


def read_animation(data, limbs):
    """an SoH animation resource -> [frame][0: root translation, 1 + limb: rotation] of (x, y, z)"""
    at = 64
    _, frames, count = struct.unpack_from("<IhI", data, at)
    at += 10
    values = struct.unpack_from(f"<{count}H", data, at)
    at += 2 * count
    (joints,) = struct.unpack_from("<I", data, at)
    at += 4
    indices = struct.unpack_from(f"<{joints * 3}H", data, at)
    at += 2 * joints * 3
    (still,) = struct.unpack_from("<h", data, at)
    assert joints == limbs + 1, (joints, limbs)

    def value(index, frame):
        v = values[index if index < still else index + frame]
        return v - 0x10000 if v >= 0x8000 else v

    return [[tuple(value(indices[j * 3 + c], frame) for c in range(3)) for j in range(joints)] for frame in range(frames)]


class Pose:
    """her root's place and every limb's rotation against its parent, as the game has them"""

    def __init__(self, body, root, rotations):
        self.body, self.root, self.rotations = body, Vector(root), [r.copy() for r in rotations]

    def copy(self):
        return Pose(self.body, self.root, self.rotations)

    def world(self):
        """-> ([each limb's orientation], [each limb's joint])"""
        turns, places = [None] * LIMBS, [None] * LIMBS
        for limb in range(LIMBS):
            parent = self.body.parents[limb]
            if parent < 0:
                turns[limb], places[limb] = self.rotations[limb], self.root
            else:
                turns[limb] = turns[parent] @ self.rotations[limb]
                places[limb] = places[parent] + turns[parent] @ self.body.offsets[limb]
        return turns, places

    def set_world(self, limb, orientation):
        turns, _ = self.world()
        parent = self.body.parents[limb]
        self.rotations[limb] = orientation if parent < 0 else turns[parent].inverted() @ orientation

    def aim(self, limb, direction):
        """turns a limb the shortest way so that it points along `direction` (limbs run along their own x)"""
        turns, _ = self.world()
        now = turns[limb] @ Vector((1, 0, 0))
        self.set_world(limb, now.rotation_difference(Vector(direction)).to_matrix() @ turns[limb])

    def reach(self, upper, lower, end, target, bend):
        """bends a two-part limb so that its end joint is at `target`, its middle joint towards `bend`"""
        _, places = self.world()
        top = places[upper]
        first, second = self.body.offsets[lower].length, self.body.offsets[end].length
        along = Vector(target) - top
        distance = min(max(along.length, abs(first - second) + 0.01), first + second - 0.01)
        along.normalize()
        near = (first * first - second * second + distance * distance) / (2 * distance)
        out = math.sqrt(max(first * first - near * near, 0.0))
        side = (Vector(bend) - top) - along * (Vector(bend) - top).dot(along)
        if side.length < 1e-6:
            side = along.orthogonal()
        middle = top + along * near + side.normalized() * out
        self.aim(upper, middle - top)
        self.aim(lower, top + along * distance - middle)


@dataclass
class Key:
    """What her body does. Places are (x, y, z); "w" in the camel's frame, "b" from her hips in her own (to her left,
    up her back, ahead of her). Angles in degrees."""
    root: tuple = (0.0, 0.0, 0.0)  # her hips
    yaw: float = 0.0  # facing to the left of the camel's heading
    pitch: float = 0.0  # her whole body tipped forward
    lean: float = 0.0  # ... to her left
    torso: tuple = (0.0, 0.0, 0.0)  # her back bent forward, twisted to the left, bent to the left
    head: tuple = (0.0, 0.0, 0.0)  # her head bowed, turned to the left, tilted to the left
    # each hand (her wrist): None lets the arm hang as when she stands; else ("w" | "b", place)
    hands: dict = field(default_factory=lambda: {"L": None, "R": None})
    elbows: dict = field(default_factory=lambda: {"L": None, "R": None})  # which way the elbow points, as a place
    # each foot (her ankle): None stands it on the ground under her; else ("w" | "b", place)
    feet: dict = field(default_factory=lambda: {"L": None, "R": None})
    knees: dict = field(default_factory=lambda: {"L": None, "R": None})
    toes: dict = field(default_factory=lambda: {"L": (0.0, 0.0), "R": (0.0, 0.0)})  # each foot: toes down, turned left


@dataclass
class Solved:
    """a Key worked out: everything as plain numbers in the camel's frame, which can be blended"""
    root: Vector
    turn: tuple  # yaw, pitch, lean
    torso: tuple
    head: tuple
    wrists: dict
    elbows: dict
    ankles: dict
    knees: dict
    toes: dict  # foot: (toes down, facing to the left of the camel's heading)


def mirrored(key):
    """the same, done from the camel's other side"""
    def flip(spec):
        return None if spec is None else (spec[0], (-spec[1][0], spec[1][1], spec[1][2]))

    def swap(pair, each=flip):
        return {"L": each(pair["R"]), "R": each(pair["L"])}

    return Key(root=(-key.root[0], key.root[1], key.root[2]), yaw=-key.yaw, pitch=key.pitch, lean=-key.lean,
               torso=(key.torso[0], -key.torso[1], -key.torso[2]), head=(key.head[0], -key.head[1], -key.head[2]),
               hands=swap(key.hands), elbows=swap(key.elbows), feet=swap(key.feet), knees=swap(key.knees),
               toes=swap(key.toes, lambda toe: (toe[0], -toe[1])))


class Body:
    def __init__(self, armature, base_frame):
        bones = sorted(armature.data.bones, key=lambda bone: bone.name)  # named bone000_... in the game's order
        assert len(bones) == LIMBS
        self.names = [bone.name for bone in bones]
        self.parents = [self.names.index(bone.parent.name) if bone.parent else -1 for bone in bones]
        back = TO_BLENDER.inverted()
        # (the imported armature has 100 game units to a Blender unit)
        self.offsets = [back @ (bone.head_local - (bone.parent.head_local if bone.parent else Vector())) * 100.0
                        for bone in bones]
        rotations = []
        for x, y, z in base_frame[1:]:
            rotations.append(rz(z * 180 / 0x8000) @ ry(y * 180 / 0x8000) @ rx(x * 180 / 0x8000))
        self.base = Pose(self, Vector(base_frame[0]) / 100.0, rotations)
        self.base_turns, self.base_places = self.base.world()
        self.height = self.base.root.y  # of her hips as she stands

    def solve(self, key):
        """-> (Pose, Solved)"""
        base, turns0, places0 = self.base, self.base_turns, self.base_places
        pose = base.copy()
        pose.root = Vector(key.root)
        whole = turn(key.yaw, key.pitch, key.lean)
        pose.rotations[ROOT] = whole @ base.rotations[ROOT]
        spine = whole @ turn(key.torso[1], key.torso[0], key.torso[2])
        pose.set_world(TORSO, spine @ turns0[TORSO])
        pose.set_world(HEAD, spine @ turn(key.head[1], key.head[0], key.head[2]) @ turns0[HEAD])

        def place(spec):
            return Vector(spec[1]) if spec[0] == "w" else pose.root + whole @ Vector(spec[1])

        wrists, elbows, ankles, knees, toes = {}, {}, {}, {}, {}
        for side, (upper, lower, hand) in ARMS.items():
            if key.hands[side] is not None:
                _, places = pose.world()
                bend = place(key.elbows[side]) if key.elbows[side] else places[upper] + whole @ Vector(
                    (6.0 * SIDE[side], -8.0, -6.0))
                pose.reach(upper, lower, hand, place(key.hands[side]), bend)
            _, places = pose.world()
            wrists[side], elbows[side] = places[hand], places[lower]
        for side, (upper, lower, foot) in LEGS.items():
            _, places = pose.world()
            down, left = key.toes[side]
            if key.feet[side] is None:
                # standing on the ground under her, as she stands at rest but facing her way
                rest = places0[foot] - base.root
                ankle = Vector((pose.root.x, places0[foot].y, pose.root.z)) + ry(key.yaw) @ Vector((rest.x, 0, rest.z))
                facing = key.yaw + left
            else:
                ankle = place(key.feet[side])
                facing = key.yaw + left
            bend = place(key.knees[side]) if key.knees[side] else places[upper] + ry(key.yaw) @ Vector(
                (2.0 * SIDE[side], -4.0, 14.0))
            pose.reach(upper, lower, foot, ankle, bend)
            pose.set_world(foot, ry(facing) @ rx(down) @ turns0[foot])
            _, places = pose.world()
            ankles[side], knees[side], toes[side] = places[foot], places[lower], (down, facing)
        return pose, Solved(pose.root.copy(), (key.yaw, key.pitch, key.lean), tuple(key.torso), tuple(key.head), wrists,
                            elbows, ankles, knees, toes)

    def pose(self, solved):
        """-> Pose of a Solved (possibly a blend of two): every arm and leg bent to reach"""
        base, turns0 = self.base, self.base_turns
        pose = base.copy()
        pose.root = solved.root.copy()
        whole = turn(*solved.turn)
        pose.rotations[ROOT] = whole @ base.rotations[ROOT]
        spine = whole @ turn(solved.torso[1], solved.torso[0], solved.torso[2])
        pose.set_world(TORSO, spine @ turns0[TORSO])
        pose.set_world(HEAD, spine @ turn(solved.head[1], solved.head[0], solved.head[2]) @ turns0[HEAD])
        for side, (upper, lower, hand) in ARMS.items():
            pose.reach(upper, lower, hand, solved.wrists[side], solved.elbows[side])
        for side, (upper, lower, foot) in LEGS.items():
            pose.reach(upper, lower, foot, solved.ankles[side], solved.knees[side])
            down, facing = solved.toes[side]
            pose.set_world(foot, ry(facing) @ rx(down) @ turns0[foot])
        return pose


def blend(a, b, q):
    def mid(x, y):
        return x + (y - x) * q

    def each(x, y):
        return tuple(mid(i, j) for i, j in zip(x, y))

    def both(x, y, how):
        return {side: how(x[side], y[side]) for side in "LR"}

    return Solved(a.root.lerp(b.root, q), each(a.turn, b.turn), each(a.torso, b.torso), each(a.head, b.head),
                  both(a.wrists, b.wrists, lambda x, y: x.lerp(y, q)), both(a.elbows, b.elbows, lambda x, y: x.lerp(y, q)),
                  both(a.ankles, b.ankles, lambda x, y: x.lerp(y, q)), both(a.knees, b.knees, lambda x, y: x.lerp(y, q)),
                  both(a.toes, b.toes, each))


EASE = {"smooth": ease, "in": lambda q: q * q, "out": lambda q: 1 - (1 - q) ** 2, "even": lambda q: q}


class Rider:
    def __init__(self, game_archive, nabooru_archive, camel, camel_armature):
        """camel: build_camel's module (its measures and gaits)"""
        self.camel, self.camel_armature = camel, camel_armature
        size = camel.SIZE
        self.seat = (camel.SEAT[1] * size, camel.SEAT[0] * size)  # height of the seat, how far forward it is
        (ff, fu), (ftf, ftu), _, _ = camel.HORNS[0]
        (bf, bu), (btf, btu), _, _ = camel.HORNS[1]
        self.front_horn = Vector((0.0, ftu * size, ftf * size))  # the tops of the saddle's horns
        self.back_horn = Vector((0.0, btu * size, btf * size))

        before = set(bpy.data.objects)
        scene = bpy.context.scene
        scene.hm64_use_o2r_import = True
        scene.hm64_o2r_path = str(game_archive)
        settings = scene.fast64.oot.skeletonImportSettings
        settings.mode, settings.name, settings.folder = "Generic", SKELETON, "object_nb"
        settings.actorOverlayName = "ovl_En_Nb"
        settings.autoDetectActorScale, settings.actorScale = False, 100
        assert bpy.ops.object.oot_import_skeleton() == {"FINISHED"}, "Fast64 could not import Nabooru"
        new = [obj for obj in bpy.data.objects if obj not in before]
        self.armature = next(obj for obj in new if obj.type == "ARMATURE")
        self.objects = [obj for obj in new if obj.parent == self.armature] + [self.armature]
        for obj in new:
            if obj not in self.objects:  # (Fast64's material library plane)
                obj.hide_render = True
        # to the camel's scale (hers: 100 game units to a Blender unit), and carried by its seat
        seat = camel_armature.data.bones["Seat"]
        self.armature.scale = (10.0 * camel.RIG.unit / (0.1 * size),) * 3 if False else (10.0,) * 3
        self.armature.parent, self.armature.parent_type, self.armature.parent_bone = camel_armature, "BONE", "Seat"
        self.armature.matrix_parent_inverse = (seat.matrix_local @ Matrix.Translation((0, seat.length, 0))).inverted()

        with zipfile.ZipFile(nabooru_archive) as archive:
            frames = read_animation(archive.read(f"{OBJECT}/{BASE_ANIMATION}"), LIMBS)
        self.body = Body(self.armature, frames[0])
        self.actions = {}
        print(f"rider: Nabooru's hips stand {self.body.height:.1f} high; seat {self.seat[0]:.1f} high, "
              f"{self.seat[1]:.1f} forward")

    def hide(self, hidden):
        for obj in self.objects:
            obj.hide_render = hidden

    # ------------------------------------------------------------ what she does
    def sitting(self, **changes):
        """in the saddle: astride the hump, her legs down its flanks, her left hand on the front horn, her right on
        her thigh"""
        seat_y, seat_z = self.seat
        horn = self.front_horn
        key = Key(root=(0.0, seat_y + 3.6, seat_z - 0.3), torso=(3.0, 0.0, 0.0),
                  hands={"L": ("w", (1.4, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-8.8, seat_y + 5.6, seat_z + 7.0))},
                  elbows={"L": ("b", (10.0, 9.0, -3.0)), "R": ("b", (-11.0, 9.0, -5.0))},
                  feet={"L": ("w", (12.6, seat_y - 17.0, seat_z + 7.0)), "R": ("w", (-12.6, seat_y - 17.0, seat_z + 7.0))},
                  knees={"L": ("w", (10.0, seat_y + 0.4, seat_z + 10.5)), "R": ("w", (-10.0, seat_y + 0.4, seat_z + 10.5))},
                  toes={"L": (22.0, 8.0), "R": (22.0, -8.0)})
        return replace(key, **changes)

    def standing(self, side, yaw=0.0):
        """beside the camel, facing its way (or `yaw` from it): where Link's own mounting starts and his dismounting
        ends (from the rider's place En_Horse keeps: z_player.c's D_80854578)"""
        left, ahead = (35.17, 6.61) if side > 0 else (-34.16, 7.91)
        return Key(root=(left, self.body.height, self.seat[1] + ahead), yaw=yaw)

    def mount_keys(self):
        """climbing on from the camel's left: a step up to it and a crouch, a jump to catch the saddle's horns, pulling
        herself up onto her front across the saddle, her right leg swung over its back, sitting up"""
        h, (seat_y, seat_z), front, back = self.body.height, self.seat, self.front_horn, self.back_horn
        grip_front = ("w", (2.6, front.y + 0.4, front.z))
        grip_back = ("w", (2.6, back.y + 0.4, back.z))
        hang = {"L": ("b", (5.0, 13.0, 9.0)), "R": ("b", (-5.0, 13.0, 9.0))}
        sit = self.sitting()
        return [
            # (she has walked up to its flank: she starts facing it, where Link is turned its way)
            (0, self.standing(1, -90), "even"),
            (5, Key(root=(29.5, h - 1.0, seat_z + 3.5), yaw=-90, torso=(4, 0, 0), head=(-14, 0, 0)), "smooth"),
            (10, Key(root=(24.5, h - 8.0, seat_z + 1.0), yaw=-90, pitch=14, torso=(12, 0, 0), head=(-24, 0, 0),
                     hands={"L": ("b", (6.5, 9.0, 9.0)), "R": ("b", (-6.5, 9.0, 9.0))}), "smooth"),
            (15, Key(root=(15.8, 61.0, seat_z), yaw=-90, pitch=6, torso=(4, 0, 0), head=(-20, 0, 0),
                     hands={"L": grip_front, "R": grip_back}, elbows=hang,
                     feet={"L": ("b", (3.5, -25.0, 4.0)), "R": ("b", (-3.5, -27.0, -1.0))},
                     toes={"L": (30.0, 0.0), "R": (36.0, 0.0)}), "out"),
            (21, Key(root=(10.6, seat_y + 2.0, seat_z), yaw=-90, pitch=52, torso=(12, 0, 0), head=(-30, 0, 0),
                     hands={"L": grip_front, "R": grip_back},
                     elbows={"L": ("b", (11.0, 12.0, 4.0)), "R": ("b", (-11.0, 12.0, 4.0))},
                     feet={"L": ("w", (15.0, seat_y - 25.0, seat_z + 4.0)), "R": ("w", (16.0, seat_y - 22.0, seat_z - 5.0))},
                     toes={"L": (34.0, 0.0), "R": (30.0, 0.0)}), "smooth"),
            (28, Key(root=(5.0, seat_y + 5.0, seat_z - 0.5), yaw=-50, pitch=40, torso=(8, 14, 0), head=(-26, 20, 0),
                     hands={"L": grip_front, "R": ("w", (3.0, seat_y + 2.0, seat_z - 6.0))},
                     elbows={"L": ("b", (11.0, 10.0, 2.0)), "R": ("b", (-10.0, 8.0, -4.0))},
                     feet={"L": ("w", (15.0, seat_y - 19.0, seat_z + 6.0)), "R": ("w", (-3.0, seat_y + 9.5, seat_z - 19.0))},
                     knees={"L": None, "R": ("w", (2.0, seat_y + 12.0, seat_z - 9.0))},
                     toes={"L": (26.0, 0.0), "R": (40.0, 0.0)}), "smooth"),
            (35, Key(root=(0.8, seat_y + 4.6, seat_z - 0.4), yaw=-10, pitch=24, torso=(6, 4, 0), head=(-18, 6, 0),
                     hands={"L": sit.hands["L"], "R": ("w", (-1.6, front.y + 2.2, front.z - 2.4))},
                     elbows={"L": sit.elbows["L"], "R": ("b", (-10.0, 8.0, -2.0))},
                     feet={"L": sit.feet["L"], "R": ("w", (-14.6, seat_y - 9.0, seat_z + 2.0))},
                     knees={"L": sit.knees["L"], "R": ("w", (-11.5, seat_y + 2.0, seat_z + 8.0))},
                     toes={"L": (20.0, 6.0), "R": (30.0, -6.0)}), "smooth"),
            (41, replace(sit, root=(0.0, seat_y + 3.4, seat_z - 0.3), pitch=3, torso=(6.0, 0.0, 0.0),
                         hands={"L": sit.hands["L"], "R": ("w", (-6.0, seat_y + 8.0, seat_z + 6.5))}), "smooth"),
            (46, sit, "smooth"),
        ]

    def dismount_keys(self):
        """getting off to the camel's left: leaning onto the front horn, her right leg swung back over, turning onto
        her front across the saddle, sliding off and dropping, a landing in a crouch, standing up"""
        h, (seat_y, seat_z), front, back = self.body.height, self.seat, self.front_horn, self.back_horn
        grip_front = ("w", (2.6, front.y + 0.4, front.z))
        grip_back = ("w", (2.6, back.y + 0.4, back.z))
        sit = self.sitting()
        return [
            (0, sit, "even"),
            (5, replace(sit, root=(0.6, seat_y + 4.4, seat_z - 0.4), yaw=-8, pitch=22, torso=(6, 4, 0), head=(-16, 10, 0),
                        hands={"L": sit.hands["L"], "R": ("w", (-1.6, front.y + 2.2, front.z - 2.4))},
                        elbows={"L": sit.elbows["L"], "R": ("b", (-10.0, 8.0, -2.0))}), "smooth"),
            (11, Key(root=(5.0, seat_y + 5.0, seat_z - 0.5), yaw=-52, pitch=40, torso=(8, 12, 0), head=(-24, 24, 0),
                     hands={"L": grip_front, "R": ("w", (3.0, seat_y + 2.0, seat_z - 6.0))},
                     elbows={"L": ("b", (11.0, 10.0, 2.0)), "R": ("b", (-10.0, 8.0, -4.0))},
                     feet={"L": ("w", (15.0, seat_y - 19.0, seat_z + 6.0)), "R": ("w", (-3.0, seat_y + 9.5, seat_z - 19.0))},
                     knees={"L": None, "R": ("w", (2.0, seat_y + 12.0, seat_z - 9.0))},
                     toes={"L": (26.0, 0.0), "R": (40.0, 0.0)}), "smooth"),
            (17, Key(root=(10.8, seat_y + 1.0, seat_z), yaw=-90, pitch=46, torso=(10, 0, 0), head=(-26, 0, 0),
                     hands={"L": grip_front, "R": grip_back},
                     elbows={"L": ("b", (11.0, 12.0, 4.0)), "R": ("b", (-11.0, 12.0, 4.0))},
                     feet={"L": ("w", (15.0, seat_y - 26.0, seat_z + 4.0)), "R": ("w", (15.5, seat_y - 26.0, seat_z - 4.0))},
                     toes={"L": (34.0, 0.0), "R": (34.0, 0.0)}), "smooth"),
            (21, Key(root=(17.5, 58.0, seat_z + 0.5), yaw=-90, pitch=10, torso=(4, 0, 0), head=(-6, 0, 0),
                     hands={"L": ("b", (7.5, 20.0, 9.0)), "R": ("b", (-7.5, 20.0, 9.0))},
                     feet={"L": ("b", (3.5, -26.0, 3.0)), "R": ("b", (-3.5, -27.0, 0.0))},
                     toes={"L": (30.0, 0.0), "R": (30.0, 0.0)}), "in"),
            (25, Key(root=(26.0, h - 9.5, seat_z + 2.0), yaw=-84, pitch=16, torso=(14, 0, 0), head=(-14, 0, 0),
                     hands={"L": ("b", (9.0, 4.0, 8.0)), "R": ("b", (-9.0, 4.0, 8.0))}), "in"),
            (29, Key(root=(32.0, h - 2.5, seat_z + 5.0), yaw=-40, torso=(5, 0, 0), head=(-4, 14, 0)), "out"),
            (32, self.standing(1), "smooth"),
        ]

    def riding(self, name, t):
        """her seat on a moving camel: she is carried by its body, so this is only what she does against it. She
        keeps her own back upright as it rolls, gives with its bounce, leans into its gallop."""
        camel = self.camel
        sit = self.sitting()
        seat_y, seat_z = self.seat
        horn = self.front_horn
        both_hands = {"L": ("w", (1.5, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-1.5, horn.y + 2.2, horn.z - 2.4))}
        both_elbows = {"L": ("b", (10.0, 8.5, -2.0)), "R": ("b", (-10.0, 8.5, -2.0))}

        def legs(swing_l, swing_r, grip=0.0):
            """her lower legs swinging forward by these, her knees drawn up by `grip`"""
            return dict(feet={"L": ("w", (12.6, seat_y - 17.0 + grip, seat_z + 7.0 + swing_l)),
                              "R": ("w", (-12.6, seat_y - 17.0 + grip, seat_z + 7.0 + swing_r))},
                        knees={"L": ("w", (10.0, seat_y + 0.4 + grip, seat_z + 10.5)),
                               "R": ("w", (-10.0, seat_y + 0.4 + grip, seat_z + 10.5))})

        if name == "RideIdle":
            breath = wave(t, 0.0, 2)
            look = wave(t, 0.1)
            return replace(sit, torso=(3.0 + 1.0 * breath, 0.0, 0.0), head=(-0.6 * breath, 5.0 * look, 0.0),
                           root=(0.0, seat_y + 3.6 + 0.08 * breath, seat_z - 0.3))
        if name == "RideWalk":
            sway = wave(t, 0.36)  # the camel's own: +1 over its left legs
            nod = wave(t, 0.3, 2)
            return replace(sit, root=(-0.5 * sway, seat_y + 3.6 + 0.15 * nod, seat_z - 0.3), lean=-1.9 * sway,
                           torso=(4.0 + 1.6 * nod, 0.0, -1.2 * sway), head=(-1.5 * nod, 0.0, 1.4 * sway),
                           **legs(1.2 * wave(t, 0.5), -1.2 * wave(t, 0.5)))
        if name == "RidePace":
            sway = wave(t, 0.27)
            bounce = wave(t, 0.22, 2)
            return replace(sit, root=(-0.9 * sway, seat_y + 3.7 + 0.45 * bounce, seat_z - 0.4), lean=-2.9 * sway, pitch=2,
                           torso=(7.0 + 2.2 * bounce, 0.0, -2.0 * sway), head=(-4.0 - 2.0 * bounce, 0.0, 2.2 * sway),
                           hands={"L": sit.hands["L"], "R": ("w", (-8.4, seat_y + 5.9 + 0.3 * bounce, seat_z + 6.6))},
                           **legs(1.8 * wave(t, 0.45), -1.8 * wave(t, 0.45), 0.6))
        if name == "RideGallop":
            rock = wave(t, 0.95)  # the camel's own pitching
            lift = wave(t, 0.2)
            return replace(sit, root=(0.0, seat_y + 4.6 + 0.9 * lift, seat_z - 1.2), pitch=12 - 3.4 * rock,
                           torso=(13.0 + 4.0 * wave(t, 0.3), 0.0, 0.0), head=(-22.0 - 3.0 * wave(t, 0.4), 0.0, 0.0),
                           hands=both_hands, elbows=both_elbows, toes={"L": (22.0, 10.0), "R": (22.0, -10.0)},
                           **legs(-2.5 + 1.2 * rock, -2.5 + 1.2 * rock, 1.6))
        raise KeyError(name)

    def halt_keys(self):
        """reined in: thrown forward onto the horn as the camel sits back, hauling back against it, settling"""
        sit = self.sitting()
        seat_y, seat_z = self.seat
        horn = self.front_horn
        both = {"L": ("w", (1.5, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-1.5, horn.y + 2.2, horn.z - 2.4))}
        elbows = {"L": ("b", (10.0, 8.5, -2.0)), "R": ("b", (-10.0, 8.5, -2.0))}
        gallop = self.riding("RideGallop", 0.0)
        thrown = replace(sit, root=(0.0, seat_y + 3.9, seat_z + 0.6), pitch=17, torso=(15.0, 0.0, 0.0), head=(-20.0, 0, 0),
                         hands=both, elbows={"L": ("b", (11.5, 6.0, 0.0)), "R": ("b", (-11.5, 6.0, 0.0))})
        hauling = replace(sit, root=(0.0, seat_y + 3.6, seat_z - 1.4), pitch=-9, torso=(-4.0, 0.0, 0.0), head=(6.0, 0, 0),
                          hands=both, elbows=elbows, toes={"L": (4.0, 12.0), "R": (4.0, -12.0)})
        easing = replace(sit, pitch=-3, torso=(1.0, 0.0, 0.0), hands=both, elbows=elbows)
        last = self.camel.HALT_FRAMES - 1
        return [(0, gallop, "even"), (8, thrown, "out"), (22, hauling, "smooth"), (40, easing, "smooth"),
                (last - 6, sit, "smooth"), (last, sit, "even")]

    def balk_keys(self):
        """the camel balks: she holds on with both hands, thrown about a little by its tossing"""
        sit = self.sitting()
        seat_y, seat_z = self.seat
        horn = self.front_horn
        both = {"L": ("w", (1.5, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-1.5, horn.y + 2.2, horn.z - 2.4))}
        elbows = {"L": ("b", (10.0, 8.5, -2.0)), "R": ("b", (-10.0, 8.5, -2.0))}

        def held(pitch, lean, head):
            return replace(sit, root=(0.0, seat_y + 3.7, seat_z - 0.2), pitch=pitch, lean=lean, torso=(8.0, 0.0, -lean),
                           head=head, hands=both, elbows=elbows)

        last = self.camel.BALK_FRAMES - 1
        return [(0, sit, "even"), (6, held(10, 0, (-10, 0, 0)), "out"), (13, held(7, 3, (-6, -8, 0)), "smooth"),
                (20, held(9, -3, (-8, 8, 0)), "smooth"), (self.camel.BALK_STAMP + 1, held(13, 0, (-4, 0, 0)), "in"),
                (last, sit, "smooth")]

    # ------------------------------------------------------------ actions
    def frames_of(self, keys, right=False):
        """-> [Pose on each frame] of [(frame, Key, how it is reached)]"""
        solved = [(frame, self.body.solve(mirrored(key) if right else key)[1], how) for frame, key, how in keys]
        poses = []
        for frame in range(keys[-1][0] + 1):
            for (f0, a, _), (f1, b, how) in zip(solved, solved[1:]):
                if frame <= f1:
                    poses.append(self.body.pose(blend(a, b, EASE[how]((frame - f0) / (f1 - f0)))))
                    break
        return poses

    def make_actions(self):
        camel = self.camel
        made = {
            "MountL": self.frames_of(self.mount_keys()),
            "MountR": self.frames_of(self.with_standing(self.mount_keys(), 0), right=True),
            "DismountL": self.frames_of(self.dismount_keys()),
            "DismountR": self.frames_of(self.with_standing(self.dismount_keys(), -1), right=True),
            "RideHalt": self.frames_of(self.halt_keys()),
            "RideBalk": self.frames_of(self.balk_keys()),
        }
        for name, frames in (("RideIdle", 60), ("RideWalk", camel.WALK_FRAMES), ("RidePace", camel.PACE_FRAMES),
                             ("RideGallop", camel.GALLOP_FRAMES)):
            made[name] = [self.body.pose(self.body.solve(self.riding(name, frame / frames))[1]) for frame in range(frames)]
        self.armature.animation_data_create()
        bones = [self.armature.pose.bones[name] for name in self.body.names]
        rest = [bone.bone.matrix_local for bone in bones]
        for name, poses in made.items():
            action = bpy.data.actions.new(name)
            action.use_fake_user = True
            self.armature.animation_data.action = action
            before = {}
            for frame, pose in enumerate(poses):
                turns, places = pose.world()
                # the game's limb matrices are the Blender bones' (the armature has 100 game units to its unit)
                world = [Matrix.Translation(TO_BLENDER @ place / 100.0) @ (TO_BLENDER @ turned).to_4x4()
                         for turned, place in zip(turns, places)]
                for limb, bone in enumerate(bones):
                    parent = self.body.parents[limb]
                    if parent < 0:
                        basis = rest[limb].inverted() @ world[limb]
                    else:
                        basis = (rest[parent].inverted() @ rest[limb]).inverted() @ world[parent].inverted() @ world[limb]
                    q = basis.to_quaternion()
                    if limb in before and before[limb].dot(q) < 0:
                        q.negate()
                    before[limb] = q
                    bone.rotation_mode = "QUATERNION"
                    bone.rotation_quaternion = q
                    bone.keyframe_insert("rotation_quaternion", frame=frame)
                    if parent < 0:
                        bone.location = basis.to_translation()
                        bone.keyframe_insert("location", frame=frame)
            self.actions[name] = action
        print(f"rider: {len(self.actions)} animations: " + ", ".join(f"{n} {len(p)}" for n, p in made.items()))

    def with_standing(self, keys, index):
        """keys for the camel's right side, before mirroring: Link stands a little differently there"""
        left, height, ahead = self.standing(-1).root
        frame, key, how = keys[index]
        keys = list(keys)
        keys[index] = (frame, replace(key, root=(-left, height, ahead)), how)
        return keys

    def export(self):
        """-> {path in the game's archive: animation resource}"""
        from fast64_hm.fast64_internal.z64.utility import getOOTScale

        files = {}
        scale = self.armature.scale.copy()
        self.armature.scale = (1.0, 1.0, 1.0)  # (Fast64 takes her at her own scale)
        for name, action in self.actions.items():
            files[f"{OBJECT}/gPlayAsNabooru{name}Anim"] = bc.export_animation(self.armature, action, SKELETON)
        self.armature.scale = scale
        return files

    # ------------------------------------------------------------ pictures
    def render_previews(self, folder, camel_actions):
        look = dict(target=(0.0, -0.3, 5.6), span=17.0)
        with_camel = {"MountL": "Idle", "MountR": "Idle", "DismountL": "Idle", "DismountR": "Idle", "RideIdle": "Idle",
                      "RideWalk": "Walk", "RidePace": "Pace", "RideGallop": "Gallop", "RideHalt": "Halt",
                      "RideBalk": "Balk"}
        for name, action in self.actions.items():
            if name.endswith("R") and name[:-1] + "L" in self.actions:
                continue
            last = int(action.frame_range[1])
            bc.show(self.armature, action)
            bc.show(self.camel_armature, camel_actions[with_camel[name]])
            count = 12 if "ount" in name else 8
            frames = sorted({round(i * last / (count - 1)) for i in range(count)})
            side = 62 if "ount" in name else 90
            bc.render_previews(folder, [(f"{name.lower()}_{frame:02}", side, 8, frame) for frame in frames], columns=4, **look)
            bc.render_previews(folder, [(f"{name.lower()}front_{frame:02}", 8, 10, frame) for frame in frames], columns=4,
                               **look)
        bc.show(self.armature, self.actions["MountR"])
        bc.show(self.camel_armature, camel_actions["Idle"])
        bc.render_previews(folder, [(f"mountr_{frame:02}", -62, 8, frame) for frame in (0, 10, 21, 28, 35, 46)], columns=3,
                           **look)
