import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional

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

    ON:  /usr/bin/analog_sticks_ledcontrol <brightness> <Rr> <Rg> <Rb> <Lr> <Lg> <Lb>
    OFF: /usr/bin/ledcontrol off

    State caching:
      LEDCONTROL does not expose current state. We cache the last non-zero
      color/brightness in a JSON file so the UI can restore it on re-enable
      and across plugin reloads.
    """

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

        log.info(
            "LEDController init: helper=%s ledcontrol=%s state_file=%s presets_file=%s",
            os.path.exists(self.HELPER),
            os.path.exists(self.LEDCONTROL),
            self.STATE_FILE,
            self.PRESETS_FILE,
        )
        self._diag_env()

        # In-memory cache, hydrated from disk if present.
        self._cache: Dict = self._load_cache()
        self._presets: List[Dict] = self._load_presets()

    # ----------------------------------------------------------------- diag --

    def _diag_env(self) -> None:
        log.info("device ids resolved: %s", self._device_ids())

    # ---------------------------------------------------------------- cache --

    def _default_cache(self) -> Dict:
        return {
            "enabled": True,
            "sync": True,
            "left": {"brightness": 255, "r": 255, "g": 255, "b": 255},
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

    def _device_ids(self) -> dict:
        cached = getattr(self, "_device_ids_cache", None)
        if cached is not None:
            return cached
        ids = {}
        PLAT = "/usr/lib/autostart/quirks/platforms"

        def _read_compat_candidates():
            try:
                with open("/sys/firmware/devicetree/base/compatible", "rb") as f:
                    toks = [t for t in f.read().split(b"\x00") if t]
                out = []
                for t in toks:
                    s = t.decode("utf-8", "ignore").strip()
                    if not s:
                        continue
                    out.append(s.split(",")[-1].strip().upper())
                return out
            except Exception:
                return []

        hw = (os.environ.get("HW_DEVICE") or "").strip()
        if not hw:
            try:
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if line.startswith("HW_DEVICE="):
                            hw = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            except Exception as e:
                log.warning("device id: os-release read failed: %s", e)
        if not hw:
            cands = _read_compat_candidates()
            for c in cands:
                if os.path.isfile(f"{PLAT}/{c}/bin/analog_sticks_ledcontrol"):
                    hw = c
                    break
            if not hw:
                for c in cands:
                    if os.path.isdir(f"{PLAT}/{c}"):
                        hw = c
                        break
            log.debug("device id: compat candidates=%s chosen=%r", cands, hw)

        quirk = (os.environ.get("QUIRK_DEVICE") or "").strip()
        if not quirk:
            try:
                with open("/sys/firmware/devicetree/base/model", "rb") as f:
                    quirk = f.read().split(b"\x00", 1)[0].decode("utf-8", "ignore").strip().replace("/", "-")
            except Exception:
                quirk = ""
        if not quirk:
            quirk = hw or "unknown"

        if hw:
            ids["HW_DEVICE"] = hw
        ids["QUIRK_DEVICE"] = quirk
        self._device_ids_cache = ids
        return ids

    def _clean_env(self) -> dict:
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ""
        try:
            env.update(self._device_ids())
        except Exception as e:
            log.warning("device id injection failed: %s", e)
        return env

    # --------------------------------------------------------- persist config
    def _persist_rocknix_cfg(
        self,
        enabled: bool,
        brightness: int = 0,
        rr: int = 0,
        rg: int = 0,
        rb: int = 0,
        lr: int = 0,
        lg: int = 0,
        lb: int = 0,
    ) -> None:
        try:
            if enabled:
                led_args = f"{brightness} {rr} {rg} {rb} {lr} {lg} {lb}"
                cmd = (
                    f". /etc/profile.d/001-functions; "
                    f'set_setting analogsticks.led "{led_args}"; '
                    f'set_setting led.color "rgb"'
                )
            else:
                cmd = f'. /etc/profile.d/001-functions; set_setting led.color "off"'
            subprocess.run(
                ["sh", "-c", cmd], check=False, capture_output=True, stdin=subprocess.DEVNULL, timeout=5, env=self._clean_env()
            )
        except Exception as e:
            log.warning("_persist_rocknix_cfg failed: %s", e)

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
                        existing_nums.append(int(n[len("Custom ") :]))
                    except ValueError:
                        pass
            next_num = (max(existing_nums) + 1) if existing_nums else 1
            clean_name = f"Custom {next_num}"

        # Generate an id unique within this presets list, even on rapid
        # successive saves within the same millisecond.
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
            "left": dict(
                self._cache.get(
                    "left", {"brightness": 255, "r": 255, "g": 255, "b": 255}
                )
            ),
            "right": dict(
                self._cache.get(
                    "right", {"brightness": 255, "r": 255, "g": 255, "b": 255}
                )
            ),
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

    # ----------------------------------------------------- helper utilities --

    def _call_helper(
        self, brightness: int, lr: int, lg: int, lb: int, rr: int, rg: int, rb: int
    ) -> bool:
        """Invoke the ROCKNIX helper.

        Signature (from ROCKNIX SM8250 analog_sticks_ledcontrol):
            <brightness> <Rr> <Rg> <Rb> <Lr> <Lg> <Lb>
        Note the order: RIGHT side first, then LEFT. Brightness is shared.
        """
        if not os.path.exists(self.HELPER):
            log.warning("helper not found at %s", self.HELPER)
            return False
        argv = [
            self.HELPER,
            str(brightness),
            str(rr),
            str(rg),
            str(rb),
            str(lr),
            str(lg),
            str(lb),
        ]
        log.info("invoking helper: %s", " ".join(argv))
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
                env=self._clean_env(),
            )
            log.info(
                "helper rc=%d stdout=%r stderr=%r",
                result.returncode,
                result.stdout,
                result.stderr,
            )
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
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=self._clean_env(),
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
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._clean_env(),
            )
            return True
        except Exception:
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

    def set_sides(
        self, enabled: bool, left: Dict, right: Dict, sync: bool = False
    ) -> Dict:
        """Set independent left/right color and brightness."""
        log.info(
            "set_sides enabled=%s sync=%s left=%s right=%s", enabled, sync, left, right
        )
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
        if not enabled:
            log.info("apply OFF path")
            if self._call_helper(0, 0, 0, 0, 0, 0, 0):
                method = "helper"
            elif self._call_ledcontrol_off():
                method = "ledcontrol"
            else:
                log.error("no LED off path available")
                return {"ok": False, "error": "No LED control method available"}

            self._cache["enabled"] = False
            self._cache["sync"] = sync
            self._save_cache()
            self._persist_rocknix_cfg(enabled=False)
            log.info("OFF applied via %s", method)
            return self._state_payload(method=method, override_enabled=False)

        # ON path — HELPER is the only supported path.
        if not self._call_helper(
            left["brightness"],
            left["r"],
            left["g"],
            left["b"],
            right["r"],
            right["g"],
            right["b"],
        ):
            log.error("HELPER failed or not available")
            return {"ok": False, "error": "HELPER not available"}

        log.info("ON applied via helper")
        # Cache only meaningful (non-zero) state
        self._cache["enabled"] = True
        self._cache["sync"] = sync
        if left["brightness"] > 0:
            self._cache["left"] = left
        if right["brightness"] > 0:
            self._cache["right"] = right
        self._save_cache()
        self._persist_rocknix_cfg(
            enabled=True,
            brightness=left["brightness"],
            rr=right["r"],
            rg=right["g"],
            rb=right["b"],
            lr=left["r"],
            lg=left["g"],
            lb=left["b"],
        )

        return self._state_payload(method="helper", override_enabled=True)

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
        return self._apply(
            enabled=enabled, left=left, right=right, sync=state.get("sync", True)
        )

    def set_brightness(self, brightness: int) -> Dict:
        """Legacy: set brightness on both sides."""
        state = self._cache
        b = _clamp(brightness)
        left = {**state.get("left", {"r": 255, "g": 255, "b": 255}), "brightness": b}
        right = {**state.get("right", {"r": 255, "g": 255, "b": 255}), "brightness": b}
        return self._apply(
            enabled=b > 0, left=left, right=right, sync=state.get("sync", True)
        )

    def set_color(self, r: int, g: int, b: int) -> Dict:
        """Legacy: set color on both sides."""
        state = self._cache
        r, g, bl = _clamp(r), _clamp(g), _clamp(b)
        left = {**state.get("left", {"brightness": 255}), "r": r, "g": g, "b": bl}
        right = {**state.get("right", {"brightness": 255}), "r": r, "g": g, "b": bl}
        # Ensure brightness > 0 so the color is visible
        if left.get("brightness", 0) == 0:
            left["brightness"] = 255
        if right.get("brightness", 0) == 0:
            right["brightness"] = 255
        return self._apply(
            enabled=True, left=left, right=right, sync=state.get("sync", True)
        )

    # ------------------------------------------------------------- state --

    def _state_payload(
        self, method: Optional[str] = None, override_enabled: Optional[bool] = None
    ) -> Dict:
        c = self._cache
        return {
            "ok": True,
            "method": method,
            "enabled": override_enabled
            if override_enabled is not None
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

        payload = self._state_payload(override_enabled=self._cache.get("enabled", True))
        payload["has_helper"] = os.path.exists(self.HELPER)
        payload["has_ledcontrol"] = os.path.exists(self.LEDCONTROL)
        return payload

    def get_capabilities(self) -> Dict:
        # HELPER accepts independent colors per side but a single shared brightness.
        has_helper = os.path.exists(self.HELPER)
        return {
            "ok": True,
            "supports_enabled": True,
            "supports_brightness": True,
            "supports_color": True,
            "supports_split": has_helper,
            "has_helper": has_helper,
            "has_ledcontrol": os.path.exists(self.LEDCONTROL),
        }
