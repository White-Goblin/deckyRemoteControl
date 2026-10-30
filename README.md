# Remote Control for Decky Loader

Use a phone or tablet as a touchpad and keyboard for your SteamOS device. Scan a QR code — **no app install** on the phone.

- Touchpad: drag = move, tap = click, two-finger tap = right-click, two-finger drag = scroll
- A translucent circular cursor with a speed-based trail appears in Steam's UI while you use it and disappears ~1.5 s after you stop
- Phone keyboard input (swipe typing, autocorrect, dictation, emoji), plus Esc / arrow / Enter buttons
- Idle cost is zero: the server only runs while the toggle is on; no overlay exists unless input is arriving

## Install (Decky developer mode)
1. Build the zip: `./scripts/package.sh` (needs node + pnpm 9), or download it from Releases.
2. Decky settings → Developer → *Install plugin from ZIP* (or unzip into `~/homebrew/plugins/`), then restart Decky: `sudo systemctl restart plugin_loader`.
3. Open the Quick Access Menu → Remote Control → enable → scan the QR with the phone (same network).

## How it works
Python backend (stdlib only) serves the touch page + a WebSocket and injects input through `/dev/uinput` (a standard relative mouse + keyboard); no root is needed on SteamOS, where the logged-in user can open `/dev/uinput`. The URL contains a random token; "New link" revokes it.

## Limitations
- The cursor is drawn in its own Steam browser view (above menus and games). That view would swallow clicks, so it is hidden for a moment while a tap happens (a brief flicker).
- Characters outside ASCII use `xdotool` if installed, otherwise Ctrl+Shift+U entry (works in GTK apps only).
- Traffic is plain HTTP/WS on your LAN; use it on trusted networks.
