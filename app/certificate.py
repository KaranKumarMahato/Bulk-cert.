"""Certificate rendering: a single predefined template drawn with Pillow.

Design notes
------------
* One fixed template (A4-landscape-style layout) -- no template editor, per
  the requirements.
* Fonts are resolved from well-known system font locations with a graceful
  fallback chain, so the code runs on Windows/Linux/macOS and in CI without
  bundling font files.
* Rendered text metadata is embedded as PNG ``tEXt`` chunks (recipient name,
  job id, ...) which makes the generated files self-describing and easy to
  verify in tests without OCR.
* Files are written atomically (tmp file + ``os.replace``) so a crash never
  leaves a half-written certificate on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

CANVAS_SIZE = (1600, 1131)  # A4 landscape proportions at ~140 dpi

PAPER = (253, 251, 245)
INK = (26, 35, 66)      # deep navy
GOLD = (176, 141, 42)   # muted gold accent
MUTED = (96, 102, 118)  # secondary text

# ---------------------------------------------------------------------------
# Font handling
# ---------------------------------------------------------------------------

_FONT_KINDS: dict[str, list[str]] = {
    "heading": ["georgiab.ttf", "georgia.ttf", "timesbd.ttf", "times.ttf", "arialbd.ttf", "arial.ttf"],
    "serif": ["georgia.ttf", "times.ttf", "arial.ttf", "segoeui.ttf"],
    "sans": ["segoeui.ttf", "arial.ttf", "verdana.ttf", "tahoma.ttf", "calibri.ttf"],
    "script": ["segoesc.ttf", "segoepr.ttf", "comicbd.ttf", "comic.ttf", "georgia.ttf"],
}


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if windows_fonts.is_dir():
        dirs.append(windows_fonts)
    dirs.append(Path("/usr/share/fonts/truetype/dejavu"))
    dirs.append(Path("/usr/share/fonts/truetype/liberation"))
    dirs.append(Path("/System/Library/Fonts"))
    dirs.append(Path("/Library/Fonts"))
    try:  # matplotlib ships DejaVuSans -- a portable fallback when installed
        import matplotlib

        mpl_fonts = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
        if mpl_fonts.is_dir():
            dirs.append(mpl_fonts)
    except Exception:  # pragma: no cover - matplotlib is optional
        pass
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=128)
def _load_font(
    filename: str, size: int
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont | None:
    """Try to load *filename* at *size*; return ``None`` if unavailable."""
    for directory in _font_dirs():
        path = directory / filename
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    try:
        return ImageFont.truetype(filename, size=size)  # let Pillow search its paths
    except OSError:
        return None


def get_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return a font of the requested *kind* at *size* pixels.

    Walks the candidate list until one resolves, then falls back to Pillow's
    bundled default font so rendering never fails outright.
    """
    for candidate in _FONT_KINDS[kind]:
        font = _load_font(candidate, size)
        if font is not None:
            return font
    # Pillow >= 10.1 returns a scalable default font when a size is given.
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _centered(draw: ImageDraw.ImageDraw, y: float, text: str, font, fill, canvas_w: int) -> float:
    width = draw.textlength(text, font=font)
    draw.text(((canvas_w - width) / 2, y), text, font=font, fill=fill)
    return width


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[float, float]:
    """Return (width, height) of *text* when drawn with *font*."""
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: float, kind: str,
              start_size: int, min_size: int = 20):
    size = start_size
    font = get_font(kind, size)
    while size > min_size and draw.textlength(text, font=font) > max_width:
        size = max(size - 4, min_size)
        font = get_font(kind, size)
    return font


def _draw_spaced(draw: ImageDraw.ImageDraw, center_x: float, y: float, text: str,
                 font, fill, spacing: float = 6.0) -> None:
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = center_x - total / 2
    for ch, width in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += width + spacing


def _draw_borders(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    draw.rectangle([40, 40, w - 40, h - 40], outline=GOLD, width=6)
    draw.rectangle([58, 58, w - 58, h - 58], outline=INK, width=2)
    for cx, cy in ((40, 40), (w - 40, 40), (40, h - 40), (w - 40, h - 40)):
        r = 17
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=GOLD)
        r2 = 7
        draw.polygon(
            [(cx, cy - r2), (cx + r2, cy), (cx, cy + r2), (cx - r2, cy)], fill=PAPER
        )


def _draw_divider(draw: ImageDraw.ImageDraw, y: float, canvas_w: int) -> None:
    half = 210
    center = canvas_w // 2
    draw.line([(center - half, y), (center - 46, y)], fill=GOLD, width=3)
    draw.line([(center + 46, y), (center + half, y)], fill=GOLD, width=3)
    r = 14
    draw.polygon([(center, y - r), (center + r, y), (center, y + r), (center - r, y)], fill=GOLD)


def _draw_signature_block(draw: ImageDraw.ImageDraw, content: "CertificateContent",
                          h: int, canvas_w: int) -> None:
    """Draw the signature line (left) and date line (right) near the bottom."""
    y_line = h - 175
    line_half = 190
    left_x, right_x = canvas_w // 2 - 400, canvas_w // 2 + 400

    # Left block: issued_by sits above the line (like a signature),
    # the organisation is printed below it.
    issued_font = _fit_font(draw, content.issued_by, line_half * 2 - 20, "script", 42, 24)
    _, issued_h = _text_size(draw, content.issued_by, issued_font)
    _centered_on(draw, left_x, y_line - issued_h - 12, content.issued_by, issued_font, INK)
    draw.line([(left_x - line_half, y_line), (left_x + line_half, y_line)], fill=INK, width=2)
    _centered_on(draw, left_x, y_line + 16, content.organization, get_font("sans", 26), MUTED)

    # Right block: date above the line, label below it.
    date_label = content.completion_date.strftime("%d %B %Y")
    date_font = _fit_font(draw, date_label, line_half * 2 - 20, "serif", 34, 22)
    _, date_h = _text_size(draw, date_label, date_font)
    _centered_on(draw, right_x, y_line - date_h - 12, date_label, date_font, INK)
    draw.line([(right_x - line_half, y_line), (right_x + line_half, y_line)], fill=INK, width=2)
    _centered_on(draw, right_x, y_line + 16, "Date of issue", get_font("sans", 26), MUTED)


def _centered_on(draw: ImageDraw.ImageDraw, center_x: float, y: float, text: str,
                 font, fill) -> float:
    width = draw.textlength(text, font=font)
    draw.text((center_x - width / 2, y), text, font=font, fill=fill)
    return width


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CertificateContent:
    """Everything needed to render one certificate."""

    recipient_name: str
    course: str
    completion_date: date
    heading: str
    organization: str
    issued_by: str
    notes: str | None
    metadata: dict[str, str]


def render_certificate(content: CertificateContent, output_path: Path) -> None:
    """Render *content* to *output_path* as a PNG (atomically)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = CANVAS_SIZE
    image = Image.new("RGB", CANVAS_SIZE, PAPER)
    draw = ImageDraw.Draw(image)

    _draw_borders(draw, w, h)

    # Heading
    heading_font = _fit_font(draw, content.heading, w - 300, "heading", 76, 36)
    _draw_spaced(draw, w / 2, 150, content.heading, heading_font, INK, spacing=8)
    _draw_divider(draw, 300, w)

    # Presentation lines
    _centered(draw, 360, "This certificate is proudly presented to",
              get_font("sans", 34), MUTED, w)

    name_font = _fit_font(draw, content.recipient_name, w - 320, "heading", 104, 44)
    name_w = _centered(draw, 430, content.recipient_name, name_font, INK, w)
    # flourish under the name
    y_name_bottom = 430 + (name_font.size if hasattr(name_font, "size") else 90) + 16
    draw.line([(w / 2 - name_w / 2 - 40, y_name_bottom),
               (w / 2 + name_w / 2 + 40, y_name_bottom)], fill=GOLD, width=3)

    _centered(draw, y_name_bottom + 40, "for successfully completing",
              get_font("sans", 32), MUTED, w)

    course_font = _fit_font(draw, content.course, w - 400, "serif", 54, 28)
    _centered(draw, y_name_bottom + 96, content.course, course_font, INK, w)

    if content.notes:
        note_font = _fit_font(draw, content.notes, w - 420, "sans", 26, 18)
        _centered(draw, y_name_bottom + 180, content.notes, note_font, MUTED, w)

    _draw_signature_block(draw, content, h, w)

    # Embed descriptive metadata as PNG text chunks (self-describing files).
    # Non-Latin-1 values fall back to iTXt (UTF-8) so any recipient name works.
    pnginfo = PngImagePlugin.PngInfo()
    for key, value in content.metadata.items():
        try:
            pnginfo.add_text(str(key), str(value))
        except UnicodeEncodeError:
            pnginfo.add_itxt(str(key), str(value))

    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        image.save(tmp_path, format="PNG", pnginfo=pnginfo)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
