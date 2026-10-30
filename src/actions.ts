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

// Like the physical button: open the menu, or close it when it is already the one showing
function toggleMenu(menu: number) {
  if (mainWindow()?.MenuStore?.GetOpenSideMenu?.() === menu) Navigation.CloseSideMenus();
  else if (menu === MENU_MAIN) Navigation.OpenMainMenu();
  else Navigation.OpenQuickAccessMenu();
}

// Shortcuts (non-Steam apps) are terminated by their 64-bit game id, Steam games by app id
function quitRunningApp() {
  const running = [...(SteamUIStore.m_runningAppIDs ?? [])];
  if (!running.length) {
    report("No app is running");
    return;
  }
  const appid = String(running[0]);
  const overview = appStore.GetAppOverviewByAppID(Number(appid));
  SteamClient.Apps.TerminateApp(overview?.app_type === APP_TYPE_SHORTCUT ? String(overview.m_gameid) : appid, false);
  report(`Closing ${overview?.display_name ?? "app"}`);
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
