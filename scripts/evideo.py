#!/usr/bin/env python3
"""evideo 0.2 - narrated or silent animated explainers from a JSON scene spec.

One spec, many outputs:
  canvas   landscape | square | portrait (4:5, LinkedIn feed) | story (9:16)   (aliases: linkedin, youtube, reels ...)
  style    clean | sketch (hand-drawn whiteboard look)
  theme    light | warm | dark
  output   .mp4  .gif  .png (single frame)  .pdf (carousel, one page per scene)
  audio    none (text on screen) | files (your TTS clips) | generate (pluggable TTS) ; captions on or off

Needs only Python 3.9+, Pillow, numpy and ffmpeg.  No browser, no GPU.

Subcommands:  validate | narration | audio | still | render      (run with -h for each)
"""
import argparse
import bisect
import json
import math
import os
import random
import re
import shlex
import subprocess
import sys
import wave
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

VERSION = "0.2.0"
HERE = Path(__file__).resolve().parent
FONT_DIR = HERE.parent / "assets" / "fonts"

# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------
CANVASES = {"landscape": (1600, 900), "square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920)}
CANVAS_ALIASES = {"linkedin": "portrait", "feed": "portrait", "4:5": "portrait", "youtube": "landscape",
                  "16:9": "landscape", "1:1": "square", "vertical": "story", "reels": "story",
                  "shorts": "story", "tiktok": "story", "9:16": "story"}
VARIANT_ORDER = {"landscape": ["landscape", "wide", "default"], "square": ["square", "default"],
                 "portrait": ["portrait", "tall", "default"], "story": ["story", "tall", "portrait", "default"],
                 "custom": ["default"]}
STYLES = ("clean", "sketch")
THEMES = {
    "light": {"bg": "#F3F5EF", "bg2": "#E7ECE3", "ink": "#1F2A24", "muted": "#74837A", "accent": "#1F7A5A",
              "accent_soft": "#D9EDE3", "card": "#FFFFFF", "line": "#C5D0C8", "warn": "#C8742B", "blue": "#3B6EA8",
              "white": "#FFFFFF", "cyan": "#2A7F9E", "coral": "#D4543F", "yellow": "#B58900", "orange": "#CF7A1E",
              "green": "#1F7A5A"},
    "warm": {"bg": "#FBF3E4", "bg2": "#F1E4CC", "ink": "#2B2118", "muted": "#8A7762", "accent": "#C8553D",
             "accent_soft": "#F6D9CD", "card": "#FFFBF2", "line": "#DCCBB0", "warn": "#B7791F", "blue": "#3B6EA8",
             "white": "#FFFFFF", "cyan": "#2F7F95", "coral": "#C8553D", "yellow": "#B8860B", "orange": "#D9822B",
             "green": "#4A7C59"},
    "dark": {"bg": "#0B0B0C", "bg2": "#18181A", "ink": "#F2F2EE", "muted": "#8D8D88", "accent": "#5CD6E8",
             "accent_soft": "#10262B", "card": "#0B0B0C", "line": "#4A4A47", "warn": "#F2994A", "blue": "#6AA9FF",
             "white": "#FFFFFF", "cyan": "#5CD6E8", "coral": "#F26B63", "yellow": "#F4CF5B", "orange": "#F2994A",
             "green": "#6FD08C"},
}
PALETTE = ("cyan", "coral", "yellow", "orange", "green", "blue")
BOX_STYLES = ("default", "accent", "dark", "muted") + PALETTE
ELEMENT_TYPES = {"box", "grid", "arrow", "text", "pill", "circle", "image", "flow"}
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
SYSTEM_FONTS = {
    "regular": ["segoeui.ttf", "Arial.ttf", "arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "bold": ["segoeuib.ttf", "Arial Bold.ttf", "arialbd.ttf", "Helvetica.ttc", "DejaVuSans-Bold.ttf",
             "LiberationSans-Bold.ttf"],
}
FOOTER_H = 70


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def num(v, ref):
    """Numbers are canvas units; strings like '50%' are a share of the canvas width/height."""
    if isinstance(v, str):
        v = v.strip()
        return float(v[:-1]) * ref / 100.0 if v.endswith("%") else float(v)
    return v


def resolve_canvas(value):
    """-> (name, (w, h)); raises ValueError on a bad value."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return "custom", (int(value[0]), int(value[1]))
    name = CANVAS_ALIASES.get(str(value), str(value))
    if name not in CANVASES:
        raise ValueError(f"canvas must be one of {sorted(CANVASES)} (aliases: {sorted(CANVAS_ALIASES)}) or [w, h]")
    return name, CANVASES[name]


# --------------------------------------------------------------------------
# Drawing context: theme, fonts, caches
# --------------------------------------------------------------------------
class Ctx:
    def __init__(self, spec, scale=1.0, ss=2):
        v = spec["video"]
        self.spec, self.v = spec, v
        self.dir = Path(spec.get("_dir", "."))
        self.style = v.get("style", "clean")
        self.canvas_name, (self.W, self.H) = resolve_canvas(v.get("canvas", "landscape"))
        th = v.get("theme", "dark" if self.style == "sketch" else "light")
        base = dict(THEMES[th]) if isinstance(th, str) else dict(THEMES["light"])
        if isinstance(th, dict):
            base.update(th)
        base.update(spec.get("theme", {}))
        self.theme = base
        self.ss = ss
        self.out_w = int(round(self.W * scale)) // 2 * 2
        self.S = ss * self.out_w / self.W
        self.out_h = int(round(self.H * self.S / ss)) // 2 * 2
        self.bg = hex2rgb(self.theme["bg"])
        self.is_dark = (0.299 * self.bg[0] + 0.587 * self.bg[1] + 0.114 * self.bg[2]) < 110
        self.fs = 1.12 if self.style == "sketch" else 1.0      # handwriting fonts run small
        self.g = 1.0                                            # global fade for scene in/out
        self.cap = False                                        # are captions burned in (reserves space)
        self._fonts, self._images, self._avatar, self._wm = {}, {}, {}, None
        self.author = self._parse_author()

    # ---- colours ----
    def rgb(self, c):
        if isinstance(c, (list, tuple)):
            return tuple(c)
        if isinstance(c, str):
            if c in self.theme:
                return hex2rgb(self.theme[c])
            if c.startswith("#") and len(c) == 7:
                return hex2rgb(c)
        return hex2rgb(self.theme["ink"])

    def fc(self, c, p=1.0):
        """Colour faded towards the background (cheap alpha for flat designs)."""
        return lerp(self.bg, self.rgb(c), clamp(p * self.g))

    # ---- fonts and text ----
    def _resolve(self, p):
        if not p:
            return None
        q = Path(p)
        if not q.is_absolute() and (self.dir / q).exists():
            return str(self.dir / q)
        return str(q)

    def font(self, size, bold=False):
        key = (round(size * self.fs * self.S), bold)
        if key not in self._fonts:
            user = self.v.get("fonts", {})
            cands = []
            if self.style == "sketch":
                cands += [self._resolve(user.get("hand")), str(FONT_DIR / "PatrickHand-Regular.ttf")]
            cands += [self._resolve(user.get("bold" if bold else "regular")),
                      str(FONT_DIR / ("Inter-Bold.ttf" if bold else "Inter-Regular.ttf"))]
            cands += SYSTEM_FONTS["bold" if bold else "regular"]
            font = None
            for c in cands:
                if not c:
                    continue
                try:
                    font = ImageFont.truetype(c, key[0])
                    break
                except OSError:
                    continue
            self._fonts[key] = font or ImageFont.load_default()
        return self._fonts[key]

    def text(self, d, x, y, s, size, color, bold=False, anchor="la", stroke=None):
        sw = int(round(0.6 * self.S)) if (bold and self.style == "sketch") else 0
        if stroke:
            d.text((x * self.S, y * self.S), s, font=self.font(size, bold), fill=color, anchor=anchor,
                   stroke_width=int(round(2.2 * self.S)), stroke_fill=stroke)
        d.text((x * self.S, y * self.S), s, font=self.font(size, bold), fill=color, anchor=anchor,
               stroke_width=sw, stroke_fill=color)

    # ---- images ----
    def load_image(self, path):
        if path not in self._images:
            p = Path(path)
            if not p.is_absolute():
                p = self.dir / p
            try:
                self._images[path] = Image.open(p).convert("RGBA")
            except Exception:
                self._images[path] = None
        return self._images[path]

    def fitted(self, path, w, h, round_=False):
        key = ("fit", path, round(w), round(h), round_)
        if key not in self._images:
            im = self.load_image(path)
            if im is None:
                self._images[key] = None
            else:
                pw, ph = max(2, round(w * self.S)), max(2, round(h * self.S))
                r = min(pw / im.width, ph / im.height)
                tile = im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))), Image.LANCZOS)
                if round_:
                    side = min(tile.size)
                    l, t = (tile.width - side) // 2, (tile.height - side) // 2
                    tile = tile.crop((l, t, l + side, t + side)).resize((pw, pw), Image.LANCZOS)
                    m = Image.new("L", (pw * 4, pw * 4), 0)
                    ImageDraw.Draw(m).ellipse([0, 0, pw * 4 - 1, pw * 4 - 1], fill=255)
                    tile.putalpha(ImageChops.multiply(tile.getchannel("A"), m.resize((pw, pw), Image.LANCZOS)))
                self._images[key] = tile
        return self._images[key]

    # ---- author / avatar / watermark ----
    def _parse_author(self):
        a = self.v.get("author")
        if not a:
            return None
        if isinstance(a, str):
            a = {"name": a, "avatar": False}
        pos = a.get("position") or ("top-right" if self.style == "sketch" else "bottom-left")
        if pos == "none":
            return None
        d = max(32, round(min(self.W, self.H) * 0.042))
        return {**a, "d": d, "pos": pos}

    def avatar(self, d_units):
        if d_units in self._avatar:
            return self._avatar[d_units]
        a, px = self.author, max(4, round(d_units * self.S))
        tile = None
        if a.get("avatar"):
            tile = self.fitted(a["avatar"], d_units, d_units, round_=True)
        if tile is None:
            tile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            m = Image.new("L", (px * 4, px * 4), 0)
            ImageDraw.Draw(m).ellipse([0, 0, px * 4 - 1, px * 4 - 1], fill=255)
            disc = Image.new("RGBA", (px, px), self.rgb("accent_soft") + (255,))
            dd = ImageDraw.Draw(disc)
            words = [w for w in re.split(r"\s+", (a.get("name") or "?").strip()) if w]
            ini = "".join(w[0] for w in words[:2]).upper() or "?"
            dd.text((px / 2, px / 2), ini, font=self.font(d_units * 0.42 / self.fs, True), fill=self.rgb("accent"),
                    anchor="mm")
            disc.putalpha(m.resize((px, px), Image.LANCZOS))
            tile = disc
        self._avatar[d_units] = tile
        return tile

    def watermark(self):
        if self._wm is not None:
            return self._wm or None
        w = self.v.get("watermark")
        if not w:
            self._wm = False
            return None
        if isinstance(w, str):
            w = {"text": w}
        S, W, H = self.S, self.W, self.H
        op, size = w.get("opacity", 0.3), w.get("size", 22)
        pos = w.get("position", "bottom-right")
        ov = Image.new("RGBA", (round(W * S), round(H * S)), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        col = self.rgb(w.get("color", "ink")) + (int(255 * clamp(op)),)
        mb = 40 if self.style == "sketch" else 70
        mx = 40
        if pos == "center":
            size = size * 3
        f = self.font(size, True)
        tw = f.getlength(w["text"]) / S if w.get("text") else 0
        ih = w.get("image_size", size * 1.6)
        tile = self.fitted(w["image"], ih * 4, ih) if w.get("image") else None
        iw = (tile.width / S) if tile is not None else 0
        gap = 10 if (tile is not None and tw) else 0
        total = iw + gap + tw
        if pos == "center":
            x0, yc = (W - total) / 2, H / 2
        else:
            x0 = mx if pos.endswith("left") else W - mx - total
            yc = (mb + ih / 2) if pos.startswith("top") else H - mb - ih / 2 + (20 if self.style == "clean" else 0)
        if tile is not None:
            t2 = tile.copy()
            t2.putalpha(t2.getchannel("A").point(lambda a_: int(a_ * clamp(op))))
            ov.paste(t2, (round(x0 * S), round((yc - tile.height / S / 2) * S)), t2)
        if w.get("text"):
            d.text(((x0 + iw + gap) * S, yc * S), w["text"], font=f, fill=col, anchor="lm")
        self._wm = ov
        return ov


def wrap(font, text, max_px):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if cur and font.getlength(trial) > max_px:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------
# Pen: straight in `clean`, wobbly double-stroke in `sketch`
# --------------------------------------------------------------------------
def partial(pts, f):
    if f >= 0.999:
        return [tuple(p) for p in pts]
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    target, acc, out = f * sum(seg), 0.0, [tuple(pts[0])]
    for i, L in enumerate(seg):
        if acc + L <= target:
            out.append(tuple(pts[i + 1]))
            acc += L
        else:
            r = (target - acc) / L if L else 0
            out.append((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * r, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * r))
            break
    return out


def dashed(pts, on=11, off=8):
    runs, cur_on, remain = [], True, on
    for a, b in zip(pts, pts[1:]):
        L, pos = math.dist(a, b), 0.0
        while pos < L - 1e-9:
            step = min(remain, L - pos)
            if cur_on:
                t0, t1 = pos / L, (pos + step) / L
                runs.append([(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                             (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)])
            pos += step
            remain -= step
            if remain <= 1e-9:
                cur_on = not cur_on
                remain = on if cur_on else off
    return runs


def wobble(pts, seed, amp, step=30):
    rnd, out, walk = random.Random(seed), [], 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L == 0:
            continue
        nx, ny = -(y2 - y1) / L, (x2 - x1) / L
        n = max(1, int(L / step))
        for i in range(n):
            t = i / n
            walk = 0.55 * walk + rnd.uniform(-amp, amp)
            out.append((x1 + (x2 - x1) * t + nx * walk, y1 + (y2 - y1) * t + ny * walk))
    lx, ly = pts[-1]
    out.append((lx + rnd.uniform(-amp, amp) * 0.6, ly + rnd.uniform(-amp, amp) * 0.6))
    return out


def pen(ctx, d, pts, color, width, seed=0, dash=False, prog=1.0, rough=None):
    S = ctx.S
    pts = [tuple(p) for p in pts]
    if ctx.style == "sketch":
        amp = 1.6 if rough is None else rough
        passes = [(seed, amp, 1.0), (seed + 7919, amp * 1.15, 0.75)]
    else:
        passes = [(seed, 0, 1.0)]
    for sd, amp, wf in passes:
        path = partial(wobble(pts, sd, amp) if amp else pts, prog)
        for run in (dashed(path) if dash else [path]):
            if len(run) > 1:
                d.line([(x * S, y * S) for x, y in run], fill=color, width=max(1, int(round(width * wf * S))),
                       joint="curve")


def rect_pts(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]


def ellipse_pts(cx, cy, rx, ry, n=44):
    return [(cx + rx * math.cos(2 * math.pi * 1.04 * i / n), cy + ry * math.sin(2 * math.pi * 1.04 * i / n))
            for i in range(n + 1)]


# --------------------------------------------------------------------------
# Elements.  p = appear progress 0..1, h = highlight 0..1
# --------------------------------------------------------------------------
def box_colors(ctx, style):
    """-> (fill or None, outline, title colour, sub colour)"""
    rgb, dark = ctx.rgb, ctx.is_dark
    if style == "dark":
        return rgb("ink"), rgb("ink"), rgb("bg"), lerp(rgb("ink"), rgb("bg"), 0.45)
    if style == "muted":
        return (None if dark else rgb("bg2")), rgb("line"), rgb("muted") if dark else rgb("ink"), rgb("muted")
    if style == "accent":
        return (None if dark else rgb("accent_soft")), rgb("accent"), rgb("accent") if dark else rgb("ink"), rgb("muted")
    if style in PALETTE:
        c = rgb(style)
        return (None if dark else lerp(rgb("card"), c, 0.12)), c, c if dark else rgb("ink"), rgb("muted")
    return (None if dark else rgb("card")), rgb("ink") if dark else rgb("line"), rgb("ink"), rgb("muted")


def draw_box(ctx, d, e, p, h):
    S, sketch = ctx.S, ctx.style == "sketch"
    x, w, hh = e["x"], e["w"], e["h"]
    y = e["y"] + (0 if sketch else (1 - p) * 14)
    fill, outline, tcol, scol = box_colors(ctx, e.get("style", "default"))
    fill = fill if fill is not None else ctx.bg
    seed, r, dash = e.get("_seed", 1), e.get("radius", 14), e.get("dashed", False)
    if sketch:
        po, pt = clamp(p / 0.7), clamp((p - 0.45) / 0.55)
        if pt > 0:
            d.rectangle([x * S, y * S, (x + w) * S, (y + hh) * S], fill=ctx.fc(fill, pt))
        if h > 0:
            pen(ctx, d, rect_pts(x - 4, y - 4, w + 8, hh + 8), ctx.fc(outline, 0.35 * h), 7, seed + 3, prog=po)
        pen(ctx, d, rect_pts(x, y, w, hh), ctx.fc("accent" if h > 0.5 else outline, clamp(po * 6)),
            3.6 if h > 0.5 else 3.0, seed, dash=dash, prog=po)
        ta = pt
    else:
        if h > 0:
            for k in (3, 2, 1):
                g = 5 * k * h
                d.rounded_rectangle([(x - g) * S, (y - g) * S, (x + w + g) * S, (y + hh + g) * S], radius=(r + g) * S,
                                    fill=ctx.fc("accent_soft", p * 0.35))
        col = ctx.fc("accent" if h > 0.5 else outline, p)
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + hh) * S], radius=r * S, fill=ctx.fc(fill, p),
                            outline=None if dash else col, width=max(1, int((3 if h > 0.5 else 1.5) * S)))
        if dash:
            pen(ctx, d, rect_pts(x, y, w, hh), col, 1.8, seed, dash=True)
        ta = p
    title, sub = e.get("title", ""), e.get("sub", "")
    tsz, ssz = e.get("size", 22), e.get("sub_size", 15)
    maxpx = (w - 24) * S
    tl = wrap(ctx.font(tsz, True), title, maxpx) if title else []
    sl = wrap(ctx.font(ssz), sub, maxpx) if sub else []
    tlh, slh = tsz * 1.25 * ctx.fs, ssz * 1.35 * ctx.fs
    total = len(tl) * tlh + (len(sl) * slh + 6 if sl else 0)
    cy, cx = y + (hh - total) / 2, x + w / 2
    for ln in tl:
        ctx.text(d, cx, cy, ln, tsz, ctx.fc(tcol, ta), True, "ma")
        cy += tlh
    if sl:
        cy += 6
        for ln in sl:
            ctx.text(d, cx, cy, ln, ssz, ctx.fc(scol, ta), False, "ma")
            cy += slh


def grid_size(e):
    cell, gap = e.get("cell", 18), e.get("gap", 4)
    return e["cols"] * cell + (e["cols"] - 1) * gap, e["rows"] * cell + (e["rows"] - 1) * gap


def draw_grid(ctx, d, e, p, tn, ad):
    S = ctx.S
    cols, rows = e["cols"], e["rows"]
    cell, gap = e.get("cell", 18), e.get("gap", 4)
    n = cols * rows
    base, card = ctx.rgb(e.get("color", "accent")), ctx.rgb("card")
    lit, nlit = e.get("lit"), n
    if lit:
        nlit = int(clamp((tn - lit[0] * ad) / max(1e-6, (lit[1] - lit[0]) * ad)) * n)
    seed = e.get("seed", 1)
    y0 = e["y"] + (1 - p) * 14
    for i in range(n):
        r_, c_ = divmod(i, cols)
        shade = 0.40 + 0.60 * (((i + 1) * 2654435761 + seed * 97) % 1000) / 1000
        colour = lerp(card, base, shade) if i < nlit else ctx.rgb("line")
        px, py = e["x"] + c_ * (cell + gap), y0 + r_ * (cell + gap)
        d.rounded_rectangle([px * S, py * S, (px + cell) * S, (py + cell) * S], radius=min(4, cell / 4) * S,
                            fill=lerp(ctx.bg, colour, clamp(p * ctx.g)))
    if e.get("label"):
        w, h = grid_size(e)
        ctx.text(d, e["x"] + w / 2, y0 + h + 10, e["label"], 15, ctx.fc("muted", p), False, "ma")


def draw_arrow(ctx, d, e, q):
    S = ctx.S
    pts = e["points"]
    colour = ctx.fc(e.get("color", "accent"), clamp(q * 5))
    width = e.get("width", 4 if ctx.style == "clean" else 3.6)
    pen(ctx, d, pts, colour, width, e.get("_seed", 1), dash=e.get("dashed", False), prog=q, rough=1.2)
    if q >= 0.999:
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang, hs = math.atan2(y2 - y1, x2 - x1), e.get("head", 13)
        a1 = (x2 - hs * math.cos(ang - 0.45), y2 - hs * math.sin(ang - 0.45))
        a2 = (x2 - hs * math.cos(ang + 0.45), y2 - hs * math.sin(ang + 0.45))
        if ctx.style == "sketch":
            for a in (a1, a2):
                d.line([(x2 * S, y2 * S), (a[0] * S, a[1] * S)], fill=colour, width=max(1, int(width * S)))
        else:
            d.polygon([(x2 * S, y2 * S), (a1[0] * S, a1[1] * S), (a2[0] * S, a2[1] * S)], fill=colour)
    if e.get("label") and q > 0.6:
        seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        i = max(range(len(seg)), key=lambda k: seg[k])
        mx, my = (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2
        horizontal = abs(pts[i + 1][0] - pts[i][0]) >= abs(pts[i + 1][1] - pts[i][1])
        if horizontal:
            ctx.text(d, mx, my - 10, e["label"], e.get("label_size", 18), ctx.fc("muted", q), False, "ms")
        else:
            ctx.text(d, mx + 12, my, e["label"], e.get("label_size", 18), ctx.fc("muted", q), False, "lm")


def draw_text(ctx, d, e, p, mq):
    S, size = ctx.S, e.get("size", 22)
    bold = e.get("bold", False)
    font = ctx.font(size, bold)
    align = e.get("align", "left")
    anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
    lines = wrap(font, e["text"], e["w"] * S) if e.get("w") else str(e["text"]).split("\n")
    lh = size * 1.3 * ctx.fs
    y = e["y"] + (1 - p) * 10
    col = e.get("color", "ink")
    for i, ln in enumerate(lines):
        yy = y + i * lh
        ctx.text(d, e["x"], yy, ln, size, ctx.fc(col, p), bold, anchor)
        mk = e.get("mark")
        if mk and mq > 0:
            wpx = font.getlength(ln) / S
            x0 = e["x"] - {"left": 0, "center": wpx / 2, "right": wpx}[align]
            ly = yy + size * ctx.fs * (0.72 if mk.get("kind", "underline") == "strike" else 1.12)
            pen(ctx, d, [(x0 - 6, ly), (x0 + wpx + 6, ly)], ctx.fc(mk.get("color", "coral"), 1.0),
                max(3.0, size * 0.075), e.get("_seed", 1) + i, prog=mq, rough=1.2)


def pill_width(ctx, e):
    return ctx.font(e.get("size", 14), True).getlength(e["text"]) / ctx.S + 24


def draw_pill(ctx, d, e, p, filled=None):
    S, size = ctx.S, e.get("size", 14)
    w, h = pill_width(ctx, e), size * ctx.fs + 14
    y = e["y"] + (1 - p) * 8
    filled = e.get("filled", False) if filled is None else filled
    colour = e.get("color", "accent")
    d.rounded_rectangle([e["x"] * S, y * S, (e["x"] + w) * S, (y + h) * S], radius=h / 2 * S,
                        fill=ctx.fc(colour, p) if filled else None,
                        outline=ctx.fc(colour if filled else "line", p), width=max(1, int(1.5 * S)))
    ctx.text(d, e["x"] + w / 2, y + h / 2, e["text"], size, ctx.fc("bg" if filled else "muted", p), True, "mm")


def draw_circle(ctx, d, e, p, h):
    S = ctx.S
    r = e.get("r", 24) * p
    col = e.get("style", "coral")
    if e.get("fill", True):
        d.ellipse([(e["x"] - r) * S, (e["y"] - r) * S, (e["x"] + r) * S, (e["y"] + r) * S], fill=ctx.fc(col, p))
    else:
        pen(ctx, d, ellipse_pts(e["x"], e["y"], r, r), ctx.fc(col, p), 2.6, e.get("_seed", 1))
    if e.get("label"):
        ctx.text(d, e["x"], e["y"] + r + 14, e["label"], e.get("size", 22), ctx.fc(e.get("label_color", col), p), False,
                 "ma")


def draw_image(ctx, img, e, p):
    tile = ctx.fitted(e["path"], e["w"], e["h"], e.get("round", False))
    if tile is None:
        return
    op = clamp(e.get("opacity", 1.0) * p * ctx.g)
    if op < 0.999:
        tile = tile.copy()
        tile.putalpha(tile.getchannel("A").point(lambda a: int(a * op)))
    x = e["x"] + (e["w"] - tile.width / ctx.S) / 2
    y = e["y"] + (e["h"] - tile.height / ctx.S) / 2
    img.paste(tile, (round(x * ctx.S), round(y * ctx.S)), tile)


# --------------------------------------------------------------------------
# Layout: header, content region, flow auto-layout
# --------------------------------------------------------------------------
def header_layout(ctx, spec, sc):
    v, W, H, S = ctx.v, ctx.W, ctx.H, ctx.S
    L = {}
    if ctx.style == "sketch":
        ts = 30 if W >= 1400 else 34
        a = ctx.author
        y0 = 36 + (a["d"] + 18 if (a and a["pos"].startswith("top")) else 14)
        lines = wrap(ctx.font(ts), sc["heading"], (W - 160) * S) if sc.get("heading") else []
        L.update(title_size=ts, title_lines=lines, title_y=y0, tabs=[], sub_lines=[], head_lines=[])
        L["top"] = y0 + len(lines) * ts * 1.25 * ctx.fs + 46 if lines else y0 + 24
        return L
    hs = 46 if W >= 1400 else 40
    head = wrap(ctx.font(hs, True), sc.get("heading", ""), (W - 128) * S)
    y = 62 + len(head) * hs * 1.15
    sub_lines, sub_y = [], 0
    if sc.get("sub"):
        sub_lines = wrap(ctx.font(22), sc["sub"], (W - 128) * S)
        sub_y = y + 12
        y = sub_y + len(sub_lines) * 30
    tabs = []
    stepped = [(i, s) for i, s in enumerate(spec["scenes"]) if s.get("step")]
    if len(stepped) > 1 and v.get("tabs", True):
        x, ty = 64, y + 14
        for i, s in stepped:
            e = {"x": x, "y": ty, "text": s["step"].upper(), "size": 13, "color": "accent"}
            pw = pill_width(ctx, e)
            if x + pw > W - 64:
                x, ty = 64, ty + 36
                e["x"], e["y"] = x, ty
            tabs.append((e, i))
            x += pw + 10
        top = ty + 27 + 23
    else:
        top = y + 26
    L.update(head_size=hs, head_lines=head, head_y=62, sub_lines=sub_lines, sub_y=sub_y, tabs=tabs, top=top)
    return L


def content_rect(ctx, L):
    cap_h = 150 if ctx.W >= 1400 else 200
    bottom = ctx.H - (cap_h if ctx.cap else (56 if ctx.style == "sketch" else FOOTER_H))
    return 48, L["top"], ctx.W - 48, bottom


def resolve_units(ctx, e):
    e = dict(e)
    for k, ref in (("x", ctx.W), ("w", ctx.W), ("y", ctx.H), ("h", ctx.H)):
        if k in e:
            e[k] = num(e[k], ref)
    if "points" in e:
        e["points"] = [[num(p[0], ctx.W), num(p[1], ctx.H)] for p in e["points"]]
    if "r" in e:
        e["r"] = num(e["r"], ctx.W)
    return e


def expand_flow(ctx, e, region):
    """A flow is nodes joined by arrows, laid out to fit the canvas (row on wide, column on tall)."""
    nodes = e["nodes"]
    n = len(nodes)
    x0, y0, x1, y1 = region
    if "x" in e: x0 = num(e["x"], ctx.W)
    if "y" in e: y0 = num(e["y"], ctx.H)
    if "w" in e: x1 = x0 + num(e["w"], ctx.W)
    if "h" in e: y1 = y0 + num(e["h"], ctx.H)
    loop = e.get("loop", False)
    arrows = e.get("arrows", True)
    direction = e.get("direction", "auto")
    rw, rh = x1 - x0, y1 - y0
    gap_row = 70 if arrows else 36
    if direction == "auto":
        direction = "row" if (rw >= 1.25 * rh and (rw - gap_row * (n - 1)) / n >= 170) else "column"
    out = []
    span = e.get("span", [0.04, 0.82])
    step = (span[1] - span[0]) / max(1, n)
    base_size = e.get("size")
    boxes = []
    if direction == "row":
        if loop:
            rh -= 80
        nw = min(e.get("node_w", 290), (rw - gap_row * (n - 1)) / n)
        nh = e.get("node_h", 140)
        total = n * nw + (n - 1) * gap_row
        sx, sy = x0 + (rw - total) / 2, y0 + (rh - nh) / 2
        for i in range(n):
            boxes.append((sx + i * (nw + gap_row), sy, nw, nh))
    else:
        if loop:
            rw -= 100
            if e.get("loop_label"):
                rh -= 50
        nw = min(e.get("node_w", 860), rw)
        gap = 44 if arrows else 26
        nh = min(e.get("node_h", 200), max(64, (rh - gap * (n - 1)) / n))
        if n > 1 and n * nh + (n - 1) * gap > rh:
            gap = max(26, (rh - n * nh) / (n - 1))
            nh = (rh - gap * (n - 1)) / n
        total = n * nh + (n - 1) * gap
        sx, sy = x0 + (rw - nw) / 2, y0 + (rh - total) / 2
        for i in range(n):
            boxes.append((sx, sy + i * (nh + gap), nw, nh))
    for i, (nd, (bx, by, bw, bh)) in enumerate(zip(nodes, boxes)):
        b = {"type": "box", "x": bx, "y": by, "w": bw, "h": bh, "at": span[0] + i * step}
        b.update({k: v for k, v in nd.items() if k not in ("arrow_label",)})
        b.setdefault("size", base_size or (34 if bh >= 170 else 27 if bh >= 110 else 21))
        b.setdefault("sub_size", max(14, round(b["size"] * 0.62)))
        out.append(b)
        if arrows and i < n - 1:
            nx, ny, nbw, nbh = boxes[i + 1]
            if direction == "row":
                pts = [[bx + bw + 6, by + bh / 2], [nx - 6, ny + nbh / 2]]
            else:
                pts = [[bx + bw / 2, by + bh + 6], [nx + nbw / 2, ny - 6]]
            a = {"type": "arrow", "points": pts, "at": span[0] + (i + 0.55) * step, "color": e.get("color", "accent"),
                 "label_size": max(16, round(b["size"] * 0.6))}
            if nd.get("arrow_label"):
                a["label"] = nd["arrow_label"]
            out.append(a)
    if loop and n > 1:
        (fx, fy, fw, fh), (lx, ly, lw, lh) = boxes[0], boxes[-1]
        if direction == "row":
            yb = max(ly, fy) + max(lh, fh) + 6
            pts = [[lx + lw / 2, yb], [lx + lw / 2, yb + 52], [fx + fw / 2, yb + 52], [fx + fw / 2, yb]]
        else:
            xr = max(fx + fw, lx + lw) + 6
            pts = [[lx + lw + 6, ly + lh / 2], [xr + 48, ly + lh / 2], [xr + 48, fy + fh / 2], [fx + fw + 6, fy + fh / 2]]
        a = {"type": "arrow", "points": pts, "at": span[1], "dur": 1.0, "color": e.get("loop_color", "coral"),
             "label_size": max(16, round(out[0]["size"] * 0.6))}
        if e.get("loop_label"):
            if direction == "row":
                a["label"] = e["loop_label"]
            else:  # a vertical loop leaves no room beside it, so caption it under the last node
                out.append({"type": "text", "x": sx + nw / 2, "y": ly + lh + 18, "text": e["loop_label"],
                            "size": max(20, round(out[0]["size"] * 0.7)), "color": e.get("loop_color", "coral"),
                            "align": "center", "at": span[1]})
        out.append(a)
    return out


def expand_scene(ctx, spec, sc, k, L):
    els = sc.get("elements", [])
    if isinstance(els, dict):
        els = next((els[key] for key in VARIANT_ORDER[ctx.canvas_name] if key in els), [])
    region = content_rect(ctx, L)
    out = []
    for e in els:
        if e.get("type") == "flow":
            out += expand_flow(ctx, e, region)
        else:
            out.append(resolve_units(ctx, e))
    for i, e in enumerate(out):
        e["_seed"] = zlib.crc32(f"{sc['id']}:{i}".encode()) % 100000
    return out


# --------------------------------------------------------------------------
# Spec loading, planning, timing, audio
# --------------------------------------------------------------------------
def load_spec(path, canvas=None, style=None, theme=None):
    p = Path(path)
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["_dir"] = str(p.resolve().parent)
    spec.setdefault("video", {})
    v = spec["video"]
    if canvas: v["canvas"] = canvas
    if style: v["style"] = style
    if theme: v["theme"] = theme
    v.setdefault("canvas", "landscape")
    v.setdefault("style", "clean")
    try:
        resolve_canvas(v["canvas"])
    except ValueError as err:
        sys.exit(str(err))
    if v["style"] not in STYLES:
        sys.exit(f"video.style must be one of {STYLES}")
    if isinstance(v.get("theme"), str) and v["theme"] not in THEMES:
        sys.exit(f"video.theme must be one of {sorted(THEMES)} (or an object of colours)")
    if not spec.get("scenes"):
        sys.exit("spec has no scenes")
    for k, sc in enumerate(spec["scenes"], 1):
        sc.setdefault("id", f"scene-{k:02d}")
        sc.setdefault("elements", [])
    return spec


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s]


def estimate(text):
    return max(2.5, len(text.split()) / 2.5 + 0.25 * len(sentences(text)))


def narration_of(sc):
    return (sc.get("narration") or "").strip()


def find_audio(audio_dir, sid):
    if not audio_dir:
        return None
    for ext in AUDIO_EXTS:
        p = Path(audio_dir) / (sid + ext)
        if p.exists():
            return str(p)
    return None


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def build_plan(spec, audio_dir, use_audio=True):
    """Each scene lasts lead + content + tail seconds. Content = the audio clip if there is one, else the
    scene's `duration`, else an estimate from the narration, else 6 s."""
    v = spec["video"]
    lead, tail = v.get("lead", 0.5), v.get("tail", 0.9)
    plan, t, missing = [], 0.0, []
    for sc in spec["scenes"]:
        narr = narration_of(sc)
        audio = find_audio(audio_dir, sc["id"]) if use_audio else None
        if audio:
            ad = probe(audio)
        else:
            if narr and use_audio:
                missing.append(sc["id"])
            ad = float(sc["duration"]) if sc.get("duration") else (estimate(narr) if narr else 6.0)
        dur = lead + ad + tail
        plan.append({"id": sc["id"], "start": t, "dur": dur, "lead": lead, "ad": ad, "audio": audio})
        t += dur
    return plan, t, missing


def build_audio(plan, total, out_wav, sr=24000):
    buf = np.zeros(int(math.ceil(total * sr)) + sr, dtype=np.float32)
    for p in plan:
        if not p["audio"]:
            continue
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p["audio"], "-f", "f32le", "-ar", str(sr), "-ac", "1", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        s = int((p["start"] + p["lead"]) * sr)
        buf[s:s + len(x)] += x[:len(buf) - s]
    pcm = (np.clip(buf, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def caption_timeline(narration, ad):
    sents = sentences(narration)
    total = sum(len(s) for s in sents) or 1
    out, t = [], 0.0
    for s in sents:
        dur = ad * len(s) / total
        out.append((t, t + dur, s))
        t += dur
    return out


# ---- TTS engines: any engine that can turn narration/<id>.txt into audio/<id>.<ext> ----
TTS_PRESETS = {
    "edge": {"cmd": "edge-tts --file {text_file} --voice {voice} --rate={rate} --write-media {out}", "ext": ".mp3",
             "voice": "en-US-AriaNeural"},
    "piper": {"cmd": "piper --model {voice} --output_file {out}", "stdin": True, "ext": ".wav",
              "voice": "en_US-lessac-medium.onnx"},
}


def write_narration(spec, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for sc in spec["scenes"]:
        if narration_of(sc):
            (out / f"{sc['id']}.txt").write_text(narration_of(sc) + "\n", encoding="utf-8")
            n += 1
    return n


def find_narrate(hint, spec_dir):
    cands = [hint, os.environ.get("EVIDEO_NARRATE"), HERE.parent.parent / "narrate-your-writing" / "narrate.py",
             Path(spec_dir) / "narrate.py", Path.cwd() / "narrate.py"]
    for c in cands:
        if not c:
            continue
        c = Path(c)
        if c.is_dir():
            c = c / "narrate.py"
        if c.exists():
            return c.resolve()
    sys.exit("Could not find narrate.py (narrate-your-writing). Pass --narrate-path /path/to/narrate.py, "
             "set EVIDEO_NARRATE, or use another engine: --tts edge | piper | command --tts-cmd '...'")


def generate_audio(spec, a):
    tts = dict(spec["video"].get("tts", {}))
    engine = (a.tts or tts.get("engine") or "narrate-your-writing").lower()
    voice, speed = a.voice or tts.get("voice"), a.speed or tts.get("speed")
    audio_dir, narr_dir = Path(a.audio_dir), Path(getattr(a, "narration_dir", "narration"))
    audio_dir.mkdir(parents=True, exist_ok=True)
    if write_narration(spec, narr_dir) == 0:
        print("no scene has narration; nothing to generate", file=sys.stderr)
        return
    if engine in ("narrate-your-writing", "nyw", "kokoro"):
        script = find_narrate(a.narrate_path or tts.get("narrate_path"), spec["_dir"])
        cmd = [a.tts_python or tts.get("python") or sys.executable, str(script), str(narr_dir.resolve()),
               "--out", str(audio_dir.resolve())]
        if voice: cmd += ["--voice", str(voice)]
        if speed: cmd += ["--speed", str(speed)]
        if tts.get("device"): cmd += ["--device", str(tts["device"])]
        cmd += [str(x) for x in tts.get("args", [])]
        print("running:", " ".join(shlex.quote(c) for c in cmd), file=sys.stderr)
        subprocess.run(cmd, cwd=script.parent, check=True)
        return
    preset = TTS_PRESETS.get(engine, {})
    cmd = a.tts_cmd or tts.get("command") or preset.get("cmd")
    if not cmd:
        sys.exit(f"Unknown TTS engine '{engine}'. Use narrate-your-writing, edge, piper, or command with --tts-cmd "
                 "'mytts --in {text_file} --out {out}'")
    ext = tts.get("ext") or preset.get("ext", ".mp3")
    stdin = tts.get("stdin", preset.get("stdin", False))
    voice = voice or preset.get("voice", "")
    rate = f"{round(((float(speed) if speed else 1.0) - 1) * 100):+d}%"
    for sc in spec["scenes"]:
        narr = narration_of(sc)
        out = audio_dir / (sc["id"] + ext)
        if not narr or (out.exists() and not a.force):
            continue
        m = {"text_file": shlex.quote(str((narr_dir / (sc["id"] + ".txt")).resolve())), "text": shlex.quote(narr),
             "out": shlex.quote(str(out.resolve())), "voice": shlex.quote(str(voice)), "speed": speed or 1.0,
             "rate": rate}
        full = cmd.format(**m)
        print(f"[{sc['id']}] {full}", file=sys.stderr)
        subprocess.run(full, shell=True, input=narr if stdin else None, text=True, check=True)
        if not out.exists():
            sys.exit(f"TTS command ran but did not create {out}")


# --------------------------------------------------------------------------
# Frame rendering
# --------------------------------------------------------------------------
G = {}


def _init(spec, plan, opts):
    ctx = Ctx(spec, opts["scale"], opts.get("ss", 2))
    ctx.cap = bool(opts["captions"])
    layouts = [header_layout(ctx, spec, sc) for sc in spec["scenes"]]
    G.update(spec=spec, plan=plan, opts=opts, ctx=ctx, starts=[p["start"] for p in plan], layouts=layouts,
             els=[expand_scene(ctx, spec, sc, k, layouts[k]) for k, sc in enumerate(spec["scenes"])])


def draw_author(ctx, img, d):
    a = ctx.author
    if not a:
        return
    dd, S, W, H = a["d"], ctx.S, ctx.W, ctx.H
    mx = 48 if ctx.style == "sketch" else 64
    cy = (36 + dd / 2) if a["pos"].startswith("top") else (H - 36)
    right = a["pos"].endswith("right")
    show_av = a.get("avatar", True) is not False
    cx = (W - mx - dd / 2) if right else (mx + dd / 2)
    tx = (cx - dd / 2 - 12) if (right and show_av) else (W - mx if right else (cx + dd / 2 + 12 if show_av else mx))
    anchor = "r" if right else "l"
    ns, hs = round(dd * 0.5), round(dd * 0.36)
    if show_av:
        av = ctx.avatar(dd)
        img.paste(av, (round((cx - dd / 2) * S), round((cy - dd / 2) * S)), av)
    if a.get("handle"):
        ctx.text(d, tx, cy - hs * 0.55, a.get("name", ""), ns, ctx.rgb("ink"), False, anchor + "m")
        ctx.text(d, tx, cy + ns * 0.55, a["handle"], hs, ctx.rgb("muted"), False, anchor + "m")
    else:
        ctx.text(d, tx, cy, a.get("name", ""), ns, ctx.rgb("ink"), False, anchor + "m")


def render_frame_at(k, tn, lt, dur, ad, with_captions=True):
    """Render scene k at local time lt (tn = seconds into the content phase)."""
    spec, ctx = G["spec"], G["ctx"]
    v, sc, L = spec["video"], spec["scenes"][k], G["layouts"][k]
    W, H, S = ctx.W, ctx.H, ctx.S
    img = Image.new("RGB", (round(S * W), round(S * H)), ctx.bg)
    d = ImageDraw.Draw(img)

    # ---- persistent chrome ----
    ctx.g = 1.0
    ph = ease(lt / 0.45)
    if ctx.style == "sketch":
        for i, ln in enumerate(L["title_lines"]):
            ctx.text(d, W / 2, L["title_y"] + i * L["title_size"] * 1.25 * ctx.fs, ln, L["title_size"],
                     ctx.fc("yellow", ph), False, "ma")
        if v.get("kicker"):
            ctx.text(d, 48, 40, v["kicker"].upper(), 14, ctx.fc("muted"), True, "la")
    else:
        if v.get("kicker"):
            ctx.text(d, 64, 34, v["kicker"].upper(), 14, ctx.fc("muted"), True, "la")
        if sc.get("tag"):
            ctx.text(d, W - 64, 34, sc["tag"].upper(), 14, ctx.fc("accent"), True, "ra")
        for i, ln in enumerate(L["head_lines"]):
            ctx.text(d, 64, L["head_y"] + (1 - ph) * 10 + i * L["head_size"] * 1.15, ln, L["head_size"],
                     ctx.fc("ink", ph), True, "la")
        for i, ln in enumerate(L["sub_lines"]):
            ctx.text(d, 64, L["sub_y"] + i * 30, ln, 22, ctx.fc("muted", ph), False, "la")
        for e, i in L["tabs"]:
            draw_pill(ctx, d, e, 1.0, filled=(i == k))
        fy = H - 36
        if v.get("footer_right"):
            ctx.text(d, W - 64, fy, v["footer_right"], 15, ctx.fc("muted"), False, "rm")
        if v.get("dots", len(spec["scenes"]) > 1):
            n, dot, gap_ = len(spec["scenes"]), 9, 10
            x = W / 2 - (n * dot + (n - 1) * gap_) / 2
            for i in range(n):
                wdt = dot * 2.2 if i == k else dot
                d.rounded_rectangle([x * S, (fy - dot / 2) * S, (x + wdt) * S, (fy + dot / 2) * S], radius=dot / 2 * S,
                                    fill=ctx.fc("accent" if i == k else "line"))
                x += wdt + gap_
    draw_author(ctx, img, d)

    # ---- animated content ----
    ctx.g = clamp((dur - lt) / 0.3)
    for e in G["els"][k]:
        t = e["type"]
        dur_e = e.get("dur", 0.7 if t == "arrow" else 0.45)
        p = ease((tn - e.get("at", 0) * ad) / dur_e)
        if p <= 0:
            continue
        hl = 0.0
        if e.get("hl"):
            a, b = e["hl"][0] * ad, e["hl"][1] * ad
            hl = clamp(min(tn - a, b - tn) / 0.25)
        if t == "box":
            draw_box(ctx, d, e, p, hl)
        elif t == "grid":
            draw_grid(ctx, d, e, p, tn, ad)
        elif t == "arrow":
            draw_arrow(ctx, d, e, p)
        elif t == "text":
            mq = ease((tn - e["mark"].get("at", 0.8) * ad) / 0.45) if e.get("mark") else 0.0
            draw_text(ctx, d, e, p, mq)
        elif t == "pill":
            draw_pill(ctx, d, e, p)
        elif t == "circle":
            draw_circle(ctx, d, e, p, hl)
        elif t == "image":
            draw_image(ctx, img, e, p)

    # ---- captions ----
    narr = narration_of(sc)
    if with_captions and G["opts"]["captions"] and narr and 0 <= tn <= ad:
        tc = min(tn, ad - 1e-6)
        cs = v.get("caption_style", {})
        boxed = cs.get("box", ctx.style == "clean")
        size = cs.get("size", 26 if W >= 1400 else 30)
        for a, b, s in caption_timeline(narr, ad):
            if a <= tc < b:
                ctx.g = 1.0
                lines = wrap(ctx.font(size), s, (W - 260) * S)[:3]
                lh = size * 1.3 * ctx.fs
                bh = len(lines) * lh + 26
                y1 = H - (66 if ctx.style == "clean" else 80)
                if boxed:
                    d.rounded_rectangle([130 * S, (y1 - bh) * S, (W - 130) * S, y1 * S], radius=16 * S,
                                        fill=ctx.fc("card"), outline=ctx.fc("line"), width=max(1, int(S)))
                for i, ln in enumerate(lines):
                    ctx.text(d, W / 2, y1 - bh + 13 + i * lh, ln, size, ctx.fc("ink"), False, "ma",
                             stroke=None if boxed else ctx.bg)
                break
    ov = ctx.watermark()
    if ov is not None:
        img.paste(ov, (0, 0), ov)
    if img.size != (ctx.out_w, ctx.out_h):
        if img.width == ctx.out_w * ctx.ss and img.height == ctx.out_h * ctx.ss:
            return img.reduce(ctx.ss)
        return img.resize((ctx.out_w, ctx.out_h), Image.LANCZOS)
    return img


def frame_job(i):
    plan, fps = G["plan"], G["opts"]["fps"]
    t = i / fps
    k = max(0, bisect.bisect_right(G["starts"], t) - 1)
    p = plan[k]
    lt = min(t - p["start"], p["dur"] - 1e-3)
    return render_frame_at(k, lt - p["lead"], lt, p["dur"], p["ad"]).tobytes()


def final_state(k, at=1.0):
    p = G["plan"][k]
    tn = at * p["ad"]
    return render_frame_at(k, tn, tn + p["lead"], p["dur"], p["ad"], with_captions=False)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
REQUIRED = {"box": ["x", "y", "w", "h"], "grid": ["x", "y", "cols", "rows"], "arrow": ["points"],
            "text": ["x", "y", "text"], "pill": ["x", "y", "text"], "circle": ["x", "y"],
            "image": ["x", "y", "w", "h", "path"]}


def bbox(ctx, e):
    t = e["type"]
    if t == "box":
        return e["x"], e["y"], e["w"], e["h"]
    if t == "grid":
        w, h = grid_size(e)
        return e["x"], e["y"], w, h + (26 if e.get("label") else 0)
    if t == "pill":
        return e["x"], e["y"], pill_width(ctx, e), e.get("size", 14) * ctx.fs + 14
    if t == "circle":
        r = e.get("r", 24)
        return e["x"] - r, e["y"] - r, 2 * r, 2 * r + (40 if e.get("label") else 0)
    if t == "image":
        return e["x"], e["y"], e["w"], e["h"]
    if t == "text":
        f = ctx.font(e.get("size", 22), e.get("bold", False))
        sz = e.get("size", 22) * 1.3 * ctx.fs
        if e.get("w"):
            lines = wrap(f, e["text"], e["w"] * ctx.S)
            return e["x"], e["y"], e["w"], len(lines) * sz
        lines = str(e["text"]).split("\n")
        w = max(f.getlength(ln) for ln in lines) / ctx.S
        x = e["x"] - {"left": 0, "center": w / 2, "right": w}[e.get("align", "left")]
        return x, e["y"], w, len(lines) * sz
    xs, ys = [p[0] for p in e["points"]], [p[1] for p in e["points"]]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def validate(spec, captions=False):
    ctx = Ctx(spec)
    ctx.cap = captions
    issues, seen = [], set()
    for sc in spec["scenes"]:
        if sc["id"] in seen:
            issues.append(f"{sc['id']}: duplicate scene id (audio files are matched by id)")
        seen.add(sc["id"])
    for k, sc in enumerate(spec["scenes"]):
        sid = sc["id"]
        narr = narration_of(sc)
        if narr and re.search(r"\d", narr):
            issues.append(f"{sid}: narration contains digits - write numbers as words so the TTS reads them right")
        if not sc.get("heading"):
            issues.append(f"{sid}: missing heading")
        L = header_layout(ctx, spec, sc)
        x0, top, x1, bottom = content_rect(ctx, L)
        raw = sc.get("elements", [])
        if isinstance(raw, dict):
            raw = next((raw[key] for key in VARIANT_ORDER[ctx.canvas_name] if key in raw), [])
        for n, e in enumerate(raw):
            if e.get("type") not in ELEMENT_TYPES:
                issues.append(f"{sid} element {n + 1}: unknown type '{e.get('type')}'; use one of {sorted(ELEMENT_TYPES)}")
            if e.get("type") == "flow" and not e.get("nodes"):
                issues.append(f"{sid} element {n + 1}: flow needs a non-empty `nodes` list")
            for key in ("at", "dur"):
                if key in e and not isinstance(e[key], (int, float)):
                    issues.append(f"{sid} element {n + 1}: `{key}` must be a number")
            if "at" in e and isinstance(e["at"], (int, float)) and not 0 <= e["at"] <= 1:
                issues.append(f"{sid} element {n + 1}: `at` is a fraction of the scene (0 to 1)")
        els = expand_scene(ctx, spec, sc, k, L)
        boxes = []
        for n, e in enumerate(els):
            name = f"{sid} element {n + 1} ({e.get('type')}: {e.get('title') or e.get('text') or ''})"
            if e.get("type") not in ELEMENT_TYPES or e["type"] == "flow":
                continue
            miss = [kk for kk in REQUIRED[e["type"]] if kk not in e]
            if miss:
                issues.append(f"{name}: missing {miss}")
                continue
            if e["type"] == "box" and e.get("style", "default") not in BOX_STYLES:
                issues.append(f"{name}: unknown style; use one of {list(BOX_STYLES)}")
                continue
            if e["type"] == "image" and ctx.load_image(e["path"]) is None:
                issues.append(f"{name}: cannot open image '{e['path']}' (paths are relative to the spec file)")
                continue
            x, y, w, h = bbox(ctx, e)
            if x < 24 or x + w > ctx.W - 24 or y < top - 4 or y + h > bottom + 4:
                hint = ("" if ctx.canvas_name == "landscape" else
                        f" This canvas is {ctx.canvas_name}: use a `flow` element, % units, or per-canvas `elements`.")
                issues.append(f"{name}: outside the safe area (x 24..{ctx.W - 24}, y {top:.0f}..{bottom:.0f}) "
                              f"at x={x:.0f}..{x + w:.0f}, y={y:.0f}..{y + h:.0f}.{hint}")
            if e["type"] == "box":
                tsz, ssz = e.get("size", 22), e.get("sub_size", 15)
                tf, sf = ctx.font(tsz, True), ctx.font(ssz)
                tl = wrap(tf, e.get("title", ""), (e["w"] - 24) * ctx.S) if e.get("title") else []
                sl = wrap(sf, e.get("sub", ""), (e["w"] - 24) * ctx.S) if e.get("sub") else []
                need = len(tl) * tsz * 1.25 * ctx.fs + (len(sl) * ssz * 1.35 * ctx.fs + 6 if sl else 0)
                if need > e["h"] - 8:
                    issues.append(f"{name}: text needs ~{need:.0f} units of height but the box is {e['h']:.0f}")
                if any(tf.getlength(wd) > (e["w"] - 24) * ctx.S for wd in e.get("title", "").split()):
                    issues.append(f"{name}: a title word is wider than the box")
            if e["type"] in ("box", "grid"):
                boxes.append((name, (x, y, w, h)))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i][1], boxes[j][1]
                if a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]:
                    issues.append(f"{sid}: '{boxes[i][0]}' overlaps '{boxes[j][0]}'")
    return issues


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
def srt_time(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(spec, plan, path):
    lines, n = [], 1
    for sc, p in zip(spec["scenes"], plan):
        if not narration_of(sc):
            continue
        for a, b, s in caption_timeline(narration_of(sc), p["ad"]):
            t0 = p["start"] + p["lead"]
            lines += [str(n), f"{srt_time(t0 + a)} --> {srt_time(t0 + b)}", s, ""]
            n += 1
    if n > 1:
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        return True
    return False


def encode(spec, plan, total, target, a, fps, scale, captions, has_audio, wav):
    ext = target.suffix.lower()
    opts = {"scale": scale, "fps": fps, "captions": captions, "ss": 1 if a.fast else a.ss}
    ctx = Ctx(spec, scale, opts["ss"])
    nframes = int(math.ceil(total * fps))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ctx.out_w}x{ctx.out_h}",
           "-r", str(fps), "-i", "-"]
    if ext == ".gif":
        cmd += ["-vf", "split[s0][s1];[s0]palettegen=max_colors=160:stats_mode=diff[p];"
                       "[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", str(target)]
    else:
        if has_audio:
            cmd += ["-i", str(wav)]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(23 if a.fast else a.crf), "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"] if has_audio else []
        cmd += ["-movflags", "+faststart", str(target)]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    workers = a.workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"{len(plan)} scenes, {total:.1f}s, {nframes} frames at {ctx.out_w}x{ctx.out_h}, {workers} workers "
          f"[{ctx.canvas_name}/{ctx.style}/{spec['video'].get('theme', 'default')}]", file=sys.stderr)
    with Pool(workers, initializer=_init, initargs=(spec, plan, opts)) as pool:
        for n, frame in enumerate(pool.imap(frame_job, range(nframes), chunksize=8)):
            ff.stdin.write(frame)
            if n % 90 == 0:
                print(f"\r  frame {n}/{nframes}", end="", file=sys.stderr)
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("ffmpeg failed")
    print(file=sys.stderr)


def render_one(a, spec, target):
    ext = target.suffix.lower()
    if ext not in (".mp4", ".gif", ".png", ".pdf"):
        sys.exit("--out must end in .mp4, .gif, .png or .pdf")
    v = spec["video"]
    mode = "none" if a.preview else (a.audio or v.get("audio", "auto"))
    if mode not in ("auto", "none", "files", "generate"):
        sys.exit("--audio must be auto, none, files or generate")
    target.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".mp4" and mode == "generate":
        generate_audio(spec, a)
    plan, total, missing = build_plan(spec, a.audio_dir, use_audio=(mode != "none"))
    if ext == ".mp4" and missing and mode in ("auto", "files", "generate"):
        sys.exit("No audio for: " + ", ".join(missing) + "\nPick one:\n"
                 "  --audio none        silent video, narration text shown as captions\n"
                 "  --audio generate    make the voice now (--tts narrate-your-writing | edge | piper | command)\n"
                 f"  or put audio/<scene-id>.mp3 files in {a.audio_dir}/ and run again")
    has_audio = ext == ".mp4" and any(p["audio"] for p in plan)
    has_narr = any(narration_of(sc) for sc in spec["scenes"])
    captions = a.captions if a.captions is not None else v.get("captions", (not has_audio) and has_narr)
    if ext in (".png", ".pdf"):
        captions = False
    ss = 1 if a.fast else a.ss
    if ext in (".png", ".pdf"):
        scale = a.scale or 1.0
        _init(spec, plan, {"scale": scale, "fps": 30, "captions": False, "ss": ss})
        n = len(spec["scenes"])
        if ext == ".pdf":
            imgs = [final_state(k, a.at) for k in range(n)]
            imgs[0].save(target, "PDF", resolution=150, save_all=True, append_images=imgs[1:])
            print(f"wrote {target} ({n} pages, {imgs[0].width}x{imgs[0].height})", file=sys.stderr)
        elif a.scene == "all":
            for k in range(n):
                t = target.with_name(f"{target.stem}-{k + 1:02d}{target.suffix}")
                final_state(k, a.at).save(t)
                print(f"wrote {t}", file=sys.stderr)
        else:
            k = int(a.scene) - 1
            im = final_state(k, a.at)
            im.save(target)
            print(f"wrote {target} ({im.width}x{im.height}) scene {k + 1}", file=sys.stderr)
        return
    scale = a.scale or (0.5 if ext == ".gif" else 1.0)
    fps = a.fps or (15 if ext == ".gif" else 30)
    wav = target.with_suffix(".wav")
    if has_audio:
        build_audio(plan, total, wav)
    encode(spec, plan, total, target, a, fps, scale, captions, has_audio, wav)
    if has_audio:
        wav.unlink()
    print(f"wrote {target}", file=sys.stderr)
    if ext == ".mp4" and write_srt(spec, plan, target.with_suffix(".srt")):
        print(f"wrote {target.with_suffix('.srt')}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def spec_from(a):
    return load_spec(a.spec, canvas=getattr(a, "canvas", None), style=getattr(a, "style", None),
                     theme=getattr(a, "theme", None))


def cmd_validate(a):
    spec = spec_from(a)
    issues = validate(spec, bool(a.captions))
    for i in issues:
        print("WARN", i)
    v = spec["video"]
    print(f"{len(spec['scenes'])} scenes, {len(issues)} issue(s)  "
          f"[{resolve_canvas(v['canvas'])[0]}/{v['style']}/{v.get('theme', 'default')}]")
    return 1 if issues else 0


def cmd_narration(a):
    spec = spec_from(a)
    n = write_narration(spec, a.out)
    print(f"wrote {n} files to {a.out}/")
    print("Next: make audio/<scene-id>.mp3 with your TTS, or let evideo do it:")
    print(f"  evideo.py audio {a.spec} --tts narrate-your-writing --narrate-path /path/to/narrate.py")


def cmd_audio(a):
    spec = spec_from(a)
    a.narration_dir = a.narration_dir or "narration"
    generate_audio(spec, a)
    got = [sc["id"] for sc in spec["scenes"] if find_audio(a.audio_dir, sc["id"])]
    need = [sc["id"] for sc in spec["scenes"] if narration_of(sc)]
    print(f"audio ready for {len([i for i in need if i in got])}/{len(need)} narrated scenes in {a.audio_dir}/")


def cmd_still(a):
    spec = spec_from(a)
    mode = "none" if a.audio_dir is None else "auto"
    plan, _, _ = build_plan(spec, a.audio_dir, use_audio=(mode != "none"))
    ss = 1 if a.fast else a.ss
    _init(spec, plan, {"scale": a.scale, "fps": 30, "captions": a.captions, "ss": ss})
    ks = range(len(spec["scenes"])) if a.scene == "all" else [int(a.scene) - 1]
    for k in ks:
        out = Path(a.out)
        if a.scene == "all":
            out = out.with_name(f"{out.stem}-{k + 1:02d}{out.suffix}")
        tn = a.at * plan[k]["ad"]
        im = render_frame_at(k, tn, tn + plan[k]["lead"], plan[k]["dur"], plan[k]["ad"])
        im.save(out)
        print(f"wrote {out} ({im.width}x{im.height}) scene {k + 1} at {a.at:.0%}")


def cmd_render(a):
    cvs = [c.strip() for c in a.canvas.split(",")] if a.canvas else [None]
    out = Path(a.out)
    for cv in cvs:
        a.canvas_one = cv
        spec = load_spec(a.spec, canvas=cv, style=a.style, theme=a.theme)
        target = out if len(cvs) == 1 else out.with_name(f"{out.stem}-{cv}{out.suffix}")
        render_one(a, spec, target)


def add_look(p):
    p.add_argument("--canvas", help="landscape | square | portrait | story  (aliases: linkedin, youtube, reels)")
    p.add_argument("--style", choices=STYLES, help="clean | sketch")
    p.add_argument("--theme", help="light | warm | dark")


def main():
    ap = argparse.ArgumentParser(prog="evideo", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a spec for layout problems before rendering")
    v.add_argument("spec")
    add_look(v)
    v.add_argument("--captions", action="store_true", help="reserve space for burned-in captions")
    v.set_defaults(fn=cmd_validate)

    n = sub.add_parser("narration", help="write narration/<scene-id>.txt for your TTS")
    n.add_argument("spec")
    n.add_argument("--out", default="narration")
    n.set_defaults(fn=cmd_narration)

    au = sub.add_parser("audio", help="generate audio/<scene-id>.* with a TTS engine")
    au.add_argument("spec")
    au.add_argument("--tts", help="narrate-your-writing (Kokoro) | edge | piper | command")
    au.add_argument("--tts-cmd", help="custom command, e.g. 'mytts --in {text_file} --out {out} --voice {voice}'")
    au.add_argument("--tts-python", help="python for narrate.py (e.g. the conda env that has kokoro-onnx)")
    au.add_argument("--narrate-path", help="path to narrate-your-writing/narrate.py")
    au.add_argument("--voice")
    au.add_argument("--speed", type=float)
    au.add_argument("--audio-dir", default="audio")
    au.add_argument("--narration-dir", default="narration")
    au.add_argument("--force", action="store_true", help="regenerate clips that already exist")
    au.set_defaults(fn=cmd_audio)

    s = sub.add_parser("still", help="render one scene to a PNG (check layout before a full render)")
    s.add_argument("spec")
    add_look(s)
    s.add_argument("--scene", default="1", help="scene number or 'all'")
    s.add_argument("--at", type=float, default=1.0, help="position in the scene, 0..1 (1 = everything shown)")
    s.add_argument("--out", default="still.png")
    s.add_argument("--audio-dir", default=None, help="use real clip lengths for timing")
    s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--captions", action="store_true")
    s.add_argument("--ss", type=int, default=2, help="supersampling (1 = faster, 2 = smoother)")
    s.add_argument("--fast", action="store_true")
    s.set_defaults(fn=cmd_still)

    r = sub.add_parser("render", help="render .mp4 / .gif / .png / .pdf")
    r.add_argument("spec")
    add_look(r)
    r.add_argument("--out", "-o", default="out/video.mp4", help=".mp4 video | .gif loop | .png single frame | .pdf carousel")
    r.add_argument("--audio", choices=["auto", "none", "files", "generate"],
                   help="none = on-screen text only | files = use audio/<id>.mp3 | generate = make it with --tts")
    r.add_argument("--preview", "--silent", action="store_true", help="same as --audio none")
    r.add_argument("--audio-dir", default="audio")
    r.add_argument("--captions", action=argparse.BooleanOptionalAction, default=None,
                   help="burn narration captions in (default: on when there is no audio)")
    r.add_argument("--scene", default="1", help="png only: scene number or 'all'")
    r.add_argument("--at", type=float, default=1.0, help="png/pdf: position in each scene, 0..1")
    r.add_argument("--scale", type=float, default=None, help="size multiplier (default 1.0; gif 0.5)")
    r.add_argument("--fps", type=int, default=None)
    r.add_argument("--crf", type=int, default=18)
    r.add_argument("--ss", type=int, default=2)
    r.add_argument("--fast", action="store_true", help="draft quality: no supersampling, smaller file")
    r.add_argument("--workers", type=int, default=0)
    for flag in ("--tts", "--tts-cmd", "--tts-python", "--narrate-path", "--voice"):
        r.add_argument(flag)
    r.add_argument("--speed", type=float)
    r.add_argument("--narration-dir", default="narration")
    r.add_argument("--force", action="store_true")
    r.set_defaults(fn=cmd_render)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
