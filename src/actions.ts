import { Navigation } from "@decky/ui";
import { callable } from "@decky/api";
import { mainWindow } from "./CursorOverlay";

// Steam client globals available in the plugin's SharedJSContext
declare const SteamClient: any;
declare const SteamUIStore: any;
declare const appStore: any;

const MENU_MAIN = 1;
const MENU_QUICK_ACCESS = 2;
const APP_TYPE_SHORTCUT = 1073741824;

const report = callable<[message: string], void>("action_result");
const reportApp = callable<[name: string], void>("app_state");

// Like the physical button: open the menu, or close it when it is already the one showing
function toggleMenu(menu: number) {
  if (mainWindow()?.MenuStore?.GetOpenSideMenu?.() === menu) Navigation.CloseSideMenus();
  else if (menu === MENU_MAIN) Navigation.OpenMainMenu();
  else Navigation.OpenQuickAccessMenu();
}

// The app the Quit button acts on: Steam's first running app
function activeApp(): { appid: string; name: string } | null {
  const running = [...(SteamUIStore.m_runningAppIDs ?? [])];
  if (!running.length) return null;
  const appid = String(running[0]);
  return { appid, name: appStore.GetAppOverviewByAppID(Number(appid))?.display_name || "App" };
}

// Shortcuts (non-Steam apps) are terminated by their 64-bit game id, Steam games by app id
function quitRunningApp() {
  const app = activeApp();
  if (!app) {
    report("No app is running");
    return;
  }
  const overview = appStore.GetAppOverviewByAppID(Number(app.appid));
  SteamClient.Apps.TerminateApp(overview?.app_type === APP_TYPE_SHORTCUT ? String(overview.m_gameid) : app.appid, false);
  report(`Closing ${app.name}`);
}

// Tell the backend (and so the phone's Quit button) which app is running; "" when none
function syncRunningApp() {
  let name = "";
  try {
    name = activeApp()?.name ?? "";
  } catch {
    // Steam's store is not ready yet
  }
  reportApp(name).catch(() => {});
}

export function watchRunningApp(): () => void {
  syncRunningApp();
  // the running list updates just after the notification arrives
  const lifetime = SteamClient.GameSessions.RegisterForAppLifetimeNotifications(() => setTimeout(syncRunningApp, 300));
  return () => lifetime?.unregister?.();
}

export function runAction(action: string) {
  try {
    if (action === "steam") toggleMenu(MENU_MAIN);
    else if (action === "qam") toggleMenu(MENU_QUICK_ACCESS);
    else if (action === "quit") quitRunningApp();
  } catch (e) {
    console.error("Remote Control: action failed", action, e);
    report("That didn't work");
  }
}
