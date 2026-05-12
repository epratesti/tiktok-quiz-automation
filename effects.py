from __future__ import annotations

import math
import random
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import settings


THEMES = {
    "dark_neon": {
        "bg_top": (8, 10, 24),
        "bg_bottom": (18, 5, 38),
        "primary": (0, 255, 224),
        "secondary": (255, 42, 170),
        "accent": (255, 230, 64),
        "text": (255, 255, 255),
    },
    "neon_grid": {
        "bg_top": (4, 12, 18),
        "bg_bottom": (22, 7, 34),
        "primary": (50, 220, 255),
        "secondary": (255, 67, 98),
        "accent": (158, 255, 82),
        "text": (255, 255, 255),
    },
    "minimal_glow": {
        "bg_top": (10, 10, 12),
        "bg_bottom": (24, 20, 38),
        "primary": (255, 214, 80),
        "secondary": (60, 190, 255),
        "accent": (255, 82, 138),
        "text": (255, 255, 255),
    },
}


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    for font_path in settings.paths.fonts.glob("*.ttf"):
        candidates.append(font_path)
    windows_font = Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf")
    candidates.append(windows_font)
    candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            if draw.textbbox((0, 0), word, font=font)[2] > max_width:
                lines.extend(textwrap.wrap(word, width=max(5, len(word) // 2)))
                current = ""
            else:
                current = word
    if current:
        lines.append(current)
    return lines


def text_width(text: str, font: ImageFont.ImageFont) -> int:
    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def fit_font(text: str, max_width: int, start_size: int, min_size: int = 30) -> ImageFont.ImageFont:
    size = start_size
    while size > min_size:
        font = load_font(size)
        if all(text_width(line, font) <= max_width for line in wrap_text(text, font, max_width)):
            return font
        size -= 2
    return load_font(min_size)


def text_panel(
    text: str,
    width: int,
    font_size: int,
    theme_name: str,
    stroke_width: int = 4,
    align: str = "center",
    highlight: str = "",
    padding: int = 36,
) -> Image.Image:
    theme = THEMES[theme_name]
    font = load_font(font_size)
    small_font = load_font(max(28, int(font_size * 0.72)))
    max_width = width - padding * 2
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        lines.extend(wrap_text(paragraph, font, max_width) or [""])
    draw_probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    line_fonts = [font if len(line) < 28 else small_font for line in lines]
    line_heights = [draw_probe.textbbox((0, 0), line, font=line_font)[3] for line, line_font in zip(lines, line_fonts, strict=False)]
    height = max(120, sum(line_heights) + padding * 2 + len(lines) * 12)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    radius = min(42, padding)
    glow_draw.rounded_rectangle(
        (8, 8, width - 8, height - 8),
        radius=radius,
        fill=(*theme["primary"], 35),
        outline=(*theme["primary"], 110),
        width=3,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    image.alpha_composite(glow)

    panel_draw = ImageDraw.Draw(image)
    panel_draw.rounded_rectangle(
        (12, 12, width - 12, height - 12),
        radius=radius,
        fill=(8, 10, 18, 182),
        outline=(*theme["secondary"], 170),
        width=3,
    )

    y = padding
    for line, line_height, line_font in zip(lines, line_heights, line_fonts, strict=False):
        bbox = panel_draw.textbbox((0, 0), line, font=line_font)
        x = padding if align == "left" else (width - (bbox[2] - bbox[0])) // 2
        fill = theme["accent"] if highlight and highlight.upper() in line.upper() else theme["text"]
        panel_draw.text(
            (x, y),
            line,
            font=line_font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 220),
        )
        y += line_height + 16
    return image


def option_panel(label: str, text: str, width: int, theme_name: str, selected: bool = False) -> Image.Image:
    theme = THEMES[theme_name]
    height = 150
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (12, 15, 30, 210)
    outline = theme["accent"] if selected else theme["primary"]
    draw.rounded_rectangle((8, 8, width - 8, height - 8), radius=22, fill=fill, outline=(*outline, 210), width=4)

    bubble_box = (34, 34, 112, 112)
    bubble_center = ((bubble_box[0] + bubble_box[2]) // 2, (bubble_box[1] + bubble_box[3]) // 2)
    label_font = load_font(48)
    draw.ellipse(bubble_box, fill=(*outline, 235))
    draw.text(
        bubble_center,
        label,
        font=label_font,
        fill=(5, 8, 18),
        anchor="mm",
        stroke_width=1,
        stroke_fill=(5, 8, 18),
    )

    text_area_width = width - 178
    text_font = fit_font(text, text_area_width, 48, 34)
    lines = wrap_text(text, text_font, text_area_width)[:2]
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    line_heights = [probe.textbbox((0, 0), line, font=text_font)[3] for line in lines]
    total_text_height = sum(line_heights) + max(0, len(lines) - 1) * 10
    y = (height - total_text_height) // 2 - 2
    for line, line_height in zip(lines, line_heights, strict=False):
        draw.text((142, y), line, font=text_font, fill=theme["text"], stroke_width=2, stroke_fill=(0, 0, 0, 200))
        y += line_height + 10
    return image


def thumbnail_image(question: str, answer: str, theme_name: str) -> Image.Image:
    width, height = settings.video.width, settings.video.height
    bg = background_frame(0.0, width, height, theme_name)
    image = Image.fromarray(bg).convert("RGBA")
    title = text_panel("QUIZ RELAMPAGO", width - 120, 82, theme_name, padding=34)
    question_panel = text_panel(question, width - 120, 68, theme_name, padding=36)
    answer_panel = text_panel(f"Resposta: {answer}", width - 160, 56, theme_name, padding=28)
    image.alpha_composite(title, (60, 220))
    image.alpha_composite(question_panel, (60, 650))
    image.alpha_composite(answer_panel, (80, 1370))
    return image.convert("RGB")


def background_frame(t: float, width: int, height: int, theme_name: str) -> np.ndarray:
    theme = THEMES[theme_name]
    top = np.array(theme["bg_top"], dtype=np.float32)
    bottom = np.array(theme["bg_bottom"], dtype=np.float32)
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    base = (top * (1 - y) + bottom * y).astype(np.uint8)
    frame = np.repeat(base[:, None, :], width, axis=1)

    wave = (np.sin(np.linspace(0, 10, width) + t * 1.8) * 18).astype(np.int16)
    for offset, color in [(520, theme["primary"]), (980, theme["secondary"]), (1450, theme["accent"])]:
        yy = np.clip(offset + wave + int(math.sin(t + offset) * 18), 0, height - 1)
        for x, y_value in enumerate(yy):
            frame[max(0, y_value - 2) : min(height, y_value + 3), x, :] = color

    grid_y = int((t * 80) % 120)
    for line_y in range(grid_y, height, 120):
        frame[line_y : line_y + 2, :, :] = np.maximum(frame[line_y : line_y + 2, :, :], 44)
    for line_x in range(0, width, 135):
        frame[:, line_x : line_x + 2, :] = np.maximum(frame[:, line_x : line_x + 2, :], 34)

    rng = random.Random(42)
    for _ in range(70):
        px = int((rng.random() * width + t * rng.uniform(8, 32)) % width)
        py = int((rng.random() * height + t * rng.uniform(12, 42)) % height)
        color = theme["primary"] if rng.random() > 0.5 else theme["secondary"]
        frame[max(0, py - 2) : min(height, py + 3), max(0, px - 2) : min(width, px + 3), :] = color
    return frame


def timer_frame(t: float, total: float, theme_name: str) -> np.ndarray:
    width, height = 142, 720
    theme = THEMES[theme_name]
    remaining = max(0.0, total - t)
    progress = remaining / total if total else 0
    image = Image.new("RGBA", (width, height), (*theme["bg_bottom"], 205))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, width - 4, height - 4), radius=34, fill=(5, 8, 18, 235), outline=(*theme["primary"], 215), width=4)
    draw.rounded_rectangle((46, 36, 96, height - 130), radius=25, fill=(2, 5, 14, 245), outline=(*theme["primary"], 210), width=4)
    bar_h = int((height - 180) * progress)
    draw.rounded_rectangle((55, height - 139 - bar_h, 87, height - 139), radius=18, fill=(*theme["accent"], 245))
    font = load_font(62)
    number = str(int(math.ceil(remaining)))
    draw.text((width // 2, height - 70), number, font=font, fill=theme["text"], anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
    # Dynamic MoviePy clips are RGB in the deployed workflow, so the sidebar is drawn as a designed panel.
    return np.array(image.convert("RGB"))


def progress_frame(t: float, duration: float, width: int, theme_name: str) -> np.ndarray:
    height = 26
    theme = THEMES[theme_name]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 6, width - 40, 20), radius=7, fill=(255, 255, 255, 45))
    filled = int((width - 80) * min(max(t / duration, 0), 1))
    draw.rounded_rectangle((40, 6, 40 + filled, 20), radius=7, fill=(*theme["primary"], 230))
    # Keep the dynamic progress bar in RGB to avoid RGBA blit errors during composition.
    return np.array(image.convert("RGB"))
