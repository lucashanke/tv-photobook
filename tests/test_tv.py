"""Tests for the FrameTV translation layer over a stubbed samsungtvws client."""

import pytest
from samsungtvws import exceptions

from tv_photobook.tv import FrameTV, TVError


class StubArt:
    def __init__(
        self,
        frame_support=True,
        content=(),
        error=None,
        delete_confirmed=True,
    ):
        self.content = list(content)
        self.frame_support = frame_support
        self.error = error  # raised by every TV call when set
        self.delete_confirmed = delete_confirmed
        self.upload_calls = []

    def _maybe_raise(self):
        if self.error is not None:
            raise self.error

    def open(self):
        self._maybe_raise()

    def supported(self):
        self._maybe_raise()
        return self.frame_support

    def available(self, category=None):
        self._maybe_raise()
        return [{"content_id": cid} for cid in self.content]

    def upload(self, file, **kwargs):
        self._maybe_raise()
        self.upload_calls.append((file, kwargs))
        return "MY_F0042"

    def delete(self, content_id):
        self._maybe_raise()
        return self.delete_confirmed

    def get_matte_list(self):
        self._maybe_raise()
        return {
            "matte_types": [{"matte_type": t} for t in ("none", "shadowbox", "flexible")],
            "matte_colors": [{"color": c} for c in ("black", "polar")],
        }

    def change_matte(self, content_id, matte_id=None, portrait_matte=None):
        self._maybe_raise()
        self.matte_calls = getattr(self, "matte_calls", [])
        self.matte_calls.append((content_id, matte_id, portrait_matte))

    def set_auto_rotation_status(self, duration=0, type=True, category_id=None):
        self._maybe_raise()
        self.rotation_calls = getattr(self, "rotation_calls", [])
        self.rotation_calls.append((duration, type, category_id))


@pytest.fixture
def make_tv(tmp_path):
    def factory(**stub_kwargs):
        token_file = tmp_path / "config" / "token.txt"
        tv = FrameTV("tv.local", token_file, timeout=1.0)
        tv._art = StubArt(**stub_kwargs)
        return tv

    return factory


def test_insecure_request_warning_is_suppressed():
    # A fresh interpreter, so pytest's own warning filters don't interfere:
    # importing tv_photobook.tv must silence urllib3's InsecureRequestWarning.
    import subprocess
    import sys

    code = (
        "import warnings, tv_photobook.tv\n"
        "from urllib3.exceptions import InsecureRequestWarning\n"
        "warnings.warn('unverified HTTPS request', InsecureRequestWarning)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0
    assert "InsecureRequestWarning" not in result.stderr


def test_connect_opens_the_art_channel(make_tv):
    make_tv().connect()  # no error when authorized


def test_connect_translates_unauthorized_to_a_pairing_hint(make_tv):
    tv = make_tv(error=exceptions.UnauthorizedError("denied"))
    with pytest.raises(TVError, match="pairing prompt"):
        tv.connect()


def test_connect_translates_connection_errors(make_tv):
    tv = make_tv(error=exceptions.ConnectionFailure("nope"))
    with pytest.raises(TVError, match="could not reach"):
        tv.connect()


def test_verify_passes_on_a_frame_tv(make_tv):
    make_tv(frame_support=True).verify_art_supported()


def test_verify_rejects_a_non_frame_tv(make_tv):
    with pytest.raises(TVError, match="does not support art mode"):
        make_tv(frame_support=False).verify_art_supported()


def test_verify_translates_connection_errors(make_tv):
    tv = make_tv(error=exceptions.ConnectionFailure("nope"))
    with pytest.raises(TVError, match="could not reach the TV"):
        tv.verify_art_supported()


def test_list_content_ids_returns_the_ids(make_tv):
    tv = make_tv(content=["MY_F0001", "SAM-S1234"])
    assert tv.list_content_ids() == {"MY_F0001", "SAM-S1234"}


def test_list_content_ids_maps_a_tv_error_answer_to_none(make_tv):
    tv = make_tv(error=exceptions.ResponseError("error 119"))
    assert tv.list_content_ids() is None


def test_list_content_ids_translates_connection_errors(make_tv):
    tv = make_tv(error=exceptions.ConnectionFailure("nope"))
    with pytest.raises(TVError, match="could not list"):
        tv.list_content_ids()


def test_upload_sends_the_path_with_the_matte(make_tv, tmp_path):
    tv = make_tv()
    photo = tmp_path / "a.jpg"

    assert tv.upload(photo, "shadowbox_polar") == "MY_F0042"

    [(file, kwargs)] = tv._art.upload_calls
    assert file == str(photo)
    assert kwargs == {"matte": "shadowbox_polar", "portrait_matte": "shadowbox_polar"}


def test_upload_translates_library_errors(make_tv, tmp_path):
    tv = make_tv(error=exceptions.ConnectionFailure("nope"))
    with pytest.raises(TVError):
        tv.upload(tmp_path / "a.jpg", "none")


@pytest.mark.parametrize("matte", ["none", "shadowbox_polar", "flexible_black"])
def test_validate_matte_accepts_supported_mattes(make_tv, matte):
    make_tv().validate_matte(matte)


def test_validate_matte_skips_the_tv_call_for_none(make_tv):
    tv = make_tv(error=exceptions.ConnectionFailure("nope"))
    tv.validate_matte("none")


@pytest.mark.parametrize("matte", ["shadowbox", "polar", "shadowbox_sparkles", "none_black"])
def test_validate_matte_rejects_unsupported_mattes(make_tv, matte):
    with pytest.raises(TVError, match="does not support the matte"):
        make_tv().validate_matte(matte)


def test_change_matte_sets_both_orientations(make_tv):
    tv = make_tv()

    tv.change_matte("MY_F0001", "flexible_polar")

    assert tv._art.matte_calls == [("MY_F0001", "flexible_polar", "flexible_polar")]


def test_change_matte_translates_library_errors(make_tv):
    tv = make_tv(error=exceptions.ResponseError("nope"))
    with pytest.raises(TVError):
        tv.change_matte("MY_F0001", "flexible_polar")


def test_set_slideshow_enables_rotation_over_my_pictures(make_tv):
    tv = make_tv()

    tv.set_slideshow(10, shuffle=True)

    assert tv._art.rotation_calls == [(10, True, "MY-C0002")]


def test_set_slideshow_off_passes_zero_minutes(make_tv):
    tv = make_tv()

    tv.set_slideshow(0, shuffle=False)

    assert tv._art.rotation_calls == [(0, False, "MY-C0002")]


def test_set_slideshow_translates_library_errors(make_tv):
    tv = make_tv(error=exceptions.ResponseError("nope"))
    with pytest.raises(TVError, match="slideshow setting"):
        tv.set_slideshow(10, shuffle=True)


def test_delete_passes_when_confirmed(make_tv):
    make_tv(delete_confirmed=True).delete("MY_F0001")


def test_unconfirmed_delete_raises(make_tv):
    with pytest.raises(TVError, match="did not confirm"):
        make_tv(delete_confirmed=False).delete("MY_F0001")


def test_delete_translates_library_errors(make_tv):
    tv = make_tv(error=exceptions.ResponseError("nope"))
    with pytest.raises(TVError):
        tv.delete("MY_F0001")
