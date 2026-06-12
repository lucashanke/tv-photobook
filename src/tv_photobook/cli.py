"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compose import POLAR, Layout
from .state import StateError, StateStore
from .sync import SyncPlan, execute_plan, plan_sync, scan_folder
from .tv import FrameTV, TVError

DEFAULT_TOKEN_FILE = Path.home() / ".config" / "tv-photobook" / "token.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tv-photobook",
        description=(
            "Sync a folder of photos to a Samsung Frame TV's art mode. The folder "
            "is the source of truth: new photos are uploaded, removed photos are "
            "deleted from the TV. Only art uploaded by this tool is ever deleted."
        ),
    )
    parser.add_argument("folder", type=Path, help="folder of photos to sync")
    parser.add_argument("--host", required=True, help="IP address or hostname of the TV")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without modifying the TV",
    )
    parser.add_argument(
        "--matte",
        default="shadowbox_polar",
        help=(
            "matte (frame) style as <type>_<color>, e.g. shadowbox_polar or "
            "flexible_neutral; keeps photo proportions. Use 'none' for "
            "full-bleed 16:9 photos (default: shadowbox_polar)"
        ),
    )
    parser.add_argument(
        "--frame-color",
        default=POLAR,
        help=(
            "background of composed subfolder diptychs, a color name or hex; "
            f"defaults to {POLAR}, an approximation of the Frame's polar matte"
        ),
    )
    parser.add_argument(
        "--frame-margin",
        type=int,
        default=100,
        help="outer border of composed diptychs in pixels (default: 100)",
    )
    parser.add_argument(
        "--frame-gap",
        type=int,
        default=100,
        help="gap between photos in composed diptychs in pixels (default: 100)",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help=f"where the TV pairing token is stored (default: {DEFAULT_TOKEN_FILE})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="sync state location (default: <folder>/.tv-photobook.json)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="TV connection timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=20.0,
        help="skip photos larger than this many MB (default: 20)",
    )
    parser.add_argument(
        "--slideshow",
        type=int,
        metavar="MINUTES",
        help=(
            "cycle art mode through My Pictures every MINUTES (0 turns it off); "
            "omit to leave the TV's slideshow setting unchanged. Rotation is "
            "category-wide, so it includes any other art in My Pictures"
        ),
    )
    parser.add_argument(
        "--slideshow-order",
        choices=("shuffle", "ordered"),
        default="shuffle",
        help="order when --slideshow is on (default: shuffle)",
    )
    return parser


def describe_slideshow(minutes: int, order: str) -> str:
    if minutes == 0:
        return "turn the art-mode slideshow off"
    return f"cycle My Pictures every {minutes} min ({order})"


def describe_plan(plan: SyncPlan) -> None:
    for upload in plan.uploads:
        print(f"would upload {upload.artwork.name} ({upload.reason})")
        if upload.stale_content_id:
            print(f"  and delete its previous version ({upload.stale_content_id})")
    for restyle in plan.restyles:
        print(f"would change the matte of {restyle.name} to {restyle.matte}")
    for deletion in plan.deletions:
        print(f"would delete {deletion.name} from the TV ({deletion.content_id})")
    for name in plan.forgets:
        print(f"would forget {name} (already gone from the TV)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.folder.is_dir():
        print(f"error: {args.folder} is not a directory", file=sys.stderr)
        return 2

    if args.slideshow is not None and args.slideshow < 0:
        print("error: --slideshow minutes must be 0 or more", file=sys.stderr)
        return 2

    state_path = args.state_file or args.folder / ".tv-photobook.json"
    try:
        store = StateStore.load(state_path)
    except StateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    tv = FrameTV(args.host, args.token_file, args.timeout)
    try:
        tv.verify_art_supported()
        tv.connect()
        tv.validate_matte(args.matte)

        layout = Layout(args.frame_margin, args.frame_gap, args.frame_color)
        artworks, warnings = scan_folder(
            args.folder, int(args.max_size_mb * 1_000_000), args.matte, layout
        )
        for warning in warnings:
            print(warning)

        tv_ids = tv.list_content_ids()
        if tv_ids is None:
            # Ambiguous answer: an empty TV on some firmwares, or a glitch.
            # With no prior uploads recorded, either reading is harmless;
            # otherwise planning deletions against it would be a guess.
            if store.items:
                raise TVError(
                    "the TV reported an error listing its art, so the sync "
                    "cannot tell what is still on it. Try again; if the TV "
                    f"art was wiped on purpose, delete {state_path} to start fresh."
                )
            tv_ids = set()

        plan = plan_sync(artworks, store.items, tv_ids)

        if args.dry_run:
            if plan.is_empty:
                print(f"Already in sync: {len(artworks)} artwork(s).")
            else:
                describe_plan(plan)
            if args.slideshow is not None:
                print(f"would {describe_slideshow(args.slideshow, args.slideshow_order)}")
            return 0

        failures = 0
        if plan.is_empty:
            print(f"Already in sync: {len(artworks)} artwork(s), nothing to do.")
        else:
            failures = execute_plan(plan, tv, store)

        if args.slideshow is not None:
            tv.set_slideshow(args.slideshow, args.slideshow_order == "shuffle")
            print(f"Slideshow: {describe_slideshow(args.slideshow, args.slideshow_order)}.")
    except TVError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        tv.close()

    if failures:
        print(f"Sync finished with {failures} failure(s).", file=sys.stderr)
        return 1
    if not plan.is_empty:
        print("Sync complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
