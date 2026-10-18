"""Offline, deterministic illustrated poetry films with correctly shaped Tamil text."""

from __future__ import annotations

import hashlib
import math
import random
import re
import shutil
import subprocess
import unicodedata
import wave
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, features

FONT_ROOT = Path("/usr/share/fonts/truetype/noto")
BUNDLED_FONTS = Path(__file__).parent / "static" / "fonts"
THEMES = {"moon", "ocean", "rain", "forest", "sunrise", "city", "love", "abstract"}
FPS = 24


@lru_cache(maxsize=128)
def _font(size: int, *, latin: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = ("NotoSans" if latin else "NotoSerifTamil") + ("-Bold.ttf" if bold else "-Regular.ttf")
    candidates = [
        BUNDLED_FONTS / name,
        FONT_ROOT / name,
        BUNDLED_FONTS / "NotoSerifTamil-Regular.ttf",
        FONT_ROOT / ("NotoSansTamil-Bold.ttf" if bold else "NotoSansTamil-Regular.ttf"),
    ]
    if latin:
        candidates.insert(2, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError("Tamil fonts are missing. Install fonts-noto-core before rendering.")
    if not features.check_feature("raqm"):
        raise RuntimeError(
            "Pillow needs RAQM text shaping to render Tamil correctly. Install the official Pillow wheel."
        )
    return ImageFont.truetype(str(path), size=size, layout_engine=ImageFont.Layout.RAQM)


def _clusters(text: str) -> list[str]:
    """Keep vowel signs, viramas and joiners attached when a long word wraps."""
    result: list[str] = []
    for char in text:
        if result and (
            unicodedata.category(char).startswith("M")
            or char == "\u200d"
            or result[-1].endswith(("\u200d", "\u0bcd"))
        ):
            result[-1] += char
        else:
            result.append(char)
    return result


def _measure(text: str, font: ImageFont.FreeTypeFont) -> float:
    return font.getlength(text)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if _measure(candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if _measure(word, font) <= max_width:
                current = word
            else:
                for cluster in _clusters(word):
                    if current and _measure(current + cluster, font) > max_width:
                        lines.append(current)
                        current = ""
                    current += cluster
        if current:
            lines.append(current)
        elif not paragraph.strip() and lines and lines[-1] != "":
            lines.append("")
    return lines or [""]


def _gradient(
    size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    w, h = size
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    colors = np.asarray(top)[None, None, :] * (1 - t) + np.asarray(bottom)[None, None, :] * t
    return Image.fromarray(np.broadcast_to(colors, (h, w, 3)).astype(np.uint8), "RGB").convert(
        "RGBA"
    )


def _glow(
    image: Image.Image,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    opacity: int = 110,
) -> None:
    w, h = image.size
    layer = Image.new("RGBA", image.size)
    d = ImageDraw.Draw(layer)
    cx, cy = center[0] * w, center[1] * h
    r = radius * w
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, opacity))
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(1, r * 0.45))))


def _ridge(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    level: float,
    amplitude: float,
    color: tuple[int, ...],
    seed: int,
) -> None:
    w, h = size
    rng = random.Random(seed)
    phase = rng.random() * 5
    points = []
    for x in range(-10, w + 11, max(1, w // 120)):
        y = h * (
            level + amplitude * (math.sin(x / w * 5 + phase) + 0.45 * math.sin(x / w * 13 + phase))
        )
        points.append((x, y))
    draw.polygon([(0, h), *points, (w, h)], fill=color)


def _stars(image: Image.Image, rng: random.Random, count: int = 80) -> None:
    w, h = image.size
    d = ImageDraw.Draw(image)
    for _ in range(count):
        x, y = rng.uniform(0.04, 0.96) * w, rng.uniform(0.02, 0.58) * h
        r = rng.choice([0.5, 0.7, 1.0, 1.3]) * w / 400
        a = rng.randint(75, 210)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(225, 235, 255, a))


def _moon(
    image: Image.Image, center: tuple[float, float] = (0.73, 0.23), radius: float = 0.10
) -> None:
    w, h = image.size
    _glow(image, center, radius * 1.8, (220, 216, 188), 75)
    d = ImageDraw.Draw(image)
    x, y, r = center[0] * w, center[1] * h, radius * w
    d.ellipse((x - r, y - r, x + r, y + r), fill=(244, 236, 204, 255))
    d.ellipse((x - r * 0.5, y - r * 0.65, x + r * 0.1, y - r * 0.05), fill=(220, 215, 193, 95))
    d.ellipse((x + r * 0.05, y + r * 0.1, x + r * 0.62, y + r * 0.65), fill=(222, 216, 193, 65))


def _tree(
    draw: ImageDraw.ImageDraw, x: float, ground: float, height: float, color: tuple[int, ...]
) -> None:
    draw.line((x, ground - height * 0.85, x, ground), fill=color, width=max(1, int(height * 0.025)))
    for tier in range(5):
        y = ground - height + tier * height * 0.15
        half = height * (0.10 + tier * 0.035)
        draw.polygon(
            [(x, y), (x - half, y + height * 0.3), (x + half, y + height * 0.3)], fill=color
        )


def _scenery(
    theme: str, style: str, mood: str, size: tuple[int, int], seed_text: str
) -> Image.Image:
    """Draw original scenery; never relies on remote stock media or credentials."""
    seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    w, h = size
    palettes = {
        "moon": ((10, 19, 42), (62, 81, 107)),
        "ocean": ((14, 40, 64), (95, 139, 148)),
        "rain": ((15, 29, 43), (62, 82, 93)),
        "forest": ((12, 33, 36), (87, 124, 100)),
        "sunrise": ((60, 39, 72), (239, 167, 111)),
        "city": ((22, 26, 48), (111, 81, 98)),
        "love": ((59, 30, 62), (173, 98, 117)),
        "abstract": ((25, 30, 57), (96, 95, 130)),
    }
    top, bottom = palettes.get(theme, palettes["abstract"])
    if style == "dreamy":
        top = tuple(min(255, c + 19) for c in top)
        bottom = tuple(min(255, c + 18) for c in bottom)
    if mood == "melancholic":
        bottom = tuple(int(c * 0.77) for c in bottom)
    image = _gradient(size, top, bottom)
    if theme in {"moon", "ocean", "forest", "love", "abstract"}:
        _stars(image, rng, 90 if theme == "moon" else 35)
    d = ImageDraw.Draw(image)

    if theme == "moon":
        _moon(image)
        for level, amplitude, color in [
            (0.60, 0.09, (39, 56, 76, 255)),
            (0.73, 0.06, (25, 42, 60, 255)),
            (0.86, 0.035, (14, 28, 44, 255)),
        ]:
            _ridge(d, size, level, amplitude, color, seed + int(level * 100))
        # A solitary tree leaves room for the poem in the centre.
        _tree(d, w * 0.17, h * 0.90, h * 0.20, (10, 23, 35, 255))
    elif theme == "ocean":
        _moon(image, (0.72, 0.22), 0.075)
        d = ImageDraw.Draw(image)
        horizon = 0.48
        d.rectangle((0, h * horizon, w, h), fill=(28, 70, 85, 255))
        for i in range(110):
            yy = h * (horizon + i / 110 * (1 - horizon))
            xmid = w * (0.72 + 0.025 * math.sin(i * 0.8))
            span = w * (0.025 + 0.18 * i / 110)
            d.line(
                (xmid - span, yy, xmid + span, yy),
                fill=(166, 177, 155, max(7, 55 - int(i * 0.3))),
                width=max(1, int(h / 800)),
            )
        for i in range(28):
            yy = h * (0.5 + i * 0.019)
            pts = [
                (x, yy + math.sin(x / w * 10 + i) * h * 0.003 * (1 + i / 10))
                for x in range(0, w + 4, 4)
            ]
            d.line(pts, fill=(105, 163, 166, 65), width=max(1, int(h / 500)))
        _ridge(d, size, 0.96, 0.022, (14, 40, 53, 255), seed)
    elif theme == "sunrise":
        _glow(image, (0.52, 0.43), 0.55, (255, 193, 123), 150)
        d = ImageDraw.Draw(image)
        r = w * 0.16
        d.ellipse(
            (w * 0.52 - r, h * 0.43 - r, w * 0.52 + r, h * 0.43 + r), fill=(255, 218, 166, 255)
        )
        for level, amp, color in [
            (0.53, 0.055, (113, 87, 110, 255)),
            (0.66, 0.07, (77, 73, 94, 255)),
            (0.79, 0.05, (47, 56, 74, 255)),
            (0.94, 0.045, (24, 40, 55, 255)),
        ]:
            _ridge(d, size, level, amp, color, seed + int(level * 100))
        for i in range(3):
            xx, yy = w * (0.18 + i * 0.07), h * (0.27 + i * 0.017)
            d.arc(
                (xx, yy, xx + w * 0.025, yy + h * 0.01),
                180,
                340,
                fill=(56, 44, 67, 255),
                width=max(1, w // 400),
            )
    elif theme == "forest":
        _glow(image, (0.60, 0.25), 0.47, (195, 211, 158), 100)
        d = ImageDraw.Draw(image)
        for layer in range(3):
            level = 0.61 + layer * 0.15
            color = [(45, 79, 71, 255), (25, 57, 52, 255), (13, 38, 38, 255)][layer]
            _ridge(d, size, level, 0.04, color, seed + layer)
            for i in range(9 - layer):
                x = (i / (8 - layer) + rng.uniform(-0.04, 0.04)) * w
                # Border trees frame the caption.
                height = (
                    h * rng.uniform(0.10, 0.25)
                    if 0.27 < x / w < 0.73
                    else h * rng.uniform(0.20, 0.42)
                )
                _tree(d, x, h * (level + 0.11), height, color)
        for _ in range(23):
            x, y = rng.uniform(0.05, 0.95) * w, rng.uniform(0.47, 0.92) * h
            d.ellipse((x, y, x + w * 0.003, y + w * 0.003), fill=(219, 212, 138, 160))
    elif theme in {"city", "rain"}:
        _glow(image, (0.65, 0.32), 0.45, (158, 141, 151), 60)
        d = ImageDraw.Draw(image)
        for layer in range(2):
            x = -w * 0.02
            ground = h * (0.73 + layer * 0.14)
            while x < w:
                bw = rng.uniform(0.06, 0.14) * w
                bh = rng.uniform(0.07, 0.30) * h
                color = (35, 48, 64, 255) if layer == 0 else (17, 32, 46, 255)
                d.rectangle((x, ground - bh, x + bw, ground), fill=color)
                for wx in np.arange(x + bw * 0.2, x + bw * 0.9, max(3, bw * 0.28)):
                    for wy in np.arange(
                        ground - bh + h * 0.016, ground - h * 0.01, max(5, h * 0.018)
                    ):
                        if rng.random() < 0.28:
                            d.rectangle(
                                (wx, wy, wx + w * 0.006, wy + h * 0.004),
                                fill=(220, 177, 107, rng.randint(80, 180)),
                            )
                x += bw + w * 0.012
        d.rectangle((0, h * 0.87, w, h), fill=(12, 28, 39, 255))
        for _ in range(35):
            x, y = rng.random() * w, rng.uniform(0.88, 1) * h
            d.line((x, y, x + w * 0.06, y), fill=(163, 139, 104, 55), width=max(1, int(h * 0.002)))
        if theme == "rain":
            rain = Image.new("RGBA", size)
            rd = ImageDraw.Draw(rain)
            for _ in range(270):
                x, y = rng.uniform(-0.1, 1.1) * w, rng.random() * h
                length = rng.uniform(0.012, 0.045) * h
                rd.line(
                    (x, y, x - length * 0.15, y + length),
                    fill=(192, 218, 229, rng.randint(12, 60)),
                    width=max(1, w // 600),
                )
            image.alpha_composite(rain)
    elif theme == "love":
        _glow(image, (0.70, 0.25), 0.40, (255, 192, 166), 100)
        _moon(image, (0.72, 0.23), 0.07)
        d = ImageDraw.Draw(image)
        _ridge(d, size, 0.73, 0.05, (76, 48, 77, 255), seed)
        _ridge(d, size, 0.87, 0.045, (39, 34, 58, 255), seed + 1)
        # Paired trees, shared horizon: a subtle visual metaphor for companionship.
        _tree(d, w * 0.17, h * 0.91, h * 0.23, (29, 29, 47, 255))
        _tree(d, w * 0.28, h * 0.91, h * 0.19, (29, 29, 47, 255))
        for _ in range(18):
            x, y = rng.random() * w, rng.uniform(0.12, 0.78) * h
            r = rng.uniform(0.004, 0.014) * w
            d.ellipse((x - r, y - r, x + r, y + r), fill=(244, 183, 170, 50))
    else:
        for center, radius, color in [
            ((0.8, 0.23), 0.44, (186, 147, 133)),
            ((0.1, 0.70), 0.5, (86, 119, 141)),
            ((0.6, 0.92), 0.35, (148, 113, 158)),
        ]:
            _glow(image, center, radius, color, 90)
        d = ImageDraw.Draw(image)
        for i in range(6):
            _ridge(
                d, size, 0.57 + i * 0.08, 0.035, (38 + i * 2, 43 + i * 2, 69 + i * 2, 100), seed + i
            )

    # A little film grain prevents broad gradients from banding in the MP4.
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    noise = np.random.default_rng(seed).normal(0, 1.1, (h, w, 1))
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    image = Image.fromarray(arr, "RGB")
    if style == "minimal":
        veil = Image.new("RGB", size, (17, 23, 34))
        image = Image.blend(image, veil, 0.36)
    return image


def _caption(
    size: tuple[int, int], text: str, title: str, author: str, index: int, count: int, style: str
) -> Image.Image:
    w, h = size
    image = Image.new("RGBA", size)
    # Soft central scrim leaves the illustration visible and guarantees contrast.
    y = np.linspace(0, 1, h)
    alpha = (
        28 + 95 * np.exp(-(((y - 0.56) / 0.28) ** 2)) + 40 * np.maximum(0, (y - 0.83) / 0.17)
    ).astype(np.uint8)
    scrim = np.zeros((h, w, 4), dtype=np.uint8)
    scrim[:, :, 0:3] = (5, 10, 19)
    scrim[:, :, 3] = alpha[:, None]
    image.alpha_composite(Image.fromarray(scrim, "RGBA"))
    d = ImageDraw.Draw(image)
    margin = w * 0.105
    title_font = _font(max(11, int(w * 0.031)), bold=True)
    title_lines = _wrap(title, title_font, w * 0.73)[:2]
    title_y = h * 0.082
    for line in title_lines:
        d.text(
            (w / 2, title_y),
            line,
            font=title_font,
            fill=(230, 226, 214, 205),
            anchor="mt",
            stroke_width=0,
        )
        title_y += title_font.size * 1.5
    # Reduce the font until every line fits, including poems without whitespace.
    font_size = int(w * (0.067 if style == "minimal" else 0.064))
    min_size = max(12, int(w * 0.034))
    while True:
        font = _font(font_size)
        lines = _wrap(text, font, w - margin * 2)
        bboxes = [font.getbbox(line or " ") for line in lines]
        ink_heights = [max(font_size * 0.8, b[3] - b[1]) for b in bboxes]
        gap = font_size * 0.48
        total = sum(ink_heights) + gap * max(0, len(lines) - 1)
        if total <= h * 0.56 or font_size <= min_size:
            break
        font_size -= 1
    # Anchor by real ink bounds so Tamil vowel signs are not clipped or crowded.
    if total > h * 0.56 and len(lines) > 1:
        gap = max(font_size * 0.15, (h * 0.56 - sum(ink_heights)) / (len(lines) - 1))
    total = sum(ink_heights) + gap * max(0, len(lines) - 1)
    if total > h * 0.57:
        raise ValueError(
            "This scene has too much text to read comfortably. Split it into two scenes and export again."
        )
    y0 = h * 0.54 - total / 2
    accent = (222, 190, 135, 235)
    d.line(
        (w * 0.455, y0 - h * 0.035, w * 0.545, y0 - h * 0.035), fill=accent, width=max(1, w // 250)
    )
    cursor = y0
    for line, bbox, ink_height in zip(lines, bboxes, ink_heights):
        d.text(
            (w / 2, cursor - bbox[1]),
            line,
            font=font,
            fill=(255, 249, 234, 255),
            anchor="ma",
            stroke_width=max(1, w // 500),
            stroke_fill=(0, 0, 0, 90),
        )
        cursor += ink_height + gap
    footer_font = _font(max(10, int(w * 0.028)))
    if author:
        footer_lines = _wrap(author, footer_font, w * 0.72)[:2]
        fy = h * 0.84
        for line in footer_lines:
            d.text((w / 2, fy), line, font=footer_font, fill=(235, 224, 207, 200), anchor="mt")
            fy += footer_font.size * 1.45
    # Quiet page markers, clear of common social video button overlays.
    dot_r = max(1.3, w * 0.004)
    spacing = w * 0.025
    start = w / 2 - (count - 1) * spacing / 2
    for i in range(count):
        x = start + i * spacing
        d.ellipse(
            (x - dot_r, h * 0.935 - dot_r, x + dot_r, h * 0.935 + dot_r),
            fill=accent if i == index else (240, 235, 224, 65),
        )
    return image


def _run(args: list[str], *, timeout: int = 120) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Video rendering timed out. Try a shorter poem or draft quality."
        ) from exc
    if result.returncode:
        raise RuntimeError("Video encoding failed: " + result.stderr[-1800:])


def _audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("The narration audio could not be read.") from exc


def _ambient(path: Path, duration: float, mood: str, enabled: bool) -> None:
    rate = 32000
    # Chords written and synthesized here; no licensed recordings or remote downloads.
    progressions = {
        "reflective": [
            (146.83, 174.61, 220.00),
            (130.81, 164.81, 196.00),
            (116.54, 146.83, 174.61),
            (130.81, 164.81, 220.00),
        ],
        "romantic": [
            (130.81, 164.81, 196.00, 246.94),
            (110, 164.81, 220),
            (146.83, 174.61, 220),
            (98, 146.83, 196),
        ],
        "hopeful": [
            (130.81, 164.81, 196),
            (98, 146.83, 196),
            (110, 130.81, 164.81),
            (87.31, 130.81, 174.61),
        ],
        "melancholic": [
            (110, 130.81, 164.81),
            (87.31, 110, 130.81),
            (98, 123.47, 146.83),
            (82.41, 110, 164.81),
        ],
    }
    chords = progressions.get(mood, progressions["reflective"])
    n = math.ceil(duration * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        # Stream a second at a time so longer clips do not consume excessive memory.
        for start in range(0, n, rate):
            t = np.arange(start, min(start + rate, n), dtype=np.float64) / rate
            audio = np.zeros((len(t), 2), dtype=np.float64)
            if enabled:
                # Only synthesize chords audible during this block. Adjacent eight-second
                # chords overlap with smooth raised cosine envelopes.
                for chord_index in range(
                    math.floor((t[0] - 10) / 8) + 1, math.floor(t[-1] / 8) + 1
                ):
                    chord = chords[chord_index % len(chords)]
                    local = t - chord_index * 8
                    env = np.where(
                        (local >= 0) & (local < 10),
                        np.sin(np.pi * np.clip(local / 10, 0, 1)) ** 2,
                        0,
                    )
                    for ni, freq in enumerate(chord):
                        tone = (
                            np.sin(2 * np.pi * freq * t + ni * 0.4)
                            + 0.22 * np.sin(2 * np.pi * freq * 2 * t)
                        ) * 0.035
                        audio[:, 0] += tone * env * (0.8 + 0.2 * math.sin(ni))
                        audio[:, 1] += tone * env * (0.8 + 0.2 * math.cos(ni))
                fade = np.minimum(1, t / 1.2) * np.minimum(1, np.maximum(0, duration - t) / 1.8)
                audio *= fade[:, None]
            wav.writeframes((np.clip(audio, -0.8, 0.8) * 32767).astype("<i2").tobytes())


def render_video(
    request: dict,
    output_dir: Path,
    assets_dir: Path,
    progress: Callable[[int, str], None],
    narration_path: Path | None = None,
) -> dict:
    """Render an export from validated scenes. All generated media stays in output_dir."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required to render video.")
    scenes = request.get("scenes") or []
    if not 1 <= len(scenes) <= 24:
        raise ValueError("A video needs between 1 and 24 scenes.")
    output_dir = Path(output_dir)
    assets_dir = Path(assets_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / "frames"
    work.mkdir(exist_ok=True)
    size = (720, 1280) if request.get("quality") == "hd" else (360, 640)
    w, h = size
    style = request.get("style", "cinematic")
    mood = request.get("mood", "reflective")
    durations = [max(2.5, min(15, float(s.get("duration", 5)))) for s in scenes]
    if narration_path:
        narration_length = _audio_duration(narration_path) + 0.5
        if narration_length > sum(durations):
            factor = narration_length / sum(durations)
            durations = [d * factor for d in durations]
    durations = [math.ceil(d * FPS) / FPS for d in durations]
    total = sum(durations)
    if total > 360:
        raise ValueError("Video duration must be six minutes or less.")
    progress(5, "Composing original scenery")
    segments = []
    for i, (scene, duration) in enumerate(zip(scenes, durations)):
        text = str(scene.get("text", ""))
        theme = scene.get("theme", "abstract")
        theme = theme if theme in THEMES else "abstract"
        background_size = (int(w * 1.12) // 2 * 2, int(h * 1.12) // 2 * 2)
        image_id = str(scene.get("image_id") or "")
        image_path = assets_dir / f"{image_id}.png"
        if image_id and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", image_id) and image_path.is_file():
            with Image.open(image_path) as uploaded:
                background = ImageOps.fit(
                    ImageOps.exif_transpose(uploaded).convert("RGB"),
                    background_size,
                    method=Image.Resampling.LANCZOS,
                )
        else:
            background = _scenery(theme, style, mood, background_size, text + str(i))
        overlay = _caption(
            size,
            text,
            str(request.get("title") or ""),
            str(request.get("author") or ""),
            i,
            len(scenes),
            style,
        )
        bg_path = work / f"background-{i:03d}.png"
        overlay_path = work / f"caption-{i:03d}.png"
        background.save(bg_path)
        overlay.save(overlay_path)
        if i == 0:
            poster = ImageOps.fit(background, size, method=Image.Resampling.LANCZOS).convert("RGBA")
            poster.alpha_composite(overlay)
            poster.convert("RGB").save(output_dir / "poster.jpg", quality=92)
        segment = work / f"scene-{i:03d}.mp4"
        frames = round(duration * FPS)
        # Render captions after camera motion so Tamil remains stable and sharp.
        zoom = f"min(1.055,1+on/{max(1, frames - 1)}*0.055)"
        filt = (
            f"[0:v]zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={w}x{h}:fps={FPS},format=rgba[bg];"
            f"[bg][1:v]overlay=0:0,fade=t=in:st=0:d=0.35,fade=t=out:st={max(0, duration - 0.35):.3f}:d=0.35,format=yuv420p[v]"
        )
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-threads",
                "2",
                "-filter_complex_threads",
                "1",
                "-i",
                str(bg_path),
                "-loop",
                "1",
                "-i",
                str(overlay_path),
                "-filter_complex",
                filt,
                "-map",
                "[v]",
                "-t",
                f"{duration:.5f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast" if w == 360 else "veryfast",
                "-crf",
                "21",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                str(segment),
            ],
            timeout=max(90, int(duration * 8)),
        )
        segments.append(segment)
        progress(10 + int((i + 1) / len(scenes) * 67), f"Rendering scene {i + 1} of {len(scenes)}")
    concat_path = work / "scenes.txt"
    concat_path.write_text("".join(f"file '{s.name}'\n" for s in segments))
    progress(80, "Creating the soundtrack")
    ambient_path = work / "ambient.wav"
    _ambient(ambient_path, total, mood, bool(request.get("music", True)))
    video_path = output_dir / "video.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "2",
        "-filter_complex_threads",
        "1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(ambient_path),
    ]
    if narration_path:
        command += [
            "-i",
            str(narration_path),
            "-filter_complex",
            "[1:a]volume=0.35[m];[2:a]apad,volume=1.15[n];[m][n]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
        ]
    else:
        command += ["-map", "0:v:0", "-map", "1:a:0"]
    command += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        f"{total:.5f}",
        "-movflags",
        "+faststart",
        str(video_path),
    ]
    progress(90, "Finishing your video")
    _run(command, timeout=max(120, int(total * 2)))
    # Keep final outputs and narration only; intermediate frames are disposable.
    shutil.rmtree(work)
    progress(100, "Your film is ready")
    return {
        "video_path": str(video_path),
        "poster_path": str(output_dir / "poster.jpg"),
        "duration": round(total, 3),
        "width": w,
        "height": h,
    }
