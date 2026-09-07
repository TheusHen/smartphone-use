# MCP Server (V2)

`scripts/mcp_server.py` exposes all of `phone.py` as MCP tools over stdio —
zero third-party dependencies (hand-rolled JSON-RPC, newline-delimited).
## Wiring (client config)

```json
{
  "mcpServers": {
    "smartphone-use": {
      "command": "python",
      "args": ["<PATH-TO-REPO>/skills/smartphone-use/scripts/mcp_server.py"],
      "env": {
        "ANDROID_SERIAL": "",
        "VLM_GROUND_URL": "",
        "MCP_ALLOW_SHELL": "0"
      }
    }
  }
}
```

## Tools (35)

`status screenshot dump tap swipe type key launch packages notify clip_get
clip_set record files_pull files_push files_ls ground fleet desk mirror run
recipe_new recipe_check eval connect_usb pair connect_wifi disconnect tunnel
shell`

- Results are the same JSON `phone.py --json` emits, wrapped as MCP text
  content with `isError` set from the exit code (cause+action preserved).
- `mirror`/`desk` always launch **detached** (never block the server loop).
- `shell` is **disabled** unless `MCP_ALLOW_SHELL=1` — raw adb shell from an
  agent session is the one tool that can exfiltrate or brick; gate it.
- `ANDROID_SERIAL` sets the default `-s` for all calls.

## Debugging

Run it manually and speak protocol:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python scripts/mcp_server.py
```

Diagnostics go to stderr; stdout carries ONLY protocol messages (phone.py
output is captured per-call). If a client shows empty results, check the
server's stderr first.
