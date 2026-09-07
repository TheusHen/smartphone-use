# Autonomy: Recipes, Run Loop, Transcripts, Eval (V2)

V2 adds determinism around the agent: the agent still plans, but execution,
verification, and audit are mechanical.

## Recipes — `recipe new NAME [--out] | recipe check FILE`

A recipe is plain JSON (no YAML dependency):

```json
{
  "name": "morning",
  "steps": [
    {"action": "launch", "package": "com.android.settings"},
    {"action": "expect", "text": "Settings"},
    {"action": "tap", "text": "Network"},
    {"action": "screenshot", "output": "prove.png"}
  ]
}
```

Actions: `launch tap swipe type key wait expect screenshot`.
`tap` takes `{"x","y"}` or `{"text"}` (instant XML grounding — recipes stay
offline and fast by design). `expect` takes `{"text"}` (on-screen label) or
`{"package"}` (live process via pidof). `wait` takes `{"seconds"}`.

## Run — `run RECIPE [--from-step N] [--shots DIR] [--dry-run]`

Executes steps in order, stops at the first mismatch, saves `FAIL_stepN.png`
(when `--shots` is set), and prints how to resume:

```powershell
python scripts/phone.py run morning.recipe.json --shots ./shots
# on failure: phone.py run morning.recipe.json --from-step 2 --shots ./shots
```

`--dry-run` prints the numbered plan without touching the device — use it to
show the user what WILL happen before it happens (approval workflow).

## Transcripts — `--log-dir DIR` (any command)

Appends one JSON line per invocation (`ts, argv, exit`) to
`DIR/steps.jsonl`. Screenshots/recordings land next to it. Keep the directory
per task; delete when done (screens may contain private data).

## Dry-run — `--dry-run` (any mutating command)

`tap/swipe/key/type/launch/shell/record/files push/clip set/desk/run`
print `would_execute` instead of acting. Reads (`status/dump/screenshot/
notify/fleet/ground/eval`) always execute — there is nothing to preview.

## Eval harness — `eval [--act]`

Self-test for the whole stack. Read-only by default (adb, device,
screenshot bytes+PNG magic, dump parses with nodes, battery). `--act` adds
`HOME → launch Settings → pidof → BACK`. Exits 0 only if every check passes.
Run it after installs, updates, or any "it stopped working" report, and
paste its JSON when asking for help.

## Observe-first loops (2.1+)

Prefer `observe shot.png` over `screenshot`+`dump` pairs: one call returns
the PNG path, screen size, foreground package, and up to 60 text elements
with bounds. After acting, prefer `tap … --verify` (reports `ui_changed` +
`changed_pct` + a next-step hint) and `wait-for` over fixed sleeps:

```powershell
python scripts/phone.py observe now.png --json
python scripts/phone.py tap --text "Save" --verify --json
python scripts/phone.py wait-for --text "Saved" --timeout 15 --json
python scripts/phone.py wait-for --change --timeout 10 --json
```

Every `--json` result carries `ts` + `elapsed_ms`; every error carries
`retry: auto|user|no` (`auto` = safe to retry mechanically, `user` = needs a
human, `no` = the request is wrong). `--verbose` echoes raw adb invocations
to stderr; `--max-chars N` caps long outputs (default 8000).
