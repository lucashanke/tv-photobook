"""End-to-end CLI tests with FrameTV swapped for FakeTV."""

import json

import pytest
from conftest import FakeTV, write_image

from tv_photobook import cli
from tv_photobook.state import StateStore


@pytest.fixture
def folder(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    return photos


@pytest.fixture
def fake_tv(monkeypatch):
    tv = FakeTV()
    captured = {}

    def factory(host, token_file, timeout):
        captured.update(host=host, token_file=token_file, timeout=timeout)
        return tv

    monkeypatch.setattr(cli, "FrameTV", factory)
    tv.constructor_args = captured
    return tv


def run(folder, tmp_path, *extra):
    token_file = tmp_path / "token.txt"
    return cli.main(
        [str(folder), "--host", "tv.local", "--token-file", str(token_file), *extra]
    )


class TestArguments:
    def test_host_is_required(self, folder, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main([str(folder)])
        assert exc.value.code == 2
        assert "--host" in capsys.readouterr().err

    def test_missing_folder_exits_2(self, tmp_path, fake_tv, capsys):
        assert run(tmp_path / "nope", tmp_path) == 2
        assert "is not a directory" in capsys.readouterr().err

    def test_corrupt_state_file_exits_2(self, folder, tmp_path, fake_tv, capsys):
        (folder / ".tv-photobook.json").write_text("{broken")
        assert run(folder, tmp_path) == 2
        assert "corrupt" in capsys.readouterr().err


class TestConnection:
    def test_unreachable_tv_exits_2(self, folder, tmp_path, fake_tv, capsys):
        fake_tv.reachable = False
        assert run(folder, tmp_path) == 2
        assert "could not reach the TV" in capsys.readouterr().err
        assert fake_tv.closed

    def test_non_frame_tv_exits_2(self, folder, tmp_path, fake_tv, capsys):
        fake_tv.art_supported = False
        assert run(folder, tmp_path) == 2
        assert "does not support art mode" in capsys.readouterr().err

    def test_unpaired_tv_exits_2_with_a_pairing_hint(self, folder, tmp_path, fake_tv, capsys):
        fake_tv.unauthorized = True
        assert run(folder, tmp_path) == 2
        assert "pairing prompt" in capsys.readouterr().err
        assert fake_tv.closed

    def test_constructor_receives_cli_options(self, folder, tmp_path, fake_tv):
        run(folder, tmp_path, "--timeout", "5")
        assert fake_tv.constructor_args["host"] == "tv.local"
        assert fake_tv.constructor_args["timeout"] == 5.0
        assert fake_tv.constructor_args["token_file"] == tmp_path / "token.txt"

    def test_a_clean_connection_prints_no_pairing_noise(self, folder, tmp_path, fake_tv, capsys):
        run(folder, tmp_path)
        assert "pairing" not in capsys.readouterr().out


class TestAmbiguousContentList:
    def test_listing_error_with_no_prior_uploads_is_treated_as_empty(
        self, folder, tmp_path, fake_tv
    ):
        fake_tv.content_list_error = True
        (folder / "a.jpg").write_bytes(b"photo")

        assert run(folder, tmp_path) == 0

        assert [op[0] for op in fake_tv.ops] == ["upload"]

    def test_listing_error_with_prior_uploads_refuses_to_sync(
        self, folder, tmp_path, fake_tv, capsys
    ):
        fake_tv.content_list_error = True
        (folder / "a.jpg").write_bytes(b"photo")
        run(folder, tmp_path)  # records a.jpg in the state file

        (folder / "a.jpg").unlink()
        assert run(folder, tmp_path) == 2

        assert "cannot tell what is still on it" in capsys.readouterr().err
        assert [op[0] for op in fake_tv.ops] == ["upload"]  # no deletions attempted


class TestSync:
    def test_uploads_new_photos_and_writes_state(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo-a")
        (folder / "b.png").write_bytes(b"photo-b")

        assert run(folder, tmp_path) == 0

        assert [op[0] for op in fake_tv.ops] == ["upload", "upload"]
        state = StateStore.load(folder / ".tv-photobook.json")
        assert state.items.keys() == {"a.jpg", "b.png"}
        out = capsys.readouterr().out
        assert "uploading a.jpg (new)" in out
        assert "Sync complete." in out
        assert fake_tv.closed

    def test_in_sync_folder_does_nothing(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo-a")
        assert run(folder, tmp_path) == 0

        fake_tv.ops.clear()
        assert run(folder, tmp_path) == 0

        assert fake_tv.ops == []
        assert "nothing to do" in capsys.readouterr().out

    def test_removed_photo_is_deleted_from_the_tv(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo-a")
        run(folder, tmp_path)
        (folder / "a.jpg").unlink()

        assert run(folder, tmp_path) == 0

        assert fake_tv.content_ids == set()
        assert StateStore.load(folder / ".tv-photobook.json").items == {}

    def test_upload_failure_exits_1_but_finishes_the_rest(
        self, folder, tmp_path, fake_tv, capsys
    ):
        (folder / "bad.jpg").write_bytes(b"photo")
        (folder / "good.jpg").write_bytes(b"photo")
        fake_tv.fail_uploads = {"bad.jpg"}

        assert run(folder, tmp_path) == 1

        state = StateStore.load(folder / ".tv-photobook.json")
        assert state.items.keys() == {"good.jpg"}
        assert "1 failure(s)" in capsys.readouterr().err

    def test_oversized_photo_is_skipped_with_a_warning(self, folder, tmp_path, fake_tv, capsys):
        (folder / "huge.jpg").write_bytes(b"x" * 2_000_000)
        (folder / "ok.jpg").write_bytes(b"x")

        assert run(folder, tmp_path, "--max-size-mb", "1") == 0

        assert [op[1] for op in fake_tv.ops] == ["ok.jpg"]
        assert "skipping huge.jpg" in capsys.readouterr().out

    def test_custom_state_file_location(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")
        state_file = tmp_path / "elsewhere.json"

        assert run(folder, tmp_path, "--state-file", str(state_file)) == 0

        assert state_file.exists()
        assert not (folder / ".tv-photobook.json").exists()


class TestMatte:
    def test_uploads_use_the_default_shadowbox_matte(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")

        assert run(folder, tmp_path) == 0

        assert list(fake_tv.mattes.values()) == ["shadowbox_polar"]
        state = StateStore.load(folder / ".tv-photobook.json")
        assert state.items["a.jpg"].matte == "shadowbox_polar"

    def test_matte_flag_overrides_the_default(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")

        assert run(folder, tmp_path, "--matte", "flexible_neutral") == 0

        assert list(fake_tv.mattes.values()) == ["flexible_neutral"]

    def test_changing_the_matte_restyles_without_reupload(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")
        run(folder, tmp_path, "--matte", "none")
        fake_tv.ops.clear()

        assert run(folder, tmp_path) == 0

        assert fake_tv.ops == [("matte", "MY_F0001", "shadowbox_polar")]
        state = StateStore.load(folder / ".tv-photobook.json")
        assert state.items["a.jpg"].matte == "shadowbox_polar"

    def test_unsupported_matte_exits_2_before_any_upload(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo")
        fake_tv.invalid_mattes = {"shadowbox_sparkles"}

        assert run(folder, tmp_path, "--matte", "shadowbox_sparkles") == 2

        assert "does not support the matte" in capsys.readouterr().err
        assert fake_tv.ops == []


class TestGroups:
    def test_subfolder_uploads_one_composite_with_no_matte(self, folder, tmp_path, fake_tv):
        pair = folder / "venice"
        pair.mkdir()
        write_image(pair / "1.jpg")
        write_image(pair / "2.jpg")

        assert run(folder, tmp_path) == 0

        assert [op[0] for op in fake_tv.ops] == ["upload"]
        state = StateStore.load(folder / ".tv-photobook.json")
        assert state.items.keys() == {"venice/"}
        assert state.items["venice/"].matte == "none"

    def test_single_photo_subfolder_warns_and_uploads_nothing(
        self, folder, tmp_path, fake_tv, capsys
    ):
        lonely = folder / "lonely"
        lonely.mkdir()
        write_image(lonely / "only.jpg")

        assert run(folder, tmp_path) == 0

        assert fake_tv.ops == []
        assert "at least 2 photos" in capsys.readouterr().out


class TestSlideshow:
    def test_enables_shuffle_rotation_after_sync(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo")

        assert run(folder, tmp_path, "--slideshow", "15") == 0

        assert fake_tv.slideshow == (15, True)
        assert "every 15 min (shuffle)" in capsys.readouterr().out

    def test_ordered_rotation_when_requested(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")

        run(folder, tmp_path, "--slideshow", "30", "--slideshow-order", "ordered")

        assert fake_tv.slideshow == (30, False)

    def test_zero_minutes_turns_it_off(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo")

        run(folder, tmp_path, "--slideshow", "0")

        assert fake_tv.slideshow == (0, True)
        assert "slideshow off" in capsys.readouterr().out

    def test_applies_even_when_already_in_sync(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")
        run(folder, tmp_path)  # first sync, no slideshow flag
        fake_tv.ops.clear()

        assert run(folder, tmp_path, "--slideshow", "10") == 0

        assert fake_tv.ops == []  # nothing re-uploaded
        assert fake_tv.slideshow == (10, True)

    def test_omitting_the_flag_leaves_the_setting_untouched(self, folder, tmp_path, fake_tv):
        (folder / "a.jpg").write_bytes(b"photo")

        run(folder, tmp_path)

        assert fake_tv.slideshow is None

    def test_negative_minutes_exits_2(self, folder, tmp_path, fake_tv, capsys):
        assert run(folder, tmp_path, "--slideshow", "-5") == 2
        assert "0 or more" in capsys.readouterr().err

    def test_dry_run_describes_but_does_not_set(self, folder, tmp_path, fake_tv, capsys):
        (folder / "a.jpg").write_bytes(b"photo")

        assert run(folder, tmp_path, "--slideshow", "10", "--dry-run") == 0

        assert fake_tv.slideshow is None
        assert "would cycle My Pictures every 10 min" in capsys.readouterr().out


class TestDryRun:
    def test_dry_run_prints_the_plan_and_mutates_nothing(
        self, folder, tmp_path, fake_tv, capsys
    ):
        (folder / "a.jpg").write_bytes(b"photo-a")

        assert run(folder, tmp_path, "--dry-run") == 0

        assert fake_tv.ops == []
        assert not (folder / ".tv-photobook.json").exists()
        assert "would upload a.jpg (new)" in capsys.readouterr().out

    def test_dry_run_shows_deletions_and_forgets(self, folder, tmp_path, fake_tv, capsys):
        state = {
            "version": 1,
            "items": {
                "removed.jpg": {
                    "sha256": "1" * 64,
                    "content_id": "MY_F0001",
                    "uploaded_at": "2026-06-11T00:00:00+00:00",
                },
                "forgotten.jpg": {
                    "sha256": "2" * 64,
                    "content_id": "MY_F0002",
                    "uploaded_at": "2026-06-11T00:00:00+00:00",
                },
            },
        }
        (folder / ".tv-photobook.json").write_text(json.dumps(state))
        fake_tv.content_ids = {"MY_F0001"}

        assert run(folder, tmp_path, "--dry-run") == 0

        out = capsys.readouterr().out
        assert "would delete removed.jpg from the TV (MY_F0001)" in out
        assert "would forget forgotten.jpg" in out
        assert fake_tv.ops == []
