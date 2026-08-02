from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
PNG_DIR = ROOT / "png"
PNG_DIR.mkdir(parents=True, exist_ok=True)

FONT_PATH = "/System/Library/Fonts/SFNSRounded.ttf"
BLUE = "#4F83F1"
LIGHT_TEXT = "#171B20"
DARK_TEXT = "#F7F8F8"


def text_bbox(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int, int, int]:
    probe = Image.new("L", (10, 10), 0)
    draw = ImageDraw.Draw(probe)
    return draw.textbbox((0, 0), text, font=font)


def tracked_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, tracking: int) -> int:
    width = 0
    for index, char in enumerate(text):
      left, _, right, _ = draw.textbbox((0, 0), char, font=font)
      width += right - left
      if index < len(text) - 1:
          width += tracking
    return width


def draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    origin: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    tracking: int,
) -> list[tuple[float, float, float, float]]:
    x, y = origin
    boxes: list[tuple[float, float, float, float]] = []
    for char in text:
        box = draw.textbbox((x, y), char, font=font)
        draw.text((x, y), char, font=font, fill=fill)
        boxes.append(box)
        x = box[2] + tracking
    return boxes


def draw_tracked_mask(
    draw: ImageDraw.ImageDraw,
    origin: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    tracking: int,
) -> list[tuple[float, float, float, float]]:
    x, y = origin
    boxes: list[tuple[float, float, float, float]] = []
    for char in text:
        box = draw.textbbox((x, y), char, font=font)
        draw.text((x, y), char, font=font, fill=255)
        boxes.append(box)
        x = box[2] + tracking
    return boxes


def apply_clipped_overlay(
    image: Image.Image,
    alpha_mask: Image.Image,
    polygon: list[tuple[float, float]],
    fill: str,
) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.polygon(polygon, fill=fill)
    clipped = Image.new("RGBA", image.size, (0, 0, 0, 0))
    clipped.paste(overlay, mask=alpha_mask)
    image.alpha_composite(clipped)


def save_svg(
    filename: str,
    width: int,
    height: int,
    text: str,
    font_size: int,
    tracking: float,
    text_color: str,
    accent_polygon: list[tuple[float, float]],
    text_x: float,
    text_y: float,
) -> None:
    points = " ".join(f"{x:.2f},{y:.2f}" for x, y in accent_polygon)
    content = f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect width="{width}" height="{height}" fill="none"/>
  <defs>
    <clipPath id="logo-clip">
      <text x="{text_x:.2f}" y="{text_y:.2f}" font-family="'SF Pro Rounded','SF Compact Rounded','SF Pro Display','Helvetica Neue',Arial,sans-serif" font-size="{font_size}" font-weight="820" letter-spacing="{tracking}em">{escape(text)}</text>
    </clipPath>
  </defs>
  <text x="{text_x:.2f}" y="{text_y:.2f}" font-family="'SF Pro Rounded','SF Compact Rounded','SF Pro Display','Helvetica Neue',Arial,sans-serif" font-size="{font_size}" font-weight="820" letter-spacing="{tracking}em" fill="{text_color}">{escape(text)}</text>
  <g clip-path="url(#logo-clip)">
    <polygon points="{points}" fill="{BLUE}"/>
  </g>
</svg>
"""
    (ROOT / filename).write_text(content, encoding="utf-8")


def render_icon(filename: str, text_color: str, svg_name: str) -> None:
    size = 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, 284)
    tracking = -28
    text = "CW"

    width = tracked_width(draw, text, font, tracking)
    left, top, right, bottom = text_bbox(font, text)
    height = bottom - top
    origin = ((size - width) / 2, (size - height) / 2 - 20)

    alpha_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(alpha_mask)
    boxes = draw_tracked_mask(mask_draw, origin, text, font, tracking)
    draw_tracked_text(draw, origin, text, font, text_color, tracking)

    c_box, w_box = boxes
    accent_polygon = [
        (c_box[2] - 6, c_box[1] + 8),
        (w_box[0] + 34, w_box[1] + 8),
        (w_box[0] + 2, w_box[3] - 12),
        (w_box[0] - 4, w_box[3] - 12),
    ]
    apply_clipped_overlay(image, alpha_mask, accent_polygon, BLUE)
    image.save(PNG_DIR / filename)

    save_svg(
        svg_name,
        size,
        size,
        text,
        284,
        -0.092,
        text_color,
        accent_polygon,
        origin[0],
        origin[1] + font.size,
    )


def render_wordmark(filename: str, text_color: str, svg_name: str) -> None:
    width_px, height_px = 1600, 320
    image = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, 214)
    tracking = -15
    text = "CoWorker"

    width = tracked_width(draw, text, font, tracking)
    left, top, right, bottom = text_bbox(font, text)
    height = bottom - top
    origin = ((width_px - width) / 2, (height_px - height) / 2 - 16)

    alpha_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(alpha_mask)
    boxes = draw_tracked_mask(mask_draw, origin, text, font, tracking)
    draw_tracked_text(draw, origin, text, font, text_color, tracking)

    w_box = boxes[2]
    accent_polygon = [
        (w_box[0] + 34, w_box[1] + 14),
        (w_box[0] + 98, w_box[1] + 14),
        (w_box[0] + 66, w_box[3] - 8),
        (w_box[0] + 4, w_box[3] - 8),
    ]
    apply_clipped_overlay(image, alpha_mask, accent_polygon, BLUE)
    image.save(PNG_DIR / filename)

    save_svg(
        svg_name,
        width_px,
        height_px,
        text,
        214,
        -0.07,
        text_color,
        accent_polygon,
        origin[0],
        origin[1] + font.size,
    )


def main() -> None:
    render_icon("cw-icon-black.png", LIGHT_TEXT, "cw-icon-black.svg")
    render_icon("cw-icon-white.png", DARK_TEXT, "cw-icon-white.svg")
    render_wordmark("coworker-logo-black.png", LIGHT_TEXT, "coworker-logo-black.svg")
    render_wordmark("coworker-logo-white.png", DARK_TEXT, "coworker-logo-white.svg")


if __name__ == "__main__":
    main()
