"""Item icons for "Play as Nabooru", rendered from the game's own Gerudo weapon models.

OoT's item icons are small pre-rendered pictures of the 3D items, lit from the upper left, with a soft dark shadow to
the lower right. This draws a weapon's display list the same way: a tiny software rasterizer for the few commands the
Gerudo weapons use (vertex loads, triangles, one texture at a time, lit texturing or the environment-mapped metal
look), rendered large and scaled down to 32x32.
"""
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image

import ootanim

ICON_SIZE = 32
SUPERSAMPLE = 8
LIGHT = np.array([-0.55, 0.65, 0.55])  # from the upper left, in front (x right, y up, z towards the viewer)
AMBIENT, DIFFUSE = 0.5, 0.7
SHADOW_OFFSET, SHADOW_ALPHA = 1, 0.55


def _calls(text):
    """(name, [args]) of each gsXxx(...) macro in a display list source, in order."""
    out = []
    for m in re.finditer(r"\b(gs\w+)\(", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        args = [a.strip() for a in re.split(r",(?![^()]*\))", text[m.end(): i - 1])]
        out.append((m.group(1), args))
    return out


def _int(v):
    return int(v, 0)


def _vertices(obj, array):
    text = (ootanim.ASSETS / obj / f"{array}.inc.c").read_text()
    rows = []
    for m in re.findall(r"VTX\(([^)]*)\)", text):
        x, y, z, s, t, r, g, b, a = (_int(v.strip()) for v in m.split(","))
        sb = lambda c: c - 256 if c > 127 else c  # normals are signed bytes
        rows.append(((x, y, z), (s, t), (sb(r), sb(g), sb(b))))
    return rows


def _texture(obj, name):
    for f in (ootanim.ASSETS / obj).glob(f"{name}.*png"):
        return np.asarray(Image.open(f).convert("RGBA"), float) / 255.0
    raise FileNotFoundError(name)


def load_display_list(obj, dl):
    """Triangles of a display list: (positions 3x3, texture coords 3x2, normals 3x3, material)."""
    text = (ootanim.ASSETS / obj / f"{dl}.inc.c").read_text()
    tris, buffer = [], [None] * 32
    mat = {"tex": None, "scale": (1.0, 1.0), "wrap": ("wrap", "wrap"), "texgen": False, "metal": False,
           "prim": np.ones(3), "env": np.zeros(3)}
    for name, args in _calls(text):
        if name == "gsSPTexture":
            mat["scale"] = (_int(args[0]) / 65536.0, _int(args[1]) / 65536.0)
        elif name == "gsDPLoadTextureBlock":
            flags = (args[6], args[7])
            mat["tex"] = _texture(obj, args[0])
            mat["wrap"] = tuple("mirror" if "G_TX_MIRROR" in f else "clamp" if "CLAMP" in f else "wrap" for f in flags)
        elif name == "gsDPSetCombineLERP":
            mat["metal"] = args[0] == "PRIMITIVE" and args[1] == "ENVIRONMENT"
        elif name == "gsSPSetGeometryMode" and "G_TEXTURE_GEN" in args[0]:
            mat["texgen"] = True
        elif name == "gsSPClearGeometryMode" and "G_TEXTURE_GEN" in args[0]:
            mat["texgen"] = False
        elif name in ("gsDPSetPrimColor", "gsDPSetEnvColor"):
            rgb = np.array([_int(v) for v in args[-4:-1]]) / 255.0
            mat["prim" if name == "gsDPSetPrimColor" else "env"] = rgb
        elif name == "gsSPVertex":
            array, first = re.match(r"&(\w+)\[(\d+)\]", args[0]).groups()
            rows = _vertices(obj, array)[int(first): int(first) + _int(args[1])]
            for k, row in enumerate(rows):
                buffer[_int(args[2]) + k] = row
        elif name in ("gsSP1Triangle", "gsSP2Triangles"):
            idx = [_int(a) for a in args]
            for tri in ([idx[0:3]] if name == "gsSP1Triangle" else [idx[0:3], idx[4:7]]):
                v = [buffer[i] for i in tri]
                tris.append((np.array([p for p, _, _ in v], float), np.array([st for _, st, _ in v], float),
                             np.array([n for _, _, n in v], float), dict(mat)))
    return tris


def frame_for(tris, tip_metal=True):
    """Rotation putting a weapon's length along +X (towards its blade), its width along +Y, its thin side towards
    the viewer (+Z); and the centre of its extent along that frame."""
    pts = np.vstack([t[0] for t in tris])
    centre = pts.mean(0)
    _, _, axes = np.linalg.svd(pts - centre)
    blade = np.vstack([t[0] for t in tris if t[3]["metal"] == tip_metal]).mean(0)
    if (blade - centre) @ axes[0] < 0:
        axes[0] = -axes[0]
    axes[2] = np.cross(axes[0], axes[1])
    return axes


def place(tris, axes, angle, length, offset=(0.0, 0.0), mirror=False, depth=0.0, girth=1.0):
    """The weapon turned to point `angle` degrees (0 = right, 90 = up), `length` icon pixels long from end to end,
    centred at `offset` (pixels from the icon centre); optionally mirrored left to right, and drawn `girth` times
    as thick as it is (a long, thin weapon would otherwise be a hairline at this size)."""
    pts = np.vstack([t[0] for t in tris]) @ axes.T
    lo, hi = pts.min(0), pts.max(0)
    centre = (lo + hi) / 2
    scale = length / (hi[0] - lo[0])
    a = math.radians(angle)
    turn = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    flip = np.diag([-1.0, 1, 1]) if mirror else np.eye(3)
    stretch = np.diag([1.0, girth, girth])
    m = flip @ turn @ stretch  # applied to coordinates in the weapon's own frame
    normal_m = flip @ turn @ np.linalg.inv(stretch)
    out = []
    for p, st, n, mat in tris:
        q = ((p @ axes.T - centre) @ m.T) * scale + np.array([offset[0], offset[1], depth])
        out.append((q, st, (n @ axes.T) @ normal_m.T, mat))
    return out


def _sample(tex, u, v, wrap):
    h, w = tex.shape[:2]

    def coord(c, size, mode):
        c = np.floor(c).astype(int)
        if mode == "clamp":
            return np.clip(c, 0, size - 1)
        if mode == "mirror":
            c = np.mod(c, 2 * size)
            return np.where(c >= size, 2 * size - 1 - c, c)
        return np.mod(c, size)

    return tex[coord(v, h, wrap[1]), coord(u, w, wrap[0])]


def render(placed):
    """Draws placed triangles (icon pixel coordinates, y up) into a 32x32 RGBA icon with its drop shadow."""
    n = ICON_SIZE * SUPERSAMPLE
    color = np.zeros((n, n, 3))
    alpha = np.zeros((n, n))
    depth = np.full((n, n), -np.inf)
    light = LIGHT / np.linalg.norm(LIGHT)
    for p, st, nrm, mat in placed:
        # pixel centres -> icon coordinates: x right, y up, origin at the icon centre
        sx = (p[:, 0] + ICON_SIZE / 2) * SUPERSAMPLE
        sy = (ICON_SIZE / 2 - p[:, 1]) * SUPERSAMPLE
        x0, x1 = max(int(sx.min()), 0), min(int(sx.max()) + 1, n - 1)
        y0, y1 = max(int(sy.min()), 0), min(int(sy.max()) + 1, n - 1)
        if x0 > x1 or y0 > y1:
            continue
        area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
        if abs(area) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((sx[1] - gx) * (sy[2] - gy) - (sx[2] - gx) * (sy[1] - gy)) / area
        w1 = ((sx[2] - gx) * (sy[0] - gy) - (sx[0] - gx) * (sy[2] - gy)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        z = w0 * p[0, 2] + w1 * p[1, 2] + w2 * p[2, 2]
        region = depth[y0:y1 + 1, x0:x1 + 1]
        draw = inside & (z > region)
        if not draw.any():
            continue
        nv = w0[..., None] * nrm[0] + w1[..., None] * nrm[1] + w2[..., None] * nrm[2]
        nv /= np.maximum(np.linalg.norm(nv, axis=-1, keepdims=True), 1e-9)
        facing = np.where(nv[..., 2:3] < 0, -nv, nv)  # both sides of thin blades are lit
        shade = AMBIENT + DIFFUSE * np.clip(facing @ light, 0, 1)
        tex = mat["tex"]
        if tex is None:
            texel = np.ones(nv.shape[:2] + (4,))
        elif mat["texgen"]:  # environment mapping: the texture follows the surface's direction
            h, w = tex.shape[:2]
            texel = _sample(tex, (facing[..., 0] * 0.5 + 0.5) * w, (0.5 - facing[..., 1] * 0.5) * h, mat["wrap"])
        else:
            u = (w0 * st[0, 0] + w1 * st[1, 0] + w2 * st[2, 0]) / 32 * mat["scale"][0]
            v = (w0 * st[0, 1] + w1 * st[1, 1] + w2 * st[2, 1]) / 32 * mat["scale"][1]
            texel = _sample(tex, u, v, mat["wrap"])
        if mat["metal"]:
            rgb = mat["env"] + (mat["prim"] - mat["env"]) * texel[..., :1]
        else:
            rgb = texel[..., :3] * mat["prim"]
        rgb = np.clip(rgb * shade[..., None], 0, 1)
        color[y0:y1 + 1, x0:x1 + 1][draw] = rgb[draw]
        alpha[y0:y1 + 1, x0:x1 + 1][draw] = 1.0
        region[draw] = z[draw]

    # scale down (premultiplied), then add the shadow underneath
    def shrink(a):
        return a.reshape(ICON_SIZE, SUPERSAMPLE, ICON_SIZE, SUPERSAMPLE, *a.shape[2:]).mean((1, 3))

    a = shrink(alpha)
    c = shrink(color * alpha[..., None])
    shadow = np.zeros_like(a)
    k = SHADOW_OFFSET
    shadow[k:, k:] = a[:-k, :-k] * SHADOW_ALPHA
    out_a = a + shadow * (1 - a)
    out_c = c  # (the shadow is black)
    rgb = np.where(out_a[..., None] > 0, out_c / np.maximum(out_a[..., None], 1e-9), 0)
    return np.concatenate([rgb, out_a[..., None]], -1)


def to_rgba32(icon):
    return (np.clip(np.round(icon * 255), 0, 255)).astype(np.uint8)


def scimitars_icon():
    """Nabooru's twin scimitars, crossed."""
    blade = load_display_list("object_geldb", "object_geldb_009460_DL")
    axes = frame_for(blade)
    placed = place(blade, axes, 45, 29, offset=(0, 0), depth=1000) + \
        place(blade, axes, 45, 29, offset=(0, 0), mirror=True, depth=0)
    return render(placed)


def spear_icon():
    """The Gerudo guards' spear."""
    spear = load_display_list("object_gla", "gObjectGlaSpearDL")
    return render(place(spear, frame_for(spear), 45, 40, girth=2.2))


def preview(icons, path, zoom=6):
    """Icons side by side, enlarged, on the grey of the pause screen."""
    sheet = Image.new("RGBA", (len(icons) * ICON_SIZE * zoom, ICON_SIZE * zoom), (90, 90, 90, 255))
    for i, icon in enumerate(icons):
        im = Image.fromarray(to_rgba32(icon), "RGBA").resize((ICON_SIZE * zoom,) * 2, Image.NEAREST)
        sheet.alpha_composite(im, (i * ICON_SIZE * zoom, 0))
    sheet.save(path)
