from pathlib import Path

from PIL import Image

from tv_photobook.state import StateEntry
from tv_photobook.sync import LocalPhoto
from tv_photobook.tv import TVError


def write_image(path, size=(400, 600), color=(120, 30, 30)):
    """Write a small solid-color JPEG/PNG so compose/scan tests have real images."""
    Image.new("RGB", size, color).save(path)
    return path


class FakeTV:
    """In-memory stand-in for FrameTV with scriptable failures."""

    def __init__(
        self,
        content_ids=(),
        art_supported=True,
        reachable=True,
        fail_uploads=(),
        fail_deletes=(),
        fail_mattes=(),
        invalid_mattes=(),
        content_list_error=False,
        unauthorized=False,
        fail_slideshow=False,
    ):
        self.content_ids = set(content_ids)
        self.art_supported = art_supported
        self.reachable = reachable
        self.fail_uploads = set(fail_uploads)  # file names whose upload raises
        self.fail_deletes = set(fail_deletes)  # content ids whose delete raises
        self.fail_mattes = set(fail_mattes)  # content ids whose change_matte raises
        self.invalid_mattes = set(invalid_mattes)  # mattes rejected by validation
        self.content_list_error = content_list_error  # TV errors instead of listing
        self.unauthorized = unauthorized  # TV refuses the art channel until paired
        self.fail_slideshow = fail_slideshow  # slideshow change raises
        self.mattes = {}  # content_id -> matte
        self.slideshow = None  # (minutes, shuffle) once set
        self.ops = []
        self.closed = False
        self._counter = 0

    def verify_art_supported(self):
        if not self.reachable:
            raise TVError("could not reach the TV")
        if not self.art_supported:
            raise TVError("the TV does not support art mode")

    def connect(self):
        if self.unauthorized:
            raise TVError("accept the pairing prompt on the TV screen")

    def set_slideshow(self, minutes, shuffle):
        if self.fail_slideshow:
            raise TVError("slideshow change failed")
        self.slideshow = (minutes, shuffle)

    def list_content_ids(self):
        if self.content_list_error:
            return None
        return set(self.content_ids)

    def validate_matte(self, matte):
        if matte in self.invalid_mattes:
            raise TVError(f"the TV does not support the matte {matte!r}")

    def upload(self, path, matte):
        name = Path(path).name
        if name in self.fail_uploads:
            raise TVError(f"upload of {name} failed")
        self._counter += 1
        content_id = f"MY_F{self._counter:04d}"
        self.content_ids.add(content_id)
        self.mattes[content_id] = matte
        self.ops.append(("upload", name, content_id))
        return content_id

    def change_matte(self, content_id, matte):
        if content_id in self.fail_mattes:
            raise TVError(f"matte change of {content_id} failed")
        self.mattes[content_id] = matte
        self.ops.append(("matte", content_id, matte))

    def delete(self, content_id):
        if content_id in self.fail_deletes:
            raise TVError(f"delete of {content_id} failed")
        self.ops.append(("delete", content_id))
        self.content_ids.discard(content_id)

    def close(self):
        self.closed = True


def make_photo(name, sha="a" * 64, matte="none"):
    return LocalPhoto(name=name, path=Path(f"/photos/{name}"), sha256=sha, matte=matte)


def make_entry(sha="a" * 64, content_id="MY_F0001", matte="none"):
    return StateEntry(
        sha256=sha,
        content_id=content_id,
        uploaded_at="2026-06-11T00:00:00+00:00",
        matte=matte,
    )


def upload_tuples(plan):
    return [(u.artwork.name, u.reason, u.stale_content_id) for u in plan.uploads]
