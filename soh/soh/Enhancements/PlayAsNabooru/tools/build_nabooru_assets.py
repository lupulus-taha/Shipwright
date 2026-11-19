#!/usr/bin/env python3
"""Builds the extra assets for the "Play as Nabooru" enhancement from an extracted OoT decomp.

No game data is stored in the repository: everything is generated from the user's own decomp extraction
(`make setup` / `tools.assets.extract`) and written to a mod archive for SoH's `mods` folder.

Gerudo NPCs share one skeleton rig: the spear guard (object_gla) and the twin-scimitar fighter (object_geldb) have
exactly Nabooru's joint offsets, only with a different limb order and a few zero-offset helper limbs (head/hand
roots, veil, weapons). Their animations are retargeted by copying limb rotations and folding the helper limbs into
the limb they wrap. Every generated animation is checked: each mapped joint must land exactly on the source's.

usage: build_nabooru_assets.py --out play_as_nabooru.o2r [--decomp ~/oot] [--version pal-1.0] [--debug-c DIR]
"""
import argparse
import itertools
import re
import math
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import icons  # noqa: E402
import names  # noqa: E402
import ootanim  # noqa: E402
from ootanim import fk, parse_anim, parse_skeleton, rot_zyx, zyx_from_rot  # noqa: E402

NB = ("object_nb", "gNabooruSkel", "NABOORU_LIMB_")
NB_LIMBS = 18
NB_L_ARM = (8, 9, 10)  # upper arm, forearm, hand
NB_R_ARM = (11, 12, 13)
NB_L_FOOT, NB_R_FOOT = 3, 6

# How to build each Nabooru limb's local rotation from source limb rotations G[k]:
#   int k          : copy G[k]
#   ("mul", a, b)  : G[a] @ G[b]            (b is a zero-offset child of a, folded into one limb)
#   ("inv", a, b)  : G[a]^-1 @ G[b]         (b hangs off a's parent in the source, off a in Nabooru)
#   "static"       : keep Nabooru's own pose from her walk cycle


@dataclass
class Source:
    obj: str
    skel: str
    prefix: str
    to_nb: dict
    # Nabooru limb -> source limb whose world position must match (verification)
    joint_pairs: dict = field(default_factory=dict)


GUARD = Source(
    "object_gla", "gObjectGlaSkel", "OBJECT_GLA_LIMB_",
    {
        0: 0, 1: 14, 2: 15, 3: 16, 4: 17, 5: 18, 6: 19, 7: 1,
        8: 11, 9: 12, 10: 13,  # left arm, forearm, hand
        11: 6, 12: 7, 13: ("mul", 8, 9),  # right arm, forearm, hand root * hand
        14: ("mul", 2, 5),  # head root * head
        15: "static",
        16: ("inv", 5, 3),  # ponytail
        17: 20,  # pelvis
    },
    {1: 14, 2: 15, 3: 16, 4: 17, 5: 18, 6: 19, 7: 1, 8: 11, 9: 12, 10: 13, 11: 6, 12: 7, 13: 8, 14: 2, 16: 3},
)

FIGHTER = Source(
    "object_geldb", "gGerudoRedSkel", "GERUDO_RED_LIMB_",
    {
        0: 0, 1: 16, 2: 17, 3: 18, 4: 19, 5: 20, 6: 21, 7: 1,
        8: 11, 9: 12, 10: ("mul", 13, 14),  # left arm, forearm, wrist * hand
        11: 6, 12: 7, 13: ("mul", 8, 9),  # right arm, forearm, wrist * hand
        14: ("mul", 2, 5),  # neck * head
        15: "static",
        16: ("inv", 5, 3),  # ponytail
        17: 22,  # waist
    },
    {1: 16, 2: 17, 3: 18, 4: 19, 5: 20, 6: 21, 7: 1, 8: 11, 9: 12, 10: 13, 11: 6, 12: 7, 13: 8, 14: 2, 16: 3},
)

# The white-clothed Gerudo (object_ge1) has the same limb order for her first 15 limbs, but her own proportions: only
# limb rotations carry over (no joint check), and the root height comes from Nabooru's stance.
WHITE = Source(
    "object_ge1", "gGerudoWhiteSkel", "GERUDO_WHITE_LIMB_",
    {**{i: i for i in range(15)}, 15: "static", 16: "static", 17: "static"},
)


@dataclass
class Job:
    out: str
    source: Source
    anim: str
    arms_from_walk: bool = False  # replace arms with Nabooru's own (phase-matched, exaggerated) walk swing
    write: bool = True  # False: only an ingredient of generated animations


# Fighter (twin scimitars) sword limbs: each hangs off the wrist next to the hand, with zero offset.
# (wrist, hand, sword) source limb indices, left then right.
FIGHTER_SWORDS = ((13, 14, 15), (8, 9, 10))

JOBS = [
    Job("gPlayAsNabooruRunAnim", GUARD, "gObjectGlaRunAnim", arms_from_walk=True),
    # twin-scimitar fighter: attacks, defense, movement, reactions
    Job("gPlayAsNabooruSlashAnim", FIGHTER, "gGerudoRedSlashAnim"),
    Job("gPlayAsNabooruSpinAttackAnim", FIGHTER, "gGerudoRedSpinAttackAnim"),
    Job("gPlayAsNabooruFlipAnim", FIGHTER, "gGerudoRedFlipAnim"),
    Job("gPlayAsNabooruBlockAnim", FIGHTER, "gGerudoRedBlockAnim"),
    Job("gPlayAsNabooruDefeatAnim", FIGHTER, "gGerudoRedDefeatAnim"),
    Job("gPlayAsNabooruDamageAnim", FIGHTER, "gGerudoRedDamageAnim"),
    Job("gPlayAsNabooruFightWalkAnim", FIGHTER, "gGerudoRedWalkAnim"),
    Job("gPlayAsNabooruSidestepAnim", FIGHTER, "gGerudoRedSidestepAnim"),
    Job("gPlayAsNabooruJumpAnim", FIGHTER, "gGerudoRedJumpAnim"),
    Job("gPlayAsNabooruStandAnim", FIGHTER, "gGerudoRedStandAnim"),
    Job("gPlayAsNabooruNeutralAnim", FIGHTER, "gGerudoRedNeutralAnim"),
    # spear guard (several of these are unused in the game)
    Job("gPlayAsNabooruGuardSlashAnim", GUARD, "gObjectGlaAttackAnim"),
    Job("gPlayAsNabooruGuardSlashRecoverAnim", GUARD, "gObjectGlaRecoverFromAttackAnim"),
    Job("gPlayAsNabooruFaintAnim", GUARD, "gObjectGlaFaintAnim"),
    Job("gPlayAsNabooruCrouchAnim", GUARD, "gObjectGlaKneelAnim"),
    Job("gPlayAsNabooruCrouchingAnim", GUARD, "gObjectGlaKneelingAnim"),
    Job("gPlayAsNabooruGuardIdleAnim", GUARD, "gObjectGlaIdleAnim", write=False),
    Job("gPlayAsNabooruGuardWalkAnim", GUARD, "gObjectGlaWalkAnim", write=False),
    # white-clothed Gerudo (unarmed)
    Job("gPlayAsNabooruWhiteIdleAnim", WHITE, "gGerudoWhiteIdleAnim", write=False),
    Job("gPlayAsNabooruWhiteFoldArmsAnim", WHITE, "gGerudoWhiteUnusedFoldingArmsAnim", write=False),
]

# How much to exaggerate her walk arm swing when running (upper arm, forearm, hand).
RUN_ARM_SWING_GAIN = (1.4, 1.2, 1.0)
# Her walk's torso twist and head sway, layered on the run's own lean (Nabooru limb -> gain).
NB_TORSO, NB_HEAD = 7, 14
NB_BLANK = 15
RUN_CORE_SWAY_GAIN = {NB_TORSO: 2.3, NB_HEAD: 1.0}
# With scimitars in hand her shoulders twist and roll much less (they swung the blades about): half the twist, a third
# of the roll.
ARMED_RUN_CORE_SWAY_GAIN = {NB_TORSO: 0.8, NB_HEAD: 1.0}

RESOURCE_HEADER = bytes.fromhex("00000000" "4d4e414f" "00000000" "efbeaddeefbeadde") + bytes(64 - 20)
TEXTURE_HEADER = bytes.fromhex("00000000" "5845544f" "00000000" "efbeaddeefbeadde") + bytes(64 - 20)
TEXTURE_RGBA32 = 1
TEXTURE_IA4 = 7


def encode_texture(rgba):
    """SoH binary texture, 32-bit RGBA."""
    h, w = rgba.shape[:2]
    pixels = icons.to_rgba32(rgba).tobytes()
    return TEXTURE_HEADER + struct.pack("<4I", TEXTURE_RGBA32, w, h, len(pixels)) + pixels


# ---------------------------------------------------------------- rotation helpers
def quat_from_rot(R):
    w = math.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    x = math.copysign(math.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2, R[2, 1] - R[1, 2])
    y = math.copysign(math.sqrt(max(0.0, 1 - R[0, 0] + R[1, 1] - R[2, 2])) / 2, R[0, 2] - R[2, 0])
    z = math.copysign(math.sqrt(max(0.0, 1 - R[0, 0] - R[1, 1] + R[2, 2])) / 2, R[1, 0] - R[0, 1])
    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)


def rot_from_quat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def quat_mul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_conj(q):
    return q * np.array([1, -1, -1, -1])


def quat_pow(q, k):
    q = q if q[0] >= 0 else -q
    angle = 2 * math.acos(min(1.0, q[0]))
    if angle < 1e-9:
        return np.array([1.0, 0, 0, 0])
    axis = q[1:] / math.sin(angle / 2)
    a = angle * k / 2
    return np.concatenate([[math.cos(a)], axis * math.sin(a)])


def slerp(a, b, t):
    if a @ b < 0:
        b = -b
    return quat_mul(a, quat_pow(quat_mul(quat_conj(a), b), t))


def quat_mean(qs):
    ref = qs[0]
    acc = sum((q if q @ ref >= 0 else -q) for q in qs)
    return acc / np.linalg.norm(acc)


# ---------------------------------------------------------------- retargeting
def retarget(source: Source, src_anim, nb_static_frame):
    out = np.zeros((src_anim.frames, NB_LIMBS + 1, 3), int)
    n_src = src_anim.table.shape[1] - 1
    for f in range(src_anim.frames):
        t = src_anim.table[f]
        G = [rot_zyx(t[k + 1]) for k in range(n_src)]
        out[f, 0] = t[0]  # root translation (same rig, same proportions)
        for nb, src in source.to_nb.items():
            if isinstance(src, int):
                out[f, nb + 1] = t[src + 1]
            elif src == "static":
                out[f, nb + 1] = nb_static_frame[nb + 1]
            elif src[0] == "mul":
                out[f, nb + 1] = zyx_from_rot(G[src[1]] @ G[src[2]])
            elif src[0] == "inv":
                out[f, nb + 1] = zyx_from_rot(G[src[1]].T @ G[src[2]])
    return out


def verify(source: Source, nb_limbs, src_limbs, nb_table, src_table, skip=()):
    worst = 0.0
    for f in range(len(nb_table)):
        _, Pn = fk(nb_limbs, nb_table[f])
        _, Pg = fk(src_limbs, src_table[f])
        for n, g in source.joint_pairs.items():
            if n not in skip:
                worst = max(worst, float(np.linalg.norm(Pn[n] - Pg[g])))
    return worst


def left_foot_forward_phase(limbs, table):
    """fraction of the cycle where the left foot is furthest forward (+Z) relative to the right foot."""
    lead = []
    for f in range(len(table)):
        _, P = fk(limbs, table[f])
        lead.append(P[NB_L_FOOT][2] - P[NB_R_FOOT][2])
    return int(np.argmax(lead)) / len(table)


def overlay_walk_motion(run_table, nb_limbs, walk_table, limb_gains, keep_run_pose):
    """Gives limbs of a run Nabooru's own walk motion, phase-matched to the run's legs and exaggerated by `gain`.

    The motion is her walk's deviation from its mean pose. It is applied around her walk's mean pose (arms: she
    shouldn't keep the source's weapon grip) or around her walk's mean pose plus the run's lean (torso/head). The
    run's average twist around the spine is dropped: the spear guard runs with her shoulders turned ~41 degrees to
    the side (and her head turned back), which only makes sense while carrying a spear."""
    out = run_table.copy()
    run_phase = left_foot_forward_phase(nb_limbs, run_table)
    walk_phase = left_foot_forward_phase(nb_limbs, walk_table)
    nw, nr = len(walk_table), len(run_table)
    for limb, gain in limb_gains.items():
        qs = [quat_from_rot(rot_zyx(walk_table[w, limb + 1])) for w in range(nw)]
        walk_mean = quat_mean(qs)
        base = walk_mean
        if keep_run_pose:
            run_mean = quat_mean([quat_from_rot(rot_zyx(run_table[f, limb + 1])) for f in range(nr)])
            # run_mean = walk_mean * D in the limb's own axes; X runs along the spine (twist), Y/Z are bend/lean.
            D = rot_from_quat(walk_mean).T @ rot_from_quat(run_mean)
            _, bend, lean = zyx_from_rot(D)
            base = quat_mul(walk_mean, quat_from_rot(rot_zyx((0, bend, lean))))
        for f in range(nr):
            phase = (f / nr - run_phase + walk_phase) % 1.0
            w = phase * nw
            w0 = int(w) % nw
            q = slerp(qs[w0], qs[(w0 + 1) % nw], w - int(w))
            q = quat_mul(base, quat_pow(quat_mul(quat_conj(walk_mean), q), gain))
            out[f, limb + 1] = zyx_from_rot(rot_from_quat(q))
    return out


def arms_from_walk(run_table, nb_limbs, walk_table, core_gain=None):
    """Arms swing like her walk (exaggerated); torso and head get her walk's twist/sway (by `core_gain`, default
    RUN_CORE_SWAY_GAIN) on top of the run's lean."""
    arms = {limb: gain for side in (NB_L_ARM, NB_R_ARM) for limb, gain in zip(side, RUN_ARM_SWING_GAIN)}
    out = overlay_walk_motion(run_table, nb_limbs, walk_table, arms, keep_run_pose=False)
    return overlay_walk_motion(out, nb_limbs, walk_table, core_gain or RUN_CORE_SWAY_GAIN, keep_run_pose=True)


def shoulder_sway(nb_limbs, table):
    """How far her shoulder line turns (yaw) and tilts (roll) over a cycle, in degrees."""
    yaw, roll = [], []
    for f in table:
        _, P = fk(nb_limbs, f)
        d = P[NB_L_ARM[0]] - P[NB_R_ARM[0]]
        yaw.append(math.degrees(math.atan2(-d[2], d[0])))
        roll.append(math.degrees(math.atan2(d[1], d[0])))
    return max(yaw) - min(yaw), max(roll) - min(roll)


def sword_table(src_anim):
    """Companion 'animation' with the scimitars relative to Nabooru's hands:
    joint 0 = unused root translation, joint 1 = left sword, joint 2 = right sword.
    Nabooru's hand = wrist * hand, the sword hangs off the wrist: sword relative to her hand = hand^-1 * sword."""
    out = np.zeros((src_anim.frames, 3, 3), int)
    for f in range(src_anim.frames):
        t = src_anim.table[f]
        for j, (_, hand, sword) in enumerate(FIGHTER_SWORDS):
            out[f, j + 1] = zyx_from_rot(rot_zyx(t[hand + 1]).T @ rot_zyx(t[sword + 1]))
    return out


def verify_swords(nb_limbs, src_limbs, nb_table, src_table, swords):
    """sword world orientation through Nabooru's hand must equal the fighter's sword limb."""
    worst = 0.0
    for f in range(len(nb_table)):
        Wn, _ = fk(nb_limbs, nb_table[f])
        Wg, _ = fk(src_limbs, src_table[f])
        for j, (nb_hand, (_, _, sword)) in enumerate(zip((10, 13), FIGHTER_SWORDS)):
            worst = max(worst, float(np.abs(Wn[nb_hand] @ rot_zyx(swords[f, j + 1]) - Wg[sword]).max()))
    return worst


# ---------------------------------------------------------------- output
TIRED_FRAMES = 30
TIRED_TORSO_BEND = (12.0, 3.5)  # degrees forward: mean, breathing amplitude
TIRED_HEAD_BEND = (8.0, 2.5)


def bend_forward(table_frame, nb_limbs, limb, degrees):
    """Tilt `limb` (and everything above it) forward about her side-to-side axis, pivoting at the limb."""
    W, _ = fk(nb_limbs, table_frame)
    parent = nb_limbs[limb].parent
    a = math.radians(degrees)
    bend = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])  # +Z is forward
    local = W[parent].T @ bend @ W[limb]
    table_frame[limb + 1] = zyx_from_rot(local)


def turn(table_frame, nb_limbs, limb, degrees):
    """Turn `limb` (and everything above it) about the vertical axis, pivoting at the limb."""
    W, _ = fk(nb_limbs, table_frame)
    parent = nb_limbs[limb].parent
    a = math.radians(degrees)
    yaw = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    table_frame[limb + 1] = zyx_from_rot(W[parent].T @ yaw @ W[limb])


def rotate_world(table_frame, nb_limbs, limb, axis, degrees):
    """Rotate `limb` (and everything below it in the hierarchy) about a model-space axis, pivoting at the limb.
    Model space: +X her left, +Y up, +Z forward."""
    W, _ = fk(nb_limbs, table_frame)
    R = rot_from_quat(np.concatenate([[math.cos(math.radians(degrees) / 2)],
                                      np.asarray(axis, float) / np.linalg.norm(axis) * math.sin(math.radians(degrees) / 2)]))
    parent = nb_limbs[limb].parent
    world = R @ W[limb]
    table_frame[limb + 1] = zyx_from_rot(world if parent < 0 else W[parent].T @ world)


X_AXIS, Y_AXIS, Z_AXIS = (1, 0, 0), (0, 1, 0), (0, 0, 1)


def make_tired(base_frame, nb_limbs):
    """Critical-health idle: her relaxed stance, bent forward and heaving for breath."""
    out = np.repeat(base_frame[None], TIRED_FRAMES, axis=0).copy()
    for i in range(TIRED_FRAMES):
        breath = math.sin(2 * math.pi * i / TIRED_FRAMES)
        bend_forward(out[i], nb_limbs, NB_TORSO, TIRED_TORSO_BEND[0] + TIRED_TORSO_BEND[1] * breath)
        bend_forward(out[i], nb_limbs, NB_HEAD, TIRED_HEAD_BEND[0] + TIRED_HEAD_BEND[1] * math.sin(
            2 * math.pi * i / TIRED_FRAMES + 0.6))
    return out


RELAXED_FRAMES = 60  # 3 seconds
RELAXED_ARM_SWAY = 0.12  # fraction of her walk's arm swing
RELAXED_BREATH = 1.2  # degrees of torso bend
RELAXED_LOOK = 5.0  # degrees her head turns either way


def make_relaxed(stand_table, walk_table, nb_limbs):
    """Empty-handed idle: the fighter's stance with her arms hanging at her sides (her walk's average arm pose),
    breathing, arms swaying a little and her head slowly looking around."""
    walk_q = {limb: [quat_from_rot(rot_zyx(frame[limb + 1])) for frame in walk_table] for limb in NB_L_ARM + NB_R_ARM}
    mean_q = {limb: quat_mean(qs) for limb, qs in walk_q.items()}
    out = np.repeat(stand_table[:1], RELAXED_FRAMES, axis=0).copy()
    for i in range(RELAXED_FRAMES):
        t = i / RELAXED_FRAMES
        k = int(t * len(walk_table)) % len(walk_table)
        for limb in NB_L_ARM + NB_R_ARM:
            out[i, limb + 1] = zyx_from_rot(rot_from_quat(slerp(mean_q[limb], walk_q[limb][k], RELAXED_ARM_SWAY)))
        bend_forward(out[i], nb_limbs, NB_TORSO, RELAXED_BREATH * math.sin(2 * math.pi * 2 * t))
        turn(out[i], nb_limbs, NB_HEAD, RELAXED_LOOK * math.sin(2 * math.pi * t))
    return out


def make_guard(block_table, neutral_table, nb_limbs):
    """Standing guard: the fighter's crossed-scimitar block (first frame of her block), but with the stance's upright
    torso and head instead of hunching over her blades. Her arms keep their pose relative to the torso."""
    out = block_table[:1].copy()
    Wb, _ = fk(nb_limbs, out[0])
    Wn, _ = fk(nb_limbs, neutral_table[0])
    out[0, NB_TORSO + 1] = zyx_from_rot(Wb[nb_limbs[NB_TORSO].parent].T @ Wn[NB_TORSO])
    out[0, NB_HEAD + 1] = neutral_table[0, NB_HEAD + 1]
    return out


NB_LEGS = (1, 2, 3, 4, 5, 6)


def mean_frame(table):
    """Each joint's average rotation over a cycle (root translation from frame 0)."""
    out = table[0].copy()
    for j in range(1, table.shape[1]):
        q = quat_mean([quat_from_rot(rot_zyx(f[j])) for f in table])
        out[j] = zyx_from_rot(rot_from_quat(q))
    return out


def forward_lean(nb_limbs, table):
    """Average forward lean (degrees) of the line from her torso joint to her head."""
    leans = []
    for f in table:
        _, P = fk(nb_limbs, f)
        d = P[NB_HEAD] - P[NB_TORSO]
        leans.append(math.degrees(math.atan2(d[2], d[1])))
    return float(np.mean(leans))


CROSSED_FRAMES = 80
CROSSED_BREATH = 1.0  # degrees of torso bend
CROSSED_LOOK = 9.0  # degrees her head turns either way


def with_arms(base_frame, arms_frame):
    out = base_frame.copy()
    for limb in NB_L_ARM + NB_R_ARM:
        out[limb + 1] = arms_frame[limb + 1]
    return out


def make_crossed(white_frame, stand_frame, nb_limbs):
    """Empty-handed idle: the white-clothed Gerudo's folded arms over Nabooru's stance, breathing and looking around."""
    base = with_arms(stand_frame, white_frame)
    out = np.repeat(base[None], CROSSED_FRAMES, axis=0).copy()
    for i in range(CROSSED_FRAMES):
        t = i / CROSSED_FRAMES
        bend_forward(out[i], nb_limbs, NB_TORSO, CROSSED_BREATH * math.sin(2 * math.pi * 2 * t))
        turn(out[i], nb_limbs, NB_HEAD, CROSSED_LOOK * math.sin(2 * math.pi * t))
    return out


def make_fold(white_fold, stand_frame):
    """The white-clothed Gerudo's (unused) arm-folding motion over Nabooru's stance."""
    return np.array([with_arms(stand_frame, f) for f in white_fold])


def make_air(relaxed_frame, walk_mean, nb_limbs, arm_out, elbow, hips, knees, lean):
    """A pose in mid-air: legs bent (left, right hip flexion and knee bend), arms out for balance."""
    f = relaxed_frame.copy()
    for limb in NB_LEGS:
        f[limb + 1] = walk_mean[limb + 1]
    bend_forward(f, nb_limbs, NB_TORSO, lean)
    for (thigh, shin), hip, knee in zip(((1, 2), (4, 5)), hips, knees):
        rotate_world(f, nb_limbs, thigh, X_AXIS, -hip)
        rotate_world(f, nb_limbs, shin, X_AXIS, knee)
    for (upper, fore), side in (((8, 9), 1), ((11, 12), -1)):
        rotate_world(f, nb_limbs, upper, Z_AXIS, side * arm_out)
        rotate_world(f, nb_limbs, fore, X_AXIS, -elbow)
    return f[None]


NB_L_LEG, NB_R_LEG = (1, 2, 3), (4, 5, 6)  # thigh, shin, foot


def solve_leg(frame, nb_limbs, leg, ankle):
    """Two-bone IK: the knee bent and the thigh turned so the leg's ankle reaches `ankle` (model space, as close as
    the leg allows), the foot keeping its orientation in model space."""
    thigh, shin, foot = leg
    f = frame.copy()
    W, P = fk(nb_limbs, f)
    foot_world = W[foot]
    l1, l2 = np.linalg.norm(P[shin] - P[thigh]), np.linalg.norm(P[foot] - P[shin])
    reach = np.clip(np.linalg.norm(ankle - P[thigh]), abs(l1 - l2) + 1, l1 + l2 - 1)
    want = math.acos(np.clip((l1 * l1 + l2 * l2 - reach * reach) / (2 * l1 * l2), -1, 1))
    a, c = P[thigh] - P[shin], P[foot] - P[shin]
    now = math.acos(np.clip(a @ c / np.linalg.norm(a) / np.linalg.norm(c), -1, 1))
    rotate_world(f, nb_limbs, shin, np.cross(a, c), math.degrees(want - now))  # (+ opens the knee)
    W, P = fk(nb_limbs, f)
    cur, to = P[foot] - P[thigh], ankle - P[thigh]
    axis = np.cross(cur, to)
    if np.linalg.norm(axis) > 1e-9:
        angle = math.acos(np.clip(cur @ to / np.linalg.norm(cur) / np.linalg.norm(to), -1, 1))
        rotate_world(f, nb_limbs, thigh, axis, math.degrees(angle))
    W, _ = fk(nb_limbs, f)
    f[foot + 1] = zyx_from_rot(W[shin].T @ foot_world)
    return f


class FootBoxes:
    """Her feet's mesh bounding boxes, to put a foot on the ground (its lowest point) rather than its ankle."""

    def __init__(self, nb_limbs):
        self.limbs = nb_limbs
        self.corners = {}
        for foot in (NB_L_LEG[2], NB_R_LEG[2]):
            v = read_vtx("object_nb", f"{nb_limbs[foot].name[: -len('Limb')]}Vtx")
            lo, hi = v.min(0), v.max(0)
            self.corners[foot] = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                           for z in (lo[2], hi[2])])

    def lowest(self, frame, foot):
        W, P = fk(self.limbs, frame)
        return float((P[foot] + (W[foot] @ self.corners[foot].T).T)[:, 1].min())


def place_foot(frame, nb_limbs, feet, leg, ankle, height, toes, flat=None):
    """The leg reaching for `ankle` (model space; its height is then adjusted) with the foot pitched `toes` degrees
    toes-down (from `flat`, its orientation in model space, if given), its lowest point at model height `height`."""
    thigh, shin, foot = leg
    f = frame.copy()
    W, _ = fk(nb_limbs, f)
    if flat is not None:
        f[foot + 1] = zyx_from_rot(W[shin].T @ flat)
        W, _ = fk(nb_limbs, f)
    toe = W[foot] @ np.array([1.0, 0.0, 0.0])  # the foot points along its +X
    side = np.cross(np.array([0.0, 1.0, 0.0]), toe)  # pitching about this turns the toes down
    f[foot + 1] = zyx_from_rot(W[shin].T @ rot_from_quat(np.concatenate(
        [[math.cos(math.radians(toes) / 2)], side / np.linalg.norm(side) * math.sin(math.radians(toes) / 2)])) @ W[foot])
    target = np.array(ankle, float)
    for _ in range(4):  # the ankle's height that puts the lowest point of the foot at `height`
        f = solve_leg(f, nb_limbs, leg, target)
        target[1] += height - feet.lowest(f, foot)
    return f


# Side hop (Link's: 6 frames in the air, under 7 units high, 12.75 units sideways per frame, then a landing during which
# he slides 11.2 units more and stops). Hers, one key per frame of his, in an arc: a crouch, then a push-off from the
# trailing foot, which stays where she took off while her hips go on over to the side; in the air her hips lead, her
# legs tucked together trailing behind them, her hips turned towards the hop and her shoulders turned back against it,
# her upper body tilted back towards where she came from and her arms kept on that side (a narrow figure); her
# leading foot reaches out to where it will stand, plants there as she touches down and stays while she slides on over
# it (sinking into the landing), the trailing foot setting down beside it, and she rises into `base`.
# Per key, for a hop to her right (mirrored for the left): how far the player has travelled (game units), Link's height
# (0 while her feet are kept on the ground), how far her hips are ahead of where they stand (game units, towards the
# hop) and their height (model units), her hips' turn towards the hop, her shoulders' turn back against it (relative
# to her hips) and her upper body's tilt back towards where she came from (degrees), her torso bending forward, each
# foot's (shift towards the hop from where it stands, in game units, or "takeoff"/"landing": where it stood before the
# hop / will stand after it; height of its lowest point above the ground, in game units; toes-down pitch) for the
# trailing and the leading leg, and her arms: how far they have swung to the side she came from (negative: through
# to the other side, landing).
HOP_STEP = 12.75  # Link's sideways speed in the air (8.5, at 1.5 game units per frame)
HOP_SLIDE = 11.2  # how far he slides on as he lands
HOP_AIR = 6  # keys before the touchdown
SIDE_HOP = [
    dict(travel=0, h=0.0, ahead=1.5, hips=-450, turn=8, twist=2, tilt=-3, bend=10,
         trail=(0, 0.0, 0), lead=(0, 0.0, 0), arms=0.35),  # crouch, weight shifting over
    dict(travel=1, h=0.0, ahead=0.5, hips=-250, turn=24, twist=6, tilt=8, bend=4,
         trail=("takeoff", 0.0, 55), lead=(-7.5, 5.0, 30), arms=0.85),  # push-off: trailing toes still down
    dict(travel=2, h=6.0, ahead=1.0, hips=-150, turn=34, twist=9, tilt=12, bend=3,
         trail=(-8.5, 9.0, 50), lead=(-8.0, 12.0, 30), arms=1.0),
    dict(travel=3, h=6.8, ahead=1.0, hips=-150, turn=34, twist=10, tilt=10, bend=3,
         trail=(-5.5, 11.5, 40), lead=(-6.5, 14.0, 25), arms=1.0),
    dict(travel=4, h=6.0, ahead=1.5, hips=-150, turn=30, twist=9, tilt=6, bend=3,
         trail=(-3.5, 10.0, 30), lead=(-3.0, 10.0, 15), arms=0.9),
    dict(travel=5, h=3.8, ahead=2.0, hips=-200, turn=22, twist=7, tilt=1, bend=4,
         trail=(-1.5, 7.0, 25), lead=(3.5, 2.5, 0), arms=0.55),  # reaching for the ground
    dict(travel=6, h=0.0, ahead=2.5, hips=-400, turn=12, twist=4, tilt=-4, bend=7,
         trail=(1.5, 3.0, 15), lead=("landing", 0.0, 0), arms=0.15),  # touching down
    dict(travel=7, h=0.0, ahead=1.5, hips=-750, turn=5, twist=-1, tilt=-7, bend=12,
         trail=("landing", 0.8, 5), lead=("landing", 0.0, 0), arms=-0.3),  # slid on over the leading foot
    dict(travel=7, h=0.0, ahead=0.8, hips=-600, turn=2, twist=-2, tilt=-5, bend=9,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=-0.2),
    dict(travel=7, h=0.0, ahead=0.4, hips=-380, turn=1, twist=-1, tilt=-2, bend=5,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=-0.1),
    dict(travel=7, h=0.0, ahead=0.15, hips=-190, turn=0, twist=0, tilt=-1, bend=2,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=0.0),
    dict(travel=7, h=0.0, ahead=0.05, hips=-70, turn=0, twist=0, tilt=0, bend=1,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=0.0),
    dict(travel=7, h=0.0, ahead=0.0, hips=-20, turn=0, twist=0, tilt=0, bend=0,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=0.0),
    dict(travel=7, h=0.0, ahead=0.0, hips=0, turn=0, twist=0, tilt=0, bend=0,
         trail=("landing", 0.0, 0), lead=("landing", 0.0, 0), arms=0.0),  # standing in `base`
]
SIDE_HOP_LANDING = HOP_AIR  # the landing starts at the touchdown key
SIDE_HOP_FEET_TURN = 0.5  # in the air her feet turn this much of her hips' turn
SIDE_HOP_HEAD_STEADY = 0.8  # her head keeps this much of its orientation in `base` (looking ahead)
# Her arms kept on the side she came from (directions from shoulder to hand in model space for a hop to her right,
# elbow bend): the arm on that side out and down behind her, the other across in front of her; swung through to the
# other side on landing.
SIDE_HOP_ARMS_BACK = {"near": ((0.45, -0.85, -0.2), 30), "far": ((0.15, -0.8, 0.55), 75)}
SIDE_HOP_ARMS_THROUGH = {"near": ((-0.15, -0.95, 0.2), 20), "far": ((-0.35, -0.9, 0.2), 25)}


def hop_travel(key):
    """How far the player has travelled sideways at a side hop key (game units)."""
    return min(key["travel"], HOP_AIR) * HOP_STEP + (HOP_SLIDE if key["travel"] > HOP_AIR else 0.0)


def turn_matrix(axis, degrees):
    a = math.radians(degrees) / 2
    return rot_from_quat(np.concatenate([[math.cos(a)], np.asarray(axis, float) / np.linalg.norm(axis) * math.sin(a)]))


def steady_limb(frame, nb_limbs, limb, world, keep):
    """`limb` turned so that `keep` of its orientation in model space is `world`'s."""
    W, _ = fk(nb_limbs, frame)
    target = rot_from_quat(slerp(quat_from_rot(W[limb]), quat_from_rot(world), keep))
    frame[limb + 1] = zyx_from_rot(W[nb_limbs[limb].parent].T @ target)


def hop_arms(frame, nb_limbs, toward, right, swing):
    """Her arms swung `swing` of the way to the side she came from (negative: through to the side she hops to)."""
    pose = SIDE_HOP_ARMS_BACK if swing >= 0 else SIDE_HOP_ARMS_THROUGH
    near, far = (NB_L_ARM, NB_R_ARM) if right else (NB_R_ARM, NB_L_ARM)  # near: the arm on the side she came from
    swung = frame.copy()
    for arm, (direction, elbow) in ((near, pose["near"]), (far, pose["far"])):
        x, y, z = direction
        swung = aim_arm(swung, nb_limbs, arm, (-toward * x, y, z))
        rotate_world(swung, nb_limbs, arm[1], X_AXIS, -elbow)
    posed = blend_frames(frame, swung, abs(swing))
    out = frame.copy()
    for limb in NB_L_ARM + NB_R_ARM:
        out[limb + 1] = posed[limb + 1]
    return out


SIDE_HOP_ARM_LIFT = range(5, 65, 5)  # degrees an arm may be raised out to the side to keep its scimitar off her legs


def make_side_hop(base, nb_limbs, feet, right, bare_arms, clearance=None, swords=None):
    """A side hop from `base` (her idle, or her fighting stance) to her right or left, landing back in `base`: the
    keys of SIDE_HOP, one per frame of Link's hop (0 to HOP_AIR) and of his landing (from HOP_AIR on). With
    `bare_arms` her arms swing to the side she came from; without, they keep `base`'s guard, turning with her torso.
    With `clearance` (and `swords`, the scimitars in her hands), an arm whose blade would cut her (her legs swing by
    it) is raised out to the side just enough."""
    toward = -1 if right else 1  # model +X is her left
    lead, trail = (NB_R_LEG, NB_L_LEG) if right else (NB_L_LEG, NB_R_LEG)
    ground = {foot: feet.lowest(base, foot) for foot in (NB_L_LEG[2], NB_R_LEG[2])}
    Wb, standing = fk(nb_limbs, base)  # her ankles and feet as she stands
    landed = hop_travel(SIDE_HOP[-1])
    out = []
    for key in SIDE_HOP:
        travel = hop_travel(key)
        f = base.copy()
        f[0, 0] += toward * key["ahead"] * 100
        f[0, 1] += key["hips"]
        rotate_world(f, nb_limbs, 0, Y_AXIS, -toward * key["turn"])  # (+Y turns her to face her left)
        rotate_world(f, nb_limbs, NB_TORSO, Y_AXIS, toward * key["twist"])
        rotate_world(f, nb_limbs, NB_TORSO, Z_AXIS, toward * key["tilt"])  # (+Z leans her to her right)
        bend_forward(f, nb_limbs, NB_TORSO, key["bend"])
        steady_limb(f, nb_limbs, NB_HEAD, Wb[NB_HEAD], SIDE_HOP_HEAD_STEADY)
        if bare_arms:
            f = hop_arms(f, nb_limbs, toward, right, key["arms"])
        for leg, (shift, above, toes) in ((trail, key["trail"]), (lead, key["lead"])):
            shift = -travel if shift == "takeoff" else (landed - travel) if shift == "landing" else shift
            height = ground[leg[2]] + (above - key["h"]) * 100
            flat = turn_matrix(Y_AXIS, -toward * key["turn"] * SIDE_HOP_FEET_TURN) @ Wb[leg[2]]
            f = place_foot(f, nb_limbs, feet, leg, standing[leg[2]] + [toward * shift * 100, 0, 0], height, toes,
                           flat=flat)
        if clearance is not None:
            for j, arm in SWORD_ARM.items():
                if clearance.count(f, swords, only=j):
                    out_side = 1 if j == 1 else -1
                    for lift in SIDE_HOP_ARM_LIFT:
                        raised = f.copy()
                        rotate_world(raised, nb_limbs, arm[0], Z_AXIS, out_side * lift)
                        if not clearance.count(raised, swords, only=j):
                            break
                    f = raised
        out.append(f)
    return np.array(out)


def aim_arm(frame, nb_limbs, arm, direction):
    """The arm turned at the shoulder (as a whole) so that shoulder-to-hand points along `direction` (model space)."""
    _, P = fk(nb_limbs, frame)
    d = (P[arm[2]] - P[arm[0]]) / np.linalg.norm(P[arm[2]] - P[arm[0]])
    want = np.asarray(direction, float) / np.linalg.norm(direction)
    axis = np.cross(d, want)
    out = frame.copy()
    if np.linalg.norm(axis) > 1e-9:
        rotate_world(out, nb_limbs, arm[0], axis, math.degrees(math.acos(np.clip(d @ want, -1, 1))))
    return out


# Landing hurt after a long fall (Link crouches, holds it shaking, then gets up): a hard landing into a deep squat,
# folded over her knees, head down, arms hanging; breathing hard while Link holds; standing up into her idle. Keys
# per frame of the landing: her hips' height and how far back they go (model units, from where they are standing),
# pelvis pitch and torso bend forward, head bend (degrees), and how far her arms have come from her pose in the air
# to hanging.
HURT_LAND = [
    dict(hips=-700, back=-100, pitch=5, bend=8, head=0, arms=0.0),  # touching down
    dict(hips=-1550, back=-250, pitch=14, bend=30, head=8, arms=0.4),
    dict(hips=-2000, back=-350, pitch=20, bend=48, head=18, arms=0.8),
    dict(hips=-2080, back=-380, pitch=22, bend=57, head=26, arms=1.0),  # the impact carries her a little further
    dict(hips=-2010, back=-360, pitch=21, bend=54, head=24, arms=1.0),
    dict(hips=-1980, back=-350, pitch=20, bend=53, head=22, arms=1.0),
    dict(hips=-1980, back=-350, pitch=20, bend=53, head=22, arms=1.0),
    dict(hips=-1980, back=-350, pitch=20, bend=53, head=22, arms=1.0),
]
HURT_HOLD_FRAMES = 16  # a breath: hips, torso and head heave by these (model units, degrees)
HURT_BREATH = dict(hips=40, bend=3.0, head=2.0)
HURT_RISE_FRAMES = 10
# Hanging arms: shoulder-to-hand direction (left arm; mirrored for the right), elbow bend; with scimitars also the
# forearm roll and wrist (as in HOLD_*): a little further out and forward, the blades held level, pointing ahead
# (found by searching for blades clear of her and of the ground).
HURT_ARMS = dict(hang=(0.2, -1.0, 0.3), elbow=20)
HURT_ARMS_ARMED = dict(hang=(0.6, -1.0, 0.6), elbow=40, twist=-45, wrist=(30, -30, 60))
BLADE_GROUND_CLEARANCE = 100  # model units


def hurt_pose(rest, nb_limbs, feet, key, air, arms_cfg, arms=1.0, rest_arms=0.0, bare=None):
    """`rest` crouched as `key` says, feet where they stand. Arms: `arms` of the way from `air`'s to hanging (posed
    from `bare`'s arms if given: `rest` may have turned its wrists already), then `rest_arms` of the way to `rest`'s."""
    f = rest.copy()
    f[0, 1] += key["hips"]
    f[0, 2] += key["back"]
    rotate_world(f, nb_limbs, 0, X_AXIS, key["pitch"])
    bend_forward(f, nb_limbs, NB_TORSO, key["bend"])
    bend_forward(f, nb_limbs, NB_HEAD, key["head"])
    hang = f.copy()
    if bare is not None:
        for limb in NB_L_ARM + NB_R_ARM:
            hang[limb + 1] = bare[limb + 1]
    for arm, side in ((NB_L_ARM, 1), (NB_R_ARM, -1)):
        x, y, z = arms_cfg["hang"]
        hang = aim_arm(hang, nb_limbs, arm, (side * x, y, z))
        rotate_world(hang, nb_limbs, arm[1], X_AXIS, -arms_cfg["elbow"])
        if "wrist" in arms_cfg:
            wrist = arms_cfg["wrist"] if side == 1 else (-arms_cfg["wrist"][0], -arms_cfg["wrist"][1], arms_cfg["wrist"][2])
            hang[arm[1] + 1] = zyx_from_rot(rot_zyx(hang[arm[1] + 1]) @ rot_zyx((int(side * arms_cfg["twist"] * 182), 0, 0)))
            hang[arm[2] + 1] = zyx_from_rot(rot_zyx(hang[arm[2] + 1]) @ rot_zyx(tuple(int(v * 182) for v in wrist)))
    in_air = f.copy()
    for limb in NB_L_ARM + NB_R_ARM:
        in_air[limb + 1] = air[limb + 1]
    posed = blend_frames(blend_frames(in_air, hang, arms), rest, rest_arms)
    for limb in NB_L_ARM + NB_R_ARM:
        f[limb + 1] = posed[limb + 1]
    W, P = fk(nb_limbs, rest)
    for leg in (NB_L_LEG, NB_R_LEG):
        f = place_foot(f, nb_limbs, feet, leg, P[leg[2]], feet.lowest(rest, leg[2]), 0, flat=W[leg[2]])
    return f


def blades_clear(frame, nb_limbs, clearance):
    """Her scimitars (in her grip) are out of her body and above the ground."""
    if clearance.count(frame):
        return False
    W, P = fk(nb_limbs, frame)
    return all((P[hand] + (W[hand] @ clearance.blades[j].T).T)[:, 1].min() >= BLADE_GROUND_CLEARANCE
               for j, hand in SWORD_HAND.items())


def make_hurt_landing(rest, nb_limbs, feet, air, arms_cfg, clearance=None, bare=None):
    """-> landing (one frame per frame of Link's crouch), holding (a loop), getting up (ends in `rest`). With
    `clearance` (scimitars in hand), a frame where the blades would cut her or the ground moves her arms a little
    less or further along, whichever is nearest and clear. `bare`: her idle with empty hands, if `rest` isn't."""

    def pose(key, arms, rest_arms, search):
        tries = [search] + sorted(np.arange(0.0, 1.0001, 0.05), key=lambda w: abs(w - search))
        for w in tries:
            f = hurt_pose(rest, nb_limbs, feet, key, air, arms_cfg, *((w, rest_arms) if arms is None else (arms, w)),
                          bare=bare)
            if clearance is None or blades_clear(f, nb_limbs, clearance):
                return f
        sys.exit("HurtLand: no clear place for her scimitars")

    land = np.array([pose(key, None, 0.0, key["arms"]) for key in HURT_LAND])
    squat = HURT_LAND[-1]
    hold = []
    for i in range(HURT_HOLD_FRAMES):
        breath = math.sin(2 * math.pi * i / HURT_HOLD_FRAMES)
        key = dict(squat, hips=squat["hips"] + HURT_BREATH["hips"] * breath,
                   bend=squat["bend"] + HURT_BREATH["bend"] * breath,
                   head=squat["head"] + HURT_BREATH["head"] * math.sin(2 * math.pi * i / HURT_HOLD_FRAMES + 0.6))
        hold.append(pose(key, 1.0, 0.0, 0.0))
    rise = []
    for i in range(HURT_RISE_FRAMES - 1):
        w = smoothstep(i / (HURT_RISE_FRAMES - 1))
        rise.append(pose({k: v * (1 - w) for k, v in squat.items()}, 1.0, None, w))
    rise.append(rest.copy())
    return land, np.array(hold), np.array(rise)


# Running off a ledge, Link leaps (his run_jump: 13 frames, then he holds the last while he falls). Hers is her own run
# carried into the air: her push-off and the stride in the air that follows (run frames 3 to 5), that stride held a
# little longer and a little wider, her upper body a little more upright, easing into her pose in the air for the fall.
# Per key (one per frame of his): the frame of her run, how much wider her stride is, how much more upright her upper
# body is (degrees) and how far she is into her pose in the air.
RUN_JUMP = [(3.0, 1.0, 0, 0.0), (3.8, 1.0, 1, 0.0), (4.4, 1.05, 2, 0.0), (4.8, 1.1, 3, 0.0), (5.0, 1.15, 4, 0.0),
            (5.15, 1.15, 5, 0.0), (5.3, 1.15, 5, 0.0), (5.4, 1.15, 5, 0.05), (5.5, 1.15, 5, 0.15), (5.5, 1.1, 5, 0.3),
            (5.5, 1.05, 5, 0.5), (5.5, 1.0, 5, 0.7), (5.5, 1.0, 5, 0.85)]
RUN_JUMP_ARM_LIFT = range(5, 65, 5)  # degrees an arm may be raised out to the side to keep its scimitar off her


def make_run_jump(run, nb_limbs, air, hold=None, clearance=None):
    """Her leap off a ledge from `run` (her run cycle; RUN_JUMP keys), ending near `air`; with `hold` (HOLD_* table) her
    scimitars held as she runs with them, an arm raised out just enough wherever a blade would cut her (`clearance`)."""
    mean = mean_frame(run)
    out = []
    for at, stride, upright, into_air in RUN_JUMP:
        i = int(at)
        f = blend_frames(run[i], run[(i + 1) % len(run)], at - i)
        for limb in NB_LEGS:  # the stride: her legs further from their average over the cycle
            m = quat_from_rot(rot_zyx(mean[limb + 1]))
            q = quat_from_rot(rot_zyx(f[limb + 1]))
            f[limb + 1] = zyx_from_rot(rot_from_quat(quat_mul(m, quat_pow(quat_mul(quat_conj(m), q), stride))))
        bend_forward(f, nb_limbs, NB_TORSO, -upright)
        if hold is not None:
            f = hold_blades(f, nb_limbs, hold)
        f = blend_frames(f, air, into_air)
        if hold is not None:
            for j, arm in SWORD_ARM.items():
                if clearance.count(f, only=j):
                    for lift in RUN_JUMP_ARM_LIFT:
                        raised = f.copy()
                        rotate_world(raised, nb_limbs, arm[0], Z_AXIS, (1 if j == 1 else -1) * lift)
                        if not clearance.count(raised, only=j):
                            break
                    f = raised
        out.append(f)
    return np.array(out)


ARMED_RUN_SWING = 0.45  # with scimitars in hand her arms swing this much of her run's swing (about her walk's)


def damp_swing(table, limbs, k):
    """Each of `limbs` keeps only `k` of its motion around its average pose over the cycle."""
    out = table.copy()
    for limb in limbs:
        qs = [quat_from_rot(rot_zyx(f[limb + 1])) for f in table]
        mean = quat_mean(qs)
        for i, q in enumerate(qs):
            out[i, limb + 1] = zyx_from_rot(rot_from_quat(quat_mul(mean, quat_pow(quat_mul(quat_conj(mean), q), k))))
    return out


def make_run_back(run, nb_limbs, lean):
    """Her run with the torso upright (leaning back by `lean` degrees), to play backwards."""
    out = run.copy()
    for f in out:
        bend_forward(f, nb_limbs, NB_TORSO, -lean)
    return out


# The fighter's scimitars never move relative to her hands (every frame of every fighter animation): her grip is
# fixed, and where a blade points is down to her wrist and arm. Poses with blades are checked against her mesh.
SWORD_VTX = {1: ("object_geldb", "object_geldb_Vtx_0078A8"), 2: ("object_geldb", "object_geldb_Vtx_007CF8")}
SWORD_HAND = {1: 10, 2: 13}
SWORD_ARM = {1: NB_L_ARM, 2: NB_R_ARM}
BODY_BOX_SCALE = 0.85  # each limb's mesh is approximated by its bounding box, a little shrunk


def read_vtx(obj, name):
    text = (ootanim.ASSETS / obj / f"{name}.inc.c").read_text()
    return np.array([[int(v, 0) for v in m] for m in re.findall(r"VTX\(\s*(-?\w+),\s*(-?\w+),\s*(-?\w+)", text)], float)


class BladeClearance:
    """Counts points of her scimitars inside her body (other than the arm holding each one)."""

    def __init__(self, nb_limbs):
        self.limbs = nb_limbs
        self.boxes = {}
        for i, limb in enumerate(nb_limbs):
            vtx = ootanim.ASSETS / "object_nb" / f"{limb.name[: -len('Limb')]}Vtx.inc.c"
            if vtx.exists():
                v = read_vtx("object_nb", vtx.name[: -len(".inc.c")])
                self.boxes[i] = ((v.min(0) + v.max(0)) / 2, (v.max(0) - v.min(0)) / 2 * BODY_BOX_SCALE)
        self.blades = {}
        for j, (obj, name) in SWORD_VTX.items():
            v = read_vtx(obj, name)
            pts = [v] + [(v[a] * (1 - t) + v[b] * t)[None] for a, b in itertools.combinations(range(len(v)), 2)
                         if np.linalg.norm(v[a] - v[b]) < 1500 for t in (0.25, 0.5, 0.75)]
            self.blades[j] = np.vstack(pts)

    def count(self, frame, swords=None, only=None):
        """(`only`: just the scimitar in that hand, 1 left or 2 right)"""
        W, P = fk(self.limbs, frame)
        n = 0
        for j, hand in SWORD_HAND.items():
            if only is not None and j != only:
                continue
            grip = rot_zyx(swords[j]) if swords is not None else np.eye(3)
            pts = P[hand] + (W[hand] @ grip @ self.blades[j].T).T
            for i, (center, half) in self.boxes.items():
                if i not in SWORD_ARM[j]:
                    local = (W[i].T @ (pts - P[i]).T).T
                    n += int(np.all(np.abs(local - center) < half, axis=1).sum())
        return n


def hold_blades(frame, nb_limbs, arms):
    """Arms posed to point the blades: per side (1 left, 2 right) the arm swung out from her side, the elbow bent
    forward, the forearm twisted and the wrist turned (degrees). Found by searching for blades pointing where wanted
    without touching her."""
    f = frame.copy()
    for j, (out, elbow, twist, wrist) in arms.items():
        upper, fore, hand = SWORD_ARM[j]
        side = 1 if j == 1 else -1
        rotate_world(f, nb_limbs, upper, Z_AXIS, side * out)
        rotate_world(f, nb_limbs, fore, X_AXIS, -elbow)
        f[fore + 1] = zyx_from_rot(rot_zyx(f[fore + 1]) @ rot_zyx((int(side * twist * 182), 0, 0)))
        f[hand + 1] = zyx_from_rot(rot_zyx(f[hand + 1]) @ rot_zyx(tuple(int(v * 182) for v in wrist)))
    return f


# arms lax at her sides (only the forearm roll and wrist change), blades held normally, pointing forward and a little
# down; running, her arms swing a little wider so the blades pass her legs
TIRED_ARMS_OUT = 15
HOLD_IDLE = {1: (0, 0, -90, (30, -60, 60)), 2: (0, 0, -45, (-45, 60, 60))}
HOLD_WALK = {1: (0, 0, -45, (30, -60, 60)), 2: (0, 0, -75, (-45, 60, 60))}
HOLD_RUN = {1: (20, 0, -45, (30, -60, 60)), 2: (20, 0, -75, (-45, 60, 60))}
HOLD_TIRED = {1: (TIRED_ARMS_OUT, 0, -90, (30, -60, 60)), 2: (TIRED_ARMS_OUT, 0, -45, (-45, 60, 60))}


def blend_frames(a, b, w):
    """Pose `a` moved `w` of the way to pose `b` (rotations along the shortest path)."""
    out = a.copy()
    out[0] = np.round(a[0] * (1 - w) + b[0] * w)
    for j in range(1, len(a)):
        q = slerp(quat_from_rot(rot_zyx(a[j])), quat_from_rot(rot_zyx(b[j])), w)
        out[j] = zyx_from_rot(rot_from_quat(q))
    return out


SOFT_LANDING = 0.5  # how much of the fighter's landing crouch a soft landing keeps


def make_soft_landing(jump, rest):
    """The fighter's landing with half the crouch, ending standing in `rest` instead of rising into her stance."""
    out = []
    for f in range(len(jump)):
        settle = min(max((f - 6) / (len(jump) - 7), 0.0), 1.0)
        out.append(blend_frames(jump[f], rest, SOFT_LANDING + (1 - SOFT_LANDING) * settle))
    return np.array(out)


LOW_LANDING_LEGS = 0.5  # how much of the landing's knee bend to take out
# After a short drop (up to 80 units) her knees cushion it (30% of the landing's knee bend) and her upper body stays
# almost upright (it keeps this much of its orientation standing).
LIGHT_LANDING_LEGS = 0.7
LIGHT_LANDING_UPRIGHT = 0.8
NB_LEG_JOINTS = (0, 1, 2, 3, 4, 5, 6, 18)  # table rows: root translation and rotation, legs, waist


LANDING_ARMS_FRAMES = 8  # empty-handed, her arms settle from the in-air pose to her sides over these frames


def make_low_landing(jump, rest, clearance=None, arms_from=None, stiff=LOW_LANDING_LEGS, nb_limbs=None, upright=0.0):
    """The fighter's landing with `stiff` of her knee bend taken out (about half by default); her arms and upper body
    ease into `rest` over the second half instead of rising into her fighting stance. With `upright` (and `nb_limbs`)
    her torso keeps that much of its orientation in `rest`, instead of bending forward over her knees. With `clearance` (she holds scimitars), any
    frame where a blade would cut her takes more of `rest`'s upper body, until it doesn't. With `arms_from` (her pose
    in the air), her arms come down from it to `rest`'s instead of following the fighter's, which fling out wide."""
    out = []
    W_rest = fk(nb_limbs, rest)[0] if upright else None
    for f in range(len(jump)):
        settle = min(max((f - 8) / (len(jump) - 9), 0.0), 1.0)
        legs = blend_frames(jump[f], rest, stiff + (1 - stiff) * settle)
        for upper in np.arange(settle, 1.0001, 0.1):
            frame = blend_frames(jump[f], rest, min(upper, 1.0))
            frame[list(NB_LEG_JOINTS)] = legs[list(NB_LEG_JOINTS)]
            if upright:
                steady_limb(frame, nb_limbs, NB_TORSO, W_rest[NB_TORSO], upright)
            if clearance is None or clearance.count(frame) == 0:
                break
        if arms_from is not None:
            t = min(max((f - 1) / LANDING_ARMS_FRAMES, 0.0), 1.0)
            arms = blend_frames(arms_from, rest, t * t * (3 - 2 * t))
            for limb in NB_L_ARM + NB_R_ARM:
                frame[limb + 1] = arms[limb + 1]
        out.append(frame)
    return np.array(out)


# Landing her flip, the fighter throws her right arm up, ready for her stance; empty-handed, Nabooru's right arm
# mirrors her left (out and low, for balance) instead: flip frame -> how much.
BARE_FLIP_ARM_MIRROR = {6: 0.5, 7: 1.0, 8: 1.0, 9: 1.0}


def mirror_arm(frame, nb_limbs, reflections, src_arm, dst_arm):
    """`dst_arm` posed as the mirror image of `src_arm` (relative to her torso)."""
    out = frame.copy()
    for src, dst in zip(src_arm, dst_arm):
        parent = nb_limbs[dst].parent
        out[dst + 1] = zyx_from_rot(reflections[parent] @ rot_zyx(frame[src + 1]) @ reflections[dst])
    return out


def arm_elevation(frame, nb_limbs, arm):
    """Degrees above (+) or below (-) the horizon of the line from shoulder to hand, in model space."""
    _, P = fk(nb_limbs, frame)
    d = P[arm[2]] - P[arm[0]]
    return math.degrees(math.asin(np.clip(d[1] / np.linalg.norm(d), -1, 1)))


def lower_arm(frame, nb_limbs, arm, elevation):
    """The arm turned down at the shoulder (as a whole) until shoulder-to-hand points `elevation` degrees from the
    horizon, keeping its direction around her."""
    _, P = fk(nb_limbs, frame)
    d = P[arm[2]] - P[arm[0]]
    d = d / np.linalg.norm(d)
    flat = np.array([d[0], 0.0, d[2]])
    flat /= np.linalg.norm(flat)
    e = math.radians(elevation)
    want = flat * math.cos(e) + np.array([0, 1, 0]) * math.sin(e)
    axis = np.cross(d, want)
    if np.linalg.norm(axis) < 1e-9:
        return frame
    angle = math.degrees(math.acos(np.clip(d @ want, -1, 1)))
    out = frame.copy()
    rotate_world(out, nb_limbs, arm[0], axis, angle)
    return out


def make_bare_flip(flip, crouch, rest, nb_limbs, reflections):
    """The fighter's backflip for empty hands: the flight unchanged, then landing into a light crouch (her right
    arm mirroring her left) and rising gradually towards standing relaxed instead of in her fighting stance (the
    game blends the rest into her idle)."""
    flip = flip.copy()
    for f, weight in BARE_FLIP_ARM_MIRROR.items():
        mirrored = mirror_arm(flip[f], nb_limbs, reflections, NB_L_ARM, NB_R_ARM)
        flip[f] = blend_frames(flip[f], mirrored, weight)
        # her body is still rolled from the flip as she lands: relative to the ground, keep the right arm no higher
        # than the left
        left, right = arm_elevation(flip[f], nb_limbs, NB_L_ARM), arm_elevation(flip[f], nb_limbs, NB_R_ARM)
        if right > left:
            flip[f] = lower_arm(flip[f], nb_limbs, NB_R_ARM, left)
    out = flip.copy()
    out[8] = blend_frames(flip[8], crouch, 0.35)
    out[9] = blend_frames(blend_frames(flip[9], crouch, 0.75), rest, 0.35)
    out[10] = blend_frames(crouch, rest, 0.75)
    return out


BONK_FALL = (7, 19)  # the fighter's defeat: falling back onto her backside (7-10), bouncing and settling (10-19)
BONK_KNEEL = (10, 3)  # the spear guard's kneel, played backwards (before 3 she lifts a foot to step back)


def smoothstep(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def make_bonk(defeat, kneel, rest, defeat_swords=None, clearance=None):
    """Rolling into a wall: the end of the fighter's defeat (falling back onto her backside, bouncing, settling),
    then getting up through the guard's kneel, played backwards, into `rest` (her idle). Her arms and head ease
    into `rest`'s along the way: the guard looks to one side, then the other as she rises, which is a whip of the
    head when it's this quick. With `defeat_swords` (scimitars in hand) also returns the scimitars, easing into her
    grip; with `clearance`, a frame where a blade would cut her eases its arms a little less or more (crouched,
    her idle's arms would put the blades through her thighs), whichever is nearest and clear.
    Frames: fall 0-3, sitting 3-12, rising 12-23."""
    first, last = BONK_FALL
    sit = defeat[last]
    rise = [blend_frames(sit, kneel[BONK_KNEEL[0]], 0.5)]
    rise += [kneel[k] for k in range(BONK_KNEEL[0], BONK_KNEEL[1] - 1, -1)]
    rise += [blend_frames(kneel[BONK_KNEEL[1]], rest, 0.5), rest]
    out = list(defeat[first: last + 1])
    swords = None if defeat_swords is None else list(defeat_swords[first: last + 1])
    grip = None if defeat_swords is None else np.zeros_like(defeat_swords[0])  # the fighter's grip, as in her idle
    for i, frame in enumerate(rise):
        scheduled = smoothstep((i + 1) / len(rise))
        frame = frame.copy()
        frame[NB_HEAD + 1] = blend_frames(sit, rest, scheduled)[NB_HEAD + 1]
        tries = [scheduled] + sorted(np.arange(0.0, 1.0001, 0.05), key=lambda w: abs(w - scheduled))
        for w in tries:
            w = min(w, 1.0)
            eased = blend_frames(sit, rest, w)  # where her arms are, rotation by rotation
            posed = frame.copy()
            for limb in NB_L_ARM + NB_R_ARM:
                posed[limb + 1] = eased[limb + 1]
            blades = None if swords is None else blend_frames(defeat_swords[last], grip, w)
            if clearance is None or clearance.count(posed, blades) == 0:
                break
        out.append(posed)
        if swords is not None:
            swords.append(blades)
    return np.array(out) if swords is None else (np.array(out), np.array(swords))


# The spear guard's horizontal slash (cut from the game: nothing plays it), for twin scimitars. She steps in with her
# left foot and sweeps the spear, held in her right hand, level from her left across her front to her right; her left
# hand lets go of the shaft and swings back. A scimitar in the fighter's grip points within 24 degrees of where the
# spear's blade did, so the sweep is the right scimitar's as it stands. Her first frames only lift her left foot for
# the step: the sweep starts as that foot passes where it stands in the fighter's stance, which is where her combo
# comes from. The guard then draws herself up to attention, spear upright (her unused recovery): Nabooru's legs and
# body follow that, her arms come down to her armed idle instead, and she ends standing in it.
SWEEP_FROM = 2
SWEEP_SETTLE = 4  # the last frames of the recovery, over which the rest of her settles into her idle


def make_sweep(slash, recover, rest, clearance):
    """-> the sweep (frames SWEEP_FROM on of `slash`) followed by its recovery, ending in `rest`; with `clearance`, a
    recovery frame where a blade would cut her eases its arms a little less or more, whichever is nearest and clear."""
    out = [f.copy() for f in slash[SWEEP_FROM:]]
    last = out[-1]
    n = len(recover)
    for i, frame in enumerate(recover):
        scheduled = smoothstep((i + 1) / n)
        body = blend_frames(frame, rest, smoothstep((i + 1 - (n - SWEEP_SETTLE)) / SWEEP_SETTLE))
        tries = [scheduled] + sorted(np.arange(0.0, 1.0001, 0.05), key=lambda w: abs(w - scheduled))
        for w in tries:
            eased = blend_frames(last, rest, min(w, 1.0))
            posed = body.copy()
            for limb in NB_L_ARM + NB_R_ARM:
                posed[limb + 1] = eased[limb + 1]
            if clearance is None or clearance.count(posed) == 0:
                break
        out.append(posed)
    out[-1] = rest.copy()
    return np.array(out)


class BodyPoints:
    """Her mesh's vertices, limb by limb (not her ponytail), to put her body on the ground: its lowest point."""

    def __init__(self, nb_limbs):
        self.limbs = nb_limbs
        self.points = {}
        for i, limb in enumerate(nb_limbs):
            path = ootanim.ASSETS / "object_nb" / f"{limb.name[: -len('Limb')]}Vtx.inc.c"
            if path.exists() and i != NB_PONYTAIL:
                self.points[i] = read_vtx("object_nb", path.name[: -len(".inc.c")])

    def lowest(self, frame):
        W, P = fk(self.limbs, frame)
        return min(float((P[i] + (W[i] @ v.T).T)[:, 1].min()) for i, v in self.points.items())


NB_PONYTAIL = 16

# Knocked down from behind, Link pitches forward onto his front (back_downA: 6 frames, sliding on), lies a moment
# (back_downB) and gets up (back_down_wake: 36 frames). Hers lands on her left shoulder and side rather than on all
# fours: the hit thrusts her hips forward and whips her upper body back, then she pitches forward, rolling onto her
# left side, her left arm reaching out ahead to take the fall; she lies curled on her left side, then pushes herself up
# on her right hand and left forearm, gets her knees under her and rises through the guard's kneel (the get-up she
# has for falling on her back).
# Her pose lying on her left side, built standing (then laid down): hips and knees bent (the top leg more), back
# curled a little, head tilted down to the ground, the bottom (left) arm reaching out past her head, the top (right)
# arm's hand on the ground in front of her chest. Directions are shoulder to hand in her standing frame; angles are
# degrees.
KNOCK_LYING = dict(hips=(50, 75), knees=(70, 95), curl=12, head_tilt=-20, head_bend=10,
                   left_arm=((0.15, 0.95, 0.25), 25), right_arm=((0.55, 0.1, 0.8), 60))
# The hit: hips thrust forward (model units), upper body and head whipped back, arms flung back, knees giving a little.
KNOCK_HIT = dict(hips_forward=250, arch=12, head_back=10, arms=((0.25, -0.8, -0.5), 15), knees=15)
# Falling, her arms reach forward to take the fall (left, right: direction, elbow) on their way from the hit's to lying.
KNOCK_REACH = (((0.2, -0.35, 0.9), 15), ((-0.25, -0.5, 0.8), 30))
# Per key: how far she has pitched forward (degrees; 90 is lying), rolled onto her left side (degrees, -90 is fully on
# it), gone from the hit's pose to lying (limbs), how far forward her hips are (model units), how high off the ground
# she is (model units; a little bounce). Fall (Link's 6 frames), then lying (8 keys over his 14 frames).
KNOCK_FALL = [(0, 0, 0.0, 250, 0), (20, -5, 0.25, 350, 0), (45, -20, 0.5, 450, 0), (68, -45, 0.75, 550, 0),
              (86, -65, 0.95, 600, 0), (90, -70, 1.0, 600, 0)]
KNOCK_DOWN = [(90, -70, 1.0, 600, 0), (90, -70, 1.0, 600, 90), (90, -70, 1.0, 600, 40), (90, -70, 1.0, 600, 0),
              (90, -70, 1.0, 600, 0), (90, -70, 1.0, 600, 0), (90, -70, 1.0, 600, 0), (90, -70, 1.0, 600, 0)]
# Getting up (Link's 36 frames): poses at some of his frames, in between blended; from KNOCK_KNEEL_AT on, the guard's
# kneel played backwards (its 10 frames over the rest of his).
KNOCK_PROPPED = dict(pitch=55, roll=-55, forward=600, hips=(70, 85), knees=(100, 110), curl=8,
                     left_arm=((0.5, -0.5, 0.7), 90), right_arm=((0.6, -0.6, 0.5), 30))
KNOCK_KNEES_UNDER = dict(pitch=35, roll=-10, forward=400, hips=(95, 100), knees=(125, 130), curl=10,
                         left_arm=((0.2, -0.9, 0.35), 20), right_arm=((-0.05, -0.75, 0.65), 20))
KNOCK_WAKE = [(0, "lying"), (5, "lying"), (11, "propped"), (17, "knees_under"), (22, "kneel")]
KNOCK_KNEEL_AT = 22


def curl_pose(rest, nb_limbs, hips, knees, curl, left_arm, right_arm, head_tilt=0, head_bend=0):
    """`rest` with hips and knees bent (left, right), back curled, head tilted (to her left) and bent, arms aimed."""
    f = rest.copy()
    for (thigh, shin), hip, knee in zip(((1, 2), (4, 5)), hips, knees):
        rotate_world(f, nb_limbs, thigh, X_AXIS, -hip)
        rotate_world(f, nb_limbs, shin, X_AXIS, knee)
    bend_forward(f, nb_limbs, NB_TORSO, curl)
    rotate_world(f, nb_limbs, NB_HEAD, Z_AXIS, head_tilt)
    bend_forward(f, nb_limbs, NB_HEAD, head_bend)
    for arm, (direction, elbow) in ((NB_L_ARM, left_arm), (NB_R_ARM, right_arm)):
        f = aim_arm(f, nb_limbs, arm, direction)
        rotate_world(f, nb_limbs, arm[1], X_AXIS, -elbow)
    return f


def laid(pose, nb_limbs, body, rest_root, pitch, roll, forward, lift):
    """`pose` pitched forward and rolled onto her left side, `forward` model units ahead, resting on the ground (or
    `lift` above it)."""
    f = pose.copy()
    f[1] = zyx_from_rot(turn_matrix(Z_AXIS, roll) @ turn_matrix(X_AXIS, pitch) @ rest_root)
    f[0, 2] = forward
    f[0, 1] = 0
    f[0, 1] = int(-body.lowest(f) + lift)
    return f


def make_knock_front(rest, kneel, nb_limbs, body):
    """Knocked down from behind -> fall + lying + getting up, one table (KNOCK_FALL, KNOCK_DOWN, then Link's 36 frames
    of getting up); `kneel`: the guard's kneel (11 frames, standing to kneeling) with her arms as wanted."""
    rest_root = fk(nb_limbs, rest)[0][0]
    lying = curl_pose(rest, nb_limbs, **{k: v for k, v in KNOCK_LYING.items()})
    hit = rest.copy()
    bend_forward(hit, nb_limbs, NB_TORSO, -KNOCK_HIT["arch"])
    bend_forward(hit, nb_limbs, NB_HEAD, -KNOCK_HIT["head_back"])
    for arm, side in ((NB_L_ARM, 1), (NB_R_ARM, -1)):
        (x, y, z), elbow = KNOCK_HIT["arms"]
        hit = aim_arm(hit, nb_limbs, arm, (side * x, y, z))
        rotate_world(hit, nb_limbs, arm[1], X_AXIS, -elbow)
    for thigh, shin in ((1, 2), (4, 5)):
        rotate_world(hit, nb_limbs, thigh, X_AXIS, -KNOCK_HIT["knees"])
        rotate_world(hit, nb_limbs, shin, X_AXIS, 2 * KNOCK_HIT["knees"])
    reach = hit.copy()
    for arm, (direction, elbow) in zip((NB_L_ARM, NB_R_ARM), KNOCK_REACH):
        reach = aim_arm(reach, nb_limbs, arm, direction)
        rotate_world(reach, nb_limbs, arm[1], X_AXIS, -elbow)
    out = []
    for pitch, roll, to_lying, forward, lift in KNOCK_FALL + KNOCK_DOWN:
        pose = blend_frames(hit, lying, to_lying)
        arms = (blend_frames(hit, reach, to_lying * 2) if to_lying < 0.5 else
                blend_frames(reach, lying, to_lying * 2 - 1))
        for limb in NB_L_ARM + NB_R_ARM:
            pose[limb + 1] = arms[limb + 1]
        out.append(laid(pose, nb_limbs, body, rest_root, pitch, roll, forward, lift))
    poses = {"lying": out[-1]}
    for name, cfg in (("propped", KNOCK_PROPPED), ("knees_under", KNOCK_KNEES_UNDER)):
        c = dict(cfg)
        pitch, roll, forward = c.pop("pitch"), c.pop("roll"), c.pop("forward")
        poses[name] = laid(curl_pose(rest, nb_limbs, **c), nb_limbs, body, rest_root, pitch, roll, forward, 0)
    poses["kneel"] = kneel[-1]
    wake = []
    for (a, pa), (b, pb) in zip(KNOCK_WAKE, KNOCK_WAKE[1:]):
        for t in range(a, b):
            f = blend_frames(poses[pa], poses[pb], smoothstep((t - a) / (b - a)))
            f[0, 1] = 0
            f[0, 1] = int(-body.lowest(f))
            wake.append(f)
    rise = len(kneel) - 1
    for t in range(KNOCK_KNEEL_AT, 36):
        k = (t - KNOCK_KNEEL_AT) / (35 - KNOCK_KNEEL_AT) * rise
        i = int(k)
        wake.append(blend_frames(kneel[rise - i], kneel[max(rise - i - 1, 0)], k - i))
    return np.concatenate([np.array(out), np.array(wake)])


# With scimitars in hand while she falls, lies and gets up, a blade that would cut her or go into the ground has her
# wrist turned (by up to 90 degrees about two axes), and if that isn't enough her arm raised out to the side (degrees);
# chosen over the whole animation so that her wrists change as little as possible from frame to frame.
BLADE_WRIST_TURNS = [(a, b) for a in range(-90, 91, 15) for b in range(-90, 91, 15)]
BLADE_ARM_LIFTS = range(0, 61, 15)
BLADE_GROUND = -50  # model units: a blade may lie on the ground, not go into it


def keep_blades_clear(frames, nb_limbs, clearance):
    """`frames` with each wrist turned, and if need be the arm raised, wherever its scimitar (in her grip) would cut her
    or go into the ground: of the turns that keep it clear, the ones changing least from frame to frame overall."""
    out = np.array(frames).copy()
    states = [(lift, a, b) for lift in BLADE_ARM_LIFTS for a, b in BLADE_WRIST_TURNS]

    def apply(f, j, state):
        lift, a, b = state
        g = f.copy()
        hand = SWORD_HAND[j]
        if lift:
            rotate_world(g, nb_limbs, SWORD_ARM[j][0], Z_AXIS, (1 if j == 1 else -1) * lift)
        g[hand + 1] = zyx_from_rot(rot_zyx(g[hand + 1]) @ rot_zyx((0, int(a * 182), int(b * 182))))
        return g

    def clear(g, j):
        if clearance.count(g, only=j):
            return False
        W, P = fk(nb_limbs, g)
        hand = SWORD_HAND[j]
        return (P[hand] + (W[hand] @ clearance.blades[j].T).T)[:, 1].min() >= BLADE_GROUND

    def effort(s):
        return 0.05 * (abs(s[1]) + abs(s[2])) + 0.5 * s[0]

    def change(s, t):
        return ((s[1] - t[1]) ** 2 + (s[2] - t[2]) ** 2 + 4 * (s[0] - t[0]) ** 2) / 15  # (big jumps cost the most)

    for j in SWORD_HAND:
        options = []
        for f in out:  # (the identity first: most frames are clear as they are)
            ok = [s for s in states if s == (0, 0, 0) and clear(f, j)]
            options.append(ok or [s for s in states if clear(apply(f, j, s), j)])
            if not options[-1]:
                sys.exit("keep_blades_clear: no clear place for a scimitar")
        cost = [{s: effort(s) for s in options[0]}]
        back = [{}]
        for k in range(1, len(out)):
            cost.append({})
            back.append({})
            for s in options[k]:
                p = min(cost[k - 1], key=lambda t: cost[k - 1][t] + change(t, s))
                cost[k][s] = cost[k - 1][p] + change(p, s) + effort(s)
                back[k][s] = p
        s = min(cost[-1], key=cost[-1].get)
        for k in range(len(out) - 1, -1, -1):
            out[k] = apply(out[k], j, s)
            s = back[k].get(s)
    return out


# ---------------------------------------------------------------- the spear
# Her second weapon is the spear of the guards (object_gla). Like the fighter's scimitars, the guard's spear never
# moves relative to the hand that holds it, her right: it is drawn in her hand's own frame, its shaft running along
# the hand's -Y (towards the blade) through SPEAR_GRIP. Where it points is down to her arm and wrist. The guard runs
# and starts her sweep with her left hand on the shaft too; where the shaft lies in that hand is measured from her run.
NB_L_HAND, NB_R_HAND = 10, 13
SPEAR_GRIP = {NB_R_HAND: (np.array([470.0, 0.0, 136.0]), np.array([0.0, -1.0, 0.0])),
              NB_L_HAND: (np.array([420.0, 50.0, -130.0]), np.array([0.05, -0.975, -0.22]) / 1.0008)}
SPEAR_BUTT, SPEAR_NECK, SPEAR_TIP = 2634.0, -4044.0, -6982.0  # along the hand's Y: end of the shaft, blade, point
SPEAR_VTX = ("object_gla", "gObjectGlaSpearVtx")
ELBOW_POLE = {NB_L_ARM: np.array([0.55, -1.0, -0.45]), NB_R_ARM: np.array([-0.55, -1.0, -0.45])}  # out, down, back
FOREARM_ROLL = 0.7  # how much of a hand's turn about her forearm the forearm takes up itself


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def direction(yaw, elevation):
    """A direction in model space: `yaw` degrees round from straight ahead towards her left, `elevation` up."""
    y, e = math.radians(yaw), math.radians(elevation)
    return np.array([math.sin(y) * math.cos(e), math.sin(e), math.cos(y) * math.cos(e)])


def largest_step(table, loops=False):
    """-> the largest turn of any joint from one frame to the next (degrees), the joint and the frame it turns to."""
    worst = (0.0, 0, 0)
    for f in range(1, len(table) + (1 if loops else 0)):
        for j in range(2, table.shape[1]):
            a, b = quat_from_rot(rot_zyx(table[f - 1, j])), quat_from_rot(rot_zyx(table[f % len(table), j]))
            turn = math.degrees(2 * math.acos(min(1.0, abs(float(a @ b)))))
            worst = max(worst, (turn, j - 1, f % len(table)))
    return worst


def spear_of(frame, nb_limbs):
    """-> where her right hand holds the shaft and the direction of the blade from there (model space)."""
    W, P = fk(nb_limbs, frame)
    offset, along = SPEAR_GRIP[NB_R_HAND]
    return P[NB_R_HAND] + W[NB_R_HAND] @ offset, W[NB_R_HAND] @ along


def set_world(frame, nb_limbs, limb, world):
    W, _ = fk(nb_limbs, frame)
    frame[limb + 1] = zyx_from_rot(W[nb_limbs[limb].parent].T @ world)


def aim_bone(frame, nb_limbs, limb, child, towards):
    """`limb` turned the shortest way so that its bone (to joint `child`) points along `towards`."""
    _, P = fk(nb_limbs, frame)
    now, want = unit(P[child] - P[limb]), unit(towards)
    axis = np.cross(now, want)
    if np.linalg.norm(axis) > 1e-9:
        rotate_world(frame, nb_limbs, limb, axis, math.degrees(math.acos(np.clip(now @ want, -1, 1))))


def reach(frame, nb_limbs, arm, wrist, pole=None):
    """Two-bone IK: the arm bent so its wrist is at `wrist` (model space; as near as it reaches), the elbow out
    towards `pole`."""
    upper, fore, hand = arm
    f = frame.copy()
    _, P = fk(nb_limbs, f)
    l1, l2 = np.linalg.norm(nb_limbs[fore].joint), np.linalg.norm(nb_limbs[hand].joint)
    to = np.asarray(wrist, float) - P[upper]
    d = float(np.clip(np.linalg.norm(to), abs(l1 - l2) + 1, l1 + l2 - 1))
    n = unit(to)
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    r = math.sqrt(max(l1 * l1 - a * a, 0.0))
    pole = ELBOW_POLE[arm] if pole is None else np.asarray(pole, float)
    side = pole - (pole @ n) * n
    side = unit(side) if np.linalg.norm(side) > 1e-6 else unit(np.cross(n, [1.0, 0.0, 0.0]))
    elbow = P[upper] + a * n + r * side
    aim_bone(f, nb_limbs, upper, fore, elbow - P[upper])
    _, P = fk(nb_limbs, f)
    aim_bone(f, nb_limbs, fore, hand, P[upper] + d * n - P[fore])
    return f


ROLL_STEP = 10  # degrees between the ways round the shaft a hand is tried in
ROLL_STEADY = 2.0  # what a hand turning round the shaft from one frame to the next costs, against a bent wrist
SHAFT_SLIDE = 50.0  # model units: the steps in which a hand that cannot reach slides along the shaft
HANDS_APART = 600.0  # her hands on the shaft are never closer together than this
LENIENT = False  # while designing: a hand that cannot reach the spear is reported, not an error
QUICK = False  # while designing: the spear is not kept clear of her and the ground (which takes minutes)


def out_of_reach(message):
    if not LENIENT:
        sys.exit(message)
    print("  !!", message)


def grip_ways(frame, nb_limbs, arm, point, toward):
    """Every way round the shaft the arm's hand could sit, closed round it at `point` (model space) of a spear
    pointing along `toward` -> [(degrees round it, the frame with the arm reaching there, the hand's rotation in model
    space, how far that bends her wrist (radians), how far the hand is off the shaft: it may be out of reach)]."""
    hand = arm[2]
    offset, along = SPEAR_GRIP[hand]
    toward = unit(toward)
    point = np.asarray(point, float)
    ref = np.array([0.0, 1.0, 0.0]) if abs(toward[1]) < 0.95 else np.array([0.0, 0.0, 1.0])
    u = unit(ref - (ref @ toward) * toward)
    v = np.cross(toward, u)
    side = unit(np.cross(along, [1.0, 0.0, 0.0]))  # with `along`, spans the hand's own frame
    local = np.array([along, side, np.cross(along, side)]).T
    ways = []
    for angle in range(0, 360, ROLL_STEP):
        a = math.radians(angle)
        across = math.cos(a) * u + math.sin(a) * v
        world = np.array([toward, across, np.cross(toward, across)]).T @ local.T
        f = reach(frame, nb_limbs, arm, point - world @ offset)
        _, P = fk(nb_limbs, f)
        bend = math.acos(np.clip((world @ [1.0, 0.0, 0.0]) @ unit(P[hand] - P[arm[1]]), -1, 1))
        off = P[hand] + world @ offset - point
        ways.append((angle, f, world, bend, float(np.linalg.norm(off - (off @ toward) * toward))))
    return ways


def close_hand(frame, nb_limbs, arm, world, near=None):
    """The arm's hand turned to `world` (its rotation in model space); her forearm takes up most of the hand's turn
    about it. -> the frame, and that turn (radians; of the two ways round, the one nearer `near`: the last frame's)."""
    W, _ = fk(nb_limbs, frame)
    q = quat_from_rot(W[arm[1]].T @ world)
    centre = 0.0 if near is None else near
    twist = (2 * math.atan2(q[1], q[0]) - centre + math.pi) % (2 * math.pi) - math.pi + centre  # about its own X
    f = frame.copy()
    f[arm[1] + 1] = zyx_from_rot(rot_zyx(f[arm[1] + 1]) @ turn_matrix(X_AXIS, math.degrees(twist) * FOREARM_ROLL))
    set_world(f, nb_limbs, arm[2], world)
    return f, twist


def steady_ways(options, frames):
    """For each of `frames` (their numbers), one of its ways (see grip_ways) -> the index of each: her wrist bent
    least over them all, the hand not jumping round the shaft from one frame to the next."""
    def step(a, b):
        d = math.radians((a[0] - b[0] + 180) % 360 - 180)
        return ROLL_STEADY * d * d

    cost = [[w[3] ** 2 + (w[4] / 100.0) ** 2 for w in options[0]]]
    back = [[]]
    for k in range(1, len(options)):
        joined = frames[k] == frames[k - 1] + 1
        cost.append([])
        back.append([])
        for w in options[k]:
            j = min(range(len(options[k - 1])),
                    key=lambda i: cost[k - 1][i] + (step(options[k - 1][i], w) if joined else 0.0))
            cost[k].append(cost[k - 1][j] + (step(options[k - 1][j], w) if joined else 0.0) + w[3] ** 2 +
                           (w[4] / 100.0) ** 2)
            back[k].append(j)
    pick = [min(range(len(cost[-1])), key=cost[-1].__getitem__)]
    for k in range(len(options) - 1, 0, -1):
        pick.append(back[k][pick[-1]])
    return pick[::-1]


def hold_spear_through(frames, nb_limbs, holds, what="spear", tolerance=60.0):
    """`frames` (an animation, or part of one) with her hands on the spear. Per frame a hold: `grip`, where her right
    hand holds it (model space), `toward`, the way its blade points, and optionally `left`: her left hand on the
    shaft that far from her right (model units, towards the blade), or as near to that as it can reach (the shaft
    slides through that hand), `weight` of the way from where the frame has that arm (1: on the shaft). A hand that
    cannot reach the shaft is an error, unless it is letting go (weight < 1) or the hold is not `strict`."""
    n = len(frames)
    out, options = [], []
    for i in range(n):
        ways = grip_ways(frames[i], nb_limbs, NB_R_ARM, holds[i]["grip"], holds[i]["toward"])
        near = [w for w in ways if w[4] <= tolerance]
        if not near:
            out_of_reach(f"{what} frame {i}: her right hand is {min(w[4] for w in ways):.0f} model units short of "
                         "the spear")
        options.append(near or ways)
    twist = None
    for i, k in enumerate(steady_ways(options, list(range(n)))):
        f, twist = close_hand(options[i][k][1], nb_limbs, NB_R_ARM, options[i][k][2], twist)
        out.append(f)

    lefts = [i for i in range(n) if holds[i].get("left") is not None and holds[i].get("weight", 1.0) > 0.0]
    options = []
    for i in lefts:
        grip, toward = np.asarray(holds[i]["grip"], float), unit(holds[i]["toward"])
        _, P = fk(nb_limbs, out[i])
        nearest = float((P[NB_L_ARM[0]] - grip) @ toward)  # the point of the shaft nearest her left shoulder
        along = float(holds[i]["left"])
        nearest = max(nearest, HANDS_APART) if along > 0 else min(nearest, -HANDS_APART)
        while True:
            ways = grip_ways(out[i], nb_limbs, NB_L_ARM, grip + along * toward, toward)
            near = [w for w in ways if w[4] <= tolerance]
            if near or abs(along - nearest) < 1.0:
                break
            along += float(np.clip(nearest - along, -SHAFT_SLIDE, SHAFT_SLIDE))
        if not near:
            if holds[i].get("weight", 1.0) >= 1.0 and holds[i].get("strict", True):
                out_of_reach(f"{what} frame {i}: her left hand is {min(w[4] for w in ways):.0f} model units short "
                             "of the spear")
            near = [min(ways, key=lambda w: w[4])]  # as near as it gets
        options.append(near)
    if lefts:
        twist = None
        for (i, ways), k in zip(zip(lefts, options), steady_ways(options, lefts)):
            f, twist = close_hand(ways[k][1], nb_limbs, NB_L_ARM, ways[k][2], twist)
            weight = holds[i].get("weight", 1.0)
            for limb in NB_L_ARM:
                q = slerp(quat_from_rot(rot_zyx(out[i][limb + 1])), quat_from_rot(rot_zyx(f[limb + 1])), weight)
                out[i][limb + 1] = zyx_from_rot(rot_from_quat(q))
    return np.array(out)


def hold_spear(frame, nb_limbs, grip, toward, left=None, tolerance=60.0, what="spear", strict=True):
    """One frame with her hands on the spear (see hold_spear_through)."""
    return hold_spear_through([frame], nb_limbs, [dict(grip=grip, toward=toward, left=left, strict=strict)], what,
                              tolerance)[0]


def spear_hold_from(body, nb_limbs, hand, aim, left=None, left_weight=1.0):
    """A hold (see hold_spear_through) from: where her right hand holds the spear, `hand` (model units, model axes)
    from her right shoulder as `body` has it; the way its blade points, `aim` = (yaw, elevation) degrees."""
    _, P = fk(nb_limbs, body)
    return dict(grip=P[NB_R_ARM[0]] + np.asarray(hand, float), toward=direction(*aim), left=left, weight=left_weight)


def with_spear(body, nb_limbs, hand, aim, left=None, left_weight=1.0, what="spear", tolerance=60.0):
    """`body` with her arms on the spear (see spear_hold_from)."""
    return hold_spear_through([body], nb_limbs, [spear_hold_from(body, nb_limbs, hand, aim, left, left_weight)], what,
                              tolerance)[0]


EASE = {"linear": lambda t: t, "in": lambda t: t * t, "out": lambda t: 1 - (1 - t) * (1 - t), "smooth": smoothstep}


def tween(keys, at):
    """Values at frame `at` between keys: dicts with `at` (frame), numbers or tuples of numbers under other names,
    and optionally `ease` (how the key is approached: linear, in, out or smooth)."""
    if at <= keys[0]["at"]:
        return dict(keys[0])
    for a, b in zip(keys, keys[1:]):
        if at <= b["at"]:
            w = EASE[b.get("ease", "smooth")]((at - a["at"]) / (b["at"] - a["at"]))
            out = {}
            for name, value in b.items():
                if name in ("at", "ease") or a.get(name) is None or value is None:
                    out[name] = value
                else:
                    out[name] = (np.asarray(a[name], float) * (1 - w) + np.asarray(value, float) * w)
            return out
    return dict(keys[-1])


def left_on_shaft(frame, nb_limbs):
    """How far along the shaft from her right hand her left hand is (model units, towards the blade), and how far
    off it."""
    grip, toward = spear_of(frame, nb_limbs)
    W, P = fk(nb_limbs, frame)
    held = P[NB_L_HAND] + W[NB_L_HAND] @ SPEAR_GRIP[NB_L_HAND][0]
    along = float((held - grip) @ toward)
    return along, float(np.linalg.norm(held - grip - along * toward))


class SpearClearance:
    """Counts points of the spear (in her right hand) inside her body, other than the arm holding it (and her left
    arm, with `two_handed`), and tells how far below the ground it reaches."""
    SKIP = (NB_PONYTAIL,)  # her ponytail swings free of it

    def __init__(self, nb_limbs, boxes):
        self.limbs = nb_limbs
        self.boxes = boxes
        offset, _ = SPEAR_GRIP[NB_R_HAND]
        shaft = [[offset[0], y, offset[2]] for y in np.arange(SPEAR_BUTT, SPEAR_NECK, -220.0)]
        blade = read_vtx(*SPEAR_VTX)
        blade = blade[blade[:, 1] < SPEAR_NECK + 200]
        self.points = np.vstack([np.array(shaft), blade])

    def world(self, frame):
        W, P = fk(self.limbs, frame)
        return P[NB_R_HAND] + (W[NB_R_HAND] @ self.points.T).T, W, P

    def count(self, frame, two_handed=False):
        return self.check(frame, two_handed)[0]

    def lowest(self, frame):
        return float(self.world(frame)[0][:, 1].min())

    def check(self, frame, two_handed=False):
        """-> how many of its points are inside her, and its lowest point's height."""
        pts, W, P = self.world(frame)
        skip = set(NB_R_ARM) | set(self.SKIP) | (set(NB_L_ARM) if two_handed else set())
        n = 0
        for i, (center, half) in self.boxes.items():
            if i not in skip:
                local = (W[i].T @ (pts - P[i]).T).T
                n += int(np.all(np.abs(local - center) < half, axis=1).sum())
        return n, float(pts[:, 1].min())


# ---- how she moves with it
# Out of combat she carries it as the guards do: at attention standing, shouldered walking, couched in both hands
# running (their own animations, as they are). Ready to fight (Z-targeting) she stands and steps as the fighter
# does, her arms as the guard's are when she runs: the spear couched at her right hip in both hands, pointing ahead
# and a little up.
COUCH_FRAME = 0  # of the guard's run


def couched(body, run):
    f = body.copy()
    for limb in NB_L_ARM + NB_R_ARM:
        f[limb + 1] = run[COUCH_FRAME][limb + 1]
    return f


def squared(frame, nb_limbs, stance_world, keep, lean=0.0, head=0.8):
    """`frame` with her upper body kept `keep` of the way as it is in her stance (her left side leading), bent `lean`
    degrees further forward; her head `head` of the way as in her stance, looking ahead."""
    f = frame.copy()
    steady_limb(f, nb_limbs, NB_TORSO, stance_world[NB_TORSO], keep)
    bend_forward(f, nb_limbs, NB_TORSO, lean)
    steady_limb(f, nb_limbs, NB_HEAD, stance_world[NB_HEAD], head)
    return f


def spear_hold(frame, nb_limbs):
    """How `frame` holds the spear, as with_spear takes it: hand, aim, left."""
    grip, toward = spear_of(frame, nb_limbs)
    _, P = fk(nb_limbs, frame)
    return dict(hand=tuple(grip - P[NB_R_ARM[0]]),
                aim=(math.degrees(math.atan2(toward[0], toward[2])), math.degrees(math.asin(toward[1]))),
                left=left_on_shaft(frame, nb_limbs)[0])


def spear_moves(bodies, keys, nb_limbs, what):
    """One frame per body, its arms on the spear as the keys say at that frame (see tween, with_spear; `lw` is
    with_spear's left_weight)."""
    holds = []
    for i, body in enumerate(bodies):
        k = tween(keys, i)
        holds.append(spear_hold_from(body, nb_limbs, k["hand"], k["aim"], k.get("left"), float(k.get("lw", 1.0))))
    return hold_spear_through(bodies, nb_limbs, holds, what)


def at_frame(table, at):
    """Frame `at` of an animation, between two frames if it is not whole."""
    i = min(int(at), len(table) - 1)
    return table[i] if at == i else blend_frames(table[i], table[min(i + 1, len(table) - 1)], at - i)


# ---- her attacks with it (see "attacks" in PlayAsNabooru.cpp)
# The jab: a short thrust in both hands. Her weight goes onto her front leg and back as it does in the fighter's
# slash, without that slash's turn of the shoulders: her left side stays leading, her right hand drives the shaft
# forward through her left. Per frame: the frame of the slash her legs and hips are taken from, and how much further
# her upper body bends forward (degrees).
JAB_BODY = [(0, 0), (0.5, 0), (1, 0), (2.2, 4), (3, 7), (3.2, 7), (2.6, 5), (1.8, 3), (0.9, 1), (0, 0)]
JAB_SQUARE = 0.9
# Thrusting, the spear goes out from her right side and points in towards her middle, so that the end of its shaft
# passes by her right hip, not through her.
JAB = [dict(at=2, hand=(-1250, -1750, -500), aim=(14, 13), left=2900, ease="out"),  # drawn back
       dict(at=4, hand=(-500, -800, 1650), aim=(10, 5), left=2713, ease="in"),  # out
       dict(at=5, hand=(-480, -780, 1700), aim=(10, 5), left=2713, ease="out")]


def make_jab(slash, stance, nb_limbs):
    world = fk(nb_limbs, stance)[0]
    rest = spear_hold(stance, nb_limbs)
    bodies = arms_from([squared(at_frame(slash, s), nb_limbs, world, JAB_SQUARE, lean) for s, lean in JAB_BODY], stance)
    keys = [dict(at=0, **rest)] + JAB + [dict(at=len(bodies) - 1, **rest)]
    return settle_into(spear_moves(bodies, keys, nb_limbs, "SpearJab"), stance, 3, begin=2)


# The lunge: a long thrust in her right hand alone, a passing lunge. Out of her stance (left foot ahead) she draws the
# spear back, her weight going onto her left foot, and falls forward into a step: up on her left leg, her right foot
# passes it and reaches far out ahead, heel first, her upper body leaning into the step and her chest still turned
# away. As her heel strikes she twists: her chest comes round past side on while her right arm drives the spear out
# through her left hand to full reach, a little upwards, her eyes along it; she sinks into a deep lunge, her weight
# over her bent front leg, her back leg straight, its foot turning out on its ball (the ball stays where it stood).
# Her left hand lets go as her heel strikes, and the turn of her chest drags that arm back, the elbow leading and the
# forearm trailing, then unfolding: it is stretched out behind her, a little in front of the line of her shoulders, on
# the frame her right arm is at its furthest. She holds it, pushes back off her right foot, steps it back behind her
# left and takes the shaft in both hands again: her stance, where she began. The game carries the player along with
# her (LUNGE_TRAVEL), and she is drawn from where it has carried them, so her left foot stays where it stands.
# Keys: her hips (model units, from where the player stood as she began), their yaw and her chest's (degrees, towards
# her left; her stance has LUNGE_STANCE_YAWS), her upper body's bend forward and her pelvis's share of it, how much her
# head keeps its orientation in her stance, its tilt (negative: back), how far it turns to look along the spear, and
# each foot: where its ankle is (x, z), how high its lowest point is off the ground, its yaw from her stance's, its
# toes pitched down. Her left foot keeps the ball of its foot where it stands in her stance: its x, z are 0.
LUNGE_FRAMES = 17
LUNGE_STANCE_YAWS = (-7.0, -43.0)
LUNGE_BALL = 560.0  # how far ahead of her ankle the ball of her foot is (along the foot, model units)
LUNGE_DEEP, LUNGE_FRONT = 3560.0, 4700.0  # her hips at full reach (z); where her right foot lands (z)
LUNGE_BODY = [
    dict(at=0, hips=(-309, 2368, -809), hy=-7, chest=-43, lean=0, pitch=0, head=0.85, nod=0, gaze=0,
         lf=(0, 0, 0, 0, 0), rf=(-765, -1515, 0, 0, 0)),
    dict(at=1, hips=(-330, 2330, -560), hy=-12, chest=-60, lean=7, pitch=2, head=0.85, nod=0, gaze=0,  # drawn back
         lf=(0, 0, 0, 0, 0), rf=(-765, -1515, 160, 0, 30), ease="out"),
    dict(at=2, hips=(-300, 2450, 250), hy=-4, chest=-52, lean=15, pitch=5, head=0.85, nod=0, gaze=0,  # passing
         lf=(0, 0, 0, 5, 10), rf=(-330, 150, 650, 10, 5), ease="in"),
    dict(at=3, hips=(-230, 2330, 1400), hy=12, chest=-30, lean=20, pitch=7, head=0.85, nod=-6, gaze=0,  # reaching out
         lf=(0, 0, 0, 22, 30), rf=(-260, 3650, 800, 32, -60), ease="linear"),
    dict(at=4, hips=(-180, 1880, 2550), hy=38, chest=28, lean=22, pitch=7, head=0.85, nod=-10, gaze=0.4,  # heel strikes
         lf=(0, 0, 0, 45, 22), rf=(-250, LUNGE_FRONT, 0, 35, -50), ease="linear"),
    dict(at=5, hips=(-140, 1500, LUNGE_DEEP - 220), hy=55, chest=96, lean=31, pitch=10, head=0.85, nod=-10, gaze=0.8,
         lf=(0, 0, 0, 62, 0), rf=(-250, LUNGE_FRONT, 0, 36, -31), ease="out"),  # landed
    dict(at=6, hips=(-130, 1380, LUNGE_DEEP), hy=58, chest=108, lean=36, pitch=12, head=0.85, nod=-10, gaze=1.0,
         lf=(0, 0, 0, 64, 0), rf=(-250, LUNGE_FRONT, 0, 36, -31), ease="out"),  # full reach: deepest
    dict(at=7, hips=(-130, 1405, LUNGE_DEEP - 30), hy=58, chest=104, lean=35, pitch=12, head=0.85, nod=-10, gaze=1.0,
         lf=(0, 0, 0, 64, 0), rf=(-250, LUNGE_FRONT, 0, 36, -31)),
    dict(at=8, hips=(-130, 1440, LUNGE_DEEP - 70), hy=57, chest=100, lean=34, pitch=11, head=0.85, nod=-10, gaze=1.0,
         lf=(0, 0, 0, 64, 0), rf=(-250, LUNGE_FRONT, 0, 36, -31)),
    dict(at=9, hips=(-170, 1800, 2650), hy=40, chest=60, lean=18, pitch=6, head=0.85, nod=-12, gaze=0.5,
         lf=(0, 0, 0, 45, 0), rf=(-250, LUNGE_FRONT, 60, 36, 0), ease="in"),  # pushing back off it
    dict(at=10, hips=(-250, 2200, 1250), hy=12, chest=5, lean=7, pitch=3, head=0.85, nod=-5, gaze=0,
         lf=(0, 0, 0, 15, 0), rf=(-420, 2200, 480, 20, 10), ease="linear"),
    dict(at=11, hips=(-290, 2300, 0), hy=-2, chest=-25, lean=4, pitch=2, head=0.85, nod=0, gaze=0,
         lf=(0, 0, 0, 5, 0), rf=(-650, -600, 280, 8, 10), ease="linear"),
    dict(at=12, hips=(-305, 2340, -700), hy=-6, chest=-40, lean=1, pitch=0, head=0.85, nod=0, gaze=0,  # set down
         lf=(0, 0, 0, 0, 0), rf=(-765, -1515, 0, 0, 0), ease="out"),
    dict(at=14, hips=(-309, 2368, -809), hy=-7, chest=-43, lean=0, pitch=0, head=0.85, nod=0, gaze=0,
         lf=(0, 0, 0, 0, 0), rf=(-765, -1515, 0, 0, 0)),
]
# Her right hand: where it holds the spear from her right shoulder, the way the blade points (see spear_hold_from),
# and where her left hand holds the shaft while it does.
LUNGE = [dict(at=1, hand=(-1250, -1700, -750), aim=(12, 9), left=2900, ease="out"),  # drawn back
         dict(at=2, hand=(-1250, -1650, -500), aim=(12, 8), left=2900),
         dict(at=3, hand=(-1100, -1350, 300), aim=(9, 5), left=1100, ease="in"),  # driven through her left
         dict(at=4, hand=(-650, -700, 1250), aim=(7, 3), left=900, ease="linear"),
         dict(at=5, hand=(-300, -150, 1850), aim=(8, 4), left=900, ease="out"),  # home
         dict(at=6, hand=(-280, -80, 1870), aim=(9, 5), left=900, ease="out"),  # at its furthest
         dict(at=8, hand=(-280, -40, 1860), aim=(8, 4), left=900),
         dict(at=10, hand=(-1000, -1000, 800), aim=(12, 6), left=2700),
         dict(at=11, hand=(-1150, -1250, 300), aim=(13, 8), left=2900)]
LUNGE_REST_AT = 14  # back as she holds it in her stance


def lunge_arm(side, out):
    """A direction (model space) from its angle in a side view of her (degrees from straight ahead, + up) and how far
    it leans out of that view's plane, towards her left (her front, at full reach)."""
    a, s = math.radians(side), math.sqrt(1 - out * out)
    return (out, math.sin(a) * s, math.cos(a) * s)


# Her left arm: which way her upper arm points from her shoulder and her forearm from her elbow, and `w`, how much of
# the way her hand is on the shaft.
LUNGE_LEFT_ARM = [dict(at=3, up=lunge_arm(-55, 0.2), fore=lunge_arm(-28, 0.1), w=1.0),
                  dict(at=4, up=lunge_arm(-100, 0.25), fore=lunge_arm(-15, 0.0), w=0.0, ease="linear"),  # elbow leads
                  dict(at=5, up=lunge_arm(-150, 0.3), fore=lunge_arm(-92, 0.3), w=0.0, ease="linear"),  # unfolding
                  dict(at=6, up=lunge_arm(-170, 0.13), fore=lunge_arm(-177, 0.12), w=0.0, ease="out"),  # stretched
                  dict(at=7, up=lunge_arm(-171, 0.12), fore=lunge_arm(-179, 0.1), w=0.0),
                  dict(at=8, up=lunge_arm(-172, 0.12), fore=lunge_arm(-180, 0.1), w=0.0),
                  dict(at=9, up=lunge_arm(-142, 0.2), fore=lunge_arm(-118, 0.2), w=0.0, ease="in"),
                  dict(at=10, up=(0.25, -0.96, 0.1), fore=(0.3, -0.6, 0.75), w=0.2, ease="linear"),
                  dict(at=11, up=(0.2, -0.9, 0.4), fore=(0.1, -0.4, 0.9), w=0.6, ease="linear"),
                  dict(at=12, up=(0.2, -0.9, 0.4), fore=(0.1, -0.4, 0.9), w=1.0, ease="out")]
# How far the game has carried the player forward by each frame (game units; none after): with her hips as she steps
# in, and back. PlayAsNabooru.cpp has the same (kLungeTravel).
LUNGE_TRAVEL = (0.0, 2.5, 10.6, 22.1, 33.6, 41.5, 43.7, 43.4, 43.0, 34.6, 20.6, 8.1)
SETTLE_FRAMES = 4  # an attack's last frames, blended into her stance exactly


def lunge_body(stance, nb_limbs, feet, i):
    k = tween(LUNGE_BODY, i)
    W0, P0 = fk(nb_limbs, stance)
    hips_yaw, chest_yaw = LUNGE_STANCE_YAWS
    f = stance.copy()
    f[0] = k["hips"]
    rotate_world(f, nb_limbs, 0, Y_AXIS, float(k["hy"]) - hips_yaw)
    rotate_world(f, nb_limbs, 0, X_AXIS, float(k["pitch"]))
    rotate_world(f, nb_limbs, NB_TORSO, Y_AXIS, (float(k["chest"]) - chest_yaw) - (float(k["hy"]) - hips_yaw))
    bend_forward(f, nb_limbs, NB_TORSO, float(k["lean"]) - float(k["pitch"]))
    steady_limb(f, nb_limbs, NB_HEAD, W0[NB_HEAD], float(k["head"]))
    if float(k["nod"]):
        bend_forward(f, nb_limbs, NB_HEAD, float(k["nod"]))
    if float(k["gaze"]) > 0:  # her face (her head's +Y) turned towards where the blade points
        W, _ = fk(nb_limbs, f)
        yaw, pitch = (math.radians(float(v)) for v in tween(LUNGE, i)["aim"])
        want = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
        face = W[NB_HEAD] @ np.array([0.0, 1.0, 0.0])
        axis = np.cross(face, want)
        if np.linalg.norm(axis) > 1e-9:
            rotate_world(f, nb_limbs, NB_HEAD, axis,
                         float(k["gaze"]) * math.degrees(math.acos(np.clip(face @ want, -1, 1))))
    for leg, key in ((NB_L_LEG, k["lf"]), (NB_R_LEG, k["rf"])):
        x, z, lift, yaw, toes = (float(v) for v in key)
        foot = leg[2]
        flat = turn_matrix(Y_AXIS, yaw) @ W0[foot]
        if leg == NB_L_LEG:  # turning on the ball of her foot, which stays where it is in her stance
            ahead = W0[foot] @ np.array([LUNGE_BALL, 0.0, 0.0])
            turned = flat @ np.array([LUNGE_BALL * math.cos(math.radians(toes)), 0.0, 0.0])
            ankle = P0[foot] + np.array([ahead[0] - turned[0] + x, 0.0, ahead[2] - turned[2] + z])
        else:
            ankle = np.array([x, P0[foot][1], z])
        ankle[1] = P0[foot][1]
        f = place_foot(f, nb_limbs, feet, leg, ankle, feet.lowest(stance, foot) + lift, toes, flat=flat)
    return f


def arms_from(frames, source):
    """`frames` with her arms as `source` has them. The spear's holds are solved from where a frame has her arms, and
    an arm turns about itself as that start has it: started alike, her arms turn the same way round on the spear from
    one frame to the next, and into and out of her stance."""
    out = np.array(frames).copy()
    for f in out:
        for limb in NB_L_ARM + NB_R_ARM:
            f[limb + 1] = source[limb + 1]
    return out


def settle_into(frames, rest, n=SETTLE_FRAMES, begin=0):
    """`frames` with the last `n` blended into `rest`, the last one exactly it: the pose that follows the attack; and
    with `begin`, the first that many coming out of it, the first one exactly it."""
    out = np.array(frames).copy()
    for k in range(n):
        i = len(out) - n + k
        out[i] = blend_frames(out[i], rest, smoothstep((k + 1) / n))
    for k in range(begin):
        out[k] = blend_frames(rest, out[k], smoothstep(k / begin))
    return out


def free_left_arm(held, bodies, nb_limbs, keys, hand_rest):
    """`held` (her left hand on the shaft) with that hand `w` of the way on it, as `keys` say (see LUNGE_LEFT_ARM):
    off it, her upper arm points `up` from her shoulder and her forearm `fore` from her elbow, her wrist going straight
    between there and the shaft; her arm reaches from where `bodies` has it, its forearm turning about itself towards
    how it holds the shaft only as her hand closes on it. Off the shaft her hand continues her forearm as `hand_rest`
    (a frame) has it."""
    upper_length, fore_length = (float(np.linalg.norm(nb_limbs[j].joint)) for j in (NB_L_ARM[1], NB_L_ARM[2]))
    out = []
    for i, f in enumerate(held):
        k = tween(keys, i)
        w = float(k["w"])
        if w >= 1.0:
            out.append(f)
            continue
        body = f.copy()
        for limb in NB_L_ARM:
            body[limb + 1] = bodies[i][limb + 1]
        W, P = fk(nb_limbs, f)
        up = unit(k["up"])
        wrist = P[NB_L_ARM[0]] + upper_length * up + fore_length * unit(k["fore"])
        free = reach(body, nb_limbs, NB_L_ARM, wrist, pole=up)
        free[NB_L_HAND + 1] = hand_rest[NB_L_HAND + 1]
        Wf, _ = fk(nb_limbs, free)
        g = reach(body, nb_limbs, NB_L_ARM, wrist * (1 - w) + P[NB_L_HAND] * w, pole=up)
        fore = NB_L_ARM[1]
        q = slerp(quat_from_rot(rot_zyx(g[fore + 1])), quat_from_rot(rot_zyx(f[fore + 1])), w)
        g[fore + 1] = zyx_from_rot(rot_from_quat(q))
        hand = rot_from_quat(slerp(quat_from_rot(Wf[NB_L_HAND]), quat_from_rot(W[NB_L_HAND]), w))
        Wg, _ = fk(nb_limbs, g)
        g[NB_L_HAND + 1] = zyx_from_rot(Wg[fore].T @ hand)
        out.append(g)
    return np.array(out)


def make_lunge(stance, nb_limbs, feet):
    bodies = arms_from([lunge_body(stance, nb_limbs, feet, i) for i in range(LUNGE_FRAMES)], stance)
    rest = spear_hold(stance, nb_limbs)
    keys = [dict(at=0, **rest)] + LUNGE + [dict(at=LUNGE_REST_AT, **rest)]
    holds = []
    for i, body in enumerate(bodies):
        k = tween(keys, i)
        holds.append(dict(spear_hold_from(body, nb_limbs, k["hand"], k["aim"], k["left"]), strict=False))
    held = hold_spear_through(bodies, nb_limbs, holds, "SpearLunge")
    out = settle_into(free_left_arm(held, bodies, nb_limbs, LUNGE_LEFT_ARM, stance), stance, begin=2)
    for i, f in enumerate(out):  # drawn from where the game has carried the player
        f[0][2] -= 100.0 * (LUNGE_TRAVEL[i] if i < len(LUNGE_TRAVEL) else 0.0)
    return out


# The sweep is the guard's own, as it was made (the game never plays it): she draws the spear up to her left, steps
# in and sweeps it level across her front to her right, then draws herself up to attention.
SPEAR_SWEEP_FROM = 1


def make_spear_sweep(attack, recover):
    return np.concatenate([attack[SPEAR_SWEEP_FROM:], recover])


# The charge (B held): she runs at her enemy with the spear couched, as the guards run (their own run, from where her
# left foot is planted ahead as it is in her stance; one stride of it, which the game loops while she charges), and
# thrusts it home in both hands as she pulls up: the spear driven out over three frames. Frames: CHARGE_INTO_RUN into
# the run, CHARGE_STRIDES of it and its first again, then the thrust and her stance (kSpearCharge, kChargeRunFrom...
# in PlayAsNabooru.cpp).
CHARGE_INTO_RUN = 3  # frames from her stance into the run
CHARGE_RUN_FROM = 6  # the run's frame her first stride starts from
CHARGE_STRIDES = 13  # frames of running: one cycle of the run
CHARGE_THRUST = [(1.6, 2), (2.2, 4), (3, 7), (3.2, 7), (3.2, 7), (2.8, 6), (2.3, 4), (1.7, 3), (1.1, 1), (0.6, 0),
                 (0.2, 0), (0, 0)]  # as JAB_BODY: pulling up into the thrust, then back into her stance
CHARGE_DRIVE = [dict(hand=(-1200, -1600, -400), aim=(17, 3), ease="in"),  # pulling up, the spear drawn in
                dict(hand=(-850, -1200, 700), aim=(12, 5), ease="linear"),
                None,
                dict(hand=(-480, -780, 1700), aim=(10, 5), ease="out"),  # home
                None,
                dict(hand=(-480, -780, 1700), aim=(10, 5))]


def make_charge(run, slash, stance, nb_limbs):
    world = fk(nb_limbs, stance)[0]
    rest, running = spear_hold(stance, nb_limbs), spear_hold(run[CHARGE_RUN_FROM], nb_limbs)
    first = run[CHARGE_RUN_FROM]
    bodies = [blend_frames(stance, first, smoothstep((i + 1) / (CHARGE_INTO_RUN + 1))) for i in range(CHARGE_INTO_RUN)]
    # one stride, and its first frame again: played in a loop, frames between its last two lead back into it
    bodies += [run[(CHARGE_RUN_FROM + k) % len(run)] for k in range(1, CHARGE_STRIDES + 2)]
    ran = len(bodies)  # the first frame of the thrust
    thrust = [squared(at_frame(slash, s), nb_limbs, world, JAB_SQUARE, lean) for s, lean in CHARGE_THRUST]
    bodies += [blend_frames(first, thrust[0], 0.5)] + thrust[1:]
    bodies = list(arms_from(bodies, stance))
    keys = [dict(at=0, **rest), dict(at=CHARGE_INTO_RUN, **running), dict(at=ran - 1, **running)]
    keys += [dict(at=ran + i, left=2713, **k) for i, k in enumerate(CHARGE_DRIVE) if k is not None]
    keys.append(dict(at=len(bodies) - 1, **rest))
    out = settle_into(spear_moves(bodies, keys, nb_limbs, "SpearCharge"), stance, 3)
    out[ran - 1] = out[CHARGE_INTO_RUN]
    return out


# The plunge, from the air: the spear raised in both hands over her right shoulder, point down, her left hand above
# her right on the shaft (towards its end), knees drawn up; she drives it into the ground in front of her as she
# lands in the fighter's landing crouch, and rises from that into her stance, her left hand letting go of the shaft
# and taking it again ahead of her right. Frames: PLUNGE_AIR in the air, PLUNGE_HIT landing, then the fighter's
# landing from PLUNGE_RISE_FROM.
PLUNGE_AIR, PLUNGE_HIT, PLUNGE_RISE_FROM = 10, 5, 6
PLUNGE_LEFT = -800  # her left hand on the shaft (towards its end: negative)
PLUNGE_FALL = [dict(at=0, hand=(-500, 500, -500), aim=(8, -15), left=PLUNGE_LEFT, lw=1.0),
               dict(at=4, hand=(-300, 900, -300), aim=(6, -38), left=PLUNGE_LEFT, lw=1.0, ease="out"),  # raised
               dict(at=7, hand=(-200, 800, 0), aim=(5, -50), left=PLUNGE_LEFT, lw=1.0),
               dict(at=9, hand=(0, 500, 500), aim=(3, -42), left=PLUNGE_LEFT, lw=1.0, ease="in")]  # coming down
PLUNGE_LEAN = (4, 10, 14, 18)  # her upper body's bend forward in the air at the keys above (degrees)
PLUNGE_STRIKE = dict(hand=(0, -500, 1250), aim=(0, -19), left=PLUNGE_LEFT, lw=1.0)
PLUNGE_CROUCH = (2, 3, 4, 5, 5)  # frames of the fighter's landing she strikes in
PLUNGE_REGRIP = (3, 5, 8)  # frames after landing: her left hand has let go, is ahead of her right, holds again


def make_plunge(air, jump, stance, nb_limbs):
    rest = dict(spear_hold(stance, nb_limbs), lw=1.0)
    lean = [dict(at=k["at"], lean=v) for k, v in zip(PLUNGE_FALL, PLUNGE_LEAN)]
    bodies = []
    for i in range(PLUNGE_AIR):
        f = air.copy()
        bend_forward(f, nb_limbs, NB_TORSO, float(tween(lean, i)["lean"]))
        bodies.append(f)
    bodies += [jump[f] for f in PLUNGE_CROUCH]
    landed = len(bodies)
    rise = make_low_landing(jump, stance, stiff=0.0)[PLUNGE_RISE_FROM:]
    bodies += list(rise)
    struck = dict(at=landed + 1, **PLUNGE_STRIKE)
    end = len(bodies) - 1
    let_go, ahead, holds = (landed + k for k in PLUNGE_REGRIP)

    def between(at):  # her right hand on its way from the strike to her stance
        k = tween([struck, dict(at=end, **rest)], at)
        return dict(at=at, hand=tuple(k["hand"]), aim=tuple(k["aim"]))

    # (in the air her arms start out as in her pose in the air, which she plunges from; landing, as in her stance,
    # which she rises into: the turn of her arms about themselves from one to the other comes at the impact)
    bodies = bodies[:PLUNGE_AIR] + list(arms_from(bodies[PLUNGE_AIR:], stance))
    keys = PLUNGE_FALL + [dict(at=PLUNGE_AIR, ease="in", **PLUNGE_STRIKE), struck,
                          dict(between(let_go), left=PLUNGE_LEFT, lw=0.0),
                          dict(between(ahead), left=rest["left"], lw=0.0),
                          dict(between(holds), left=rest["left"], lw=1.0), dict(at=end, **rest)]
    return settle_into(spear_moves(bodies, keys, nb_limbs, "SpearPlunge"), stance, 3)


# The leap (Z-targeting, A): from her stance she crouches, taking the spear up in the plunge's grip, pushes off from
# her toes and is in the air as in the plunge: its frames in the air are the plunge's own (she lands in the plunge).
# Link leaves the ground at once, so her crouch and push-off are drawn lowered by however far he has risen
# (KeepFeetDown in PlayAsNabooru.cpp). Her crouch and push-off: how far her hips go down (up: negative) and forward,
# her upper body's bend forward, her toes pitched down; where her right hand holds the spear from her shoulder and
# where its blade points.
LEAP_CROUCH = dict(down=450, forward=100, lean=10, toes=0, hand=(-700, -200, -100), aim=(10, -2))
LEAP_PUSH = dict(down=-300, forward=450, lean=16, toes=50, hand=(-650, 100, -250), aim=(9, -6))


def crouched(stance, nb_limbs, feet, down, forward, lean, toes):
    """Her stance with her hips `down` and `forward`, bent forward `lean` degrees, her feet where they stand, pitched
    toes-down `toes` degrees (on her toes, pushing off)."""
    W0, P0 = fk(nb_limbs, stance)
    f = stance.copy()
    f[0, 1] -= down
    f[0, 2] += forward
    bend_forward(f, nb_limbs, NB_TORSO, lean)
    steady_limb(f, nb_limbs, NB_HEAD, W0[NB_HEAD], 0.7)
    for leg in (NB_L_LEG, NB_R_LEG):
        foot = leg[2]
        f = place_foot(f, nb_limbs, feet, leg, P0[foot], feet.lowest(stance, foot), toes, flat=W0[foot])
    return f


def arms_between(frame, a, b, t):
    """`frame` with each of her arm joints `t` of the way from how `a` has it to how `b` has it."""
    f = frame.copy()
    for limb in NB_L_ARM + NB_R_ARM:
        q = slerp(quat_from_rot(rot_zyx(a[limb + 1])), quat_from_rot(rot_zyx(b[limb + 1])), t)
        f[limb + 1] = zyx_from_rot(rot_from_quat(q))
    return f


def make_spear_leap(stance, plunge, nb_limbs, feet):
    takeoff = list(arms_from([crouched(stance, nb_limbs, feet, *(k[v] for v in ("down", "forward", "lean", "toes")))
                              for k in (LEAP_CROUCH, LEAP_PUSH)], stance))
    takeoff[1] = arms_between(takeoff[1], stance, plunge[0], 0.5)  # (her arms on their way to the plunge's)
    holds = [spear_hold_from(f, nb_limbs, k["hand"], k["aim"], PLUNGE_LEFT)
             for f, k in zip(takeoff, (LEAP_CROUCH, LEAP_PUSH))]
    return np.concatenate([hold_spear_through(takeoff, nb_limbs, holds, "SpearLeap"), plunge[:PLUNGE_AIR]])


# Her block: the shaft held across her in both hands, low at her right, high at her left, standing upright (the
# body of her scimitar block).
SPEAR_BLOCK = dict(hand=(-250, -1350, 950), aim=(78, 32), left=2900)


def make_spear_guard(guard, nb_limbs):
    return np.array([with_spear(guard[0], nb_limbs, what="SpearGuard", **SPEAR_BLOCK)])


# ---- carrying it through everything else
# With the spear in her right hand a frame of any of the fighter's animations has it where her right scimitar was.
# Wherever it would go through her or into the ground, her wrist is turned (by up to 90 degrees about two axes), and
# if that isn't enough her arm raised out to the side; chosen over the whole animation so that her wrist changes as
# little as possible from frame to frame.
SPEAR_WRIST_TURNS = [(a, b) for a in range(-90, 91, 15) for b in range(-90, 91, 15)]
SPEAR_ARM_LIFTS = range(0, 61, 15)
SPEAR_GROUND = -50  # model units: it may lie on the ground, not go into it


def keep_spear_clear(frames, nb_limbs, spear, what, ground=SPEAR_GROUND, allow=0, two_handed=False):
    """`frames` with her right wrist turned, and if need be that arm raised, wherever the spear would go through her
    (more than `allow` points of it; with `two_handed`, her left arm is on it) or into the ground."""
    out = np.array(frames).copy()
    if QUICK:
        return out
    states = [(lift, a, b) for lift in SPEAR_ARM_LIFTS for a, b in SPEAR_WRIST_TURNS]

    def apply(f, state):
        lift, a, b = state
        g = f.copy()
        if lift:
            rotate_world(g, nb_limbs, NB_R_ARM[0], Z_AXIS, -lift)
        g[NB_R_HAND + 1] = zyx_from_rot(rot_zyx(g[NB_R_HAND + 1]) @ rot_zyx((int(a * 182), 0, int(b * 182))))
        return g

    def clear(g):
        inside, lowest = spear.check(g, two_handed)
        return inside <= allow and lowest >= ground

    def effort(s):
        return 0.05 * (abs(s[1]) + abs(s[2])) + 0.5 * s[0]

    def change(s, t):
        return ((s[1] - t[1]) ** 2 + (s[2] - t[2]) ** 2 + 4 * (s[0] - t[0]) ** 2) / 15

    options, turned = [], []
    for i, f in enumerate(out):
        ok = [(0, 0, 0)] if clear(f) else [s for s in states if clear(apply(f, s))]
        if not ok:
            sys.exit(f"{what}: no clear place for the spear in frame {i}")
        if ok[0] != (0, 0, 0):
            turned.append(i)
        options.append(ok)
    if turned:
        print(f"  {what}: her wrist turned to keep the spear clear in frames {turned}")
    cost, back = [{s: effort(s) for s in options[0]}], [{}]
    for k in range(1, len(out)):
        cost.append({})
        back.append({})
        for s in options[k]:
            p = min(cost[k - 1], key=lambda t: cost[k - 1][t] + change(t, s))
            cost[k][s] = cost[k - 1][p] + change(p, s) + effort(s)
            back[k][s] = p
    s = min(cost[-1], key=cost[-1].get)
    for k in range(len(out) - 1, -1, -1):
        out[k] = apply(out[k], s)
        s = back[k].get(s)
    return out


def regrip(frames, nb_limbs, weights, what="spear"):
    """`frames` with her left hand brought onto the shaft (where it lies in her right hand), `weights` of the way
    frame by frame, as near to where the guard holds it running as it can reach."""
    holds = []
    for f, weight in zip(frames, weights):
        grip, toward = spear_of(f, nb_limbs)
        holds.append(dict(grip=grip, toward=toward, left=2713.0, weight=weight, strict=False))
    held = hold_spear_through(frames, nb_limbs, holds, what, tolerance=1e9)
    out = np.array(frames).copy()
    for limb in NB_L_ARM:  # (her right arm stays as it is)
        out[:, limb + 1] = held[:, limb + 1]
    return out


# In the air she carries it in her right hand at her side, pointing ahead and up.
SPEAR_AIR = dict(hand=(-600, -1300, 300), aim=(8, 25))
# Hurt by a long fall she squats holding on to it, its butt on the ground in front of her (see HURT_LAND): where her
# right hand holds it from her shoulder once she is down, and how upright it stands (yaw, elevation of the blade).
SPEAR_HURT = dict(hand=(-350, 150, 1250), aim=(0, 84))
SPEAR_HURT_REACH = 150.0  # (model units: as she comes down her hand may be this far off it for a frame)
# Out of breath she leans on it: the spear stays as she holds it at attention while she bends over.


def make_spear_tired(idle0, nb_limbs):
    grip, toward = spear_of(idle0, nb_limbs)
    out = make_tired(idle0, nb_limbs)
    return hold_spear_through(out, nb_limbs, [dict(grip=grip, toward=toward)] * len(out), "SpearArmedTired")


def make_spear_run_jump(run, nb_limbs, air):
    """Her leap off a ledge with the spear couched: the guard's run carried into the air (see make_run_jump), her
    arms and upper body as she runs."""
    plain = make_run_jump(run, nb_limbs, air)
    out = []
    for (at, _, _, _), f in zip(RUN_JUMP, plain):
        i = int(at)
        upper = blend_frames(run[i], run[(i + 1) % len(run)], at - i)
        g = f.copy()
        for limb in (NB_TORSO, NB_HEAD) + NB_L_ARM + NB_R_ARM:
            g[limb + 1] = upper[limb + 1]
        out.append(g)
    return np.array(out)


def make_spear_hurt(rest, nb_limbs, feet, air, bare):
    """Landing hurt with the spear (see make_hurt_landing): her left arm hangs, her right holds on to the spear, which
    comes down from how she carries it in the air to stand on the ground in front of her; getting up, she draws it
    to her as she holds it at attention (`rest`)."""
    land, hold, rise = make_hurt_landing(rest, nb_limbs, feet, air, HURT_ARMS, None, bare)

    def planted(f, w):
        k = tween([dict(at=0, **SPEAR_AIR), dict(at=1, **SPEAR_HURT)], w)
        hand = np.array(k["hand"], float)
        g = with_spear(f, nb_limbs, hand, k["aim"], what="SpearHurt", tolerance=SPEAR_HURT_REACH)
        grip, toward = spear_of(g, nb_limbs)
        butt = grip[1] - SPEAR_BUTT * toward[1]  # how high the end of the shaft is
        if w >= 1.0 or butt < 0.0:  # standing on the ground
            hand[1] -= butt
            g = with_spear(f, nb_limbs, hand, k["aim"], what="SpearHurt", tolerance=SPEAR_HURT_REACH)
        return g

    land = np.array([planted(f, smoothstep(i / 3)) for i, f in enumerate(land)])
    hold = np.array([planted(f, 1.0) for f in hold])
    out = []
    for i, f in enumerate(rise):
        eased = blend_frames(planted(f, 1.0), rest, smoothstep((i + 1) / len(rise)))
        g = f.copy()
        for limb in NB_R_ARM:
            g[limb + 1] = eased[limb + 1]
        grip, toward = spear_of(g, nb_limbs)
        butt = grip[1] - SPEAR_BUTT * toward[1]
        if butt < 0.0:  # (its end stays on the ground, not in it)
            g = hold_spear(g, nb_limbs, grip - [0.0, butt, 0.0], toward, what="SpearHurtRise", tolerance=SPEAR_HURT_REACH)
        out.append(g)
    out[-1] = rest.copy()
    return land, hold, np.array(out)


# Hopping sideways from her stance she keeps the spear couched, her arms turning with her body; where her legs come up
# past it (her trailing leg hopping to her left, her leading leg as she lands to her right) both hands lift it
# clear: by SPEAR_HOP_LIFT (model units), as much of that as SPEAR_HOP_LIFTED says frame by frame (hop to her right:
# True).
SPEAR_HOP_LIFT = (-350.0, 800.0, 0.0)
SPEAR_HOP_LIFTED = {True: [(4, 0.0), (6, 1.0), (8, 0.0)], False: [(0, 0.0), (1, 1.0), (5, 1.0), (7, 0.0)]}


def make_spear_hop(hop, nb_limbs, lifted):
    weights = [float(tween([dict(at=at, w=w) for at, w in lifted], i)["w"]) for i in range(len(hop))]
    holds = []
    for f, w in zip(hop, weights):
        own = spear_hold(f, nb_limbs)
        holds.append(spear_hold_from(f, nb_limbs, np.array(own["hand"]) + w * np.array(SPEAR_HOP_LIFT), own["aim"],
                                     own["left"]))
    out = hold_spear_through(hop, nb_limbs, holds, "SpearSideHop")
    for i, w in enumerate(weights):  # (where it isn't lifted her arms are the guard's own)
        if w == 0.0:
            out[i] = hop[i]
    return out


def make_spear_set(tables, nb_limbs, feet, spear, relaxed, air, reflections):
    """-> every animation of hers with the spear: name (as in gPlayAsNabooruSpear<name>Anim; where the same move has
    an animation for her scimitars, <name> is that one's) -> (table, note)."""
    T = lambda name: tables[f"gPlayAsNabooru{name}Anim"]
    idle, walk, run = T("GuardIdle"), T("GuardWalk"), T("GuardRun")
    neutral, slash, jump = T("Neutral"), T("Slash"), T("Jump")
    stance, attention = couched(neutral[0], run), idle[0]
    out = {}

    def carried(name, frames, note, allow=0, two_handed=False):
        out[name] = (keep_spear_clear(frames, nb_limbs, spear, f"Spear{name}", allow=allow, two_handed=two_handed),
                     note)

    out["ArmedIdle"] = (idle, "the guard standing at attention")
    out["ArmedWalk"] = (walk, "the guard's walk, spear shouldered")
    out["ArmedRun"] = (run, "the guard's run, spear couched in both hands")
    lean = forward_lean(nb_limbs, run) - forward_lean(nb_limbs, walk) + 4.0
    out["ArmedRunBack"] = (make_run_back(run, nb_limbs, lean), "the guard's run upright, for running backwards")
    out["ArmedTired"] = (make_spear_tired(attention, nb_limbs), "bent over, leaning on the spear")
    out["Neutral"] = (np.array([couched(f, run) for f in neutral]), "the fighter's stance, spear couched")
    out["FightWalk"] = (np.array([couched(f, run) for f in T("FightWalk")]), "the fighter's steps, spear couched")
    side = T("Sidestep")
    out["Sidestep"] = (np.array([couched(f, run) for f in side]), "the fighter's sidestep, spear couched")
    world = fk(nb_limbs, stance)[0]
    mirrored = mirror_table(side, nb_limbs, reflections)
    out["SidestepMirror"] = (np.array([couched(squared(f, nb_limbs, world, 0.85), run) for f in mirrored]),
                             "the sidestep mirrored, her left side still leading")
    out["Guard"] = (make_spear_guard(T("Guard"), nb_limbs), "blocking with the shaft")
    # attacks
    out["Jab"] = (make_jab(slash, stance, nb_limbs), "a short thrust in both hands")
    out["Lunge"] = (make_lunge(stance, nb_limbs, feet), "a passing lunge, deep, her left arm flung back")
    out["Sweep"] = (make_spear_sweep(T("GuardSlash"), T("GuardSlashRecover")),
                    "the guard's unused sweep and her recovery to attention")
    out["Charge"] = (make_charge(run, slash, stance, nb_limbs), "running in with the spear couched, then a thrust")
    plunge = make_plunge(air, jump, stance, nb_limbs)
    out["Plunge"] = (plunge, "from the air: the spear driven down as she lands")
    out["Leap"] = (make_spear_leap(stance, plunge, nb_limbs, feet), "Z+A: crouching, leaping into the plunge")
    carried("SpinAttack", T("SpinAttack"), "the fighter's leaping spin, the spear swung round her")
    # acrobatics and falls, the spear carried
    carried("Flip", T("Flip"), "the fighter's backflip")
    carried("Jump", jump, "the fighter's landing")
    out["Air"] = (np.array([with_spear(air, nb_limbs, what="SpearAir", **SPEAR_AIR)]), "in the air")
    out["RunJumpArmed"] = (make_spear_run_jump(run, nb_limbs, air), "leaping off a ledge, spear couched")
    light = dict(stiff=LIGHT_LANDING_LEGS, nb_limbs=nb_limbs, upright=LIGHT_LANDING_UPRIGHT)
    for name, rest, kind in (("LandLowArmed", attention, {}), ("LandLowStance", stance, {}),
                             ("LandLightArmed", attention, light), ("LandLightStance", stance, light)):
        carried(name, make_low_landing(jump, rest, **kind), "landing, the spear carried",
                allow=5 if rest is attention else 0, two_handed=rest is stance)
        if rest is stance:  # her left hand comes back onto the shaft as she settles
            settled = out[name][0]
            out[name] = (regrip(settled, nb_limbs, [smoothstep((i - 6) / 6) for i in range(len(settled))],
                                f"Spear{name}"), out[name][1])
    for right, suffix in ((True, "R"), (False, "L")):
        hop = make_side_hop(stance, nb_limbs, feet, right, bare_arms=False)
        hop = make_spear_hop(hop, nb_limbs, SPEAR_HOP_LIFTED[right])
        inside = [spear.check(f, True)[0] for f in hop]
        if any(inside):
            sys.exit(f"SpearSideHopArmed{suffix}: the spear goes through her legs (points per frame: {inside})")
        out[f"SideHopArmed{suffix}"] = (hop, "side hop from her stance, spear couched, lifted clear of her legs")
    land, hold, rise = make_spear_hurt(attention, nb_limbs, feet, out["Air"][0][0], relaxed)
    out["HurtLandArmed"] = (land, "hurt by a fall: landing in a squat, the spear planted")
    out["HurtHoldArmed"] = (hold, "hurt by a fall: holding on to the spear")
    out["HurtRiseArmed"] = (rise, "hurt by a fall: getting up to attention")
    carried("Damage", T("Damage"), "the fighter's flinch")
    carried("Defeat", T("Defeat"), "the fighter knocked down")
    carried("BonkArmed", make_bonk(T("Defeat"), T("Crouch"), attention), "rolled into a wall", allow=5)
    carried("KnockFrontArmed", T("KnockFront"), "knocked down from behind")
    return out


# Left/right limb pairs, and the reflection that maps each limb's local frame onto its mirror image's.
NB_MIRROR = {1: 4, 2: 5, 3: 6, 8: 11, 9: 12, 10: 13}
NB_MIRROR.update({b: a for a, b in NB_MIRROR.items()})
MODEL_REFLECT = np.diag([-1.0, 1.0, 1.0])  # her left/right axis in model space is X
REFLECTIONS = [np.diag(d) for d in ((-1.0, 1, 1), (1, -1.0, 1), (1, 1, -1.0), (-1.0, -1, -1))]


def choose_reflections(nb_limbs, reference_table):
    """For every limb, the local reflection under which a symmetric pose maps onto itself (judged on the average
    rotations over a reference cycle, her walk), constrained by mirrored child joint offsets where there are any."""
    means = [rot_from_quat(quat_mean([quat_from_rot(rot_zyx(f[i + 1])) for f in reference_table]))
             for i in range(len(nb_limbs))]
    L = [None] * len(nb_limbs)
    for i, limb in enumerate(nb_limbs):  # parents first
        Lp = MODEL_REFLECT if limb.parent < 0 else L[limb.parent]
        m = NB_MIRROR.get(i, i)
        children = [c for c, l in enumerate(nb_limbs) if l.parent == i]

        def cost(Li):
            rot = np.linalg.norm(means[i] - Lp @ means[m] @ Li)
            offsets = sum(np.linalg.norm(Li @ nb_limbs[c].joint - nb_limbs[NB_MIRROR.get(c, c)].joint) for c in children)
            return offsets * 10 + rot

        L[i] = min(REFLECTIONS, key=cost)
    return L


def mirror_table(table, nb_limbs, L):
    """The same motion mirrored left to right."""
    out = table.copy()
    out[:, 0, 0] = -table[:, 0, 0]
    for f in range(len(table)):
        for i, limb in enumerate(nb_limbs):
            Lp = MODEL_REFLECT if limb.parent < 0 else L[limb.parent]
            out[f, i + 1] = zyx_from_rot(Lp @ rot_zyx(table[f, NB_MIRROR.get(i, i) + 1]) @ L[i])
    return out


def mirror_swords(swords, L):
    """Mirrored scimitars: swapped hands, each reflected in its hand's frame."""
    out = swords.copy()
    for f in range(len(swords)):
        for j, hand in ((1, 10), (2, 13)):
            out[f, j] = zyx_from_rot(L[hand] @ rot_zyx(swords[f, 3 - j]) @ L[hand])
    return out


def verify_mirror(nb_limbs, table, mirrored):
    """Max distance between each mirrored joint and the reflection of its counterpart."""
    err = 0.0
    for f in range(len(table)):
        _, P = fk(nb_limbs, table[f])
        _, Pm = fk(nb_limbs, mirrored[f])
        for i in range(len(nb_limbs)):
            if i == NB_BLANK:  # an asymmetric helper bone with no geometry
                continue
            err = max(err, np.linalg.norm(Pm[i] - MODEL_REFLECT @ P[NB_MIRROR.get(i, i)]))
    return err


def continuous(table: np.ndarray) -> np.ndarray:
    """The same rotations, each frame using whichever of its two equivalent Euler forms, (x, y, z) or
    (x + 180, 180 - y, z + 180), is closer to the previous frame's: the game interpolates between frames angle by
    angle, so a flip between the forms would swing the limb all the way round."""
    out = table.copy()
    wrap = lambda v: ((v + 0x8000) & 0xFFFF) - 0x8000
    for f in range(1, len(out)):
        for j in range(1, out.shape[1]):
            x, y, z = (int(v) for v in out[f, j])
            alt = (wrap(x + 0x8000), wrap(0x8000 - y), wrap(z + 0x8000))
            prev = out[f - 1, j]
            dist = lambda r: sum(abs(wrap(a - int(b))) for a, b in zip(r, prev))
            if dist(alt) < dist((x, y, z)):
                out[f, j] = alt
    return out


def encode_animation(table: np.ndarray) -> bytes:
    """SoH binary Normal animation (no static values: index k*frames + frame)."""
    table = continuous(table)
    frames, joints, _ = table.shape
    values, indices = [], []
    for j in range(joints):
        idx = []
        for c in range(3):
            idx.append(len(values))
            values.extend(int(v) & 0xFFFF for v in table[:, j, c])
        indices.append(idx)
    body = struct.pack("<Ih", 0, frames)
    body += struct.pack("<I", len(values)) + struct.pack(f"<{len(values)}H", *values)
    body += struct.pack("<I", len(indices)) + b"".join(struct.pack("<3H", *i) for i in indices)
    body += struct.pack("<h", 0)  # staticIndexMax
    return RESOURCE_HEADER + body


def write_debug_c(path: Path, name: str, table: np.ndarray):
    frames, joints, _ = table.shape
    base = name[: -len("Anim")]
    vals, idx = [], []
    for j in range(joints):
        row = []
        for c in range(3):
            row.append(len(vals))
            vals.extend(int(v) for v in table[:, j, c])
        idx.append(row)
    lines = [f"s16 {base}FrameData[] = {{"]
    # decomp-style hex (Fast64's C parser expects it)
    lines += [f"    {'-' if v < 0 else ' '}0x{abs(v):04X}," for v in vals] + ["};", ""]
    lines += [f"JointIndex {base}JointIndices[] = {{"]
    lines += [f"    {{ {x}, {y}, {z} }}," for x, y, z in idx] + ["};", ""]
    lines += [f"AnimationHeader {name} = {{", f"    {{ {frames} }}, {base}FrameData, {base}JointIndices, 0", "};", ""]
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--decomp", type=Path, default=Path.home() / "oot")
    ap.add_argument("--version", default="pal-1.0")
    ap.add_argument("--debug-c", type=Path, help="also write decomp-style C of each animation here (for Fast64)")
    ap.add_argument("--icons-preview", type=Path, help="also write the item icons, enlarged, to this PNG")
    ap.add_argument("--sheets", type=Path, help="also draw contact sheets of the animations (see posesheet.py) here")
    ap.add_argument("--sheet-filter", default="Spear", help="which animations to draw: a regular expression")
    ap.add_argument("--lenient", action="store_true", help="report hands that cannot reach the spear, carry on")
    ap.add_argument("--quick", action="store_true", help="skip keeping the spear clear of her (not for release)")
    ap.add_argument("--steps", help="print how far her arms turn frame by frame in the spear animations matching this")
    args = ap.parse_args()

    global LENIENT, QUICK
    LENIENT, QUICK = args.lenient, args.quick
    ootanim.ASSETS = args.decomp / "extracted" / args.version / "assets" / "objects"
    ootanim.DECOMP_ASSETS = args.decomp / "assets" / "objects"

    nb_limbs = parse_skeleton(*NB)
    assert len(nb_limbs) == NB_LIMBS
    nb_walk = parse_anim("object_nb", "gNabooruWalkAnim", NB_LIMBS)
    src_limbs = {}

    resources = {}
    tables = {}
    sword_tables = {}
    retargeted = {}  # before her own arm swing and sway
    for job in JOBS:
        src = job.source
        if src.obj not in src_limbs:
            src_limbs[src.obj] = parse_skeleton(src.obj, src.skel, src.prefix)
        limbs = src_limbs[src.obj]
        anim = parse_anim(src.obj, job.anim, len(limbs))
        table = retarget(src, anim, nb_walk.table[0])
        retargeted[job.out] = table
        skip = ()
        if job.arms_from_walk:
            table = arms_from_walk(table, nb_limbs, nb_walk.table)
            skip = NB_L_ARM + NB_R_ARM + (NB_TORSO, NB_HEAD, 15, 16)  # torso moves everything above it
        if src is WHITE:
            table[:, 0] = tables["gPlayAsNabooruStandAnim"][0, 0]
        err = verify(src, nb_limbs, limbs, table, anim.table, skip)
        note = " (arms, torso sway from her walk)" if job.arms_from_walk else ""
        print(f"{job.out}: {anim.frames} frames from {job.anim}{note}; max joint error {err:.2f} units")
        if err > 2.0:
            sys.exit(f"retarget check failed for {job.out}")
        if job.write:
            resources[f"objects/object_nb/{job.out}"] = encode_animation(table)
        tables[job.out] = table
        if src is FIGHTER:
            swords = sword_table(anim)
            sword_err = verify_swords(nb_limbs, limbs, table, anim.table, swords)
            if sword_err > 1e-3:
                sys.exit(f"sword check failed for {job.out}: {sword_err}")
            resources[f"objects/object_nb/{job.out[: -len('Anim')]}SwordsAnim"] = encode_animation(swords)
            sword_tables[job.out] = swords
        if args.debug_c:
            args.debug_c.mkdir(parents=True, exist_ok=True)
            write_debug_c(args.debug_c / f"{job.out}.c", job.out, table)

    def add(name, table, note, swords=None):
        tables[f"gPlayAsNabooru{name}Anim"] = table
        resources[f"objects/object_nb/gPlayAsNabooru{name}Anim"] = encode_animation(table)
        if swords is not None:
            resources[f"objects/object_nb/gPlayAsNabooru{name}SwordsAnim"] = encode_animation(swords)
        print(f"gPlayAsNabooru{name}Anim: {len(table)} frames, {note}")
        if args.debug_c:
            write_debug_c(args.debug_c / f"gPlayAsNabooru{name}Anim.c", f"gPlayAsNabooru{name}Anim", table)

    relaxed = make_relaxed(tables["gPlayAsNabooruStandAnim"], nb_walk.table, nb_limbs)
    add("Relaxed", relaxed, "her stance with relaxed, swaying arms")
    add("Tired", make_tired(relaxed[0], nb_limbs), "relaxed stance bent over, breathing hard")
    # the fighter's grip, for anything without sword data of its own
    resources["objects/object_nb/gPlayAsNabooruCarrySwordsAnim"] = encode_animation(np.zeros((1, 3, 3), int))
    guard = make_guard(tables["gPlayAsNabooruBlockAnim"], tables["gPlayAsNabooruNeutralAnim"], nb_limbs)
    add("Guard", guard, "crossed-scimitar block standing upright", sword_tables["gPlayAsNabooruBlockAnim"][:1])

    # with scimitars, standing: her relaxed stance, arms at her sides, blades hanging down
    clearance = BladeClearance(nb_limbs)
    armed_idle = np.array([hold_blades(f, nb_limbs, HOLD_IDLE) for f in relaxed])
    # bent over, her arms swing out a little so the blades clear her legs
    armed_tired = make_tired(hold_blades(relaxed[0], nb_limbs, HOLD_TIRED), nb_limbs)
    stand = tables["gPlayAsNabooruStandAnim"]
    # empty-handed idle: she folds her arms (the white-clothed Gerudo's unused motion) and keeps them folded
    stand0 = stand[0]
    add("CrossedArms", make_crossed(tables["gPlayAsNabooruWhiteIdleAnim"][0], stand0, nb_limbs),
        "white-clothed Gerudo's folded arms, breathing and looking around")
    add("FoldArms", make_fold(tables["gPlayAsNabooruWhiteFoldArmsAnim"], stand0), "folding her arms")

    air = make_air(relaxed[0], mean_frame(nb_walk.table), nb_limbs, arm_out=25, elbow=45, hips=(40, 25), knees=(85, 65),
                   lean=12)
    add("Air", air, "in the air: knees drawn up, arms out")
    run = tables["gPlayAsNabooruRunAnim"]
    lean = forward_lean(nb_limbs, run) - forward_lean(nb_limbs, nb_walk.table) + 4.0
    add("RunBack", make_run_back(run, nb_limbs, lean), f"run leaning back {lean:.1f} degrees, for running backwards")
    add("RunJump", make_run_jump(run, nb_limbs, air[0]), "leaping off a ledge: her run's stride carried into the air")

    # moving with scimitars out: her own walk/run arm swing (less of it running), wrists turned to keep the blades off
    # her body
    armed_run = damp_swing(arms_from_walk(retargeted["gPlayAsNabooruRunAnim"], nb_limbs, nb_walk.table,
                                          ARMED_RUN_CORE_SWAY_GAIN), NB_L_ARM + NB_R_ARM, ARMED_RUN_SWING)
    print("shoulder twist/roll over a stride: run %.0f/%.0f degrees, armed run %.0f/%.0f"
          % (*shoulder_sway(nb_limbs, run), *shoulder_sway(nb_limbs, armed_run)))
    armed = {"ArmedIdle": armed_idle, "ArmedTired": armed_tired,
             "ArmedWalk": np.array([hold_blades(f, nb_limbs, HOLD_WALK) for f in nb_walk.table]),
             "ArmedRun": np.array([hold_blades(f, nb_limbs, HOLD_RUN) for f in armed_run]),
             "RunJumpArmed": make_run_jump(armed_run, nb_limbs, air[0], HOLD_RUN, clearance),
             "ArmedRunBack": np.array([hold_blades(f, nb_limbs, HOLD_RUN)
                                       for f in make_run_back(armed_run, nb_limbs, lean)])}
    jump = tables["gPlayAsNabooruJumpAnim"]
    soft = make_soft_landing(jump, relaxed[0])
    add("LandLow", make_low_landing(jump, relaxed[0], arms_from=air[0]),
        "landing with the knees half as bent, arms coming down from the air, settling relaxed")
    armed["LandLowArmed"] = make_low_landing(jump, armed_idle[0], clearance)
    # Z-targeting with scimitars (side hops): settling into her fighting stance, which comes next
    armed["LandLowStance"] = make_low_landing(jump, tables["gPlayAsNabooruNeutralAnim"][0], clearance)
    # after a short drop: the same, only a light dip
    light = dict(stiff=LIGHT_LANDING_LEGS, nb_limbs=nb_limbs, upright=LIGHT_LANDING_UPRIGHT)
    add("LandLight", make_low_landing(jump, relaxed[0], arms_from=air[0], **light),
        "landing from a short drop: a light dip, arms coming down from the air, settling relaxed")
    armed["LandLightArmed"] = make_low_landing(jump, armed_idle[0], clearance, **light)
    armed["LandLightStance"] = make_low_landing(jump, tables["gPlayAsNabooruNeutralAnim"][0], clearance, **light)
    defeat, kneel = tables["gPlayAsNabooruDefeatAnim"], tables["gPlayAsNabooruCrouchAnim"]
    add("Bonk", make_bonk(defeat, kneel, relaxed[0]), "rolled into a wall: falls back, sits, gets up relaxed")
    bonk_armed, bonk_swords = make_bonk(defeat, kneel, armed_idle[0], sword_tables["gPlayAsNabooruDefeatAnim"],
                                        clearance)
    clipping = sum(clearance.count(f, s) for f, s in zip(bonk_armed[BONK_FALL[1] - BONK_FALL[0]:],
                                                          bonk_swords[BONK_FALL[1] - BONK_FALL[0]:]))
    if clipping:
        sys.exit(f"BonkArmed: scimitars cut through her body as she gets up ({clipping} points)")
    add("BonkArmed", bonk_armed, "the same, scimitars in hand, blades clear of her as she gets up", bonk_swords)
    # side hops, with their landings: bare from her idle, arms swinging; armed from her fighting stance, guard kept
    feet = FootBoxes(nb_limbs)
    neutral = tables["gPlayAsNabooruNeutralAnim"]
    for right, suffix in ((True, "R"), (False, "L")):
        add(f"SideHop{suffix}", make_side_hop(relaxed[0], nb_limbs, feet, right, bare_arms=True),
            f"side hop to her {'right' if right else 'left'} in an arc, landing: hips first, feet trailing")
        grip = sword_tables["gPlayAsNabooruNeutralAnim"][0]
        hop = make_side_hop(neutral[0], nb_limbs, feet, right, bare_arms=False, clearance=clearance, swords=grip)
        hop_swords = np.repeat(grip[None], len(hop), axis=0)
        clipping = sum(clearance.count(f, s) for f, s in zip(hop, hop_swords))
        if clipping:
            sys.exit(f"SideHopArmed{suffix}: scimitars cut through her body ({clipping} points)")
        add(f"SideHopArmed{suffix}", hop, "the same from her fighting stance, blades clear of her body", hop_swords)
    # landing hurt after a long fall: crouching hard, holding it, getting up
    for suffix, rest_pose, arms_cfg, blades in (("", relaxed[0], HURT_ARMS, None),
                                                ("Armed", armed_idle[0], HURT_ARMS_ARMED, clearance)):
        land, hold, rise = make_hurt_landing(rest_pose, nb_limbs, feet, air[0], arms_cfg, blades, relaxed[0])
        note = ", blades clear of her and the ground" if blades else ""
        add(f"HurtLand{suffix}", land, "hurt by a fall: landing in a deep squat" + note)
        add(f"HurtHold{suffix}", hold, "hurt by a fall: breathing hard in the squat" + note)
        add(f"HurtRise{suffix}", rise, "hurt by a fall: getting up" + note)
    reflections = choose_reflections(nb_limbs, nb_walk.table)
    # getting up from the guard's kneel: empty-handed, her right arm mirrors her left instead of holding a spear
    getup_bare = np.array([mirror_arm(f, nb_limbs, reflections, NB_L_ARM, NB_R_ARM)
                           for f in tables["gPlayAsNabooruCrouchAnim"]])
    add("GetUpBare", getup_bare, "the guard's kneel, right arm mirroring the left (no spear in hand)")
    # knocked down from behind: onto her left side, lying, getting up
    body = BodyPoints(nb_limbs)
    add("KnockFront", make_knock_front(relaxed[0], getup_bare, nb_limbs, body),
        "knocked down from behind: falls onto her left side, lies, gets up through the kneel")
    knock_armed = keep_blades_clear(make_knock_front(armed_idle[0], tables["gPlayAsNabooruCrouchAnim"], nb_limbs, body),
                                    nb_limbs, clearance)
    add("KnockFrontArmed", knock_armed, "the same with scimitars in hand, blades clear of her and the ground")
    add("FlipBare", make_bare_flip(tables["gPlayAsNabooruFlipAnim"], soft[5], relaxed[0], nb_limbs, reflections),
        "backflip landing relaxed, for empty hands")
    for name, table in armed.items():
        clipping = sum(clearance.count(f) for f in table)
        if clipping:
            sys.exit(f"{name}: scimitars cut through her body ({clipping} points)")
        add(name, table, "with scimitars, blades clear of her body")

    # the cut horizontal slash, with its recovery into her armed idle
    sweep = make_sweep(tables["gPlayAsNabooruGuardSlashAnim"], tables["gPlayAsNabooruGuardSlashRecoverAnim"],
                       armed_idle[0], clearance)
    swing = len(tables["gPlayAsNabooruGuardSlashAnim"]) - SWEEP_FROM
    print("Sweep: blade points inside her body per frame:", [clearance.count(f) for f in sweep])
    clipping = sum(clearance.count(f) for f in sweep[swing:])
    if clipping:
        sys.exit(f"Sweep: scimitars cut through her body as she recovers ({clipping} points)")
    add("Sweep", sweep, f"the spear guard's unused horizontal slash ({swing} frames), then recovering into her armed idle",
        np.zeros((len(sweep), 3, 3), int))

    side = tables["gPlayAsNabooruSidestepAnim"]
    side_mirror = mirror_table(side, nb_limbs, reflections)
    err = verify_mirror(nb_limbs, side, side_mirror)
    if err > 2.0:
        sys.exit(f"mirror check failed: {err}")
    add("SidestepMirror", side_mirror, f"sidestep mirrored (max joint error {err:.2f})",
        mirror_swords(sword_tables["gPlayAsNabooruSidestepAnim"], reflections))

    # with the spear (the guard's run as it is: the run above has her own arm swing)
    tables["gPlayAsNabooruGuardRunAnim"] = retargeted["gPlayAsNabooruRunAnim"]
    spear = SpearClearance(nb_limbs, clearance.boxes)
    for name, (table, note) in make_spear_set(tables, nb_limbs, feet, spear, relaxed[0], air[0], reflections).items():
        inside = [spear.check(f, True) for f in table]
        turn, joint, at = largest_step(table)
        print(f"  Spear{name}: most points of the spear inside her {max(n for n, _ in inside)}, its lowest point "
              f"{min(low for _, low in inside):.0f}; largest turn of a joint {turn:.0f} degrees (joint {joint}, into "
              f"frame {at})")
        add(f"Spear{name}", table, note)
        if args.steps and re.search(args.steps, name):  # how far each arm joint turns, frame by frame
            for f in range(1, len(table)):
                turns = [math.degrees(2 * math.acos(min(1.0, abs(float(
                    quat_from_rot(rot_zyx(table[f - 1, j + 1])) @ quat_from_rot(rot_zyx(table[f, j + 1])))))))
                    for j in NB_L_ARM + NB_R_ARM]
                grip, toward = spear_of(table[f], nb_limbs)
                print(f"    {f:2d}: left arm {turns[0]:4.0f} {turns[1]:4.0f} {turns[2]:4.0f}  right arm {turns[3]:4.0f} "
                      f"{turns[4]:4.0f} {turns[5]:4.0f}  grip {grip.round(0)} left hand {left_on_shaft(table[f], nb_limbs)[0]:.0f} along, "
                      f"{left_on_shaft(table[f], nb_limbs)[1]:.0f} off")
    if args.sheets:
        import posesheet
        body = posesheet.Body("object_nb", nb_limbs)
        mesh = posesheet.Mesh("object_gla", "gObjectGlaSpearDL")
        for name, table in tables.items():
            short = name[len("gPlayAsNabooru"): -len("Anim")]
            if not re.search(args.sheet_filter, short):
                continue
            every = max(1, -(-len(table) // 24))
            shown = list(range(0, len(table), every))
            weapons = (lambda k, W, P: [posesheet.held(mesh, W, P, NB_R_HAND)]) if short.startswith("Spear") else None
            posesheet.sheet(args.sheets / f"{short}.png", body, table[shown], views=(20, 100), weapons=weapons,
                            labels=shown, title=short, cell=330, cols=min(6, len(shown)), span=14500.0, ahead=0.66)

    # item icons: her scimitars for the Master Sword, the Gerudo spear for the Biggoron Sword
    item_icons = {"Scimitars": icons.scimitars_icon(), "Spear": icons.spear_icon()}
    for name, icon in item_icons.items():
        resources[f"textures/play_as_nabooru/gPlayAsNabooru{name}IconTex"] = encode_texture(icon)
        print(f"gPlayAsNabooru{name}IconTex: {icons.ICON_SIZE}x{icons.ICON_SIZE} item icon")
    if args.icons_preview:
        icons.preview(list(item_icons.values()), args.icons_preview)
    # and their names on the pause screen, in the game's lettering (English)
    lettering = names.Lettering(args.decomp / "extracted" / args.version / "assets" / "textures" / "item_name_static")
    for name, text in {"Scimitars": "Twin Scimitars", "Spear": "Gerudo Spear"}.items():
        pixels = names.to_ia4(*lettering.render(text))
        resources[f"textures/play_as_nabooru/gPlayAsNabooru{name}NameENGTex"] = (
            TEXTURE_HEADER + struct.pack("<4I", TEXTURE_IA4, names.WIDTH, names.HEIGHT, len(pixels)) + pixels)
        print(f"gPlayAsNabooru{name}NameENGTex: \"{text}\"")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in resources.items():
            z.writestr(path, data)
    print(f"wrote {len(resources)} resource(s) to {args.out}")


if __name__ == "__main__":
    main()
