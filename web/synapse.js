/* THE SYNAPSE — narrative themes as glowing neurons with dendrite branches,
   axons into the cortex, and blob agents drifting between them. Pure canvas. */
(function () {
  const PALETTE = ["#22e6ff", "#39ff88", "#ff3df2", "#ff8a3d", "#a26bff", "#ffd23d", "#ff4d6d", "#4dd0ff", "#b6ff3d", "#ff9de6", "#7d8ba0"];
  const AGENT_COLORS = { SCOUT: "#22e6ff", HOLDERS: "#39ff88", KOLS: "#ff3df2", FEES: "#ff8a3d", CATALYST: "#a26bff", THESIS: "#f2f7ff" };
  const AGENT_SHAPE = { SCOUT: "drop", HOLDERS: "round", KOLS: "tri", FEES: "drop", CATALYST: "tri", THESIS: "round" };

  function rng(seed) { let s = 0; for (const ch of String(seed)) s = (s * 31 + ch.charCodeAt(0)) >>> 0;
    return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }

  const S = { canvas: null, ctx: null, w: 0, h: 0, dpr: 1, nodes: [], agents: [], pulses: [], dendrites: null,
    themes: [], agentState: {}, hover: null, t0: performance.now(), sources: [], leader: null, hourlyLeader: null };

  function init(canvas, tip) {
    S.canvas = canvas; S.ctx = canvas.getContext("2d"); S.tip = tip;
    const ro = new ResizeObserver(resize); ro.observe(canvas); resize();
    for (const name of Object.keys(AGENT_COLORS)) S.agents.push(makeAgent(name));
    canvas.addEventListener("mousemove", onMove); canvas.addEventListener("mouseleave", () => { S.hover = null; tip.style.display = "none"; });
    requestAnimationFrame(frame);
  }

  function resize() {
    const r = S.canvas.getBoundingClientRect(); S.dpr = Math.min(2, window.devicePixelRatio || 1);
    S.w = r.width; S.h = r.height; S.canvas.width = r.width * S.dpr; S.canvas.height = r.height * S.dpr;
    layout();
  }

  function center() { return { x: S.w / 2, y: S.h * 0.54 }; }

  function layout() {
    if (!S.themes.length || !S.w) return;
    const narrow = S.w < 900; const c = center();
    const rx = narrow ? S.w * 0.38 : Math.min(S.w * 0.24, 340), ry = narrow ? S.h * 0.36 : S.h * 0.36;
    const maxR = Math.max(1, ...S.themes.map(t => t.runners));
    S.nodes = S.themes.map((t, i) => {
      const ang = -Math.PI / 2 + (i / S.themes.length) * Math.PI * 2;
      const old = S.nodes.find(n => n.theme === t.theme);
      const size = 7 + Math.sqrt(t.runners / maxR) * 26;
      return { ...t, x: c.x + Math.cos(ang) * rx, y: c.y + Math.sin(ang) * ry, ang, r: size,
        color: PALETTE[i % PALETTE.length], phase: old ? old.phase : Math.random() * 6 };
    });
    buildDendrites();
  }

  // Pre-render dendrite trees into an offscreen canvas (cheap per frame).
  function buildDendrites() {
    const off = document.createElement("canvas"); off.width = S.canvas.width; off.height = S.canvas.height;
    const g = off.getContext("2d"); g.scale(S.dpr, S.dpr); g.lineCap = "round";
    const c = center();
    for (const n of S.nodes) {
      const R = rng(n.theme); const branches = 3 + Math.round(Math.min(7, n.runners / 3));
      const glow = Math.min(1, 0.25 + n.momentum / 4);
      for (let b = 0; b < branches; b++) {
        const outward = n.ang + (R() - 0.5) * 2.4;
        grow(g, n.x, n.y, outward, 34 + R() * 46 + n.r, 3.2, 0, R, n.color, glow);
      }
      // axon to cortex: wavy bezier
      g.strokeStyle = hexA(n.color, 0.22 + glow * 0.25); g.lineWidth = 1.2 + Math.min(3, n.runners / 8);
      g.beginPath(); g.moveTo(n.x, n.y);
      const mx = (n.x + c.x) / 2 + (R() - 0.5) * 80, my = (n.y + c.y) / 2 + (R() - 0.5) * 80;
      g.quadraticCurveTo(mx, my, c.x, c.y); g.stroke();
      n.axon = { mx, my };
    }
    // neighbour synapses
    for (let i = 0; i < S.nodes.length; i++) {
      const a = S.nodes[i], b = S.nodes[(i + 1) % S.nodes.length];
      g.strokeStyle = "rgba(120,160,200,0.07)"; g.lineWidth = 1; g.beginPath(); g.moveTo(a.x, a.y);
      g.quadraticCurveTo((a.x + b.x) / 2 * 0.9 + c.x * 0.1, (a.y + b.y) / 2 * 0.9 + c.y * 0.1, b.x, b.y); g.stroke();
    }
    S.dendrites = off;
  }

  function grow(g, x, y, ang, len, w, depth, R, color, glow) {
    if (depth > 5 || len < 5) return;
    const segs = 4; let cx = x, cy = y, a = ang;
    g.strokeStyle = hexA(color, (0.55 - depth * 0.08) * (0.5 + glow * 0.5)); g.lineWidth = Math.max(0.4, w);
    g.shadowColor = color; g.shadowBlur = depth < 2 ? 6 * glow : 0;
    g.beginPath(); g.moveTo(cx, cy);
    for (let i = 0; i < segs; i++) { a += (R() - 0.5) * 0.5; cx += Math.cos(a) * len / segs; cy += Math.sin(a) * len / segs; g.lineTo(cx, cy); }
    g.stroke(); g.shadowBlur = 0;
    const kids = depth < 2 ? 2 + (R() < 0.5 ? 1 : 0) : 2;
    for (let k = 0; k < kids; k++) grow(g, cx, cy, a + (R() - 0.5) * 1.5, len * (0.6 + R() * 0.2), w * 0.62, depth + 1, R, color, glow);
  }

  function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a.toFixed(3)})`; }

  function makeAgent(name) {
    const c = center();
    return { name, color: AGENT_COLORS[name], shape: AGENT_SHAPE[name], x: c.x + (Math.random() - .5) * 200,
      y: c.y + (Math.random() - .5) * 200, tx: c.x, ty: c.y, size: name === "THESIS" ? 46 : 18, seed: Math.random() * 10, retarget: 0 };
  }

  function update(themes, agents, hourlyLeader) {
    const sig = JSON.stringify(themes.map(t => [t.theme, t.runners, t.momentum]));
    S.themes = themes.filter(t => t.theme !== "Unthemed" || t.runners > 0);
    S.hourlyLeader = hourlyLeader;
    S.leader = (themes.find(t => t.theme !== "Unthemed" && t.runners > 0) || {}).theme;
    for (const a of agents || []) S.agentState[a.name] = a;
    if (sig !== S._sig) { S._sig = sig; layout(); }
  }

  function onMove(e) {
    const r = S.canvas.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top;
    const n = S.nodes.find(n => (n.x - x) ** 2 + (n.y - y) ** 2 < (n.r + 10) ** 2);
    S.hover = n || null;
    if (!n) { S.tip.style.display = "none"; return; }
    S.tip.style.display = "block"; S.tip.style.left = Math.min(x + 16, S.w - 190) + "px"; S.tip.style.top = (y + 10) + "px";
    S.tip.innerHTML = `<b style="color:${n.color}">${n.theme}</b><div class="kv" style="margin-top:4px">
      <span>runners 24h</span><b>${n.runners}</b><span>launches</span><b>${n.launches}</b>
      <span>hit rate</span><b>${n.hit_rate}%</b><span>momentum</span><b>${n.momentum}x</b>
      <span>share</span><b>${n.share}%</b><span>real</span><b>${n.real_pct == null ? "—" : n.real_pct + "%"}</b>
      <span>last hour</span><b>${n.h_runners} runners</b></div>
      ${n.top && n.top.length ? `<div style="margin-top:4px;color:#5d7183">top: ${n.top.map(t => "$" + t.symbol).join(" ")}</div>` : ""}`;
  }

  function frame(now) {
    const g = S.ctx, t = (now - S.t0) / 1000; const c = center();
    g.setTransform(S.dpr, 0, 0, S.dpr, 0, 0); g.clearRect(0, 0, S.w, S.h);
    // grid
    g.strokeStyle = "rgba(60,90,120,0.07)"; g.lineWidth = 1;
    for (let x = (t * 6) % 40; x < S.w; x += 40) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, S.h); g.stroke(); }
    for (let y = 0; y < S.h; y += 40) { g.beginPath(); g.moveTo(0, y); g.lineTo(S.w, y); g.stroke(); }
    drawFibers(g, t);
    if (S.dendrites) g.drawImage(S.dendrites, 0, 0, S.w, S.h);

    // pulses along axons
    for (const n of S.nodes) {
      const rate = 0.15 + Math.min(3, n.momentum) * 0.35 + n.runners / 30;
      if (n.runners && Math.random() < rate / 60) S.pulses.push({ n, p: 0, sp: 0.25 + Math.random() * 0.3 });
    }
    S.pulses = S.pulses.filter(p => p.p <= 1);
    for (const p of S.pulses) {
      p.p += p.sp / 60; const n = p.n, u = 1 - p.p;
      const x = u * u * n.x + 2 * u * p.p * n.axon.mx + p.p * p.p * c.x, y = u * u * n.y + 2 * u * p.p * n.axon.my + p.p * p.p * c.y;
      g.fillStyle = n.color; g.shadowColor = n.color; g.shadowBlur = 12; g.beginPath(); g.arc(x, y, 2.4, 0, 7); g.fill(); g.shadowBlur = 0;
    }

    // nodes
    for (const n of S.nodes) {
      const glow = Math.min(1, 0.15 + n.momentum / 3); const breathe = 1 + Math.sin(t * (1 + glow * 2) + n.phase) * 0.06 * (0.5 + glow);
      const r = n.r * breathe;
      const grd = g.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * (2.4 + glow * 2));
      grd.addColorStop(0, hexA(n.color, 0.45 * glow + 0.1)); grd.addColorStop(1, hexA(n.color, 0));
      g.fillStyle = grd; g.beginPath(); g.arc(n.x, n.y, r * (2.4 + glow * 2), 0, 7); g.fill();
      g.fillStyle = hexA(n.color, n.runners ? 0.95 : 0.35); g.shadowColor = n.color; g.shadowBlur = 10 + glow * 30;
      g.beginPath(); g.arc(n.x, n.y, r, 0, 7); g.fill(); g.shadowBlur = 0;
      g.fillStyle = "rgba(255,255,255,0.75)"; g.beginPath(); g.arc(n.x - r * 0.3, n.y - r * 0.3, r * 0.28, 0, 7); g.fill();
      if (n.theme === S.leader || n.theme === S.hourlyLeader || S.hover === n) {
        g.strokeStyle = n.theme === S.hourlyLeader && n.theme !== S.leader ? "#ff3df2" : "#f2f7ff"; g.lineWidth = 1.2;
        g.setLineDash([4, 4]); g.lineDashOffset = -t * 20; g.beginPath(); g.arc(n.x, n.y, r + 8, 0, 7); g.stroke(); g.setLineDash([]);
      }
      // label pill
      const label = `${n.theme.toUpperCase()}  ${n.runners}`; g.font = "700 10px JetBrains Mono, monospace";
      const lw = g.measureText(label).width + 14, lx = n.x - lw / 2, ly = n.y + r + 10;
      g.fillStyle = "rgba(4,7,11,0.85)"; g.strokeStyle = hexA(n.color, 0.7); g.lineWidth = 1;
      roundRect(g, lx, ly, lw, 17, 8); g.fill(); g.stroke();
      g.fillStyle = "#e8f1fa"; g.fillText(label, lx + 7, ly + 12);
      if (n.momentum >= 1.5) { g.fillStyle = n.color; g.font = "700 9px JetBrains Mono, monospace";
        g.fillText(`▲ ${n.momentum}x`, n.x - 16, n.y - r - 8); }
    }
    // cortex core glow
    const cg = g.createRadialGradient(c.x, c.y, 0, c.x, c.y, 110);
    cg.addColorStop(0, "rgba(242,247,255,0.16)"); cg.addColorStop(1, "rgba(242,247,255,0)"); g.fillStyle = cg; g.beginPath(); g.arc(c.x, c.y, 110, 0, 7); g.fill();

    for (const a of S.agents) moveAgent(a, t);
    for (const a of S.agents) drawAgent(g, a, t);
    requestAnimationFrame(frame);
  }

  // input fibers from the SENSORY INPUT cards into the nearest neurons
  function drawFibers(g, t) {
    const cards = document.querySelectorAll("#sources .src"); if (!cards.length || !S.nodes.length || S.w < 900) return;
    const cr = S.canvas.getBoundingClientRect();
    cards.forEach((el, i) => {
      const r = el.getBoundingClientRect(); const x0 = r.right - cr.left, y0 = r.top + r.height / 2 - cr.top;
      const n = S.nodes[i % S.nodes.length]; const ok = el.classList.contains("ok");
      g.strokeStyle = ok ? hexA(n.color, 0.28) : "rgba(255,77,109,0.12)"; g.lineWidth = 1;
      g.beginPath(); g.moveTo(x0, y0); g.bezierCurveTo(x0 + 90, y0, n.x - 120, n.y, n.x, n.y); g.stroke();
      if (ok) { const p = (t * 0.35 + i * 0.17) % 1, u = 1 - p;
        const x = u ** 3 * x0 + 3 * u * u * p * (x0 + 90) + 3 * u * p * p * (n.x - 120) + p ** 3 * n.x;
        const y = u ** 3 * y0 + 3 * u * u * p * y0 + 3 * u * p * p * n.y + p ** 3 * n.y;
        g.fillStyle = n.color; g.beginPath(); g.arc(x, y, 1.8, 0, 7); g.fill(); }
    });
  }

  function moveAgent(a, t) {
    const st = (S.agentState[a.name] || {}).status; const busy = st === "running";
    if (a.name === "THESIS") { const c = center(); a.tx = c.x; a.ty = c.y + Math.sin(t * 0.8) * 6; }
    else if (t > a.retarget && S.nodes.length) {
      const n = S.nodes[Math.floor(Math.random() * S.nodes.length)]; const c = center(); const k = 0.35 + Math.random() * 0.4;
      a.tx = n.x + (c.x - n.x) * k + (Math.random() - .5) * 60; a.ty = n.y + (c.y - n.y) * k + (Math.random() - .5) * 60;
      a.retarget = t + (busy ? 1.5 : 4) + Math.random() * 4;
    }
    const sp = busy ? 0.05 : 0.015; a.x += (a.tx - a.x) * sp; a.y += (a.ty - a.y) * sp;
  }

  function drawAgent(g, a, t) {
    const st = (S.agentState[a.name] || {}); const busy = st.status === "running", err = st.status === "error";
    const bob = Math.sin(t * 2 + a.seed) * 3, s = a.size * (1 + Math.sin(t * 3 + a.seed) * 0.03);
    const x = a.x, y = a.y + bob; const col = err ? "#ff4d6d" : a.color;
    g.save(); g.translate(x, y);
    g.shadowColor = col; g.shadowBlur = busy ? 36 : 18;
    const body = g.createRadialGradient(-s * 0.3, -s * 0.4, s * 0.1, 0, 0, s * 1.2);
    body.addColorStop(0, "#ffffff"); body.addColorStop(0.35, col); body.addColorStop(1, hexA(col, 0.75));
    g.fillStyle = a.name === "THESIS" ? (() => { const b = g.createRadialGradient(-s * .3, -s * .4, s * .1, 0, 0, s * 1.1);
      b.addColorStop(0, "#ffffff"); b.addColorStop(0.7, "#e6ecf5"); b.addColorStop(1, "#9aa6b8"); return b; })() : body;
    g.beginPath();
    const wob = (k) => 1 + Math.sin(t * 3 + a.seed + k) * 0.05;
    if (a.shape === "round") { g.ellipse(0, 0, s * wob(0), s * 0.95 * wob(1), 0, 0, 7); }
    else if (a.shape === "drop") { g.moveTo(0, -s * 1.35 * wob(0)); g.bezierCurveTo(s * 0.9, -s * 0.3, s * 1.0 * wob(1), s * 0.9, 0, s * 0.9);
      g.bezierCurveTo(-s * 1.0 * wob(2), s * 0.9, -s * 0.9, -s * 0.3, 0, -s * 1.35 * wob(0)); }
    else { g.moveTo(0, -s * 1.2); g.quadraticCurveTo(s * 0.4, -s * 0.6, s * 1.05 * wob(1), s * 0.75);
      g.quadraticCurveTo(0, s * 1.05, -s * 1.05 * wob(2), s * 0.75); g.quadraticCurveTo(-s * 0.4, -s * 0.6, 0, -s * 1.2); }
    g.fill(); g.shadowBlur = 0;
    // eyes: two vertical capsules, blink occasionally
    const blink = (Math.sin(t * 0.9 + a.seed * 7) > 0.985) ? 0.15 : 1;
    g.fillStyle = "#06080c"; const ew = s * 0.13, eh = s * 0.34 * blink, ex = s * 0.24, ey = s * 0.02;
    roundRect(g, -ex - ew / 2, ey - eh / 2, ew, eh, ew / 2); g.fill(); roundRect(g, ex - ew / 2, ey - eh / 2, ew, eh, ew / 2); g.fill();
    g.restore();
    // name pill
    g.font = "700 9px JetBrains Mono, monospace"; const lbl = a.name + (busy ? " ●" : "");
    const lw = g.measureText(lbl).width + 12, lx = x + s + 6, ly = y - 8;
    g.fillStyle = "rgba(4,7,11,0.8)"; g.strokeStyle = hexA(col, 0.8); roundRect(g, lx, ly, lw, 15, 7); g.fill(); g.stroke();
    g.fillStyle = col; g.fillText(lbl, lx + 6, ly + 11);
  }

  function roundRect(g, x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }

  window.Synapse = { init, update, AGENT_COLORS, PALETTE };
})();
