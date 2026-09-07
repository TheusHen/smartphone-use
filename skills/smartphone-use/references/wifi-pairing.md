# Wi-Fi Pairing (Android 11+ Wireless Debugging)

Wireless ADB uses **two different ports**. Confusing them is the #1 failure —
read this file whenever a `pair` / `connect-wifi` step fails.

## The two ports

| Screen on the phone | Gives you | Used with |
|---|---|---|
| Developer options → Wireless debugging → **Pair device with pairing code** | Ephemeral **pairing** endpoint (e.g. `192.168.1.10:37891`, changes every time) + 6-digit code | `phone.py pair <PAIR-ENDPOINT> <CODE>` |
| Developer options → Wireless debugging → **IP address & Port** | Stable **connection** endpoint (e.g. `192.168.1.10:5555`) | `phone.py connect-wifi <CONNECTION-ENDPOINT>` |

## Procedure

1. Phone and PC on the **same Wi-Fi** (guest networks with client isolation
   will not work; VPNs on either side often swallow LAN traffic).
2. Do the USB flow once first (proves authorization works at all).
3. Unplug USB. Enable **Wireless debugging**.
4. `phone.py pair 192.168.1.10:<PAIR-PORT> <CODE>` — codes are single-use
   and expire fast; generate a fresh one on each retry.
5. `phone.py connect-wifi 192.168.1.10:5555` — note: the **connection** port.
6. `phone.py screenshot prove.png` to confirm.

## Failure checklist

- "failed to connect": wrong port (pairing vs connection?), different
  subnet/VLAN, client isolation, Windows Firewall blocking 5555, corp VPN.
- "pairing failed": stale code, wrong (connection) port, not the same Wi-Fi.
- Device goes `offline` after minutes: Wi-Fi sleep — keep the phone awake
  or plugged in while working.
- After the session: `phone.py disconnect` (resets transport to USB) and turn
  Wireless debugging OFF. Never leave the phone listening on TCP.
