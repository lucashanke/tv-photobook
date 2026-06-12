"""Tests for StateStore persistence."""

import json

import pytest
from conftest import make_entry

from tv_photobook.state import StateError, StateStore


def test_missing_file_loads_as_empty(tmp_path):
    store = StateStore.load(tmp_path / "state.json")
    assert store.items == {}


def test_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.items["a.jpg"] = make_entry(sha="1" * 64, content_id="MY_F0001")
    store.save()

    loaded = StateStore.load(store.path)

    assert loaded.items == store.items


def test_saved_file_is_valid_versioned_json(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.items["a.jpg"] = make_entry()
    store.save()

    raw = json.loads(store.path.read_text())

    assert raw["version"] == 1
    assert set(raw["items"]["a.jpg"]) == {"sha256", "content_id", "uploaded_at", "matte"}


def test_state_files_from_before_matte_support_still_load(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"version": 1, "items": {"a.jpg": {"sha256": "%s", '
        '"content_id": "MY_F0001", "uploaded_at": "2026-06-11T00:00:00+00:00"}}}' % ("1" * 64)
    )

    store = StateStore.load(path)

    assert store.items["a.jpg"].matte == "none"


def test_corrupt_json_raises_state_error(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(StateError, match="unreadable or corrupt"):
        StateStore.load(path)


@pytest.mark.parametrize(
    "content",
    [
        '{"version": 999, "items": {}}',
        '{"items": {}}',
        '[]',
        '{"version": 1, "items": {"a.jpg": {"wrong": "fields"}}}',
    ],
)
def test_unsupported_format_raises_state_error(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    with pytest.raises(StateError, match="unsupported format"):
        StateStore.load(path)


def test_save_leaves_no_temp_files(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.items["a.jpg"] = make_entry()
    store.save()
    store.save()

    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_interrupted_save_keeps_the_previous_state(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    store.items["a.jpg"] = make_entry()
    store.save()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("tv_photobook.state.os.replace", boom)
    store.items["b.jpg"] = make_entry(content_id="MY_F0002")
    with pytest.raises(OSError):
        store.save()

    assert StateStore.load(store.path).items.keys() == {"a.jpg"}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
