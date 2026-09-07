# Troubleshooting Matrix

Always start with `python scripts/phone.py status --json` — or faster,
`phone.py doctor` (host+server health) and `phone.py diag` (full bundle for
handoffs). `doctor --fix` safely restarts the ADB server and clears stale
Wi-Fi entries. Find your symptom below. Apply fixes top-to-bottom; if two fail, stop and ask the user
one targeted question with the full `status` output attached.

## A. `adb-missing` — "`adb` not found"

- Run `scripts/install-deps.ps1`, open a NEW terminal (PATH refresh), retry.
- Manual: install platform-tools, add it to PATH, `adb --version`.

## B. `no-device` — empty list (USB)

1. Cable: must be DATA (PC plays USB sound on plug). Swap cable/port, no hub.
2. Phone: USB debugging ON, USB mode = File transfer, RSA prompt accepted.
3. `adb kill-server` → `adb start-server` → replug → `status`.
4. Windows "Unknown USB device": install vendor OEM USB driver → replug.
5. Try another PC port (USB 2.0 ports are more forgiving than USB 3.x).

## C. `unauthorized`

1. Unlock phone → accept prompt (Always allow). No prompt?
2. Toggle USB debugging OFF/ON → replug.
3. Developer options → Revoke USB debugging authorizations → replug.
4. (Wi-Fi) authorization is per-transport: accept the wireless prompt too.

## D. `offline`

- `adb kill-server` → replug / `disconnect` + `connect-wifi` again.
- Phone Wi-Fi asleep → keep awake/plugged. Emulator → cold boot.

## E. `wifi-unreachable` — "failed to connect to IP:port"

1. Same Wi-Fi? No guest isolation? Same subnet (compare first 3 octets)?
2. Right CONNECTION port (IP & Port screen), not the pairing port.
3. Disable VPNs on phone+PC temporarily; allow 5555/5037 in Windows Firewall.
4. `ping <phone-ip>` — no ping = no ADB, fix the network first.

## F. `pair-failed`

- Fresh code (single-use, expires in ~1 min), ephemeral pairing port,
  same network. Pairing survives rarely — expect to re-pair after reboots.

## G. `ambiguous-device`

- Pass `-s SERIAL` to every acting command. Unplug what you don't need.

## H. Mirror / scrcpy issues

- "device not found" while adb sees it → second adb server owns it
  (Android Studio/IDE). `adb kill-server`, use ONE server.
- Laggy → `--max-size 1280 --max-fps 30`; USB for timing-sensitive gestures.
- No audio flag errors → older scrcpy: add `--no-audio` (already defaulted
  in `phone.py mirror`).
- Black window on Netflix/banking → DRM/secure surface, by design.

## I. `dump` / `type` quirks

- Empty dump on a buttonful screen → secure window → drive by screenshot.
- `type` drops `& " ' accents/emoji` → ADB `input text` limits → use the
  scrcpy window for passwords/special text.
- Tap "does nothing" → screen didn't change: re-screenshot, adjust, max
  2 blind retries before asking.

## J. Nuclear option (in order)

`adb kill-server` → replug/restart wireless → revoke authorizations →
reboot phone → different cable/port → reinstall platform-tools. Report the
step where `status` output changed.
