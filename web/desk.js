/* SNIPER DESK: the paper-trading sniper's own view (core/sniper.py, /api/sniper).
   Operators strip, SIGNAL CORE vortex (canvas), evidence matrix, handoffs, risk gauge, live log, DryFlip handoff queue.
   Uses app.js helpers: $, esc, usd, ago, coinImg, openPop, adminPw. */
(() => {
  const D = { on: false, data: null, after: 0, log: [], logTab: "all", codeTab: "risk_gate.py", timer: null };
  const OP_COL = { WATCHER: "#d9d9d9", ANALYST: "#f2e14c", RISK: "#e85cf0", EXECUTOR: "#ffffff", REPORTER: "#a78bfa", HUMAN: "#7dd3fc" };
  // each operator is an instrument: "verb · object", like a decision readout
  const LANE = { WATCHER: ["Scan", "launches"], ANALYST: ["Score", "confidence"], RISK: ["Choice", "gate"], EXECUTOR: ["Fill", "paper book"],
    REPORTER: ["Write", "trace"], HUMAN: ["Handoff", "dryflip"] };
  const KIND = { fill: "FILL", pass: "PASS", hold: "HOLD", handoff: "HANDOFF", warn: "ALERT", active: "ACTIVE", info: "SYNC", paused: "PAUSED", idle: "IDLE" };
  const f2 = (v) => v == null ? "—" : Number(v).toFixed(2);
  const sol = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(3);

  function spark(vals, w = 120, h = 26, col = "#f2e14c") {
    const v = (vals || []).filter(x => x != null);
    if (v.length < 2) return `<svg class="dk-spark" viewBox="0 0 ${w} ${h}"><path d="M0 ${h / 2} L${w} ${h / 2}" stroke="${col}55" fill="none"/></svg>`;
    const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
    const d = v.map((x, i) => `${i ? "L" : "M"}${(i / (v.length - 1) * w).toFixed(1)} ${(h - 3 - (x - lo) / r * (h - 6)).toFixed(1)}`).join(" ");
    return `<svg class="dk-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${d}" stroke="${col}" stroke-width="1.6" fill="none" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // ---- layout ------------------------------------------------------------------------------
  function mount() {
    if ($("#desk").dataset.ready) return;
    $("#desk").dataset.ready = 1;
    $("#desk").innerHTML = `
      <header class="dk-top">
        <div class="dk-brand"><i></i><b>TRENCH</b> <span>Sniper Desk</span></div>
        <div class="dk-title"><b>SNIPER DESK / LANDSCAPE OF SIGNALS</b><span id="dk-sub">paper trading · no wallet · no real orders</span></div>
        <div class="dk-ctl">
          <div class="dk-seg" id="dk-mode" role="tablist" title="AUTO: the bot paper-fills its own trades. DRYFLIP: every trade becomes a handoff you mirror in the DryFlip extension.">
            <button data-mode="auto">AUTO PAPER</button><button data-mode="dryflip">DRYFLIP HANDOFF</button></div>
          <div class="dk-seg" id="dk-view"><button data-dv="live" aria-selected="true">◉ LIVE</button><button data-dv="hist" aria-selected="false">☰ HISTORY</button></div>
          <button class="dk-btn" id="dk-run">PAUSE</button><button class="dk-btn" id="dk-cfg">⚙ RULES</button>
          <span class="dk-live"><i></i>LIVE</span><span class="dk-clock" id="dk-clock"></span>
        </div>
      </header>
      <section class="dk-hist" id="dk-hist" hidden></section>
      <section class="dk-metrics" id="dk-metrics"></section>
      <section class="dk-field" id="dk-field"><canvas id="dk-canvas"></canvas>
        <div class="dk-fieldlbl"><b>LANDSCAPE OF SIGNALS</b><span>fresh launches → six operators → paper book · every pulse is a real decision, at its real speed</span></div>
        <div class="dk-ops" id="dk-ops"></div>
        <div class="dk-floorlbl"><b>STATE</b> · one shared book · hills = candidates by confidence · lights = open positions</div>
      </section>
      <div class="dk-ticker"><div class="dk-tick-track" id="dk-ticker"></div></div>
      <section class="dk-main">
        <div class="dk-col">
          <div class="dk-card"><div class="dk-h">WATCHLIST <i>fresh launches through your filters</i><em id="dk-wn"></em></div><div class="dk-fchips" id="dk-fchips"></div><div id="dk-watch"></div></div>
          <div class="dk-card"><div class="dk-h">OPEN POSITIONS <em id="dk-on"></em></div><div id="dk-pos"></div></div>
          <div class="dk-card"><div class="dk-h">EQUITY <i>paper book, SOL</i><em id="dk-eq"></em></div><div id="dk-equity" class="dk-equity"></div></div>
        </div>
        <div class="dk-col">
          <div class="dk-card dk-code"><div class="dk-tabs" id="dk-codetabs"></div><pre id="dk-code"></pre></div>
          <div class="dk-card dk-logc"><div class="dk-h">LIVE LOG <span class="dk-tabs small" id="dk-logtabs"></span></div><div id="dk-log" class="dk-log"></div></div>
        </div>
        <div class="dk-col">
          <div class="dk-card dk-learn"><div class="dk-h">ALWAYS LEARNING <i>scores itself on what happened</i><em id="dk-lv"></em></div><div id="dk-learn"></div></div>
          <div class="dk-card dk-speed"><div class="dk-h">SPEED <i>launch → decision, measured</i><em id="dk-spd"></em></div><div id="dk-speed"></div></div>
          <div class="dk-card dk-ready"><div class="dk-h">LIVE READINESS <i>is the paper record good enough to risk money?</i></div><div id="dk-ready"></div></div>
          <div class="dk-card dk-dry" id="dk-drycard"><div class="dk-h">DRYFLIP HANDOFF QUEUE <em id="dk-dn"></em></div><div id="dk-dry"></div></div>
          <div class="dk-card"><div class="dk-h">EVIDENCE MATRIX <i>top candidates × evidence</i></div><div id="dk-matrix"></div></div>
          <div class="dk-card"><div class="dk-h">HANDOFFS <i>operator activity</i></div><div id="dk-hand"></div></div>
          <div class="dk-card"><div class="dk-h">RISK GAUGE <i>exposure of equity</i></div><div id="dk-gauge"></div></div>
        </div>
      </section>
      <footer class="dk-foot" id="dk-foot"></footer>
      <dialog id="dk-recap"><form method="dialog"><h3>◆ WEEKLY RECAP</h3><pre id="dk-recap-text" class="dk-recap"></pre>
        <div class="set-actions"><button value="cancel">CLOSE</button><button id="dk-recap-new" value="new">WRITE A NEW ONE NOW</button></div></form></dialog>
      <dialog id="dk-rules"><form method="dialog" id="dk-rules-form"><h3>◆ SNIPER RULES</h3><div class="dk-rgrid" id="dk-rgrid"></div>
        <label class="set-key">AXIOM FILTER PRESET <i class="dim" style="font-style:normal">paste the JSON Axiom exports (New Pairs) · leave as is to keep the current one · type <b>clear</b> to remove</i>
          <textarea name="axiom" rows="5" placeholder='{"filters": {"protocols": {...}, "age": {"max": 4, ...}}, "tab": "newPairs"}'></textarea></label>
        <div class="dk-fsum" id="dk-fsum"></div>
        <p class="dim small">Paper trading only. The bot never touches a wallet. Fills use the live price plus your slippage, the terminal fee and a priority fee (like DryFlip models them).</p>
        <p class="small" id="dk-rmsg"></p>
        <div class="set-actions"><button value="cancel" formnovalidate>CLOSE</button><button id="dk-reset" value="reset" formnovalidate>RESET BOOK</button><button value="default">SAVE</button></div></form></dialog>`;
    document.querySelectorAll("#dk-mode button").forEach(b => b.onclick = () => save({ mode: b.dataset.mode }));
    document.querySelectorAll("#dk-view button").forEach(b => b.onclick = () => setDeskView(b.dataset.dv));
    $("#dk-run").onclick = () => save({ enabled: !(D.data && D.data.settings.enabled) });
    $("#dk-cfg").onclick = openRules;
    $("#dk-rules-form").addEventListener("submit", onRules);
    $("#dk-logtabs").innerHTML = ["all", "trades", "signals", "handoffs"].map(t => `<button data-t="${t}">${t.toUpperCase()}</button>`).join("");
    $("#dk-logtabs").querySelectorAll("button").forEach(b => b.onclick = () => { D.logTab = b.dataset.t; renderLog(); });
    $("#dk-codetabs").innerHTML = ["sniper_desk.py", "evidence.py", "risk_gate.py"].map(t => `<button data-t="${t}">${t}</button>`).join("") + '<span class="dk-live sm"><i></i>LIVE</span>';
    $("#dk-codetabs").querySelectorAll("button").forEach(b => b.onclick = () => { D.codeTab = b.dataset.t; renderCode(); });
    D.field = new SignalField($("#dk-canvas"), $("#dk-field"));
  }

  async function post(url, body) {
    const pw = await adminPw(); if (pw === null) return null;
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json", "x-admin-password": pw }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { alert(r.status === 403 ? "Wrong admin password." : "Request failed (" + r.status + ")"); ADMIN_PW = ""; return null; }
    return r.json();
  }
  async function save(upd) { await post("/api/sniper/settings", upd); poll(true); }

  const RULES = [["gate", "Confidence gate (0-1)", 0.01], ["size_sol", "Buy size (SOL)", 0.05], ["max_open", "Max open positions", 1],
    ["entry_min_mcap", "Entry mcap min ($)", 1000], ["entry_max_mcap", "Entry mcap max ($)", 1000], ["max_age_min", "Max coin age (min)", 5],
    ["tp1_x", "Take-profit 1 (x)", 0.1], ["tp1_sell_pct", "TP1 sells (%)", 5], ["tp2_x", "Take-profit 2 (x)", 0.1],
    ["trail_pct", "Trailing stop after TP1 (%)", 1], ["stop_loss_pct", "Stop-loss (%)", 1], ["max_hold_min", "Time stop (min)", 5],
    ["slippage_pct", "Slippage (%)", 0.5], ["fee_pct", "Terminal fee (%)", 0.1], ["priority_sol", "Priority fee (SOL)", 0.0005],
    ["cooldown_min", "Re-entry cooldown (min)", 5]];
  function openRules() {
    const s = (D.data || {}).settings || {};
    $("#dk-rgrid").innerHTML = RULES.map(([k, l, st]) => `<label class="set-key">${l}<input name="${k}" type="number" step="${st}" value="${s[k] ?? ""}"></label>`).join("") +
      `<label class="set-key">Handoff terminal<select name="terminal">${["axiom", "gmgn", "dexscreener"].map(t => `<option ${s.terminal === t ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
    const fs = (D.data || {}).filters || { active: [], ignored: [] };
    $("#dk-fsum").innerHTML = `<div><b>Applied:</b> ${fs.active.map(esc).join(" · ") || "none"}</div>` +
      (fs.ignored.length ? `<div><b>Not measured (Axiom-only data, ignored):</b> ${fs.ignored.map(esc).join(" · ")}</div>` : "");
    $("#dk-rmsg").textContent = ""; $("#dk-rules").showModal();
  }
  async function onRules(e) {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    if (e.submitter && e.submitter.value === "reset") {
      if (!confirm("Reset the paper book? Positions and history are cleared and the balance goes back to the start.")) return;
      if (await post("/api/sniper/reset")) { $("#dk-rules").close(); D.after = 0; D.log = []; poll(true); }
      return;
    }
    const f = e.target, upd = {};
    RULES.forEach(([k]) => { if (f[k].value !== "") upd[k] = Number(f[k].value); });
    upd.terminal = f.terminal.value;
    const ax = f.axiom.value.trim();
    if (ax) {
      let body;
      if (ax.toLowerCase() === "clear") body = { filters: {} };
      else { try { body = JSON.parse(ax); } catch { $("#dk-rmsg").textContent = "That isn't valid JSON: copy the whole preset from Axiom."; return; } }
      if (!(await post("/api/sniper/filters", body))) return;
    }
    if (await post("/api/sniper/settings", upd)) { $("#dk-rules").close(); poll(true); }
  }

  // ---- render ------------------------------------------------------------------------------
  function render() {
    const d = D.data; if (!d) return;
    const s = d.settings, k = d.kpi;
    document.querySelectorAll("#dk-mode button").forEach(b => b.setAttribute("aria-selected", b.dataset.mode === s.mode));
    $("#dk-run").textContent = s.enabled ? "❚❚ PAUSE" : "▶ RUN";
    $("#dk-sub").textContent = s.mode === "dryflip" ? "DryFlip handoff · the bot finds, you mirror in DryFlip" : "auto paper · the bot fills its own simulated trades";
    // operators
    const bars = (v) => { const xs = (v || []).slice(-24), mx = Math.max(1e-9, ...xs.map(Math.abs));
      return `<div class="dk-bars">${xs.map(x => `<i style="height:${Math.max(8, Math.abs(x) / mx * 100)}%"></i>`).join("")}</div>`; };
    $("#dk-ops").innerHTML = Object.entries(d.operators).map(([n, o], i) => `<div class="dk-op ${o.status === "hold" || o.status === "handoff" || o.status === "fill" || o.status === "pass" ? "hot" : ""}" style="--oc:${OP_COL[n]}">
      <div class="dk-op-h"><span>${String(i + 1).padStart(2, "0")}</span><b>${n}</b><em class="k-${o.status}">${KIND[o.status] || o.status.toUpperCase()}</em></div>
      <div class="dk-op-r">${bars(o.spark)}<b>${o.value == null ? "—" : typeof o.value === "number" && o.value <= 1.5 ? f2(o.value) : esc(String(o.value))}</b></div>
      ${spark(o.spark, 140, 16, OP_COL[n])}<div class="dk-op-l">${LANE[n][0]} · <b>${LANE[n][1]}</b></div></div>`).join("");
    // watchlist
    $("#dk-wn").textContent = d.candidates.length;
    const fs = d.filters || { active: [], ignored: [] };
    $("#dk-fchips").innerHTML = fs.active.length ? fs.active.slice(0, 4).map(a => `<span>${esc(a.length > 34 ? a.slice(0, 33) + "…" : a)}</span>`).join("") +
      (fs.active.length > 4 ? `<span>+${fs.active.length - 4}</span>` : "") + (fs.ignored.length ? `<span class="ig" title="${esc(fs.ignored.join(", "))}">${fs.ignored.length} not measured</span>` : "")
      : '<span class="ig">no Axiom preset · ⚙ RULES to paste one</span>';
    $("#dk-watch").innerHTML = d.candidates.map(c => `<div class="dk-row" data-mint="${esc(c.mint)}">${coinImg(c.image, c.symbol, false, c.mint)}
      <div class="dk-rn"><b>$${esc(c.symbol || "?")}</b><span>${usd(c.mcap)} · ${c.age_min}m</span></div>
      <div class="dk-bar"><i style="width:${Math.round(c.conf * 100)}%;background:${c.conf >= s.gate ? "#f2e14c" : "#4a4a4a"}"></i></div><em>${f2(c.conf)}</em></div>`).join("") || '<div class="dk-empty">watching for fresh launches…</div>';
    // equity
    const eq = [s.start_balance_sol, ...d.pnl_series.map(p => s.start_balance_sol + p[1]), k.equity_sol];
    $("#dk-eq").innerHTML = `<b style="color:${k.pnl_sol >= 0 ? "#7ee2a8" : "#ff8fb1"}">${sol(k.pnl_sol)} SOL</b>`;
    $("#dk-equity").innerHTML = spark(eq, 300, 70, "#f2e14c") + `<div class="dk-eqv"><span>${f2(k.equity_sol)} SOL equity</span><span>${f2(k.balance_sol)} cash</span></div>`;
    // positions
    $("#dk-on").textContent = `${d.positions.length}/${s.max_open}`;
    $("#dk-pos").innerHTML = d.positions.map(p => `<div class="dk-row" data-mint="${esc(p.mint)}">${coinImg(p.image, p.symbol, false, p.mint)}
      <div class="dk-rn"><b>$${esc(p.symbol || "?")}${p.mode === "dryflip" ? ' <i class="dk-tag">DRYFLIP</i>' : ""}</b><span>${p.size_sol} SOL · in ${usd(p.entry_mcap)} · ${ago(p.opened_ts)}</span></div>
      <em class="${(p.last_x || 1) >= 1 ? "up" : "dn"}">${f2(p.last_x)}x</em></div>`).join("") || '<div class="dk-empty">no open positions</div>';
    const fc = d.focus, rate = eventRate();
    $("#dk-metrics").innerHTML = [["DECISIONS", (k.decisions ?? 0).toLocaleString()], ["EVENTS / MIN", rate.toFixed(1)], ["MEDIAN TICK", k.tick_ms != null ? k.tick_ms + " ms" : "—"],
      ["FOCUS", fc ? "$" + esc(fc.symbol || "?") + " " + f2(fc.conf) : "—"], ["GATE", f2(s.gate)], ["GATE PASS", k.gate_pass_pct != null ? k.gate_pass_pct + "%" : "—"],
      ["EQUITY", f2(k.equity_sol) + " SOL"], ["EXPOSURE", k.exposure_pct + "%"], ["COST / TRADE", k.trades ? (k.fees_sol / k.trades).toFixed(4) + " SOL" : "—"]]
      .map(([l, v]) => `<div><span>${l}</span><b>${v}</b><i>${"▮".repeat(3)}</i></div>`).join("");
    // evidence matrix: rows = top candidates, cols = evidence
    const keys = d.evidence_keys;
    $("#dk-matrix").innerHTML = `<div class="dk-mx" style="--n:${keys.length}"><span></span>${keys.map(x => `<span class="mxk" title="${x}">${x.slice(0, 3)}</span>`).join("")}
      ${d.candidates.slice(0, 6).map(c => `<span class="mxr">$${esc((c.symbol || "?").slice(0, 6))}</span>` + keys.map(x => {
        const v = c.ev[x]; if (v == null) return `<i class="na" title="${x}: not measured yet"></i>`;
        return `<i title="${x} ${f2(v)}" style="background:rgba(${Math.round(60 + 182 * v)},${Math.round(60 + 165 * v)},${Math.round(60 + 16 * v)},${(0.35 + v * 0.65).toFixed(2)})"></i>`; }).join("")).join("")}</div>`
      || '<div class="dk-empty">no candidates</div>';
    // handoffs: operator activity in the log window
    const cnt = {}; D.log.slice(-200).forEach(e => cnt[e.op] = (cnt[e.op] || 0) + 1);
    const mx = Math.max(1, ...Object.values(cnt));
    $("#dk-hand").innerHTML = Object.keys(d.operators).map(n => `<div class="dk-hb"><span>${n}</span><div><i style="width:${(cnt[n] || 0) / mx * 100}%;background:${OP_COL[n]}"></i></div></div>`).join("");
    // risk gauge
    const ex = Math.min(100, k.exposure_pct || 0), R = 34, C = 2 * Math.PI * R;
    $("#dk-gauge").innerHTML = `<div class="dk-gw"><svg viewBox="0 0 90 90"><circle cx="45" cy="45" r="${R}" stroke="#222" stroke-width="9" fill="none"/>
      <circle cx="45" cy="45" r="${R}" stroke="url(#dkg)" stroke-width="9" fill="none" stroke-linecap="round" stroke-dasharray="${(ex / 100 * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 45 45)"/>
      <defs><linearGradient id="dkg"><stop offset="0" stop-color="#8a8a8a"/><stop offset="1" stop-color="#f2e14c"/></linearGradient></defs>
      <text x="45" y="50" text-anchor="middle" fill="#f3e6f0" font-size="15" font-family="JetBrains Mono" font-weight="700">${Math.round(ex)}%</text></svg>
      <div class="dk-gs"><div><span>DD</span><b>${k.pnl_sol < 0 ? (k.pnl_sol / s.start_balance_sol * 100).toFixed(1) + "%" : "0%"}</b></div><div><span>OPEN</span><b>${k.open}</b></div>
      <div><span>SL</span><b>-${s.stop_loss_pct}%</b></div><div><span>TP</span><b>${s.tp1_x}x</b></div></div></div>`;
    // live readiness 0.0-100
    const rd = d.readiness, sc = rd.score, rc = sc < 40 ? "#ff8fb1" : sc < 70 ? "#ffcf8a" : "#7ee2a8";
    $("#dk-ready").innerHTML = `<div class="dk-rm"><b style="color:${rc}">${sc.toFixed(1)}</b><span>/ 100</span><em style="color:${rc}">${esc(rd.verdict.toUpperCase())}</em></div>
      <div class="dk-rbar"><i style="width:${sc}%;background:linear-gradient(90deg,#6b6b6b,${rc})"></i><s style="left:70%"></s></div>
      ${rd.caps.map(c => `<div class="dk-rcap">⚠ ${esc(c)}</div>`).join("")}
      <div class="dk-rparts">${Object.entries(rd.parts).map(([k, p]) => `<div title="${esc(p.note)}"><span>${k.replace("_", " ")}</span><div><i style="width:${p.score}%"></i></div><em>${esc(p.note)}</em></div>`).join("")}</div>
      <div class="dk-rnote">Information only: the bot has no wallet and never trades real money.</div>`;
    // DryFlip queue
    $("#dk-drycard").classList.toggle("on", s.mode === "dryflip" || d.handoffs.length > 0);
    $("#dk-dn").textContent = d.handoffs.length;
    $("#dk-dry").innerHTML = d.handoffs.map(p => { const h = p.handoff, ins = h.instructions, u = h.urls;
      return `<div class="dk-ho"><div class="dk-row" data-mint="${esc(p.mint)}">${coinImg(p.image, p.symbol, false, p.mint)}<div class="dk-rn"><b>#${p.id} BUY $${esc(p.symbol || "?")}</b><span>conf ${f2(p.conf)} · ${ago(h.created)} ago · now ${f2(p.last_x)}x</span></div></div>
        <div class="dk-ins"><span>Buy <b>${ins.buy_sol} SOL</b></span><span>TP <b>${ins.take_profit_x}x</b> / <b>${ins.take_profit_2_x}x</b></span><span>SL <b>-${ins.stop_loss_pct}%</b></span><span>Slip <b>${ins.slippage_pct}%</b></span></div>
        <div class="dk-hoa"><a class="dk-btn pri" href="${esc(u[s.terminal] || u.axiom)}" target="_blank" rel="noopener noreferrer">OPEN IN ${esc(s.terminal.toUpperCase())} ↗</a>
          <button class="dk-btn" data-copy="${esc(p.mint)}">COPY CA</button><button class="dk-btn ok" data-done="${p.id}">✓ DONE IN DRYFLIP</button><button class="dk-btn" data-skip="${p.id}">SKIP</button></div></div>`; }).join("")
      || `<div class="dk-empty">${s.mode === "dryflip" ? "no trades waiting. When the gate passes, the trade shows up here: open it in your terminal and place it with DryFlip's overlay." : "switch to DRYFLIP HANDOFF to mirror the bot's trades in the DryFlip extension"}</div>`;
    $("#dk-dry").querySelectorAll("[data-done]").forEach(b => b.onclick = async () => { if (await post(`/api/sniper/handoff/${b.dataset.done}/done`)) poll(true); });
    $("#dk-dry").querySelectorAll("[data-skip]").forEach(b => b.onclick = async () => { if (await post(`/api/sniper/handoff/${b.dataset.skip}/skip`)) poll(true); });
    $("#dk-dry").querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "COPIED"; } catch { prompt("Contract address:", b.dataset.copy); } });
    document.querySelectorAll("#desk .dk-row[data-mint]").forEach(r => { r.onclick = (e) => { e.stopPropagation(); openPop(r.dataset.mint, r); }; });
    // footer
    const lvl = k.exposure_pct < 25 ? "LOW" : k.exposure_pct < 55 ? "MEDIUM" : "HIGH";
    $("#dk-foot").innerHTML = [["TRADES", k.trades], ["WIN RATE", k.win_rate != null ? k.win_rate + "%" : "—"], ["GATE PASS", k.gate_pass_pct != null ? k.gate_pass_pct + "%" : "—"],
      ["MEDIAN TICK", k.tick_ms != null ? k.tick_ms + " ms" : "—"], ["FEES PAID", f2(k.fees_sol) + " SOL"], ["REALIZED", sol(k.realized_sol) + " SOL"]]
      .map(([l, v]) => `<div><span>${l}</span><b>${v}</b></div>`).join("") +
      `<div><span>POSITION RISK</span><b>${lvl}</b><div class="dk-blocks">${Array.from({ length: 10 }, (_, i) => `<i class="${i < Math.ceil(k.exposure_pct / 10) ? "on" : ""}"></i>`).join("")}</div></div>
       <div class="dk-disc">SIMULATED · PAPER TRADING · NOT TRADING ADVICE</div>`;
    renderLog(); renderCode();
    feedField(d);
  }

  function renderLog() {
    document.querySelectorAll("#dk-logtabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.t === D.logTab));
    const f = { all: () => true, trades: e => e.kind === "fill" || e.op === "EXECUTOR", signals: e => e.op === "ANALYST" || e.op === "RISK", handoffs: e => e.op === "HUMAN" }[D.logTab];
    $("#dk-log").innerHTML = D.log.filter(f).slice(-60).reverse().map(e => `<div class="dk-le k-${e.kind}"><span>${new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
      <b style="color:${OP_COL[e.op]}">${e.op.toLowerCase()}</b><em>${esc(e.msg)}</em></div>`).join("") || '<div class="dk-empty">quiet…</div>';
  }

  function renderCode() {
    const d = D.data; if (!d) return;
    const s = d.settings, fc = d.focus || { conf: 0, ev: {}, symbol: "—" };
    document.querySelectorAll("#dk-codetabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.t === D.codeTab));
    const v = (x) => `<i class="cv">${esc(String(x))}</i>`;
    const C = {
      "risk_gate.py": `<b>def</b> gate(coin, book):
    conf = analyst.score(coin)                 ${v("# $" + (fc.symbol || "—") + " conf " + f2(fc.conf))}
    <b>if</b> conf &lt; ${f2(s.gate)}: <b>return</b> HOLD          ${v(fc.conf >= s.gate ? "# passes" : "# holds")}
    <b>if</b> book.open &gt;= ${s.max_open}: <b>return</b> HOLD      ${v("# open " + d.kpi.open)}
    <b>if</b> book.cash &lt; ${s.size_sol} + fees: <b>return</b> HOLD  ${v("# cash " + f2(d.kpi.balance_sol))}
    <b>if</b> coin.flagged_fake: <b>return</b> NEVER
    <b>return</b> ${s.mode === "dryflip" ? "human.handoff(coin, dryflip)" : "executor.buy(coin, " + s.size_sol + ")"}

<b>def</b> manage(pos):
    <b>if</b> pos.x &gt;= ${s.tp1_x}: sell(${s.tp1_sell_pct}%)         <b>if</b> pos.x &gt;= ${s.tp2_x}: sell(rest)
    <b>if</b> after_tp1 <b>and</b> pos.x &lt;= peak*${(1 - s.trail_pct / 100).toFixed(2)}: sell(rest)
    <b>if</b> pos.x &lt;= ${(1 - s.stop_loss_pct / 100).toFixed(2)}: sell(rest)           <b>if</b> held &gt; ${s.max_hold_min}m: sell(rest)`,
      "evidence.py": `<b>EVIDENCE</b> = {  ${v("# $" + (fc.symbol || "—"))}
${d.evidence_keys.map(x => `    "${x.toLowerCase()}": ${(fc.ev || {})[x] == null ? "None   # not measured yet" : f2((fc.ev || {})[x])},`).join("\n")}
}
conf = weighted_mean(measured) × coverage   ${v("# = " + f2(fc.conf))}`,
      "sniper_desk.py": `<b>for</b> op <b>in</b> [watcher, analyst, risk, executor, reporter, human]:
    op.observe(tick)                       ${v("# tick " + d.kpi.ticks + " · " + (d.kpi.tick_ms ?? "—") + " ms")}
watcher.scan(fresh_launches, band=(${usd(s.entry_min_mcap)}, ${usd(s.entry_max_mcap)}), age&lt;=${s.max_age_min}m)
mode = "${s.mode}"                          ${v(s.mode === "dryflip" ? "# you place it in DryFlip" : "# bot fills on paper")}
book = Paper(start=${s.start_balance_sol} SOL, fee=${s.fee_pct}%, slip=${s.slippage_pct}%)   ${v("# equity " + f2(d.kpi.equity_sol))}`,
    };
    $("#dk-code").innerHTML = C[D.codeTab];
  }

  // ---- HISTORY: every trade, why it bought, how it ended, what the coin did after, mistakes, your notes ----------------
  const H = { view: "live", filter: "all", open: null, data: null };
  const TAGC = { sold_early: "#f2e14c", stopped_then_ran: "#ff9f5a", gave_back: "#ff6b6b", bought_rug: "#e85cf0", thin_evidence: "#a78bfa", late_entry: "#7dd3fc", stop_too_tight: "#ffb3d9" };
  const TAGN = { sold_early: "SOLD EARLY", stopped_then_ran: "STOPPED, THEN RAN", gave_back: "GAVE BACK GAINS", bought_rug: "BOUGHT A RUG", thin_evidence: "THIN EVIDENCE", late_entry: "BOUGHT THE TOP", stop_too_tight: "STOP TOO TIGHT" };
  function setDeskView(v) {
    H.view = v;
    document.querySelectorAll("#dk-view button").forEach(b => b.setAttribute("aria-selected", b.dataset.dv === v));
    const live = v === "live";
    ["#dk-metrics", "#dk-field", ".dk-ticker", ".dk-main", "#dk-foot"].forEach(sel => { const el = $("#desk " + sel) || $(sel); if (el) el.hidden = !live; });
    $("#dk-hist").hidden = live;
    if (D.field) D.field.active = live && D.on;
    if (!live) loadHistory();
  }
  async function loadHistory() {
    try { H.data = await j("/api/sniper/history?filter=" + H.filter); } catch { return; }
    renderHistory();
  }
  function renderHistory() {
    const d = H.data; if (!d) return;
    const sm = d.summary, f2 = (v) => v == null ? "—" : Number(v).toFixed(2), x = (v) => v == null ? "—" : Number(v).toFixed(2) + "x";
    const fl = [["all", "ALL"], ["open", "OPEN"], ["wins", "WINS"], ["losses", "LOSSES"], ["mistakes", "MISTAKES"], ["noted", "NOTED"]];
    const mxEdge = Math.max(0.01, ...sm.evidence_edge.map(e => Math.abs(e.gap)));
    $("#dk-hist").innerHTML = `
      <div class="hs-top">
        <div class="hs-kpis">${[["TRADES", sm.trades], ["WIN RATE", sm.win_rate != null ? sm.win_rate + "%" : "—"], ["REALIZED", (sm.realized_sol >= 0 ? "+" : "") + sm.realized_sol + " SOL"],
          ["AVG WIN", sm.avg_win_sol != null ? "+" + sm.avg_win_sol : "—"], ["AVG LOSS", sm.avg_loss_sol ?? "—"], ["OPEN", sm.open]].map(([l, v]) => `<div><span>${l}</span><b>${v}</b></div>`).join("")}</div>
        <div class="hs-ins">
          <div class="dk-card"><div class="dk-h">MISTAKES <i>found automatically · click to filter</i></div>
            ${Object.entries(sm.mistakes).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<div class="hs-m" title="${esc(d.mistake_labels[k] || "")}"><i style="background:${TAGC[k]}"></i><span>${TAGN[k] || k}</span><b>${n}</b><em>${esc((d.mistake_labels[k] || "").split(": ")[1] || "")}</em></div>`).join("") || '<div class="dk-empty">no mistakes tagged yet</div>'}</div>
          <div class="dk-card"><div class="dk-h">WHAT SEPARATED WINNERS FROM LOSERS <i>average evidence at entry</i></div>
            ${sm.evidence_edge.map(e => `<div class="hs-e"><span>${e.feature}</span><div><i class="${e.gap >= 0 ? "pos" : "neg"}" style="width:${Math.abs(e.gap) / mxEdge * 50}%;${e.gap >= 0 ? "left:50%" : "right:50%"}"></i></div><b>${f2(e.winners)} vs ${f2(e.losers)}</b></div>`).join("") || '<div class="dk-empty">needs a few wins and losses</div>'}</div>
          <div class="dk-card"><div class="dk-h">HOW TRADES ENDED <i>exit reason · count · SOL</i></div>
            ${Object.entries(sm.by_exit).map(([k, v]) => `<div class="hs-x"><span>${esc(k)}</span><b>${v.n}</b><em class="${v.pnl >= 0 ? "up" : "dn"}">${v.pnl >= 0 ? "+" : ""}${v.pnl}</em></div>`).join("") || '<div class="dk-empty">no closed trades yet</div>'}</div>
        </div>
      </div>
      <div class="hs-bar"><div class="dk-seg">${fl.map(([k, l]) => `<button data-hf="${k}" aria-selected="${H.filter === k}">${l}</button>`).join("")}</div>
        <span class="dim">${d.rows.length} trades · click a trade to see why it bought, how it exited and what the coin did after</span>
        <button class="dk-btn" id="hs-csv">⤓ EXPORT CSV</button></div>
      <div class="hs-table"><div class="hs-h"><span>COIN</span><span>BOUGHT</span><span>ENTRY</span><span>CONF</span><span>EXIT</span><span>PEAK</span><span>AFTER (24H PEAK)</span><span>PNL</span><span>MISTAKES</span></div>
        ${d.rows.map(r => `<div class="hs-r ${r.status !== "closed" ? "open" : r.win ? "win" : "loss"} ${H.open === r.id ? "sel" : ""}" data-id="${r.id}">
          <span class="hs-coin">${coinImg(r.image, r.symbol, false, r.mint)}<b>$${esc(r.symbol || "?")}</b>${r.mode === "dryflip" ? '<i class="dk-tag">DRYFLIP</i>' : ""}${r.note ? ' <i title="has a note">✎</i>' : ""}</span>
          <span>${ago(r.opened_ts)} ago${r.entry_age_s != null ? `<small>coin ${dur(r.entry_age_s)} old</small>` : ""}</span>
          <span>${usd(r.entry_mcap)}<small>${r.size_sol} SOL</small></span>
          <span class="${r.conf < d.gate + 0.03 ? "warn" : ""}">${f2(r.conf)}</span>
          <span>${r.status !== "closed" ? '<em class="open">open · ' + x(r.last_x) + "</em>" : x(r.exit_x) + `<small>${esc(((r.exits.slice(-1)[0] || {}).why || "").split(" ")[0])}</small>`}</span>
          <span>${x(r.peak_x)}</span>
          <span>${r.after.h24 ? x(r.after.h24.peak_x) : '<small>pending</small>'}${r.label ? `<small>${esc(r.label)}</small>` : ""}</span>
          <span class="${r.realized_sol >= 0 ? "up" : "dn"}">${r.status === "closed" ? (r.realized_sol >= 0 ? "+" : "") + r.realized_sol : "—"}<small>${r.roi_pct != null && r.status === "closed" ? r.roi_pct + "%" : ""}</small></span>
          <span class="hs-tags">${r.tags.map(t => t.startsWith("yours:") ? `<i class="yours">${esc(t.slice(6))}</i>` : `<i style="--t:${TAGC[t]}">${TAGN[t] || t}</i>`).join("")}</span></div>
          ${H.open === r.id ? detail(r, d) : ""}`).join("") || '<div class="dk-empty" style="padding:14px">no trades here yet</div>'}</div>`;
    document.querySelectorAll("#dk-hist [data-hf]").forEach(b => b.onclick = () => { H.filter = b.dataset.hf; loadHistory(); });
    document.querySelectorAll("#dk-hist .hs-r").forEach(row => row.onclick = () => { H.open = H.open === +row.dataset.id ? null : +row.dataset.id; renderHistory(); });
    document.querySelectorAll("#dk-hist [data-coin]").forEach(b => b.onclick = (e) => { e.stopPropagation(); openPop(b.dataset.coin, b); });
    const sv = $("#hs-save"); if (sv) sv.onclick = async (e) => { e.stopPropagation(); const r = await post(`/api/sniper/history/${H.open}`, { note: $("#hs-note").value, tag: $("#hs-tag").value }); if (r) loadHistory(); };
    $("#hs-csv").onclick = () => {
      const head = ["id", "symbol", "mint", "opened", "entry_mcap", "size_sol", "conf", "exit_x", "peak_x", "after_24h_peak_x", "realized_sol", "roi_pct", "exit_reason", "mistakes", "note"];
      const rows = d.rows.map(r => [r.id, r.symbol, r.mint, new Date(r.opened_ts * 1000).toISOString(), Math.round(r.entry_mcap), r.size_sol, r.conf, r.exit_x, r.peak_x,
        r.after.h24 ? r.after.h24.peak_x : "", r.realized_sol, r.roi_pct, ((r.exits.slice(-1)[0] || {}).why || ""), r.tags.join("|"), (r.note || "").replace(/\n/g, " ")]);
      const csv = [head, ...rows].map(a => a.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "sniper-history.csv"; a.click();
    };
  }
  function detail(r, d) {
    const keys = Object.keys(r.evidence);
    return `<div class="hs-d">
      <div><div class="ln-k">WHY IT BOUGHT · evidence at entry (conf ${Number(r.conf).toFixed(2)} vs gate ${Number(d.gate).toFixed(2)})</div>
        ${keys.map(k => { const v = r.evidence[k]; return `<div class="hs-ev"><span>${k}</span><div>${v == null ? '<s>not measured</s>' : `<i style="width:${v * 100}%"></i>`}</div><b>${v == null ? "—" : Number(v).toFixed(2)}</b></div>`; }).join("")}
        <div class="ln-t" style="margin-top:6px">${esc(r.reason || "")}</div></div>
      <div><div class="ln-k">HOW IT EXITED</div>
        ${r.exits.map(e => `<div class="hs-ex"><span>${new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span><b>${e.x}x</b><em>${esc(e.why)}</em><i class="${e.pnl >= 0 ? "up" : "dn"}">${e.pnl >= 0 ? "+" : ""}${e.pnl}</i></div>`).join("") || '<div class="ln-t">still open</div>'}
        <div class="ln-k">WHAT THE COIN DID AFTER (vs our entry)</div>
        ${["h1", "h24", "d7"].map(k => `<div class="hs-ex"><span>${k}</span>${r.after[k] ? `<b>${r.after[k].x}x now</b><em>peak ${r.after[k].peak_x}x</em>` : "<em>pending</em>"}</div>`).join("")}
        ${r.tags.filter(t => !t.startsWith("yours:")).map(t => `<div class="hs-why" style="--t:${TAGC[t]}">${esc(d.mistake_labels[t] || t)}</div>`).join("")}</div>
      <div><div class="ln-k">YOUR NOTES</div>
        <textarea id="hs-note" rows="4" placeholder="what would you have done differently?" onclick="event.stopPropagation()">${esc(r.note || "")}</textarea>
        <input id="hs-tag" placeholder="your tag (e.g. fomo, chased, good read)" value="${esc((r.tags.find(t => t.startsWith("yours:")) || "").slice(6))}" onclick="event.stopPropagation()">
        <div class="lb-act"><button class="dk-btn pri" id="hs-save">SAVE NOTE</button><button class="dk-btn" data-coin="${esc(r.mint)}">OPEN COIN</button></div></div></div>`;
  }
  setInterval(() => { if (D.on && H.view === "hist" && !document.activeElement.closest?.(".hs-d")) loadHistory(); }, 20000);

  // ALWAYS LEARNING: versions, weights + why, outcomes, lists, alerts, change log (/api/learning)
  async function pollLearn() {
    let L; try { L = await j("/api/learning"); } catch { return; }
    D.learn = L;
    const lv = L.live, sh = L.shadow, w = lv.weights, mx = Math.max(...Object.values(w), 0.01);
    const imp = Object.fromEntries(((lv.metrics || {}).importance || (sh && sh.metrics.importance) || []).map(i => [i.feature, i]));
    $("#dk-lv").textContent = `v${lv.version} live` + (sh ? ` · v${sh.version} shadow` : "");
    const lab = L.outcomes.labels || {}, col = { ran: "#7ee2a8", rugged: "#ff6b6b", fake: "#e85cf0", died: "#8a8a8a", flat: "#5e5e5e" };
    const st = (sh && sh.metrics.shadow_test) || null;
    $("#dk-learn").innerHTML = `
      ${(L.alerts || []).slice(-2).reverse().map(a => `<div class="ln-alert">⚠ ${esc(a.msg)}</div>`).join("")}
      <div class="ln-k">WHAT IT WEIGHTS MOST · and why (runner rate when the evidence is high vs low)</div>
      ${Object.entries(w).sort((a, b) => b[1] - a[1]).map(([k, v]) => { const i = imp[k];
        return `<div class="ln-w"><span>${k}</span><div><i style="width:${v / mx * 100}%"></i></div><b>${v.toFixed(2)}</b>
          <em>${i && i.high_rate != null && i.low_rate != null ? `${Math.round(i.high_rate * 100)}% vs ${Math.round(i.low_rate * 100)}%` : ""}</em></div>`; }).join("")}
      <div class="ln-k">OUTCOMES · ${L.outcomes.total.toLocaleString()} coins tracked at 1h / 24h / 7d</div>
      <div class="ln-lab">${Object.entries(lab).map(([k, v]) => `<span style="--c:${col[k] || "#8a8a8a"}"><i></i>${k} <b>${v}</b></span>`).join("") || '<span>resolving…</span>'}</div>
      ${sh ? `<div class="ln-k">SHADOW TEST · v${sh.version} vs v${lv.version}</div><div class="ln-t">${st ? `AUC ${st.auc_shadow} vs ${st.auc_live} · hit rate ${st.hit_shadow} vs ${st.hit_live} (${st.n} coins)`
        : `trained on ${sh.metrics.trained_on} outcomes · scoring alongside live, promoted only if it does better`}</div>` : ""}
      <div class="ln-k">LISTS</div><div class="ln-t">${L.smart.filter(x => x.status === "active").length} smart wallets / devs · ${L.blacklist.length} blacklisted</div>
      ${(L.missed || []).length ? `<div class="ln-k">MISSED RUNNERS · a signal that would have caught them</div>` + L.missed.slice(0, 2).map(m => `<div class="ln-t">${esc(m.rule)} → caught ${m.caught}/${m.of_missed} at ${Math.round(m.precision * 100)}% precision</div>`).join("") : ""}
      <div class="ln-k">CHANGE LOG</div>${L.changes.slice(0, 5).map(c => `<div class="ln-c"><span>${ago(c.ts)}</span><b>${esc(c.kind)}</b><em>${esc(c.summary)}</em></div>`).join("") || '<div class="ln-t">no automatic changes yet</div>'}
      <div class="ln-lock">🔒 never changed by the bot: ${L.risk_limits_locked.join(" · ")}<br>⏻ kill switch: ${esc(L.kill_switch)}</div>
      <div class="lb-act"><button class="dk-btn" id="ln-retrain">↻ RETRAIN NOW</button><button class="dk-btn" id="ln-rollback">⟲ ROLL BACK</button><button class="dk-btn pri" id="ln-recap">WEEKLY RECAP</button></div>`;
    $("#ln-retrain").onclick = async () => { const r = await post("/api/learning/retrain"); if (r) { alert(r.skipped || `v${r.version} trained, now running in shadow`); pollLearn(); } };
    $("#ln-rollback").onclick = async () => { if (!confirm("Roll back the scorer to the previous good version?")) return; const r = await post("/api/learning/rollback"); if (r) { alert(r.skipped || `rolled back v${r.from} → v${r.to}`); pollLearn(); } };
    $("#ln-recap").onclick = openRecap;
  }
  async function openRecap() {
    $("#dk-recap-text").textContent = "loading…"; $("#dk-recap").showModal();
    let r = null; try { r = (await j("/api/recap")); } catch { /* none yet */ }
    $("#dk-recap-text").textContent = r && r.text ? r.text : "No recap yet: the first one is written after a week of running (or press WRITE A NEW ONE NOW).";
    $("#dk-recap-new").onclick = async (e) => { e.preventDefault(); $("#dk-recap-text").textContent = "writing…"; const n = await post("/api/learning/recap"); if (n) $("#dk-recap-text").textContent = n.text; };
  }
  setInterval(() => { if (D.on) pollLearn(); }, 15000);

  // SPEED: the measured path a coin takes, and the slowest steps (/api/perf)
  const fmtS = (x) => x == null ? "—" : x < 1 ? Math.round(x * 1000) + " ms" : x < 120 ? x.toFixed(1) + " s" : (x / 60).toFixed(1) + " min";
  const STAGE_NAME = { "launch→ws": "launch → feed", "ws→db": "feed → database", "db→scored": "database → scored", "scored→gated": "scored → decision",
    "gated→filled": "decision → paper fill", "launch→gated": "LAUNCH → DECISION", "launch→filled": "LAUNCH → FILL" };
  async function pollPerf() {
    let p; try { p = await j("/api/perf"); } catch { return; }
    const rows = p.path.filter(r => r.n), mx = Math.max(0.001, ...rows.map(r => r.p95 || 0));
    const slow = (p.timers.sniper || []).concat(p.timers.scan || []).slice(0, 6);
    $("#dk-spd").textContent = p.feed && p.feed.connected ? "feed live" : p.feed && p.feed.since ? "feed reconnecting" : "feed off (demo)";
    $("#dk-speed").innerHTML = (rows.map(r => `<div class="sp-r ${r.stage.startsWith("launch→g") || r.stage.startsWith("launch→f") ? "tot" : ""}"><span>${STAGE_NAME[r.stage] || r.stage}</span>
        <div><i style="width:${Math.max(2, r.p50 / mx * 100)}%"></i><s style="left:${Math.min(100, r.p95 / mx * 100)}%"></s></div><b>${fmtS(r.p50)}</b><em>p95 ${fmtS(r.p95)}</em></div>`).join("")
      || '<div class="dk-empty">collecting… every coin is timed from launch to decision</div>') +
      (slow.length ? `<div class="sp-k">SLOWEST STEPS (p50)</div>` + slow.map(t => `<div class="sp-t"><span>${esc(t.name.split(":")[1] || t.name)}</span><b>${fmtS(t.p50)}</b></div>`).join("") : "") +
      `<div class="sp-k">RPC · auto failover</div>` + (p.rpc || []).map(r => `<div class="sp-t"><span>${esc(r.name)}${r.benched ? ` <i style="color:#ff6b6b;font-style:normal">benched ${r.bench_s}s</i>` : ""}</span><b>${r.calls ? r.ms + " ms" : "idle"}</b></div>`).join("") +
      `<div class="sp-k">FAST GATE · in memory</div><div class="sp-t"><span>${p.fastgate.devs.toLocaleString()} devs · ${p.fastgate.blacklisted} blacklisted · ${p.fastgate.kol_wallets} KOL wallets</span><b>&lt;1 µs</b></div>` +
      (p.feed && p.feed.trades != null ? `<div class="sp-t"><span>live trade stream · ${p.feed.watching || 0} coins watched</span><b>${(p.feed.trades || 0).toLocaleString()} trades</b></div>` : "");
  }
  setInterval(() => { if (D.on) pollPerf(); }, 4000);

  async function poll(force) {
    if (!D.on && !force) return;
    try {
      const d = await j("/api/sniper?after=" + D.after);
      D.data = d;
      if (d.log.length) { D.log = D.log.concat(d.log).slice(-400); D.after = d.log[d.log.length - 1].id; }
      const fresh = d.log;
      render();
      schedule(fresh);
    } catch { /* backend busy: keep the last frame */ }
  }

  // ---- SIGNAL FIELD feed: real decisions -> pulses, real candidates/positions -> sources + floor ----------
  function eventRate() {
    const now = Date.now() / 1000;
    return D.log.filter(e => now - e.ts < 60).length;  // decisions in the last minute
  }
  function feedField(d) {
    if (!D.field) return;
    const seen = new Set(), src = [];
    for (const p of d.positions) if (!seen.has(p.symbol)) { seen.add(p.symbol); src.push({ id: p.symbol, label: "$" + (p.symbol || "?"), sub: (p.last_x || 1).toFixed(2) + "x", lit: true }); }
    for (const c of d.candidates) if (!seen.has(c.symbol) && src.length < 11) { seen.add(c.symbol); src.push({ id: c.symbol, label: "$" + (c.symbol || "?"), sub: usd(c.mcap) }); }
    D.sym = seen;
    D.field.layout(src, [...document.querySelectorAll("#dk-ops .dk-op")]);
    const floor = d.candidates.map((c, i) => ({ u: 0.08 + 0.84 * (i / Math.max(1, d.candidates.length - 1)), v: 0.3 + (i % 3) * 0.12, h: c.conf }))
      .concat(d.positions.map((p, i) => ({ u: 0.15 + 0.7 * (i / Math.max(1, d.positions.length - 1 || 1)), v: 0.72, h: 0.55 + Math.min(0.6, Math.abs((p.last_x || 1) - 1)),
        color: (p.last_x || 1) >= 1 ? "#7ee2a8" : "#ff6b6b", label: `$${p.symbol} ${(p.last_x || 1).toFixed(2)}x` })));
    const fsig = JSON.stringify(floor.map(f => [f.u.toFixed(2), f.h.toFixed(2), f.color]));
    if (fsig !== D.fsig) { D.fsig = fsig; D.field.setFloor(floor); }
    const rate = eventRate();
    D.field.setRate(rate);
    // ticker: the real decisions, scrolling at the bot's real pace
    const dec = D.log.filter(e => ["ANALYST", "RISK", "EXECUTOR", "HUMAN"].includes(e.op)).slice(-28);
    const txt = dec.map(e => { const m = e.msg.match(/\$([A-Za-z0-9]+)/), v = e.msg.match(/(\d\.\d\d)/);
      return `<span><b>${v ? v[1] : "·"}</b> → ${e.op.toLowerCase()}.${(KIND[e.kind] || e.kind).toLowerCase()}(${m ? "$" + esc(m[1]) : ""})</span>`; }).join("");
    if (txt !== D.tick) { D.tick = txt; $("#dk-ticker").innerHTML = txt + txt; }
    $("#dk-ticker").style.animationDuration = Math.max(18, 140 / Math.sqrt(rate + 1)) + "s";
  }
  const PULSE = (e) => {
    const m = e.msg.match(/\$([A-Za-z0-9]+)/), sym = m && D.sym && D.sym.has(m[1]) ? m[1] : null, v = (e.msg.match(/(\d\.\d\d)/) || [])[1];
    if (e.op === "WATCHER") return [null, 0, "#d9d9d9", null];
    if (!sym) return null;
    if (e.op === "ANALYST") return [sym, 1, "#f2e14c", v ? "score " + v : null];
    if (e.op === "RISK") return e.kind === "pass" ? [sym, 2, "#f2e14c", "PASS " + (v || "")] : e.kind === "warn" ? [sym, 3, "#ff6b6b", e.msg.split(" ")[0]] : [sym, 2, "#e85cf0", "HOLD " + (v || "")];
    if (e.op === "EXECUTOR") return [sym, 3, e.msg.startsWith("SELL") ? "#7ee2a8" : "#ffffff", e.msg.split(" ").slice(0, 2).join(" ")];
    if (e.op === "REPORTER") return [sym, 4, "#a78bfa", null];
    if (e.op === "HUMAN") return [sym, 5, "#7dd3fc", "HANDOFF"];
    return null;
  };
  function schedule(events) {  // replay new events with their real spacing
    if (!D.field || !events.length) return;
    const t0 = events[0].ts;
    for (const e of events) {
      const p = PULSE(e); if (!p) continue;
      setTimeout(() => D.on && D.field.pulse(...p), Math.min(2400, Math.max(0, (e.ts - t0) * 1000)));
    }
  }

  // ---- view switch --------------------------------------------------------------------------
  function show(on) {
    D.on = on;
    if (D.field) D.field.active = on;
    if (on) { mount(); D.field.active = true; poll(true); pollPerf(); pollLearn(); clearInterval(D.timer); D.timer = setInterval(poll, 2000); } else clearInterval(D.timer);
  }
  setInterval(() => { if (D.on) $("#dk-clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
  // one switch for the three views: SCOPE (dashboard) | SNIPER DESK | DEV LAB
  window.VIEWS = { desk: show };
  window.setView = (v) => {
    const views = ["scope", "desk", "lab"]; if (!views.includes(v)) v = "scope";
    document.querySelector("main.grid").hidden = v !== "scope"; $(".tfbar").hidden = v !== "scope";
    $("#desk").hidden = v !== "desk"; $("#lab").hidden = v !== "lab";
    document.querySelectorAll("#viewsw button").forEach(b => b.setAttribute("aria-selected", b.dataset.v === v));
    document.body.classList.toggle("desk-on", v !== "scope");
    try { localStorage.setItem("ts_view", v); } catch { /* private mode */ }
    Object.entries(window.VIEWS).forEach(([k, fn]) => fn(k === v));
  };
  document.querySelectorAll("#viewsw button").forEach(b => b.onclick = () => window.setView(b.dataset.v));
})();
