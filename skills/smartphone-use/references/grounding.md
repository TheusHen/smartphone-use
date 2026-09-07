# Vision Grounding (V2)

`tap --text` and `ground` resolve a natural-language query to pixels through
pluggable providers. `auto` (default for `ground`) tries XML first and falls
back to configured remote providers; `tap --text` defaults to `xml`
(instant, offline) with `--provider` opt-in.

## Tier 1 — xml (built-in, default)

`uiautomator dump` → match text/content-desc/resource-id → tap center.
Free, instant, no network. Blind on: secure windows (banking, permissions),
canvas/custom views, DRM. That blindness is real — switch tiers, don't retry.

## Tier 2 — omniparser (self-hosted, optional)

Any service honoring this contract, configured via `OMNIPARSER_URL`:

- Request: `POST {"image": "<base64 png>", "query": "..."}`
- Response: `{"elements": [{"x": px, "y": py, "label": "..."}]}`

The skill substring-matches `query` against `label` and taps the first hit;
on no match it lists visible labels instead of guessing. Adapt the 20-line
`ground_with_omniparser` if your service speaks a different shape.

## Tier 3 — vlm (any OpenAI-compatible vision endpoint, optional)

Configure `VLM_GROUND_URL` (+ `VLM_GROUND_KEY`, `VLM_GROUND_MODEL`). This
works with UI-TARS/GUI-Owl served via vLLM, Qwen-VL endpoints, or frontier
VLMs — anything speaking `/v1/chat/completions` with `image_url`. The model
is asked for `{"x","y"}` in 0–1000 relative coordinates, converted to device
pixels from the PNG header (no Pillow needed).

```powershell
$env:VLM_GROUND_URL = "http://localhost:8000/v1/chat/completions"
$env:VLM_GROUND_MODEL = "ui-tars"
python scripts/phone.py ground "blue Send button" --provider vlm --json
```

## Set-of-Mark annotation

`screenshot out.png --annotate` (needs Pillow, in install-deps) draws
numbered boxes over clickable elements — the overlay pattern that turns
coordinate guessing into "element N" selection. `ground ... --annotate
mark.png` marks the single resolved point for verification.

## Cost/latency guidance

xml ≈ 1 s free → omniparser ≈ 1–2 s self-hosted → vlm ≈ seconds + tokens.
Keep recipes on xml; spend vision calls on the 5% of screens where XML is
blind. If a VLM call returns non-coordinates, the error shows the raw reply
— sharpen the query (color, nearby label) and retry once.
