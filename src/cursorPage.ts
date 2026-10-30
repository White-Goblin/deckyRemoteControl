// The cursor page is inlined (data: URL) so the overlay never depends on an HTTP load that could
// show a browser error page; it only needs the WebSocket, which just stays closed if the server is off.
const PAGE = (wsUrl: string) => `<!doctype html><html><head><meta charset="utf-8"><title>cursor</title>
<style>html,body{margin:0;background:transparent;overflow:hidden}canvas{position:fixed;inset:0;width:100vw;height:100vh}</style>
</head><body><canvas id="c"></canvas><script>const WS_URL=${JSON.stringify(wsUrl)};
(() => {
  const IDLE = 1500, TRAIL = 380;
  const cv = document.getElementById('c'), ctx = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  function size() { cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
  size(); addEventListener('resize', size);
  let trail = [], cur = null, last = 0, raf = 0;
  function frame(now) {
    raf = 0;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    if (!cur || now - last > IDLE) return;
    trail = trail.filter((p) => now - p.t < TRAIL);
    ctx.lineCap = 'round';
    for (let i = 1; i < trail.length; i++) {
      const a = trail[i - 1], b = trail[i], life = 1 - (now - b.t) / TRAIL;
      ctx.strokeStyle = "rgba(120,185,255," + 0.45 * life + ")";
      ctx.lineWidth = Math.max(2, b.w * life);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.globalAlpha = Math.min(1, (IDLE - (now - last)) / 400);
    ctx.beginPath(); ctx.arc(cur.x, cur.y, cur.d ? 17 : 22, 0, Math.PI * 2);
    ctx.fillStyle = cur.d ? 'rgba(255,255,255,.55)' : 'rgba(255,255,255,.32)'; ctx.fill();
    ctx.lineWidth = 2.5; ctx.strokeStyle = 'rgba(90,165,255,.9)'; ctx.stroke();
    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(frame);
  }
  function connect() {
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data), now = performance.now();
      cur = m; last = now;
      trail.push({ x: m.x, y: m.y, t: now, w: Math.min(18, 4 + m.s * 0.6) });
      if (trail.length > 60) trail.shift();
      if (!raf) raf = requestAnimationFrame(frame);
    };
    ws.onclose = () => setTimeout(connect, 1000);
  }
  connect();
})();
</script></body></html>`;

export const cursorDataUrl = (wsUrl: string) => "data:text/html;charset=utf-8," + encodeURIComponent(PAGE(wsUrl));
