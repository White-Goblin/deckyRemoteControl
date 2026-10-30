import { findSP } from "@decky/ui";

// Cursor overlay for Steam's gaming-mode UI. Nothing exists in the DOM (and no
// animation frame runs) unless input arrived within the last IDLE_MS.
const IDLE_MS = 1500;
const TRAIL_MS = 380;

interface Pt { x: number; y: number; t: number; w: number }

let canvas: HTMLCanvasElement | null = null;
let win: Window | null = null;
let raf = 0;
let trail: Pt[] = [];
let cur = { x: 0, y: 0, down: false };
let lastInput = 0;

function ensure(): boolean {
  if (canvas && canvas.isConnected) return true;
  try {
    win = findSP();
    const doc = win?.document;
    if (!doc?.body) return false;
    canvas = doc.createElement("canvas");
    canvas.width = win.innerWidth;
    canvas.height = win.innerHeight;
    canvas.style.cssText =
      "position:fixed;left:0;top:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483647;";
    doc.body.appendChild(canvas);
    return true;
  } catch {
    return false;
  }
}

function teardown() {
  if (raf && win) win.cancelAnimationFrame(raf);
  raf = 0;
  canvas?.remove();
  canvas = null;
  trail = [];
}

function frame(now: number) {
  raf = 0;
  if (!canvas || !win) return;
  if (now - lastInput > IDLE_MS) return teardown();
  const ctx = canvas.getContext("2d")!;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  trail = trail.filter((p) => now - p.t < TRAIL_MS);

  ctx.lineCap = "round";
  for (let i = 1; i < trail.length; i++) {
    const a = trail[i - 1], b = trail[i];
    const life = 1 - (now - b.t) / TRAIL_MS;
    ctx.strokeStyle = `rgba(120,185,255,${0.45 * life})`;
    ctx.lineWidth = Math.max(2, b.w * life);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  const fade = Math.min(1, (IDLE_MS - (now - lastInput)) / 400);
  const r = cur.down ? 17 : 22;
  ctx.globalAlpha = fade;
  ctx.beginPath();
  ctx.arc(cur.x, cur.y, r, 0, Math.PI * 2);
  ctx.fillStyle = cur.down ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.32)";
  ctx.fill();
  ctx.lineWidth = 2.5;
  ctx.strokeStyle = "rgba(90,165,255,0.9)";
  ctx.stroke();
  ctx.globalAlpha = 1;
  raf = win.requestAnimationFrame(frame);
}

export function onActivity(x: number, y: number, speed: number, down: boolean) {
  if (!ensure() || !canvas || !win) return;
  const now = performance.now();
  lastInput = now;
  cur = { x, y, down };
  trail.push({ x, y, t: now, w: Math.min(18, 4 + speed * 0.6) });
  if (trail.length > 60) trail.shift();
  if (!raf) raf = win.requestAnimationFrame(frame);
}

export function disposeOverlay() {
  teardown();
}

export function screenSize(): [number, number] {
  try {
    const w = findSP();
    return [w.innerWidth, w.innerHeight];
  } catch {
    return [1280, 800];
  }
}
