---
name: smartphone-use
description: Connect to and control Android phones/tablets and emulators from a PC via USB or Wi-Fi using ADB, mirror the screen with scrcpy, and operate any app, chat, window, or system screen through screenshots and UI dumps. Use when the user asks to connect a phone, mirror/control a mobile screen, automate an Android app, read what's on a phone screen, tap/type/swipe on a device, diagnose adb connection problems, or set up USB/Wireless debugging.
---

# Smartphone Use

Operate a real Android device (or emulator) from this PC: connect it over USB
or Wi-Fi, see its screen, and drive any app, chat, dialog, or system window —
the mobile equivalent of Computer Use.

Stack: **ADB** (transport + shell + input) + **scrcpy** (live mirror for the
human) + **UI dumps** (semantic element lookup, no Appium needed) +
`scripts/phone.py` (the only CLI you need).

## 0. Golden rules

1. **Diagnose before acting.** Always run `phone.py status --json` first and
   name the *cause* (see §3). Never paste a blind sequence of adb commands.
2. **One question at a time.** Ask only for the single datum the current step
   needs (serial? IP? pairing code?) — never dump the whole setup on the user.
3. **USB is the baseline.** If anything wireless fails, fall back to USB: if
   USB works, the problem is the network; if USB fails, wireless never will.
4. **Never expose raw ADB to the Internet.** Port 5555 has no real
   authentication — remote access travels inside SSH or a mesh VPN only
   (`phone.py tunnel`, `references/internet-tunnel.md`).
5. **Confirm visually.** After every 1–3 actions, take a `screenshot` (and
   `dump` when you need element text) and verify the screen actually changed
   before continuing the flow.
6. **AWAKE IS MANDATORY, not optional.** Run `phone.py awake on` immediately
   after EVERY connect — the human WILL walk away and the phone WILL lock.
   Never tap anything before `awake on` confirms. If you later find a
   PIN/password screen, STOP acting: call the human back, wait for the
   unlock, re-run `awake on`, resume from a fresh `observe`. ADB can never
   unlock it for you.
7. **Close cleanly.** End every session with `phone.py awake off` +
   `phone.py disconnect` and tell the user to turn debugging off when they
   are done.

## 1. When to activate / when NOT to activate

Activate when the user says things like: "connect my phone", "mirror my
screen", "control my Android", "open X on my phone and do Y", "read my
phone screen", "adb says unauthorized", "pair wireless debugging".

Do NOT use this skill for:

- **iPhones / iPads** — they need WebDriverAgent + Xcode on a Mac (see
  `references/ios-future.md`). Say so and stop.
- **Exposing ADB over the public Internet** (`adb connect <public-ip>`).
  Refuse the raw form; offer the SSH/VPN recipe instead.
- **Rooting, bootloader unlock, bypassing lock screens, PINs, biometrics,
  DRM, or banking-app protections.** Black screens on DRM video are expected
  behavior, not a bug to circumvent.
- **Bulk spam / scraping chats at scale.** Generic single-user flows are
  fine; automation that violates an app's Terms of Service is not.

## 2. Prerequisites and installation

If `phone.py status` reports `adb-missing` or `scrcpy-missing`:

1. Run `scripts/install-deps.ps1` (Windows, PowerShell 7+): installs
   platform-tools (adb) + scrcpy via winget and the optional `uiautomator2`
   Python package. Idempotent — safe to run twice.
2. Re-run `phone.py status --json` to confirm.
3. If a physical device shows with an empty/garbled serial on Windows, it is
   the classic missing **OEM USB driver** — point the user at their vendor
   driver (Google/Samsung/etc.), then unplug/replug.

Phone side (guide the user step by step, do not paste all at once):

- **USB:** Settings → About phone → tap *Build number* 7× → Developer
  options → enable **USB debugging** → plug in a **data cable** (many cheap
  cables are charge-only) → accept the **"Allow USB debugging?"** RSA prompt,
  ticking *Always allow from this computer*. Full detail:
  `references/usb-setup.md`.
- **Wi-Fi (Android 11+):** phone and PC on the **same** Wi-Fi → Developer
  options → **Wireless debugging ON** → either *Pair device with pairing
  code* (gives an ephemeral **pairing** port + 6-digit code) or read *IP
  address & Port* (the stable **connection** port, usually 5555). Full
  detail: `references/wifi-pairing.md`.
- **Emulator:** start it in Android Studio (or `emulator -avd NAME`); it
  appears as `emulator-5554`. Full detail: `references/emulator.md`.

## 3. Diagnose first — the cause table

`python scripts/phone.py status --json` returns `devices: [{serial, state}]`.
Map the state to a cause, **tell the user the cause in plain language**,
then execute the matching fix. Do not retry the same failing command hoping
for a different result.

| `status` shows | Cause | What to say + do |
|---|---|---|
| `adb` not found | `adb-missing` | "ADB isn't installed." → run `install-deps.ps1`. |
| empty device list (USB) | `no-device` | "The PC doesn't see the phone." → data cable? USB debugging on? RSA prompt accepted? Try another port, File-Transfer USB mode, `adb kill-server`. |
| `unauthorized` | `unauthorized` | "The phone hasn't trusted this PC yet." → unlock phone, accept the RSA prompt; if it never appears: toggle USB debugging off/on, *Revoke USB debugging authorizations*, replug. |
| `offline` | `offline` | "ADB lost the device." → `adb kill-server` + `adb start-server`, replug (USB) or `disconnect`+`connect-wifi` (wireless). |
| 2+ `device` rows, no `-s` | `ambiguous-device` | "I see N devices." → ask which one, then always pass `-s SERIAL`. |
| `connect` says "unable to connect" | `wifi-unreachable` | "I can't reach the phone on the network." → same Wi-Fi? Right **connection** port (not pairing port)? VPN/firewall blocking 5555/5037? See §5. |
| `pair` fails | `pair-failed` | "Pairing didn't complete." → fresh code (they expire), ephemeral pairing port (not 5555), same network. |
| tap `--text` matches nothing | `element-not-found` | "That label isn't on screen." → run `dump`, show nearby labels, or tap by screenshot coordinates. |
| scrcpy black window on video apps | DRM protection | Expected. State it, continue on a non-protected screen. |
| device row but `shell` hangs | stale daemon | `adb kill-server`, wait 3 s, `status` again. |

Prefer semantic recovery (`--text` from a fresh `dump`) over coordinate
replay: layouts shift between screens and OS versions
(`references/uiautomator-vs-coords.md`).

## 4. Connection flows

### Flow A — USB (default, most reliable)

```
1. phone.py status --json
2. If empty: walk the user through §2 USB steps (one question at a time).
3. phone.py connect-usb [-s SERIAL]
4. phone.py awake on  → MANDATORY (rule 6): the screen must stay on while
   you think between steps. Do not proceed until it confirms.
5. phone.py screenshot prove.png  → confirm you see the home screen.
```

### Flow B — Wi-Fi LAN (Android 11+, equal citizen)

```
1. Baseline: Flow A once (proves authorization works at all).
2. Ask for the pairing screen: phone.py pair <IP:PAIR-PORT> <6-DIGIT-CODE>
   ⚠️ The pairing port is EPHEMERAL (e.g. 37891) and ≠ the connection port.
3. Ask for the connection endpoint:
   phone.py connect-wifi <IP:CONNECTION-PORT>   # usually ...:5555
4. phone.py awake on → MANDATORY (rule 6): model latency between steps
   must never meet a locked screen. Do not proceed until it confirms.
5. phone.py screenshot prove.png → confirm.
6. On failure return to Flow A, then re-run `connect-wifi` (authorization
   survives; pairing usually does not need repeating on the same network).
```

Common Wi-Fi traps: PC on Ethernet-VLAN ≠ phone Wi-Fi; guest networks with
client isolation; VPN on either side swallowing LAN; Windows Firewall
blocking inbound 5037/5555; typing the pairing port into `connect-wifi`.

### Flow C — Emulator

Same commands with `-s emulator-5554`. Second instance is `emulator-5556`,
etc. If the emulator shows `offline`, cold-boot it. Detail:
`references/emulator.md`.

### Flow D — Live mirror (for the human)

`phone.py mirror [-s SERIAL]` opens scrcpy: mouse = touch, keyboard types,
right-click = BACK. Use it when the user wants to *watch* or take over
manually; keep driving programmatically via screenshot/dump/tap in parallel.
Tuning flags pass through: `phone.py mirror -- --max-size 1280 --max-fps 30`.

### Flow D2 — Desk mode (agent works, human keeps the phone)

`phone.py desk com.app.pkg` opens the app in a separate scrcpy **virtual
display**: the physical screen stays untouched. Prefer desk over mirror for
long autonomous runs. Detail: `references/device-power.md`.

### Flow E — Secure remote ("over the Internet")

There is exactly one allowed shape, and `phone.py tunnel` prints it:

- **SSH:** remote PC holds the USB phone; `ssh -CN -L5038:localhost:5037
  -R27183:localhost:27183 user@remote-pc` stays open; locally set
  `ADB_SERVER_SOCKET=tcp:localhost:5038` and run scrcpy with
  `--tunnel-host`. Detail: `references/internet-tunnel.md`.
- **Mesh VPN:** Tailscale (or equivalent) on phone + PC, then
  `connect-wifi <vpn-ip>:5555`. Never port-forward 5555 on a router.

If the user insists on raw `adb connect <public-ip>:5555`, refuse and
explain: unauthenticated shell + silent app installs + known in-the-wild
exploitation (see `references/security.md`).

## 5. The control loop (Computer Use for phones)

Operate in this loop; narrate briefly what you see and what you will do:

```
launch com.example.app
  → screenshot + dump
  → find target (prefer --text / resource-id from dump)
  → tap / swipe / type / key
  → screenshot again → did the screen change as expected?
  → yes: next step. no: re-dump (1 retry), then ask the user.
```

Command reference (`phone.py <cmd> --help` for flags; all accept `--json`,
`--dry-run`, `--log-dir DIR`, `--verbose`, `--max-chars N`):

| Goal | Command |
|---|---|
| Diagnose | `status --json` / `fleet` (model, Android, battery, transport) / `eval [--act]` (self-test harness) / `doctor [--fix]` (host+server health, auto-repair) / `diag [--out bundle.zip]` (handoff bundle) |
| First run | `setup [--mode usb\|wifi]` (wizard: guides, waits, proves with screenshot) |
| Stay awake | `awake on` right after connect (screen stays on while you think); `awake off` at the end; `awake status` to inspect |
| Open app | `launch com.android.settings` / find pkg via `packages --filter name` |
| See everything at once | `observe now.png` (screenshot + elements + foreground app in ONE call — prefer over screenshot+dump separately) |
| See screen | `screenshot out.png [--annotate]` (numbered boxes, needs Pillow) |
| Read UI tree | `dump [--out ui.xml]` (JSON lists text/content-desc/bounds, capped at 200; full tree in `--out`) |
| Wait smart | `wait-for --text "Saved" --timeout 20` / `--package PKG [--focused]` / `--change` (never blind-sleep; times out with cause) |
| Locate anything | `ground "blue Send button" [--provider auto\|xml\|vlm\|omniparser]` (no tap; vision fallback when XML is blind) |
| Tap (+verify) | `tap 540 1200` or `tap --text "Settings" [--provider …] [--verify]` |
| Scroll | `swipe 540 1600 540 600` (up); reverse for down; 300 ms default |
| Type | `type "hello world"` (ASCII; quotes/`&`/unicode are skipped — use the scrcpy mirror for passwords) |
| Keys | `key 4` (BACK) `key 3` (HOME) `key 26` (POWER) `key 82` (MENU) |
| Notifications | `notify [--filter app]` (read-only) |
| Clipboard | `clip get` / `clip set "text"` (needs helper app; else `--via type` or scrcpy MOD+v) |
| Files | `files pull/push/ls` |
| Record video | `record demo.mp4 --seconds 15` (no audio, max 180 s) |
| Raw shell | `shell -- <args>` (escape hatch; state why you need it) |
| Mirror | `mirror [-s SERIAL]` / `mirror --otg` (no debugging, no video) |
| Desk mode | `desk com.app.pkg [--size 1280x960]` (separate virtual display; physical screen untouched) |
| Recipes | `recipe new NAME` / `recipe check FILE` / `run FILE [--from-step N] [--shots DIR]` |
| End session | `disconnect [endpoint]` (also resets transport to USB) |

Preview before acting: `--dry-run` prints `would_execute` for any mutating
command; `run … --dry-run` prints the numbered plan. Audit everything with
`--log-dir DIR` (JSONL transcript). Debug the mapping with `--verbose`
(echoes raw adb invocations). Cap floods with `--max-chars N`.
Acting commands (`tap/swipe/key/type/launch`) take `--verify` to report
`ui_changed` after acting. Deterministic flows belong in recipes
(`references/autonomy.md`); one-off exploration stays in the loop above.

Typing passwords: prefer the scrcpy window (real key events, no shell
metacharacter mangling, nothing logged in shell history).

## 6. Error catalog (symptom → cause → exact fix)

- **"No devices/emulators found" / empty list** → `no-device` → data cable,
  USB debugging, RSA prompt, File-Transfer mode, different port, kill-server.
- **"unauthorized"** → `unauthorized` → accept prompt; revoke authorizations
  and re-prompt if stale (`references/troubleshooting.md`).
- **"offline" after Wi-Fi sleep** → `offline` → keep phone awake/plugged,
  `disconnect` + `connect-wifi` again.
- **"failed to connect to IP:port"** → `wifi-unreachable` → §4 Flow B trap
  list; verify with `ping` and same-subnet check.
- **"pairing code expired / wrong"** → `pair-failed` → generate a new code;
  codes are single-use and time-limited.
- **Port confusion (37891 vs 5555)** → explain both numbers out loud and ask
  which screen the user is reading from.
- **`type` garbles `&`, quotes, accents** → expected ADB `input text`
  limits → switch to scrcpy mirror for that field.
- **"`dump` empty on a screen that clearly has buttons"** → secure/flagged
  window (banking, DRM, permission dialogs use non-exported surfaces) →
  `ground "…" --provider vlm` (vision sees pixels where XML is empty) or
  drive by screenshot coordinates.
- **Screenshot black on video/streaming apps** → DRM, by design. Say so.
- **scrcpy "device not found" but adb sees it** → another adb server owns
  it (Android Studio, second terminal) → use one server, or
  `adb kill-server` first.
- **Everything lags on Wi-Fi** → crowded airtime → drop to
  `--max-size 1280 --max-fps 30`, or go back to USB for timing-sensitive
  gestures.
- **Windows sees "Unknown USB device"** → OEM driver missing → install
  vendor driver, replug, `status` again.
- **Screen sleeps/locks mid-session** → you skipped `awake on` → run it now
  (stay-on + 30-min timeout + wake). If a **PIN/password/pattern** prompt is
  up, ADB can never dismiss it by design: ask the human to unlock once, then
  `awake on` keeps it from re-locking. `awake status` shows the policy.
- **Human walked away and it locked (the walk-away playbook).** Assume this
  WILL happen on long tasks. Recovery, in order:
  1. `phone.py awake status --json` → read `keyguard_locked` (and
     `observe` reports it on every read too).
  2. If `true`: STOP all acting commands immediately. Tell the user plainly:
     "phone is locked — unlock it once and tell me".
  3. After they confirm: `phone.py awake on` → fresh `observe` → resume the
     flow from the CURRENT screen (never replay blindly; the app state may
     have changed while locked).
  4. Never tap at a PIN pad "just in case" — wrong guesses can wipe or
     lock out the device.
- **Flaky daemon / missed tap / transient dump failure** → `retry: auto` →
  re-run with `--retries 2` (backoff built in); only `auto` causes repeat.
- **`env-missing` (Pillow etc.)** → host dependency gap, not a device
  problem → `install-deps.ps1` (or `doctor` to list all gaps at once).

If two fixes fail in a row, stop guessing: paste the full
`status --json` + the exact failing command + its output, and ask the user
one targeted question. Full matrix: `references/troubleshooting.md`.

## 7. Session hygiene and safety

- Ask before: installing APKs, granting permissions, signing into accounts,
  spending money, sending messages on the user's behalf, or wiping data.
- Never toggle OEM unlocking, never accept debugging prompts on PCs the user
  doesn't own, never leave Wireless debugging on after the session —
  `disconnect` + `awake off` + Developer-options OFF + *Revoke authorizations*
  on shared machines.
- Treat screenshots/dumps as sensitive: they may contain messages, codes,
  photos. Don't store them beyond the task; don't paste them anywhere else.
- One device at a time: with several attached, every acting command carries
  `-s SERIAL`.

## 8. References (progressive disclosure — load on demand)

- `references/usb-setup.md` — USB debugging + drivers + RSA prompt.
- `references/wifi-pairing.md` — Android 11+ pairing vs connection ports.
- `references/emulator.md` — emulator targets, cold boot, multi-instance.
- `references/internet-tunnel.md` — SSH/VPN remote recipes.
- `references/uiautomator-vs-coords.md` — semantic vs coordinate strategy.
- `references/troubleshooting.md` — full symptom→fix matrix.
- `references/device-power.md` — notify/clip/files/record/desk/fleet/OTG.
- `references/autonomy.md` — recipes, run loop, transcripts, dry-run, eval.
- `references/grounding.md` — xml/vlm/omniparser tiers + Set-of-Mark.
- `references/mcp.md` — MCP server wiring (30 tools, shell gating).
- `references/security.md` — threat model, why 5555 stays on LAN.
- `references/ios-future.md` — why iOS is out of scope (future track).
- `assets/checklist-connection.txt` — one-page user handout.
- `assets/example-flows.md` — copy-paste walkthroughs (settings toggle,
  note-taking app, alarm clock).
