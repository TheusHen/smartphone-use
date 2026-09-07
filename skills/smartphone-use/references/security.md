# Security Model

## Why ADB stays on trusted networks

- `adb shell` = shell user (uid 2000) on the device; on rooted/vendor boxes
  it escalates to root. Remote code execution, silent APK installs, file and
  credential theft all follow from one open port.
- ADB-over-TCP (default 5555) historically offers no password prompt; modern
  Wireless debugging adds pairing, but misconfiguration and unpatched stacks
  (e.g. CVE-2026-0073, a wireless-ADB auth bypass patched at the 2026-05-01
  SPL) keep hostile-LAN exposure dangerous.
- Shodan-style scans routinely find open 5555s with crypto-miner/auth-bypass
  worms from past outbreaks. Treat any Internet-facing 5555 as compromised.

## Rules for this skill

1. USB or same-LAN Wi-Fi only, by default.
2. Internet traversal ONLY inside SSH (`phone.py tunnel --via ssh`) or a
   mesh VPN (`--via vpn`). No exceptions, no "just this once" port-forward.
3. After sessions: `phone.py disconnect` (resets to USB transport), turn
   Wireless/USB debugging OFF, revoke authorizations on shared PCs.
4. Keep devices on the latest security patch; never enable ADB on devices
   you don't own or on unknown PCs.
5. Screenshots/dumps are sensitive data — keep them local to the task,
   delete when done.
