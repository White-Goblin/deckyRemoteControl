import { Router } from "@decky/ui";
import { callable } from "@decky/api";
import { useEffect, useState } from "react";
import { cursorDataUrl } from "./cursorPage";
import { UIComposition, useUIComposition } from "./useUIComposition";

// Cursor drawn in its own Steam browser view so it sits above menus, Quick Access and games
// (same technique as a picture-in-picture overlay). The view exists only while input is active.
const IDLE_MS = 1700;

const getCursorUrl = callable<[], { cursor_ws: string }>("get_state");

const mainWindow = (): any => (Router as any).WindowStore?.GamepadUIMainWindowInstance;

export const screenInfo = () => {
  const win: Window | undefined = mainWindow()?.BrowserWindow;
  return {
    width: win?.innerWidth || 1280,
    height: win?.innerHeight || 720,
    scale: win?.devicePixelRatio || 1,
  };
};

type Listener = (url: string) => void;
let listener: Listener | null = null;
let hideTimer: ReturnType<typeof setTimeout> | undefined;
let showing = false;
let browser: any = null;
let viewHidden = false;

// Steam's browser view would swallow clicks meant for the UI beneath it, so the backend asks
// us to hide it while a mouse button is down.
export function setViewVisible(visible: boolean) {
  viewHidden = !visible;
  browser?.SetVisible(visible);
}

export function onActivity() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { showing = false; listener?.(""); }, IDLE_MS);
  if (showing) return;
  showing = true;
  getCursorUrl().then((s) => { if (showing) listener?.(cursorDataUrl(s.cursor_ws)); });
}

export function disposeCursor() {
  viewHidden = false;
  clearTimeout(hideTimer);
  showing = false;
  listener?.("");
}

const CursorView = ({ url }: { url: string }) => {
  useUIComposition(UIComposition.Notification);
  const [view] = useState<any>(() => {
    try { return mainWindow().CreateBrowserView("decky-remote-control-cursor"); }
    catch (e) { console.error("Remote Control: CreateBrowserView failed", e); return null; }
  });
  useEffect(() => (view ? () => { browser = null; view.Destroy(); } : undefined), [view]);
  useEffect(() => {
    if (!view) return;
    const { width, height } = screenInfo();
    view.LoadURL(url);
    const b = view.GetBrowser();
    b.SetBounds(0, 0, width, height);
    browser = b;
    b.SetVisible(!viewHidden);
  }, [view, url]);
  return null;
};

// Side menus are raised above views that already exist when they open, so while the cursor is
// showing, recreate its view whenever the open menu changes (a new view is created on top).
const POLL_MS = 150;
const RECREATE_AT_MS = [0, 400, 1000]; // the menu is raised partway through its open animation
const openMenu = () => mainWindow()?.MenuStore?.GetOpenSideMenu?.();

export const CursorOverlay = () => {
  const [url, setUrl] = useState("");
  const [generation, setGeneration] = useState(0);
  useEffect(() => { listener = setUrl; return () => { listener = null; }; }, []);
  useEffect(() => {
    if (!url) return;
    let last = openMenu();
    const timers: ReturnType<typeof setTimeout>[] = [];
    const poll = setInterval(() => {
      const now = openMenu();
      if (now === last) return;
      last = now;
      RECREATE_AT_MS.forEach((ms) => timers.push(setTimeout(() => setGeneration((g) => g + 1), ms)));
    }, POLL_MS);
    return () => { clearInterval(poll); timers.forEach(clearTimeout); };
  }, [url]);
  return url ? <CursorView key={generation} url={url} /> : null;
};
