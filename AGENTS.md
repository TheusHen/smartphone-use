# AGENTS.md — Smartphone Use

Repo for AI agents working on this project. The product is an agent skill
(`SKILL.md` + CLI + MCP server) that lets AI agents connect to and operate
Android devices via ADB.

## Layout

```
install.ps1                              # one-command setup (repo root)
README.md / CHANGELOG.md / LICENSE / .gitignore / skills.sh.json
skills/smartphone-use/                   # canonical skill (skills.sh layout)
  SKILL.md                               # agent instructions (keep < ~300 lines)
  scripts/phone.py                       # the CLI — 31 subcommands, v2.2.0
  scripts/mcp_server.py                  # MCP stdio server — 35 tools
  scripts/install-deps.ps1               # Windows deps (adb + scrcpy + pillow)
  references/*.md                        # deep dives (load on demand, 1 topic each)
  assets/                                # checklist-connection.txt, example-flows.md
.agents/skills + .claude/skills + .cursor/skills  # links → canonical (install.ps1, gitignored)
```

## Hard rules

1. **Stdlib-only in `phone.py` and `mcp_server.py`.** Pillow/`uiautomator2`
   are optional enhancements with graceful degradation — never hard imports
   at module level (import inside functions, catch `ImportError` → `PhoneError`
   with install hint).
2. **Every failure is a `PhoneError(cause, message, action)`.** Causes are
   stable strings (`adb-missing`, `unauthorized`, `timeout`, …) in
   `RETRY_POLICY` + `EXIT_CAUSE`. Never invent a new cause without adding it
   to both maps and to `SKILL.md` §3.
3. **Every `--json` result flows through `emit_ok`/`emit_error`** (envelope:
   `ts`, `elapsed_ms`, `retry`). Never `print()` raw JSON elsewhere —
   `mcp_server.py` captures stdout per call and a second document breaks it.
4. **No stdout pollution in `mcp_server.py`.** Diagnostics → stderr only.
   New tools need an entry in `TOOLS` + `ns()` defaults covering every attr
   the `cmd_*` touches (missing attrs = `AttributeError` at call time).
5. **Windows-console safe:** no chars outside cp1252 in CLI help/output
   paths (the `→` crash). Device-originated text is covered by the UTF-8
   reconfigure at import — keep it.
6. **No raw ADB-to-Internet, ever.** Remote = SSH/VPN recipes only
   (`tunnel`, `references/internet-tunnel.md`). No root/OEM-unlock/bypass
   features, no dedicated WhatsApp flows.
7. **Docs stay in sync:** new command → SKILL.md table + README table +
   reference doc + `example-flows.md` snippet + CHANGELOG entry.
   Counts live in README layout line, README MCP line, `references/mcp.md`.

## Workflows

```powershell
# validate (no device needed for these)
python -m py_compile skills/smartphone-use/scripts/phone.py skills/smartphone-use/scripts/mcp_server.py
python skills/smartphone-use/scripts/phone.py <cmd> --help
python skills/smartphone-use/scripts/phone.py status --json        # expect adb-missing JSON, exit 5
python skills/smartphone-use/scripts/phone.py eval --json         # per-check verdicts
# with a device attached:
python skills/smartphone-use/scripts/phone.py eval --act --json   # full self-test
```

For logic changes without a device, write a throwaway in-process test that
monkeypatches `phone.run_adb`/`phone.capture_png` (see pattern: stub `devices
-l`, `uiautomator dump` + `pull`, `pidof`, `dumpsys window`) under
`$env:TEMP/opencode/`, run it, delete it. Never commit test scaffolding or
`__pycache__/` (gitignored).

## Skill spec compliance

- `SKILL.md` frontmatter: `name` must equal the directory name
  (`smartphone-use`), `description` ≤ 1024 chars, must state when to trigger.
- Keep `SKILL.md` procedural and push detail to `references/`; body texture
  (tables, flows) over prose.
- `install.ps1` is idempotent (re-runnable links + PATH reload); keep it so.
