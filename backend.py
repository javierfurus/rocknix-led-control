import glob
import json
import logging
import os
import subprocess
from typing import Dict, List, Optional, Tuple

# Decky redirects the root logger to its plugin log file, so plain logging works.
log = logging.getLogger("rp5-led")


def _clamp(value, lo=0, hi=255) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


class LEDController:
    """Backend for Retroid Pocket 5 analog stick LED control.

    Strategy (per side):
      1. Prefer the ROCKNIX helper /usr/bin/analog_sticks_ledcontrol when present
         (single call sets both sides; we fall back to sysfs for per-side writes).
      2. Otherwise write directly to the sysfs LED nodes under /sys/devices/platform.
      3. For a true off, prefer /usr/bin/ledcontrol off if available.

    State caching:
      The sysfs nodes only report the *current* values. When the LEDs are switched
      off, brightness reads as 0 and the prior color is lost. We cache the last
      non-zero color/brightness in a JSON file so the UI can restore it on
      re-enable and across plugin reloads.
    """

    BASE = "/sys/devices/platform"
    HELPER = "/usr/bin/analog_sticks_ledcontrol"
    LEDCONTROL = "/usr/bin/ledcontrol"
    # Defaults (used if no settings_dir is provided); /tmp is fine for the
    # state cache but custom presets need a persistent directory.
    STATE_FILE = "/tmp/decky-rp5-led-state.json"
    PRESETS_FILE = "/tmp/decky-rp5-led-presets.json"

    def __init__(self, settings_dir: Optional[str] = None):
        # If a persistent settings dir is provided (typically
        # decky.DECKY_PLUGIN_SETTINGS_DIR), put both the state cache and the
        # custom presets file there. Otherwise fall back to /tmp.
        if settings_dir:
            try:
                os.makedirs(settings_dir, exist_ok=True)
                self.STATE_FILE = os.path.join(settings_dir, "led-state.json")
                self.PRESETS_FILE = os.path.join(settings_dir, "led-presets.json")
            except Exception as e:
                log.warning("could not use settings_dir %s: %s", settings_dir, e)

        self.left_brightness = sorted(
            glob.glob(f"{self.BASE}/multi-ledl*/leds/rgb:l*/brightness")
        )
        self.right_brightness = sorted(
            glob.glob(f"{self.BASE}/multi-ledr*/leds/rgb:r*/brightness")
        )
        self.left_intensity = sorted(
            glob.glob(f"{self.BASE}/multi-ledl*/leds/rgb:l*/multi_intensity")
        )
        self.right_intensity = sorted(
            glob.glob(f"{self.BASE}/multi-ledr*/leds/rgb:r*/multi_intensity")
        )
        log.info("LEDController init: helper=%s ledcontrol=%s "
                 "left_brightness=%d right_brightness=%d "
                 "left_intensity=%d right_intensity=%d "
                 "state_file=%s presets_file=%s",
                 os.path.exists(self.HELPER), os.path.exists(self.LEDCONTROL),
                 len(self.left_brightness), len(self.right_brightness),
                 len(self.left_intensity), len(self.right_intensity),
                 self.STATE_FILE, self.PRESETS_FILE)

        # In-memory cache, hydrated from disk if present.
        self._cache: Dict = self._load_cache()
        self._presets: List[Dict] = self._load_presets()

    # ---------------------------------------------------------------- cache --

    def _default_cache(self) -> Dict:
        return {
            "enabled": True,
            "sync": True,
            "left":  {"brightness": 255, "r": 255, "g": 255, "b": 255},
            "right": {"brightness": 255, "r": 255, "g": 255, "b": 255},
        }

    def _load_cache(self) -> Dict:
        try:
            with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Light shape validation
            for side in ("left", "right"):
                if side not in data or not isinstance(data[side], dict):
                    return self._default_cache()
            return data
        except Exception:
            return self._default_cache()

    def _save_cache(self) -> None:
        try:
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except Exception:
            pass

    # --------------------------------------------------------- custom presets

    def _load_presets(self) -> List[Dict]:
        try:
            with open(self.PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                # Shape-validate each entry
                valid = []
                for p in data:
                    if not isinstance(p, dict):
                        continue
                    if "id" not in p or "name" not in p:
                        continue
                    if "left" not in p or "right" not in p:
                        continue
                    valid.append(p)
                return valid
        except Exception:
            pass
        return []

    def _save_presets(self) -> None:
        try:
            with open(self.PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._presets, f)
        except Exception as e:
            log.exception("save_presets failed: %s", e)

    def list_presets(self) -> Dict:
        return {"ok": True, "presets": self._presets}

    def save_preset(self, name: Optional[str] = None) -> Dict:
        """Persist the current cached state as a named custom preset.

        If `name` is empty or None, auto-generate "Custom N".
        """
        # Auto-name if no name supplied
        clean_name = (name or "").strip()
        if not clean_name:
            existing_nums = []
            for p in self._presets:
                n = p.get("name", "")
                if n.startswith("Custom "):
                    try:
                        existing_nums.append(int(n[len("Custom "):]))
                    except ValueError:
                        pass
            next_num = (max(existing_nums) + 1) if existing_nums else 1
            clean_name = f"Custom {next_num}"

        # Generate an id unique within this presets list, even on rapid
        # successive saves within the same millisecond.
        import time
        base_id = f"p_{int(time.time() * 1000)}"
        existing_ids = {p.get("id") for p in self._presets}
        preset_id = base_id
        suffix = 1
        while preset_id in existing_ids:
            preset_id = f"{base_id}_{suffix}"
            suffix += 1

        preset = {
            "id": preset_id,
            "name": clean_name[:32],  # cap length
            "sync": self._cache.get("sync", True),
            "left": dict(self._cache.get("left", {"brightness": 255, "r": 255, "g": 255, "b": 255})),
            "right": dict(self._cache.get("right", {"brightness": 255, "r": 255, "g": 255, "b": 255})),
        }
        self._presets.append(preset)
        self._save_presets()
        log.info("saved preset %s (%s)", preset["id"], preset["name"])
        return {"ok": True, "preset": preset, "presets": self._presets}

    def delete_preset(self, preset_id: str) -> Dict:
        before = len(self._presets)
        self._presets = [p for p in self._presets if p.get("id") != preset_id]
        if len(self._presets) != before:
            self._save_presets()
            log.info("deleted preset %s", preset_id)
            return {"ok": True, "presets": self._presets}
        return {"ok": False, "error": "preset not found", "presets": self._presets}

    def apply_preset(self, preset_id: str) -> Dict:
        """Load a saved preset and apply it to the hardware."""
        match = next((p for p in self._presets if p.get("id") == preset_id), None)
        if not match:
            return {"ok": False, "error": "preset not found"}
        return self._apply(
            enabled=True,
            left=dict(match.get("left", {})),
            right=dict(match.get("right", {})),
            sync=bool(match.get("sync", True)),
        )

    # ----------------------------------------------------------- low-level --

    def _write(self, path: str, value: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)

    def _write_many(self, paths: List[str], value: str) -> None:
        for path in paths:
            self._write(path, value)

    def _has_sysfs(self) -> bool:
        return bool(
            self.left_brightness
            and self.right_brightness
            and self.left_intensity
            and self.right_intensity
        )

    def _read_state_from_sysfs(self) -> Tuple[int, int, int, int, int, int, int, int]:
        """Return (lB, lR, lG, lB_b, rB, rR, rG, rB_b) reading per-side state.

        The four ints per side are brightness, R, G, B.
        """
        def read_side(b_paths: List[str], i_paths: List[str]) -> Tuple[int, int, int, int]:
            bright = 0
            r = g = b = 0
            if b_paths:
                try:
                    with open(b_paths[0], "r", encoding="utf-8") as f:
                        bright = int(f.read().strip())
                except Exception:
                    pass
            if i_paths:
                try:
                    with open(i_paths[0], "r", encoding="utf-8") as f:
                        parts = f.read().strip().split()
                    if len(parts) == 3:
                        # SM8250 sysfs order: B G R
                        b = int(parts[0])
                        g = int(parts[1])
                        r = int(parts[2])
                except Exception:
                    pass
            return bright, r, g, b

        lb, lr, lg, lbb = read_side(self.left_brightness, self.left_intensity)
        rb, rr, rg, rbb = read_side(self.right_brightness, self.right_intensity)
        return lb, lr, lg, lbb, rb, rr, rg, rbb

    # ----------------------------------------------------- helper utilities --

    def _call_helper(self, brightness: int,
                     lr: int, lg: int, lb: int,
                     rr: int, rg: int, rb: int) -> bool:
        """Invoke the ROCKNIX helper.

        Signature (from ROCKNIX SM8250 analog_sticks_ledcontrol):
            <brightness> <Rr> <Rg> <Rb> <Lr> <Lg> <Lb>
        Note the order: RIGHT side first, then LEFT. Brightness is shared.
        """
        if not os.path.exists(self.HELPER):
            log.warning("helper not found at %s", self.HELPER)
            return False
        argv = [self.HELPER, str(brightness),
                str(rr), str(rg), str(rb),
                str(lr), str(lg), str(lb)]
        log.info("invoking helper: %s", " ".join(argv))
        try:
            result = subprocess.run(
                argv, check=False, capture_output=True, text=True, timeout=5,
            )
            log.info("helper rc=%d stdout=%r stderr=%r",
                     result.returncode, result.stdout, result.stderr)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            log.error("helper timed out")
            return False
        except Exception as e:
            log.exception("helper call raised: %s", e)
            return False

    def _call_ledcontrol_off(self) -> bool:
        if not os.path.exists(self.LEDCONTROL):
            return False
        try:
            subprocess.run(
                [self.LEDCONTROL, "off"],
                check=True, capture_output=True, text=True,
            )
            return True
        except Exception:
            return False

    def _call_ledcontrol_rainbow(self) -> bool:
        """Trigger the ROCKNIX built-in rainbow cycle effect."""
        if not os.path.exists(self.LEDCONTROL):
            return False
        try:
            # Note: this is a blocking call (~1s for two HSV sweeps in ROCKNIX),
            # but the helper handles restoring the previous state itself.
            subprocess.Popen(
                [self.LEDCONTROL, "rainbow"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------ write per side --

    def _write_side(self, brightness_paths: List[str], intensity_paths: List[str],
                    brightness: int, r: int, g: int, b: int) -> None:
        self._write_many(brightness_paths, str(brightness))
        # sysfs order is B G R
        self._write_many(intensity_paths, f"{b} {g} {r}")

    def _apply_sysfs(self, l_bright: int, lr: int, lg: int, lb: int,
                     r_bright: int, rr: int, rg: int, rb: int) -> bool:
        if not self._has_sysfs():
            log.warning("sysfs not detected; cannot apply")
            return False
        log.info("sysfs write L=br%d/%d,%d,%d R=br%d/%d,%d,%d",
                 l_bright, lr, lg, lb, r_bright, rr, rg, rb)
        try:
            self._write_side(self.left_brightness,  self.left_intensity,  l_bright, lr, lg, lb)
            self._write_side(self.right_brightness, self.right_intensity, r_bright, rr, rg, rb)
            return True
        except Exception as e:
            log.exception("sysfs write failed: %s", e)
            return False

    # ----------------------------------------------------------- public API --

    def set_all(self, enabled: bool, brightness: int, r: int, g: int, b: int) -> Dict:
        """Set both sides to the same color/brightness (legacy entrypoint)."""
        brightness = _clamp(brightness)
        r, g, b = _clamp(r), _clamp(g), _clamp(b)
        return self._apply(
            enabled=enabled,
            left={"brightness": brightness, "r": r, "g": g, "b": b},
            right={"brightness": brightness, "r": r, "g": g, "b": b},
            sync=True,
        )

    def set_sides(self, enabled: bool,
                  left: Dict, right: Dict, sync: bool = False) -> Dict:
        """Set independent left/right color and brightness."""
        log.info("set_sides enabled=%s sync=%s left=%s right=%s",
                 enabled, sync, left, right)
        left = {
            "brightness": _clamp(left.get("brightness", 255)),
            "r": _clamp(left.get("r", 255)),
            "g": _clamp(left.get("g", 255)),
            "b": _clamp(left.get("b", 255)),
        }
        right = {
            "brightness": _clamp(right.get("brightness", 255)),
            "r": _clamp(right.get("r", 255)),
            "g": _clamp(right.get("g", 255)),
            "b": _clamp(right.get("b", 255)),
        }
        return self._apply(enabled=enabled, left=left, right=right, sync=sync)

    def _apply(self, enabled: bool, left: Dict, right: Dict, sync: bool) -> Dict:
        # OFF path
        if not enabled or (left["brightness"] == 0 and right["brightness"] == 0):
            log.info("apply OFF path")
            method = None
            if self._call_ledcontrol_off():
                method = "ledcontrol"
            elif self._has_sysfs():
                self._apply_sysfs(0, 0, 0, 0, 0, 0, 0, 0)
                method = "sysfs"
            else:
                log.error("no LED off path available")
                return {"ok": False, "error": "No LED control method available"}

            self._cache["enabled"] = False
            self._cache["sync"] = sync
            self._save_cache()
            log.info("OFF applied via %s", method)
            return self._state_payload(method=method, override_enabled=False)

        # ON path — prefer the helper when brightnesses match (it accepts
        # different colors per side but shares one brightness value).
        method: Optional[str] = None
        same_brightness = left["brightness"] == right["brightness"]
        if same_brightness and self._call_helper(
            left["brightness"],
            left["r"], left["g"], left["b"],
            right["r"], right["g"], right["b"],
        ):
            method = "helper"
        elif self._apply_sysfs(
            left["brightness"], left["r"], left["g"], left["b"],
            right["brightness"], right["r"], right["g"], right["b"],
        ):
            method = "sysfs"
        else:
            log.error("no ON path available (no helper, no sysfs)")
            return {"ok": False, "error": "No helper or matching sysfs LED paths found"}

        log.info("ON applied via %s", method)
        # Cache only meaningful (non-zero) state
        self._cache["enabled"] = True
        self._cache["sync"] = sync
        if left["brightness"] > 0:
            self._cache["left"] = left
        if right["brightness"] > 0:
            self._cache["right"] = right
        self._save_cache()

        return self._state_payload(method=method, override_enabled=True)

    # ----------------------------------------------- convenience wrappers --

    def set_enabled(self, enabled: bool) -> Dict:
        state = self._cache
        left = state.get("left", {"brightness": 255, "r": 255, "g": 255, "b": 255})
        right = state.get("right", {"brightness": 255, "r": 255, "g": 255, "b": 255})
        # If brightness was zero in cache (shouldn't be, but be safe), use 255
        if left.get("brightness", 0) == 0:
            left = {**left, "brightness": 255}
        if right.get("brightness", 0) == 0:
            right = {**right, "brightness": 255}
        return self._apply(enabled=enabled, left=left, right=right,
                           sync=state.get("sync", True))

    def set_brightness(self, brightness: int) -> Dict:
        """Legacy: set brightness on both sides."""
        state = self._cache
        b = _clamp(brightness)
        left = {**state.get("left",  {"r": 255, "g": 255, "b": 255}), "brightness": b}
        right = {**state.get("right", {"r": 255, "g": 255, "b": 255}), "brightness": b}
        return self._apply(enabled=b > 0, left=left, right=right,
                           sync=state.get("sync", True))

    def set_color(self, r: int, g: int, b: int) -> Dict:
        """Legacy: set color on both sides."""
        state = self._cache
        r, g, bl = _clamp(r), _clamp(g), _clamp(b)
        left  = {**state.get("left",  {"brightness": 255}), "r": r, "g": g, "b": bl}
        right = {**state.get("right", {"brightness": 255}), "r": r, "g": g, "b": bl}
        # Ensure brightness > 0 so the color is visible
        if left.get("brightness", 0) == 0:
            left["brightness"] = 255
        if right.get("brightness", 0) == 0:
            right["brightness"] = 255
        return self._apply(enabled=True, left=left, right=right,
                           sync=state.get("sync", True))

    # ------------------------------------------------------------- state --

    def _state_payload(self, method: Optional[str] = None,
                       override_enabled: Optional[bool] = None) -> Dict:
        c = self._cache
        return {
            "ok": True,
            "method": method,
            "enabled": override_enabled if override_enabled is not None
                       else c.get("enabled", False),
            "sync": c.get("sync", True),
            "left": c.get("left"),
            "right": c.get("right"),
        }

    def play_rainbow(self) -> Dict:
        """Play the ROCKNIX rainbow effect (non-blocking). After the effect
        the helper restores the previous state on its own."""
        if self._call_ledcontrol_rainbow():
            return {"ok": True, "method": "ledcontrol"}
        return {"ok": False, "error": "ledcontrol not available"}

    def get_state(self) -> Dict:
        # Read hardware to detect external changes (e.g. another tool turned LEDs off).
        try:
            lb, lr, lg, lbb, rb, rr, rg, rbb = self._read_state_from_sysfs()
            hw_on = lb > 0 or rb > 0
        except Exception:
            hw_on = self._cache.get("enabled", False)

        payload = self._state_payload(override_enabled=hw_on and self._cache.get("enabled", True))
        payload["has_helper"] = os.path.exists(self.HELPER)
        payload["has_ledcontrol"] = os.path.exists(self.LEDCONTROL)
        payload["sysfs_detected"] = self._has_sysfs()
        return payload

    def get_capabilities(self) -> Dict:
        # Split colors are possible via either path. Split brightness needs sysfs
        # (the helper only takes a single brightness value).
        has_helper = os.path.exists(self.HELPER)
        has_sysfs = self._has_sysfs()
        return {
            "ok": True,
            "supports_enabled": True,
            "supports_brightness": True,
            "supports_color": True,
            "supports_split": has_helper or has_sysfs,
            "supports_split_brightness": has_sysfs,
            "has_helper": has_helper,
            "has_ledcontrol": os.path.exists(self.LEDCONTROL),
            "sysfs_detected": has_sysfs,
        }
