"""Tests for execute_plan against a FakeTV and a real on-disk StateStore."""

import pytest
from conftest import FakeTV, make_entry, make_photo, write_image

from tv_photobook.compose import Layout
from tv_photobook.state import StateStore
from tv_photobook.sync import (
    Deletion,
    MatteChange,
    PhotoGroup,
    SyncPlan,
    Upload,
    plan_sync,
    execute_plan,
)


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


def reload(store):
    return StateStore.load(store.path)


class TestUploads:
    def test_upload_records_state_and_persists(self, store):
        tv = FakeTV()
        plan = SyncPlan(uploads=[Upload(make_photo("a.jpg", matte="shadowbox_polar"), "new")])

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert store.items["a.jpg"].content_id == "MY_F0001"
        assert store.items["a.jpg"].sha256 == "a" * 64
        assert store.items["a.jpg"].matte == "shadowbox_polar"
        assert tv.mattes["MY_F0001"] == "shadowbox_polar"
        assert reload(store).items.keys() == {"a.jpg"}

    def test_failed_upload_is_counted_and_sync_continues(self, store):
        tv = FakeTV(fail_uploads={"a.jpg"})
        plan = SyncPlan(
            uploads=[Upload(make_photo("a.jpg"), "new"), Upload(make_photo("b.jpg"), "new")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert "a.jpg" not in store.items
        assert store.items["b.jpg"].content_id == "MY_F0001"
        assert reload(store).items.keys() == {"b.jpg"}

    def test_state_is_persisted_after_each_upload(self, store):
        tv = FakeTV(fail_uploads={"b.jpg"})
        plan = SyncPlan(
            uploads=[Upload(make_photo("a.jpg"), "new"), Upload(make_photo("b.jpg"), "new")]
        )

        execute_plan(plan, tv, store)

        # a.jpg reached disk even though the run hit a failure afterwards
        assert reload(store).items.keys() == {"a.jpg"}


class TestGroupUploads:
    def _group(self, tmp_path, name="venice/"):
        members = (write_image(tmp_path / "1.jpg"), write_image(tmp_path / "2.jpg"))
        return PhotoGroup(name, members, sha256="g" * 64, layout=Layout())

    def test_group_is_composed_and_uploaded_with_no_matte(self, store, tmp_path):
        tv = FakeTV()
        plan = SyncPlan(uploads=[Upload(self._group(tmp_path), "new")])

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert [op[:2] for op in tv.ops] == [("upload", "venice.jpg")]
        assert tv.mattes["MY_F0001"] == "none"
        assert store.items["venice/"].content_id == "MY_F0001"
        assert store.items["venice/"].sha256 == "g" * 64

    def test_unreadable_member_fails_the_group_without_aborting(self, store, tmp_path):
        tv = FakeTV()
        broken = PhotoGroup(
            "broken/",
            (write_image(tmp_path / "ok.jpg"), tmp_path / "missing.jpg"),
            sha256="b" * 64,
            layout=Layout(),
        )
        plan = SyncPlan(
            uploads=[Upload(broken, "new"), Upload(make_photo("after.jpg"), "new")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert "broken/" not in store.items
        assert store.items["after.jpg"].content_id == "MY_F0001"


class TestReplacements:
    def test_replacement_uploads_before_deleting_the_old_art(self, store):
        tv = FakeTV(content_ids={"MY_F9999"})
        plan = SyncPlan(
            uploads=[Upload(make_photo("a.jpg"), "changed", stale_content_id="MY_F9999")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert tv.ops == [("upload", "a.jpg", "MY_F0001"), ("delete", "MY_F9999")]
        assert store.items["a.jpg"].content_id == "MY_F0001"
        assert "MY_F9999" not in tv.content_ids

    def test_failed_upload_leaves_the_old_art_alone(self, store):
        tv = FakeTV(content_ids={"MY_F9999"}, fail_uploads={"a.jpg"})
        plan = SyncPlan(
            uploads=[Upload(make_photo("a.jpg"), "changed", stale_content_id="MY_F9999")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert tv.ops == []
        assert "MY_F9999" in tv.content_ids
        assert store.items == {}

    def test_stale_delete_failure_keeps_the_new_state_but_counts(self, store):
        tv = FakeTV(content_ids={"MY_F9999"}, fail_deletes={"MY_F9999"})
        plan = SyncPlan(
            uploads=[Upload(make_photo("a.jpg"), "changed", stale_content_id="MY_F9999")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert store.items["a.jpg"].content_id == "MY_F0001"
        assert reload(store).items["a.jpg"].content_id == "MY_F0001"


class TestRestyles:
    def test_restyle_changes_the_matte_and_persists(self, store):
        tv = FakeTV(content_ids={"MY_F0001"})
        store.items["a.jpg"] = make_entry(content_id="MY_F0001", matte="none")
        plan = SyncPlan(restyles=[MatteChange("a.jpg", "MY_F0001", "shadowbox_polar")])

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert tv.ops == [("matte", "MY_F0001", "shadowbox_polar")]
        assert store.items["a.jpg"].matte == "shadowbox_polar"
        assert reload(store).items["a.jpg"].matte == "shadowbox_polar"

    def test_failed_restyle_keeps_the_recorded_matte_and_counts(self, store):
        tv = FakeTV(content_ids={"MY_F0001"}, fail_mattes={"MY_F0001"})
        store.items["a.jpg"] = make_entry(content_id="MY_F0001", matte="none")
        plan = SyncPlan(restyles=[MatteChange("a.jpg", "MY_F0001", "shadowbox_polar")])

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert store.items["a.jpg"].matte == "none"


class TestDeletions:
    def test_deletion_removes_the_entry_and_persists(self, store):
        tv = FakeTV(content_ids={"MY_F0001"})
        store.items["a.jpg"] = make_entry(content_id="MY_F0001")
        plan = SyncPlan(deletions=[Deletion("a.jpg", "MY_F0001")])

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert store.items == {}
        assert reload(store).items == {}
        assert "MY_F0001" not in tv.content_ids

    def test_failed_deletion_keeps_the_entry_and_sync_continues(self, store):
        tv = FakeTV(content_ids={"MY_F0001", "MY_F0002"}, fail_deletes={"MY_F0001"})
        store.items["a.jpg"] = make_entry(content_id="MY_F0001")
        store.items["b.jpg"] = make_entry(content_id="MY_F0002")
        plan = SyncPlan(
            deletions=[Deletion("a.jpg", "MY_F0001"), Deletion("b.jpg", "MY_F0002")]
        )

        failures = execute_plan(plan, tv, store)

        assert failures == 1
        assert store.items.keys() == {"a.jpg"}
        assert reload(store).items.keys() == {"a.jpg"}


class TestForgets:
    def test_forgotten_entries_are_dropped_and_persisted(self, store):
        tv = FakeTV()
        store.items["gone.jpg"] = make_entry(content_id="MY_F0001")
        plan = SyncPlan(forgets=["gone.jpg"])

        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert store.items == {}
        assert reload(store).items == {}
        assert tv.ops == []


class TestFullRun:
    def test_planned_sync_converges_against_the_fake_tv(self, store):
        folder_photos = [
            make_photo("new.jpg"),
            make_photo("changed.jpg", sha="2" * 64),
        ]
        store.items["changed.jpg"] = make_entry(sha="old" + "0" * 61, content_id="MY_F9001")
        store.items["removed.jpg"] = make_entry(content_id="MY_F9002")
        tv = FakeTV(content_ids={"MY_F9001", "MY_F9002", "SAM-S1234"})

        plan = plan_sync(folder_photos, store.items, tv.list_content_ids())
        failures = execute_plan(plan, tv, store)

        assert failures == 0
        assert store.items.keys() == {"new.jpg", "changed.jpg"}
        # The TV holds exactly our two uploads plus the untouched store art
        assert tv.content_ids == {
            store.items["new.jpg"].content_id,
            store.items["changed.jpg"].content_id,
            "SAM-S1234",
        }
        # A second planning pass finds nothing left to do
        assert plan_sync(folder_photos, store.items, tv.list_content_ids()).is_empty
