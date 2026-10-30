import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { addEventListener, removeEventListener, callable, definePlugin, routerHook } from "@decky/api";
import { useEffect, useState } from "react";
import { FaMobileAlt } from "react-icons/fa";
import qrcode from "qrcode-generator";
import { runAction, watchRunningApp } from "./actions";
import { CursorOverlay, disposeCursor, onActivity, reportScreen, setViewVisible } from "./CursorOverlay";

interface State {
  running: boolean;
  enabled: boolean;
  url: string;
  alt_url: string | null;
  link_host: "name" | "ip";
  clients: number;
  error: string;
}

const getState = callable<[], State>("get_state");
const setEnabled = callable<[enabled: boolean], State>("set_enabled");
const regenerateToken = callable<[], State>("regenerate_token");
const viewReady = callable<[], void>("view_ready");
const setLinkHost = callable<[mode: string], State>("set_link_host");

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
    // the server may still be starting by itself (it was left enabled): keep looking until it is up
    const poll = setInterval(() => getState().then((n) => setS((p) => (p && p.running === n.running && p.error === n.error ? p : n))), 3000);
    const l = (n: number) => setS((p) => (p ? { ...p, clients: n } : p));
    const h = addEventListener<[n: number]>("rc_clients", l);
    return () => { clearInterval(poll); removeEventListener("rc_clients", h); };
  }, []);

  if (!s) return <PanelSection title="Remote Control">Loading…</PanelSection>;

  return (
    <>
      <PanelSection title="Remote Control">
        <PanelSectionRow>
          <ToggleField
            label="Enable"
            description={
              s.running ? `${s.clients} device(s) connected · starts automatically with the device`
                : s.enabled ? "Starting…" : "Server is off"
            }
            checked={s.enabled}
            onChange={async (v) => {
              if (v) reportScreen();
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
            <div style={{ fontSize: 11, opacity: 0.8 }}>
              <b>Instructions:</b> Scan the code on the same network. A DHCP-reserved or static IP for this device is
              preferred; mDNS is an alternative but may not work or may be slow on your phone.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: 11, wordBreak: "break-all" }}>{s.url}</div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => setS(await regenerateToken())}>
              New link (disconnects devices)
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
      {s.alt_url && (
        <PanelSection title="Settings">
          <PanelSectionRow>
            <ToggleField
              label="Use mDNS"
              description="A host name that survives IP changes. May be slow, or not work, on some devices and networks."
              checked={s.link_host === "name"}
              onChange={async (v) => setS(await setLinkHost(v ? "name" : "ip"))}
            />
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => {
  routerHook.addGlobalComponent("DeckyRemoteControlCursor", CursorOverlay);
  const listener = addEventListener<[down: boolean]>("rc_activity", () => onActivity());
  let tries = 0;
  const screenTimer = setInterval(() => { if (reportScreen() || ++tries > 20) clearInterval(screenTimer); }, 1000);
  const unwatchApp = watchRunningApp();
  const actionListener = addEventListener<[action: string]>("rc_action", runAction);
  const viewListener = addEventListener<[visible: boolean]>("rc_view", (visible) => {
    setViewVisible(visible);
    if (!visible) setTimeout(() => viewReady(), 40); // let the compositor drop the view first
  });

  return {
    name: "Remote Control",
    titleView: <div className={staticClasses.Title}>Remote Control</div>,
    content: <Content />,
    icon: <FaMobileAlt />,
    onDismount() {
      removeEventListener("rc_activity", listener);
      removeEventListener("rc_view", viewListener);
      removeEventListener("rc_action", actionListener);
      unwatchApp();
      clearInterval(screenTimer);
      disposeCursor();
      routerHook.removeGlobalComponent("DeckyRemoteControlCursor");
    },
  };
});
