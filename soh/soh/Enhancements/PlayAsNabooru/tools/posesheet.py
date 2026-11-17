"""Contact sheets of Nabooru's generated animations, drawn from the decomp's meshes without running the game.

A plain flat-shaded picture of her model (and the weapon in her hands) per frame, from one or more sides: enough to
judge a pose, a silhouette, where a weapon points and whether her hands are on it. The game is still the last word
(tools/run_test.ps1 with `pose`).
"""
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import ootanim
from ootanim import fk, rot_zyx

SKIN = (196, 140, 96)
COLOURS = (("Skin", SKIN), ("Face", SKIN), ("Ear", SKIN), ("Belly", SKIN), ("Hand", SKIN), ("Nose", SKIN),
           ("Hair", (196, 64, 40)), ("Eye", (240, 240, 240)), ("Pants", (236, 226, 214)), ("Cloth", (214, 120, 150)),
           ("Glove", (150, 90, 170)), ("Shoe", (170, 110, 60)), ("Sole", (120, 84, 50)), ("Metal", (190, 190, 205)),
           ("Jewel", (90, 190, 200)), ("Gold", (214, 180, 70)), ("Blade", (200, 205, 220)), ("Handle", (120, 84, 50)),
           ("Element", (200, 60, 50)))
OTHER = (214, 150, 160)


def colour_for(texture):
    for key, colour in COLOURS:
        if key.lower() in texture.lower():
            return colour
    return OTHER


class Mesh:
    """A display list's triangles: per corner a position and the matrix it is in (-1: the limb's own)."""

    def __init__(self, obj, name):
        text = re.sub(r"//[^\n]*", "", (ootanim.ASSETS / obj / f"{name}.inc.c").read_text())
        vtx_cache = {}
        buffer = {}
        matrix = -1
        colour = OTHER
        self.pos, self.mat, self.colour = [], [], []
        for cmd, args in re.findall(r"gs(SPMatrix|SPVertex|SP2Triangles|SP1Triangle|DPLoadTextureBlock)\(([^)]*)\)",
                                    text, re.S):
            a = [s.strip() for s in args.split(",")]
            if cmd == "SPMatrix":
                matrix = (int(a[0], 0) - 0x0D000000) // 0x40
            elif cmd == "DPLoadTextureBlock":
                colour = colour_for(a[0])
            elif cmd == "SPVertex":
                m = re.match(r"&?(\w+)\[(\d+)\]", a[0])
                source, start = m.group(1), int(m.group(2))
                if source not in vtx_cache:
                    vtx_cache[source] = read_vtx(obj, source)
                for k in range(int(a[1], 0)):
                    buffer[int(a[2], 0) + k] = (vtx_cache[source][start + k], matrix)
            else:
                idx = [int(v, 0) for v in a]
                for tri in ((idx[0:3],) if cmd == "SP1Triangle" else (idx[0:3], idx[4:7])):
                    self.pos.append([buffer[i][0] for i in tri])
                    self.mat.append([buffer[i][1] for i in tri])
                    self.colour.append(colour)
        self.pos = np.array(self.pos, float).reshape(-1, 3, 3)
        self.mat = np.array(self.mat, int).reshape(-1, 3)


def read_vtx(obj, name):
    text = (ootanim.ASSETS / obj / f"{name}.inc.c").read_text()
    return np.array([[int(v, 0) for v in m] for m in re.findall(r"VTX\(\s*(-?\w+),\s*(-?\w+),\s*(-?\w+)", text)], float)


class Body:
    """Her skeleton's meshes, limb by limb, with the flex matrix order (limbs that draw something, in limb order)."""

    def __init__(self, obj, limbs):
        self.limbs = limbs
        self.meshes = {}
        for i, limb in enumerate(limbs):
            text = (ootanim.ASSETS / obj / f"{limb.name}.inc.c").read_text()
            dl = re.search(r"(\w+),\s*//\s*dList", text).group(1)
            if dl != "NULL":
                self.meshes[i] = Mesh(obj, dl)
        self.order = sorted(self.meshes)  # flex matrix k belongs to this limb

    def triangles(self, frame):
        W, P = fk(self.limbs, frame)
        tris, colours = [], []
        for i, mesh in self.meshes.items():
            if not len(mesh.pos):
                continue
            which = np.where(mesh.mat < 0, i, np.array(self.order)[np.clip(mesh.mat, 0, len(self.order) - 1)])
            world = np.einsum("tcij,tcj->tci", W[which], mesh.pos) + P[which]
            tris.append(world)
            colours += mesh.colour
        return np.concatenate(tris), colours, W, P


def held(mesh, W, P, hand, grip=None):
    """A weapon's triangles in the hand limb's frame, turned by `grip` (s16 angles) relative to it."""
    R = W[hand] if grip is None else W[hand] @ rot_zyx(grip)
    return np.einsum("ij,tcj->tci", R, mesh.pos) + P[hand], mesh.colour


def draw(image, origin, scale, tris, colours, yaw, pitch=12.0):
    """Paints triangles (model space) into `image`, seen from `yaw` degrees round her (0: from the front, 90: from her
    left), the camera raised by `pitch` degrees; `origin` is where the model's origin on the ground lands."""
    y, p = math.radians(yaw), math.radians(pitch)
    right = np.array([math.cos(y), 0.0, -math.sin(y)])
    toward = np.array([math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])
    up = np.cross(toward, right)
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1)
    length[length == 0] = 1
    light = np.array([0.35, 0.6, 0.72])
    light = light[0] * right + light[1] * up + light[2] * toward
    shade = 0.5 + 0.5 * np.abs(normal @ light) / length
    depth = (tris @ toward).mean(axis=1)
    pen = ImageDraw.Draw(image)
    for t in np.argsort(depth):
        pts = [(origin[0] + float(v @ right) * scale, origin[1] - float(v @ up) * scale) for v in tris[t]]
        pen.polygon(pts, fill=tuple(int(c * shade[t]) for c in colours[t]))


def sheet(path, body, frames, views=(30, 100), weapons=None, labels=None, cell=230, span=13000.0, cols=None,
          title=None, aspect=0.72, ahead=0.5):
    """One row of cells per view, one cell per frame: `cell` pixels wide for `span` model units, `aspect` times as
    high, the model's origin `ahead` of the way across (more room in front of her seen from the side: > 0.5). A view
    is a yaw (see draw), or (yaw, pitch) to see her from higher up.
    `weapons(frame_index, W, P)` -> [(triangles, colours)]."""
    n = len(frames)
    cols = cols or n
    rows = math.ceil(n / cols)
    scale = cell / span
    head = 16 if title else 0
    high = int(cell * aspect) + 14
    floor = 14 + int(cell * aspect * 0.9)
    image = Image.new("RGB", (cols * cell, head + rows * len(views) * high), (250, 250, 250))
    pen = ImageDraw.Draw(image)
    if title:
        pen.text((4, 2), title, fill=(0, 0, 0))
    for k, frame in enumerate(frames):
        tris, colours, W, P = body.triangles(frame)
        extra = weapons(k, W, P) if weapons else []
        for more, more_colours in extra:
            tris = np.concatenate([tris, more])
            colours = colours + list(more_colours)
        for v, view in enumerate(views):
            yaw, pitch = view if isinstance(view, tuple) else (view, 12.0)
            x0 = (k % cols) * cell
            y0 = head + ((k // cols) * len(views) + v) * high
            tile = Image.new("RGB", (cell, high), (250, 250, 250))
            tpen = ImageDraw.Draw(tile)
            tpen.line([(0, floor), (cell, floor)], fill=(200, 200, 200))
            # (seen from her left she faces left: the room ahead of her is on that side)
            across = cell * (ahead if 45 < yaw % 360 < 135 else 1 - ahead if 225 < yaw % 360 < 315 else 0.5)
            draw(tile, (across, floor if pitch < 45 else high // 2), scale, tris, colours, yaw, pitch)
            tpen.text((3, 1), f"{labels[k] if labels else k}  @{yaw}" + (f"/{pitch:g}" if pitch != 12.0 else ""),
                      fill=(60, 60, 60))
            image.paste(tile, (x0, y0))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
