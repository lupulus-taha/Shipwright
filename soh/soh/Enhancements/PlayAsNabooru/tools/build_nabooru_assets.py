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


def arms_from_walk(run_table, nb_limbs, walk_table):
    """Arms swing like her walk (exaggerated); torso and head get her walk's twist/sway on top of the run's lean."""
    arms = {limb: gain for side in (NB_L_ARM, NB_R_ARM) for limb, gain in zip(side, RUN_ARM_SWING_GAIN)}
    out = overlay_walk_motion(run_table, nb_limbs, walk_table, arms, keep_run_pose=False)
    return overlay_walk_motion(out, nb_limbs, walk_table, RUN_CORE_SWAY_GAIN, keep_run_pose=True)


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


# Side hop (Link's is 6 frames in the air and under 7 units high): a crouch and a push-off from her toes while her
# feet are still on the ground (the game keeps them there, the player is already rising), then a low skip with her
# legs apart, reaching for the ground. Per frame of Link's hop, for a hop to her right: Link's height (game units;
# 0 while her feet are kept down), her hips (model units) and lean towards the hop (degrees), torso bend, each foot's
# (sideways reach, height of its lowest point above the ground in game units, toes-down pitch) for the leading and
# trailing leg, and how far her arms have gone from her idle's to her pose in the air (negative: swung back).
SIDE_HOP = [
    dict(h=0.0, hips=-450, lean=0, bend=10, lead=(0, 0.0, 0), trail=(0, 0.0, 0), arms=-1.0),  # crouch
    dict(h=0.0, hips=250, lean=7, bend=4, lead=(0, 0.0, 35), trail=(0, 0.0, 40), arms=0.5),  # push-off, on her toes
    dict(h=6.0, hips=-250, lean=9, bend=2, lead=(250, 1.6, 25), trail=(-120, 2.4, 35), arms=0.85),
    dict(h=6.8, hips=-300, lean=7, bend=2, lead=(380, 1.8, 20), trail=(-200, 2.8, 30), arms=1.0),
    dict(h=6.0, hips=-250, lean=4, bend=2, lead=(420, 1.2, 15), trail=(-150, 2.4, 25), arms=1.0),
    dict(h=3.8, hips=-250, lean=1, bend=2, lead=(350, 0.4, 5), trail=(-60, 1.6, 15), arms=1.0),
    dict(h=0.0, hips=-300, lean=0, bend=4, lead=(150, 0.0, 0), trail=(0, 0.6, 10), arms=1.0),  # touching down
]
SIDE_HOP_ARMS_BACK = 12  # degrees her arms swing back in the crouch
SIDE_HOP_LEAD_ARM = 10  # degrees the arm on the side she hops to goes further out


def make_side_hop(base, nb_limbs, feet, right, air=None):
    """A side hop from `base` (her idle, or her fighting stance) to her right or left. With `air` (her pose in the
    air) her arms swing back, then out to it; without, her upper body keeps `base`'s (her stance's guard)."""
    side = -1 if right else 1  # model +X is her left
    lead, trail = (NB_R_LEG, NB_L_LEG) if right else (NB_L_LEG, NB_R_LEG)
    lead_arm = NB_R_ARM if right else NB_L_ARM
    ground = {foot: feet.lowest(base, foot) for foot in (NB_L_LEG[2], NB_R_LEG[2])}
    _, standing = fk(nb_limbs, base)  # where her ankles are
    out = []
    for key in SIDE_HOP:
        f = base.copy()
        f[0, 1] += key["hips"]
        rotate_world(f, nb_limbs, 0, Z_AXIS, -side * key["lean"])
        bend_forward(f, nb_limbs, NB_TORSO, key["bend"])
        if air is not None:
            w = key["arms"]
            if w < 0:
                for upper in (NB_L_ARM[0], NB_R_ARM[0]):
                    rotate_world(f, nb_limbs, upper, X_AXIS, -w * SIDE_HOP_ARMS_BACK)
            else:
                posed = blend_frames(base, air, w)
                for limb in NB_L_ARM + NB_R_ARM:
                    f[limb + 1] = posed[limb + 1]
                rotate_world(f, nb_limbs, lead_arm[0], Z_AXIS, side * SIDE_HOP_LEAD_ARM * w)
        for leg, (dx, clearance, toes) in ((lead, key["lead"]), (trail, key["trail"])):
            height = ground[leg[2]] + (clearance - key["h"]) * 100
            f = place_foot(f, nb_limbs, feet, leg, standing[leg[2]] + [side * dx, 0, 0], height, toes)
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

    def count(self, frame, swords=None):
        W, P = fk(self.limbs, frame)
        n = 0
        for j, hand in SWORD_HAND.items():
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
NB_LEG_JOINTS = (0, 1, 2, 3, 4, 5, 6, 18)  # table rows: root translation and rotation, legs, waist


LANDING_ARMS_FRAMES = 8  # empty-handed, her arms settle from the in-air pose to her sides over these frames


def make_low_landing(jump, rest, clearance=None, arms_from=None):
    """The fighter's landing with her knees bent about half as much; her arms and upper body ease into `rest`
    over the second half instead of rising into her fighting stance. With `clearance` (she holds scimitars), any
    frame where a blade would cut her takes more of `rest`'s upper body, until it doesn't. With `arms_from` (her pose
    in the air), her arms come down from it to `rest`'s instead of following the fighter's, which fling out wide."""
    out = []
    for f in range(len(jump)):
        settle = min(max((f - 8) / (len(jump) - 9), 0.0), 1.0)
        legs = blend_frames(jump[f], rest, LOW_LANDING_LEGS + (1 - LOW_LANDING_LEGS) * settle)
        for upper in np.arange(settle, 1.0001, 0.1):
            frame = blend_frames(jump[f], rest, min(upper, 1.0))
            frame[list(NB_LEG_JOINTS)] = legs[list(NB_LEG_JOINTS)]
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
    args = ap.parse_args()

    ootanim.ASSETS = args.decomp / "extracted" / args.version / "assets" / "objects"
    ootanim.DECOMP_ASSETS = args.decomp / "assets" / "objects"

    nb_limbs = parse_skeleton(*NB)
    assert len(nb_limbs) == NB_LIMBS
    nb_walk = parse_anim("object_nb", "gNabooruWalkAnim", NB_LIMBS)
    src_limbs = {}

    resources = {}
    tables = {}
    sword_tables = {}
    for job in JOBS:
        src = job.source
        if src.obj not in src_limbs:
            src_limbs[src.obj] = parse_skeleton(src.obj, src.skel, src.prefix)
        limbs = src_limbs[src.obj]
        anim = parse_anim(src.obj, job.anim, len(limbs))
        table = retarget(src, anim, nb_walk.table[0])
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

    # moving with scimitars out: her own walk/run arm swing (less of it running), wrists turned to keep the blades off
    # her body
    armed_run = damp_swing(run, NB_L_ARM + NB_R_ARM, ARMED_RUN_SWING)
    armed = {"ArmedIdle": armed_idle, "ArmedTired": armed_tired,
             "ArmedWalk": np.array([hold_blades(f, nb_limbs, HOLD_WALK) for f in nb_walk.table]),
             "ArmedRun": np.array([hold_blades(f, nb_limbs, HOLD_RUN) for f in armed_run]),
             "ArmedRunBack": np.array([hold_blades(f, nb_limbs, HOLD_RUN)
                                       for f in make_run_back(armed_run, nb_limbs, lean)])}
    jump = tables["gPlayAsNabooruJumpAnim"]
    soft = make_soft_landing(jump, relaxed[0])
    add("LandLow", make_low_landing(jump, relaxed[0], arms_from=air[0]),
        "landing with the knees half as bent, arms coming down from the air, settling relaxed")
    armed["LandLowArmed"] = make_low_landing(jump, armed_idle[0], clearance)
    # Z-targeting with scimitars (side hops): settling into her fighting stance, which comes next
    armed["LandLowStance"] = make_low_landing(jump, tables["gPlayAsNabooruNeutralAnim"][0], clearance)
    defeat, kneel = tables["gPlayAsNabooruDefeatAnim"], tables["gPlayAsNabooruCrouchAnim"]
    add("Bonk", make_bonk(defeat, kneel, relaxed[0]), "rolled into a wall: falls back, sits, gets up relaxed")
    bonk_armed, bonk_swords = make_bonk(defeat, kneel, armed_idle[0], sword_tables["gPlayAsNabooruDefeatAnim"],
                                        clearance)
    clipping = sum(clearance.count(f, s) for f, s in zip(bonk_armed[BONK_FALL[1] - BONK_FALL[0]:],
                                                          bonk_swords[BONK_FALL[1] - BONK_FALL[0]:]))
    if clipping:
        sys.exit(f"BonkArmed: scimitars cut through her body as she gets up ({clipping} points)")
    add("BonkArmed", bonk_armed, "the same, scimitars in hand, blades clear of her as she gets up", bonk_swords)
    # side hops: bare from her idle, arms out as in the air; armed from her fighting stance, guard kept
    feet = FootBoxes(nb_limbs)
    neutral = tables["gPlayAsNabooruNeutralAnim"]
    for right, suffix in ((True, "R"), (False, "L")):
        add(f"SideHop{suffix}", make_side_hop(relaxed[0], nb_limbs, feet, right, air[0]),
            f"side hop to her {'right' if right else 'left'}: crouch, push-off, low skip")
        hop = make_side_hop(neutral[0], nb_limbs, feet, right)
        hop_swords = np.repeat(sword_tables["gPlayAsNabooruNeutralAnim"][:1], len(hop), axis=0)
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
    add("FlipBare", make_bare_flip(tables["gPlayAsNabooruFlipAnim"], soft[5], relaxed[0], nb_limbs, reflections),
        "backflip landing relaxed, for empty hands")
    for name, table in armed.items():
        clipping = sum(clearance.count(f) for f in table)
        if clipping:
            sys.exit(f"{name}: scimitars cut through her body ({clipping} points)")
        add(name, table, "with scimitars, blades clear of her body")

    side = tables["gPlayAsNabooruSidestepAnim"]
    side_mirror = mirror_table(side, nb_limbs, reflections)
    err = verify_mirror(nb_limbs, side, side_mirror)
    if err > 2.0:
        sys.exit(f"mirror check failed: {err}")
    add("SidestepMirror", side_mirror, f"sidestep mirrored (max joint error {err:.2f})",
        mirror_swords(sword_tables["gPlayAsNabooruSidestepAnim"], reflections))

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
