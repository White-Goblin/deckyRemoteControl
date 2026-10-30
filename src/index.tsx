import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { addEventListener, removeEventListener, callable, definePlugin } from "@decky/api";
import { useEffect, useState } from "react";
import { FaMobileAlt } from "react-icons/fa";
import qrcode from "qrcode-generator";
import { disposeOverlay, onActivity, screenSize } from "./overlay";

interface State {
  running: boolean;
  url: string;
  clients: number;
  sensitivity: number;
  error: string;
}

const getState = callable<[], State>("get_state");
const setEnabled = callable<[enabled: boolean], State>("set_enabled");
const regenerateToken = callable<[], State>("regenerate_token");
const setSensitivity = callable<[value: number], void>("set_sensitivity");
const setScreen = callable<[width: number, height: number], void>("set_screen");

function Qr({ text }: { text: string }) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const svg = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  return (
    <div
      style={{ background: "#fff", padding: 8, borderRadius: 8, width: "100%", aspectRatio: "1" }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function Content() {
  const [s, setS] = useState<State | null>(null);

  useEffect(() => {
    getState().then(setS);
    const l = (n: number) => setS((p) => (p ? { ...p, clients: n } : p));
    const h = addEventListener<[n: number]>("rc_clients", l);
    return () => removeEventListener("rc_clients", h);
  }, []);

  if (!s) return <PanelSection title="Remote Control">Loading…</PanelSection>;

  return (
    <>
      <PanelSection title="Remote Control">
        <PanelSectionRow>
          <ToggleField
            label="Enable"
            description={s.running ? `${s.clients} device(s) connected` : "Server is off"}
            checked={s.running}
            onChange={async (v) => {
              if (v) { const [w, h] = screenSize(); await setScreen(w, h); }
              setS(await setEnabled(v));
            }}
          />
        </PanelSectionRow>
        {s.error && <PanelSectionRow><div style={{ color: "#f66" }}>{s.error}</div></PanelSectionRow>}
      </PanelSection>
      {s.running && (
        <PanelSection title="Connect">
          <PanelSectionRow>
            <Qr text={s.url} />
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: 11, wordBreak: "break-all", opacity: 0.8 }}>
              Scan with your phone camera (same Wi-Fi), or open:<br />{s.url}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => setS(await regenerateToken())}>
              New link (disconnects devices)
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
      <PanelSection title="Settings">
        <PanelSectionRow>
          <SliderField
            label="Pointer speed"
            min={0.5} max={3} step={0.1} showValue
            value={s.sensitivity}
            onChange={(v) => { setS({ ...s, sensitivity: v }); setSensitivity(v); }}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  const listener = addEventListener<[x: number, y: number, speed: number, down: boolean]>(
    "rc_activity", (x, y, speed, down) => onActivity(x, y, speed, down));

  return {
    name: "Remote Control",
    titleView: <div className={staticClasses.Title}>Remote Control</div>,
    content: <Content />,
    icon: <FaMobileAlt />,
    onDismount() {
      removeEventListener("rc_activity", listener);
      disposeOverlay();
    },
  };
});
