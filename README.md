# tv-photobook

Sync a folder of photos to a Samsung Frame TV's art mode. The folder is the
source of truth: new photos are uploaded, changed photos are replaced, and
photos you delete from the folder are removed from the TV. Only art uploaded
by this tool is ever deleted; Samsung store art and anything uploaded another
way are never touched.

Works with 2021-2023 Frame TVs (LS03A/B/C) over the local network, using
[samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api).

## Usage

```sh
uv run tv-photobook --host 192.168.1.50 ~/Pictures/frame

# Preview what would change without touching the TV
uv run tv-photobook --host 192.168.1.50 --dry-run ~/Pictures/frame

# Sync, then cycle art mode through the photos every 10 minutes
uv run tv-photobook --host 192.168.1.50 --slideshow 10 ~/Pictures/frame
```

The first time an unknown app connects, the TV shows an on-screen prompt;
select **Allow** and run the command again. If the TV issues a pairing token it
is saved to `~/.config/tv-photobook/token.txt`; some firmwares authorize by app
name instead and never send one. Either way, once approved the TV connects
silently, and the tool only mentions pairing if the TV actually refuses.

The TV's IP is under Settings > General > Network > Network Status on the TV
(consider giving it a DHCP reservation in your router so it doesn't change).

## How it works

Sync state lives in `<folder>/.tv-photobook.json`, mapping each photo to the
TV content id its upload produced. Each run diffs the folder against that
state and the TV's actual content list:

| Situation | Action |
| --- | --- |
| Photo not in state | upload |
| Photo changed (hash differs) | upload new, then delete the old art |
| Photo unchanged but deleted on the TV | re-upload (folder wins) |
| Photo removed from the folder | delete its art from the TV |
| Photo removed from folder and TV | drop it from the state file |
| Matte setting changed | restyle the art in place (no re-upload) |
| Subfolder of photos | compose into one side-by-side frame, then sync as a unit |

State is saved after every upload/delete, so an interrupted sync resumes
cleanly. Worst case after a crash is one duplicate upload, never lost art.

## Notes

- Your machine and the TV must be on the same network/subnet; the TV must be
  on (or in art mode), not in standby.
- Only `.jpg`, `.jpeg`, and `.png` files are synced; dotfiles are ignored.
- **Showing two photos at once:** put them in a subfolder. The Frame displays
  one artwork at a time, so a subfolder is composed locally into a single
  16:9 image with its photos side by side (sorted by filename: first is left),
  each fitted inside its own cell so nothing is stretched. Two portraits is the
  sweet spot; more than two tile in a row. The frame around them is painted
  into the image (`--frame-color`, `--frame-margin`, `--frame-gap`), so these
  composites ignore `--matte`. Editing, adding, or removing a photo in the
  subfolder re-composes and re-uploads that one frame.
- Photos display inside a matte (frame) that preserves their proportions;
  the default is `shadowbox_polar` (white shadowbox). Pick another with
  `--matte <type>_<color>` (e.g. `flexible_neutral`), or `--matte none` for
  full-bleed, which stretches photos that are not 16:9. The TV reports its
  supported types and colors; an unsupported value fails before any upload.
- **Cycling through the photos:** pass `--slideshow MINUTES` to turn on the
  Frame's art-mode rotation (shuffle by default; `--slideshow-order ordered`
  for sequence, `--slideshow 0` to stop it). Rotation is a TV setting over the
  whole "My Pictures" category, so it cycles any other art there too, not only
  what this tool uploaded. Omit the flag to leave the TV's current setting
  alone.
- Files over 20 MB are skipped (`--max-size-mb` to adjust).
- See `tv-photobook --help` for `--state-file`, `--token-file`, and
  `--timeout`.

## Development

```sh
uv run pytest
```
