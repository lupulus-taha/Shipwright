"""Item names for the pause screen, set in the game's own lettering.

The pause screen's item names are 128x16 pictures (4-bit: 3 bits of intensity, 1 bit of alpha): white letters with a
dark outline, centred. New names are put together from letters cut out of the game's English item names: each name's
letters are found by their bright cores (the outlines of neighbouring letters touch), matched to the name's spelling
(taken from its texture name), and set again with the font's usual spacing, each letter keeping its own outline.
"""
import re
from collections import Counter

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 128, 16
CORE = 160  # intensity of a letter's body; its outline is dark


def _spelling(name):
    """'GerudoMask' -> 'Gerudo Mask' (texture names drop spaces)"""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _runs(mask):
    runs, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


class Lettering:
    def __init__(self, textures_dir):
        self.glyphs = {}  # letter -> (columns of intensity, columns of alpha, core start offset, core width)
        gaps, spaces = Counter(), Counter()
        for path in sorted(textures_dir.glob("g*ItemNameENGTex.ia4.png")):
            text = _spelling(path.name[1: -len("ItemNameENGTex.ia4.png")])
            if not re.fullmatch(r"[A-Za-z ]+", text):
                continue
            img = np.asarray(Image.open(path).convert("LA"), int)
            inten, alpha = img[:, :, 0], img[:, :, 1] > 0
            cores = _runs(((inten >= CORE) & alpha).any(0))
            letters = text.replace(" ", "")
            if len(cores) != len(letters):
                continue  # letters touching, or a spelling the texture name doesn't tell
            words = _runs(alpha.any(0))
            # every pixel of the lettering belongs to the letter whose bright core is nearest (outlines touch)
            core_px = np.argwhere((inten >= CORE) & alpha)
            core_of = np.array([next(k for k, (a, b) in enumerate(cores) if a <= x < b) for _, x in core_px])
            owner = np.full(alpha.shape, -1)
            for y, x in np.argwhere(alpha):
                owner[y, x] = core_of[np.argmin(((core_px - (y, x)) ** 2).sum(1))]
            for k, (letter, (a, b)) in enumerate(zip(letters, cores)):
                lo, hi = max(a - 3, 0), min(b + 3, WIDTH)
                mine = owner[:, lo:hi] == k
                self.glyphs.setdefault(letter, (np.where(mine, inten[:, lo:hi], 0), mine, a - lo, b - a))
                if k + 1 < len(cores):
                    gap = cores[k + 1][0] - b
                    same_word = any(w0 <= a and cores[k + 1][1] <= w1 for w0, w1 in words)
                    (gaps if same_word else spaces)[gap] += 1
        self.gap = gaps.most_common(1)[0][0]
        self.space = spaces.most_common(1)[0][0]

    def missing(self, text):
        return sorted({c for c in text.replace(" ", "") if c not in self.glyphs})

    def render(self, text):
        """(intensity, alpha) arrays, 128x16, the text centred."""
        assert not self.missing(text), f"no letters for {self.missing(text)} in the game's item names"
        x, placed = 0, []
        for i, c in enumerate(text):
            if c == " ":
                x += self.space - self.gap
                continue
            inten, alpha, off, width = self.glyphs[c]
            placed.append((x - off, inten, alpha))
            x += width + self.gap
        total = x - self.gap
        shift = (WIDTH - total) // 2
        out_i = np.zeros((HEIGHT, WIDTH), int)
        out_a = np.zeros((HEIGHT, WIDTH), bool)
        for x0, inten, alpha in placed:
            for col in range(inten.shape[1]):
                X = x0 + col + shift
                if 0 <= X < WIDTH:
                    # a letter's body wins over a neighbour's outline
                    take = alpha[:, col] & ((inten[:, col] >= out_i[:, X]) | ~out_a[:, X])
                    out_i[take, X] = inten[take, col]
                    out_a[:, X] |= alpha[:, col]
        return out_i, out_a


def to_ia4(inten, alpha):
    """Packed 4-bit pixels, two per byte (first in the high nibble)."""
    nib = (np.clip(inten, 0, 255) * 7 // 255) << 1 | alpha.astype(int)
    flat = nib.reshape(-1)
    return bytes((flat[0::2] << 4 | flat[1::2]).astype(np.uint8))


def preview(images, path, zoom=4):
    sheet = Image.new("RGBA", (WIDTH * zoom, HEIGHT * zoom * len(images)), (60, 60, 140, 255))
    for k, (inten, alpha) in enumerate(images):
        la = np.dstack([inten, alpha * 255]).astype(np.uint8)
        im = Image.fromarray(la, "LA").convert("RGBA").resize((WIDTH * zoom, HEIGHT * zoom), Image.NEAREST)
        sheet.alpha_composite(im, (0, k * HEIGHT * zoom))
    sheet.save(path)
