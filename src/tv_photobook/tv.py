"""Thin wrapper around samsungtvws; the only module that talks to the TV."""

from __future__ import annotations

from pathlib import Path

import urllib3
import websocket
from samsungtvws import SamsungTVArt, exceptions

# The Frame serves its local API over HTTPS with a self-signed certificate that
# cannot be verified, so the library connects with verification off. That makes
# urllib3 emit an InsecureRequestWarning on every REST call; it is expected here
# and only clutters the output.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LIBRARY_ERRORS = (
    exceptions.ConnectionFailure,
    exceptions.ResponseError,
    exceptions.HttpApiError,
    exceptions.MessageError,
    websocket.WebSocketException,
    # requests and socket errors are both OSError subclasses
    OSError,
)


class TVError(Exception):
    pass


# "My Pictures", where uploaded art lands, is auto-rotation category MY-C0002.
_MY_PICTURES = "MY-C0002"


class FrameTV:
    def __init__(self, host: str, token_file: Path, timeout: float) -> None:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        self.host = host
        # The TV persists a pairing token here once it issues one; some
        # firmwares authorize by app name instead and never send a token.
        # Port 8002 is the TLS endpoint that supports token pairing; the
        # library defaults to 8001, which does not.
        self._art = SamsungTVArt(
            host,
            token_file=str(token_file),
            port=8002,
            timeout=timeout,
            name="tv-photobook",
        )

    def connect(self) -> None:
        """Open the art channel, surfacing a clear hint if pairing is required.

        The first connection from an unknown app makes the TV show an on-screen
        Allow prompt; until it is accepted the TV reports the channel as
        unauthorized.
        """
        try:
            self._art.open()
        except exceptions.UnauthorizedError as e:
            raise TVError(
                f"the TV at {self.host} has not authorized tv-photobook. Accept "
                "the pairing prompt on the TV screen, then run the command again."
            ) from e
        except _LIBRARY_ERRORS as e:
            raise TVError(
                f"could not reach the TV at {self.host}; make sure it is on and "
                f"on the same network ({e})"
            ) from e

    def verify_art_supported(self) -> None:
        try:
            supported = self._art.supported()
        except _LIBRARY_ERRORS as e:
            raise TVError(
                f"could not reach the TV at {self.host}; make sure it is on and "
                f"on the same network ({e})"
            ) from e
        if not supported:
            raise TVError(f"the TV at {self.host} does not support art mode (not a Frame?)")

    def list_content_ids(self) -> set[str] | None:
        """The TV's content ids, or None if the TV answered with an error.

        Some firmwares report an error instead of an empty list when the TV
        holds no art at all, so an error answer is ambiguous; the caller
        decides whether to treat it as empty.
        """
        try:
            return {item["content_id"] for item in self._art.available()}
        except exceptions.ResponseError:
            return None
        except _LIBRARY_ERRORS as e:
            raise TVError(f"could not list the TV's art: {e}") from e

    def validate_matte(self, matte: str) -> None:
        if matte == "none":
            return
        try:
            available = self._art.get_matte_list()
        except _LIBRARY_ERRORS as e:
            raise TVError(f"could not fetch the TV's matte list: {e}") from e
        types = {t["matte_type"] for t in available.get("matte_types", [])} - {"none"}
        colors = {c["color"] for c in available.get("matte_colors", [])}
        if matte not in {f"{t}_{c}" for t in types for c in colors}:
            raise TVError(
                f"the TV does not support the matte {matte!r}; pick <type>_<color> "
                f"with type in {sorted(types)} and color in {sorted(colors)}, or 'none'"
            )

    def upload(self, path: Path, matte: str) -> str:
        try:
            return self._art.upload(str(path), matte=matte, portrait_matte=matte)
        except _LIBRARY_ERRORS as e:
            raise TVError(str(e)) from e

    def change_matte(self, content_id: str, matte: str) -> None:
        try:
            self._art.change_matte(content_id, matte_id=matte, portrait_matte=matte)
        except _LIBRARY_ERRORS as e:
            raise TVError(str(e)) from e

    def set_slideshow(self, minutes: int, shuffle: bool) -> None:
        """Cycle art mode through My Pictures every `minutes`, or off when 0.

        Rotation is category-wide, so it cycles everything in My Pictures, not
        only the art this tool uploaded.
        """
        try:
            self._art.set_auto_rotation_status(
                duration=minutes, type=shuffle, category_id=_MY_PICTURES
            )
        except _LIBRARY_ERRORS as e:
            raise TVError(f"could not change the slideshow setting: {e}") from e

    def delete(self, content_id: str) -> None:
        try:
            confirmed = self._art.delete(content_id)
        except _LIBRARY_ERRORS as e:
            raise TVError(str(e)) from e
        if not confirmed:
            raise TVError("the TV did not confirm the deletion")

    def close(self) -> None:
        self._art.close()
