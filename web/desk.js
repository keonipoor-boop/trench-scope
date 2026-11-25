/* SNIPER DESK: the paper-trading sniper's own view (core/sniper.py, /api/sniper).
   Operators strip, SIGNAL CORE vortex (canvas), evidence matrix, handoffs, risk gauge, live log, DryFlip handoff queue.
   Uses app.js helpers: $, esc, usd, ago, coinImg, openPop, adminPw. */
(() => {
  const D = { on: false, data: null, after: 0, log: [], logTab: "all", codeTab: "risk_gate.py", timer: null };
  const OP_COL = { WATCHER: "#ff6fb5", ANALYST: "#ff4fa3", RISK: "#ff3df2", EXECUTOR: "#ffa0d2", REPORTER: "#c9a7ff", HUMAN: "#ffe0f0" };
  const KIND = { fill: "FILL", pass: "PASS", hold: "HOLD", handoff: "HANDOFF", warn: "ALERT", active: "ACTIVE", info: "SYNC", paused: "PAUSED", idle: "IDLE" };
  const f2 = (v) => v == null ? "—" : Number(v).toFixed(2);
  const sol = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(3);

  function spark(vals, w = 120, h = 26, col = "#ff4fa3") {
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
        <div class="dk-title"><b>ORBITAL SIGNAL ENGINE</b><span id="dk-sub">paper trading · no wallet · no real orders</span></div>
        <div class="dk-ctl">
          <div class="dk-seg" id="dk-mode" role="tablist" title="AUTO: the bot paper-fills its own trades. DRYFLIP: every trade becomes a handoff you mirror in the DryFlip extension.">
            <button data-mode="auto">AUTO PAPER</button><button data-mode="dryflip">DRYFLIP HANDOFF</button></div>
          <button class="dk-btn" id="dk-run">PAUSE</button><button class="dk-btn" id="dk-cfg">⚙ RULES</button>
          <span class="dk-live"><i></i>LIVE</span><span class="dk-clock" id="dk-clock"></span>
        </div>
      </header>
      <section class="dk-ops" id="dk-ops"></section>
      <section class="dk-main">
        <div class="dk-col">
          <div class="dk-card"><div class="dk-h">WATCHLIST <i>fresh launches through your filters</i><em id="dk-wn"></em></div><div class="dk-fchips" id="dk-fchips"></div><div id="dk-watch"></div></div>
          <div class="dk-card"><div class="dk-h">EQUITY <i>paper book, SOL</i><em id="dk-eq"></em></div><div id="dk-equity" class="dk-equity"></div></div>
          <div class="dk-card"><div class="dk-h">OPEN POSITIONS <em id="dk-on"></em></div><div id="dk-pos"></div></div>
        </div>
        <div class="dk-core">
          <canvas id="dk-canvas"></canvas>
          <div class="dk-call c1" id="dk-choice"></div><div class="dk-call c2" id="dk-risk"></div><div class="dk-call c3" id="dk-gate"></div>
          <div class="dk-corelbl">SIGNAL CORE</div>
          <div class="dk-kpis" id="dk-kpis"></div>
        </div>
        <div class="dk-col">
          <div class="dk-card"><div class="dk-h">EVIDENCE MATRIX <i>top candidates × evidence</i></div><div id="dk-matrix"></div></div>
          <div class="dk-card"><div class="dk-h">HANDOFFS <i>operator activity</i></div><div id="dk-hand"></div></div>
          <div class="dk-card dk-ready"><div class="dk-h">LIVE READINESS <i>is the paper record good enough to risk money?</i></div><div id="dk-ready"></div></div>
          <div class="dk-card"><div class="dk-h">RISK GAUGE <i>exposure of equity</i></div><div id="dk-gauge"></div></div>
          <div class="dk-card dk-dry" id="dk-drycard"><div class="dk-h">DRYFLIP HANDOFF QUEUE <em id="dk-dn"></em></div><div id="dk-dry"></div></div>
        </div>
      </section>
      <section class="dk-bottom">
        <div class="dk-card dk-code"><div class="dk-tabs" id="dk-codetabs"></div><pre id="dk-code"></pre></div>
        <div class="dk-card dk-logc"><div class="dk-h">LIVE LOG <span class="dk-tabs small" id="dk-logtabs"></span></div><div id="dk-log" class="dk-log"></div></div>
      </section>
      <footer class="dk-foot" id="dk-foot"></footer>
      <dialog id="dk-rules"><form method="dialog" id="dk-rules-form"><h3>◆ SNIPER RULES</h3><div class="dk-rgrid" id="dk-rgrid"></div>
        <label class="set-key">AXIOM FILTER PRESET <i class="dim" style="font-style:normal">paste the JSON Axiom exports (New Pairs) · leave as is to keep the current one · type <b>clear</b> to remove</i>
          <textarea name="axiom" rows="5" placeholder='{"filters": {"protocols": {...}, "age": {"max": 4, ...}}, "tab": "newPairs"}'></textarea></label>
        <div class="dk-fsum" id="dk-fsum"></div>
        <p class="dim small">Paper trading only. The bot never touches a wallet. Fills use the live price plus your slippage, the terminal fee and a priority fee (like DryFlip models them).</p>
        <p class="small" id="dk-rmsg"></p>
        <div class="set-actions"><button value="cancel" formnovalidate>CLOSE</button><button id="dk-reset" value="reset" formnovalidate>RESET BOOK</button><button value="default">SAVE</button></div></form></dialog>`;
    document.querySelectorAll("#dk-mode button").forEach(b => b.onclick = () => save({ mode: b.dataset.mode }));
    $("#dk-run").onclick = () => save({ enabled: !(D.data && D.data.settings.enabled) });
    $("#dk-cfg").onclick = openRules;
    $("#dk-rules-form").addEventListener("submit", onRules);
    $("#dk-logtabs").innerHTML = ["all", "trades", "signals", "handoffs"].map(t => `<button data-t="${t}">${t.toUpperCase()}</button>`).join("");
    $("#dk-logtabs").querySelectorAll("button").forEach(b => b.onclick = () => { D.logTab = b.dataset.t; renderLog(); });
    $("#dk-codetabs").innerHTML = ["sniper_desk.py", "evidence.py", "risk_gate.py"].map(t => `<button data-t="${t}">${t}</button>`).join("") + '<span class="dk-live sm"><i></i>LIVE</span>';
    $("#dk-codetabs").querySelectorAll("button").forEach(b => b.onclick = () => { D.codeTab = b.dataset.t; renderCode(); });
    Core.init($("#dk-canvas"));
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
    $("#dk-ops").innerHTML = Object.entries(d.operators).map(([n, o], i) => `<div class="dk-op ${o.status === "hold" || o.status === "handoff" ? "hot" : ""}" style="--oc:${OP_COL[n]}">
      <div class="dk-op-h"><span>0${i + 1}</span><b>${n}</b><em class="k-${o.status}">${KIND[o.status] || o.status.toUpperCase()}</em></div>
      ${spark(o.spark, 140, 26, OP_COL[n])}<div class="dk-op-v">${o.value == null ? "—" : typeof o.value === "number" && o.value <= 1.5 ? f2(o.value) : o.value}</div></div>`).join("");
    // watchlist
    $("#dk-wn").textContent = d.candidates.length;
    const fs = d.filters || { active: [], ignored: [] };
    $("#dk-fchips").innerHTML = fs.active.length ? fs.active.slice(0, 4).map(a => `<span>${esc(a.length > 34 ? a.slice(0, 33) + "…" : a)}</span>`).join("") +
      (fs.active.length > 4 ? `<span>+${fs.active.length - 4}</span>` : "") + (fs.ignored.length ? `<span class="ig" title="${esc(fs.ignored.join(", "))}">${fs.ignored.length} not measured</span>` : "")
      : '<span class="ig">no Axiom preset · ⚙ RULES to paste one</span>';
    $("#dk-watch").innerHTML = d.candidates.map(c => `<div class="dk-row" data-mint="${esc(c.mint)}">${coinImg(c.image, c.symbol, false, c.mint)}
      <div class="dk-rn"><b>$${esc(c.symbol || "?")}</b><span>${usd(c.mcap)} · ${c.age_min}m</span></div>
      <div class="dk-bar"><i style="width:${Math.round(c.conf * 100)}%;background:${c.conf >= s.gate ? "#ff4fa3" : "#6b3a5c"}"></i></div><em>${f2(c.conf)}</em></div>`).join("") || '<div class="dk-empty">watching for fresh launches…</div>';
    // equity
    const eq = [s.start_balance_sol, ...d.pnl_series.map(p => s.start_balance_sol + p[1]), k.equity_sol];
    $("#dk-eq").innerHTML = `<b style="color:${k.pnl_sol >= 0 ? "#7ee2a8" : "#ff8fb1"}">${sol(k.pnl_sol)} SOL</b>`;
    $("#dk-equity").innerHTML = spark(eq, 300, 70, "#ff4fa3") + `<div class="dk-eqv"><span>${f2(k.equity_sol)} SOL equity</span><span>${f2(k.balance_sol)} cash</span></div>`;
    // positions
    $("#dk-on").textContent = `${d.positions.length}/${s.max_open}`;
    $("#dk-pos").innerHTML = d.positions.map(p => `<div class="dk-row" data-mint="${esc(p.mint)}">${coinImg(p.image, p.symbol, false, p.mint)}
      <div class="dk-rn"><b>$${esc(p.symbol || "?")}${p.mode === "dryflip" ? ' <i class="dk-tag">DRYFLIP</i>' : ""}</b><span>${p.size_sol} SOL · in ${usd(p.entry_mcap)} · ${ago(p.opened_ts)}</span></div>
      <em class="${(p.last_x || 1) >= 1 ? "up" : "dn"}">${f2(p.last_x)}x</em></div>`).join("") || '<div class="dk-empty">no open positions</div>';
    // callouts
    const fc = d.focus;
    $("#dk-choice").innerHTML = fc ? `<b>CHOICE / ${fc.conf >= s.gate ? "LONG" : "WATCH"}</b><span>$${esc(fc.symbol || "?")} · conf ${f2(fc.conf)}</span>` : "<b>CHOICE / —</b><span>scanning</span>";
    const lastRisk = [...D.log].reverse().find(e => e.op === "RISK");
    $("#dk-risk").innerHTML = `<b>RISK ${lastRisk ? (KIND[lastRisk.kind] || "") : "IDLE"}</b><span>${lastRisk ? esc(lastRisk.msg.slice(0, 46)) : "—"}</span>`;
    $("#dk-gate").innerHTML = `<b>GATE / ${fc && fc.conf >= s.gate ? "PASS" : "HOLD"}</b><span>${fc ? f2(fc.conf) : "—"} ${fc && fc.conf >= s.gate ? "≥" : "<"} ${f2(s.gate)} · ${s.mode === "dryflip" ? "human" : "auto"}</span>`;
    // KPIs under the core
    const conf = d.operators.ANALYST.spark, risk = d.operators.RISK.spark;
    $("#dk-kpis").innerHTML = [["CONF", fc ? f2(fc.conf) : "—", conf], ["GATE", f2(s.gate), risk], ["EQUITY", f2(k.equity_sol), eq], ["EXPOSURE", k.exposure_pct + "%", null]]
      .map(([l, v, sp]) => `<div class="dk-kpi"><span>${l}</span><b>${v}</b>${spark(sp || [], 90, 22)}</div>`).join("");
    // evidence matrix: rows = top candidates, cols = evidence
    const keys = d.evidence_keys;
    $("#dk-matrix").innerHTML = `<div class="dk-mx" style="--n:${keys.length}"><span></span>${keys.map(x => `<span class="mxk" title="${x}">${x.slice(0, 3)}</span>`).join("")}
      ${d.candidates.slice(0, 6).map(c => `<span class="mxr">$${esc((c.symbol || "?").slice(0, 6))}</span>` + keys.map(x => {
        const v = c.ev[x]; if (v == null) return `<i class="na" title="${x}: not measured yet"></i>`;
        return `<i title="${x} ${f2(v)}" style="background:rgba(255,${Math.round(79 + 140 * (1 - v))},${Math.round(163 + 60 * v)},${(0.12 + v * 0.88).toFixed(2)})"></i>`; }).join("")).join("")}</div>`
      || '<div class="dk-empty">no candidates</div>';
    // handoffs: operator activity in the log window
    const cnt = {}; D.log.slice(-200).forEach(e => cnt[e.op] = (cnt[e.op] || 0) + 1);
    const mx = Math.max(1, ...Object.values(cnt));
    $("#dk-hand").innerHTML = Object.keys(d.operators).map(n => `<div class="dk-hb"><span>${n}</span><div><i style="width:${(cnt[n] || 0) / mx * 100}%;background:${OP_COL[n]}"></i></div></div>`).join("");
    // risk gauge
    const ex = Math.min(100, k.exposure_pct || 0), R = 34, C = 2 * Math.PI * R;
    $("#dk-gauge").innerHTML = `<div class="dk-gw"><svg viewBox="0 0 90 90"><circle cx="45" cy="45" r="${R}" stroke="#2a1426" stroke-width="9" fill="none"/>
      <circle cx="45" cy="45" r="${R}" stroke="url(#dkg)" stroke-width="9" fill="none" stroke-linecap="round" stroke-dasharray="${(ex / 100 * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 45 45)"/>
      <defs><linearGradient id="dkg"><stop offset="0" stop-color="#ff4fa3"/><stop offset="1" stop-color="#ffe0f0"/></linearGradient></defs>
      <text x="45" y="50" text-anchor="middle" fill="#f3e6f0" font-size="15" font-family="JetBrains Mono" font-weight="700">${Math.round(ex)}%</text></svg>
      <div class="dk-gs"><div><span>DD</span><b>${k.pnl_sol < 0 ? (k.pnl_sol / s.start_balance_sol * 100).toFixed(1) + "%" : "0%"}</b></div><div><span>OPEN</span><b>${k.open}</b></div>
      <div><span>SL</span><b>-${s.stop_loss_pct}%</b></div><div><span>TP</span><b>${s.tp1_x}x</b></div></div></div>`;
    // live readiness 0.0-100
    const rd = d.readiness, sc = rd.score, rc = sc < 40 ? "#ff8fb1" : sc < 70 ? "#ffcf8a" : "#7ee2a8";
    $("#dk-ready").innerHTML = `<div class="dk-rm"><b style="color:${rc}">${sc.toFixed(1)}</b><span>/ 100</span><em style="color:${rc}">${esc(rd.verdict.toUpperCase())}</em></div>
      <div class="dk-rbar"><i style="width:${sc}%;background:linear-gradient(90deg,#ff4fa3,${rc})"></i><s style="left:70%"></s></div>
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
    Core.setEnergy(Math.min(1, 0.35 + d.candidates.length / 16 + (d.positions.length / Math.max(1, s.max_open)) * 0.4), d.focus ? d.focus.conf : 0.5, s.gate);
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

  async function poll(force) {
    if (!D.on && !force) return;
    try {
      const d = await j("/api/sniper?after=" + D.after);
      D.data = d;
      if (d.log.length) { D.log = D.log.concat(d.log).slice(-400); D.after = d.log[d.log.length - 1].id; }
      render();
    } catch { /* backend busy: keep the last frame */ }
  }

  // ---- SIGNAL CORE: a tilted particle vortex -----------------------------------------------
  const Core = {
    init(cv) {
      this.cv = cv; this.ctx = cv.getContext("2d"); this.energy = 0.5; this.conf = 0.5; this.gate = 0.72;
      this.P = Array.from({ length: 1400 }, () => this.spawn(true));
      const loop = (t) => { if (D.on) this.frame(t); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    },
    setEnergy(e, conf, gate) { this.energy = e; this.conf = conf; this.gate = gate; },
    spawn(init) {
      const r = init ? Math.random() : 0.85 + Math.random() * 0.25;
      return { a: Math.random() * Math.PI * 2, r, v: 0.004 + Math.random() * 0.01, s: Math.random() < 0.08 ? 2.2 : 1, h: Math.random() };
    },
    frame(t) {
      const cv = this.cv, dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      if (!W || !H) return;
      if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
      const c = this.ctx; c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.fillStyle = "rgba(10,4,12,0.28)"; c.fillRect(0, 0, W, H);
      const cx = W / 2, cy = H * 0.46, RX = W * 0.46, RY = H * 0.3, e = this.energy;
      c.globalCompositeOperation = "lighter";
      for (const p of this.P) {
        p.a += p.v * (1.4 - p.r) * (0.6 + e);
        p.r -= 0.0009 * (0.5 + e);                       // spiral in toward the core
        if (p.r < 0.06) Object.assign(p, this.spawn(false));
        const x = cx + Math.cos(p.a) * RX * p.r, y = cy + Math.sin(p.a) * RY * p.r;
        const funnel = p.r < 0.35 ? (0.35 - p.r) * H * 0.55 : 0;  // the vortex dips into a funnel at the centre
        const hot = p.h > 0.85 ? 255 : 160 + p.h * 80;
        c.fillStyle = `rgba(255,${Math.round(hot * 0.45)},${Math.round(150 + p.h * 90)},${0.35 + (1 - p.r) * 0.5})`;
        c.fillRect(x, y + funnel, p.s, p.s);
      }
      // the inner ring and the core glow
      const g = c.createRadialGradient(cx, cy + H * 0.12, 4, cx, cy + H * 0.12, RX * 0.4);
      g.addColorStop(0, `rgba(255,220,240,${0.35 + e * 0.3})`); g.addColorStop(0.4, "rgba(255,79,163,0.18)"); g.addColorStop(1, "rgba(255,79,163,0)");
      c.fillStyle = g; c.beginPath(); c.ellipse(cx, cy + H * 0.12, RX * 0.4, RY * 0.4, 0, 0, Math.PI * 2); c.fill();
      c.strokeStyle = `rgba(255,140,200,${0.25 + 0.2 * Math.sin(t / 600)})`; c.lineWidth = 1.2;
      c.beginPath(); c.ellipse(cx, cy + H * 0.12, RX * 0.34, RY * 0.22, 0, 0, Math.PI * 2); c.stroke();
      c.globalCompositeOperation = "source-over";
      // tethers from the core to the callouts
      const pass = this.conf >= this.gate;
      [[0.2, 0.33], [0.8, 0.16], [0.84, 0.74]].forEach(([fx, fy], i) => {
        c.strokeStyle = i === 2 && pass ? "rgba(126,226,168,0.55)" : "rgba(255,190,225,0.35)"; c.lineWidth = 1;
        c.beginPath(); c.moveTo(cx, cy + H * 0.1); c.lineTo(W * fx, H * fy); c.stroke();
      });
    },
  };

  // ---- view switch --------------------------------------------------------------------------
  function show(on) {
    D.on = on;
    if (on) { mount(); poll(true); clearInterval(D.timer); D.timer = setInterval(poll, 2000); } else clearInterval(D.timer);
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
