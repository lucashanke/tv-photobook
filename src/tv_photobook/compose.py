"""Compose several photos onto one 16:9 canvas (a diptych and friends).

The Frame shows a single artwork at a time, so "two photos side by side" has to
be one image. Each photo is fitted into an equal-width cell (so a landscape
ends up shorter than a portrait, never stretched), then the photos are packed
together and the whole set is centered on the canvas, leaving equal mat on the
left and right. The gap between photos equals the tallest photo's distance to
the top/bottom border, so the spacing inside the set matches the spacing around
it. Each photo gets a soft shadow so it reads as recessed into the warm
off-white mat, like the TV's ``shadowbox`` matte does for single photos.

       gap = the tallest photo's top/bottom border
    |------|          v          |------|
   |------| +-------------+ |--| +------+ |------|
           |  landscape   |     | port |
           +-------------+      +------+
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

# 4K UHD, the Frame's native panel resolution.
CANVAS = (3840, 2160)

# A warm off-white mat behind composites; tune with --frame-color to taste.
DEFAULT_FRAME_COLOR = "#eee7d7"

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
    """The frame baked into a composite: border and fill color.

    The gap between photos is not configured here; it is derived per composite
    from the tallest photo's distance to the border (see ``compose``).
    """

    margin: int = 100
    color: str = DEFAULT_FRAME_COLOR

    def signature(self) -> str:
        """Stable string folded into a group's hash so a look change re-composes.

        The ``pc2`` tag versions the compositing itself, so changing the layout
        or shadow algorithm re-uploads existing groups even when margin and
        color are unchanged.
        """
        return f"pc2:{self.margin}:{self.color}"


def compose(members: list[Path], dest: Path, layout: Layout) -> None:
    """Fit each photo in an equal cell, center the packed set, save to dest."""
    width, height = CANVAS
    cols = len(members)
    cell_h = height - 2 * layout.margin
    if cell_h <= 0 or width - 2 * layout.margin <= 0:
        raise ComposeError(
            f"margin {layout.margin} leaves no room for {cols} photos on a "
            f"{width}x{height} canvas"
        )

    try:
        background = ImageColor.getrgb(layout.color)
    except ValueError as e:
        raise ComposeError(f"unknown frame color {layout.color!r}: {e}") from e

    photos = []
    for path in members:
        try:
            with Image.open(path) as raw:
                photos.append(ImageOps.exif_transpose(raw).convert("RGB"))
        except (OSError, UnidentifiedImageError) as e:
            raise ComposeError(f"could not read {path.name}: {e}") from e

    # Size once in gapless cells to find the tallest photo; its top/bottom mat
    # sets the gap between photos. Then re-size reserving that gap so the packed
    # set still fits within the side margins.
    cell_w0 = (width - 2 * layout.margin) // cols
    tallest = max(ImageOps.contain(p, (cell_w0, cell_h)).height for p in photos)
    gap = (height - tallest) // 2
    cell_w = (width - 2 * layout.margin - gap * (cols - 1)) // cols
    if cell_w <= 0:
        raise ComposeError(
            f"margin {layout.margin} leaves no room for {cols} photos on a "
            f"{width}x{height} canvas"
        )
    fitted = [ImageOps.contain(p, (cell_w, cell_h)) for p in photos]

    total_w = sum(p.width for p in fitted) + gap * (cols - 1)
    x = (width - total_w) // 2
    placements = []
    for photo in fitted:
        placements.append((photo, x, (height - photo.height) // 2))
        x += photo.width + gap

    canvas = Image.new("RGB", CANVAS, background)
    _cast_shadows(canvas, [(px, py, p.width, p.height) for p, px, py in placements])
    for photo, px, py in placements:
        canvas.paste(photo, (px, py))

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
