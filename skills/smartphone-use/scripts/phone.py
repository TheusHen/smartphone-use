#!/usr/bin/env python3
"""phone.py — Single CLI entry point for the Smartphone Use skill.

Wraps Android Debug Bridge (ADB) with agent-friendly diagnostics, JSON output,
and safe defaults so an AI agent can connect to and control an Android device
(physical via USB / Wi-Fi LAN, or emulator) without memorizing ADB quirks.

Requires: Android platform-tools (`adb`) on PATH. See install-deps.ps1.
Optional: `uiautomator2` Python package (only used for `--text` resolution
fallback; the default path parses the XML dump with the standard library).

Conventions:
  * Every command accepts `--json` for machine-readable output.
  * Errors always print `CAUSE: <code> | ACTION: <what to do next>` on stderr
    and exit with a non-zero code (see EXIT CODES below).
  * `--serial/-s` targets one device when several are attached. If multiple
    devices are attached and no serial is given, commands fail with
    CAUSE `ambiguous-device` instead of guessing.
  * `--dry-run` prints the planned ADB/scrcpy invocation without executing
    mutating actions. `--log-dir DIR` appends a JSONL transcript of every
    invocation (timestamp, argv, exit code) for auditability.

EXIT CODES:
  0  success
  1  generic failure / ADB error
  2  device unauthorized (RSA prompt not accepted yet)
  3  unreachable (Wi-Fi connect/pair timeout, offline device)
  4  ambiguous (multiple devices, need -s SERIAL)
  5  adb not found (platform-tools missing)
  6  usage error (bad arguments)
  7  element not found (--text matched nothing on screen)

Examples:
  python scripts/phone.py status --json
  python scripts/phone.py connect-usb
  python scripts/phone.py pair 192.168.1.10:37891 482913
  python scripts/phone.py connect-wifi 192.168.1.10:5555
  python scripts/phone.py screenshot screen.png
  python scripts/phone.py dump
  python scripts/phone.py tap --text "Settings"
  python scripts/phone.py launch com.android.settings
  python scripts/phone.py mirror
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

VERSION = "2.3.1"

# Windows legacy consoles default to cp1252, which cannot encode device text
# (accents, emoji in notifications/titles). Prefer UTF-8; best-effort.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
del _stream

# ---------------------------------------------------------------------------
# Cause codes (stable strings the SKILL.md diagnostic table keys off).
# ---------------------------------------------------------------------------
CAUSE_ADB_MISSING = "adb-missing"
CAUSE_NO_DEVICE = "no-device"
CAUSE_UNAUTHORIZED = "unauthorized"
CAUSE_OFFLINE = "offline"
CAUSE_AMBIGUOUS_DEVICE = "ambiguous-device"
CAUSE_WIFI_UNREACHABLE = "wifi-unreachable"
CAUSE_PAIR_FAILED = "pair-failed"
CAUSE_ELEMENT_NOT_FOUND = "element-not-found"
CAUSE_DUMP_FAILED = "dump-failed"
CAUSE_SCRCPY_MISSING = "scrcpy-missing"
CAUSE_TIMEOUT = "timeout"
CAUSE_ENV = "env-missing"

EXIT_CAUSE = {
    CAUSE_ADB_MISSING: 5,
    CAUSE_NO_DEVICE: 1,
    CAUSE_UNAUTHORIZED: 2,
    CAUSE_OFFLINE: 3,
    CAUSE_AMBIGUOUS_DEVICE: 4,
    CAUSE_WIFI_UNREACHABLE: 3,
    CAUSE_PAIR_FAILED: 3,
    CAUSE_ELEMENT_NOT_FOUND: 7,
    CAUSE_DUMP_FAILED: 1,
    CAUSE_SCRCPY_MISSING: 5,
    CAUSE_TIMEOUT: 3,
    CAUSE_ENV: 5,
}

# Who can fix it: "auto" = safe to retry mechanically (stale daemon, timing,
# missed tap); "user" = needs a human (cable, RSA prompt, network, install);
# "no" = the request itself is wrong (bad args, unknown action/package).
RETRY_POLICY = {
    CAUSE_ADB_MISSING: "user",
    CAUSE_NO_DEVICE: "user",
    CAUSE_UNAUTHORIZED: "user",
    CAUSE_OFFLINE: "auto",
    CAUSE_AMBIGUOUS_DEVICE: "user",
    CAUSE_WIFI_UNREACHABLE: "user",
    CAUSE_PAIR_FAILED: "user",
    CAUSE_ELEMENT_NOT_FOUND: "auto",
    CAUSE_DUMP_FAILED: "auto",
    CAUSE_SCRCPY_MISSING: "user",
    CAUSE_TIMEOUT: "auto",
    CAUSE_ENV: "user",
}

_T0: float = time.monotonic()  # reset per invocation in main()
VERBOSE: bool = False
_LAST_ERROR: "PhoneError | None" = None  # last emit_error, for --retries


def envelope() -> dict:
    """Machine-readable log context attached to every JSON result."""
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_ms": int((time.monotonic() - _T0) * 1000),
    }


def fit(text: str, limit: int) -> str:
    """Cap long outputs so they never flood the agent's context window."""
    if len(text) <= limit:
        return text
    cut = limit - 64
    return (text[:cut] + f"\n…[truncated {len(text) - cut} of {len(text)} "
                         f"chars; re-run with --max-chars N]" if cut > 0
            else f"[output {len(text)} chars exceeds --max-chars {limit}]")


class PhoneError(Exception):
    """Structured failure carrying a stable cause code and next action."""

    def __init__(self, cause: str, message: str, action: str):
        super().__init__(message)
        self.cause = cause
        self.message = message
        self.action = action

    def exit_code(self) -> int:
        return EXIT_CAUSE.get(self.cause, 1)


def emit_error(err: PhoneError, as_json: bool) -> int:
    global _LAST_ERROR
    _LAST_ERROR = err
    if as_json:
        print(json.dumps({"ok": False, "cause": err.cause,
                          "message": err.message, "action": err.action,
                          "retry": RETRY_POLICY.get(err.cause, "auto"),
                          **envelope()}, indent=2))
    else:
        print(f"CAUSE: {err.cause} | {err.message}\nACTION: {err.action}",
              file=sys.stderr)
    return err.exit_code()


def emit_ok(payload: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": True, **payload, **envelope()}, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


# ---------------------------------------------------------------------------
# ADB plumbing
# ---------------------------------------------------------------------------
def find_adb() -> str:
    """Locate the adb binary or raise with a helpful cause."""
    found = shutil.which("adb")
    if found:
        return found
    for candidate in (
        os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb"),
        os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise PhoneError(
        CAUSE_ADB_MISSING,
        "The `adb` binary was not found on PATH.",
        "Run scripts/install-deps.ps1 (Windows) or install Android "
        "platform-tools, then re-run `phone.py status`.",
    )


def run_adb(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    adb = find_adb()
    if VERBOSE:
        print(f"+ adb {' '.join(args)} (timeout {timeout}s)",
              file=sys.stderr)
    try:
        return subprocess.run(
            [adb] + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PhoneError(
            CAUSE_TIMEOUT,
            f"adb {' '.join(args)} timed out after {timeout}s.",
            "Check the USB cable / Wi-Fi, then run `phone.py status`.",
        ) from exc
    except OSError as exc:
        raise PhoneError(
            CAUSE_ADB_MISSING,
            f"Could not execute adb: {exc}",
            "Reinstall platform-tools via scripts/install-deps.ps1.",
        ) from exc


def parse_devices() -> list[dict]:
    """Parse `adb devices -l` into a list of device dicts."""
    proc = run_adb(["devices", "-l"])
    devices: list[dict] = []
    for line in proc.stdout.splitlines()[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1] if len(parts) > 1 else "unknown"
        info = {"serial": serial, "state": state}
        for token in parts[2:]:
            if ":" in token:
                key, _, value = token.partition(":")
                info[key] = value
        devices.append(info)
    return devices


def pick_device(serial: str | None) -> dict:
    """Resolve which device to talk to, or raise a precise cause."""
    devices = parse_devices()
    usable = [d for d in devices if d["state"] == "device"]
    if serial:
        match = [d for d in devices if d["serial"] == serial]
        if not match:
            raise PhoneError(
                CAUSE_NO_DEVICE,
                f"No device with serial '{serial}' is visible to adb.",
                "Run `phone.py status` to list attached devices and use "
                "the exact serial with `-s SERIAL`.",
            )
        dev = match[0]
        if dev["state"] == "unauthorized":
            raise PhoneError(
                CAUSE_UNAUTHORIZED,
                f"Device '{serial}' is unauthorized.",
                "Unlock the phone, accept the 'Allow USB debugging?' RSA "
                "prompt (tick 'Always allow'), then retry.",
            )
        if dev["state"] == "offline":
            raise PhoneError(
                CAUSE_OFFLINE,
                f"Device '{serial}' is offline.",
                "Run `adb kill-server`, unplug/replug (or `adb disconnect` "
                "+ `adb connect` for Wi-Fi), then retry.",
            )
        if dev["state"] != "device":
            raise PhoneError(
                CAUSE_NO_DEVICE,
                f"Device '{serial}' is in state '{dev['state']}'.",
                "Run `phone.py status` for details.",
            )
        return dev
    if not devices:
        raise PhoneError(
            CAUSE_NO_DEVICE,
            "No devices are visible to adb.",
            "For USB: enable USB debugging, use a data cable, accept the "
            "RSA prompt. For Wi-Fi: pair first. See references/usb-setup.md "
            "or references/wifi-pairing.md.",
        )
    if len(usable) > 1:
        serials = ", ".join(d["serial"] for d in usable)
        raise PhoneError(
            CAUSE_AMBIGUOUS_DEVICE,
            f"Multiple devices attached ({serials}).",
            "Re-run with `-s SERIAL` to pick one, e.g. "
            f"`phone.py -s {usable[0]['serial']} screenshot out.png`.",
        )
    only = devices[0]
    if only["state"] == "unauthorized":
        raise PhoneError(
            CAUSE_UNAUTHORIZED,
            f"Device '{only['serial']}' is unauthorized.",
            "Unlock the phone and accept the 'Allow USB debugging?' RSA "
            "prompt (tick 'Always allow'), then retry.",
        )
    if only["state"] == "offline":
        raise PhoneError(
            CAUSE_OFFLINE,
            f"Device '{only['serial']}' is offline.",
            "Run `adb kill-server`, unplug/replug (or `adb disconnect` + "
                "`adb connect` for Wi-Fi), then retry.",
        )
    if only["state"] != "device":
        raise PhoneError(
            CAUSE_NO_DEVICE,
            f"The only visible device is in state '{only['state']}'.",
            "Run `phone.py status` for details.",
        )
    return only


def adb_shell(serial: str, *shell_args: str, timeout: int = 30) -> str:
    proc = run_adb(["-s", serial, "shell"] + list(shell_args), timeout=timeout)
    if proc.returncode != 0:
        raise PhoneError(
            CAUSE_NO_DEVICE,
            f"adb shell failed: {proc.stderr.strip() or proc.stdout.strip()}",
            "Run `phone.py status` to check the device state.",
        )
    return proc.stdout


def escape_input_text(text: str) -> str:
    """Escape free text for `adb shell input text`.

    ADB's `input text` treats `%s` as space and breaks on most shell
    metacharacters, so we map spaces and strip characters it cannot type.
    For passwords/unicode prefer typing via an on-device keyboard or the
    scrcpy mirror instead.
    """
    out = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif ch.isascii() and (ch.isalnum() or ch in ".,/_-+:@"):
            out.append(ch)
        # Drop the rest (quotes, &, |, unicode) — they would corrupt the shell.
    return "".join(out)


# ---------------------------------------------------------------------------
# UI dump parsing (semantic taps without Appium)
# ---------------------------------------------------------------------------
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def pull_ui_dump(serial: str) -> ET.Element:
    """Dump the current UI hierarchy and return its XML root."""
    remote = "/sdcard/window_dump.xml"
    out = adb_shell(serial, "uiautomator", "dump", remote).strip()
    if "dumped to" not in out.lower() and "OK" not in out:
        # Some builds print nothing on success; verify by pulling anyway.
        pass
    with tempfile.TemporaryDirectory(prefix="phone_dump_") as tmp:
        local = os.path.join(tmp, "ui.xml")
        proc = run_adb(["-s", serial, "pull", remote, local])
        if proc.returncode != 0 or not os.path.isfile(local):
            raise PhoneError(
                CAUSE_DUMP_FAILED,
                f"Could not pull the UI dump: {proc.stderr.strip()}",
                "Retry after a fresh `screenshot`. On some secure/DRM "
                "screens the hierarchy is intentionally hidden.",
            )
        try:
            tree = ET.parse(local)
            return tree.getroot()
        except ET.ParseError as exc:
            raise PhoneError(
                CAUSE_DUMP_FAILED,
                f"UI dump XML could not be parsed: {exc}",
                "The screen may have changed mid-dump; retry.",
            ) from exc


def find_nodes_by_text(root: ET.Element, query: str) -> list[ET.Element]:
    q = query.lower()
    hits = []
    for node in root.iter("node"):
        text = (node.get("text") or "")
        desc = (node.get("content-desc") or "")
        rid = (node.get("resource-id") or "")
        if q in text.lower() or q in desc.lower() or q in rid.lower():
            hits.append(node)
    return hits


def node_center(node: ET.Element) -> tuple[int, int]:
    m = BOUNDS_RE.search(node.get("bounds") or "")
    if not m:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "Matched element has no usable bounds.",
            "Fall back to coordinates from the latest screenshot.",
        )
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------
def cmd_status(args: argparse.Namespace) -> int:
    try:
        adb = find_adb()
    except PhoneError as err:
        return emit_error(err, args.json)
    ver = run_adb(["--version"])
    devices = parse_devices()
    scrcpy = shutil.which("scrcpy")
    payload = {
        "adb": adb,
        "adb_version": ver.stdout.splitlines()[0] if ver.stdout else "unknown",
        "devices": devices,
        "scrcpy": scrcpy or "not-found",
    }
    if not devices:
        payload["hint"] = ("No devices visible. USB: enable USB debugging + "
                           "accept RSA prompt. Wi-Fi: pair first. See "
                           "references/usb-setup.md / wifi-pairing.md.")
    return emit_ok(payload, args.json)


def cmd_connect_usb(args: argparse.Namespace) -> int:
    try:
        run_adb(["wait-for-device"], timeout=20)
        dev = pick_device(args.serial)
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"connected": dev["serial"], "transport": "usb",
                    "state": dev["state"]}, args.json)


def cmd_pair(args: argparse.Namespace) -> int:
    try:
        find_adb()
    except PhoneError as err:
        return emit_error(err, args.json)
    proc = run_adb(["pair", args.endpoint, args.code], timeout=30)
    combined = (proc.stdout + proc.stderr).lower()
    if "successfully" in combined or "paired" in combined:
        return emit_ok({"paired": args.endpoint,
                        "next": "Now run: phone.py connect-wifi "
                                "<IP:CONNECTION-PORT> (note: the connection "
                                "port differs from the pairing port)."},
                       args.json)
    return emit_error(PhoneError(
        CAUSE_PAIR_FAILED,
        f"Pairing failed: {(proc.stdout + proc.stderr).strip() or 'unknown error'}",
        "Check: same Wi-Fi network? Pairing port is the EPHEMERAL one shown "
        "under 'Pair device with pairing code' (not the connection port). "
        "The pairing code expires — generate a fresh one and retry. "
        "See references/wifi-pairing.md."), args.json)


def cmd_connect_wifi(args: argparse.Namespace) -> int:
    try:
        find_adb()
    except PhoneError as err:
        return emit_error(err, args.json)
    proc = run_adb(["connect", args.endpoint], timeout=20)
    combined = (proc.stdout + proc.stderr).strip()
    # Verify it really reached `device` state (adb prints "connected" loosely).
    time.sleep(1)
    devices = parse_devices()
    host = args.endpoint.split(":")[0]
    match = [d for d in devices
             if d["serial"] == args.endpoint or d["serial"].startswith(host)]
    if any(d["state"] == "device" for d in match):
        return emit_ok({"connected": args.endpoint, "transport": "wifi",
                        "raw": combined}, args.json)
    if any(d["state"] == "unauthorized" for d in match):
        return emit_error(PhoneError(
            CAUSE_UNAUTHORIZED,
            f"Device at {args.endpoint} is unauthorized.",
            "Accept the debugging prompt on the phone, then retry."),
            args.json)
    return emit_error(PhoneError(
        CAUSE_WIFI_UNREACHABLE,
        f"Could not reach {args.endpoint}: {combined or 'no response'}",
        "Check: same Wi-Fi? Correct CONNECTION port (Settings -> Wireless "
        "debugging -> IP address & Port, not the pairing port)? Firewall/VPN "
        "blocking port 5555? See references/wifi-pairing.md."), args.json)


def cmd_disconnect(args: argparse.Namespace) -> int:
    try:
        find_adb()
    except PhoneError as err:
        return emit_error(err, args.json)
    target = [args.endpoint] if args.endpoint else []
    proc = run_adb(["disconnect"] + target)
    # Return the transport to USB mode so the phone stops listening on TCP.
    run_adb(["usb"])
    return emit_ok({"disconnected": args.endpoint or "all",
                    "raw": (proc.stdout + proc.stderr).strip(),
                    "note": "Transport reset to USB. Run `phone.py awake "
                            "off` to restore sleep settings, and disable "
                            "Wireless/USB debugging on the phone when "
                            "finished."},
                   args.json)


def cmd_screenshot(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
    except PhoneError as err:
        return emit_error(err, args.json)
    proc = run_adb(["-s", dev["serial"], "exec-out", "screencap", "-p"])
    if proc.returncode != 0 or not proc.stdout:
        return emit_error(PhoneError(
            CAUSE_NO_DEVICE,
            "Screenshot capture failed (DRM-protected screens return black "
            "or fail — this is expected on some video/banking apps).",
            "Retry on a non-protected screen."), args.json)
    # Binary-safe write: re-run via bytes-capable path if text mangled it.
    raw = subprocess.run(
        [find_adb(), "-s", dev["serial"], "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30).stdout
    # Normalize CRLF corruption that `exec-out` can introduce on Windows.
    raw = raw.replace(b"\r\n", b"\n")
    if getattr(args, "annotate", False):
        try:
            raw = annotate_png(raw, clickable_marks(dev["serial"]))
        except PhoneError as err:
            return emit_error(err, args.json)
    try:
        with open(args.output, "wb") as fh:
            fh.write(raw)
    except OSError as exc:
        return emit_error(PhoneError(
            CAUSE_NO_DEVICE, f"Cannot write {args.output}: {exc}",
            "Use a writable output path."), args.json)
    return emit_ok({"screenshot": os.path.abspath(args.output),
                    "serial": dev["serial"]}, args.json)


def cmd_dump(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        root = pull_ui_dump(dev["serial"])
    except PhoneError as err:
        return emit_error(err, args.json)
    nodes = []
    for node in root.iter("node"):
        text = node.get("text") or ""
        desc = node.get("content-desc") or ""
        rid = node.get("resource-id") or ""
        cls = node.get("class") or ""
        if text or desc:
            nodes.append({"text": text, "content_desc": desc,
                          "resource_id": rid, "class": cls,
                          "bounds": node.get("bounds") or "",
                          "clickable": node.get("clickable")})
    payload = {"serial": dev["serial"], "count": len(nodes)}
    if args.out:
        try:
            ET.ElementTree(root).write(args.out, encoding="utf-8",
                                       xml_declaration=True)
            payload["xml"] = os.path.abspath(args.out)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_DUMP_FAILED, f"Cannot write {args.out}: {exc}",
                "Use a writable output path."), args.json)
    payload["elements"] = nodes[:200]  # cap: full tree lives in --out XML
    if len(nodes) > 200:
        payload["truncated"] = f"showing 200 of {len(nodes)}; see {args.out}"
    return emit_ok(payload, args.json)


def do_tap(serial: str, x: int, y: int) -> None:
    adb_shell(serial, "input", "tap", str(x), str(y))


def cmd_tap(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        if args.text:
            x, y, _provider = resolve_ground(
                dev["serial"], args.text, getattr(args, "provider", "xml"))
        else:
            if args.x is None or args.y is None:
                raise PhoneError(
                    CAUSE_ELEMENT_NOT_FOUND,
                    "Provide coordinates (X Y) or --text 'label'.",
                    "Example: `phone.py tap 540 1200` or "
                    "`phone.py tap --text \"Settings\"`.")
            x, y = args.x, args.y
        if wants_dry(args):
            return dry_emit(args, f"adb -s {dev['serial']} shell input "
                                  f"tap {x} {y}")
        do_tap(dev["serial"], x, y)
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"tapped": [x, y], "serial": dev["serial"],
                    **check_verify(args, dev["serial"],
                                   getattr(args, "_verify_before", None))},
                   args.json)


def cmd_swipe(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        if wants_dry(args):
            return dry_emit(
                args, f"adb -s {dev['serial']} shell input swipe "
                f"{args.x1} {args.y1} {args.x2} {args.y2} {args.ms}")
        adb_shell(dev["serial"], "input", "swipe",
                  str(args.x1), str(args.y1), str(args.x2), str(args.y2),
                  str(args.ms))
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"swiped": [args.x1, args.y1, args.x2, args.y2],
                    "duration_ms": args.ms,
                    **check_verify(args, dev["serial"],
                                   getattr(args, "_verify_before", None))},
                   args.json)


def cmd_key(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        if wants_dry(args):
            return dry_emit(args, f"adb -s {dev['serial']} shell input "
                                  f"keyevent {args.code}")
        adb_shell(dev["serial"], "input", "keyevent", str(args.code))
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"keyevent": args.code,
                    "hint": "3=HOME 4=BACK 26=POWER 82=MENU 84=SEARCH",
                    **check_verify(args, dev["serial"],
                                   getattr(args, "_verify_before", None))},
                   args.json)


def cmd_type(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        safe = escape_input_text(args.text)
        if not safe:
            raise PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                "Text contained only characters ADB cannot type.",
                "Tap the field and type via the scrcpy mirror instead.")
        if wants_dry(args):
            return dry_emit(args, f"adb -s {dev['serial']} shell input "
                                  "text …")
        adb_shell(dev["serial"], "input", "text", safe)
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"typed": args.text, "serial": dev["serial"],
                    "note": "Spaces are sent as %s; quotes/unicode are "
                            "skipped — use the scrcpy mirror for those.",
                    **check_verify(args, dev["serial"],
                                   getattr(args, "_verify_before", None))},
                   args.json)


def cmd_launch(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        if wants_dry(args):
            return dry_emit(
                args, f"adb -s {dev['serial']} shell monkey -p "
                f"{args.package} -c android.intent.category.LAUNCHER 1")
        out = adb_shell(
            dev["serial"], "monkey", "-p", args.package,
            "-c", "android.intent.category.LAUNCHER", "1")
    except PhoneError as err:
        return emit_error(err, args.json)
    if "No activities found" in out:
        return emit_error(PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"Package '{args.package}' is not installed or has no launcher.",
            "Run `phone.py packages --filter <name>` to find the package."),
            args.json)
    return emit_ok({"launched": args.package,
                    **check_verify(args, dev["serial"],
                                   getattr(args, "_verify_before", None))},
                   args.json)


def cmd_packages(args: argparse.Namespace) -> int:
    try:
        dev = pick_device(args.serial)
        out = adb_shell(dev["serial"], "pm", "list", "packages", "-3")
    except PhoneError as err:
        return emit_error(err, args.json)
    pkgs = [line.partition(":")[2].strip()
            for line in out.splitlines() if line.startswith("package:")]
    if args.filter:
        q = args.filter.lower()
        pkgs = [p for p in pkgs if q in p.lower()]
    limit = getattr(args, "max_chars", 8000)
    payload: dict = {"packages": pkgs, "count": len(pkgs)}
    rendered = json.dumps(payload)
    if len(rendered) > limit:
        payload = {"packages": pkgs[:50], "count": len(pkgs),
                   "note": fit(f"list capped at 50 of {len(pkgs)}; "
                               f"narrow with --filter.", limit)}
    return emit_ok(payload, args.json)


def cmd_shell(args: argparse.Namespace) -> int:
    """Escape hatch: run a raw `adb shell` command (agent must justify use)."""
    try:
        dev = pick_device(args.serial)
        if wants_dry(args):
            return dry_emit(args, "adb -s {} shell {}".format(
                dev["serial"], " ".join(args.command)))
        out = adb_shell(dev["serial"], *args.command, timeout=args.timeout)
    except PhoneError as err:
        return emit_error(err, args.json)
    limit = getattr(args, "max_chars", 8000)
    return emit_ok({"output": fit(out.rstrip(), limit)}, args.json)


def cmd_mirror(args: argparse.Namespace) -> int:
    scrcpy = shutil.which("scrcpy")
    if not scrcpy:
        return emit_error(PhoneError(
            CAUSE_SCRCPY_MISSING,
            "scrcpy was not found on PATH.",
            "Run scripts/install-deps.ps1, then retry."), args.json)
    if getattr(args, "otg", False):
        # OTG simulates a physical keyboard/mouse over HID: no ADB, no
        # debugging needed — but also no mirroring (look at the device).
        cmd = [scrcpy, "--otg"]
        if args.json:
            return emit_ok({"otg_command": " ".join(cmd),
                            "note": "No mirroring in OTG mode; control a "
                                    "device you look at directly."},
                           args.json)
        print("Launching: " + " ".join(cmd))
        try:
            subprocess.run(cmd, check=False)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_SCRCPY_MISSING, f"Could not start scrcpy: {exc}",
                "Reinstall scrcpy via scripts/install-deps.ps1."), args.json)
        return 0
    try:
        dev = pick_device(args.serial)
    except PhoneError as err:
        return emit_error(err, args.json)
    cmd = [scrcpy, "-s", dev["serial"]]
    if args.no_audio:
        cmd.append("--no-audio")
    if args.extra:
        cmd.extend(args.extra)
    if args.json:
        return emit_ok({"mirror_command": " ".join(cmd)}, args.json)
    if getattr(args, "detach", False):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_SCRCPY_MISSING, f"Could not start scrcpy: {exc}",
                "Reinstall scrcpy via scripts/install-deps.ps1."), args.json)
        return emit_ok({"mirror_pid": proc.pid,
                        "serial": dev["serial"]}, args.json)
    print("Launching: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=False)
    except OSError as exc:
        return emit_error(PhoneError(
            CAUSE_SCRCPY_MISSING, f"Could not start scrcpy: {exc}",
            "Reinstall scrcpy via scripts/install-deps.ps1."), args.json)
    return 0


def cmd_tunnel(args: argparse.Namespace) -> int:
    """Print (never silently execute) the secure remote-access recipe."""
    if args.via == "ssh":
        recipe = {
            "step_1": "On the remote PC (phone plugged in): `adb start-server`",
            "step_2": ("Keep open: `ssh -CN -L5038:localhost:5037 "
                       f"-R27183:localhost:27183 {args.remote}`"),
            "step_3": ("Locally: `set ADB_SERVER_SOCKET=tcp:localhost:5038` "
                       "then `scrcpy --tunnel-host=localhost` (see "
                       "references/internet-tunnel.md)."),
            "warning": ("NEVER expose raw ADB port 5555 to the internet. "
                        "All traffic must travel inside this SSH tunnel."),
        }
    else:
        recipe = {
            "step_1": "Install a mesh VPN (e.g. Tailscale) on BOTH the phone "
                      "and the PC.",
            "step_2": "Join both to the same private tailnet.",
            "step_3": ("`phone.py connect-wifi <PHONE-TAILSCALE-IP>:5555` — "
                       "the ADB traffic now travels inside WireGuard "
                       "encryption."),
            "warning": ("NEVER port-forward 5555 on your router. The VPN "
                        "address is reachable only inside your tailnet."),
        }
    return emit_ok({"via": args.via, **recipe}, args.json)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# V2: dry-run, transcripts, vision grounding, device power, autonomy.
# Design goals: stdlib-only (fast, stable), every provider optional with
# graceful fallback, mutating actions support --dry-run, every invocation
# can be transcribed with --log-dir.
# ---------------------------------------------------------------------------
def wants_dry(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False))


def dry_emit(args: argparse.Namespace, planned: str) -> int:
    return emit_ok({"dry_run": True, "would_execute": planned},
                   getattr(args, "json", False))


# --- binary screenshot capture (shared by screenshot/ground/run/eval) ------
def capture_png(serial: str) -> bytes:
    proc = subprocess.run(
        [find_adb(), "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30)
    raw = proc.stdout.replace(b"\r\n", b"\n")
    if proc.returncode != 0 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise PhoneError(
            CAUSE_NO_DEVICE,
            "Screenshot capture failed (DRM-protected screens return black "
            "or fail — expected on some video/banking apps).",
            "Retry on a non-protected screen.")
    return raw


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) < 24:
        raise PhoneError(
            CAUSE_DUMP_FAILED,
            "Screenshot bytes are not a valid PNG.",
            "Re-run `screenshot` and retry.")
    return struct.unpack(">II", png[16:24])  # IHDR width, height


# --- vision grounding providers --------------------------------------------
def ground_with_xml(serial: str, query: str) -> tuple[int, int, str]:
    """Tier 1 (built-in, instant): resolve via uiautomator XML dump."""
    root = pull_ui_dump(serial)
    hits = [n for n in find_nodes_by_text(root, query)
            if n.get("clickable") == "true"]
    hits = hits or find_nodes_by_text(root, query)
    if not hits:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"No on-screen element matches '{query}'.",
            "Run `phone.py dump` to see available text, tap by coordinates, "
            "or retry with --provider vlm/omniparser (see "
            "references/grounding.md).")
    return (*node_center(hits[0]), "xml")


def ground_with_vlm(png: bytes, query: str) -> tuple[int, int, str]:
    """Tier 3: any OpenAI-compatible vision endpoint (UI-TARS/Qwen-VL/GPT).

    Configure with VLM_GROUND_URL (+ optional VLM_GROUND_KEY,
    VLM_GROUND_MODEL). The model must return {"x":..,"y":..} in 0-1000
    relative coordinates.
    """
    url = os.environ.get("VLM_GROUND_URL", "")
    if not url:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "VLM grounding requested but VLM_GROUND_URL is not set.",
            "Export VLM_GROUND_URL (+ VLM_GROUND_KEY, VLM_GROUND_MODEL) per "
            "references/grounding.md, or use --provider xml.")
    key = os.environ.get("VLM_GROUND_KEY", "")
    model = os.environ.get("VLM_GROUND_MODEL", "auto")
    width, height = png_size(png)
    b64 = base64.b64encode(png).decode("ascii")
    prompt = (
        'Locate the UI element the user wants: "%s". Reply with ONLY compact '
        'JSON like {"x": 512, "y": 300} using 0-1000 relative coordinates '
        "(x right, y down)." % query)
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + b64}}]}]},
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"VLM grounding endpoint unreachable: {exc}",
            "Check VLM_GROUND_URL (reachable? right path, e.g. "
            "/v1/chat/completions?) and retry.") from exc
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "VLM endpoint returned an unexpected shape (need OpenAI-"
            "compatible chat completions).",
            "See references/grounding.md for the required contract."
        ) from exc
    match = re.search(r'"x"\s*:\s*(\d+(?:\.\d+)?)[^}]*"y"\s*:\s*'
                      r'(\d+(?:\.\d+)?)', text)
    if not match:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"VLM reply was not coordinates: {text[:200]}",
            "Retry with a more specific query (color, nearby label).")
    return (int(float(match.group(1)) / 1000 * width),
            int(float(match.group(2)) / 1000 * height), "vlm")


def ground_with_omniparser(png: bytes, query: str) -> tuple[int, int, str]:
    """Tier 2: self-hosted OmniParser-style service.

    Contract (see references/grounding.md): POST {"image": base64png,
    "query": "..."} to $OMNIPARSER_URL, expect
    {"elements": [{"x": px, "y": py, "label": "..."}]}.
    """
    url = os.environ.get("OMNIPARSER_URL", "")
    if not url:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "OmniParser grounding requested but OMNIPARSER_URL is not set.",
            "Start your parser service and export OMNIPARSER_URL per "
            "references/grounding.md, or use --provider xml.")
    body = json.dumps({"image": base64.b64encode(png).decode("ascii"),
                       "query": query}).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"OmniParser endpoint unreachable: {exc}",
            "Check OMNIPARSER_URL and that the service is running.") from exc
    elements = data.get("elements") if isinstance(data, dict) else None
    if not elements:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "OmniParser endpoint returned no elements (need "
            '{"elements": [{"x","y","label"}]}).',
            "See references/grounding.md for the required contract.")
    query_low = query.lower()
    for element in elements:
        label = str(element.get("label", ""))
        if query_low in label.lower():
            return int(element["x"]), int(element["y"]), "omniparser"
    labels = [str(e.get("label", ""))[:40] for e in elements[:20]]
    raise PhoneError(
        CAUSE_ELEMENT_NOT_FOUND,
        f"No parsed element matches '{query}'. Visible: {'; '.join(labels)}",
        "Pick one of the listed labels or tap by coordinates.")


def resolve_ground(serial: str, query: str,
                   provider: str = "auto") -> tuple[int, int, str]:
    """Resolve a natural-language query to screen pixels.

    `auto` tries XML first (instant, offline) and falls back to configured
    remote providers. Explicit providers fail loudly when unconfigured.
    """
    provider = (provider or "auto").lower()
    if provider == "xml":
        return ground_with_xml(serial, query)
    if provider == "vlm":
        return ground_with_vlm(capture_png(serial), query)
    if provider == "omniparser":
        return ground_with_omniparser(capture_png(serial), query)
    if provider != "auto":
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"Unknown grounding provider '{provider}'.",
            "Use xml, vlm, omniparser, or auto.")
    try:
        return ground_with_xml(serial, query)
    except PhoneError as xml_err:
        last: PhoneError = xml_err
        if os.environ.get("VLM_GROUND_URL"):
            try:
                return ground_with_vlm(capture_png(serial), query)
            except PhoneError as err:
                last = err
        if os.environ.get("OMNIPARSER_URL"):
            try:
                return ground_with_omniparser(capture_png(serial), query)
            except PhoneError as err:
                last = err
        raise last


# --- Set-of-Mark style annotation (needs Pillow, optional) ------------------
def annotate_png(png: bytes, marks: list[tuple]) -> bytes:
    """Draw numbered boxes; marks = [(x1,y1,x2,y2,label), ...]."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise PhoneError(
            CAUSE_DUMP_FAILED,
            "Annotation needs Pillow (`python -m pip install pillow`).",
            "Install pillow (bundled in install-deps.ps1) or drop "
            "--annotate.") from exc
    import io
    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for i, (x1, y1, x2, y2, label) in enumerate(marks):
        draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=3)
        draw.text((x1 + 4, max(y1 - 14, 0)), f"{i}: {label}",
                  fill=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def clickable_marks(serial: str, limit: int = 60) -> list[tuple]:
    root = pull_ui_dump(serial)
    marks = []
    for node in root.iter("node"):
        if node.get("clickable") != "true":
            continue
        match = BOUNDS_RE.search(node.get("bounds") or "")
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        label = (node.get("text") or node.get("content-desc") or
                 node.get("resource-id") or "")[:28]
        marks.append((x1, y1, x2, y2, label))
        if len(marks) >= limit:
            break
    return marks


# --- first-run and self-healing ------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the host + ADB server side; --fix repairs what's safe."""
    report: dict = {"checks": [], "fixed": []}

    def check(name: str, fn, fix=None) -> None:
        try:
            report["checks"].append({"name": name, "ok": True,
                                     "detail": str(fn())})
        except PhoneError as err:
            entry = {"name": name, "ok": False,
                     "detail": f"{err.cause}: {err.message}"}
            if args.fix and fix is not None:
                try:
                    entry["fixed"] = str(fix())
                    report["fixed"].append(name)
                except PhoneError as ferr:
                    entry["fix_failed"] = (f"{ferr.cause}: {ferr.message}")
            report["checks"].append(entry)

    def server_ok():
        proc = run_adb(["start-server"])
        if proc.returncode != 0:
            raise PhoneError(CAUSE_TIMEOUT, "ADB server won't start.",
                             "Reinstall platform-tools.")
        return "server running"

    def server_restart():
        run_adb(["kill-server"])
        run_adb(["start-server"])
        return "server restarted"

    def devices_ok():
        devs = parse_devices()
        bad = [d["serial"] for d in devs if d["state"] != "device"]
        if bad:
            raise PhoneError(
                CAUSE_OFFLINE,
                f"Unhealthy devices: {', '.join(bad)}.",
                "Unplug/replug (USB) or disconnect+connect (Wi-Fi); "
                "`--fix` restarts the server.")
        return f"{len(devs)} visible, all healthy"

    def tcp_ok():
        devs = parse_devices()
        stale = [d["serial"] for d in devs
                 if re.match(r"^\d+\.\d+\.\d+\.\d+", d["serial"])
                 and d["state"] != "device"]
        if stale:
            raise PhoneError(CAUSE_WIFI_UNREACHABLE,
                             f"Stale Wi-Fi entries: {', '.join(stale)}.",
                             "`--fix` disconnects them.")
        return "no stale Wi-Fi entries"

    def tcp_fix():
        run_adb(["disconnect"])
        return "disconnected all Wi-Fi endpoints"

    def scrcpy_ok():
        path = shutil.which("scrcpy")
        if not path:
            raise PhoneError(CAUSE_SCRCPY_MISSING, "scrcpy not on PATH.",
                             "Run scripts/install-deps.ps1.")
        return path

    def pillow_ok():
        try:
            import PIL
            return f"pillow {PIL.__version__}"
        except ImportError as exc:
            raise PhoneError(
                CAUSE_ENV,
                "Pillow not installed (--annotate unavailable).",
                "Run scripts/install-deps.ps1.") from exc

    check("adb-present", lambda: find_adb() and "adb found")
    check("adb-server", server_ok, server_restart)
    check("devices", devices_ok, server_restart)
    check("wifi-leftovers", tcp_ok, tcp_fix)
    check("scrcpy", scrcpy_ok)
    check("pillow", pillow_ok)
    check("python", lambda: sys.version.split()[0])
    code = emit_ok(report, args.json)
    failed = sum(1 for c in report["checks"] if not c["ok"])
    return code if failed == 0 else 1


def cmd_setup(args: argparse.Namespace) -> int:
    """First-run wizard: instruct the human, wait, prove with a screenshot."""
    def say(msg: str) -> None:
        print(msg, file=sys.stderr)

    if args.mode == "wifi":
        say("SETUP (Wi-Fi): 1) Do the USB flow once first. 2) Same Wi-Fi on "
            "phone+PC. 3) Developer options → Wireless debugging ON. "
            "4) Tell me the pairing endpoint+code, then the connection "
            "endpoint when I ask. Waiting for a device…")
    else:
        say("SETUP (USB): 1) Settings → About phone → tap Build number 7×. "
            "2) Developer options → USB debugging ON. 3) Plug a DATA cable. "
            "4) Accept 'Allow USB debugging?' on the phone (Always allow). "
            "Waiting for a device…")
    deadline = time.monotonic() + max(args.timeout, 10)
    warned_auth = warned_empty = False
    while time.monotonic() < deadline:
        try:
            devs = parse_devices()
        except PhoneError:
            devs = []
        ready = [d for d in devs if d["state"] == "device"]
        if ready:
            serial = ready[0]["serial"]
            proof = None
            try:
                proof = os.path.abspath("setup-proof.png")
                with open(proof, "wb") as fh:
                    fh.write(capture_png(serial))
            except (PhoneError, OSError):
                proof = None
            return emit_ok({"connected": serial,
                            "proof": proof or "screenshot skipped",
                            "next": "Run `phone.py observe obs.png`."},
                           args.json)
        if any(d["state"] == "unauthorized" for d in devs) \
                and not warned_auth:
            say("I see the phone but it hasn't trusted this PC: unlock it "
                "and accept 'Allow USB debugging?' (tick Always allow).")
            warned_auth = True
        if not devs and not warned_empty:
            say("No device yet — check cable (data, not charge-only), USB "
                "debugging ON, and the RSA prompt.")
            warned_empty = True
        time.sleep(2)
    return emit_error(PhoneError(
        CAUSE_NO_DEVICE,
        f"No usable device after {args.timeout}s.",
        "See references/usb-setup.md (or wifi-pairing.md), then re-run "
        "`phone.py setup`."), args.json)


def cmd_diag(args: argparse.Namespace) -> int:
    """Support bundle for handoffs: status + read-only eval + versions."""
    def self_json(*cmd: str) -> dict:
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), *cmd],
                capture_output=True, text=True, timeout=180)
            return json.loads(proc.stdout or "{}")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"error": "subcommand failed to produce JSON"}

    try:
        import PIL
        pillow = PIL.__version__
    except ImportError:
        pillow = "missing"
    bundle = {
        "phone_version": VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "scrcpy": shutil.which("scrcpy") or "missing",
        "pillow": pillow,
        "status": self_json("status", "--json"),
        "eval": self_json("eval", "--json"),
    }
    if args.out:
        import zipfile
        try:
            with zipfile.ZipFile(args.out, "w",
                                 zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("diag.json",
                            json.dumps(bundle, indent=2))
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_DUMP_FAILED, f"Cannot write {args.out}: {exc}",
                "Use a writable output path."), args.json)
        return emit_ok({"bundle": os.path.abspath(args.out),
                        "next": "Attach this file when asking for help."},
                       args.json)
    return emit_ok(bundle, args.json)


# --- observation helpers: one bundled read, change detection ----------------
def text_signature(serial: str) -> frozenset:
    """Hashable set of visible texts — cheap, stdlib-only screen fingerprint."""
    root = pull_ui_dump(serial)
    return frozenset(
        ((node.get("text") or "") + "\u0000" +
         (node.get("content-desc") or "")).strip("\u0000")
        for node in root.iter("node")
        if (node.get("text") or node.get("content-desc")))


def focused_package(serial: str) -> str:
    """Best-effort foreground package. Never raises (returns '' unknown)."""
    try:
        out = adb_shell(serial, "dumpsys", "window", "windows", timeout=15)
    except PhoneError:
        return ""
    for pattern in (r"mCurrentFocus=Window\{[^}]* ([A-Za-z0-9_.]+)/",
                    r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/"):
        match = re.search(pattern, out)
        if match:
            return match.group(1)
    return ""


def keyguard_locked(serial: str):
    """Best-effort lockscreen state. True/False, or None when unknown.

    Lets the agent detect 'the human walked away and it locked' instead of
    tapping blindly at a PIN pad ADB can never pass.
    """
    try:
        out = adb_shell(serial, "dumpsys", "window", "windows", timeout=15)
    except PhoneError:
        return None
    if re.search(r"mShowingLockscreen=true|mDreamingLockscreen=true|"
                 r"mKeyguardLocked=true", out):
        return True
    if "mShowingLockscreen=false" in out:
        return False
    return None


def list_text_nodes(serial: str, limit: int = 60) -> list[dict]:
    root = pull_ui_dump(serial)
    items = []
    for node in root.iter("node"):
        text = node.get("text") or ""
        desc = node.get("content-desc") or ""
        if not (text or desc):
            continue
        items.append({"text": text, "content_desc": desc,
                      "resource_id": node.get("resource-id") or "",
                      "bounds": node.get("bounds") or "",
                      "clickable": node.get("clickable")})
        if len(items) >= limit:
            break
    return items


def pixel_change(before_png: bytes, after_png: bytes) -> float | None:
    """Mean % pixel difference (Pillow). None when Pillow is absent."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return None
    import io
    try:
        before = Image.open(io.BytesIO(before_png)).convert("L")
        after = Image.open(io.BytesIO(after_png)).convert("L")
        if before.size != after.size:
            after = after.resize(before.size)
        diff = ImageChops.difference(before, after)
        stat = ImageStat.Stat(diff)
        return round(sum(stat.mean) / len(stat.mean) / 255 * 100, 1)
    except Exception:
        return None


def snap_state(serial: str) -> tuple:
    """Best-effort (signature, png); each side may be None. Never raises."""
    try:
        sig = text_signature(serial)
    except PhoneError:
        sig = None
    try:
        png = capture_png(serial)
    except PhoneError:
        png = None
    return (sig, png)


def check_verify(args: argparse.Namespace, serial: str,
                 before: tuple | None) -> dict:
    """Post-action verification for --verify: did the screen react?"""
    if not getattr(args, "verify", False) or before is None:
        return {}
    time.sleep(0.8)  # let transitions/animations settle
    after = snap_state(serial)
    before_sig, before_png = before
    after_sig, after_png = after
    changed = (before_sig is not None and after_sig is not None
               and before_sig != after_sig)
    pct = None
    if before_png and after_png:
        pct = pixel_change(before_png, after_png)
        if pct is not None and pct > 2.0:
            changed = True
    return {"verify": {
        "ui_changed": changed,
        "changed_pct": pct,
        "hint": ("Screen reacted — proceed." if changed
                 else "No detectable change: tap may have missed, app is "
                      "loading (wait-for --change), or the screen is static "
                      "(then this is expected).")}}


def cmd_observe(args: argparse.Namespace) -> int:
    """One bundled read: screenshot + elements + foreground app + size."""
    try:
        dev = pick_device(args.serial)
        serial = dev["serial"]
        png = capture_png(serial)
        width, height = png_size(png)
        if getattr(args, "annotate", False):
            png = annotate_png(png, clickable_marks(serial))
        try:
            with open(args.output, "wb") as fh:
                fh.write(png)
        except OSError as exc:
            raise PhoneError(
                CAUSE_DUMP_FAILED, f"Cannot write {args.output}: {exc}",
                "Use a writable output path.") from exc
        elements = list_text_nodes(
            serial, limit=getattr(args, "max_elements", 60))
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"screenshot": os.path.abspath(args.output),
                    "screen": [width, height],
                    "focused_package": focused_package(serial),
                    "keyguard_locked": keyguard_locked(serial),
                    "serial": serial,
                    "elements": elements,
                    "element_count": len(elements)}, args.json)


def cmd_wait_for(args: argparse.Namespace) -> int:
    """Block until a screen condition holds (or timeout with cause)."""
    try:
        dev = pick_device(args.serial)
        serial = dev["serial"]
    except PhoneError as err:
        return emit_error(err, args.json)
    deadline = time.monotonic() + max(args.timeout, 1)
    baseline = None
    if args.change:
        try:
            baseline = text_signature(serial)
        except PhoneError as err:
            return emit_error(err, args.json)
    waited = 0.0
    while True:
        try:
            if args.text and find_nodes_by_text(pull_ui_dump(serial),
                                                args.text):
                return emit_ok({"satisfied": f"text:{args.text}",
                                "waited_s": round(waited, 1)}, args.json)
            if args.package:
                alive = adb_shell(serial, "pidof",
                                  args.package).strip()
                focused = focused_package(serial)
                if alive and (not args.focused or focused == args.package):
                    return emit_ok(
                        {"satisfied": f"package:{args.package}",
                         "focused": focused or "unknown",
                         "waited_s": round(waited, 1)}, args.json)
            if args.change and text_signature(serial) != baseline:
                return emit_ok({"satisfied": "screen-changed",
                                "waited_s": round(waited, 1)}, args.json)
        except PhoneError as err:
            return emit_error(err, args.json)
        if time.monotonic() >= deadline:
            return emit_error(PhoneError(
                CAUSE_TIMEOUT,
                f"wait-for timed out after {args.timeout}s "
                f"(text={args.text} package={args.package} "
                f"change={args.change}).",
                "The app may need longer (raise --timeout), the tap missed "
                "(re-observe and retry), or the flow went elsewhere "
                "(screenshot and reassess)."), args.json)
        time.sleep(max(args.interval, 0.5))
        waited = max(args.timeout, 1) - max(deadline - time.monotonic(), 0)


# --- device power commands ---------------------------------------------------
AWAKE_TIMEOUT_MS = "1800000"  # 30 min — highest most OEMs honor
SLEEP_TIMEOUT_MS = "60000"    # sane restore default (1 min)


def cmd_awake(args: argparse.Namespace) -> int:
    """Keep the screen on for agent sessions. Reversible, no root.

    - `on`: stay-on-while-plugged + 30-min timeout + wake + dismiss
      swipe-keyguard. Covers the minutes the model spends "thinking"
      between steps.
    - `off`: restores stay-off + 1-min timeout.
    - `status`: reports current sleep policy + wakefulness.
    A PIN/password/pattern lock can NEVER be dismissed by ADB (by design) —
    the human unlocks once, `awake on` keeps it from re-locking.
    """
    try:
        if args.action == "status":
            dev = pick_device(args.serial)
            serial = dev["serial"]
            stay = adb_shell(serial, "settings", "get", "global",
                             "stay_on_while_plugged_in").strip()
            timeout = adb_shell(serial, "settings", "get", "system",
                                "screen_off_timeout").strip()
            try:
                power = adb_shell(serial, "dumpsys", "power", timeout=15)
                wake = re.search(r"mWakefulness=(\w+)", power)
                wakefulness = wake.group(1) if wake else "unknown"
            except PhoneError:
                wakefulness = "unknown"
            locked = keyguard_locked(serial)
            payload = {"stay_on_while_plugged_in": stay,
                       "screen_off_timeout_ms": timeout,
                       "wakefulness": wakefulness,
                       "keyguard_locked": locked if locked is not None
                       else "unknown"}
            if locked:
                payload["attention"] = (
                    "Phone is LOCKED — stop acting, call the human back to "
                    "unlock once, then `awake on` + resume from a fresh "
                    "screenshot.")
            return emit_ok(payload, args.json)
        if wants_dry(args):
            return dry_emit(
                args, f"adb -s <device> svc power stayon "
                f"{'true' if args.action == 'on' else 'false'} + "
                f"screen_off_timeout="
                f"{AWAKE_TIMEOUT_MS if args.action == 'on' else SLEEP_TIMEOUT_MS}")
        dev = pick_device(args.serial)
        serial = dev["serial"]
        if args.action == "on":
            try:
                adb_shell(serial, "svc", "power", "stayon", "true")
            except PhoneError:
                adb_shell(serial, "settings", "put", "global",
                          "stay_on_while_plugged_in", "7")
            adb_shell(serial, "settings", "put", "system",
                      "screen_off_timeout", AWAKE_TIMEOUT_MS)
            adb_shell(serial, "input", "keyevent", "224")  # WAKEUP
            try:
                adb_shell(serial, "wm", "dismiss-keyguard")
            except PhoneError:
                pass  # swipe keyguard dismissed when possible; secure
                # locks stay — the human unlocks once (see note below)
            stay = adb_shell(serial, "settings", "get", "global",
                             "stay_on_while_plugged_in").strip()
            timeout = adb_shell(serial, "settings", "get", "system",
                                "screen_off_timeout").strip()
            return emit_ok(
                {"awake": True, "stay_on_while_plugged_in": stay,
                 "screen_off_timeout_ms": timeout,
                 "note": "Screen stays on while plugged in (USB) + 30-min "
                         "timeout on battery. Run `phone.py awake off` when "
                         "done. A PIN/password lock still needs ONE human "
                         "unlock — ADB can never bypass it."}, args.json)
        adb_shell(serial, "svc", "power", "stayon", "false")
        adb_shell(serial, "settings", "put", "system",
                  "screen_off_timeout", SLEEP_TIMEOUT_MS)
        return emit_ok({"awake": False,
                        "note": "Sleep policy restored (stay-off, 1-min "
                                "timeout)."}, args.json)
    except PhoneError as err:
        return emit_error(err, args.json)
def cmd_notify(args: argparse.Namespace) -> int:
    """List current notifications (read-only)."""
    try:
        dev = pick_device(args.serial)
        out = adb_shell(dev["serial"], "dumpsys", "notification",
                        "--noredact", timeout=30)
    except PhoneError as err:
        return emit_error(err, args.json)
    items = []
    for chunk in out.split("NotificationRecord")[1:]:
        pkg = re.search(r"pkg=([A-Za-z0-9_.]+)", chunk)
        title = re.search(r"android\.title=String \(([^)]*)\)", chunk)
        text = re.search(r"android\.text=String \(([^)]*)\)", chunk)
        items.append({
            "package": pkg.group(1) if pkg else "unknown",
            "title": title.group(1) if title else "",
            "text": text.group(1) if text else "",
        })
        if len(items) >= 50:
            break
    if args.filter:
        query = args.filter.lower()
        items = [n for n in items
                 if query in (n["package"] + n["title"] + n["text"]).lower()]
    return emit_ok({"notifications": items, "count": len(items)}, args.json)


CLIP_HELPER_PKG = "ch.pete.adbclipboard"  # AdbClipboard (open source)


def helper_present(serial: str, pkg: str) -> bool:
    return adb_shell(serial, "pm", "path", pkg).strip().startswith("package:")


def cmd_clip(args: argparse.Namespace) -> int:
    """Cross-clipboard between PC and device.

    Android 10+ blocks background clipboard access, so there is no pure-ADB
    read/write. `set` uses the AdbClipboard helper broadcast when installed
    (explicit user consent via install), `get` reads its sync file; otherwise
    the skill routes through the scrcpy mirror bridge. Nothing is faked.
    """
    try:
        dev = pick_device(args.serial)
        serial = dev["serial"]
        if args.action == "get":
            if helper_present(serial, CLIP_HELPER_PKG):
                remote = ("/sdcard/Android/data/ch.pete.adbclipboard"
                          "/files/clipboard.txt")
                proc = run_adb(["-s", serial, "shell", "cat", remote])
                if proc.returncode == 0 and proc.stdout.strip():
                    return emit_ok({"clipboard": proc.stdout.strip(),
                                    "via": "adbclipboard-helper"}, args.json)
            out = adb_shell(serial, "dumpsys", "clipboard", timeout=15)
            match = re.search(r'text="([^"]+)"', out)
            if match:
                return emit_ok({"clipboard": match.group(1),
                                "via": "dumpsys-besteffort",
                                "note": "May be stale; dumpsys is not a "
                                        "reliable clipboard reader."},
                               args.json)
            return emit_error(PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                "No readable clipboard source found on this device.",
                "Options: (1) copy on the device, then press MOD+v in the "
                "`phone.py mirror` window — scrcpy syncs it to the PC; "
                "(2) install the AdbClipboard helper app (Play Store / "
                "F-Droid) and grant its permission, then retry."), args.json)
        # set
        text = args.text or ""
        if not text:
            return emit_error(PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                "`clip set` needs TEXT.",
                'Example: phone.py clip set "hello".'), args.json)
        if helper_present(serial, CLIP_HELPER_PKG):
            if wants_dry(args):
                return dry_emit(args, f"adb -s {serial} shell am broadcast "
                                      f"-a {CLIP_HELPER_PKG}.WRITE ...")
            adb_shell(serial, "am", "broadcast", "-a",
                      f"{CLIP_HELPER_PKG}.WRITE", "-n",
                      f"{CLIP_HELPER_PKG}/.WriteReceiver",
                      "-e", "text", text)
            return emit_ok({"clipboard_set": True,
                            "via": "adbclipboard-helper"}, args.json)
        if args.via == "type":
            if wants_dry(args):
                return dry_emit(args, f"adb -s {serial} shell input text …")
            adb_shell(serial, "input", "text", escape_input_text(text))
            return emit_ok({"typed_fallback": True,
                            "note": "No helper installed: text was TYPED "
                                    "into the focused field, not copied."},
                           args.json)
        return emit_error(PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            "No clipboard helper installed; refusing to pretend.",
            "Install AdbClipboard (needs 'Display over other apps'), then "
            "retry — or re-run with `--via type` to type into the focused "
            "field, or paste via the scrcpy mirror (MOD+v)."), args.json)
    except PhoneError as err:
        return emit_error(err, args.json)


def cmd_record(args: argparse.Namespace) -> int:
    """Record the screen to MP4 (system screenrecord, no audio by design)."""
    try:
        dev = pick_device(args.serial)
        serial = dev["serial"]
        seconds = min(max(args.seconds, 1), 180)
        remote = f"/sdcard/phone_rec_{int(time.time())}.mp4"
        planned = (f"adb -s {serial} shell screenrecord --time-limit "
                   f"{seconds} {remote} && adb pull {remote} {args.output}")
        if wants_dry(args):
            return dry_emit(args, planned)
        print(f"Recording {seconds}s — keep using the device…")
        proc = run_adb(["-s", serial, "shell", "screenrecord",
                        "--time-limit", str(seconds), remote],
                       timeout=seconds + 30)
        if proc.returncode != 0:
            raise PhoneError(
                CAUSE_NO_DEVICE,
                "screenrecord failed (fails on DRM/secure screens).",
                "Retry on a non-protected screen.")
        pull = run_adb(["-s", serial, "pull", remote, args.output])
        run_adb(["-s", serial, "shell", "rm", remote])  # best-effort cleanup
        if pull.returncode != 0 or not os.path.isfile(args.output):
            raise PhoneError(
                CAUSE_NO_DEVICE,
                f"Recording finished but pull failed: "
                f"{pull.stderr.strip()}",
                "Check free space and the output path.")
    except PhoneError as err:
        return emit_error(err, args.json)
    return emit_ok({"recording": os.path.abspath(args.output),
                    "seconds": seconds,
                    "note": "System screenrecord carries no audio; max 180s."
                    }, args.json)


def cmd_files(args: argparse.Namespace) -> int:
    """push / pull / ls files between PC and device."""
    try:
        dev = pick_device(args.serial)
        serial = dev["serial"]
        if args.op == "ls":
            out = adb_shell(serial, "ls", "-la", args.remote)
            limit = getattr(args, "max_chars", 8000)
            return emit_ok({"listing": fit(out.rstrip(), limit)}, args.json)
        if args.op == "pull":
            proc = run_adb(["-s", serial, "pull", args.remote, args.local])
            if proc.returncode != 0:
                raise PhoneError(
                    CAUSE_NO_DEVICE,
                    f"pull failed: {proc.stderr.strip()}",
                    "Check the remote path with `files ls`.")
            return emit_ok({"pulled": args.remote, "to": args.local,
                            "raw": proc.stdout.strip().splitlines()[-1]
                            if proc.stdout.strip() else ""}, args.json)
        # push
        if wants_dry(args):
            return dry_emit(
                args, f"adb -s {serial} push {args.local} {args.remote}")
        if not args.local or not os.path.isfile(args.local):
            return emit_error(PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                f"Local file not found: {args.local}",
                "Pass an existing file path."), args.json)
        proc = run_adb(["-s", serial, "push", args.local, args.remote])
        if proc.returncode != 0:
            raise PhoneError(
                CAUSE_NO_DEVICE, f"push failed: {proc.stderr.strip()}",
                "Check the remote path is writable (/sdcard/…).")
        return emit_ok({"pushed": args.local, "to": args.remote}, args.json)
    except PhoneError as err:
        return emit_error(err, args.json)


def cmd_desk(args: argparse.Namespace) -> int:
    """Drive an app in a separate scrcpy virtual display (desk mode).

    The physical screen stays untouched — true multitasking: the user keeps
    the phone while the agent works in a PC window.
    """
    scrcpy = shutil.which("scrcpy")
    if not scrcpy:
        return emit_error(PhoneError(
            CAUSE_SCRCPY_MISSING,
            "scrcpy was not found on PATH.",
            "Run scripts/install-deps.ps1, then retry."), args.json)
    try:
        dev = pick_device(args.serial)
    except PhoneError as err:
        return emit_error(err, args.json)
    size = getattr(args, "size", None) or ""
    cmd = [scrcpy, "-s", dev["serial"],
           f"--new-display={size}" if size else "--new-display",
           "--flex-display", f"--start-app={args.package}"]
    if getattr(args, "detach", False):
        if wants_dry(args):
            return dry_emit(args, " ".join(cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_SCRCPY_MISSING, f"Could not start scrcpy: {exc}",
                "Reinstall scrcpy via scripts/install-deps.ps1."), args.json)
        return emit_ok({"desk_pid": proc.pid, "package": args.package,
                        "display": size or "main-size"}, args.json)
    if args.json:
        return emit_ok({"desk_command": " ".join(cmd)}, args.json)
    print("Launching: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=False)
    except OSError as exc:
        return emit_error(PhoneError(
            CAUSE_SCRCPY_MISSING, f"Could not start scrcpy: {exc}",
            "Reinstall scrcpy via scripts/install-deps.ps1."), args.json)
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    """Rich multi-device overview (model, Android, battery, transport)."""
    try:
        find_adb()
    except PhoneError as err:
        return emit_error(err, args.json)
    rows = []
    for dev in parse_devices():
        row = {"serial": dev["serial"], "state": dev["state"]}
        if dev["state"] == "device":
            serial = dev["serial"]
            try:
                row["model"] = adb_shell(
                    serial, "getprop", "ro.product.model").strip()
                row["android"] = adb_shell(
                    serial, "getprop", "ro.build.version.release").strip()
            except PhoneError:
                row["model"], row["android"] = "", ""
            try:
                batt = adb_shell(serial, "dumpsys", "battery", timeout=15)
                level = re.search(r"level:\s*(\d+)", batt)
                row["battery"] = int(level.group(1)) if level else -1
            except PhoneError:
                row["battery"] = -1
            if serial.startswith("emulator"):
                row["transport"] = "emulator"
            elif re.match(r"^\d+\.\d+\.\d+\.\d+", serial):
                row["transport"] = "wifi"
            else:
                row["transport"] = "usb"
        rows.append(row)
    return emit_ok({"devices": rows, "count": len(rows)}, args.json)


def cmd_ground(args: argparse.Namespace) -> int:
    """Resolve a natural-language query to screen pixels (no tap)."""
    try:
        dev = pick_device(args.serial)
        x, y, provider = resolve_ground(
            dev["serial"], args.query, args.provider)
    except PhoneError as err:
        return emit_error(err, args.json)
    payload = {"x": x, "y": y, "provider": provider, "query": args.query}
    if args.annotate:
        try:
            png = capture_png(dev["serial"])
            raw = annotate_png(
                png, [(x - 30, y - 30, x + 30, y + 30,
                       args.query[:24])])
            with open(args.annotate, "wb") as fh:
                fh.write(raw)
            payload["annotated"] = os.path.abspath(args.annotate)
        except PhoneError as err:
            return emit_error(err, args.json)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_DUMP_FAILED, f"Cannot write {args.annotate}: {exc}",
                "Use a writable output path."), args.json)
    return emit_ok(payload, args.json)


# --- autonomy: deterministic recipe executor --------------------------------
RECIPE_ACTIONS = ("launch", "tap", "swipe", "type", "key", "wait",
                  "expect", "screenshot")


def _step_launch(serial: str, step: dict) -> str:
    pkg = step.get("package", "")
    if not pkg:
        raise PhoneError(CAUSE_ELEMENT_NOT_FOUND, "launch needs 'package'.",
                         'Example: {"action":"launch",'
                         ' "package":"com.android.settings"}')
    out = adb_shell(serial, "monkey", "-p", pkg, "-c",
                    "android.intent.category.LAUNCHER", "1")
    if "No activities found" in out:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND,
            f"Package '{pkg}' not installed or has no launcher.",
            "Find the package with `phone.py packages --filter <name>`.")
    return f"launched {pkg}"


def _step_expect(serial: str, step: dict) -> str:
    if "text" in step:
        root = pull_ui_dump(serial)
        if not find_nodes_by_text(root, step["text"]):
            raise PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                f"expect failed: '{step['text']}' not on screen.",
                "The previous step may have missed; adjust and re-run with "
                "--from-step.")
        return f"saw '{step['text']}'"
    if "package" in step:
        out = adb_shell(serial, "pidof", step["package"]).strip()
        if not out:
            raise PhoneError(
                CAUSE_ELEMENT_NOT_FOUND,
                f"expect failed: {step['package']} has no live process.",
                "The launch step may have failed; check `packages`.")
        return f"{step['package']} alive (pid {out.split()[0]})"
    raise PhoneError(CAUSE_ELEMENT_NOT_FOUND,
                     "expect needs 'text' or 'package'.",
                     'Example: {"action":"expect", "text":"Saved"}')


def execute_step(serial: str, step: dict, shots: str | None,
                 index: int) -> str:
    action = step.get("action", "")
    if action == "launch":
        return _step_launch(serial, step)
    if action == "tap":
        if "text" in step:
            x, y, _ = ground_with_xml(serial, step["text"])
        else:
            x, y = int(step["x"]), int(step["y"])
        do_tap(serial, x, y)
        return f"tapped {x},{y}"
    if action == "swipe":
        adb_shell(serial, "input", "swipe", str(step["x1"]),
                  str(step["y1"]), str(step["x2"]), str(step["y2"]),
                  str(step.get("ms", 300)))
        return "swiped"
    if action == "type":
        adb_shell(serial, "input", "text",
                  escape_input_text(step.get("text", "")))
        return "typed"
    if action == "key":
        adb_shell(serial, "input", "keyevent", str(step.get("code", "4")))
        return f"key {step.get('code', '4')}"
    if action == "wait":
        time.sleep(float(step.get("seconds", 2)))
        return f"waited {step.get('seconds', 2)}s"
    if action == "expect":
        return _step_expect(serial, step)
    if action == "screenshot":
        out = step.get("output") or (os.path.join(
            shots, f"step{index}.png") if shots else f"step{index}.png")
        with open(out, "wb") as fh:
            fh.write(capture_png(serial))
        return f"shot {out}"
    raise PhoneError(
        CAUSE_ELEMENT_NOT_FOUND,
        f"Unknown recipe action '{action}'.",
        f"Valid: {', '.join(RECIPE_ACTIONS)}.")


def load_recipe(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise PhoneError(
            CAUSE_ELEMENT_NOT_FOUND, f"Cannot load recipe {path}: {exc}",
            "Point at a valid recipe JSON (`recipe new` scaffolds one)."
        ) from exc
    errors = []
    steps = data.get("steps")
    if not isinstance(data, dict) or not isinstance(steps, list):
        errors.append("top level needs {'name': str, 'steps': [...]}")
    else:
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or step.get("action") not in \
                    RECIPE_ACTIONS:
                errors.append(f"step {i}: action must be one of "
                              f"{', '.join(RECIPE_ACTIONS)}")
    if errors:
        raise PhoneError(CAUSE_ELEMENT_NOT_FOUND,
                         "Invalid recipe: " + "; ".join(errors),
                         "Fix the file or regenerate with `recipe new`.")
    return data


RECIPE_TEMPLATE = {
    "name": "NAME",
    "steps": [
        {"action": "launch", "package": "com.android.settings"},
        {"action": "expect", "text": "Settings"},
        {"action": "tap", "text": "Network"},
        {"action": "screenshot", "output": "prove.png"},
    ],
}


def cmd_recipe(args: argparse.Namespace) -> int:
    try:
        if args.op == "new":
            out = args.out or f"{args.name}.recipe.json"
            template = dict(RECIPE_TEMPLATE)
            template["name"] = args.name
            try:
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(template, fh, indent=2)
            except OSError as exc:
                return emit_error(PhoneError(
                    CAUSE_DUMP_FAILED, f"Cannot write {out}: {exc}",
                    "Use a writable output path."), args.json)
            return emit_ok({"recipe": os.path.abspath(out),
                            "steps": len(template["steps"]),
                            "next": f"Edit it, then `phone.py run {out}`."},
                           args.json)
        data = load_recipe(args.name)
        return emit_ok({"valid": True, "name": data.get("name", ""),
                        "steps": len(data["steps"])}, args.json)
    except PhoneError as err:
        return emit_error(err, args.json)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute a recipe deterministically: act -> verify -> stop on mismatch.

    Recipes use instant XML grounding (no network) for speed. Every failure
    saves a FAIL screenshot so the agent sees exactly what broke.
    """
    try:
        data = load_recipe(args.recipe)
        if wants_dry(args):
            plan = [f"{i}. {s.get('action')}" for i, s in
                    enumerate(data["steps"])]
            return emit_ok({"dry_run": True, "recipe": data.get("name", ""),
                            "plan": plan}, args.json)
        dev = pick_device(args.serial)
        serial = dev["serial"]
        shots = args.shots
        if shots:
            os.makedirs(shots, exist_ok=True)
    except PhoneError as err:
        return emit_error(err, args.json)
    except OSError as exc:
        return emit_error(PhoneError(
            CAUSE_DUMP_FAILED, f"Cannot create shots dir: {exc}",
            "Use a writable --shots path."), args.json)
    done = []
    start = max(args.from_step, 0)
    for i in range(start, len(data["steps"])):
        try:
            detail = execute_step(serial, data["steps"][i], shots, i)
            done.append({"step": i, "ok": True, "detail": detail})
        except PhoneError as err:
            fail_shot = None
            if shots:
                try:
                    fail_shot = os.path.join(shots, f"FAIL_step{i}.png")
                    with open(fail_shot, "wb") as fh:
                        fh.write(capture_png(serial))
                except PhoneError:
                    fail_shot = None
            return emit_ok(
                {"recipe": data.get("name", ""), "done": done,
                 "failed_step": i, "cause": err.cause,
                 "message": err.message, "action": err.action,
                 "fail_shot": fail_shot,
                 "resume": f"phone.py run {args.recipe} "
                           f"--from-step {i}"}, args.json)
        except OSError as exc:
            return emit_error(PhoneError(
                CAUSE_DUMP_FAILED, f"Step {i} I/O failed: {exc}",
                "Check --shots/output paths."), args.json)
    return emit_ok({"recipe": data.get("name", ""), "done": done,
                    "failed_step": None,
                    "passed": f"{len(done)}/{len(data['steps'])}"}, args.json)


def cmd_eval(args: argparse.Namespace) -> int:
    """Self-test harness: safe checks the skill depends on.

    Read-only by default; --act adds HOME/launch-settings/BACK.
    """
    checks = []

    def check(name: str, fn) -> None:
        try:
            checks.append({"name": name, "ok": True,
                           "detail": str(fn())})
        except PhoneError as err:
            checks.append({"name": name, "ok": False,
                           "detail": f"{err.cause}: {err.message}"})
        except Exception as exc:  # never let the harness itself crash
            checks.append({"name": name, "ok": False,
                           "detail": f"harness-error: {exc}"})

    serial_box: dict = {}

    def need_device() -> str:
        dev = pick_device(args.serial)
        serial_box["serial"] = dev["serial"]
        return dev["serial"]

    check("adb-present", lambda: find_adb() and "adb found")
    check("device-visible", need_device)

    def shot() -> str:
        png = capture_png(serial_box["serial"])
        return f"{len(png)} bytes PNG {png_size(png)}"

    def dump() -> str:
        root = pull_ui_dump(serial_box["serial"])
        count = sum(1 for _ in root.iter("node"))
        if count == 0:
            raise PhoneError(CAUSE_DUMP_FAILED, "Dump parsed but empty.",
                             "Secure window? Try another screen.")
        return f"{count} nodes"

    check("screenshot", shot if "serial" in serial_box else
          (lambda: (_ for _ in ()).throw(PhoneError(
              CAUSE_NO_DEVICE, "Skipped: no device.", "Connect one first."))))
    check("ui-dump", dump if "serial" in serial_box else
          (lambda: (_ for _ in ()).throw(PhoneError(
              CAUSE_NO_DEVICE, "Skipped: no device.", "Connect one first."))))
    if args.act and "serial" in serial_box:
        serial = serial_box["serial"]

        def act_flow() -> str:
            adb_shell(serial, "input", "keyevent", "3")
            _step_launch(serial, {"package": "com.android.settings"})
            _step_expect(serial, {"package": "com.android.settings"})
            adb_shell(serial, "input", "keyevent", "4")
            return "HOME -> Settings -> BACK ok"

        check("act-flow", act_flow)
    passed = sum(1 for c in checks if c["ok"])
    code = emit_ok({"checks": checks, "passed": f"{passed}/{len(checks)}"},
                   args.json)
    failed = len(checks) - passed
    return code if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    # Shared flags live on a parent parser so they work BEFORE and AFTER
    # the subcommand (`phone.py --json status` == `phone.py status --json`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-s", "--serial",
                        help="Target device serial (required when several "
                             "devices are attached).")
    common.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output.")
    common.add_argument("--dry-run", action="store_true",
                        help="Print the planned ADB/scrcpy invocation without "
                             "executing mutating actions.")
    common.add_argument("--log-dir", default=None,
                        help="Append a JSONL transcript of every invocation "
                             "to DIR/steps.jsonl.")
    common.add_argument("--verbose", action="store_true",
                        help="Echo every adb/scrcpy invocation to stderr "
                             "(learn the mapping, debug faster).")
    common.add_argument("--max-chars", type=int, default=8000,
                        help="Cap long text outputs (shell, listings) at N "
                             "characters to protect context (default 8000).")
    common.add_argument("--retries", type=int, default=0,
                        help="Auto-retry failures classified retry=auto "
                             "(stale daemon, missed tap) with backoff "
                             "(default 0 = no retry).")
    p = argparse.ArgumentParser(
        prog="phone.py", parents=[common],
        description="Smartphone Use CLI — connect to and control Android "
                    "devices via ADB (USB / Wi-Fi LAN / emulator).")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(*a, **k):
        """sub.add_parser with the shared --serial/--json flags included."""
        return sub.add_parser(*a, parents=[common], **k)

    c = add("status", help="Diagnose adb setup and list devices.")
    c.set_defaults(func=cmd_status)

    c = add("connect-usb", help="Connect via USB cable.")
    c.set_defaults(func=cmd_connect_usb)

    c = add("pair",
                       help="Pair for Wireless debugging (Android 11+).")
    c.add_argument("endpoint", help="Pairing endpoint, e.g. 192.168.1.10:37891")
    c.add_argument("code", help="6-digit pairing code shown on the phone.")
    c.set_defaults(func=cmd_pair)

    c = add("connect-wifi", help="Connect over Wi-Fi LAN.")
    c.add_argument("endpoint",
                   help="Connection endpoint, e.g. 192.168.1.10:5555 "
                        "(NOT the pairing port).")
    c.set_defaults(func=cmd_connect_wifi)

    c = add("disconnect", help="Disconnect and reset to USB mode.")
    c.add_argument("endpoint", nargs="?",
                   help="Optional specific endpoint to disconnect.")
    c.set_defaults(func=cmd_disconnect)

    c = add("screenshot", help="Capture the current screen.")
    c.add_argument("output", help="Output PNG path, e.g. screen.png")
    c.add_argument("--annotate", action="store_true",
                   help="Overlay numbered boxes on clickable elements "
                        "(Set-of-Mark style; needs Pillow).")
    c.set_defaults(func=cmd_screenshot)

    c = add("dump", help="Dump the UI hierarchy as XML/JSON.")
    c.add_argument("--out", default=None, help="Save full XML tree to file.")
    c.set_defaults(func=cmd_dump)

    c = add("tap", help="Tap coordinates or on-screen text.")
    c.add_argument("x", nargs="?", type=int, help="X coordinate.")
    c.add_argument("y", nargs="?", type=int, help="Y coordinate.")
    c.add_argument("--text", default=None,
                   help="Tap element containing this text instead.")
    c.add_argument("--provider", default="xml",
                   choices=["xml", "vlm", "omniparser", "auto"],
                   help="Grounding provider for --text (default xml: "
                        "instant, offline).")
    c.add_argument("--verify", action="store_true",
                   help="Snapshot before/after and report whether the "
                        "screen reacted (slower, ~2 extra reads).")
    c.set_defaults(func=cmd_tap)

    c = add("swipe", help="Swipe from (x1,y1) to (x2,y2).")
    c.add_argument("x1", type=int)
    c.add_argument("y1", type=int)
    c.add_argument("x2", type=int)
    c.add_argument("y2", type=int)
    c.add_argument("ms", nargs="?", type=int, default=300,
                   help="Duration in ms (default 300).")
    c.add_argument("--verify", action="store_true",
                   help="Snapshot before/after and report whether the "
                        "screen reacted (slower, ~2 extra reads).")
    c.set_defaults(func=cmd_swipe)

    c = add("key", help="Send an Android keyevent code.")
    c.add_argument("code", help="E.g. 3=HOME 4=BACK 26=POWER 82=MENU.")
    c.add_argument("--verify", action="store_true",
                   help="Snapshot before/after and report whether the "
                        "screen reacted (slower, ~2 extra reads).")
    c.set_defaults(func=cmd_key)

    c = add("type", help="Type ASCII text into the focused field.")
    c.add_argument("text", help="Text to type (spaces OK; quotes/unicode "
                                "are skipped — use scrcpy mirror for those).")
    c.add_argument("--verify", action="store_true",
                   help="Snapshot before/after and report whether the "
                        "screen reacted (slower, ~2 extra reads).")
    c.set_defaults(func=cmd_type)

    c = add("launch", help="Launch an app by package name.")
    c.add_argument("package", help="E.g. com.android.settings")
    c.add_argument("--verify", action="store_true",
                   help="Snapshot before/after and report whether the "
                        "screen reacted (slower, ~2 extra reads).")
    c.set_defaults(func=cmd_launch)

    c = add("packages", help="List installed third-party packages.")
    c.add_argument("--filter", default=None, help="Substring filter.")
    c.set_defaults(func=cmd_packages)

    c = add("shell",
                       help="Raw `adb shell` escape hatch (use sparingly).")
    c.add_argument("--timeout", type=int, default=30)
    c.add_argument("command", nargs=argparse.REMAINDER,
                   help="Command after `--`, e.g. shell -- dumpsys battery")
    c.set_defaults(func=cmd_shell)

    c = add("mirror", help="Open a live scrcpy mirror window.")
    c.add_argument("--no-audio", action="store_true")
    c.add_argument("--otg", action="store_true",
                   help="HID control without mirroring or debugging "
                        "(locked-down devices; look at the phone directly).")
    c.add_argument("--detach", action="store_true",
                   help="Launch in background, return PID (for MCP/agents).")
    c.add_argument("extra", nargs="*", help="Extra scrcpy flags.")
    c.set_defaults(func=cmd_mirror)

    c = add("tunnel",
                       help="Show the secure recipe for Internet access "
                            "(never exposes raw ADB).")
    c.add_argument("--via", choices=["ssh", "vpn"], default="ssh")
    c.add_argument("--remote", default="user@remote-pc",
                   help="SSH destination for --via ssh.")
    c.set_defaults(func=cmd_tunnel)

    c = add("awake", help="Keep the screen on during sessions "
                            "(reversible, no root).")
    c.add_argument("action", choices=["on", "off", "status"],
                   help="on: stay-awake + 30-min timeout + wake; off: "
                        "restore defaults; status: report policy.")
    c.set_defaults(func=cmd_awake)

    c = add("notify", help="List current notifications (read-only).")
    c.add_argument("--filter", default=None,
                   help="Keep only notifications matching this substring.")
    c.set_defaults(func=cmd_notify)

    c = add("clip", help="Get/set the device clipboard (see --help).")
    c.add_argument("action", choices=["get", "set"])
    c.add_argument("text", nargs="?", default=None,
                   help="Text for `clip set`.")
    c.add_argument("--via", choices=["helper", "type"], default="helper",
                   help="helper: real clipboard via helper app (default); "
                        "type: fall back to typing into the focused field.")
    c.set_defaults(func=cmd_clip)

    c = add("record", help="Record the screen to MP4 (no audio).")
    c.add_argument("output", help="Output MP4 path, e.g. demo.mp4")
    c.add_argument("--seconds", type=int, default=10,
                   help="Duration 1-180s (default 10).")
    c.set_defaults(func=cmd_record)

    c = add("files", help="push / pull / ls files on the device.")
    c.add_argument("op", choices=["pull", "push", "ls"])
    c.add_argument("remote", help="Device path (source for pull/ls).")
    c.add_argument("local", nargs="?", default=".",
                   help="Local path (destination for pull, source for push).")
    c.set_defaults(func=cmd_files)

    c = add("desk", help="Drive an app in a separate virtual display.")
    c.add_argument("package", help="App package, e.g. org.mozilla.firefox")
    c.add_argument("--size", default=None,
                   help="Virtual display size, e.g. 1280x960.")
    c.add_argument("--detach", action="store_true",
                   help="Launch in background, return PID (for MCP/agents).")
    c.set_defaults(func=cmd_desk)

    c = add("fleet", help="Multi-device overview "
                          "(model, Android, battery, transport).")
    c.set_defaults(func=cmd_fleet)

    c = add("ground", help="Resolve a query to screen pixels (no tap).")
    c.add_argument("query", help='E.g. "blue Send button".')
    c.add_argument("--provider", default="auto",
                   choices=["auto", "xml", "vlm", "omniparser"])
    c.add_argument("--annotate", default=None, metavar="OUT.png",
                   help="Save screenshot with the resolved point marked.")
    c.set_defaults(func=cmd_ground)

    c = add("run", help="Execute a recipe JSON deterministically.")
    c.add_argument("recipe", help="Recipe file, e.g. morning.recipe.json")
    c.add_argument("--from-step", type=int, default=0,
                   help="Resume at step N (default 0).")
    c.add_argument("--shots", default=None,
                   help="Directory for step + failure screenshots.")
    c.set_defaults(func=cmd_run)

    c = add("recipe", help="Scaffold (new) or validate (check) recipes.")
    c.add_argument("op", choices=["new", "check"])
    c.add_argument("name", nargs="?", default=None,
                   help="Recipe name (new) or file (check).")
    c.add_argument("--out", default=None, help="Output file for `new`.")
    c.set_defaults(func=cmd_recipe)

    c = add("eval", help="Self-test harness (read-only unless --act).")
    c.add_argument("--act", action="store_true",
                   help="Also run HOME -> launch Settings -> BACK.")
    c.set_defaults(func=cmd_eval)

    c = add("observe", help="One bundled read: screenshot + elements + "
                            "foreground app (fewer round trips).")
    c.add_argument("output", help="Screenshot PNG path, e.g. now.png")
    c.add_argument("--annotate", action="store_true",
                   help="Overlay numbered boxes (needs Pillow).")
    c.add_argument("--max-elements", type=int, default=60,
                   help="Cap the element list (default 60).")
    c.set_defaults(func=cmd_observe)

    c = add("wait-for", help="Block until a screen condition holds.")
    c.add_argument("--text", default=None,
                   help="Wait until this label appears on screen.")
    c.add_argument("--package", default=None,
                   help="Wait until this package has a live process.")
    c.add_argument("--focused", action="store_true",
                   help="With --package: require it to be FOREGROUND.")
    c.add_argument("--change", action="store_true",
                   help="Wait until the visible text set changes.")
    c.add_argument("--timeout", type=int, default=20,
                   help="Give up after N seconds (default 20).")
    c.add_argument("--interval", type=float, default=1.0,
                   help="Poll every N seconds (default 1.0).")
    c.set_defaults(func=cmd_wait_for)

    c = add("doctor", help="Check host + ADB server health.")
    c.add_argument("--fix", action="store_true",
                   help="Auto-repair safe issues (server restart, stale "
                        "Wi-Fi entries). Never touches the device UI.")
    c.set_defaults(func=cmd_doctor)

    c = add("setup", help="First-run wizard: guide, wait, prove.")
    c.add_argument("--mode", choices=["usb", "wifi"], default="usb")
    c.add_argument("--timeout", type=int, default=120,
                   help="Wait up to N seconds (default 120).")
    c.set_defaults(func=cmd_setup)

    c = add("diag", help="Support bundle (status + eval + versions).")
    c.add_argument("--out", default=None, metavar="BUNDLE.zip",
                   help="Write a zip bundle for handoffs.")
    c.set_defaults(func=cmd_diag)

    return p


def main(argv: list[str] | None = None) -> int:
    global _T0, VERBOSE
    _T0 = time.monotonic()
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse re-applies the shared parents' defaults inside the subparser,
    # which would clobber globals passed BEFORE the subcommand
    # (`phone.py --json status`). Re-apply pre-subcommand tokens explicitly
    # (post-subcommand values keep precedence).
    toks = list(argv) if argv is not None else sys.argv[1:]
    if args.command in toks:
        pre = toks[:toks.index(args.command)]
        i = 0
        while i < len(pre):
            tok = pre[i]
            if tok in ("--json", "--verbose") and not getattr(args,
                                                              tok[2:], False):
                setattr(args, tok[2:], True)
                i += 1
            elif tok in ("--dry-run",) and not getattr(args, "dry_run",
                                                       False):
                args.dry_run = True
                i += 1
            elif tok in ("-s", "--serial", "--log-dir", "--retries") \
                    and i + 1 < len(pre):
                attr = {"-s": "serial", "--serial": "serial",
                        "--log-dir": "log_dir",
                        "--retries": "retries"}[tok]
                current = getattr(args, attr, None)
                if current is None or current == 0:
                    raw = pre[i + 1]
                    setattr(args, attr,
                            int(raw) if attr == "retries" else raw)
                i += 2
            else:
                i += 1
    VERBOSE = bool(getattr(args, "verbose", False))
    max_retries = max(int(getattr(args, "retries", 0) or 0), 0)
    attempt = 0
    code = 1
    global _LAST_ERROR
    while True:
        _LAST_ERROR = None
        if getattr(args, "verify", False) and args.command in (
                "tap", "swipe", "key", "type", "launch"):
            # Snapshot BEFORE the action so each command can report whether
            # the screen reacted. Best-effort: the command itself reports
            # device problems with full cause/action detail.
            try:
                _dev0 = pick_device(args.serial)
                args._verify_before = snap_state(_dev0["serial"])
            except PhoneError:
                args._verify_before = None
        try:
            code = args.func(args)
        except PhoneError as err:
            # Safety net for failures raised outside cmd_* handlers.
            code = emit_error(err, getattr(args, "json", False))
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            code = 130
            break
        if code == 0 or _LAST_ERROR is None:
            break
        # Commands report via emit_error internally; retry only what the
        # policy marks safe to repeat mechanically.
        if RETRY_POLICY.get(_LAST_ERROR.cause, "auto") == "auto" \
                and attempt < max_retries:
            attempt += 1
            if VERBOSE:
                print(f"! retry {attempt}/{max_retries} after "
                      f"{_LAST_ERROR.cause} (backoff {2 * attempt}s)",
                      file=sys.stderr)
            time.sleep(2 * attempt)
            continue
        break
    log_dir = getattr(args, "log_dir", None)
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "steps.jsonl"), "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.datetime.now().isoformat(
                        timespec="seconds"),
                    "argv": list(argv) if argv is not None
                    else sys.argv[1:],
                    "exit": code, "attempts": attempt + 1}) + "\n")
        except OSError:
            pass  # transcript is best-effort; never fail the command
    return code


if __name__ == "__main__":
    sys.exit(main())
