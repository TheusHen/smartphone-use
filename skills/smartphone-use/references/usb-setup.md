# USB Setup (Android → Windows)

Step-by-step to get a physical phone visible to `phone.py status` over USB.

## 1. Enable Developer options on the phone

1. Settings → About phone → tap **Build number** 7 times (enter PIN if asked)
   until "You are now a developer!".
2. Settings → System → Developer options (location varies by vendor) →
   enable **USB debugging**.
3. Leave **Verify apps over USB** ON. Leave **OEM unlocking** OFF.

## 2. Connect with a DATA cable

- Many cheap cables are **charge-only** — if the PC makes no USB sound at
  all, swap cables first. Prefer the vendor cable, direct PC port (no hub).
- On the phone's USB prompt pick **File transfer / MTP** (some phones hide
  ADB behind "Charge only").
- Accept **"Allow USB debugging?"**, tick **Always allow from this computer**,
  and check the RSA fingerprint matches your PC.

## 3. Verify

```powershell
python scripts/phone.py status --json
# want: devices: [{serial: "<id>", state: "device", ...}]
```

## 4. If the RSA prompt never appears

1. Developer options → **Revoke USB debugging authorizations** → replug.
2. Toggle USB debugging OFF → wait 5 s → ON → replug.
3. `adb kill-server` then `adb start-server`, replug.
4. Windows "Unknown USB device" → install the vendor **OEM USB driver**
   (Google USB Driver / Samsung / Motorola / Xiaomi), then replug.

## 5. Done = `state: device`

Anything else (`unauthorized`, `offline`, empty) → see
`troubleshooting.md` and report the exact `status --json` output.
