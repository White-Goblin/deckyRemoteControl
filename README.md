# Remote Control for Decky Loader

<p align="center">
  <img src="assets/screenshots/portrait.png" alt="The phone page in portrait" height="460">
  <img src="assets/screenshots/landscape.png" alt="The phone page in landscape" height="460">
</p>

## What it is

A Decky Loader plugin that turns your phone or tablet into a touchpad and keyboard for your SteamOS device. Scan a QR code and a web page opens that controls the screen. **Nothing to install on the phone.**

## Features

- **Touchpad:** drag to move, tap to click, two-finger tap to right-click, two-finger drag or flick to scroll.
- **Arrow wheel:** arrows with Enter in the middle. Hold an arrow to repeat, or spin your thumb around the wheel to scroll.
- **Buttons:** Steam menu, Quick Access, Esc and the phone keyboard.
- **Volume:** down, mute and up.
- **Quit app:** shows the name of the running app, and takes two taps to close it.
- **Typing:** use your phone's own keyboard, including swipe typing, autocorrect, dictation and emoji.
- **Cursor:** a translucent circle with a trail follows your touch and disappears when you stop.
- **Portrait and landscape** layouts.
- Turn it on once and it stays on after reboots and updates.

## How to install

1. In Decky, turn on developer mode (Settings → General).
2. Download `Remote-Control-v1.0.0.zip` from the [Releases](../../releases) page, then choose Settings → Developer → **Install plugin from ZIP**. Or choose **Install plugin from URL** and paste the release's zip link.
3. Open the Quick Access menu, pick **Remote Control**, turn it on, and scan the QR code with your phone's camera (same network as the device).
4. Optional: use *Add to Home Screen* in your phone's browser to keep it like an app.

## Notes and limitations

- **Give the device a fixed IP address** (a DHCP reservation in your router). The link uses the device's IP address, so it only stays the same if the address does.
- **mDNS is optional:** the **Use mDNS** switch uses a host name instead of the IP address. It may be slow or not work on some phones and networks (iPhones can take several seconds to open it), so a fixed IP is the better choice.
- **Local network only:** the page is not encrypted, and anyone with the link can control the device. Use it on a network you trust. **New link** in the panel cancels the old link.
- **iPhone vs Android:** on iPhone, *Add to Home Screen* opens full screen like an app. On Android it creates a shortcut that opens in the browser.
- **Tested** on a Steam Machine with an iPhone. Other devices and phones may behave differently, and the cursor has not been tried over every game.
- Some special characters may not type in every app.

## Built with AI

This plugin was written with AI assistance (Claude, by Anthropic). The AI wrote most of the code, and the maintainer directed the work and tested it on real hardware. The commit history carries `Co-Authored-By` lines. Because the official Decky Plugin Store does not accept AI-written code, this plugin is not on the store and is installed manually. As with any plugin from outside the store, read the code if you want to be sure of what it does.

## Credits and licenses

BSD 3-Clause (see `LICENSE`). Built from the [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template). The overlay layering code in `src/useUIComposition.tsx` is adapted from [rossimo/decky-pip](https://github.com/rossimo/decky-pip) (BSD-3-Clause). QR codes are drawn with [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) (MIT).
