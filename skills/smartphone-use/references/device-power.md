# Device Power Commands (V2)

Beyond tap/type: read notifications, move clipboard and files, record video,
run apps in a virtual display, and survey the whole fleet.

## Notifications — `notify [--filter TEXT]`

Read-only list from `dumpsys notification --noredact`: package, title, text
(up to 50). Use it to "read incoming messages/alerts" without opening apps,
or as recipe verification ("expect the download-complete notification").

```powershell
python scripts/phone.py notify --json
python scripts/phone.py notify --filter "WhatsApp"
```

There is no dismiss-all via ADB — open the shade and swipe if needed.

## Clipboard — `clip get | clip set TEXT [--via type]`

Android 10+ blocks background clipboard access, so there is deliberately NO
pure-ADB clipboard. Behavior is honest about that:

- `clip set TEXT` → real clipboard via the **AdbClipboard helper app**
  (open source; user installs it once and grants "Display over other apps",
  an explicit consent step). Without the helper it REFUSES and tells you why.
- `clip set TEXT --via type` → explicit fallback: types into the focused
  field (labeled as typing, not clipboard).
- `clip get` → helper sync file, then best-effort `dumpsys clipboard`,
  else guidance: copy on-device + MOD+v in the `mirror` window (scrcpy
  syncs device→PC clipboard).

Always `clip get` after `clip set` in flows that depend on the value.

## Files — `files pull REMOTE LOCAL | push LOCAL REMOTE | ls REMOTE`

```powershell
python scripts/phone.py files ls /sdcard/DCIM
python scripts/phone.py files pull /sdcard/DCIM/photo.jpg ./
python scripts/phone.py files push ./doc.pdf /sdcard/Download/
```

`push` supports `--dry-run`. App-private dirs (`/data/data/…`) need root —
don't fight that; use share/export inside the app instead.

## Screen recording — `record OUT.mp4 [--seconds N]`

System `screenrecord` (1–180 s, **no audio by design**) + auto-pull +
device cleanup. Fails on DRM/secure screens — say so, don't retry blindly.

```powershell
python scripts/phone.py record demo.mp4 --seconds 15
```

## Desk mode — `desk PACKAGE [--size WxH] [--detach]`

scrcpy virtual display (`--new-display --flex-display --start-app`): the app
runs in a resizable PC window while the **physical screen stays untouched**.
This is how the user keeps using the phone while the agent works.

```powershell
python scripts/phone.py desk org.mozilla.firefox --size 1280x960
```

## Fleet — `fleet`

Every attached device with model, Android version, battery %, and guessed
transport (usb/wifi/emulator). Pair with `-s SERIAL` for everything else.

## Mirror extras

- `mirror --otg`: HID control with NO debugging and NO mirroring (locked-down
  or fresh-reset devices; you look at the phone directly).
- `mirror -- --video-codec=h265 -b16M`: quality passthrough, e.g. desk-size
  windows. Audio/camera flags (`--audio-source`, `--video-source=camera`)
  also pass through — see `scrcpy --help` as source of truth.
