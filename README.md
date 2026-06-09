# Decky Retroid Pocket 5 LED Control

A Decky plugin for controlling the Retroid Pocket 5's analog stick LEDs.

## Features

- On/off toggle with last-color memory
- Brightness slider (0–255)
- Full RGB color control via R/G/B sliders
- 9 built-in color presets (White, Red, Orange, Yellow, Green, Cyan, Blue, Purple, Pink)
- Custom presets — save the current color/brightness as a named preset,
  re-apply with one tap, persisted across reboots
- Independent left/right stick control with a Sync toggle (per-side colors)
- Live color preview swatches
- Persistent state across plugin reloads and reboots
  (stored under Decky's plugin settings dir)

## How it works

Verified against the ROCKNIX SM8250 quirk scripts.

The backend relies exclusively on ROCKNIX tooling:

- **ON:** `/usr/bin/analog_sticks_ledcontrol <brightness> <Rr> <Rg> <Rb> <Lr> <Lg> <Lb>`
  Note: **right side first, then left**. Brightness is shared between sticks;
  colors can differ per side.
- **OFF:** `/usr/bin/ledcontrol off`

LED state is also written to `/storage/.config/system/configs/system.cfg`
(via `set_setting`) so that the wake-from-sleep restore scripts pick up the
plugin's values instead of reverting to the ES-DE defaults.

## State caching

ROCKNIX tooling does not expose current LED state. The backend caches the last
non-zero color and brightness (per side) in a JSON file, which is restored on
re-enable and on plugin reload.

## Build

```sh
npm install   # or: npm install
npm build     # or: npm run build
```

## Install (dev)

Install the plugin through DeckyLoader by enabling developer mode first and then selecting the downloaded release zip.
