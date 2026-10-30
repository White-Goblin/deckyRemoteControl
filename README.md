# Remote Control for Decky Loader

Use a phone or tablet as a touchpad and keyboard for your SteamOS device. Scan a QR code — **no app install** on the phone.

- Touchpad: drag = move, tap = click, two-finger tap = right-click, two-finger drag = scroll, and a two-finger flick keeps scrolling with momentum
- A translucent circular cursor with a speed-based trail appears in Steam's UI while you use it and disappears ~1.5 s after you stop
- Phone keyboard input (swipe typing, autocorrect, dictation, emoji)
- Buttons under the touchpad: Steam menu and Quick Access (bottom left, each toggles its menu), arrow keys in a cross (bottom middle, hold to repeat), Esc, Enter and the keyboard (bottom right). **Quit app** sits alone at the top right and needs a second tap within 3 seconds to close the running app
- Idle cost is zero: the server only runs while the toggle is on; no overlay exists unless input is arriving

## Persistence and links
- **Stays on:** turn the server on once and it comes back by itself after reboots, Decky restarts and plugin updates (the choice is saved). If the system is not ready yet at boot, it keeps retrying in the background. Turning it off in the panel also sticks.
- **The link does not change.** It contains a token that is generated once and kept in the plugin's settings (outside the plugin folder, so updates keep it). Only the panel's **New link** button replaces it, and then every saved link stops working. Keep the plugin's name ("Remote Control") unchanged, because the settings folder is named after it.
- **Name, not address:** the link uses the device's network name (`http://<hostname>.local:8765/...`) so it keeps working when the device's IP address changes. The panel also shows the IP form as a fallback, and a switch to use the IP form in the link if your phone cannot open `.local` names (some older Android versions).
- **Saving it as an app:** open the link on your phone and use *Add to Home Screen*. The page has an icon and a web app manifest. iOS then opens it full screen like an app. On Android the page is plain HTTP, so Chrome offers a shortcut that opens in the browser instead of installing it (installing needs HTTPS).

## Install (Decky developer mode)
1. Build the zip: `./scripts/package.sh` (needs node + pnpm 9), or download it from Releases.
2. Decky settings → Developer → *Install plugin from ZIP* (or unzip into `~/homebrew/plugins/`), then restart Decky: `sudo systemctl restart plugin_loader`.
3. Open the Quick Access Menu → Remote Control → enable → scan the QR with the phone (same network).

## How it works
Python backend (stdlib only) serves the touch page + a WebSocket and injects input through `/dev/uinput` (a standard relative mouse + keyboard); no root is needed on SteamOS, where the logged-in user can open `/dev/uinput`. The URL contains a random token; "New link" revokes it.

## Limitations
- The drawn cursor follows the real pointer: it is resynced from the X server of whichever window has focus while you touch the phone. While Quick Access has focus it cannot be read, so it relies on counting your moves until focus returns.
- The cursor is drawn in its own Steam browser view (above menus and games). That view would swallow clicks, so it is hidden for a moment while a tap happens (a brief flicker).
- Characters outside ASCII use `xdotool` if installed, otherwise Ctrl+Shift+U entry (works in GTK apps only).
- Traffic is plain HTTP/WS on your LAN; use it on trusted networks.
