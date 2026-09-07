# Emulator Targets

Emulators are just more ADB devices — same commands, explicit serials.

## Addresses

- First instance: `emulator-5554`, second: `emulator-5556`, then +2 each.
- Always pass `-s`, e.g. `phone.py -s emulator-5554 screenshot e.png`.
- With a physical phone AND an emulator attached, unqualified commands fail
  with `ambiguous-device` on purpose — pick one.

## Start / recover

- Start from Android Studio Device Manager, or:
  `emulator -avd <AVD_NAME> -no-snapshot` (cold boot avoids stale `offline`).
- Emulator stuck `offline`: cold-boot it (Device Manager → ▼ → Cold Boot Now),
  or `adb -s emulator-5554 emu kill` and restart.
- Emulator has no RSA prompt and no cables — if `status` is empty, the
  emulator simply isn't running (or its ADB port is firewalled).

## Notes

- `uiautomator dump`, `screencap`, `input tap/swipe/text` all work identically.
- Play-Store system images vs plain API images differ in available apps —
  prefer `phone.py packages --filter <name>` over assuming a package exists.
- GPU-heavy screens may lag in `screenshot`; lower the emulator window scale
  rather than retrying faster.
