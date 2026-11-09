#!/usr/bin/env python3
"""Builds the extra assets for the "Play as Nabooru" enhancement from an extracted OoT decomp.

No game data is stored in the repository: everything is generated from the user's own decomp extraction
(`make setup` / `tools.assets.extract`) and written to a mod archive for SoH's `mods` folder.

Gerudo NPCs share one skeleton rig: the spear guard (object_gla) has exactly Nabooru's joint offsets, only with a
different limb order and a few zero-offset helper limbs (head/hand roots, veil, spear). So guard animations are
retargeted by copying limb rotations and folding the helper limbs into the limb they wrap.

usage: build_nabooru_assets.py --out play_as_nabooru.o2r [--decomp ~/oot] [--version pal-1.0] [--debug-c DIR]
"""
import argparse
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import ootanim  # noqa: E402
from ootanim import fk, parse_anim, parse_skeleton, rot_zyx, zyx_from_rot  # noqa: E402

NB = ("object_nb", "gNabooruSkel", "NABOORU_LIMB_")
GLA = ("object_gla", "gObjectGlaSkel", "OBJECT_GLA_LIMB_")

# Nabooru limb index -> how to build its local rotation from guard (gla) limb rotations G[k].
#   int k          : copy G[k]
#   ("mul", a, b)  : G[a] @ G[b]            (b is a zero-offset child of a, folded into one limb)
#   ("inv", a, b)  : G[a]^-1 @ G[b]         (re-parent b from a's parent onto a)
#   "static"       : keep Nabooru's own pose from her walk cycle
GLA_TO_NB = {
    0: 0,  # root
    1: 14, 2: 15, 3: 16,  # left thigh, shin, foot
    4: 17, 5: 18, 6: 19,  # right thigh, shin, foot
    7: 1,  # torso
    8: 11, 9: 12, 10: 13,  # left arm, forearm, hand
    11: 6, 12: 7,  # right arm, forearm
    13: ("mul", 8, 9),  # right hand = hand root * hand
    14: ("mul", 2, 5),  # head = head root * head
    15: "static",  # "empty" limb above the head
    16: ("inv", 5, 3),  # ponytail hangs off head root in gla, off head in Nabooru
    17: 20,  # pelvis
}

# Animations to generate: output resource name -> (guard animation)
GUARD_ANIMS = {
    "gPlayAsNabooruRunAnim": "gObjectGlaRunAnim",
}

RESOURCE_HEADER = bytes.fromhex("00000000" "4d4e414f" "00000000" "efbeaddeefbeadde") + bytes(64 - 20)


def retarget_from_guard(gla_anim, nb_static_frame):
    frames = gla_anim.frames
    out = np.zeros((frames, 19, 3), int)
    for f in range(frames):
        t = gla_anim.table[f]
        G = [rot_zyx(t[k + 1]) for k in range(21)]
        out[f, 0] = t[0]  # root translation (same rig, same proportions)
        for nb, src in GLA_TO_NB.items():
            if isinstance(src, int):
                out[f, nb + 1] = t[src + 1]
            elif src == "static":
                out[f, nb + 1] = nb_static_frame[nb + 1]
            elif src[0] == "mul":
                out[f, nb + 1] = zyx_from_rot(G[src[1]] @ G[src[2]])
            elif src[0] == "inv":
                out[f, nb + 1] = zyx_from_rot(G[src[1]].T @ G[src[2]])
    return out


def verify_against_guard(nb_limbs, gla_limbs, nb_table, gla_table):
    """Same rig -> every mapped Nabooru joint must land where the guard's joint is."""
    pairs = {1: 14, 2: 15, 3: 16, 4: 17, 5: 18, 6: 19, 7: 1, 8: 11, 9: 12, 10: 13, 11: 6, 12: 7, 13: 8, 14: 2, 16: 3}
    worst, worst_rot = 0.0, 0.0
    for f in range(len(nb_table)):
        Wn, Pn = fk(nb_limbs, nb_table[f])
        Wg, Pg = fk(gla_limbs, gla_table[f])
        for n, g in pairs.items():
            worst = max(worst, float(np.linalg.norm(Pn[n] - Pg[g])))
        # orientation check on the folded limbs (right hand, head, ponytail)
        for n, g in ((13, 9), (14, 5), (16, 3)):
            worst_rot = max(worst_rot, float(np.abs(Wn[n] - Wg[g]).max()))
    return worst, worst_rot


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
    gla_limbs = parse_skeleton(*GLA)
    nb_walk = parse_anim("object_nb", "gNabooruWalkAnim", len(nb_limbs))

    resources = {}
    for out_name, gla_name in GUARD_ANIMS.items():
        gla = parse_anim("object_gla", gla_name, len(gla_limbs))
        table = retarget_from_guard(gla, nb_walk.table[0])
        pos_err, rot_err = verify_against_guard(nb_limbs, gla_limbs, table, gla.table)
        print(f"{out_name}: {gla.frames} frames from {gla_name}; max joint error {pos_err:.2f} units, "
              f"max folded-limb rotation error {rot_err:.4f}")
        if pos_err > 2.0 or rot_err > 1e-3:
            sys.exit(f"retarget check failed for {out_name}")
        resources[f"objects/object_nb/{out_name}"] = encode_animation(table)
        if args.debug_c:
            args.debug_c.mkdir(parents=True, exist_ok=True)
            write_debug_c(args.debug_c / f"{out_name}.c", out_name, table)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in resources.items():
            z.writestr(path, data)
    print(f"wrote {len(resources)} resource(s) to {args.out}")


if __name__ == "__main__":
    main()
