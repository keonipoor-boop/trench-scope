/* Trench Scope dashboard: polls /api/state every 15s and /api/events every 3s. */
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (v) => !v ? "$0" : v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? "$" + (v / 1e3).toFixed(0) + "k" : "$" + Math.round(v);
const mins = (m) => m == null ? "—" : m >= 60 ? (m / 60).toFixed(1) + "h" : Math.round(m) + "m";
const ago = (ts) => { const s = Date.now() / 1000 - ts; return s < 60 ? Math.round(s) + "s" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? (s / 3600).toFixed(1) + "h" : (s / 86400).toFixed(1) + "d"; };
const CAT = { celebrity_tweet: "CELEB TWEET", tweet_play: "TWEET PLAY", community: "COMMUNITY", cto: "CTO", tech: "TECH/UTILITY",
  kol_call: "KOL CALL", livestream: "LIVESTREAM", news: "NEWS/EVENT", meme: "PURE MEME" };
const AGENT_INFO = { SCOUT: "discovery", HOLDERS: "holders", KOLS: "kol wallets", FEES: "fees/traders", CATALYST: "why it ran", THESIS: "narrative" };
const AC = window.Synapse.AGENT_COLORS;
let STATE = null, lastEvent = 0, selected = null, seenTape = new Set();

async function j(url, opt) { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status); return r.json(); }

async function refresh() {
  try { STATE = await j("/api/state"); } catch (e) { $("#livewire").textContent = "backend unreachable: " + e; return; }
  renderTicker(); renderBoard(); renderTape(); renderSynapse(); renderAgents(); renderRotation(); renderHourly();
  $("#foot").textContent = `${STATE.mode.toUpperCase()} MODE${$("#foot").dataset.v ? " · v" + $("#foot").dataset.v : ""} · last scan ${STATE.last_scan ? ago(STATE.last_scan.ts) + " ago" : "never"}`;
  if (!selected && STATE.tape.length) openCoin(STATE.tape[0].mint);
}

function renderTicker() {
  const s = STATE.summary, d = STATE.daily, h = STATE.hourly, th = STATE.themes.daily;
  const shift = h && h.meta_shift;
  const items = [`<span class="live">◉ SIX AGENTS LIVE</span>`,
    `<span>RUNNERS 7D <b>${s.runners}</b></span>`, `<span>REAL <b class="g">${s.real_pct}%</b></span>`,
    `<span>HIT RATE <b>${s.hit_rate}%</b></span>`, `<span>24H LEADER <b class="g">${esc(th.leader || "—")}</b></span>`,
    `<span>HOURLY LEADER <b class="${shift ? "m" : ""}">${esc(STATE.themes.hourly_leader || "—")}</b></span>`,
    shift ? `<span class="m">⚡ META SHIFT · ${esc(h.data.meta_shift.reason)}</span>` : "",
    `<span>MED TTT <b>${mins(s.median_ttt)}</b></span>`,
    ...STATE.tape.slice(0, 12).map(c => `<span>$${esc(c.symbol)} <b class="${c.is_real ? "g" : "r"}">${usd(c.ath_mcap)}</b> ${esc(CAT[c.primary_catalyst] || "")}</span>`),
    ...(th.emerging || []).slice(0, 4).map(e => `<span>EMERGING <b class="m">"${esc(e.word)}"</b> ${e.spike}x</span>`)];
  const html = items.join("");
  if ($("#ticker").dataset.h !== html) { $("#ticker").innerHTML = html + html; $("#ticker").dataset.h = html; }
  const busy = STATE.busy ? `<span class="chip y">SCANNING</span>` : "";
  $("#lwchips").innerHTML = `${busy}<span class="chip c">${STATE.mode.toUpperCase()}</span>
    <span class="chip g">HIT ${th.hit_rate}%</span>${shift ? `<span class="chip m">META SHIFT</span>` : ""}`;
}

function renderBoard() {
  const s = STATE.summary;
  $("#b-runners").textContent = s.runners;
  const today = s.per_day[s.per_day.length - 1];
  $("#b-today").textContent = `▲ +${today.real + today.other} today`;
  $("#b-max").textContent = usd(s.max_ath); $("#b-launches").textContent = `${s.launches.toLocaleString()} launches`;
  $("#b-real").textContent = s.real_pct + "%"; $("#b-avg").textContent = usd(s.avg_ath);
  $("#b-ttt").textContent = mins(s.median_ttt); $("#b-hit").textContent = s.hit_rate + "%";
  const max = Math.max(1, ...s.per_day.map(d => d.real + d.other));
  $("#b-days").innerHTML = s.per_day.map(d => `<div class="bar" title="${d.launches} launches">
    <div class="n">${d.real + d.other}</div>
    <div class="seg r" style="height:${d.other / max * 85}%"></div><div class="seg g" style="height:${d.real / max * 85}%;order:-1"></div>
    <div class="lbl">${d.day}</div></div>`).join("");
}

function renderTape() {
  const t = STATE.tape;
  if (!t.length) {
    const scout = STATE.agents.find(a => a.name === "SCOUT") || {};
    const msg = scout.status === "running" ? `scanning… ${esc(scout.detail || "")}<br><br><span class="dim">first scan takes ~5–10 min · watch THE TRACE below</span>`
      : STATE.last_scan ? `no runners in the last ${STATE.mode === "demo" ? "7 days" : "window"} yet · next scan soon` : "waiting for the first scan…";
    $("#book").innerHTML = `<div class="empty">${msg}</div>`; $("#tape-hdr").textContent = ""; return;
  }
  const max = Math.max(...t.map(c => c.ath_mcap || 0));
  $("#t-last").textContent = usd(t[0].ath_mcap);
  $("#t-lastsym").textContent = `$${t[0].symbol} · ${ago(t[0].ts_100k || t[0].created_ts)} ago`;
  $("#tape-hdr").textContent = `${t.length} latest`;
  $("#book").innerHTML = t.map(c => {
    const isNew = seenTape.size && !seenTape.has(c.mint);
    return `<div class="row ${c.mint === selected ? "sel" : ""} ${isNew ? "new" : ""}" data-mint="${esc(c.mint)}">
      <span class="fill ${c.is_real ? "g" : "r"}" style="width:${Math.max(4, Math.log10(c.ath_mcap / 1e5 + 1) / Math.log10(max / 1e5 + 1) * 100)}%"></span>
      <span class="sym">$${esc(c.symbol)}</span><span class="cat">${esc(CAT[c.primary_catalyst] || "—")}</span>
      <span class="dim">${mins(c.time_to_100k_min)}</span><span class="r" style="color:${c.is_real ? "#39ff88" : "#ff4d6d"}">${usd(c.ath_mcap)}</span></div>`;
  }).join("");
  t.forEach(c => seenTape.add(c.mint));
  document.querySelectorAll("#book .row").forEach(el => el.onclick = () => openCoin(el.dataset.mint));
  drawTapeChart();
}

function drawTapeChart() {
  const cv = $("#tapechart"), r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
  cv.width = r.width * dpr; cv.height = r.height * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const W = r.width, H = r.height, pad = 34, now = Date.now() / 1000, span = 24 * 3600;
  const cs = STATE.tape.filter(c => (c.ts_100k || c.created_ts) > now - span);
  const lo = Math.log10(1e5), hi = Math.log10(Math.max(2e5, ...cs.map(c => c.ath_mcap)) * 1.2);
  const X = ts => pad + (1 - (now - ts) / span) * (W - pad - 6), Y = v => H - 16 - (Math.log10(v) - lo) / (hi - lo) * (H - 26);
  g.font = "9px JetBrains Mono"; g.fillStyle = "#5d7183"; g.strokeStyle = "rgba(60,90,120,.15)";
  for (const v of [1e5, 3e5, 1e6, 3e6, 1e7]) { if (Math.log10(v) > hi) break; const y = Y(v); g.beginPath(); g.moveTo(pad, y); g.lineTo(W, y); g.stroke(); g.fillText(usd(v), 0, y + 3); }
  for (let hrs = 24; hrs >= 0; hrs -= 6) g.fillText(hrs ? `-${hrs}h` : "now", X(now - hrs * 3600) - 10, H - 3);
  // "vwap" style line: rolling avg of log ATH
  g.strokeStyle = "rgba(34,230,255,.5)"; g.setLineDash([3, 3]); g.beginPath();
  const sorted = [...cs].sort((a, b) => (a.ts_100k || a.created_ts) - (b.ts_100k || b.created_ts)); let acc = [];
  sorted.forEach((c, i) => { acc.push(Math.log10(c.ath_mcap)); if (acc.length > 6) acc.shift();
    const y = H - 16 - (acc.reduce((a, b) => a + b) / acc.length - lo) / (hi - lo) * (H - 26), x = X(c.ts_100k || c.created_ts);
    i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); g.setLineDash([]);
  for (const c of cs) {
    const x = X(c.ts_100k || c.created_ts), col = c.is_real ? "#39ff88" : "#ff4d6d";
    const cur = Math.max(1e5, Math.min(c.ath_mcap, c.usd_market_cap || 1e5));
    g.strokeStyle = col; g.lineWidth = 1; g.beginPath(); g.moveTo(x, Y(1e5)); g.lineTo(x, Y(c.ath_mcap)); g.stroke();
    g.fillStyle = col; g.globalAlpha = .85; g.fillRect(x - 2.5, Math.min(Y(cur), Y(c.ath_mcap)), 5, Math.max(2, Math.abs(Y(cur) - Y(c.ath_mcap)))); g.globalAlpha = 1;
    if (c.mint === selected) { g.strokeStyle = "#fff"; g.strokeRect(x - 5, Y(c.ath_mcap) - 4, 10, Y(1e5) - Y(c.ath_mcap) + 8); }
  }
}

function renderSynapse() {
  const th = STATE.themes, d = th.daily;
  window.Synapse.update(th.themes, STATE.agents, th.hourly_leader);
  $("#syn-hdr").textContent = `${th.themes.filter(t => t.runners).length} active neurons`;
  $("#synstats").innerHTML = `<div><span>24H LEADER</span><b class="g">${esc(d.leader || "—")}</b></div>
    <div><span>RUNNERS 24H</span><b>${d.runners}</b></div><div><span>LAUNCHES</span><b>${d.launches.toLocaleString()}</b></div>
    <div><span>HIT RATE</span><b>${d.hit_rate}%</b></div><div><span>REAL</span><b class="g">${d.real_pct ?? "—"}%</b></div>
    <div><span>AVG TTT</span><b>${mins(d.avg_ttt_min)}</b></div><div><span>1H LEADER</span><b class="m">${esc(th.hourly_leader || "—")}</b></div>`;
  $("#sources").innerHTML = STATE.sources.length ? STATE.sources.map(s => {
    const cls = s.ok ? "ok" : "bad";
    return `<div class="src ${cls}"><b>${esc(s.name)}</b><span class="st" style="color:${s.ok ? "#39ff88" : "#ff4d6d"}">${s.ok ? "● OK" : "● ERR"}</span>
      <span class="sub">${esc(s.agent)} · ${s.calls} calls · ${s.latency_ms ? Math.round(s.latency_ms) + "ms" : "—"}${!s.ok && s.last_error ? " · " + esc(s.last_error) : ""}</span></div>`;
  }).join("") : `<div class="src off"><b>no source calls yet</b><span></span><span class="sub">run a scan, or python run.py doctor</span></div>`;
  const t = STATE.daily;
  if (!t) { $("#cortex").innerHTML = `<div class="cx-card"><div class="empty">no daily thesis yet<br><br><button onclick="post('/api/thesis/daily')">WRITE ONE</button></div></div>`; return; }
  const x = t.data;
  $("#cx-by").innerHTML = `<span class="chip ${x.prose_by === "claude" ? "m" : "c"}">${x.prose_by === "claude" ? "CLAUDE" : "TEMPLATE"}</span>`;
  $("#cortex").innerHTML = `<div class="cx-card">
    <div class="cx-lead"><b>${esc(t.leader || "no leader")}</b><span class="dim">${ago(t.ts)} ago</span></div>
    <div class="kv"><span>runners / launches</span><b>${x.runners} / ${x.launches}</b><span>hit rate</span><b>${x.hit_rate}%</b>
      <span>REAL</span><b>${x.real_pct ?? "—"}%</b><span>avg time to 100k</span><b>${mins(x.avg_ttt_min)}</b></div>
    <div class="cx-text">${esc(t.text)}</div>
    <div class="cx-h g">▲ LOOK FOR</div><ul class="cx-list">${x.look_for.map(b => `<li>${esc(b)}</li>`).join("") || "<li>—</li>"}</ul>
    <div class="cx-h r">▼ AVOID</div><ul class="cx-list">${x.avoid.map(b => `<li>${esc(b)}</li>`).join("") || "<li>nothing flagged</li>"}</ul>
    <div class="cx-h c">CATALYST MIX</div><div class="kv">${x.catalysts.slice(0, 6).map(c => `<span>${esc(c.label.toLowerCase())}</span><b>${c.count}</b>`).join("")}</div>
    <div class="cx-h c">EMERGING WORDS</div><div class="words">${x.emerging.map(e => `<span class="word ${e.new_meta ? "new" : ""}" title="${e.theme ? "part of " + esc(e.theme) : "no theme: possible new meta"}">${esc(e.word)} ×${e.count}</span>`).join("") || "<span class=dim>none</span>"}</div>
  </div>`;
}

function renderAgents() {
  const by = Object.fromEntries(STATE.agents.map(a => [a.name, a]));
  $("#agents").innerHTML = Object.keys(AGENT_INFO).map(n => {
    const a = by[n] || { status: "idle", detail: "waiting" }; const col = a.status === "error" ? "#ff4d6d" : AC[n];
    const pct = { running: 0.7, ok: 1, warn: 0.6, error: 0.35, idle: 0.15 }[a.status] ?? 0.15;
    const C = 2 * Math.PI * 16;
    return `<div class="agent ${a.status}" style="--ac:${col}" title="${esc(a.detail)}">
      <div class="ring"><svg viewBox="0 0 38 38"><circle class="trk" cx="19" cy="19" r="16" fill="none" stroke-width="2.5"/>
        <circle class="arc" cx="19" cy="19" r="16" fill="none" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - pct)}"/></svg>
        <div class="blobicon"></div></div>
      <div class="meta"><div class="nm">${n}<small>${AGENT_INFO[n]}</small></div>
        <div class="stt">${esc(a.status)}${a.last_ts ? " · " + ago(a.last_ts) : ""}</div><div class="dt">${esc(a.detail || "")}</div></div></div>`;
  }).join("");
}

function renderRotation() {
  const cv = $("#rotcanvas"), r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
  cv.width = r.width * dpr; cv.height = r.height * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const W = r.width, H = r.height, L = 34, B = 22;
  const rows = STATE.themes.themes.filter(t => t.theme !== "Unthemed" && (t.runners || t.h_runners || t.launches > 5));
  const maxS = Math.max(20, ...rows.map(t => Math.max(t.share, t.h_share))) * 1.1;
  const lmin = Math.log(0.1), lmax = Math.log(Math.max(4, ...rows.map(t => Math.max(t.momentum, t.h_momentum))) * 1.3);
  const X = s => L + Math.sqrt(s / maxS) * (W - L - 10), Y = m => H - B - (Math.log(Math.max(0.1, m)) - lmin) / (lmax - lmin) * (H - B - 10);
  const midX = X(100 / Math.max(1, rows.length)), midY = Y(1);
  g.fillStyle = "rgba(57,255,136,.04)"; g.fillRect(midX, 10, W - midX, midY - 10);
  g.fillStyle = "rgba(255,77,109,.04)"; g.fillRect(L, midY, midX - L, H - B - midY);
  g.strokeStyle = "rgba(120,150,180,.25)"; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(L, midY); g.lineTo(W, midY); g.moveTo(midX, 10); g.lineTo(midX, H - B); g.stroke(); g.setLineDash([]);
  g.font = "700 9px JetBrains Mono"; g.fillStyle = "#39ff88aa"; g.fillText("LEADING", W - 56, 20); g.fillStyle = "#22e6ffaa"; g.fillText("IMPROVING", L + 4, 20);
  g.fillStyle = "#ff8a3daa"; g.fillText("WEAKENING", W - 64, H - B - 6); g.fillStyle = "#ff4d6daa"; g.fillText("LAGGING", L + 4, H - B - 6);
  g.font = "9px JetBrains Mono"; g.fillStyle = "#5d7183"; g.fillText("share of runners →", W - 120, H - 6);
  g.save(); g.translate(10, H / 2 + 40); g.rotate(-Math.PI / 2); g.fillText("momentum (log) →", 0, 0); g.restore();
  for (const m of [0.25, 1, 2, 4]) if (Math.log(m) < lmax) g.fillText(m + "x", 14, Y(m) + 3);
  const all = STATE.themes.themes.map(t => t.theme);
  rows.forEach(t => {
    const col = window.Synapse.PALETTE[all.indexOf(t.theme) % window.Synapse.PALETTE.length];
    const x1 = X(t.share), y1 = Y(t.momentum || 0.1), x2 = X(t.h_share), y2 = Y(t.h_momentum || 0.1);
    g.strokeStyle = col; g.globalAlpha = .6; g.lineWidth = 1.4; g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
    const a = Math.atan2(y2 - y1, x2 - x1); if (Math.hypot(x2 - x1, y2 - y1) > 8) { g.beginPath(); g.moveTo(x2, y2);
      g.lineTo(x2 - 7 * Math.cos(a - .4), y2 - 7 * Math.sin(a - .4)); g.lineTo(x2 - 7 * Math.cos(a + .4), y2 - 7 * Math.sin(a + .4)); g.fillStyle = col; g.fill(); }
    g.globalAlpha = 1; g.fillStyle = col; g.shadowColor = col; g.shadowBlur = 12;
    g.beginPath(); g.arc(x1, y1, 3 + Math.sqrt(t.runners) * 1.6, 0, 7); g.fill(); g.shadowBlur = 0;
    g.fillStyle = "#e8f1fa"; g.font = "9px JetBrains Mono"; g.fillText(t.theme.split(" ")[0].toUpperCase(), x1 + 7, y1 - 6);
  });
}

function renderHourly() {
  const h = STATE.hourly;
  if (!h) { $("#hourlybody").innerHTML = `<div class="empty">no hourly thesis yet</div>`; return; }
  const x = h.data, sh = x.meta_shift || {};
  const rows = x.themes.filter(t => t.runners || t.launches).slice(0, 7); const max = Math.max(1, ...rows.map(t => t.runners));
  const all = STATE.themes.themes.map(t => t.theme);
  $("#hourlybody").innerHTML = `<div class="hr-top">${sh.shift ? `<span class="shift">⚡ META SHIFT</span>` : `<span class="noshift">NO SHIFT</span>`}
      <span>1H LEADER <b style="color:#ff3df2">${esc(h.leader || "—")}</b></span><span class="dim">vs 24H</span><b style="color:#39ff88">${esc(sh.daily_leader || "—")}</b>
      <span class="dim">· ${x.runners} runners / ${x.launches} launches · ${ago(h.ts)} ago</span></div>
    ${sh.shift ? `<div style="color:#ff3df2;margin-top:6px">${esc(sh.reason)}</div>` : ""}
    <div class="hr-grid"><div><div class="cx-text">${esc(h.text)}</div></div>
      <div><div class="sec">THEMES THIS HOUR</div>${rows.map(t => `<div class="tbar"><span>${esc(t.theme)}</span><div class="track">
        <div class="fillb" style="width:${t.runners / max * 100}%;background:${window.Synapse.PALETTE[all.indexOf(t.theme) % 11]}"></div></div><span class="r">${t.runners} · ${t.momentum}x</span></div>`).join("")}</div>
      <div><div class="sec">LOOK FOR</div><ul class="cx-list">${x.look_for.map(b => `<li>${esc(b)}</li>`).join("") || "<li>—</li>"}</ul>
        <div class="sec">EMERGING</div><div class="words">${x.emerging.map(e => `<span class="word ${e.new_meta ? "new" : ""}" title="${e.theme ? "part of " + esc(e.theme) : "no theme: possible new meta"}">${esc(e.word)} ×${e.count}</span>`).join("") || "<span class=dim>none</span>"}</div></div></div>`;
}

async function openCoin(mint) {
  selected = mint; document.querySelectorAll("#book .row").forEach(r => r.classList.toggle("sel", r.dataset.mint === mint));
  let c; try { c = await j("/api/coin/" + encodeURIComponent(mint)); } catch { return; }
  const badge = c.is_real === true ? `<span class="badge real">REAL</span>` : c.is_real === false ? `<span class="badge fake">FAILS CHECKS</span>` : `<span class="badge unk">PENDING</span>`;
  const sig = Object.entries(c.signals || {}).map(([k, s]) => {
    const st = s.passed === true ? "ok" : s.passed === false ? "no" : "na"; const col = st === "ok" ? "#39ff88" : st === "no" ? "#ff4d6d" : "#2a3a48";
    return `<div class="sig"><span>${esc(s.label)}${s.required ? "" : "<small class=dim>*</small>"}</span><div class="track"><div class="fillb" style="width:${(s.score || 0) * 100}%;background:${col};box-shadow:0 0 8px ${col}"></div></div>
      <span class="${st}">${st === "ok" ? "PASS" : st === "no" ? "FAIL" : "—"}</span><span class="note">${esc(s.note)}</span></div>`;
  }).join("");
  const kols = (c.kols || []).map(k => `<div class="kol"><b>${esc(k.name)}</b><span class="dim">+${mins((k.ts - c.created_ts) / 60)} after launch</span></div>`).join("") || `<div class="dim">no tracked KOL buys</div>`;
  const links = [["pump.fun", "https://pump.fun/coin/" + c.mint], ["dexscreener", c.dex_url || "https://dexscreener.com/solana/" + c.mint],
    ["solscan", "https://solscan.io/token/" + c.mint], ["twitter", c.twitter], ["website", c.website], ["telegram", c.telegram]]
    .filter(l => l[1] && /^https?:\/\//.test(l[1])).map(l => `<a href="${esc(l[1])}" target="_blank" rel="noopener noreferrer">${l[0]} ↗</a>`).join("");
  const why = c.why || c.why_template || [];
  $("#coin").innerHTML = `<div class="cf-top">${c.image && /^https?:/.test(c.image) ? `<img src="${esc(c.image)}" alt="" onerror="this.remove()">` : ""}
      <div style="flex:1;min-width:0"><div class="cf-sym">$${esc(c.symbol)} ${badge}</div><div class="cf-name">${esc(c.name)} · ${esc(c.mint.slice(0, 6))}…${esc(c.mint.slice(-6))}${c.demo ? " · demo" : ""}</div></div>
      <div style="text-align:right"><div class="midnum" style="color:#39ff88">${usd(c.ath_mcap)}</div><div class="dim">ATH · now ${usd(c.usd_market_cap)}</div></div></div>
    <div class="cf-cat"><div class="lbl">◆ ${esc(CAT[c.primary_catalyst] || "UNCLASSIFIED")}</div>
      <div class="sec" style="margin-top:6px"><span>WHY IT RAN</span><span>${c.why_llm ? "CLAUDE" : "TEMPLATE"}</span></div>
      <ul>${why.map(b => `<li>${esc(b)}</li>`).join("")}</ul></div>
    <div class="grid3"><div><span>TIME TO 100K</span><b>${mins(c.time_to_100k_min)}</b></div><div><span>AGE</span><b>${ago(c.created_ts)}</b></div><div><span>VOL 24H</span><b>${usd(c.volume_24h)}</b></div>
      <div><span>HOLDERS</span><b>${c.holders?.toLocaleString() ?? "—"}</b></div><div><span>TOP 10</span><b>${c.top10_pct != null ? c.top10_pct + "%" : "—"}</b></div><div><span>TRADERS</span><b>${c.unique_traders?.toLocaleString() ?? "—"}</b></div>
      <div><span>FEES PAID</span><b>${c.fees_sol != null ? c.fees_sol + " SOL" : "—"}</b></div><div><span>FEE / VOL</span><b>${c.fee_volume_pct != null ? c.fee_volume_pct + "%" : "—"}</b></div><div><span>X MENTIONS</span><b>${c.x_mentions ?? "—"}${c.x_big_account ? " ★" : ""}</b></div>
      <div><span>ROUND-TRIP WALLETS</span><b>${c.round_trip_pct != null ? c.round_trip_pct + "%" : "—"}</b></div><div><span>SAME-SIZE TRADES</span><b>${c.size_mode_pct != null ? c.size_mode_pct + "%" : "—"}</b></div><div><span>TRADES / WALLET</span><b>${c.txs_per_trader ?? "—"}</b></div>
      <div><span>STAIRCASE</span><b style="color:${(c.staircase_score ?? 0) >= 0.65 ? "#ff4d6d" : "inherit"}">${c.staircase_score ?? "—"}</b></div><div><span>MAX PULLBACK</span><b>${c.max_pullback_pct != null ? c.max_pullback_pct + "%" : "—"}</b></div><div><span>CLIFF DUMP</span><b style="color:${c.cliff_dump ? "#ff4d6d" : "inherit"}">${c.cliff_dump == null ? "—" : c.cliff_dump ? "YES" : "no"}</b></div>
      <div><span>ORGANIC SCORE</span><b>${c.organic_score ?? "—"}</b></div><div><span>ORGANIC VOL</span><b>${c.organic_volume_pct != null ? c.organic_volume_pct + "%" : "—"}</b></div><div><span>DEV LAUNCHES</span><b>${c.dev_launches ?? "—"}</b></div></div>
    ${!c.demo ? `<div class="sec"><span>LIVE CHART</span><span>dexscreener</span></div>
      <iframe class="dexchart" loading="lazy" title="DexScreener chart" referrerpolicy="no-referrer"
        src="https://dexscreener.com/solana/${encodeURIComponent(c.pair_address || c.mint)}?embed=1&theme=dark&trades=0&info=0&chartLeftToolbar=0"></iframe>` : ""}
    <div class="sec"><span>SIGNALS</span><span>* optional</span></div>${sig || `<div class="dim">not evaluated yet</div>`}
    <div class="sec"><span>KOL BUYERS</span><span>${(c.kols || []).length}</span></div>${kols}
    <div class="sec"><span>CATALYST RULES HIT</span></div>${(c.catalysts || []).map(h => `<div class="kv"><span>${esc(h.label)}</span><b>${h.weight}</b></div>`).join("")}
    <div class="sec"><span>THEMES</span></div><div class="words">${(c.themes || []).map(t => `<span class="word">${esc(t)}</span>`).join("") || "<span class=dim>unthemed</span>"}</div>
    ${c.description ? `<div class="sec"><span>DESCRIPTION</span></div><div class="dim">${esc(c.description)}</div>` : ""}
    <div class="links">${links}</div>`;
  if (STATE) drawTapeChart();
}

async function pollEvents() {
  try {
    const evs = await j("/api/events?after=" + lastEvent + "&limit=150");
    if (evs.length) {
      const box = $("#trace"), atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      const first = lastEvent === 0;
      box.insertAdjacentHTML("beforeend", evs.map(e => `<div class="ev ${e.level} ${first ? "" : "fresh"}" style="--ac:${AC[e.agent] || "#5d7183"}">
        <span class="t">${new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span><span class="a">${esc(e.agent)}</span><span class="m">${esc(e.msg)}</span></div>`).join(""));
      while (box.children.length > 400) box.firstChild.remove();
      if (atBottom || first) box.scrollTop = box.scrollHeight;
      lastEvent = evs[evs.length - 1].id;
      const last = evs[evs.length - 1];
      $("#livewire").innerHTML = `<b>${esc(last.agent)}</b> ${esc(last.msg)} <span class="dim">· ${new Date(last.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>`;
    }
  } catch { /* backend down: keep last state */ }
}

async function post(url) { try { const r = await j(url, { method: "POST" }); if (!r.started) $("#livewire").textContent = r.reason; } catch (e) { $("#livewire").textContent = "request failed: " + e; } setTimeout(refresh, 1500); }
$("#btn-scan").onclick = () => post("/api/scan");
$("#btn-hourly").onclick = () => post("/api/thesis/hourly");
window.addEventListener("resize", () => { if (STATE) { drawTapeChart(); renderRotation(); } });

// ---- settings / setup / updates ---------------------------------------------------
let SETTINGS = null, VERSION = null;
async function loadMeta() {
  try { SETTINGS = await j("/api/settings"); } catch { return; }
  try { VERSION = await j("/api/version"); } catch { VERSION = null; }
  const b = $("#banner"); b.hidden = true; b.className = "banner";
  if (VERSION && VERSION.update_available && !VERSION.hosted) {
    b.hidden = false; b.classList.add("up");
    b.innerHTML = `▲ Update available: ${esc(VERSION.latest)} (you have ${esc(VERSION.version)}).
      <a href="${esc(VERSION.release_url)}" target="_blank" rel="noopener">DOWNLOAD</a><span class="dim">Your data and keys are kept.</span>`;
  } else if (SETTINGS.mode === "live" && !SETTINGS.keys.HELIUS_API_KEY) {
    b.hidden = false;
    b.innerHTML = `LIVE MODE · pump.fun, DexScreener and GeckoTerminal need no key. Add a free Helius key to run the REAL checks (holders, fees, KOL buys).
      <button onclick="openSettings()">ADD KEY</button>${SETTINGS.editable || SETTINGS.needs_password ? "" : '<span class="dim">(set it in your host\'s environment variables)</span>'}`;
  } else if (SETTINGS.mode === "demo") {
    b.hidden = false;
    b.innerHTML = `DEMO MODE · generated data. <button onclick="openSettings()">SWITCH TO LIVE</button>`;
  }
  $("#foot").dataset.v = VERSION ? VERSION.version : "";
}

function openSettings() {
  const d = $("#settings"), f = $("#settings-form");
  if (!SETTINGS) return;
  f.mode.value = SETTINGS.mode;
  for (const k of Object.keys(SETTINGS.keys)) { $("#k-" + k).textContent = SETTINGS.keys[k] ? "✓ saved" : ""; f[k].value = ""; }
  $("#pw-row").hidden = !SETTINGS.needs_password;
  const locked = !SETTINGS.editable && !SETTINGS.needs_password;
  $("#set-msg").textContent = locked ? "This copy is hosted without an ADMIN_PASSWORD, so settings can only be changed from the host's environment variables." : "";
  $("#set-save").disabled = locked;
  d.showModal();
}

$("#settings-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  e.preventDefault();
  const f = e.target, body = { mode: f.mode.value };
  for (const k of Object.keys(SETTINGS.keys)) { const v = f[k].value.trim(); if (v) body[k] = v.toLowerCase() === "clear" ? "" : v; }
  $("#set-msg").textContent = "saving…";
  try {
    const r = await fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json", "x-admin-password": f.admin.value },
      body: JSON.stringify(body) });
    if (!r.ok) { $("#set-msg").textContent = r.status === 403 ? "Wrong admin password." : "Save failed (" + r.status + ")."; return; }
    const out = await r.json();
    if (out.restarting) { $("#set-msg").textContent = "Saved. Restarting in " + body.mode.toUpperCase() + " mode…"; setTimeout(() => location.reload(), 6000); return; }
    $("#settings").close(); loadMeta(); post("/api/scan");
  } catch (err) { $("#set-msg").textContent = "Save failed: " + err; }
});
$("#btn-settings").onclick = openSettings;
loadMeta(); setInterval(loadMeta, 5 * 60 * 1000);

window.Synapse.init($("#syncanvas"), $("#syntip"));
refresh(); pollEvents();
setInterval(refresh, 15000); setInterval(pollEvents, 3000);
