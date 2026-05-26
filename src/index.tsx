import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  Field,
  ConfirmModal,
  showModal,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { FaLightbulb } from "react-icons/fa";

// ----- Types -----------------------------------------------------------------

type Side = { brightness: number; r: number; g: number; b: number };

type LedState = {
  ok: boolean;
  method?: string;
  enabled: boolean;
  sync: boolean;
  left: Side;
  right: Side;
  has_helper?: boolean;
  has_ledcontrol?: boolean;
  sysfs_detected?: boolean;
};

type Capabilities = {
  ok: boolean;
  supports_split: boolean;
  has_helper: boolean;
  has_ledcontrol: boolean;
  sysfs_detected: boolean;
};

type CustomPreset = {
  id: string;
  name: string;
  sync: boolean;
  left: Side;
  right: Side;
};

// ----- Python bridge ---------------------------------------------------------

const getState = callable<[], LedState>("get_state");
const getCapabilities = callable<[], Capabilities>("get_capabilities");
const setSides = callable<
  [enabled: boolean, left: Side, right: Side, sync: boolean],
  LedState
>("set_sides");

const listPresets = callable<[], { ok: boolean; presets: CustomPreset[] }>(
  "list_presets",
);
const savePreset = callable<
  [name: string],
  { ok: boolean; preset?: CustomPreset; presets: CustomPreset[] }
>("save_preset");
const deletePreset = callable<
  [preset_id: string],
  { ok: boolean; presets: CustomPreset[]; error?: string }
>("delete_preset");
const applyPresetCall = callable<[preset_id: string], LedState>("apply_preset");

// ----- Built-in presets ------------------------------------------------------

type BuiltinPreset = { name: string; r: number; g: number; b: number };
const BUILTIN_PRESETS: BuiltinPreset[] = [
  { name: "White", r: 255, g: 255, b: 255 },
  { name: "Red", r: 255, g: 0, b: 0 },
  { name: "Orange", r: 255, g: 96, b: 0 },
  { name: "Yellow", r: 255, g: 200, b: 0 },
  { name: "Green", r: 0, g: 255, b: 0 },
  { name: "Cyan", r: 0, g: 200, b: 255 },
  { name: "Blue", r: 0, g: 0, b: 255 },
  { name: "Purple", r: 160, g: 0, b: 255 },
  { name: "Pink", r: 255, g: 20, b: 147 },
];

// ----- Helpers ---------------------------------------------------------------

const previewBar = (s: Side, brightness = 255): React.CSSProperties => {
  const factor = brightness / 255;
  const r = Math.round(s.r * factor);
  const g = Math.round(s.g * factor);
  const b = Math.round(s.b * factor);
  return {
    background: `rgb(${r}, ${g}, ${b})`,
    width: "100%",
    height: "12px",
    borderRadius: "4px",
    border: "1px solid rgba(255,255,255,0.15)",
  };
};

// Small square swatch shown inside a ButtonItem label.
const Swatch = ({ r, g, b }: { r: number; g: number; b: number }) => (
  <div
    style={{
      display: "inline-block",
      width: "14px",
      height: "14px",
      borderRadius: "3px",
      background: `rgb(${r}, ${g}, ${b})`,
      border: "1px solid rgba(255,255,255,0.25)",
      marginRight: "8px",
      verticalAlign: "middle",
    }}
  />
);

// Split swatch for non-synced custom presets — half left color, half right.
const SplitSwatch = ({ left, right }: { left: Side; right: Side }) => (
  <div
    style={{
      display: "inline-block",
      width: "14px",
      height: "14px",
      borderRadius: "3px",
      background: `linear-gradient(90deg,
        rgb(${left.r}, ${left.g}, ${left.b}) 50%,
        rgb(${right.r}, ${right.g}, ${right.b}) 50%)`,
      border: "1px solid rgba(255,255,255,0.25)",
      marginRight: "8px",
      verticalAlign: "middle",
    }}
  />
);

const defaultSide = (): Side => ({ brightness: 255, r: 255, g: 255, b: 255 });

// ----- Content component -----------------------------------------------------

function Content() {
  const [enabled, setEnabled] = useState(true);
  const [sync, setSync] = useState(true);
  const [left, setLeft] = useState<Side>(defaultSide());
  const [right, setRight] = useState<Side>(defaultSide());
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [busy, setBusy] = useState(false);
  const [customs, setCustoms] = useState<CustomPreset[]>([]);

  const applyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [state, capabilities, presetList] = await Promise.all([
        getState(),
        getCapabilities(),
        listPresets(),
      ]);
      if (state?.ok) {
        setEnabled(state.enabled);
        setSync(state.sync);
        if (state.left) {
          setLeft(state.left);
        }
        if (state.right) {
          setRight(state.right);
        }
      }
      if (capabilities?.ok) {
        setCaps(capabilities);
      }
      if (presetList?.ok) {
        setCustoms(presetList.presets);
      }
    } catch (err) {
      console.error("[rp5-led] refresh failed", err);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const apply = useCallback(
    async (
      nextEnabled: boolean,
      nextLeft: Side,
      nextRight: Side,
      nextSync: boolean,
    ) => {
      setBusy(true);
      try {
        const res = await setSides(nextEnabled, nextLeft, nextRight, nextSync);
        if (!res?.ok) {
          toaster.toast({ title: "RP5 LED", body: "Failed to update LEDs" });
        }
      } catch (err) {
        console.error("[rp5-led] apply failed", err);
        toaster.toast({ title: "RP5 LED", body: "Plugin call failed" });
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const queueApply = useCallback(
    (
      nextEnabled: boolean,
      nextLeft: Side,
      nextRight: Side,
      nextSync: boolean,
    ) => {
      if (applyTimer.current) {
        clearTimeout(applyTimer.current);
      }
      applyTimer.current = setTimeout(() => {
        apply(nextEnabled, nextLeft, nextRight, nextSync);
      }, 80);
    },
    [apply],
  );

  // ---- side mutators ----

  const updateLeft = (patch: Partial<Side>) => {
    const nextLeft = { ...left, ...patch };
    if (sync) {
      const nextRight = { ...right, ...patch };
      setLeft(nextLeft);
      setRight(nextRight);
      queueApply(enabled, nextLeft, nextRight, sync);
    } else {
      setLeft(nextLeft);
      queueApply(enabled, nextLeft, right, sync);
    }
  };

  const updateRight = (patch: Partial<Side>) => {
    const nextRight = { ...right, ...patch };
    if (sync) {
      const nextLeft = { ...left, ...patch };
      setLeft(nextLeft);
      setRight(nextRight);
      queueApply(enabled, nextLeft, nextRight, sync);
    } else {
      setRight(nextRight);
      queueApply(enabled, left, nextRight, sync);
    }
  };

  const applyBuiltinPreset = (p: BuiltinPreset) => {
    const next: Side = {
      brightness: left.brightness || 255,
      r: p.r,
      g: p.g,
      b: p.b,
    };
    const nextRight: Side = sync
      ? { brightness: right.brightness || 255, r: p.r, g: p.g, b: p.b }
      : right;
    setLeft(next);
    if (sync) {
      setRight(nextRight);
    }
    setEnabled(true);
    apply(true, next, nextRight, sync);
  };

  // ---- custom preset actions ----

  const onSaveCurrent = async () => {
    setBusy(true);
    try {
      const res = await savePreset(""); // empty → auto-name
      if (res?.ok) {
        setCustoms(res.presets);
        toaster.toast({
          title: "RP5 LED",
          body: `Saved "${res.preset?.name}"`,
        });
      } else {
        toaster.toast({ title: "RP5 LED", body: "Save failed" });
      }
    } catch (err) {
      console.error("[rp5-led] save preset failed", err);
    } finally {
      setBusy(false);
    }
  };

  const onApplyCustom = async (preset: CustomPreset) => {
    setBusy(true);
    try {
      const res = await applyPresetCall(preset.id);
      if (res?.ok) {
        setEnabled(true);
        setSync(preset.sync);
        setLeft(preset.left);
        setRight(preset.right);
      } else {
        toaster.toast({ title: "RP5 LED", body: "Apply failed" });
      }
    } finally {
      setBusy(false);
    }
  };

  const onDeleteCustom = (preset: CustomPreset) => {
    showModal(
      <ConfirmModal
        strTitle={`Delete "${preset.name}"?`}
        strDescription="This preset will be permanently removed."
        strOKButtonText="Delete"
        strCancelButtonText="Cancel"
        onOK={async () => {
          const res = await deletePreset(preset.id);
          if (res?.ok) {
            setCustoms(res.presets);
          } else {
            toaster.toast({ title: "RP5 LED", body: "Delete failed" });
          }
        }}
      />,
    );
  };

  const supportsSplit = caps?.supports_split ?? false;

  // ---- render ----

  return (
    <>
      <PanelSection title="Power">
        <PanelSectionRow>
          <ToggleField
            label="LEDs On"
            checked={enabled}
            disabled={busy}
            onChange={(value: boolean) => {
              setEnabled(value);
              apply(value, left, right, sync);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Sync L/R"
            description={
              supportsSplit
                ? "Mirror both sticks. Turn off for independent control."
                : "Independent L/R requires sysfs access."
            }
            checked={sync}
            disabled={busy || !supportsSplit}
            onChange={(value: boolean) => {
              setSync(value);
              if (value) {
                setRight(left);
                apply(enabled, left, left, true);
              } else {
                apply(enabled, left, right, false);
              }
            }}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Presets">
        {BUILTIN_PRESETS.map((p) => (
          <PanelSectionRow key={p.name}>
            <ButtonItem
              layout="below"
              bottomSeparator="standard"
              disabled={busy}
              onClick={() => applyBuiltinPreset(p)}
            >
              <Swatch r={p.r} g={p.g} b={p.b} />
              {p.name}
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>

      <PanelSection title="Custom Presets">
        {customs.length === 0 && (
          <PanelSectionRow>
            <Field
              label=""
              description="No custom presets yet. Tune the colors below, then tap “Save current”."
              bottomSeparator="standard"
            />
          </PanelSectionRow>
        )}
        {customs.map((p) => (
          <Fragment key={p.id}>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                bottomSeparator="none"
                disabled={busy}
                onClick={() => onApplyCustom(p)}
              >
                <span style={{ display: "inline-flex", alignItems: "center" }}>
                  {p.sync ? (
                    <Swatch r={p.left.r} g={p.left.g} b={p.left.b} />
                  ) : (
                    <SplitSwatch left={p.left} right={p.right} />
                  )}
                  {p.name}
                </span>
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                bottomSeparator="thick"
                disabled={busy}
                onClick={() => onDeleteCustom(p)}
              >
                <span style={{ opacity: 0.7, fontSize: "0.9em" }}>Delete</span>
              </ButtonItem>
            </PanelSectionRow>
          </Fragment>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={onSaveCurrent}>
            Save current as preset
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={sync ? "Both Sticks" : "Left Stick"}>
        <PanelSectionRow>
          <Field label="Preview" bottomSeparator="none">
            <div style={previewBar(left, left.brightness)} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Brightness"
            min={0}
            max={255}
            step={1}
            value={left.brightness}
            onChange={(v: number) => updateLeft({ brightness: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Red"
            min={0}
            max={255}
            step={1}
            value={left.r}
            onChange={(v: number) => updateLeft({ r: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Green"
            min={0}
            max={255}
            step={1}
            value={left.g}
            onChange={(v: number) => updateLeft({ g: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Blue"
            min={0}
            max={255}
            step={1}
            value={left.b}
            onChange={(v: number) => updateLeft({ b: v })}
          />
        </PanelSectionRow>
      </PanelSection>

      {!sync && (
        <PanelSection title="Right Stick">
          <PanelSectionRow>
            <Field label="Preview" bottomSeparator="none">
              <div style={previewBar(right, right.brightness)} />
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Brightness"
              min={0}
              max={255}
              step={1}
              value={right.brightness}
              onChange={(v: number) => updateRight({ brightness: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Red"
              min={0}
              max={255}
              step={1}
              value={right.r}
              onChange={(v: number) => updateRight({ r: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Green"
              min={0}
              max={255}
              step={1}
              value={right.g}
              onChange={(v: number) => updateRight({ g: v })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Blue"
              min={0}
              max={255}
              step={1}
              value={right.b}
              onChange={(v: number) => updateRight({ b: v })}
            />
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => {
  console.log("[rp5-led] Plugin initializing");
  return {
    name: "Rocknix LED Control",
    titleView: <div className={staticClasses.Title}>RP5 LED Control</div>,
    content: <Content />,
    icon: <FaLightbulb />,
    onDismount() {
      console.log("[rp5-led] Plugin unloading");
    },
  };
});
