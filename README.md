# Decky Retroid Pocket 5 LED Control

A Decky plugin for controlling the Retroid Pocket 5's analog stick LEDs.

## Features

- On/off toggle with last-color memory
- Brightness slider (0–255)
- Full RGB color control via R/G/B sliders
- 9 built-in color presets (White, Red, Orange, Yellow, Green, Cyan, Blue, Purple, Pink)
- Custom presets — save the current color/brightness as a named preset,
  re-apply with one tap, persisted across reboots
- Independent left/right stick control with a Sync toggle (per-side colors and
  per-side brightness when sysfs is available)
- Live color preview swatches
- Persistent state across plugin reloads and reboots
  (stored under Decky's plugin settings dir)

## How it works

Verified against the ROCKNIX SM8250 quirk scripts.

The backend tries three control paths:

1. **`/usr/bin/analog_sticks_ledcontrol`** (ROCKNIX helper).
   Verified signature:
   ```
   analog_sticks_ledcontrol <brightness> <Rr> <Rg> <Rb> <Lr> <Lg> <Lb>
   ```
   Note: **right side first, then left**. Brightness is shared between sticks;
   colors can differ per side. Used when both sides have the same brightness.
2. **Direct sysfs writes** as fallback / for independent brightness:
   - `/sys/devices/platform/multi-ledl*/leds/rgb:l*/brightness`
   - `/sys/devices/platform/multi-ledr*/leds/rgb:r*/brightness`
   - `/sys/devices/platform/multi-ledl*/leds/rgb:l*/multi_intensity`
   - `/sys/devices/platform/multi-ledr*/leds/rgb:r*/multi_intensity`

Sysfs is the only path that supports independent brightness per side.

## State caching

Because sysfs nodes report 0 brightness when LEDs are off, the previous color
would be lost on every off/on cycle. To work around this, the backend caches
the last non-zero color and brightness (per side) in a JSON file, which is
restored on re-enable and on plugin reload.

## Build

```sh
npm install   # or: npm install
npm build     # or: npm run build
```

## Install (dev)

Install the plugin through DeckyLoader by enabling developer mode first and then selecting the downloaded release zip.
