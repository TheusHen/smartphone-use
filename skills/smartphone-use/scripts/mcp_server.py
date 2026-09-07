#!/usr/bin/env python3
"""mcp_server.py — MCP (Model Context Protocol) stdio server for Smartphone Use.

Exposes phone.py as MCP tools so ANY MCP-compatible agent (Claude Code,
Codex, Cursor, …) can drive an Android device natively: no CLI parsing,
structured arguments in, text results out.

Zero third-party dependencies: speaks JSON-RPC 2.0 over stdio (newline
delimited) implementing the MCP handshake (initialize / tools/list /
tools/call / ping).

Usage (MCP client config, command + args):
  python scripts/mcp_server.py

Safety:
  * The raw `shell` tool is DISABLED unless env MCP_ALLOW_SHELL=1.
  * `mirror`/`desk` always launch detached (never block the server loop).
  * Nothing is printed to stdout except protocol messages (all phone.py
    output is captured per-call); diagnostics go to stderr.

Env passthrough: ANDROID_SERIAL (default -s), VLM_GROUND_URL,
VLM_GROUND_KEY, VLM_GROUND_MODEL, OMNIPARSER_URL work as in phone.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import phone  # noqa: E402

SERVER_NAME = "smartphone-use"
SERVER_VERSION = phone.VERSION
PROTOCOL_VERSION = "2024-11-05"

ALLOW_SHELL = os.environ.get("MCP_ALLOW_SHELL", "") == "1"


def ns(**kwargs) -> SimpleNamespace:
    base = {"serial": os.environ.get("ANDROID_SERIAL"),
            "json": True, "dry_run": False, "log_dir": None,
            "verbose": False, "max_chars": 8000, "verify": False,
            "_verify_before": None}
    base.update(kwargs)
    return SimpleNamespace(**base)


def invoke(func, args: SimpleNamespace) -> tuple[bool, str]:
    """Run a phone.py command, capturing its output. Returns (is_error, text)."""
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = func(args)
    except phone.PhoneError as exc:
        return True, (f"CAUSE: {exc.cause} | {exc.message}\n"
                      f"ACTION: {exc.action}")
    except Exception as exc:  # never crash the server loop
        return True, f"harness-error: {exc}"
    out = buf.getvalue().strip()
    err_text = err.getvalue().strip()
    if err_text:
        out = (out + "\n" + err_text).strip()
    return (code != 0), out or f"exit={code}"


def shell_tool(a: dict) -> tuple[bool, str]:
    if not ALLOW_SHELL:
        return True, ("The `shell` tool is disabled. Set MCP_ALLOW_SHELL=1 "
                      "on the server to enable raw adb shell access.")
    cmd = a.get("command", "")
    parts = cmd.split() if isinstance(cmd, str) else list(cmd)
    return invoke(phone.cmd_shell,
                  ns(command=parts, timeout=int(a.get("timeout", 30))))


# name -> (description, inputSchema, handler)
TOOLS: dict = {}


def tool(name: str, description: str, schema: dict, handler) -> None:
    TOOLS[name] = {"description": description, "inputSchema": schema,
                   "handler": handler}


def _props(**kw) -> dict:
    return {"type": "object", "properties": kw}


tool("status", "Diagnose ADB setup and list attached devices (with state).",
     _props(), lambda a: invoke(phone.cmd_status, ns()))

tool("screenshot", "Capture the device screen to a PNG file.",
     _props(output={"type": "string"},
            annotate={"type": "boolean"}),
     lambda a: invoke(phone.cmd_screenshot,
                      ns(output=a["output"],
                         annotate=bool(a.get("annotate", False)))))

tool("dump", "Dump the UI hierarchy (text/content-desc/bounds per element).",
     _props(out={"type": "string"}),
     lambda a: invoke(phone.cmd_dump, ns(out=a.get("out"))))

tool("observe", "One bundled read: screenshot + elements + foreground app.",
     _props(output={"type": "string"}, annotate={"type": "boolean"},
            max_elements={"type": "integer"}),
     lambda a: invoke(phone.cmd_observe,
                      ns(output=a["output"],
                         annotate=bool(a.get("annotate", False)),
                         max_elements=int(a.get("max_elements", 60)))))

tool("wait_for", "Block until text/package/screen-change (or timeout).",
     _props(text={"type": "string"}, package={"type": "string"},
            focused={"type": "boolean"}, change={"type": "boolean"},
            timeout={"type": "integer"}, interval={"type": "number"}),
     lambda a: invoke(phone.cmd_wait_for,
                      ns(text=a.get("text"), package=a.get("package"),
                         focused=bool(a.get("focused", False)),
                         change=bool(a.get("change", False)),
                         timeout=int(a.get("timeout", 20)),
                         interval=float(a.get("interval", 1.0)))))

tool("tap", "Tap screen coordinates, or an element by visible text.",
     _props(x={"type": "integer"}, y={"type": "integer"},
            text={"type": "string"},
            provider={"type": "string"}),
     lambda a: invoke(phone.cmd_tap,
                      ns(x=a.get("x"), y=a.get("y"), text=a.get("text"),
                         provider=a.get("provider", "xml"))))

tool("swipe", "Swipe from (x1,y1) to (x2,y2), optional duration ms.",
     _props(x1={"type": "integer"}, y1={"type": "integer"},
            x2={"type": "integer"}, y2={"type": "integer"},
            ms={"type": "integer"}),
     lambda a: invoke(phone.cmd_swipe,
                      ns(x1=a["x1"], y1=a["y1"], x2=a["x2"], y2=a["y2"],
                         ms=int(a.get("ms", 300)))))

tool("type", "Type ASCII text into the focused field.",
     _props(text={"type": "string"}),
     lambda a: invoke(phone.cmd_type, ns(text=a.get("text", ""))))

tool("key", "Send an Android keyevent (3=HOME 4=BACK 26=POWER 82=MENU).",
     _props(code={"type": "string"}),
     lambda a: invoke(phone.cmd_key, ns(code=str(a.get("code", "4")))))

tool("launch", "Launch an app by package name.",
     _props(package={"type": "string"}),
     lambda a: invoke(phone.cmd_launch, ns(package=a["package"])))

tool("packages", "List installed third-party packages, optional filter.",
     _props(filter={"type": "string"}),
     lambda a: invoke(phone.cmd_packages, ns(filter=a.get("filter"))))

tool("notify", "List current device notifications (read-only).",
     _props(filter={"type": "string"}),
     lambda a: invoke(phone.cmd_notify, ns(filter=a.get("filter"))))

tool("clip_get", "Read the device clipboard (helper app or best-effort).",
     _props(), lambda a: invoke(phone.cmd_clip, ns(action="get")))

tool("clip_set", "Set the device clipboard (needs helper app; else type).",
     _props(text={"type": "string"}, via={"type": "string"}),
     lambda a: invoke(phone.cmd_clip,
                      ns(action="set", text=a.get("text", ""),
                         via=a.get("via", "helper"))))

tool("record", "Record the screen to MP4 (1-180s, no audio).",
     _props(output={"type": "string"}, seconds={"type": "integer"}),
     lambda a: invoke(phone.cmd_record,
                      ns(output=a["output"],
                         seconds=int(a.get("seconds", 10)))))

tool("files_pull", "Pull a file from the device to the PC.",
     _props(remote={"type": "string"}, local={"type": "string"}),
     lambda a: invoke(phone.cmd_files,
                      ns(op="pull", remote=a["remote"],
                         local=a.get("local", "."))))

tool("files_push", "Push a PC file to the device.",
     _props(local={"type": "string"}, remote={"type": "string"}),
     lambda a: invoke(phone.cmd_files,
                      ns(op="push", remote=a["remote"],
                         local=a.get("local"))))

tool("files_ls", "List a device directory.",
     _props(remote={"type": "string"}),
     lambda a: invoke(phone.cmd_files,
                      ns(op="ls", remote=a.get("remote", "/sdcard"),
                         local=".")))

tool("ground", "Resolve a natural-language query to screen pixels (no tap).",
     _props(query={"type": "string"}, provider={"type": "string"},
            annotate={"type": "string"}),
     lambda a: invoke(phone.cmd_ground,
                      ns(query=a["query"],
                         provider=a.get("provider", "auto"),
                         annotate=a.get("annotate"))))

tool("fleet", "Multi-device overview (model, Android, battery, transport).",
     _props(), lambda a: invoke(phone.cmd_fleet, ns()))

tool("desk", "Open an app in a separate scrcpy virtual display "
              "(physical screen untouched). Always detached here.",
     _props(package={"type": "string"}, size={"type": "string"}),
     lambda a: invoke(phone.cmd_desk,
                      ns(package=a["package"], size=a.get("size"),
                         detach=True)))

tool("mirror", "Open a live scrcpy mirror window. Always detached here.",
     _props(no_audio={"type": "boolean"}, otg={"type": "boolean"}),
     lambda a: invoke(phone.cmd_mirror,
                      ns(no_audio=bool(a.get("no_audio", False)),
                         otg=bool(a.get("otg", False)),
                         extra=[], detach=True)))

tool("run", "Execute a recipe JSON deterministically (act→verify).",
     _props(recipe={"type": "string"}, from_step={"type": "integer"},
            shots={"type": "string"}),
     lambda a: invoke(phone.cmd_run,
                      ns(recipe=a["recipe"],
                         from_step=int(a.get("from_step", 0)),
                         shots=a.get("shots"))))

tool("recipe_new", "Scaffold a new recipe JSON template.",
     _props(name={"type": "string"}, out={"type": "string"}),
     lambda a: invoke(phone.cmd_recipe,
                      ns(op="new", name=a["name"], out=a.get("out"))))

tool("recipe_check", "Validate a recipe JSON file.",
     _props(file={"type": "string"}),
     lambda a: invoke(phone.cmd_recipe,
                      ns(op="check", name=a["file"], out=None)))

tool("eval", "Run the self-test harness (read-only unless act=true).",
     _props(act={"type": "boolean"}),
     lambda a: invoke(phone.cmd_eval, ns(act=bool(a.get("act", False)))))

tool("doctor", "Check host + ADB server health; fix=true auto-repairs.",
     _props(fix={"type": "boolean"}),
     lambda a: invoke(phone.cmd_doctor, ns(fix=bool(a.get("fix", False)))))

tool("setup", "First-run wizard (guides, waits, proves with screenshot).",
     _props(mode={"type": "string"}, timeout={"type": "integer"}),
     lambda a: invoke(phone.cmd_setup,
                      ns(mode=a.get("mode", "usb"),
                         timeout=int(a.get("timeout", 120)))))

tool("diag", "Support bundle (status + eval + versions), optional zip.",
     _props(out={"type": "string"}),
     lambda a: invoke(phone.cmd_diag, ns(out=a.get("out"))))

tool("awake", "Keep the screen on during sessions (on/off/status).",
     _props(action={"type": "string"}),
     lambda a: invoke(phone.cmd_awake, ns(action=a.get("action", "status"))))

tool("connect_usb", "Connect via USB (after enabling USB debugging).",
     _props(), lambda a: invoke(phone.cmd_connect_usb, ns()))

tool("pair", "Pair Wireless debugging: endpoint + 6-digit code.",
     _props(endpoint={"type": "string"}, code={"type": "string"}),
     lambda a: invoke(phone.cmd_pair,
                      ns(endpoint=a["endpoint"], code=a["code"])))

tool("connect_wifi", "Connect over Wi-Fi LAN (connection port, not pairing).",
     _props(endpoint={"type": "string"}),
     lambda a: invoke(phone.cmd_connect_wifi, ns(endpoint=a["endpoint"])))

tool("disconnect", "Disconnect and reset transport to USB.",
     _props(endpoint={"type": "string"}),
     lambda a: invoke(phone.cmd_disconnect, ns(endpoint=a.get("endpoint"))))

tool("tunnel", "Secure remote-access recipe (SSH/VPN; never raw ADB).",
     _props(via={"type": "string"}, remote={"type": "string"}),
     lambda a: invoke(phone.cmd_tunnel,
                      ns(via=a.get("via", "ssh"),
                         remote=a.get("remote", "user@remote-pc"))))

tool("shell", "Raw adb shell (DISABLED unless MCP_ALLOW_SHELL=1).",
     _props(command={"type": "string"}, timeout={"type": "integer"}),
     shell_tool)


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    mid = msg.get("id")
    method = msg.get("method", "")

    def result(res: dict) -> None:
        if mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": res})

    def error(code: int, message: str) -> None:
        if mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": code, "message": message}})

    if method == "initialize":
        result({"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME,
                               "version": SERVER_VERSION}})
    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass
    elif method == "ping":
        result({})
    elif method == "tools/list":
        result({"tools": [
            {"name": name,
             "description": spec["description"],
             "inputSchema": spec["inputSchema"]}
            for name, spec in TOOLS.items()]})
    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        spec = TOOLS.get(name)
        if spec is None:
            error(-32602, f"Unknown tool: {name}")
            return
        try:
            is_error, text = spec["handler"](params.get("arguments", {}))
        except Exception as exc:  # never crash the server loop
            is_error, text = True, f"harness-error: {exc}"
        result({"content": [{"type": "text", "text": text}],
                "isError": is_error})
    else:
        error(-32601, f"Method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "Parse error"}})
            continue
        try:
            handle(msg)
        except Exception as exc:  # absolute last resort
            print(f"mcp fatal: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
