# Internet Access via Tunnel (the ONLY allowed shape)

Raw ADB over the Internet (`adb connect <public-ip>:5555`) is forbidden:
`adbd` on TCP has effectively no authentication, yields a shell plus silent
app installs, and is exploited in the wild (see `security.md`). If the phone
(or the PC holding it over USB) is remote, traffic MUST ride inside an
authenticated encrypted tunnel. `phone.py tunnel` prints these recipes.

## Option 1 — SSH tunnel (recommended)

Remote PC has the phone on USB and runs `adb start-server`.

1. Keep this open on your local machine:
   `ssh -CN -L5038:localhost:5037 -R27183:localhost:27183 user@remote-pc`
   (local 5038 → remote ADB server; remote 27183 ← local scrcpy tunnel port).
2. In another terminal:
   ```powershell
   $env:ADB_SERVER_SOCKET = 'tcp:localhost:5038'
   scrcpy --tunnel-host=localhost
   ```
   Or point `phone.py` at it the same way (it honors `ADB_SERVER_SOCKET`
   because it shells out to `adb`).
3. Close the SSH session when done — access ends with it.

## Option 2 — Mesh VPN (simplest for non-SSH users)

1. Install Tailscale (or equivalent WireGuard mesh) on the phone AND the PC.
2. Join both to the same private tailnet.
3. `phone.py connect-wifi <PHONE-VPN-IP>:5555` — ADB now travels inside
   WireGuard encryption, reachable only within your tailnet.

## Never

- Port-forward 5555 on a home router.
- Run `adb tcpip 5555` on a phone you then take to a café/office network.
- Leave a paired/connected wireless endpoint alive after the session:
  `phone.py disconnect`, Wireless debugging OFF.
