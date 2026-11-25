/* SIGNAL FIELD: the animated backdrop of the SNIPER DESK and DEV LAB terminals.
   Thin monochrome fibers run from SOURCES (coins / narratives, top row) through a lens into SINKS (the operator
   instrument cards), and drop onto a topographic STATE FLOOR whose hotspots are real positions / candidates.
   Nothing here is decorative noise: every pulse is a real bot event (`pulse()`), ambient flow and pulse speed follow
   the bot's real event rate (`setRate()`), and the floor is rebuilt from real data (`setFloor()`). */
(() => {
  const TAU = Math.PI * 2;
  const bez = (p, t) => {  // cubic bezier point
    const u = 1 - t;
    return [u * u * u * p[0] + 3 * u * u * t * p[2] + 3 * u * t * t * p[4] + t * t * t * p[6],
            u * u * u * p[1] + 3 * u * u * t * p[3] + 3 * u * t * t * p[5] + t * t * t * p[7]];
  };
  const rnd = (s) => { let x = Math.sin(s * 9301 + 49297) * 233280; return x - Math.floor(x); };

  class SignalField {
    constructor(canvas, host) {
      this.cv = canvas; this.ctx = canvas.getContext("2d"); this.host = host;
      this.sources = []; this.sinks = []; this.strands = []; this.byPair = new Map();
      this.pulses = []; this.chips = []; this.dots = []; this.floorPts = []; this.floorImg = null;
      this.rate = 6; this.active = false; this.W = 0; this.H = 0; this.dirty = true; this.cardsY = 0;
      const loop = (t) => { if (this.active) this.frame(t); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
      new ResizeObserver(() => { this.dirty = true; }).observe(host);
    }
    // sources: [{id, label, sub, lit}] ; sinkEls: DOM cards inside host (their centres are the targets)
    layout(sources, sinkEls) {
      const sig = JSON.stringify(sources.map(s => s.id)) + sinkEls.length;
      this.sources = sources; this.sinkEls = sinkEls;
      if (sig !== this.sig) { this.sig = sig; this.dirty = true; }
    }
    setRate(perMin) { this.rate = Math.max(0.5, perMin); }
    setFloor(points) { this.floorPts = points; this.floorDirty = true; }
    // a real event: light the strands source -> sink, with an optional chip label
    pulse(sourceId, sinkIdx, color, chip) {
      const now = performance.now(), dur = Math.max(650, Math.min(2600, 9000 / Math.sqrt(this.rate + 1)));
      const srcs = sourceId == null ? this.sources.map(s => s.id) : [sourceId];
      for (const id of srcs) {
        const key = id + "|" + sinkIdx, list = this.byPair.get(key);
        if (list) this.pulses.push({ list, color, t0: now, dur, sweep: sourceId == null });
      }
      if (chip) {
        const list = this.byPair.get((sourceId ?? (this.sources[0] || {}).id) + "|" + sinkIdx);
        if (list) this.chips.push({ s: list[0], text: chip, color, t0: now + dur * 0.35, life: 2600 + dur });
      }
      if (this.pulses.length > 160) this.pulses.splice(0, this.pulses.length - 160);
      if (this.chips.length > 14) this.chips.splice(0, this.chips.length - 14);
    }

    build() {
      const dpr = window.devicePixelRatio || 1, W = this.host.clientWidth, H = this.host.clientHeight;
      this.W = W; this.H = H; this.cv.width = W * dpr; this.cv.height = H * dpr;
      this.cv.style.width = W + "px"; this.cv.style.height = H + "px";
      const hb = this.host.getBoundingClientRect();
      this.sinks = this.sinkEls.map(el => { const r = el.getBoundingClientRect(); return { x: r.left - hb.left + r.width / 2, y: r.top - hb.top, b: r.bottom - hb.top }; });
      this.cardsY = this.sinks.length ? Math.min(...this.sinks.map(s => s.y)) : H * 0.6;
      const n = Math.max(1, this.sources.length), top = 84, lensX = W / 2, lensY = Math.max(120, this.cardsY * 0.42);
      this.srcPos = this.sources.map((s, i) => [W * (0.08 + 0.84 * (n === 1 ? 0.5 : i / (n - 1))), top]);
      this.strands = []; this.byPair.clear();
      this.sources.forEach((s, i) => this.sinks.forEach((k, j) => {
        const list = [];
        for (let q = 0; q < 9; q++) {
          const r1 = rnd(i * 131 + j * 17 + q), r2 = rnd(i * 7 + j * 311 + q * 3), r3 = rnd(q * 97 + i + j);
          const [sx, sy] = this.srcPos[i];
          const p = [sx + (r1 - 0.5) * 14, sy + 6, sx + (r2 - 0.5) * 90, sy + (lensY - sy) * (0.55 + r3 * 0.3),
                     lensX + (r1 - 0.5) * 70 + (k.x - lensX) * 0.18, lensY + (r2 - 0.2) * 60, k.x + (r3 - 0.5) * 40, k.y - 2];
          list.push(p); this.strands.push(p);
        }
        this.byPair.set(s.id + "|" + j, list);
      }));
      // static layer: the dense fiber mesh (thin, faint, monochrome) + the ceiling lattice
      const off = document.createElement("canvas"); off.width = this.cv.width; off.height = this.cv.height;
      const c = off.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.lineWidth = 0.6;
      for (let q = 0; q < 260; q++) {  // ceiling lattice: strands from the top edge into the source row
        const x0 = rnd(q * 3.1) * W, i = Math.floor(rnd(q * 7.7) * n), [sx, sy] = this.srcPos[i] || [W / 2, top];
        c.strokeStyle = `rgba(210,210,210,${0.025 + rnd(q) * 0.04})`;
        c.beginPath(); c.moveTo(x0, 0); c.quadraticCurveTo((x0 + sx) / 2 + (rnd(q * 5) - 0.5) * 120, 10, sx, sy); c.stroke();
      }
      for (const p of this.strands) {
        c.strokeStyle = `rgba(215,215,215,${0.045 + rnd(p[0]) * 0.05})`;
        c.beginPath(); c.moveTo(p[0], p[1]); c.bezierCurveTo(p[2], p[3], p[4], p[5], p[6], p[7]); c.stroke();
      }
      // drop lines from each card down to the floor
      this.sinks.forEach((k, j) => {
        c.strokeStyle = "rgba(220,220,220,0.18)"; c.setLineDash([2, 4]);
        c.beginPath(); c.moveTo(k.x, k.b + 4); c.lineTo(k.x, this.floorTop() + 8); c.stroke(); c.setLineDash([]);
      });
      this.staticImg = off; this.dirty = false; this.floorDirty = true;
      this.dots = Array.from({ length: 90 }, (_, q) => ({ s: this.strands[Math.floor(rnd(q * 13.3) * this.strands.length)], t: rnd(q * 2.2) }));
    }
    floorTop() { return (this.sinks.length ? Math.max(...this.sinks.map(s => s.b)) : this.H * 0.62) + 26; }
    proj(u, v) {  // floor plane in fake perspective: far edge narrow, near edge wide
      const ft = this.floorTop(), fh = this.H - ft - 10, w = this.W * (0.5 + 0.48 * v);
      return [this.W / 2 + (u - 0.5) * w, ft + Math.pow(v, 1.25) * fh];
    }
    buildFloor() {
      const dpr = window.devicePixelRatio || 1, off = document.createElement("canvas");
      off.width = this.cv.width; off.height = this.cv.height;
      const c = off.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0);
      const GX = 84, GY = 30, pts = this.floorPts;
      const h = (u, v) => {  // terrain: real points as hills + slow low-frequency relief
        let z = 0.18 * Math.sin(u * 7.1 + v * 3.3) * Math.cos(v * 5.7 - u * 2.1) + 0.1 * Math.sin(u * 17 + v * 11);
        for (const p of pts) { const dx = (u - p.u) / 0.09, dy = (v - p.v) / 0.22; z += p.h * Math.exp(-(dx * dx + dy * dy)); }
        return z;
      };
      const g = [];
      for (let y = 0; y <= GY; y++) { g.push([]); for (let x = 0; x <= GX; x++) g[y].push(h(x / GX, y / GY)); }
      c.lineWidth = 0.7;
      for (let L = -0.3; L <= 1.2; L += 0.1) {  // marching squares, one contour level at a time
        c.strokeStyle = `rgba(205,205,205,${0.10 + Math.max(0, L) * 0.16})`;
        c.beginPath();
        for (let y = 0; y < GY; y++) for (let x = 0; x < GX; x++) {
          const a = g[y][x], b = g[y][x + 1], d = g[y + 1][x], e = g[y + 1][x + 1];
          const P = [];
          const edge = (v1, v2, x1, y1, x2, y2) => { if ((v1 < L) !== (v2 < L)) { const t = (L - v1) / (v2 - v1); P.push([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t]); } };
          edge(a, b, x, y, x + 1, y); edge(b, e, x + 1, y, x + 1, y + 1); edge(d, e, x, y + 1, x + 1, y + 1); edge(a, d, x, y, x, y + 1);
          for (let k = 0; k + 1 < P.length; k += 2) {
            const [x1, y1] = this.proj(P[k][0] / GX, P[k][1] / GY), [x2, y2] = this.proj(P[k + 1][0] / GX, P[k + 1][1] / GY);
            c.moveTo(x1, y1); c.lineTo(x2, y2);
          }
        }
        c.stroke();
      }
      // grid edge of the plane
      c.strokeStyle = "rgba(220,220,220,0.12)"; c.beginPath();
      [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]].forEach(([u, v], i) => { const [x, y] = this.proj(u, v); i ? c.lineTo(x, y) : c.moveTo(x, y); }); c.stroke();
      this.floorImg = off; this.floorDirty = false;
    }

    frame(now) {
      if (this.dirty) { if (!this.host.clientWidth || !this.sinkEls) return; this.build(); }
      if (this.floorDirty) this.buildFloor();
      const c = this.ctx, dpr = window.devicePixelRatio || 1, W = this.W, H = this.H;
      c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, this.cv.width, this.cv.height);
      c.drawImage(this.floorImg, 0, 0); c.drawImage(this.staticImg, 0, 0);
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      // floor hotspots: real positions / candidates, breathing at the bot's pace
      const beat = (now / 1000) * (0.4 + this.rate / 40);
      for (const p of this.floorPts) {
        if (!p.color) continue;
        const [x, y] = this.proj(p.u, p.v), r = 10 + p.h * 26 + Math.sin(beat + p.u * 9) * 3;
        const g = c.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, p.color + "cc"); g.addColorStop(0.35, p.color + "44"); g.addColorStop(1, p.color + "00");
        c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
        if (p.label) { c.font = "9px JetBrains Mono, monospace"; c.fillStyle = "rgba(235,235,235,.75)"; c.fillText(p.label, x + 6, y - 6); }
      }
      // ambient flow: tiny dots drifting down the fibers at the bot's real event rate
      const sp = 0.0009 + this.rate * 0.00009;
      c.fillStyle = "rgba(255,255,255,.55)";
      for (const d of this.dots) { if (!d.s) continue; d.t += sp; if (d.t > 1) d.t -= 1; const [x, y] = bez(d.s, d.t); c.fillRect(x, y, 1.2, 1.2); }
      // pulses: coloured strand bundles with a travelling head
      c.lineCap = "round";
      this.pulses = this.pulses.filter(p => now - p.t0 < p.dur * 1.8);
      for (const p of this.pulses) {
        const age = (now - p.t0) / p.dur;
        if (age < 0) continue;
        const head = Math.min(1, age), fade = age > 1 ? 1 - (age - 1) / 0.8 : 1;
        c.strokeStyle = p.color; c.globalAlpha = (p.sweep ? 0.18 : 0.55) * fade; c.lineWidth = p.sweep ? 0.6 : 1.1;
        c.shadowColor = p.color; c.shadowBlur = p.sweep ? 0 : 8;
        for (const s of p.list) {
          c.beginPath(); c.moveTo(s[0], s[1]);
          const steps = 22; for (let k = 1; k <= steps * head; k++) { const [x, y] = bez(s, k / steps); c.lineTo(x, y); }
          c.stroke();
          if (age < 1 && !p.sweep) { const [x, y] = bez(s, head); c.globalAlpha = fade; c.fillStyle = "#fff"; c.fillRect(x - 1.2, y - 1.2, 2.4, 2.4); }
        }
        c.shadowBlur = 0; c.globalAlpha = 1;
      }
      // source row: small objects + labels
      c.font = "10px JetBrains Mono, monospace"; c.textAlign = "center";
      this.sources.forEach((s, i) => {
        const [x, y] = this.srcPos[i] || [0, 0], lit = this.pulses.some(p => !p.sweep && now - p.t0 < p.dur && p.list === this.byPair.get(s.id + "|" + 0)) || s.lit;
        c.fillStyle = lit ? "#f2e14c" : "rgba(200,200,200,.8)"; c.fillRect(x - 9, y - 4, 18, 8);
        c.strokeStyle = "rgba(255,255,255,.35)"; c.strokeRect(x - 13, y - 8, 26, 16);
        c.fillStyle = "rgba(235,235,235,.85)"; c.fillText(s.label || "", x, y - 14);
        if (s.sub) { c.fillStyle = "rgba(160,160,160,.75)"; c.fillText(s.sub, x, y + 20); }
      });
      c.textAlign = "left";
      // chips: little labelled tags riding the strands, like the decision readouts
      this.chips = this.chips.filter(ch => now - ch.t0 < ch.life);
      for (const ch of this.chips) {
        const age = now - ch.t0; if (age < 0) continue;
        const slot = this.chips.indexOf(ch) % 5, [x, y0] = bez(ch.s, 0.3 + slot * 0.1), y = y0, a = Math.min(1, age / 200, (ch.life - age) / 500);
        c.globalAlpha = Math.max(0, a); c.font = "bold 9.5px JetBrains Mono, monospace";
        const w = c.measureText(ch.text).width + 10;
        c.fillStyle = ch.color; c.fillRect(x - w / 2, y - 8, w, 15);
        c.fillStyle = "#0a0a0a"; c.fillText(ch.text, x - w / 2 + 5, y + 3);
        c.globalAlpha = 1;
      }
    }
  }
  window.SignalField = SignalField;
})();
