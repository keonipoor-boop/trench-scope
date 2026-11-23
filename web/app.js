/* Trench Scope dashboard: polls /api/state every 15s and /api/events every 3s. */
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (v) => !v ? "$0" : v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? "$" + (v / 1e3).toFixed(0) + "k" : "$" + Math.round(v);
const mins = (m) => m == null ? "—" : m >= 60 ? (m / 60).toFixed(1) + "h" : Math.round(m) + "m";
const ago = (ts) => { const s = Date.now() / 1000 - ts; return s < 60 ? Math.round(s) + "s" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? (s / 3600).toFixed(1) + "h" : (s / 86400).toFixed(1) + "d"; };
const CAT = { celebrity_tweet: "CELEB TWEET", tweet_play: "TWEET PLAY", community: "COMMUNITY", cto: "CTO", tech: "TECH/UTILITY",
  kol_call: "KOL CALL", livestream: "LIVESTREAM", news: "NEWS/EVENT", meme: "PURE MEME" };
const CAT_NICE = { celebrity_tweet: "Celebrity tweet", tweet_play: "Tweet play", community: "Community", cto: "CTO",
  tech: "Tech / utility", kol_call: "KOL call", livestream: "Livestream", news: "News / event", meme: "Pure meme" };
const AGENT_INFO = { SCOUT: "discovery", HOLDERS: "holders", KOLS: "kol wallets", FEES: "fees/traders", CATALYST: "why it ran", THESIS: "narrative" };
const AC = window.Synapse.AGENT_COLORS;
let STATE = null, lastEvent = 0, selected = null, seenTape = new Set();
// ---- timeframe (chart-style TF bar) -------------------------------------------------
const TFS = ["7D", "3D", "24H", "12H", "6H", "4H", "1H", "30M", "15M", "5M", "1M", "30S", "15S", "1S"];
const TF_SECS = { D: 86400, H: 3600, M: 60, S: 1 };
const tfSecs = (tf) => parseInt(tf) * TF_SECS[tf.slice(-1)];
const tfWords = (s) => s >= 86400 ? `${s / 86400}D` : s >= 3600 ? `${s / 3600}H` : s >= 60 ? `${Math.round(s / 60)}M` : `${s}S`;
let TF = "7D";
try { const saved = localStorage.getItem("tf"); if (TFS.includes(saved)) TF = saved; } catch { /* storage blocked */ }
let refreshTimer = null;
function scheduleRefresh() {  // short windows move fast: refresh every 3s at 5M and below
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, tfSecs(TF) <= 300 ? 3000 : 15000);
}
function renderTfBar() {
  $("#tf").innerHTML = TFS.map(t => `<button role="tab" aria-selected="${t === TF}" data-tf="${t}">${t}</button>`).join("");
  document.querySelectorAll("#tf button").forEach(b => b.onclick = () => {
    TF = b.dataset.tf; try { localStorage.setItem("tf", TF); } catch { /* ignore */ }
    seenTape = new Set(); renderTfBar(); scheduleRefresh(); refresh();
  });
  $("#tfnote").textContent = `coins that crossed $100k in the last ${TF} · refresh every ${tfSecs(TF) <= 300 ? "3s" : "15s"}`;
}

async function j(url, opt) { const r = await fetch(url, opt); if (!r.ok) throw new Error(r.status); return r.json(); }

async function refresh() {
  try { STATE = await j("/api/state?tf=" + encodeURIComponent(TF)); } catch (e) { $("#livewire").textContent = "backend unreachable: " + e; return; }
  renderTicker(); renderBoard(); renderTape(); renderSynapse(); renderAgents(); renderRotation(); renderHourly();
  $("#foot").textContent = `${STATE.mode.toUpperCase()} MODE${$("#foot").dataset.v ? " · v" + $("#foot").dataset.v : ""} · last scan ${STATE.last_scan ? ago(STATE.last_scan.ts) + " ago" : "never"}`;
  if (!selected && STATE.tape.length) openCoin(STATE.tape[0].mint);
}

function renderTicker() {
  const s = STATE.summary, d = STATE.daily, h = STATE.hourly, th = STATE.themes.daily;
  const shift = h && h.meta_shift;
  const items = [`<span class="live">◉ SIX AGENTS LIVE</span>`,
    `<span>RUNNERS ${TF} <b>${s.runners}</b></span>`, `<span>REAL <b class="g">${s.real_pct}%</b></span>`,
    `<span>HIT RATE <b>${s.hit_rate}%</b></span>`, `<span>${TF} LEADER <b class="g">${esc(th.leader || "—")}</b></span>`,
    `<span>LAST ${tfWords(STATE.themes.tail_s)} LEADER <b class="${shift ? "m" : ""}">${esc(STATE.themes.hourly_leader || "—")}</b></span>`,
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
  $("#b-today").textContent = `▲ +${today.real + today.other} in newest bar`;
  $("#b-label").textContent = `RUNNERS · ${TF}`;
  $("#b-sub").textContent = `— last ${TF} · every coin that cleared the bar`;
  $("#b-binlabel").textContent = `RUNNERS PER ${tfWords(s.window_s / s.per_day.length).replace(/^1(?=[DHMS])/, "")} BAR`;
  $("#b-max").textContent = usd(s.max_ath); $("#b-launches").textContent = `${s.launches.toLocaleString()} launches`;
  $("#b-real").textContent = s.real_pct + "%"; $("#b-avg").textContent = usd(s.avg_ath);
  $("#b-ttt").textContent = mins(s.median_ttt); $("#b-hit").textContent = s.hit_rate + "%";
  const max = Math.max(1, ...s.per_day.map(d => d.real + d.other));
  $("#b-days").style.gridTemplateColumns = `repeat(${s.per_day.length}, 1fr)`;
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
      : STATE.last_scan ? `no coins crossed $100k in the last ${TF}${tfSecs(TF) < 86400 ? "<br><br><span class='dim'>try a longer timeframe</span>" : " yet · next scan soon"}` : "waiting for the first scan…";
    $("#book").innerHTML = `<div class="empty">${msg}</div>`; $("#tape-hdr").textContent = ""; return;
  }
  const max = Math.max(...t.map(c => c.ath_mcap || 0));
  $("#t-last").textContent = usd(t[0].ath_mcap);
  $("#t-lastsym").textContent = `$${t[0].symbol} · ${ago(t[0].ts_100k || t[0].created_ts)} ago`;
  $("#tape-hdr").textContent = `${t.length} latest`;
  $("#book").innerHTML = t.map(c => {
    const isNew = seenTape.size && !seenTape.has(c.mint);
    const status = c.is_real === true ? '<span class="pill real">Real</span>' : c.is_real === false ? '<span class="pill fake">Fake</span>' : '<span class="pill wait">Analyzing</span>';
    const sub = [c.primary_catalyst ? CAT_NICE[c.primary_catalyst] || c.primary_catalyst : "Classifying…",
                 c.holders ? Number(c.holders).toLocaleString() + " holders" : null].filter(Boolean).join(" · ");
    return `<div class="coin-row ${c.mint === selected ? "sel" : ""} ${isNew ? "new" : ""}" data-mint="${esc(c.mint)}" tabindex="0" role="option">
      ${coinImg(c.image, c.symbol)}
      <div class="cn"><b>${esc(c.name || c.symbol)}${status}</b><span>$${esc(c.symbol)} · ${esc(sub)}</span></div>
      <div class="cv"><b>${usd(c.ath_mcap)}</b><span>now ${usd(c.usd_market_cap)}${c.time_to_100k_min != null ? " · " + mins(c.time_to_100k_min) : ""}</span></div></div>`;
  }).join("");
  t.forEach(c => seenTape.add(c.mint));
  document.querySelectorAll("#book .coin-row").forEach(el => {
    el.onclick = (e) => { e.stopPropagation(); openCoin(el.dataset.mint); openPop(el.dataset.mint, el); };
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } };
  });
  drawTapeChart();
}

function drawTapeChart() {
  const cv = $("#tapechart"), r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
  cv.width = r.width * dpr; cv.height = r.height * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const W = r.width, H = r.height, pad = 34, now = Date.now() / 1000, span = tfSecs(TF);
  const cs = STATE.tape.filter(c => (c.ts_100k || c.created_ts) > now - span);
  const lo = Math.log10(1e5), hi = Math.log10(Math.max(2e5, ...cs.map(c => c.ath_mcap)) * 1.2);
  const X = ts => pad + (1 - (now - ts) / span) * (W - pad - 6), Y = v => H - 16 - (Math.log10(v) - lo) / (hi - lo) * (H - 26);
  g.font = "9px JetBrains Mono"; g.fillStyle = "#5d7183"; g.strokeStyle = "rgba(60,90,120,.15)";
  for (const v of [1e5, 3e5, 1e6, 3e6, 1e7]) { if (Math.log10(v) > hi) break; const y = Y(v); g.beginPath(); g.moveTo(pad, y); g.lineTo(W, y); g.stroke(); g.fillText(usd(v), 0, y + 3); }
  for (let k = 4; k >= 0; k--) {  // 5 time ticks across the selected timeframe
    const ago = span * k / 4;
    g.fillText(k ? `-${tfWords(Math.round(ago)).toLowerCase()}` : "now", X(now - ago) - 10, H - 3);
  }
  // "vwap" style line: rolling avg of log ATH
  g.strokeStyle = "rgba(34,230,255,.5)"; g.setLineDash([3, 3]); g.beginPath();
  const sorted = [...cs].sort((a, b) => (a.ts_100k || a.created_ts) - (b.ts_100k || b.created_ts)); let acc = [];
  sorted.forEach((c, i) => { acc.push(Math.log10(c.ath_mcap)); if (acc.length > 6) acc.shift();
    const y = H - 16 - (acc.reduce((a, b) => a + b) / acc.length - lo) / (hi - lo) * (H - 26), x = X(c.ts_100k || c.created_ts);
    i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); g.setLineDash([]);
  for (const c of cs) {
    const x = X(c.ts_100k || c.created_ts), col = c.is_real === true ? "#39ff88" : c.is_real === false ? "#ff4d6d" : "#7d8ba0";
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
  $("#synstats").innerHTML = `<div><span>${TF} LEADER</span><b class="g">${esc(d.leader || "—")}</b></div>
    <div><span>RUNNERS ${TF}</span><b>${d.runners}</b></div><div><span>LAUNCHES</span><b>${d.launches.toLocaleString()}</b></div>
    <div><span>HIT RATE</span><b>${d.hit_rate}%</b></div><div><span>REAL</span><b class="g">${d.real_pct ?? "—"}%</b></div>
    <div><span>AVG TTT</span><b>${mins(d.avg_ttt_min)}</b></div><div><span>LAST ${tfWords(th.tail_s)} LEADER</span><b class="m">${esc(th.hourly_leader || "—")}</b></div>`;
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
  $("#rot-sub").textContent = `— share vs momentum · tail = ${TF} → last ${tfWords(STATE.themes.tail_s)}`;
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
renderTfBar(); scheduleRefresh(); setInterval(pollEvents, 3000);

// ---- KOL LAB ------------------------------------------------------------------------
let KOLS = [], KOL_SEL = null, ADMIN_PW = "";
const dur = (s) => s == null ? "—" : s >= 86400 ? (s / 86400).toFixed(1) + "d" : s >= 3600 ? (s / 3600).toFixed(1) + "h" : s >= 60 ? Math.round(s / 60) + "m" : Math.round(s) + "s";
const initials = (n) => esc((n || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?");
const pfpHtml = (url, name, big) => url && /^(https:\/\/|\/api\/pfp\/)/.test(url)
  ? `<img class="pfp${big ? " big" : ""}" src="${esc(url)}" alt="" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;pfp${big ? " big" : ""}&quot;>${initials(name)}</div>'">`
  : `<div class="pfp${big ? " big" : ""}">${initials(name)}</div>`;
const GOOD_RISK = ["Cuts losers fast", "Scales out", "Consistent sizing", "High win rate"];
const tagsHtml = (style, risk) => (style || []).map(t => `<span class="tag s">${esc(t)}</span>`).join("") +
  (risk || []).map(t => `<span class="tag ${GOOD_RISK.includes(t) ? "good" : "bad"}">${esc(t)}</span>`).join("");
const handleOf = (tw) => tw ? tw.replace(/\/+$/, "").split("/").pop().replace(/^@/, "") : "";

// solar system: KOLs orbit the KOL core as planets; ring colour = trading style
const STYLE_COLORS = [["Sniper", "#22e6ff"], ["Scalper", "#39ff88"], ["Swing", "#a26bff"], ["Late momentum", "#ff8a3d"],
  ["Whale", "#ffd23d"], ["hunter", "#ff3df2"], ["High-frequency", "#4dd0ff"], ["Generalist", "#f2f7ff"]];
const styleColor = (style) => (STYLE_COLORS.find(([k]) => (style || []).some(s => s.includes(k))) || [0, "#c8d6e2"])[1];
const PLANETS = new Map();  // name -> {el, ring, angle, speed, size}
let ZOOMED = null, spaceT0 = performance.now(), HOVER = false, pausedAt = null;

function planetSize(k) { const n = k.summary.positions || 0; return Math.round(46 + Math.min(34, Math.sqrt(n) * 4)); }

async function loadKols() {
  try { KOLS = await j("/api/kols"); } catch { return; }
  $("#kolcount").textContent = `${KOLS.length} tracked`;
  const host = $("#kolplanets"), names = new Set(KOLS.map(k => k.name));
  for (const [n, p] of PLANETS) if (!names.has(n)) { p.el.remove(); PLANETS.delete(n); }
  KOLS.forEach((k, i) => {
    let p = PLANETS.get(k.name);
    if (!p) {
      const el = document.createElement("div"); el.className = "kol-planet"; el.dataset.name = k.name;
      el.onclick = (e) => { e.stopPropagation(); zoomTo(k.name); };
      el.onmouseenter = () => { HOVER = true; }; el.onmouseleave = () => { HOVER = false; };
      host.appendChild(el);
      const ring = i % 3 + 1;
      p = { el, ring, angle: (i * 2.399) % (Math.PI * 2), speed: (0.10 / ring) * (i % 2 ? 1 : 0.8) };
      PLANETS.set(k.name, p);
    }
    p.size = planetSize(k);
    const sig = JSON.stringify([k.status, k.style, k.pfp, k.summary]);
    if (p.sig !== sig) {
      p.sig = sig;
      p.el.style.width = p.el.style.height = p.size + "px";
      p.el.style.setProperty("--pc", k.status === "running" ? "#22e6ff" : styleColor(k.style));
      p.el.classList.toggle("running", k.status === "running" || k.status === "new");
      p.el.title = `${k.name} · ${(k.style || []).join(", ") || k.status}`;
      p.el.innerHTML = pfpHtml(k.pfp, k.name) + `<span class="pl-name">${esc(k.name)}${k.summary.win_rate != null ? " · " + k.summary.win_rate + "%" : ""}</span>`;
    }
  });
  $("#kollegend").innerHTML = STYLE_COLORS.map(([k, c]) => `<span style="--c:${c}">${k === "hunter" ? "Narrative hunter" : k}</span>`).join("") +
    (KOLS.length ? "" : '<span style="--c:#ff3df2">click the core to add your first KOL</span>');
  if (ZOOMED && !PLANETS.has(ZOOMED)) zoomOut();
  if (ZOOMED && $("#koldetail").dataset.status === "running" && KOLS.find(k => k.name === ZOOMED)?.status !== "running") openKol(ZOOMED);
}

function planetPos(p, t) {
  const st = $("#kolstage"), W = st.clientWidth, H = st.clientHeight;
  const a = p.angle + (ZOOMED ? p.frozenT ?? t : t) * p.speed;
  const rx = W * 0.15 * p.ring, ry = H * 0.065 * p.ring * (H / Math.max(W, 1)) * 2.2;
  const bob = Math.sin(t * 1.3 + p.angle * 3) * 4;
  return { x: W / 2 + Math.cos(a) * rx - p.size / 2, y: H / 2 + Math.sin(a) * ry - p.size / 2 + bob, depth: Math.sin(a) };
}

function spaceFrame(now) {
  // hovering a planet pauses the orbit so it's easy to click
  if (HOVER && pausedAt == null) pausedAt = now;
  if (!HOVER && pausedAt != null) { spaceT0 += now - pausedAt; pausedAt = null; }
  const t = ((pausedAt ?? now) - spaceT0) / 1000;
  for (const p of PLANETS.values()) {
    const { x, y, depth } = planetPos(p, t);
    const sc = 0.8 + (depth + 1) * 0.15;  // nearer (lower) planets are bigger, like a tilted orbit
    p.el.style.transform = `translate(${x}px, ${y}px) scale(${sc})`;
    p.el.style.zIndex = depth > 0 ? 3 : 1;
    p.last = { x, y, sc };
  }
  requestAnimationFrame(spaceFrame);
}

function zoomTo(name) {
  const p = PLANETS.get(name); if (!p) return;
  const st = $("#kolstage"), W = st.clientWidth, H = st.clientHeight, narrow = W < 900;
  const t = ((pausedAt ?? performance.now()) - spaceT0) / 1000;
  for (const q of PLANETS.values()) q.frozenT = t;   // freeze the system while zoomed
  ZOOMED = name; KOL_SEL = name;
  document.querySelectorAll(".kol-planet").forEach(el => el.classList.toggle("sel", el.dataset.name === name));
  const { x, y } = planetPos(p, t), z = narrow ? 1.6 : 2.2;
  const cx = x + p.size / 2, cy = y + p.size / 2;
  const tx = (narrow ? W / 2 : W * 0.18) - cx * z, ty = (narrow ? H * 0.16 : H / 2) - cy * z;
  $("#kolspace").style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
  st.classList.add("zoomed");
  setTimeout(() => { $("#koldetail").hidden = false; openKol(name); }, 450);
}

function zoomOut() {
  ZOOMED = null; KOL_SEL = null;
  const st = $("#kolstage"); st.classList.remove("zoomed");
  $("#kolspace").style.transform = ""; $("#koldetail").hidden = true;
  document.querySelectorAll(".kol-planet").forEach(el => el.classList.remove("sel"));
  HOVER = false;
  const t = ((pausedAt ?? performance.now()) - spaceT0) / 1000;
  for (const q of PLANETS.values()) { if (q.frozenT != null) { q.angle += (q.frozenT - t) * q.speed; } q.frozenT = null; }
}
$("#kd-close").onclick = zoomOut;
$("#kolstage").addEventListener("click", (e) => { if (ZOOMED && e.target.closest(".kol-space") && !e.target.closest(".kol-planet")) zoomOut(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && ZOOMED && !document.querySelector("dialog[open]")) zoomOut(); });
$("#kolsun").onclick = (e) => { e.stopPropagation(); openKolForm(null); };
requestAnimationFrame(spaceFrame);

function mixHtml(obj) {
  const e = Object.entries(obj || {}); const tot = e.reduce((a, [, v]) => a + v, 0) || 1;
  return e.map(([k, v]) => `<div class="mix"><span>${esc(k)}</span><div class="track"><div class="fillb" style="width:${v / tot * 100}%"></div></div><span class="r">${Math.round(v / tot * 100)}%</span></div>`).join("") || '<div class="dim">—</div>';
}

async function openKol(name) {
  KOL_SEL = name;
  let d; try { d = await j("/api/kols/" + encodeURIComponent(name)); } catch { return; }
  const k = d.kol, p = d.profile || {}, s = p.stats || {}, x = p.x || {};
  $("#koldetail").dataset.status = p.status || "new";
  const pfp = k.twitter ? `/api/pfp/${encodeURIComponent(handleOf(k.twitter))}` : null;
  const tw = k.twitter && /^https?:\/\//.test(k.twitter) ? k.twitter : null;
  const head = `<div class="kd-top">${pfpHtml(pfp, k.name, true)}
    <div style="min-width:0"><div class="kd-name">${esc(x.display_name || k.name)}</div>
      ${tw ? `<a class="dim" href="${esc(tw)}" target="_blank" rel="noopener noreferrer">@${esc(handleOf(k.twitter))} ↗</a>` : '<span class="dim">no X handle</span>'}
      ${x.followers ? `<span class="dim"> · ${Number(x.followers).toLocaleString()} followers</span>` : ""}
      ${x.bio ? `<div class="kd-bio">${esc(x.bio)}</div>` : ""}
      <div class="tags" style="margin-top:6px">${tagsHtml(p.style, p.risk)}</div></div>
    <div class="kd-actions"><button id="kd-re">${p.status === "running" ? "RESEARCHING…" : "RE-RESEARCH"}</button><button id="kd-edit">EDIT WALLETS</button><button id="kd-del">DELETE</button></div></div>`;
  if (!p.stats) {
    $("#koldetail-body").innerHTML = head + `<div class="empty">${p.status === "running" || p.status === "new" ? '<span class="spin">◌</span> researching wallets… this can take a few minutes (longer without a Helius key)' : p.status === "error" ? "research failed: see THE TRACE" : "not researched yet"}</div>`;
  } else {
    const pct = (v) => v == null ? "—" : v + "%";
    $("#koldetail-body").innerHTML = head + `
    <div class="kd-grid"><div>
      <div class="sec"><span>PROFILE</span><span>${p.description_by === "claude" ? "CLAUDE" : "TEMPLATE"} · ${esc(p.data_source || "")} · ${ago(p.ts)} ago</span></div>
      <div class="kd-desc">${esc(p.description)}</div>
      <div class="sec"><span>STATS</span><span>${s.trades} swaps · ${s.positions} coins · ${s.days}d</span></div>
      <div class="grid4">
        <div><span>WIN RATE</span><b>${pct(s.win_rate)}</b></div><div><span>AVG WINNER</span><b>${s.avg_win_x ? s.avg_win_x + "x" : "—"}</b></div>
        <div><span>MEDIAN LOSER</span><b>${s.median_loss_pct != null ? s.median_loss_pct + "%" : "—"}</b></div>
        <div><span>TOTAL PNL</span><b class="${(s.total_pnl_sol || 0) >= 0 ? "pos" : "neg"}">${s.total_pnl_sol ?? "—"} SOL</b></div>
        <div><span>MEDIAN SIZE</span><b>${s.median_size_sol ?? "—"} SOL</b></div><div><span>ENTRY MCAP</span><b>${usd(s.median_entry_mcap)}</b></div>
        <div><span>ENTRY AFTER LAUNCH</span><b>${dur(s.median_entry_age_s)}</b></div><div><span>TRADES / DAY</span><b>${s.trades_per_day ?? "—"}</b></div>
        <div><span>HOLD (ALL)</span><b>${dur(s.median_hold_s)}</b></div><div><span>HOLD WINNERS</span><b>${dur(s.median_hold_win_s)}</b></div>
        <div><span>HOLD LOSERS</span><b>${dur(s.median_hold_loss_s)}</b></div><div><span>SCALES OUT</span><b>${pct(s.scale_out_pct)}</b></div>
        <div><span>OPEN BAGS -50%</span><b>${pct(s.bag_hold_pct)}</b></div><div><span>SIZE CONSISTENCY</span><b>${s.size_cv == null ? "—" : s.size_cv < 0.5 ? "steady" : "erratic"}</b></div>
        <div><span>ACTIVE (UTC)</span><b>${(s.active_hours_utc || []).map(h => h + ":00").join(" ") || "—"}</b></div>
        <div><span>REALIZED</span><b class="${(s.realized_pnl_sol || 0) >= 0 ? "pos" : "neg"}">${s.realized_pnl_sol ?? "—"} SOL</b></div></div>
      <div class="sec"><span>WALLETS</span><span>${(p.wallets || []).length}</span></div>
      <table class="ktable"><tr><th>ROLE</th><th>ADDRESS</th><th>COINS</th><th>WIN</th><th>SIZE</th><th>PNL</th></tr>
        ${(p.wallets || []).map(w => `<tr><td>${w.role.toUpperCase()}</td><td><a href="https://solscan.io/account/${esc(w.address)}" target="_blank" rel="noopener noreferrer">${esc(w.address.slice(0, 4))}…${esc(w.address.slice(-4))}</a></td>
          <td>${w.stats.positions ?? 0}</td><td>${pct(w.stats.win_rate)}</td><td>${w.stats.median_size_sol ?? "—"}</td>
          <td class="${(w.stats.total_pnl_sol || 0) >= 0 ? "pos" : "neg"}">${w.stats.total_pnl_sol ?? "—"}</td></tr>`).join("")}</table>
      ${(p.side_insights || []).length ? `<div class="sec"><span>SIDE WALLETS</span></div><ul class="cx-list">${p.side_insights.map(i => `<li style="color:#ff9de6">${esc(i)}</li>`).join("")}</ul>` : ""}
    </div><div>
      <div class="sec"><span>WHAT THEY BUY · ENTRY MCAP</span></div>${mixHtml(s.entry_mcap_mix)}
      <div class="sec"><span>HOW EARLY · AFTER LAUNCH</span></div>${mixHtml(s.entry_age_mix)}
      <div class="sec"><span>THEMES</span></div>${mixHtml(s.themes)}
      <div class="sec"><span>LAUNCHPADS</span></div>${mixHtml(s.launchpads)}
      <div class="sec"><span>BEST / WORST</span></div>
      ${[...(s.best || []), ...(s.worst || [])].map(b => `<div class="kv"><span>$${esc(b.symbol)}</span><b class="${b.pnl_sol >= 0 ? "pos" : "neg"}">${b.pnl_sol} SOL · ${b.multiple ?? "—"}x</b></div>`).join("") || '<div class="dim">—</div>'}
      <div class="sec"><span>RECENT BUYS</span></div>
      <table class="ktable"><tr><th>COIN</th><th>ENTRY</th><th>SIZE</th><th>X</th><th>HOLD</th></tr>
        ${(p.recent || []).map(r => `<tr><td>$${esc(r.symbol)}</td><td>${usd(r.entry_mcap)}</td><td>${r.invested_sol}</td>
          <td class="${(r.multiple || 0) >= 1 ? "pos" : "neg"}">${r.multiple ?? "—"}${r.closed ? "" : " ·open"}</td><td>${dur(r.hold_s)}</td></tr>`).join("")}</table>
    </div></div>`;
  }
  $("#kd-re").onclick = () => kolPost(`/api/kols/${encodeURIComponent(name)}/research`, "POST");
  $("#kd-del").onclick = () => { if (confirm(`Delete ${name} and their profile?`)) kolPost(`/api/kols/${encodeURIComponent(name)}`, "DELETE", () => { KOL_SEL = null; zoomOut(); }); };
  $("#kd-edit").onclick = () => openKolForm(k);
}

async function kolPost(url, method, after) {
  const pw = await adminPw(); if (pw === null) return;
  const r = await fetch(url, { method, headers: { "x-admin-password": pw } });
  if (!r.ok) { alert(r.status === 403 ? "Wrong admin password." : "Request failed (" + r.status + ")"); ADMIN_PW = ""; return; }
  after && after(); setTimeout(loadKols, 500); setTimeout(() => ZOOMED && openKol(ZOOMED), 800);
}

async function adminPw() {
  if (!SETTINGS || !SETTINGS.needs_password) return "";
  if (ADMIN_PW) return ADMIN_PW;
  const v = prompt("Admin password (the one you chose when you deployed):");
  if (v === null) return null; ADMIN_PW = v; return v;
}

function openKolForm(k) {
  const f = $("#koladd-form"); f.reset();
  if (k) { f.name.value = k.name; f.twitter.value = k.twitter || ""; f.wallet.value = k.wallet || ""; f.side_wallets.value = (k.side_wallets || []).join("\n"); }
  $("#kol-pw").hidden = !(SETTINGS && SETTINGS.needs_password); $("#kol-msg").textContent = "";
  $("#koladd").showModal();
}
$("#btn-addkol").onclick = () => openKolForm(null);
$("#koladd-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "cancel") return;
  e.preventDefault(); const f = e.target;
  if (f.admin.value) ADMIN_PW = f.admin.value;
  $("#kol-msg").textContent = "saving…";
  try {
    const r = await fetch("/api/kols", { method: "POST", headers: { "content-type": "application/json", "x-admin-password": ADMIN_PW },
      body: JSON.stringify({ name: f.name.value, twitter: f.twitter.value, wallet: f.wallet.value, side_wallets: f.side_wallets.value }) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { $("#kol-msg").textContent = r.status === 403 ? "Wrong admin password." : (out.detail || "Save failed (" + r.status + ")"); return; }
    $("#koladd").close(); const nm = f.name.value.trim(); await loadKols(); setTimeout(() => zoomTo(nm), 300);
  } catch (err) { $("#kol-msg").textContent = "Save failed: " + err; }
});
loadKols(); setInterval(loadKols, 6000);

// ---- coin popover (Claude-style menu card) ------------------------------------------
function coinImg(url, sym, big) {
  const ini = esc((sym || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?");
  return url && /^https:\/\//.test(url)
    ? `<img class="cimg${big ? " big" : ""}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;cimg${big ? " big" : ""}&quot;>${ini}</div>'">`
    : `<div class="cimg${big ? " big" : ""}">${ini}</div>`;
}
let POP_MINT = null, POP_ACTIONS = {};
async function openPop(mint, anchor) {
  const pop = $("#coinpop");
  let c; try { c = await j("/api/coin/" + encodeURIComponent(mint)); } catch { return; }
  POP_MINT = mint;
  const status = c.is_real === true ? '<span class="pill real">Real</span>' : c.is_real === false ? '<span class="pill fake">Fake</span>' : '<span class="pill wait">Analyzing</span>';
  const link = (u) => u && /^https?:\/\//.test(u) ? u : null;
  const items = [
    ["P", "◎", "Open in pump.fun", "https://pump.fun/coin/" + c.mint],
    ["D", "▦", "Open in DexScreener", link(c.dex_url) || "https://dexscreener.com/solana/" + c.mint],
    ["S", "⌕", "Open on Solscan", "https://solscan.io/token/" + c.mint],
    ["X", "𝕏", "Open X / Twitter", link(c.twitter) || link(c.twitter_dex)],
    ["W", "↗", "Open website", link(c.website)],
  ].filter(i => i[3]);
  POP_ACTIONS = Object.fromEntries(items.map(i => [i[0].toLowerCase(), () => window.open(i[3], "_blank", "noopener")]));
  POP_ACTIONS.c = async () => { try { await navigator.clipboard.writeText(c.mint); $("#pop-copy").textContent = "Copied!"; } catch { prompt("Contract address:", c.mint); } };
  POP_ACTIONS.f = () => { closePop(); $("#coinfile").scrollIntoView({ behavior: "smooth", block: "start" }); };
  const sig = Object.values(c.signals || {}).map(s => `<span class="pill ${s.passed === true ? "real" : s.passed === false ? "fake" : "wait"}" title="${esc(s.note)}" style="margin:0">${esc(s.label)}</span>`).join("");
  const stat = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
  pop.innerHTML = `<div class="pop-head">${coinImg(c.image, c.symbol, true)}
      <div style="min-width:0"><b>${esc(c.name || c.symbol)}</b><span>$${esc(c.symbol)} · ${esc(CAT_NICE[c.primary_catalyst] || "Classifying…")}</span>${status}</div>
      <button class="pop-x" aria-label="Close" onclick="closePop()">✕</button></div>
    <div class="pop-stats">${stat("ATH", usd(c.ath_mcap))}${stat("Now", usd(c.usd_market_cap))}
      ${stat("To $100k", mins(c.time_to_100k_min))}${stat("Age", ago(c.created_ts))}
      ${stat("Holders", c.holders != null ? Number(c.holders).toLocaleString() : "—")}${stat("Top 10", c.top10_pct != null ? c.top10_pct + "%" : "—")}
      ${stat("Traders", c.unique_traders != null ? Number(c.unique_traders).toLocaleString() : "—")}${stat("Vol 24h", usd(c.volume_24h))}</div>
    ${(c.why || c.why_template || []).length ? `<div class="pop-sep"></div><div class="pop-sec">Why it ran</div><ul class="pop-why">${(c.why || c.why_template).map(b => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
    ${sig ? `<div class="pop-sec">Checks</div><div class="pop-sigs">${sig}</div>` : ""}
    <div class="pop-sep"></div>
    ${items.map(i => `<a class="pop-item" href="${esc(i[3])}" target="_blank" rel="noopener noreferrer"><span class="ic">${i[1]}</span>${i[2]}<kbd>${i[0]}</kbd></a>`).join("")}
    <button class="pop-item" onclick="POP_ACTIONS.c()"><span class="ic">⧉</span><span id="pop-copy">Copy contract address</span><kbd>C</kbd></button>
    <div class="pop-sep"></div>
    <button class="pop-item" onclick="POP_ACTIONS.f()"><span class="ic">≡</span>Show full coin file<kbd>F</kbd></button>`;
  pop.hidden = false;
  // place next to the row like a context menu: left of the list if there's room, else below
  const r = anchor.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight, vw = innerWidth, vh = innerHeight;
  let x = r.left - pw - 10, y = r.top - 8;
  if (x < 8) { x = Math.min(r.left, vw - pw - 8); y = r.bottom + 6; }
  y = Math.max(8, Math.min(y, vh - ph - 8));
  pop.style.left = x + "px"; pop.style.top = y + "px";
  pop.querySelector(".pop-item")?.focus({ preventScroll: true });
}
function closePop() { $("#coinpop").hidden = true; POP_MINT = null; }
document.addEventListener("click", (e) => { if (POP_MINT && !e.target.closest("#coinpop")) closePop(); });
document.addEventListener("keydown", (e) => {
  if (!POP_MINT || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "Escape") { closePop(); return; }
  const fn = POP_ACTIONS[e.key.toLowerCase()]; if (fn) { e.preventDefault(); fn(); }
});
window.addEventListener("scroll", () => POP_MINT && closePop(), { passive: true });
