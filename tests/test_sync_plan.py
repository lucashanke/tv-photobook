"""Tests for the pure planning logic: plan_sync and scan_folder."""

from conftest import make_entry, make_photo, upload_tuples, write_image

from tv_photobook.compose import Layout
from tv_photobook.sync import Deletion, MatteChange, PhotoGroup, plan_sync, scan_folder


class TestPlanSync:
    def test_everything_empty(self):
        plan = plan_sync([], {}, set())
        assert plan.is_empty

    def test_new_file_is_uploaded(self):
        plan = plan_sync([make_photo("a.jpg")], {}, set())
        assert upload_tuples(plan) == [("a.jpg", "new", None)]
        assert plan.deletions == []
        assert plan.forgets == []

    def test_unchanged_file_on_tv_is_skipped(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001")}
        plan = plan_sync([make_photo("a.jpg")], state, {"MY_F0001"})
        assert plan.is_empty

    def test_changed_file_is_replaced(self):
        state = {"a.jpg": make_entry(sha="b" * 64, content_id="MY_F0001")}
        plan = plan_sync([make_photo("a.jpg", sha="a" * 64)], state, {"MY_F0001"})
        assert upload_tuples(plan) == [("a.jpg", "changed", "MY_F0001")]

    def test_changed_file_with_old_art_gone_has_nothing_stale_to_delete(self):
        state = {"a.jpg": make_entry(sha="b" * 64, content_id="MY_F0001")}
        plan = plan_sync([make_photo("a.jpg", sha="a" * 64)], state, set())
        assert upload_tuples(plan) == [("a.jpg", "changed", None)]

    def test_unchanged_file_deleted_on_tv_is_reuploaded(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001")}
        plan = plan_sync([make_photo("a.jpg")], state, set())
        assert upload_tuples(plan) == [("a.jpg", "missing on TV", None)]

    def test_removed_file_still_on_tv_is_deleted(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001")}
        plan = plan_sync([], state, {"MY_F0001"})
        assert plan.uploads == []
        assert plan.deletions == [Deletion("a.jpg", "MY_F0001")]
        assert plan.forgets == []

    def test_removed_file_gone_from_tv_is_forgotten(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001")}
        plan = plan_sync([], state, set())
        assert plan.uploads == []
        assert plan.deletions == []
        assert plan.forgets == ["a.jpg"]

    def test_foreign_tv_art_is_never_deleted(self):
        plan = plan_sync([], {}, {"SAM-S1234", "MY_F9999"})
        assert plan.is_empty

    def test_matte_change_restyles_without_reupload(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001", matte="none")}
        plan = plan_sync([make_photo("a.jpg", matte="shadowbox_polar")], state, {"MY_F0001"})
        assert plan.uploads == []
        assert plan.restyles == [MatteChange("a.jpg", "MY_F0001", "shadowbox_polar")]

    def test_matching_matte_needs_no_restyle(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001", matte="shadowbox_polar")}
        plan = plan_sync([make_photo("a.jpg", matte="shadowbox_polar")], state, {"MY_F0001"})
        assert plan.is_empty

    def test_changed_file_gets_the_matte_via_reupload_not_restyle(self):
        state = {"a.jpg": make_entry(sha="b" * 64, content_id="MY_F0001", matte="none")}
        plan = plan_sync(
            [make_photo("a.jpg", sha="a" * 64, matte="shadowbox_polar")], state, {"MY_F0001"}
        )
        assert upload_tuples(plan) == [("a.jpg", "changed", "MY_F0001")]
        assert plan.restyles == []

    def test_art_missing_on_tv_gets_the_matte_via_reupload_not_restyle(self):
        state = {"a.jpg": make_entry(content_id="MY_F0001", matte="none")}
        plan = plan_sync([make_photo("a.jpg", matte="shadowbox_polar")], state, set())
        assert upload_tuples(plan) == [("a.jpg", "missing on TV", None)]
        assert plan.restyles == []

    def test_all_cases_combined(self):
        photos = [
            make_photo("new.jpg"),
            make_photo("unchanged.jpg", sha="1" * 64),
            make_photo("changed.jpg", sha="2" * 64),
            make_photo("reupload.jpg", sha="3" * 64),
        ]
        state = {
            "unchanged.jpg": make_entry(sha="1" * 64, content_id="MY_F0001"),
            "changed.jpg": make_entry(sha="old" + "0" * 61, content_id="MY_F0002"),
            "reupload.jpg": make_entry(sha="3" * 64, content_id="MY_F0003"),
            "removed.jpg": make_entry(content_id="MY_F0004"),
            "forgotten.jpg": make_entry(content_id="MY_F0005"),
        }
        tv_ids = {"MY_F0001", "MY_F0002", "MY_F0004", "SAM-S1234"}

        plan = plan_sync(photos, state, tv_ids)

        assert upload_tuples(plan) == [
            ("new.jpg", "new", None),
            ("changed.jpg", "changed", "MY_F0002"),
            ("reupload.jpg", "missing on TV", None),
        ]
        assert plan.deletions == [Deletion("removed.jpg", "MY_F0004")]
        assert plan.forgets == ["forgotten.jpg"]


class TestScanFolder:
    def test_returns_photos_sorted_with_hashes(self, tmp_path):
        (tmp_path / "b.png").write_bytes(b"png-bytes")
        (tmp_path / "a.jpg").write_bytes(b"jpg-bytes")
        (tmp_path / "c.JPEG").write_bytes(b"jpeg-bytes")

        photos, warnings = scan_folder(tmp_path, max_size_bytes=1_000_000)

        assert [p.name for p in photos] == ["a.jpg", "b.png", "c.JPEG"]
        assert all(len(p.sha256) == 64 for p in photos)
        assert warnings == []

    def test_identical_content_hashes_alike(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"same")
        (tmp_path / "b.jpg").write_bytes(b"same")
        (tmp_path / "c.jpg").write_bytes(b"different")

        photos, _ = scan_folder(tmp_path, max_size_bytes=1_000_000)

        assert photos[0].sha256 == photos[1].sha256
        assert photos[0].sha256 != photos[2].sha256

    def test_ignores_non_photos_dotfiles_and_directories(self, tmp_path):
        (tmp_path / "notes.txt").write_text("not a photo")
        (tmp_path / ".hidden.jpg").write_bytes(b"dotfile")
        (tmp_path / ".tv-photobook.json").write_text("{}")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "real.jpg").write_bytes(b"photo")

        photos, warnings = scan_folder(tmp_path, max_size_bytes=1_000_000)

        assert [p.name for p in photos] == ["real.jpg"]
        assert warnings == []

    def test_oversized_files_are_skipped_with_a_warning(self, tmp_path):
        (tmp_path / "huge.jpg").write_bytes(b"x" * 100)
        (tmp_path / "ok.jpg").write_bytes(b"x" * 10)

        photos, warnings = scan_folder(tmp_path, max_size_bytes=50)

        assert [p.name for p in photos] == ["ok.jpg"]
        assert len(warnings) == 1
        assert "huge.jpg" in warnings[0]

    def test_empty_folder(self, tmp_path):
        photos, warnings = scan_folder(tmp_path, max_size_bytes=1_000_000)
        assert photos == []
        assert warnings == []

    def test_single_files_carry_the_requested_matte(self, tmp_path):
        write_image(tmp_path / "a.jpg")

        artworks, _ = scan_folder(tmp_path, 1_000_000, matte="shadowbox_polar")

        assert artworks[0].matte == "shadowbox_polar"


class TestScanGroups:
    def _pair(self, tmp_path, folder="venice"):
        group_dir = tmp_path / folder
        group_dir.mkdir()
        write_image(group_dir / "2-right.jpg")
        write_image(group_dir / "1-left.jpg")
        return group_dir

    def test_subdirectory_becomes_one_composite_artwork(self, tmp_path):
        self._pair(tmp_path)

        artworks, warnings = scan_folder(tmp_path, 1_000_000)

        assert warnings == []
        [group] = artworks
        assert isinstance(group, PhotoGroup)
        assert group.name == "venice/"
        # Always "none": the frame is painted into the composite, not set on the TV.
        assert group.matte == "none"

    def test_members_are_ordered_by_filename(self, tmp_path):
        self._pair(tmp_path)

        [group], _ = scan_folder(tmp_path, 1_000_000)

        assert [p.name for p in group.members] == ["1-left.jpg", "2-right.jpg"]

    def test_groups_and_loose_files_coexist(self, tmp_path):
        self._pair(tmp_path)
        write_image(tmp_path / "solo.jpg")

        artworks, _ = scan_folder(tmp_path, 1_000_000)

        assert sorted(a.name for a in artworks) == ["solo.jpg", "venice/"]

    def test_changing_a_member_changes_the_group_hash(self, tmp_path):
        self._pair(tmp_path)
        [before], _ = scan_folder(tmp_path, 1_000_000)
        write_image(tmp_path / "venice" / "1-left.jpg", color=(0, 200, 0))

        [after], _ = scan_folder(tmp_path, 1_000_000)

        assert before.sha256 != after.sha256

    def test_changing_the_layout_changes_the_group_hash(self, tmp_path):
        self._pair(tmp_path)

        [wide], _ = scan_folder(tmp_path, 1_000_000, layout=Layout(margin=50))
        [narrow], _ = scan_folder(tmp_path, 1_000_000, layout=Layout(margin=200))

        assert wide.sha256 != narrow.sha256

    def test_single_photo_subfolder_is_skipped_with_a_warning(self, tmp_path):
        lonely = tmp_path / "lonely"
        lonely.mkdir()
        write_image(lonely / "only.jpg")

        artworks, warnings = scan_folder(tmp_path, 1_000_000)

        assert artworks == []
        assert len(warnings) == 1
        assert "at least 2 photos" in warnings[0]

    def test_subfolder_without_photos_is_ignored_silently(self, tmp_path):
        junk = tmp_path / "junk"
        junk.mkdir()
        (junk / "notes.txt").write_text("nope")

        artworks, warnings = scan_folder(tmp_path, 1_000_000)

        assert artworks == []
        assert warnings == []
