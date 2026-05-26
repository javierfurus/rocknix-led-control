import os
import sys
from typing import Dict

import decky  # provided by decky-loader at runtime

# Decky's sandboxed plugin loader does not add the plugin directory to sys.path,
# so a plain `from backend import ...` fails with ModuleNotFoundError.
# Add this file's directory to sys.path before importing local modules.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from backend import LEDController  # noqa: E402


class Plugin:
    async def _main(self):
        decky.logger.info("RP5 LED Control: loading")
        # Use Decky's recommended settings directory so custom presets persist
        # across reboot, plugin updates, etc. Falls back to None (→ /tmp) if
        # the env var isn't set.
        settings_dir = getattr(decky, "DECKY_PLUGIN_SETTINGS_DIR", None)
        self.led = LEDController(settings_dir=settings_dir)
        caps = self.led.get_capabilities()
        decky.logger.info(f"RP5 LED Control: caps={caps}")

    async def _unload(self):
        decky.logger.info("RP5 LED Control: unloading")

    async def _uninstall(self):
        decky.logger.info("RP5 LED Control: uninstalling")

    # --- capability / state probes ---

    async def get_capabilities(self) -> Dict:
        return self.led.get_capabilities()

    async def get_state(self) -> Dict:
        return self.led.get_state()

    # --- legacy (both sides synced) ---

    async def set_enabled(self, enabled: bool) -> Dict:
        return self.led.set_enabled(enabled)

    async def set_brightness(self, brightness: int) -> Dict:
        return self.led.set_brightness(brightness)

    async def set_color(self, r: int, g: int, b: int) -> Dict:
        return self.led.set_color(r, g, b)

    async def set_all(self, enabled: bool, brightness: int,
                      r: int, g: int, b: int) -> Dict:
        return self.led.set_all(enabled, brightness, r, g, b)

    # --- independent left/right ---

    async def set_sides(self, enabled: bool, left: Dict, right: Dict,
                        sync: bool = False) -> Dict:
        return self.led.set_sides(enabled, left, right, sync)

    # --- effects ---

    async def play_rainbow(self) -> Dict:
        return self.led.play_rainbow()

    # --- custom presets ---

    async def list_presets(self) -> Dict:
        return self.led.list_presets()

    async def save_preset(self, name: str = "") -> Dict:
        return self.led.save_preset(name)

    async def delete_preset(self, preset_id: str) -> Dict:
        return self.led.delete_preset(preset_id)

    async def apply_preset(self, preset_id: str) -> Dict:
        return self.led.apply_preset(preset_id)
