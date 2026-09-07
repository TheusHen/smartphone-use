# Semantic vs Coordinate Acting

Two ways to drive the screen. Prefer the first; use the second when the
first is blind.

## 1. Semantic (`dump` → `--text`) — default

`phone.py dump` returns on-screen elements with `text`, `content_desc`,
`resource_id`, `bounds`, `clickable`. `phone.py tap --text "Save"` resolves
the label to the element center and taps it.

Why better: survives layout shifts, DPI differences, translations of
*position* (not language), and works without eyeballing pixels.

Rules:

- Dump FRESH before acting — screens change; a 30-second-old tree lies.
- Prefer `resource_id` matches (stable across languages), then visible text,
  then content-desc.
- If several nodes match, `phone.py` picks the first clickable one; if the
  tap lands wrong, re-dump and disambiguate with a longer/more exact string.
- Element text changes per app version — when `--text` stops matching after
  an app update, re-dump and learn the new label instead of hardcoding.

## 2. Coordinates (screenshot) — fallback

Use when: secure windows hide the hierarchy (banking, permissions, DRM),
canvas/custom views expose no nodes, or you need gestures (multi-step swipes,
pinch approximations via quick swipe pairs).

Rules:

- Screenshot FRESH, then map pixels 1:1 — never reuse coordinates across
  orientation changes (portrait ↔ landscape invalidates everything).
- Scrolls: full-screen vertical swipe `swipe 540 1600 540 600`; horizontal
  carousels: `swipe 900 1000 200 1000`. Repeat + re-screenshot until the
  target appears (max ~5 attempts, then ask).
- Confirm every tap with a new screenshot: no change = missed tap = adjust,
  don't blindly continue the script.
