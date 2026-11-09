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
import math
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
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


@dataclass
class Job:
    out: str
    source: Source
    anim: str
    arms_from_walk: bool = False  # replace arms with Nabooru's own (phase-matched, exaggerated) walk swing


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
]

# How much to exaggerate her walk arm swing when running (upper arm, forearm, hand).
RUN_ARM_SWING_GAIN = (1.4, 1.2, 1.0)
# Her walk's torso twist and head sway, layered on the run's own lean (Nabooru limb -> gain).
NB_TORSO, NB_HEAD = 7, 14
NB_BLANK = 15
RUN_CORE_SWAY_GAIN = {NB_TORSO: 2.3, NB_HEAD: 1.0}

RESOURCE_HEADER = bytes.fromhex("00000000" "4d4e414f" "00000000" "efbeaddeefbeadde") + bytes(64 - 20)


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


SCIMITAR_BLADE = np.array([-0.25, -1.0, 0.0])  # blade direction in the scimitar's own frame (tip at -882, -3350)
IDLE_BLADE = np.array([0.3, -0.45, 0.85])  # left blade in model space: forward, down, a little outward (+X = her left)


def resting_swords(base_frame, nb_limbs, roll):
    """Scimitars for an idle with her arms hanging: blades pointing forward and down, clear of her legs.
    `roll` (+1/-1) picks which way the flat of the blade faces."""
    W, _ = fk(nb_limbs, base_frame)
    out = np.zeros((1, 3, 3), int)
    a1 = SCIMITAR_BLADE / np.linalg.norm(SCIMITAR_BLADE)
    a3 = np.array([0.0, 0.0, 1.0])
    A = np.column_stack([a1, np.cross(a3, a1), a3])
    for joint, hand, side in ((1, 10, 1.0), (2, 13, -1.0)):
        d = IDLE_BLADE * np.array([side, 1, 1])
        d /= np.linalg.norm(d)
        lateral = np.array([side * roll, 0.0, 0.0])
        b3 = lateral - lateral.dot(d) * d
        b3 /= np.linalg.norm(b3)
        B = np.column_stack([d, np.cross(b3, d), b3])
        out[0, joint] = zyx_from_rot(W[hand].T @ B @ A.T)
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


def encode_animation(table: np.ndarray) -> bytes:
    """SoH binary Normal animation (no static values: index k*frames + frame)."""
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
        err = verify(src, nb_limbs, limbs, table, anim.table, skip)
        note = " (arms, torso sway from her walk)" if job.arms_from_walk else ""
        print(f"{job.out}: {anim.frames} frames from {job.anim}{note}; max joint error {err:.2f} units")
        if err > 2.0:
            sys.exit(f"retarget check failed for {job.out}")
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
    # resting grip: blades forward and down, their curve rising towards the tip
    resources["objects/object_nb/gPlayAsNabooruRestingSwordsAnim"] = encode_animation(resting_swords(relaxed[0], nb_limbs, -1))
    add("Tired", make_tired(relaxed[0], nb_limbs), "relaxed stance bent over, breathing hard")
    guard = make_guard(tables["gPlayAsNabooruBlockAnim"], tables["gPlayAsNabooruNeutralAnim"], nb_limbs)
    add("Guard", guard, "crossed-scimitar block standing upright", sword_tables["gPlayAsNabooruBlockAnim"][:1])

    reflections = choose_reflections(nb_limbs, nb_walk.table)
    side = tables["gPlayAsNabooruSidestepAnim"]
    side_mirror = mirror_table(side, nb_limbs, reflections)
    err = verify_mirror(nb_limbs, side, side_mirror)
    if err > 2.0:
        sys.exit(f"mirror check failed: {err}")
    add("SidestepMirror", side_mirror, f"sidestep mirrored (max joint error {err:.2f})",
        mirror_swords(sword_tables["gPlayAsNabooruSidestepAnim"], reflections))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in resources.items():
            z.writestr(path, data)
    print(f"wrote {len(resources)} resource(s) to {args.out}")


if __name__ == "__main__":
    main()
