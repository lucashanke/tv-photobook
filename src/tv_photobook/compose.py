"""Compose several photos onto one 16:9 canvas (a diptych and friends).

The Frame shows a single artwork at a time, so "two portraits side by side"
has to be one image. Each photo is fitted into its own cell preserving aspect
ratio, then given a soft shadow so it reads as recessed into the mat, the same
way the TV's ``shadowbox`` matte makes single photos look. The surrounding fill
matches the ``polar`` matte color so framed singles and diptychs look like one
set on the wall.

    margin                       gap                       margin
   |----| |--------------------| |--| |--------------------| |----|
          +--------------------+      +--------------------+
          |     photo 1        |      |     photo 2        |
          |  (fit + shadow)    |      |  (fit + shadow)    |
          +--------------------+      +--------------------+
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

# 4K UHD, the Frame's native panel resolution.
CANVAS = (3840, 2160)

# A warm off-white approximating the Frame's "polar" matte; tune with
# --frame-color if it does not match your panel exactly.
POLAR = "#f4f0e8"

# Shadowbox depth: a soft dark halo hugging each photo, weighted toward the top
# edge so the picture looks set down into the mat (light from above).
_SHADOW_BLUR = 22  # px of softness
_SHADOW_GROW = 5  # how far the halo spreads past the photo edge
_SHADOW_ALPHA = 130  # 0-255, the halo's darkest opacity
_SHADOW_DROP = 6  # downward bias, so the top edge reads as recessed


class ComposeError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    """The frame baked into a composite: border, inner gap, and fill color."""

    margin: int = 100
    gap: int = 100
    color: str = POLAR

    def signature(self) -> str:
        """Stable string folded into a group's hash so a look change re-composes.

        The ``sb1`` tag versions the compositing itself, so tweaking the shadow
        algorithm re-uploads existing groups even when the layout is unchanged.
        """
        return f"sb1:{self.margin}:{self.gap}:{self.color}"


def compose(members: list[Path], dest: Path, layout: Layout) -> None:
    """Lay the members out in a row on a CANVAS-sized background, save to dest."""
    width, height = CANVAS
    cols = len(members)
    cell_w = (width - 2 * layout.margin - layout.gap * (cols - 1)) // cols
    cell_h = height - 2 * layout.margin
    if cell_w <= 0 or cell_h <= 0:
        raise ComposeError(
            f"margin {layout.margin} and gap {layout.gap} leave no room for "
            f"{cols} photos on a {width}x{height} canvas"
        )

    try:
        background = ImageColor.getrgb(layout.color)
    except ValueError as e:
        raise ComposeError(f"unknown frame color {layout.color!r}: {e}") from e

    placements = []
    for i, path in enumerate(members):
        try:
            with Image.open(path) as raw:
                photo = ImageOps.exif_transpose(raw).convert("RGB")
        except (OSError, UnidentifiedImageError) as e:
            raise ComposeError(f"could not read {path.name}: {e}") from e
        fitted = ImageOps.contain(photo, (cell_w, cell_h))
        x = layout.margin + i * (cell_w + layout.gap) + (cell_w - fitted.width) // 2
        y = layout.margin + (cell_h - fitted.height) // 2
        placements.append((fitted, x, y))

    canvas = Image.new("RGB", CANVAS, background)
    _cast_shadows(canvas, [(x, y, p.width, p.height) for p, x, y in placements])
    for fitted, x, y in placements:
        canvas.paste(fitted, (x, y))

    try:
        canvas.save(dest, "JPEG", quality=92)
    except OSError as e:
        raise ComposeError(f"could not write the composite to {dest}: {e}") from e


def _cast_shadows(canvas: Image.Image, rects: list[tuple[int, int, int, int]]) -> None:
    """Darken a soft halo around each photo rect to fake the shadowbox recess."""
    mask = Image.new("L", canvas.size, 0)
    draw = ImageDraw.Draw(mask)
    for x, y, w, h in rects:
        draw.rectangle(
            [
                x - _SHADOW_GROW,
                y - _SHADOW_GROW + _SHADOW_DROP,
                x + w + _SHADOW_GROW,
                y + h + _SHADOW_GROW + _SHADOW_DROP,
            ],
            fill=_SHADOW_ALPHA,
        )
    mask = mask.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR))
    shadow = Image.new("RGB", canvas.size, (0, 0, 0))
    canvas.paste(shadow, (0, 0), mask)
