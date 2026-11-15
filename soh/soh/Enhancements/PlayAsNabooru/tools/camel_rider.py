"""Nabooru's animations for her camel (Play as Nabooru mod): climbing on, riding, getting off. Used by
build_camel.py, in the Blender scene that holds the camel.

Her skeleton and model are imported from the game's own archive (oot.o2r) with the HarbourMasters Fast64, scaled to
the camel and attached to its seat, so the pictures show her where the game draws her: in the camel's model frame,
carried along by its body (Camel_GetSeat in Camel.cpp). She is posed by what her body does, not by joint angles:
where her hips are and how she leans, where each hand and foot is; arms and legs are bent to reach (see Key). The
poses are keyed on her Blender armature and exported with Fast64 like the camel's.

The camel lies down to let her on and off: she climbs onto it as it lies and is carried up as it rises, and rides it
down before she steps off. Those animations are made against what the camel does meanwhile (build_camel's ForMount
and ForDismount): a key is either "carried" (given in the seat's frame, wherever the camel's body has it then) or
given on the ground, and every frame ends up in the seat's frame, where the game draws her.

All places are in game units: x to the camel's left, y up from the ground under it, z forward.
"""
import math
import struct
import zipfile
from dataclasses import dataclass, field, replace

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
    """What her body does. Places are (x, y, z): "w" as they stand, "b" from her hips in her own frame (to her left,
    up her back, ahead of her). Angles in degrees. A `carried` key is given in the seat's frame: she is on the camel,
    wherever its body is."""
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
    carried: bool = False


@dataclass
class Solved:
    """a Key worked out: places and orientations in one frame, which can be blended and moved to another"""
    root: Vector
    whole: Matrix  # how her body is turned
    torso: tuple
    head: tuple
    wrists: dict
    elbows: dict
    ankles: dict
    knees: dict
    feet: dict  # foot: how it is turned

    def moved(self, turned, shift):
        """the same, where its frame is turned and shifted"""
        def each(points):
            return {side: turned @ point + shift for side, point in points.items()}

        return Solved(turned @ self.root + shift, turned @ self.whole, self.torso, self.head, each(self.wrists),
                      each(self.elbows), each(self.ankles), each(self.knees),
                      {side: turned @ foot for side, foot in self.feet.items()})


def mirrored(key):
    """the same, done from the camel's other side"""
    def flip(spec):
        return None if spec is None else (spec[0], (-spec[1][0], spec[1][1], spec[1][2]))

    def swap(pair, each=flip):
        return {"L": each(pair["R"]), "R": each(pair["L"])}

    return Key(root=(-key.root[0], key.root[1], key.root[2]), yaw=-key.yaw, pitch=key.pitch, lean=-key.lean,
               torso=(key.torso[0], -key.torso[1], -key.torso[2]), head=(key.head[0], -key.head[1], -key.head[2]),
               hands=swap(key.hands), elbows=swap(key.elbows), feet=swap(key.feet), knees=swap(key.knees),
               toes=swap(key.toes, lambda toe: (toe[0], -toe[1])), carried=key.carried)


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
        """-> Solved, in the frame the key is given in"""
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

        wrists, elbows, ankles, knees, feet = {}, {}, {}, {}, {}
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
            else:
                ankle = place(key.feet[side])
            bend = place(key.knees[side]) if key.knees[side] else places[upper] + ry(key.yaw) @ Vector(
                (2.0 * SIDE[side], -4.0, 14.0))
            pose.reach(upper, lower, foot, ankle, bend)
            _, places = pose.world()
            ankles[side], knees[side], feet[side] = places[foot], places[lower], ry(key.yaw + left) @ rx(down)
        return Solved(pose.root.copy(), whole, tuple(key.torso), tuple(key.head), wrists, elbows, ankles, knees, feet)

    def pose(self, solved):
        """-> Pose of a Solved (possibly a blend of two): every arm and leg bent to reach"""
        base, turns0 = self.base, self.base_turns
        pose = base.copy()
        pose.root = solved.root.copy()
        pose.rotations[ROOT] = solved.whole @ base.rotations[ROOT]
        spine = solved.whole @ turn(solved.torso[1], solved.torso[0], solved.torso[2])
        pose.set_world(TORSO, spine @ turns0[TORSO])
        pose.set_world(HEAD, spine @ turn(solved.head[1], solved.head[0], solved.head[2]) @ turns0[HEAD])
        for side, (upper, lower, hand) in ARMS.items():
            pose.reach(upper, lower, hand, solved.wrists[side], solved.elbows[side])
        for side, (upper, lower, foot) in LEGS.items():
            pose.reach(upper, lower, foot, solved.ankles[side], solved.knees[side])
            pose.set_world(foot, solved.feet[side] @ turns0[foot])
        return pose


def blend(a, b, q):
    def each(x, y):
        return tuple(i + (j - i) * q for i, j in zip(x, y))

    def turned(x, y):
        return x.to_quaternion().slerp(y.to_quaternion(), q).to_matrix()

    def both(x, y, how):
        return {side: how(x[side], y[side]) for side in "LR"}

    def places(x, y):
        return both(x, y, lambda i, j: i.lerp(j, q))

    return Solved(a.root.lerp(b.root, q), turned(a.whole, b.whole), each(a.torso, b.torso), each(a.head, b.head),
                  places(a.wrists, b.wrists), places(a.elbows, b.elbows), places(a.ankles, b.ankles),
                  places(a.knees, b.knees), both(a.feet, b.feet, turned))


EASE = {"smooth": ease, "in": lambda q: q * q, "out": lambda q: 1 - (1 - q) ** 2, "even": lambda q: q}


class Rider:
    def __init__(self, game_archive, nabooru_archive, camel, camel_armature, camel_actions):
        """camel: build_camel's module (its measures and gaits)"""
        self.camel, self.camel_armature, self.camel_actions = camel, camel_armature, camel_actions
        size = camel.SIZE
        self.seat = (camel.SEAT[1] * size, camel.SEAT[0] * size)  # height of the seat, how far forward it is
        _, (ftf, ftu), _, _ = camel.HORNS[0]
        _, (btf, btu), _, _ = camel.HORNS[1]
        self.front_horn = Vector((0.0, ftu * size, ftf * size))  # the tops of the saddle's horns
        self.back_horn = Vector((0.0, btu * size, btf * size))
        self.drop = camel.DROP * size  # how far the camel's body comes down when it lies
        self.seats = {}

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
        # to the camel's scale (hers: 100 game units to a Blender unit, its: 10), and carried by its seat
        seat = camel_armature.data.bones["Seat"]
        self.armature.scale = (10.0, 10.0, 10.0)
        self.armature.parent, self.armature.parent_type, self.armature.parent_bone = camel_armature, "BONE", "Seat"
        self.armature.matrix_parent_inverse = (seat.matrix_local @ Matrix.Translation((0, seat.length, 0))).inverted()

        with zipfile.ZipFile(nabooru_archive) as archive:
            frames = read_animation(archive.read(f"{OBJECT}/{BASE_ANIMATION}"), LIMBS)
        self.body = Body(self.armature, frames[0])
        self.actions = {}
        print(f"rider: Nabooru's hips stand {self.body.height:.1f} high; seat {self.seat[0]:.1f} high, "
              f"{self.seat[1]:.1f} forward, {self.drop:.1f} lower when the camel lies")

    def hide(self, hidden):
        for obj in self.objects:
            obj.hide_render = hidden

    def seat_frame(self, sequence, frame):
        """-> (how the seat's frame is turned, where its origin is) on a frame of one of the camel's animations"""
        if (sequence, frame) not in self.seats:
            bc.show(self.camel_armature, self.camel_actions[sequence], frame)
            bpy.context.view_layer.update()
            bone = self.camel_armature.pose.bones["Seat"]
            moved = bone.matrix @ bone.bone.matrix_local.inverted()
            back = TO_BLENDER.inverted()
            # (the camel's armature has 10 game units to a Blender unit)
            self.seats[(sequence, frame)] = (back @ moved.to_3x3() @ TO_BLENDER, back @ moved.to_translation() * 10.0)
        return self.seats[(sequence, frame)]

    # ------------------------------------------------------------ what she does
    def sitting(self, **changes):
        """in the saddle: astride the hump, her legs down its flanks, her left hand on the front horn, her right on
        her thigh"""
        seat_y, seat_z = self.seat
        horn = self.front_horn
        key = Key(root=(0.0, seat_y + 3.6, seat_z - 0.3), torso=(3.0, 0.0, 0.0), carried=True,
                  hands={"L": ("w", (1.4, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-8.8, seat_y + 5.6, seat_z + 7.0))},
                  elbows={"L": ("b", (10.0, 9.0, -3.0)), "R": ("b", (-11.0, 9.0, -5.0))},
                  feet={"L": ("w", (11.6, seat_y - 17.0, seat_z + 7.0)), "R": ("w", (-11.6, seat_y - 17.0, seat_z + 7.0))},
                  knees={"L": ("w", (9.5, seat_y + 0.4, seat_z + 10.5)), "R": ("w", (-9.5, seat_y + 0.4, seat_z + 10.5))},
                  toes={"L": (22.0, 8.0), "R": (22.0, -8.0)})
        return replace(key, **changes)

    def holding(self, **changes):
        """sitting with both hands on the front horn"""
        horn = self.front_horn
        return self.sitting(**{
            "hands": {"L": ("w", (1.5, horn.y + 2.2, horn.z - 2.4)), "R": ("w", (-1.5, horn.y + 2.2, horn.z - 2.4))},
            "elbows": {"L": ("b", (10.0, 8.5, -2.0)), "R": ("b", (-10.0, 8.5, -2.0))}, **changes})

    def standing(self, side, yaw=0.0):
        """beside the camel, facing its way (or `yaw` from it): where Link's own mounting starts and his dismounting
        ends (from the rider's place En_Horse keeps: z_player.c's D_80854578)"""
        left, ahead = (35.17, 6.61) if side > 0 else (-34.16, 7.91)
        return Key(root=(left, self.body.height, self.seat[1] + ahead), yaw=yaw)

    def astride_keys(self):
        """half on the lying camel, from its left: on the ground at its flank with her hands on the saddle's horns;
        pushed up, her left foot on its folded foreleg; her right leg swung over its back. (In this order climbing on,
        the other way round getting off.) Places on the ground, by where the lying camel is."""
        h, low = self.body.height, self.drop
        seat_y, seat_z = self.seat[0] - low, self.seat[1]
        front, back = self.front_horn - Vector((0, low, 0)), self.back_horn - Vector((0, low, 0))
        grip_front, grip_back = ("w", (2.4, front.y + 0.4, front.z)), ("w", (2.4, back.y + 0.4, back.z))
        at_flank = Key(root=(20.5, h - 1.5, seat_z + 1.0), yaw=-90, pitch=5, torso=(4, 0, 0), head=(-12, 0, 0),
                       hands={"L": grip_front, "R": grip_back},
                       elbows={"L": ("b", (9.0, 6.0, 6.0)), "R": ("b", (-9.0, 6.0, 6.0))})
        pushed_up = Key(root=(11.5, seat_y - 4.0, seat_z + 0.5), yaw=-78, pitch=24, torso=(8, 8, 0), head=(-20, 6, 0),
                        hands={"L": grip_front, "R": grip_back},
                        elbows={"L": ("b", (10.0, 9.0, 3.0)), "R": ("b", (-10.0, 9.0, 3.0))},
                        feet={"L": ("w", (15.0, 15.0, seat_z + 12.0)), "R": ("w", (17.5, 27.0, seat_z - 9.0))},
                        knees={"L": ("w", (16.0, seat_y - 9.0, seat_z + 13.0)), "R": None},
                        toes={"L": (10.0, 0.0), "R": (40.0, 0.0)})
        leg_over = Key(root=(4.0, seat_y + 5.4, seat_z - 0.4), yaw=-42, pitch=30, torso=(8, 14, 0), head=(-24, 20, 0),
                       hands={"L": grip_front, "R": ("w", (3.0, seat_y + 2.5, seat_z - 6.0))},
                       elbows={"L": ("b", (11.0, 10.0, 2.0)), "R": ("b", (-10.0, 8.0, -4.0))},
                       feet={"L": ("w", (14.0, seat_y - 20.0, seat_z + 7.0)), "R": ("w", (-3.0, seat_y + 9.5, seat_z - 19.0))},
                       knees={"L": None, "R": ("w", (2.0, seat_y + 12.0, seat_z - 9.0))},
                       toes={"L": (26.0, 0.0), "R": (40.0, 0.0)})
        return at_flank, pushed_up, leg_over

    def mount_keys(self):
        """climbing on from the camel's left as it lies: up to its flank, hands on the saddle's horns, a foot on its
        folded foreleg and up, her right leg over its back, into the saddle. Then it gets up under her, rump first:
        she is tipped forward, leans back against it, and comes upright as its forelegs straighten."""
        camel = self.camel
        h, seat_z = self.body.height, self.seat[1]
        at_flank, pushed_up, leg_over = self.astride_keys()
        on, up = camel.STEP_ON_FRAMES, camel.STEP_ON_FRAMES + camel.RISE_FRAMES - 1
        return [
            # (she has walked up to its flank: she starts facing it, where Link is turned its way)
            (0, self.standing(1, -90), "even"),
            (6, Key(root=(28.0, h - 0.6, seat_z + 3.5), yaw=-90, torso=(3, 0, 0), head=(-10, 0, 0)), "smooth"),
            (11, at_flank, "smooth"),
            (15, pushed_up, "smooth"),
            (19, leg_over, "smooth"),
            (on, self.holding(pitch=10, torso=(6.0, 0.0, 0.0)), "smooth"),
            (on + 8, self.holding(pitch=-4, torso=(0.0, 0.0, 0.0), head=(4, 0, 0)), "smooth"),
            (on + 17, self.holding(pitch=-13, torso=(-3.0, 0.0, 0.0), head=(8, 0, 0)), "smooth"),
            (on + 27, self.holding(pitch=7, torso=(6.0, 0.0, 0.0), head=(-6, 0, 0)), "smooth"),
            (up, self.sitting(), "smooth"),
            (camel.MOUNT_FRAMES - 1, self.sitting(), "even"),
        ]

    def dismount_keys(self):
        """getting off to the camel's left: she holds on as it lies down under her, front first; then her right leg
        back over, a foot down on its foreleg, down to the ground at its flank, and away from it"""
        camel = self.camel
        h, seat_z = self.body.height, self.seat[1]
        at_flank, pushed_up, leg_over = self.astride_keys()
        down = camel.KNEEL_FRAMES
        return [
            (0, self.sitting(), "even"),
            (5, self.holding(pitch=-3), "smooth"),
            (12, self.holding(pitch=-13, torso=(-3.0, 0.0, 0.0), head=(8, 0, 0)), "smooth"),
            (23, self.holding(pitch=-2), "smooth"),
            (down, self.holding(pitch=8, torso=(6.0, 0.0, 0.0)), "smooth"),
            (down + 4, leg_over, "smooth"),
            (down + 8, pushed_up, "smooth"),
            (down + 12, replace(at_flank, hands={"L": None, "R": None}), "smooth"),
            (down + 17, Key(root=(28.5, h - 0.6, seat_z + 4.0), yaw=-55, torso=(3, 0, 0), head=(-4, 16, 0)), "smooth"),
            (camel.DISMOUNT_FRAMES - 1, self.standing(1), "smooth"),
        ]

    def riding(self, name, t):
        """her seat on a moving camel: she is carried by its body, so this is only what she does against it. She
        keeps her own back upright as it rolls, gives with its bounce, leans into its gallop."""
        sit = self.sitting()
        seat_y, seat_z = self.seat

        def legs(swing_l, swing_r, grip=0.0):
            """her lower legs swinging forward by these, her knees drawn up by `grip`"""
            return dict(feet={"L": ("w", (11.6, seat_y - 17.0 + grip, seat_z + 7.0 + swing_l)),
                              "R": ("w", (-11.6, seat_y - 17.0 + grip, seat_z + 7.0 + swing_r))},
                        knees={"L": ("w", (9.5, seat_y + 0.4 + grip, seat_z + 10.5)),
                               "R": ("w", (-9.5, seat_y + 0.4 + grip, seat_z + 10.5))})

        if name == "RideIdle":
            breath = wave(t, 0.0, 2)
            look = wave(t, 0.1)
            return replace(sit, torso=(3.0 + 1.0 * breath, 0.0, 0.0), head=(-0.6 * breath, 5.0 * look, 0.0),
                           root=(0.0, seat_y + 3.6 + 0.08 * breath, seat_z - 0.3))
        if name == "RideWalk":
            sway = wave(t, 0.36)  # the camel's own: +1 over its left legs
            nod = wave(t, 0.3, 2)
            return replace(sit, root=(-0.6 * sway, seat_y + 3.6 + 0.15 * nod, seat_z - 0.3), lean=-2.7 * sway,
                           torso=(4.0 + 1.6 * nod, 0.0, -1.6 * sway), head=(-1.5 * nod, 0.0, 1.8 * sway),
                           **legs(1.2 * wave(t, 0.5), -1.2 * wave(t, 0.5)))
        if name == "RidePace":
            sway = wave(t, 0.27)
            bounce = wave(t, 0.22, 2)
            return replace(sit, root=(-1.0 * sway, seat_y + 3.7 + 0.45 * bounce, seat_z - 0.4), lean=-3.9 * sway, pitch=2,
                           torso=(7.0 + 2.2 * bounce, 0.0, -2.6 * sway), head=(-4.0 - 2.0 * bounce, 0.0, 2.8 * sway),
                           hands={"L": sit.hands["L"], "R": ("w", (-8.4, seat_y + 5.9 + 0.3 * bounce, seat_z + 6.6))},
                           **legs(1.8 * wave(t, 0.45), -1.8 * wave(t, 0.45), 0.6))
        if name == "RideGallop":
            rock = wave(t, 0.95)  # the camel's own pitching
            lift = wave(t, 0.2)
            return self.holding(root=(0.0, seat_y + 4.6 + 0.9 * lift, seat_z - 1.4), pitch=17 - 4.6 * rock,
                                lean=-1.3 * wave(t, 0.3), torso=(13.0 + 4.0 * wave(t, 0.3), 0.0, 0.0),
                                head=(-22.0 - 3.0 * wave(t, 0.4), 0.0, 0.0), toes={"L": (22.0, 10.0), "R": (22.0, -10.0)},
                                **legs(-2.5 + 1.2 * rock, -2.5 + 1.2 * rock, 1.6))
        raise KeyError(name)

    def halt_keys(self):
        """reined in: thrown forward onto the horn as the camel sits back, hauling back against it, settling"""
        sit = self.sitting()
        seat_y, seat_z = self.seat
        thrown = self.holding(root=(0.0, seat_y + 3.9, seat_z + 0.6), pitch=17, torso=(15.0, 0.0, 0.0), head=(-20.0, 0, 0),
                              elbows={"L": ("b", (11.5, 6.0, 0.0)), "R": ("b", (-11.5, 6.0, 0.0))})
        hauling = self.holding(root=(0.0, seat_y + 3.6, seat_z - 1.4), pitch=-9, torso=(-4.0, 0.0, 0.0), head=(6.0, 0, 0),
                               toes={"L": (4.0, 12.0), "R": (4.0, -12.0)})
        last = self.camel.HALT_FRAMES - 1
        return [(0, self.riding("RideGallop", 0.0), "even"), (8, thrown, "out"), (22, hauling, "smooth"),
                (40, self.holding(pitch=-3, torso=(1.0, 0.0, 0.0)), "smooth"), (last - 6, sit, "smooth"), (last, sit, "even")]

    def balk_keys(self):
        """the camel balks: she holds on with both hands, thrown about a little by its tossing"""
        sit = self.sitting()
        seat_y, seat_z = self.seat

        def held(pitch, lean, head):
            return self.holding(root=(0.0, seat_y + 3.7, seat_z - 0.2), pitch=pitch, lean=lean, torso=(8.0, 0.0, -lean),
                                head=head)

        last = self.camel.BALK_FRAMES - 1
        return [(0, sit, "even"), (6, held(10, 0, (-10, 0, 0)), "out"), (13, held(7, 3, (-6, -8, 0)), "smooth"),
                (20, held(9, -3, (-8, 8, 0)), "smooth"), (self.camel.BALK_STAMP + 1, held(13, 0, (-4, 0, 0)), "in"),
                (last, sit, "smooth")]

    # ------------------------------------------------------------ actions
    def frames_of(self, keys, sequence=None, right=False):
        """-> [Pose on each frame, in the seat's frame] of [(frame, Key, how it is reached)]. `sequence`: what the
        camel does meanwhile (one of its animations); without one it stands as at rest."""
        def seat(frame):
            return self.seat_frame(sequence, frame) if sequence else (Matrix.Identity(3), Vector())

        solved = []
        for frame, key, how in keys:
            answer = self.body.solve(mirrored(key) if right else key)
            solved.append((frame, answer.moved(*seat(frame)) if key.carried else answer, how))
        poses = []
        for frame in range(keys[-1][0] + 1):
            for (f0, a, _), (f1, b, how) in zip(solved, solved[1:]):
                if frame <= f1:
                    turned, shift = seat(frame)
                    back = turned.inverted()
                    poses.append(self.body.pose(blend(a, b, EASE[how]((frame - f0) / (f1 - f0))).moved(back, -(back @ shift))))
                    break
        return poses

    def make_actions(self):
        camel = self.camel
        made = {
            "MountL": self.frames_of(self.mount_keys(), "ForMount"),
            "MountR": self.frames_of(self.with_standing(self.mount_keys(), 0), "ForMount", right=True),
            "DismountL": self.frames_of(self.dismount_keys(), "ForDismount"),
            "DismountR": self.frames_of(self.with_standing(self.dismount_keys(), -1), "ForDismount", right=True),
            "RideHalt": self.frames_of(self.halt_keys()),
            "RideBalk": self.frames_of(self.balk_keys()),
        }
        for name, frames in (("RideIdle", 60), ("RideWalk", camel.WALK_FRAMES), ("RidePace", camel.PACE_FRAMES),
                             ("RideGallop", camel.GALLOP_FRAMES)):
            made[name] = [self.body.pose(self.body.solve(self.riding(name, frame / frames))) for frame in range(frames)]
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
        files = {}
        scale = self.armature.scale.copy()
        self.armature.scale = (1.0, 1.0, 1.0)  # (Fast64 takes her at her own scale)
        for name, action in self.actions.items():
            files[f"{OBJECT}/gPlayAsNabooru{name}Anim"] = bc.export_animation(self.armature, action, SKELETON)
        self.armature.scale = scale
        return files

    # ------------------------------------------------------------ pictures
    def render_previews(self, folder):
        look = dict(target=(0.0, -0.3, 5.0), span=17.0)
        with_camel = {"MountL": "ForMount", "MountR": "ForMount", "DismountL": "ForDismount", "DismountR": "ForDismount",
                      "RideIdle": "Idle", "RideWalk": "Walk", "RidePace": "Pace", "RideGallop": "Gallop",
                      "RideHalt": "Halt", "RideBalk": "Balk"}
        for name, action in self.actions.items():
            if name.endswith("R") and name[:-1] + "L" in self.actions:
                continue
            last = int(action.frame_range[1])
            bc.show(self.armature, action)
            bc.show(self.camel_armature, self.camel_actions[with_camel[name]])
            count = 16 if "ount" in name else 8
            frames = sorted({round(i * last / (count - 1)) for i in range(count)})
            side = 62 if "ount" in name else 90
            bc.render_previews(folder, [(f"{name.lower()}_{frame:02}", side, 8, frame) for frame in frames], columns=4, **look)
            bc.render_previews(folder, [(f"{name.lower()}front_{frame:02}", 8, 10, frame) for frame in frames], columns=4,
                               **look)
        bc.show(self.armature, self.actions["MountR"])
        bc.show(self.camel_armature, self.camel_actions["ForMount"])
        bc.render_previews(folder, [(f"mountr_{frame:02}", -62, 8, frame) for frame in (0, 11, 15, 19, 22, 40)], columns=3,
                           **look)
