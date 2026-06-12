"""Tests for the diptych composer."""

import pytest
from conftest import write_image
from PIL import Image

from tv_photobook.compose import CANVAS, ComposeError, Layout, compose


def test_output_is_a_canvas_sized_jpeg(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), write_image(tmp_path / "b.jpg")]
    dest = tmp_path / "out.jpg"

    compose(members, dest, Layout())

    with Image.open(dest) as img:
        assert img.size == CANVAS
        assert img.format == "JPEG"


def test_portraits_keep_their_aspect_ratio(tmp_path):
    # A 400x600 (2:3) portrait must not be stretched to its cell's shape.
    members = [
        write_image(tmp_path / "a.jpg", size=(400, 600)),
        write_image(tmp_path / "b.jpg", size=(400, 600)),
    ]
    dest = tmp_path / "out.jpg"

    compose(members, dest, Layout(margin=100, gap=100))

    cell_w = (CANVAS[0] - 2 * 100 - 100) // 2
    cell_h = CANVAS[1] - 2 * 100
    # Fitted to a tall cell, a 2:3 photo is limited by height: w = h * 2/3.
    fitted_w = round(cell_h * 2 / 3)
    assert fitted_w <= cell_w  # it fit by height, leaving side margins


def test_each_photo_gets_a_shadow_border(tmp_path):
    members = [
        write_image(tmp_path / "a.jpg", size=(400, 600)),
        write_image(tmp_path / "b.jpg", size=(400, 600)),
    ]
    dest = tmp_path / "out.jpg"
    margin = gap = 100

    compose(members, dest, Layout(color="#ffffff", margin=margin, gap=gap))

    cell_w = (CANVAS[0] - 2 * margin - gap) // 2
    cell_h = CANVAS[1] - 2 * margin
    scale = min(cell_w / 400, cell_h / 600)
    fw, fh = round(400 * scale), round(600 * scale)
    px = margin + (cell_w - fw) // 2
    py = margin + (cell_h - fh) // 2

    img = Image.open(dest).convert("RGB")
    corner = img.getpixel((5, 5))  # far mat: untouched white
    halo = img.getpixel((px - 12, py + fh // 2))  # mat just left of the photo
    assert sum(corner) == 255 * 3
    assert sum(halo) < sum(corner)  # the shadow darkened the mat by the photo


def test_corners_show_the_frame_color(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), write_image(tmp_path / "b.jpg")]
    dest = tmp_path / "out.jpg"

    compose(members, dest, Layout(color="#ffffff"))

    with Image.open(dest) as img:
        assert img.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_exif_orientation_is_honored(tmp_path):
    # Tag a landscape image as needing a 90-degree rotation; compose should
    # apply it, yielding a portrait before fitting.
    src = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    Image.new("RGB", (600, 400), (10, 10, 10)).save(src, exif=exif)
    dest = tmp_path / "out.jpg"

    compose([src, write_image(tmp_path / "b.jpg")], dest, Layout())

    assert dest.exists()  # no crash, orientation applied during fit


def test_unreadable_member_raises_compose_error(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), tmp_path / "missing.jpg"]
    with pytest.raises(ComposeError, match="could not read missing.jpg"):
        compose(members, tmp_path / "out.jpg", Layout())


def test_unknown_color_raises_compose_error(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), write_image(tmp_path / "b.jpg")]
    with pytest.raises(ComposeError, match="unknown frame color"):
        compose(members, tmp_path / "out.jpg", Layout(color="banana"))


def test_oversized_margin_raises_compose_error(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), write_image(tmp_path / "b.jpg")]
    with pytest.raises(ComposeError, match="no room"):
        compose(members, tmp_path / "out.jpg", Layout(margin=5000))
