# iOS — Out of Scope (Future Track)

iOS has no ADB equivalent. The automation path is **WebDriverAgent (WDA)**:

- WDA is an HTTP server running on-device via Apple's XCUITest; Appium's
  XCUITest driver (or raw REST) sends tap/swipe/type/screenshot/source calls.
- Requirements: a **Mac with Xcode**, an Apple developer signing identity
  (free accounts re-sign weekly; paid $99/yr lasts a year), first-time USB +
  Trust, `libimobiledevice` for port forwarding (`iproxy 8100:8100`).
- Limits: no polished live mirror (API control, not video), runner must stay
  alive, system security prompts stay manual, no Windows host story.

When v2 tackles iOS, mirror this skill's shape: `status`-style diagnostics,
pair flow replaced by sign-and-trust flow, same screenshot/dump/act loop
against WDA endpoints (`/screenshot`, `/source`, `/wda/tap`, session create
with `bundleId`). Until then: if the user asks for iPhone control, explain
the above and stop — do not improvise with Android commands.
