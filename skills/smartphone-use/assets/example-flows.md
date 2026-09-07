# Example Flows (copy-paste walkthroughs)

All commands run from the skill folder (`scripts/` on PATH or prefixed).
Add `-s SERIAL` whenever more than one device is attached.

## 0. First run (V2.2)

```powershell
python scripts/phone.py doctor --fix        # host health + safe repairs
python scripts/phone.py setup --mode usb    # wizard: guides, waits, proves
python scripts/phone.py awake on            # keep screen on while you think
# flaky daemon? let retryable failures repeat themselves:
python scripts/phone.py --retries 2 tap --text "Save" --verify
# stuck? hand over a bundle, not screenshots of terminals:
python scripts/phone.py diag --out diag.zip
# done? restore everything:
python scripts/phone.py awake off
python scripts/phone.py disconnect
```

## 0b. Locked while away — recovery

```powershell
python scripts/phone.py awake status --json
# keyguard_locked: true → STOP. Tell the user:
#   "phone is locked — unlock it once and tell me"
# on confirmation:
python scripts/phone.py awake on
python scripts/phone.py observe now.png --json   # resume from CURRENT screen
```

## 1. Prove the connection (Settings toggle)

```powershell
python scripts/phone.py status --json
python scripts/phone.py connect-usb
python scripts/phone.py launch com.android.settings
python scripts/phone.py screenshot settings.png
python scripts/phone.py dump --out ui.xml
python scripts/phone.py tap --text "Wi-Fi"
python scripts/phone.py screenshot wifi.png
python scripts/phone.py key 4
python scripts/phone.py disconnect
```

## 2. Take a note (semantic acting)

```powershell
python scripts/phone.py launch com.google.android.keep  # or your notes app
python scripts/phone.py tap --text "New note"
python scripts/phone.py type "Buy milk"
python scripts/phone.py tap --text "Save"
python scripts/phone.py screenshot note.png   # verify it saved
```

## 3. Set an alarm (coordinates + keys)

```powershell
python scripts/phone.py launch com.google.android.deskclock
python scripts/phone.py screenshot clock.png
python scripts/phone.py tap 540 1800        # + button (adjust to screenshot)
python scripts/phone.py screenshot alarm.png
python scripts/phone.py key 4
```

## 4. Scroll a feed until an item appears

```powershell
python scripts/phone.py launch com.example.app
python scripts/phone.py swipe 540 1600 540 600
python scripts/phone.py screenshot feed1.png
python scripts/phone.py dump | Select-String "Target item"
# repeat swipe+screenshot up to ~5x, then tap --text "Target item"
```

## 5. Human watches via mirror while the agent acts

```powershell
python scripts/phone.py mirror            # opens scrcpy window for the user
python scripts/phone.py tap --text "Next" # agent keeps driving via CLI
```

## 6. Deterministic recipe (V2 autonomy)

```powershell
python scripts/phone.py recipe new morning --out morning.recipe.json
# edit the steps, validate:
python scripts/phone.py recipe check morning.recipe.json
# preview, then run with evidence:
python scripts/phone.py run morning.recipe.json --dry-run
python scripts/phone.py run morning.recipe.json --shots ./shots --log-dir ./shots
# on failure at step 2: inspect shots/FAIL_step2.png, then resume:
python scripts/phone.py run morning.recipe.json --from-step 2 --shots ./shots
```

## 7. Desk mode + notifications + recording (V2 device power)

```powershell
python scripts/phone.py desk org.mozilla.firefox --size 1280x960
python scripts/phone.py notify --filter "Download"
python scripts/phone.py record demo.mp4 --seconds 15
python scripts/phone.py files pull /sdcard/DCIM/photo.jpg ./
python scripts/phone.py fleet --json
```

## 8. Vision grounding when XML is blind (V2)

```powershell
$env:VLM_GROUND_URL = "http://localhost:8000/v1/chat/completions"
python scripts/phone.py ground "blue Send button" --provider vlm --annotate mark.png
python scripts/phone.py tap --text "Send" --provider auto
python scripts/phone.py screenshot marked.png --annotate
```

## 9. Observe-first loop with verification (2.1+)

```powershell
python scripts/phone.py observe now.png --json
python scripts/phone.py tap --text "Save" --verify --json
python scripts/phone.py wait-for --text "Saved" --timeout 15 --json
# slow loader? wait for any visual change instead of sleeping:
python scripts/phone.py wait-for --change --timeout 10 --json
# debugging the mapping:
python scripts/phone.py --verbose tap 540 1200
```
