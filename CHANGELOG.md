# Changelog

## 2.2.0 — Self-healing + first-run

- `--retries N`: auto-repeats failures classified `retry=auto` with backoff
  (each attempt logged; last result stands)
- `doctor [--fix]`: host + ADB server health with safe auto-repair
  (server restart, stale Wi-Fi cleanup); new `env-missing` cause
- `setup [--mode usb|wifi]`: first-run wizard that guides, waits, and proves
  with a screenshot
- `diag [--out bundle.zip]`: support bundle (status + read-only eval +
  versions) for handoffs
- MCP grows to 35 tools (`setup`, `doctor`, `diag`)

- `observe`: screenshot + elements + foreground app + screen size in ONE call
- `wait-for --text/--package [--focused]/--change` with explicit timeout cause
  (replaces blind sleeps)
- `--verify` on tap/swipe/key/type/launch: reports `ui_changed`,
  `changed_pct`, and a next-step hint
- Every `--json` result carries `ts` + `elapsed_ms`; every error carries
  `retry: auto|user|no` so the agent knows retry vs ask-user vs fix-request
- `--verbose` echoes raw adb invocations; `--max-chars` caps long outputs
  (default 8000) to protect context
- MCP grows to 32 tools (`observe`, `wait_for`)
- Fixed: `--help` crash on legacy Windows consoles (cp1252 arrow)

## 2.0.0 — Autonomy + device power

- 10 new `phone.py` commands (`observe`/`wait-for` in 2.1.0 bring it to 28):
  `notify`, `clip`, `record`, `files`, `desk` (scrcpy virtual display),
  `fleet`, `ground`, `run`, `recipe`, `eval`
- Pluggable vision grounding: built-in XML, self-hosted OmniParser-style
  (`OMNIPARSER_URL`), any OpenAI-compatible VLM (`VLM_GROUND_URL`);
  `screenshot --annotate` Set-of-Mark overlays (Pillow)
- Deterministic recipes (`run` with `expect`, failure screenshots,
  `--from-step` resume) + `--dry-run` previews + `--log-dir` JSONL transcripts
- `mcp_server.py`: 30 MCP tools over stdio, zero new dependencies,
  `shell` gated behind `MCP_ALLOW_SHELL=1`
- `mirror --otg` (HID control without debugging), scrcpy flag passthrough
- Fixed: `--help` crash on legacy Windows consoles (cp1252), global flags
  before the subcommand, duplicate `status` parser
- Docs: `device-power.md`, `autonomy.md`, `grounding.md`, `mcp.md`;
  installer adds Pillow; one-command root `install.ps1`

## 1.0.0 — Stable Android core

- `phone.py` with 16 commands: connect (USB/Wi-Fi/emulator), screenshot,
  dump, tap/swipe/type/key, launch, packages, shell, mirror, tunnel
- `SKILL.md` with diagnose-first cause table and troubleshooting matrix
- `install-deps.ps1` (adb + scrcpy + uiautomator2), 8 reference docs,
  connection checklist, example flows
