"""Folder scanning, sync planning (pure), and plan execution.

An *artwork* is one unit displayed on the TV. A loose file in the folder is a
single artwork; a subdirectory is one composite artwork built from its photos
(two portraits side by side). Both look the same to the planner: a name, a
content hash, a target matte, and a way to render the file to upload.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .compose import ComposeError, Layout, compose
from .state import StateEntry, StateStore
from .tv import TVError

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class ArtTV(Protocol):
    def upload(self, path: Path, matte: str) -> str: ...
    def change_matte(self, content_id: str, matte: str) -> None: ...
    def delete(self, content_id: str) -> None: ...


class Artwork(Protocol):
    """One piece of art on the TV: a single photo or a composed group."""

    name: str
    sha256: str
    matte: str

    def render(self, workdir: Path) -> Path:
        """Return the path of the file to upload, composing it if needed."""
        ...


@dataclass(frozen=True)
class LocalPhoto:
    name: str
    path: Path
    sha256: str
    matte: str = "none"

    def render(self, workdir: Path) -> Path:
        return self.path


@dataclass(frozen=True)
class PhotoGroup:
    """A subdirectory of photos shown as one side-by-side composite.

    The frame is painted into the pixels, so the TV matte is always "none";
    the layout is folded into sha256 so border/gap/color edits re-compose.
    """

    name: str
    members: tuple[Path, ...]
    sha256: str
    layout: Layout
    matte: str = "none"

    def render(self, workdir: Path) -> Path:
        dest = workdir / (self.name.rstrip("/") + ".jpg")
        compose(list(self.members), dest, self.layout)
        return dest


@dataclass(frozen=True)
class Upload:
    artwork: Artwork
    reason: str  # "new" | "changed" | "missing on TV"
    # For "changed": the previous version's art, deleted after the new upload
    # succeeds so a failure never loses the photo on the TV.
    stale_content_id: str | None = None


@dataclass(frozen=True)
class Deletion:
    name: str
    content_id: str


@dataclass(frozen=True)
class MatteChange:
    name: str
    content_id: str
    matte: str


@dataclass
class SyncPlan:
    uploads: list[Upload] = field(default_factory=list)
    # Art already on the TV that only needs its matte updated.
    restyles: list[MatteChange] = field(default_factory=list)
    deletions: list[Deletion] = field(default_factory=list)
    # State entries whose file and TV art are both already gone.
    forgets: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.uploads or self.restyles or self.deletions or self.forgets)


def scan_folder(
    folder: Path, max_size_bytes: int, matte: str = "none", layout: Layout | None = None
) -> tuple[list[Artwork], list[str]]:
    """Return the folder's artworks plus warnings for anything skipped.

    Loose files become single artworks (uploaded with ``matte``); each
    subdirectory becomes one composite artwork laid out with ``layout``.
    """
    layout = layout or Layout()
    artworks: list[Artwork] = []
    warnings: list[str] = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            group, group_warnings = _scan_group(path, layout)
            warnings.extend(group_warnings)
            if group is not None:
                artworks.append(group)
            continue
        if not path.is_file() or path.suffix.lower() not in PHOTO_EXTENSIONS:
            continue
        size = path.stat().st_size
        if size > max_size_bytes:
            warnings.append(
                f"skipping {path.name}: {size / 1_000_000:.1f} MB exceeds the "
                f"{max_size_bytes / 1_000_000:.0f} MB limit"
            )
            continue
        artworks.append(LocalPhoto(path.name, path, _sha256(path), matte))
    return artworks, warnings


def _scan_group(folder: Path, layout: Layout) -> tuple[PhotoGroup | None, list[str]]:
    members = [
        p
        for p in sorted(folder.iterdir())
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in PHOTO_EXTENSIONS
    ]
    if not members:
        return None, []  # not an attempt at a group; ignore silently
    if len(members) < 2:
        return None, [
            f"skipping folder {folder.name}/: a group needs at least 2 photos, "
            f"found {len(members)}"
        ]
    # The trailing slash keeps a group's key distinct from a same-named file.
    name = folder.name + "/"
    return PhotoGroup(name, tuple(members), _group_sha(members, layout), layout), []


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _group_sha(members: list[Path], layout: Layout) -> str:
    """Hash member names, member contents, and the layout into one signature."""
    h = hashlib.sha256()
    for path in members:
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(_sha256(path).encode())
        h.update(b"\0")
    h.update(layout.signature().encode())
    return h.hexdigest()


def plan_sync(
    artworks: list[Artwork],
    state_items: dict[str, StateEntry],
    tv_ids: set[str],
) -> SyncPlan:
    """Diff the folder (source of truth) against recorded state and the TV."""
    plan = SyncPlan()
    local_names = {art.name for art in artworks}
    for art in artworks:
        entry = state_items.get(art.name)
        if entry is None:
            plan.uploads.append(Upload(art, "new"))
        elif entry.sha256 != art.sha256:
            stale = entry.content_id if entry.content_id in tv_ids else None
            plan.uploads.append(Upload(art, "changed", stale))
        elif entry.content_id not in tv_ids:
            plan.uploads.append(Upload(art, "missing on TV"))
        elif entry.matte != art.matte:
            plan.restyles.append(MatteChange(art.name, entry.content_id, art.matte))
    for name, entry in state_items.items():
        if name in local_names:
            continue
        if entry.content_id in tv_ids:
            plan.deletions.append(Deletion(name, entry.content_id))
        else:
            plan.forgets.append(name)
    return plan


def execute_plan(
    plan: SyncPlan, tv: ArtTV, store: StateStore, log: Callable[[str], None] = print
) -> int:
    """Apply the plan, persisting state after every TV mutation.

    Failures are logged and counted, not raised, so one bad photo does not
    abort the rest of the sync. A crash between a TV-confirmed upload and the
    state save leaves one orphan on the TV; the next run uploads a duplicate.
    """
    failures = 0
    with tempfile.TemporaryDirectory(prefix="tv-photobook-") as tmp:
        workdir = Path(tmp)
        for upload in plan.uploads:
            art = upload.artwork
            log(f"uploading {art.name} ({upload.reason})")
            try:
                content_id = tv.upload(art.render(workdir), art.matte)
            except (TVError, ComposeError) as e:
                log(f"  failed: {e}")
                failures += 1
                continue
            store.items[art.name] = StateEntry(
                sha256=art.sha256,
                content_id=content_id,
                uploaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                matte=art.matte,
            )
            store.save()
            if upload.stale_content_id:
                try:
                    tv.delete(upload.stale_content_id)
                except TVError as e:
                    log(f"  warning: the previous version is still on the TV: {e}")
                    failures += 1
    for restyle in plan.restyles:
        log(f"changing the matte of {restyle.name} to {restyle.matte}")
        try:
            tv.change_matte(restyle.content_id, restyle.matte)
        except TVError as e:
            log(f"  failed: {e}")
            failures += 1
            continue
        store.items[restyle.name].matte = restyle.matte
        store.save()
    for deletion in plan.deletions:
        log(f"deleting {deletion.name} from the TV")
        try:
            tv.delete(deletion.content_id)
        except TVError as e:
            log(f"  failed: {e}")
            failures += 1
            continue
        del store.items[deletion.name]
        store.save()
    if plan.forgets:
        for name in plan.forgets:
            log(f"forgetting {name} (already gone from the TV)")
            del store.items[name]
        store.save()
    return failures
