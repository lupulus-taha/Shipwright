"""OoT decomp asset parsing + forward kinematics for skeleton/animation retargeting."""
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ASSETS = Path.home() / "oot/extracted/pal-1.0/assets/objects"
DECOMP_ASSETS = Path.home() / "oot/assets/objects"


def _read(path: Path) -> str:
    return path.read_text()


def _ints(text: str) -> list[int]:
    return [int(v, 0) for v in re.findall(r"-?0x[0-9A-Fa-f]+|-?\d+", text)]


def parse_enum(obj: str, prefix: str) -> dict[str, int]:
    """limb enum names -> value, from the object's header (decomp-maintained or extracted)."""
    for base in (DECOMP_ASSETS, ASSETS):
        h = base / obj / f"{obj}.h"
        if h.exists():
            text = _read(h)
            m = re.search(r"typedef enum[^{]*\{([^}]*" + re.escape(prefix) + r"[^}]*)\}", text, re.S)
            if m:
                names = re.findall(r"(" + re.escape(prefix) + r"\w+)", m.group(1))
                return {n: i for i, n in enumerate(dict.fromkeys(names))}
    raise FileNotFoundError(f"no limb enum {prefix} for {obj}")


@dataclass
class Limb:
    name: str
    joint: np.ndarray  # rest translation relative to parent (model units)
    child: int
    sibling: int
    parent: int = -1


def parse_skeleton(obj: str, skel: str, enum_prefix: str) -> list[Limb]:
    enum = parse_enum(obj, enum_prefix)
    limb_list = ASSETS / obj / f"{skel[: -len('Skel')]}Limbs.inc.c"  # gNabooruSkel -> gNabooruLimbs.inc.c
    names = re.findall(r"&?(\w+Limb)\b", re.sub(r"//[^\n]*", "", _read(limb_list)))
    limbs = []
    for n in names:
        t = _read(ASSETS / obj / f"{n}.inc.c")
        xyz = _ints(re.search(r"\{([^}]*)\}\s*,\s*//\s*jointPos", t, re.S).group(1))[:3]

        def idx(field):
            tok = re.search(r"\*/\s*([^,]+),\s*//\s*" + field, t).group(1).strip()
            if tok == "LIMB_DONE":
                return 255
            m = re.match(r"(\w+)\s*-\s*1", tok)
            return enum[m.group(1)] - 1 if m else int(tok, 0)

        limbs.append(Limb(n, np.array(xyz, float), idx("child"), idx("sibling")))
    # parents from child/sibling links
    def walk(i, parent):
        while i != 255:
            limbs[i].parent = parent
            walk(limbs[i].child, i)
            i = limbs[i].sibling

    walk(0, -1)
    return limbs


@dataclass
class Anim:
    frames: int
    table: np.ndarray  # [frame, 1 + limbCount, 3] int: [0] = root translation, [k+1] = limb k rotation


def parse_anim(obj: str, anim: str, limb_count: int) -> Anim:
    base = anim[: -len("Anim")]
    hdr = _ints(_read(ASSETS / obj / f"{anim}.inc.c"))
    frames, static_max = hdr[0], hdr[-1]
    data = _ints(_read(ASSETS / obj / f"{base}FrameData.inc.c"))
    data = [v for v in data]
    # strip index comments: FrameData lines look like "0x003E, // 0"
    data = _ints(re.sub(r"//[^\n]*", "", _read(ASSETS / obj / f"{base}FrameData.inc.c")))
    idx = _ints(re.sub(r"//[^\n]*", "", _read(ASSETS / obj / f"{base}JointIndices.inc.c")))
    idx = np.array(idx).reshape(-1, 3)
    assert len(idx) == limb_count + 1, (anim, len(idx), limb_count)
    table = np.zeros((frames, len(idx), 3), int)
    for f in range(frames):
        for j, (x, y, z) in enumerate(idx):
            for c, i in enumerate((x, y, z)):
                v = data[i] if i < static_max else data[i + f]
                table[f, j, c] = ((v + 0x8000) & 0xFFFF) - 0x8000
    return Anim(frames, table)


S16 = math.pi / 0x8000


def rot_zyx(r) -> np.ndarray:
    """OoT Matrix_TranslateRotateZYX rotation part: Rz(z) @ Ry(y) @ Rx(x), angles in s16 units."""
    x, y, z = (v * S16 for v in r)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def zyx_from_rot(R) -> tuple[int, int, int]:
    y = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    x = math.atan2(R[2, 1], R[2, 2])
    z = math.atan2(R[1, 0], R[0, 0])
    return tuple(int(round(a / S16)) for a in (x, y, z))


def fk(limbs: list[Limb], frame_table: np.ndarray):
    """world rotation and position per limb for one frame."""
    n = len(limbs)
    W = [None] * n
    P = [None] * n
    for i, l in enumerate(limbs):  # limbs are listed parent-before-child in OoT data
        t = frame_table[0].astype(float) if i == 0 else l.joint
        R = rot_zyx(frame_table[i + 1])
        if l.parent < 0:
            W[i], P[i] = R, t
        else:
            W[i] = W[l.parent] @ R
            P[i] = P[l.parent] + W[l.parent] @ t
    return np.array(W), np.array(P)


def nearest_rotation(M) -> np.ndarray:
    U, _, Vt = np.linalg.svd(M)
    D = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
    return U @ D @ Vt


# Link (adult): his skeleton in object_link_boy, his animations in the link_animetion segment (raw frames: 22 Vec3s,
# root translation then one rotation per limb, and a face index).
LINK_LIMBS = ["ROOT", "WAIST", "LOWER", "R_THIGH", "R_SHIN", "R_FOOT", "L_THIGH", "L_SHIN", "L_FOOT", "UPPER", "HEAD",
              "HAT", "COLLAR", "L_SHOULDER", "L_FOREARM", "L_HAND", "R_SHOULDER", "R_FOREARM", "R_HAND", "BACK", "TORSO"]
LINK_LIMB = {name: i for i, name in enumerate(LINK_LIMBS)}


def parse_link_skeleton() -> list[Limb]:
    obj = "object_link_boy"
    names = re.findall(r"&?(\w+Limb)\b", re.sub(r"//[^\n]*", "", _read(ASSETS / obj / "gLinkAdultLimbs.inc.c")))
    limbs = []
    for n in names:
        t = _read(ASSETS / obj / f"{n}.inc.c")
        xyz = _ints(re.search(r"\{([^}]*)\}\s*,\s*//\s*jointPos", t, re.S).group(1))[:3]

        def idx(field):
            tok = re.search(r"\*/\s*([^,]+),\s*//\s*" + field, t).group(1).strip()
            if tok == "LIMB_DONE":
                return 255
            m = re.match(r"PLAYER_LIMB_(\w+)\s*-\s*1", tok)
            return LINK_LIMB[m.group(1)] if m else int(tok, 0)

        limbs.append(Limb(n, np.array(xyz, float), idx("child"), idx("sibling")))

    def walk(i, parent):
        while i != 255:
            limbs[i].parent = parent
            walk(limbs[i].child, i)
            i = limbs[i].sibling

    walk(0, -1)
    return limbs


def parse_link_anim(name: str) -> np.ndarray:
    """gPlayerAnim_link_<name>: [frame, 22, 3] (root translation, then the 21 limbs' rotations)."""
    path = ASSETS.parent / "misc" / "link_animetion" / f"gPlayerAnim_link_{name}_Data.inc.c"
    v = [((x + 0x8000) & 0xFFFF) - 0x8000 for x in _ints(re.sub(r"//[^\n]*", "", _read(path)))]
    n = len(v) // 67
    return np.array(v[: n * 67]).reshape(n, 67)[:, :66].reshape(n, 22, 3)
