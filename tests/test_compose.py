"""Tests for the diptych composer."""

import pytest
from conftest import write_image
from PIL import Image, ImageChops

from tv_photobook.compose import CANVAS, ComposeError, Layout, compose


def test_output_is_a_canvas_sized_jpeg(tmp_path):
    members = [write_image(tmp_path / "a.jpg"), write_image(tmp_path / "b.jpg")]
    dest = tmp_path / "out.jpg"

    compose(members, dest, Layout())

    with Image.open(dest) as img:
        assert img.size == CANVAS
        assert img.format == "JPEG"


def test_set_is_horizontally_centered(tmp_path):
    # Mixed widths must leave equal mat left and right: the packed set is
    # centered, not drifting toward the wider photo.
    landscape = write_image(tmp_path / "a.jpg", size=(600, 400), color=(0, 0, 220))
    portrait = write_image(tmp_path / "b.jpg", size=(400, 600), color=(220, 0, 0))
    dest = tmp_path / "out.jpg"

    compose([landscape, portrait], dest, Layout(color="#ffffff"))

    img = Image.open(dest).convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    left, _, right, _ = ImageChops.difference(img, bg).getbbox()
    assert abs(left - (img.size[0] - right)) <= 4


def test_a_landscape_stays_shorter_than_a_portrait(tmp_path):
    # Equal-cell sizing (not equal height): a landscape is width-limited and so
    # ends up shorter than a portrait, each keeping its own aspect ratio.
    landscape = write_image(tmp_path / "a.jpg", size=(600, 400), color=(0, 0, 220))
    portrait = write_image(tmp_path / "b.jpg", size=(400, 600), color=(220, 0, 0))
    dest = tmp_path / "out.jpg"

    compose([landscape, portrait], dest, Layout(color="#ffffff"))

    img = Image.open(dest).convert("RGB")
    is_blue = lambda c: c[2] > 150 and c[0] < 100 and c[1] < 100
    is_red = lambda c: c[0] > 150 and c[1] < 100 and c[2] < 100
    blue_h = [y for y in range(img.size[1]) if is_blue(img.getpixel((img.size[0] // 4, y)))]
    red_h = [y for y in range(img.size[1]) if is_red(img.getpixel((3 * img.size[0] // 4, y)))]
    assert max(red_h) - min(red_h) > max(blue_h) - min(blue_h)


def test_each_photo_gets_a_shadow_border(tmp_path):
    # write_image's default fill is a distinct red, so the photo is easy to
    # tell apart from the white mat and the grey shadow halo.
    members = [
        write_image(tmp_path / "a.jpg", size=(400, 600)),
        write_image(tmp_path / "b.jpg", size=(400, 600)),
    ]
    dest = tmp_path / "out.jpg"

    compose(members, dest, Layout(color="#ffffff"))

    img = Image.open(dest).convert("RGB")
    mid = img.size[1] // 2
    is_photo = lambda c: c[0] > c[1] + 40 and c[0] > c[2] + 40
    left_edge = next(x for x in range(img.size[0]) if is_photo(img.getpixel((x, mid))))

    corner = img.getpixel((5, 5))  # far mat: untouched white
    halo = img.getpixel((left_edge - 12, mid))  # mat just left of the photo
    assert sum(corner) == 255 * 3
    assert not is_photo(halo)  # we sampled mat, not the photo
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
