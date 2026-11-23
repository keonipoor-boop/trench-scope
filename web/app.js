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
// THE TAPE: REAL (default) / FAKE / ANALYZING / ALL tabs
let TAPE_F = "real";
try { const v = localStorage.getItem("tapef"); if (["real", "fake", "pending", "all"].includes(v)) TAPE_F = v; } catch { /* ignore */ }
function renderTapeTabs() {
  const n = (STATE && STATE.tape_counts) || {};
  const tabs = [["real", "Real", "real"], ["fake", "Fake", "fake"], ["pending", "Analyzing", "wait"], ["all", "All", ""]];
  $("#tapetabs").innerHTML = tabs.map(([k, l, cls]) => `<button role="tab" aria-selected="${k === TAPE_F}" data-f="${k}">
    ${cls ? `<span class="pill ${cls}" style="margin:0 5px 0 0">${l}</span>` : l + " "}<b>${n[k] ?? "—"}</b></button>`).join("");
  document.querySelectorAll("#tapetabs button").forEach(b => b.onclick = () => {
    TAPE_F = b.dataset.f; try { localStorage.setItem("tapef", TAPE_F); } catch { /* ignore */ }
    seenTape = new Set(); renderTapeTabs(); refresh();
  });
}
// ---- timeframe (chart-style TF bar) -------------------------------------------------
const TFS = ["7D", "3D", "24H", "12H", "6H", "4H", "1H"];  // 1H is the shortest window
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
// short-lived cache for popover data: clicks render instantly, hovering prefetches
const _CACHE = new Map();
function cj(url, ttl = 20000) {
  const hit = _CACHE.get(url);
  if (hit && Date.now() - hit.t < ttl) return hit.p;
  const p = j(url).catch(e => { _CACHE.delete(url); throw e; });
  _CACHE.set(url, { t: Date.now(), p });
  return p;
}
const peek = (url) => { const h = _CACHE.get(url); return h && h.done ? h.v : null; };
function cjTrack(url, ttl) { const p = cj(url, ttl); const h = _CACHE.get(url); if (h && !h.tracked) { h.tracked = 1; p.then(v => { h.done = 1; h.v = v; }, () => {}); } return p; }
const prefetchCoin = (mint) => cjTrack("/api/coin/" + encodeURIComponent(mint), 30000);

async function refresh() {
  try { STATE = await j("/api/state?tf=" + encodeURIComponent(TF) + "&tape_filter=" + TAPE_F); } catch (e) { $("#livewire").textContent = "backend unreachable: " + e; return; }
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
  renderTapeTabs();
  const t = STATE.tape;
  if (!t.length) {
    const scout = STATE.agents.find(a => a.name === "SCOUT") || {};
    const msg = scout.status === "running" ? `scanning… ${esc(scout.detail || "")}<br><br><span class="dim">first scan takes ~5–10 min · watch THE TRACE below</span>`
      : TAPE_F !== "all" && (STATE.tape_counts || {}).all ? `no ${{ real: "real", fake: "fake", pending: "still-analysing" }[TAPE_F]} runners in the last ${TF}<br><br><span class='dim'>${(STATE.tape_counts || {}).all} runners in total: see the other tabs</span>`
      : STATE.last_scan ? `no coins crossed $100k in the last ${TF}${tfSecs(TF) < 86400 ? "<br><br><span class='dim'>try a longer timeframe</span>" : " yet · next scan soon"}` : "waiting for the first scan…";
    $("#book").innerHTML = `<div class="empty">${msg}</div>`; $("#tape-hdr").textContent = ""; return;
  }
  const max = Math.max(...t.map(c => c.ath_mcap || 0));
  $("#t-last").textContent = usd(t[0].ath_mcap);
  $("#t-lastsym").textContent = `$${t[0].symbol} · ${ago(t[0].ts_100k || t[0].created_ts)} ago`;
  $("#tape-hdr").textContent = `${t.length} latest`;
  $("#book").innerHTML = t.map(c => coinRowHtml(c, { sel: c.mint === selected, isNew: seenTape.size && !seenTape.has(c.mint) })).join("");
  t.forEach(c => seenTape.add(c.mint));
  document.querySelectorAll("#book .coin-row").forEach(el => {
    el.onmouseenter = () => prefetchCoin(el.dataset.mint);
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
    // a 429 (rate limit) with a recent success is "LIMITED" (slowing down), not broken
    const limited = !s.ok && /429|rate|Too Many/i.test(s.last_error || "") && s.last_ok_ts && Date.now() / 1000 - s.last_ok_ts < 1800;
    const cls = s.ok ? "ok" : limited ? "lim" : "bad";
    const st = s.ok ? ["#39ff88", "● OK"] : limited ? ["#ffd23d", "● LIMITED"] : ["#ff4d6d", "● ERR"];
    return `<div class="src ${cls}" data-src="${esc(s.name)}" tabindex="0" style="cursor:pointer" title="${esc(s.last_error || "")}">
      <b><img class="srcico" src="/api/srcicon/${encodeURIComponent(s.name)}" alt="" onerror="this.remove()">${esc(s.name)}</b><span class="st" style="color:${st[0]}">${st[1]}</span>
      <span class="sub">${esc(s.agent)} · ${s.calls} calls · ${s.latency_ms ? Math.round(s.latency_ms) + "ms" : "—"}${!s.ok && s.last_error ? " · " + esc(s.last_error) : ""}</span></div>`;
  }).join("") : `<div class="src off"><b>no source calls yet</b><span></span><span class="sub">run a scan, or python run.py doctor</span></div>`;
  document.querySelectorAll("#sources .src[data-src]").forEach(el => el.onclick = (e) => { e.stopPropagation(); sourcePop(el.dataset.src, el); });
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
  // a calm, readable board: one row per narrative, sorted by share, with a status pill and a trend chip
  const th = STATE.themes, all = th.themes.map(t => t.theme), noBase = th.daily.no_baseline;
  $("#rot-sub").textContent = `— who's leading the last ${TF}, and who's heating up in the last ${tfWords(th.tail_s)}`;
  const rows = th.themes.filter(t => t.runners || t.h_runners || t.launches > 5).sort((a, b) => b.share - a.share || b.runners - a.runners);
  if (!rows.length) { $("#rotlist").innerHTML = '<div class="empty">no narratives with runners in this window</div>'; return; }
  const avg = 100 / Math.max(1, rows.filter(r => r.runners).length), maxShare = Math.max(1, ...rows.map(r => r.share));
  const status = (r) => {
    const big = r.share >= avg, up = noBase ? r.h_share >= r.share : r.momentum >= 1;
    return big && up ? ["Leading", "lead"] : !big && up ? ["Improving", "impr"] : big ? ["Weakening", "weak"] : ["Lagging", "lag"];
  };
  const trend = (r) => {
    // heating up = a bigger share of runners in the last stretch than over the whole window
    const d = r.h_share - r.share;
    if (!r.runners && !r.h_runners) return '<span class="trend flat">—</span>';
    if (Math.abs(d) < 5) return '<span class="trend flat">steady</span>';
    return d > 0 ? `<span class="trend up" title="bigger share of runners lately">▲ heating</span>` : `<span class="trend down" title="smaller share of runners lately">▼ cooling</span>`;
  };
  $("#rotlist").innerHTML = rows.map(r => {
    const col = window.Synapse.PALETTE[all.indexOf(r.theme) % window.Synapse.PALETTE.length], [st, cls] = status(r);
    return `<button class="rot-row" data-theme="${esc(r.theme)}">
      <span class="rot-dot" style="background:${col};box-shadow:0 0 8px ${col}"></span>
      <span class="rot-name"><b>${esc(r.theme)}</b><span>${r.runners} runner${r.runners === 1 ? "" : "s"} · ${r.hit_rate}% hit rate</span></span>
      <span class="rot-bar"><i style="width:${r.share / maxShare * 100}%;background:${col}"></i><em>${r.share}%</em></span>
      <span class="rot-st ${cls}">${st}</span>${trend(r)}</button>`;
  }).join("");
  document.querySelectorAll("#rotlist .rot-row").forEach(b => b.onclick = (e) => { e.stopPropagation(); themePop(b.dataset.theme, b); });
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
  $("#coin").innerHTML = `<div class="cf-top"><img src="/api/img/${encodeURIComponent(c.mint)}" alt="" onerror="this.remove()">
      <div style="flex:1;min-width:0"><div class="cf-sym">$${esc(c.symbol)} ${badge}</div><div class="cf-name">${esc(c.name)} · ${esc(c.mint.slice(0, 6))}…${esc(c.mint.slice(-6))}${c.demo ? " · demo" : ""}</div></div>
      <div style="text-align:right"><div class="midnum" style="color:#39ff88">${usd(c.ath_mcap)}</div><div class="dim">ATH · now ${usd(c.usd_market_cap)}</div></div></div>
    ${fakeBlock(c)}
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
window.addEventListener("resize", () => { if (STATE) drawTapeChart(); });

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
  for (const k of Object.keys(SETTINGS.keys)) {
    $("#k-" + k).textContent = SETTINGS.keys[k] ? "✓ saved & in use" : "";
    f[k].value = "";
    f[k].dataset.ph = f[k].dataset.ph || f[k].placeholder;
    f[k].placeholder = SETTINGS.keys[k] ? "•••••••••••• key saved (hidden). Leave blank to keep it" : f[k].dataset.ph;
  }
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
const KPERIODS = [["lifetime", "LIFETIME"], ["month", "MONTH"], ["week", "WEEK"], ["day", "DAY"]];
let KPERIOD = "lifetime";
try { const v = localStorage.getItem("kperiod"); if (KPERIODS.some(x => x[0] === v)) KPERIOD = v; } catch { /* ignore */ }
const solFmt = (v) => v == null ? "—" : (v > 0 ? "+" : "") + (Math.abs(v) >= 100 ? Math.round(v) : v) + " SOL";
function renderKPeriodBar() {
  let bar = $("#kperiod");
  if (!bar) {
    bar = document.createElement("div"); bar.id = "kperiod"; bar.className = "tf-btns kperiod";
    $("#kolstage").appendChild(bar);
  }
  bar.innerHTML = `<span class="kp-l">PNL</span>` + KPERIODS.map(([k, l]) => `<button aria-selected="${k === KPERIOD}" data-k="${k}">${l}</button>`).join("");
  bar.querySelectorAll("button").forEach(b => b.onclick = (e) => {
    e.stopPropagation(); KPERIOD = b.dataset.k; try { localStorage.setItem("kperiod", KPERIOD); } catch { /* ignore */ }
    renderKPeriodBar(); for (const p of PLANETS.values()) p.sig = null; loadKols(); if (ZOOMED) openKol(ZOOMED);
  });
}
const dur = (s) => s == null ? "—" : s >= 86400 ? (s / 86400).toFixed(1) + "d" : s >= 3600 ? (s / 3600).toFixed(1) + "h" : s >= 60 ? Math.round(s / 60) + "m" : Math.round(s) + "s";
const initials = (n) => esc((n || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?");
const pfpHtml = (url, name, big) => url && /^(https:\/\/|\/api\/(pfp|img)\/)/.test(url)
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

// the same solar system shows KOLs or devs (devs populate themselves from runners' creators)
let SYS_MODE = "kol";
const DEV_COLORS = [["Hit maker", "#39ff88"], ["Fake factory", "#ff4d6d"], ["Clean runners", "#22e6ff"], ["Serial launcher", "#ff8a3d"],
  ["One-hit", "#a26bff"], ["Migrator", "#ffd23d"], ["New dev", "#f2f7ff"]];
function devToPlanet(d) {
  return { name: d.wallet, display: d.name, wallet: d.wallet, status: "done", style: d.labels,
    pfp: d.best ? "/api/img/" + d.best.mint : null, summary: { positions: d.runners * 4, win_rate: null },
    devRunners: d.runners, devReal: d.real, devFake: d.fake,
    holdings: (d.moons || []).slice(0, 5).map(m => ({ ...m, invested_sol: null, multiple: null, unrealized_sol: null })) };
}
function setSysMode(mode) {
  if (mode === SYS_MODE) return;
  SYS_MODE = mode; zoomOut();
  for (const p of PLANETS.values()) for (const el of [p.el, ...(p.moons || []).map(m => m.el)]) el.parentNode && el.parentNode.removeChild(el);
  PLANETS.clear();
  $("#kolsun").querySelector("b").textContent = mode === "dev" ? "DEV" : "KOL";
  $("#kolsun").querySelector("i").textContent = mode === "dev" ? "auto" : "+ add";
  if ($("#kperiod")) $("#kperiod").hidden = mode === "dev";
  loadKols();
}

async function loadKols() {
  if (SYS_MODE === "dev") {
    try { KOLS = (await j("/api/devs?limit=14")).map(devToPlanet); } catch { return; }
  } else {
    try { KOLS = await j("/api/kols"); } catch { return; }
  }
  $("#kolcount").textContent = SYS_MODE === "dev" ? `${KOLS.length} devs` : `${KOLS.length} tracked`;
  if (!$("#kperiod")) renderKPeriodBar();
  if ($("#kperiod")) $("#kperiod").hidden = SYS_MODE === "dev";
  const host = $("#kolplanets"), names = new Set(KOLS.map(k => k.name));
  for (const [n, p] of PLANETS) if (!names.has(n)) {
    for (const el of [p.el, ...(p.moons || []).map(m => m.el)]) el.parentNode && el.parentNode.removeChild(el);
    PLANETS.delete(n);
  }
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
    const sig = JSON.stringify([k.status, k.style, k.pfp, k.summary, k.periods, KPERIOD]);
    if (p.sig !== sig) {
      p.sig = sig;
      p.el.style.width = p.el.style.height = p.size + "px";
      p.el.style.setProperty("--pc", k.status === "running" ? "#22e6ff" : SYS_MODE === "dev"
        ? ((DEV_COLORS.find(([l]) => (k.style || []).includes(l)) || [0, "#c8d6e2"])[1]) : styleColor(k.style));
      p.el.classList.toggle("running", k.status === "running" || k.status === "new");
      p.el.title = `${k.name} · ${(k.style || []).join(", ") || k.status}`;
      const per = (k.periods || {})[KPERIOD];
      const pnl = SYS_MODE === "dev" ? `<span style="color:#7ee2a8"> · ${k.devRunners} runner${k.devRunners === 1 ? "" : "s"}</span>`
        : per ? `<span style="color:${(per.realized_sol ?? per.pnl_sol) >= 0 ? "#7ee2a8" : "#ff9aa9"}"> · ${solFmt(per.realized_sol ?? per.pnl_sol)}${KPERIOD === "lifetime" && k.history_complete === false ? "…" : ""}</span>` : "";
      p.el.innerHTML = pfpHtml(k.pfp, k.display || k.name) + `<span class="pl-name">${esc(k.display || k.name)}${pnl}</span>`;
    }
    syncMoons(p, k, host);
  });
  $("#kollegend").innerHTML = SYS_MODE === "dev"
    ? DEV_COLORS.map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("") + '<span style="--c:#5d7183">moons = their runners · found automatically</span>'
      + (KOLS.length ? "" : '<span style="--c:#ff3df2">devs appear as their coins run</span>')
    : STYLE_COLORS.map(([k, c]) => `<span style="--c:${c}">${k === "hunter" ? "Narrative hunter" : k}</span>`).join("") +
      (KOLS.length ? "" : '<span style="--c:#ff3df2">click the core to add your first KOL</span>');
  if (ZOOMED && !PLANETS.has(ZOOMED)) zoomOut();
  if (ZOOMED && $("#koldetail").dataset.status === "running" && KOLS.find(k => k.name === ZOOMED)?.status !== "running") openKol(ZOOMED);
}

// moons = coins the KOL is holding right now (open positions), with live hold age
const holdAge = (h) => h.first_buy_ts ? dur(Date.now() / 1000 - h.first_buy_ts) : "—";
function syncMoons(p, k, host) {
  const hs = k.holdings || [], sig = JSON.stringify(hs.map(h => [h.mint, h.image, h.multiple]));
  if (p.moonSig !== sig) {
    for (const m of p.moons || []) m.el.parentNode && m.el.parentNode.removeChild(m.el);
    p.moons = hs.map((h, i) => {
      const el = document.createElement("div");
      el.className = "kol-moon"; el.dataset.planet = k.name;
      el.innerHTML = coinImg(h.image, h.symbol, false, h.mint) + `<span class="mn-age"></span>`;
      el.onclick = (e) => { e.stopPropagation(); SYS_MODE === "dev" ? openPop(h.mint, el) : holdingPop(k, h, el); };
      el.onmouseenter = () => { HOVER = true; }; el.onmouseleave = () => { HOVER = false; };
      host.appendChild(el);
      return { el, h, phase: (i / Math.max(1, hs.length)) * Math.PI * 2 };
    });
    p.moonSig = sig;
  }
  for (const m of p.moons || []) {  // refresh the ticking hold age + tooltip
    const h = m.h, x = h.multiple != null ? ` · ${h.multiple}x` : "";
    m.el.querySelector(".mn-age").textContent = holdAge(h);
    m.el.title = SYS_MODE === "dev" ? `$${h.symbol} · launched ${holdAge(h)} ago · peak ${usd(h.ath)}`
      : `$${h.symbol} · holding ${holdAge(h)} · ${h.invested_sol} SOL in${x}`;
  }
}
setInterval(() => { for (const p of PLANETS.values()) for (const m of p.moons || []) m.el.querySelector(".mn-age").textContent = holdAge(m.h); }, 15000);

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
    const moons = p.moons || [], cx = x + p.size / 2, cy = y + p.size / 2;
    const mt = ZOOMED ? (p.frozenT ?? t) : t;
    moons.forEach((m, i) => {
      const ms = 22, orbit = p.size * 0.5 * sc + 22 + (i % 2) * 14;  // two staggered moon lanes
      const a = m.phase + mt * (0.55 - (i % 2) * 0.15);
      // wide, flat orbit: moons swing out to the sides so they don't sit on the name label below the planet
      const mx = cx + Math.cos(a) * orbit * 1.4 - ms / 2, my = cy - 8 + Math.sin(a) * orbit * 0.34 - ms / 2;
      m.el.style.transform = `translate(${mx}px, ${my}px)`;
      m.el.style.zIndex = Math.sin(a) > 0 ? (depth > 0 ? 4 : 2) : (depth > 0 ? 2 : 0);   // behind the planet on the far side
    });
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
  document.querySelectorAll(".kol-moon").forEach(el => el.classList.toggle("of-sel", el.dataset.planet === name));
  const { x, y } = planetPos(p, t), z = narrow ? 1.6 : 2.2;
  const cx = x + p.size / 2, cy = y + p.size / 2;
  const tx = (narrow ? W / 2 : W * 0.18) - cx * z, ty = (narrow ? H * 0.16 : H / 2) - cy * z;
  $("#kolspace").style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
  st.classList.add("zoomed");
  const pre = cj((SYS_MODE === "dev" ? "/api/devs/" : "/api/kols/") + encodeURIComponent(name), 5000);  // fetch while the camera moves
  setTimeout(() => { $("#koldetail").hidden = false; (SYS_MODE === "dev" ? openDev : openKol)(name, pre); }, 220);
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
$("#kolsun").onclick = (e) => { e.stopPropagation(); if (SYS_MODE === "kol") openKolForm(null); };
requestAnimationFrame(spaceFrame);

function historyLine(h) {
  if (!h) return "";
  const since = h.since_ts ? new Date(h.since_ts * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "—";
  return `<div class="pnl-hist ${h.complete ? "ok" : ""}">${h.complete ? "✓ Full wallet history" : "◌ Backfilling wallet history"} · ${Number(h.swaps || 0).toLocaleString()} swaps · back to ${since}
    ${h.complete ? "" : " · lifetime numbers grow as older trades load (about 3,000 swaps per wallet per hour)"}</div>`;
}

function pnlCard(p) {
  const per = (p.periods || {})[KPERIOD];
  if (!per) return "";
  const lbl = KPERIODS.find(x => x[0] === KPERIOD)[1];
  const start = { lifetime: 0, month: 30 * 86400, week: 7 * 86400, day: 86400 }[KPERIOD];
  const now = Date.now() / 1000;
  // pnl_series = realized PnL per sell [ts, pnl, symbol]: the chart is the running sum inside the period
  const rows = (p.pnl_series || []).filter(r => !start || r[0] >= now - start);
  let cum = 0; const pts = rows.map(r => [r[0], (cum += r[1])]);
  const W = 560, H = 90;
  let path = "", area = "";
  if (pts.length > 1) {
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1, lo = Math.min(0, ...pts.map(x => x[1])), hi = Math.max(0, ...pts.map(x => x[1]));
    const X = (t) => (t - t0) / Math.max(1, t1 - t0) * W, Y = (v) => H - 6 - (v - lo) / Math.max(1e-9, hi - lo) * (H - 12);
    path = pts.map((q, i) => (i ? "L" : "M") + X(q[0]).toFixed(1) + " " + Y(q[1]).toFixed(1)).join(" ");
    area = `M0 ${Y(0).toFixed(1)} ` + pts.map(q => "L" + X(q[0]).toFixed(1) + " " + Y(q[1]).toFixed(1)).join(" ") + ` L${W} ${Y(0).toFixed(1)} Z`;
    area = `<line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="#ffffff22" stroke-dasharray="3 4"/><path d="${area}" fill="${cum >= 0 ? "#39ff8818" : "#ff4d6d18"}"/>`;
  }
  // headline = REALIZED PnL (what wallet trackers like Axiom / kolscan show); unrealized is shown beside it
  const real = per.realized_sol ?? per.pnl_sol;
  const col = real >= 0 ? "#7ee2a8" : "#ff9aa9";
  const soFar = KPERIOD === "lifetime" && p.history && !p.history.complete;
  return `<div class="pnl-card">
    <div class="pnl-top"><div><span class="pnl-l">REALIZED PNL · ${lbl}${soFar ? " (SO FAR)" : ""}</span><b style="color:${col}">${solFmt(real)}</b>
      <span class="pnl-usd">${p.sol_usd ? (real >= 0 ? "+$" : "-$") + Math.round(Math.abs(real * p.sol_usd)).toLocaleString() : ""}${per.roi_pct != null ? ` · ${per.roi_pct >= 0 ? "+" : ""}${per.roi_pct}% ROI` : ""}</span>
      <span class="pnl-usd">unrealized ${solFmt(per.unrealized_sol)} · total ${solFmt(per.pnl_sol)}</span></div>
      <div class="pnl-mini"><div><span>Win rate</span><b>${per.win_rate ?? "—"}${per.win_rate != null ? "%" : ""}</b></div><div><span>Trades</span><b>${per.trades ?? "—"}</b></div>
        <div><span>Volume</span><b>${per.volume_sol ?? "—"} SOL</b></div><div><span>Best</span><b>${per.best ? "$" + esc(per.best.symbol) + " " + solFmt(per.best.pnl_sol) : "—"}</b></div></div></div>
    ${historyLine(p.history)}
    ${p.unpriced && p.unpriced.sells && !(p.history || {}).complete ? `<div class="pnl-hist">${p.unpriced.sells.toLocaleString()} sells (${p.unpriced.sol} SOL) aren't counted yet: their buys are older than the loaded history</div>` : ""}
    ${path ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="pnl-svg">${area}<path d="${path}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`
      : `<div class="dim" style="padding:8px 0">${per.positions ? "not enough closed trades to chart" : "no trades in this period"}</div>`}
  </div>`;
}

function mixHtml(obj) {
  const e = Object.entries(obj || {}); const tot = e.reduce((a, [, v]) => a + v, 0) || 1;
  return e.map(([k, v]) => `<div class="mix"><span>${esc(k)}</span><div class="track"><div class="fillb" style="width:${v / tot * 100}%"></div></div><span class="r">${Math.round(v / tot * 100)}%</span></div>`).join("") || '<div class="dim">—</div>';
}

async function openKol(name, pre) {
  KOL_SEL = name;
  let d; try { d = await (pre || j("/api/kols/" + encodeURIComponent(name))); } catch { return; }
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
    <div class="kd-actions"><span class="kd-auto">${p.status === "running" ? '<span class="spin">◌</span> researching now' : p.ts ? `auto-refreshed ${ago(p.ts)} ago<br>next in ${dur(Math.max(0, p.ts + 3600 - Date.now() / 1000))}` : "queued"}</span>
      <button id="kd-re" title="Research now instead of waiting for the hourly refresh">↻ REFRESH NOW</button><button id="kd-edit">EDIT WALLETS</button><button id="kd-del">DELETE</button></div></div>`;
  if (!p.stats) {
    $("#koldetail-body").innerHTML = head + `<div class="empty">${p.status === "running" || p.status === "new" ? '<span class="spin">◌</span> researching wallets… this can take a few minutes (longer without a Helius key)' : p.status === "error" ? "research failed: see THE TRACE" : "not researched yet"}</div>`;
  } else {
    const pct = (v) => v == null ? "—" : v + "%";
    $("#koldetail-body").innerHTML = head + `
    ${pnlCard(p)}
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
      <div class="sec"><span>HOLDING NOW</span><span>${(p.holdings || []).length} coins · as of ${ago(p.ts)} ago</span></div>
      <table class="ktable"><tr><th>COIN</th><th>HOLDING</th><th>IN</th><th>NOW</th><th>X</th></tr>
        ${(p.holdings || []).map(r => `<tr><td>${coinImg(r.image, r.symbol, false, r.mint).replace('class="cimg"', 'class="cimg" style="width:18px;height:18px;border-radius:5px;display:inline-grid;vertical-align:middle;font-size:8px;margin-right:5px"')}$${esc(r.symbol)}</td>
          <td>${holdAge(r)}</td><td>${r.invested_sol}</td><td>${r.unrealized_sol ?? "—"}</td><td class="${(r.multiple || 0) >= 1 ? "pos" : "neg"}">${r.multiple ?? "—"}</td></tr>`).join("") || '<tr><td colspan="5" class="dim">not holding anything (in the sampled history)</td></tr>'}</table>
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

// ---- HALL OF RUNNERS: runner of the day / week / month, overall + per narrative -------------------
let LD_PER = "day", LD_VIEW = "overall", LD_REAL = false;
try { const v = JSON.parse(localStorage.getItem("leaders") || "{}"); LD_PER = v.p || LD_PER; LD_VIEW = v.v || LD_VIEW; LD_REAL = !!v.r; } catch { /* ignore */ }
const ldSave = () => { try { localStorage.setItem("leaders", JSON.stringify({ p: LD_PER, v: LD_VIEW, r: LD_REAL })); } catch { /* ignore */ } };
function ldTabs() {
  document.querySelectorAll(".ld-per button").forEach(b => b.setAttribute("aria-selected", b.dataset.per === LD_PER));
  document.querySelectorAll(".ld-view button").forEach(b => b.setAttribute("aria-selected", b.dataset.view === LD_VIEW));
  $("#ld-real").checked = LD_REAL;
  $("#ld-title").textContent = LD_VIEW === "overall" ? `RUNNER OF THE ${LD_PER.toUpperCase()}` : `NARRATIVE RUNNERS OF THE ${LD_PER.toUpperCase()}`;
}
document.querySelectorAll(".ld-per button").forEach(b => b.onclick = () => { LD_PER = b.dataset.per; ldSave(); ldTabs(); loadLeaders(); });
document.querySelectorAll(".ld-view button").forEach(b => b.onclick = () => { LD_VIEW = b.dataset.view; ldSave(); ldTabs(); loadLeaders(); });
$("#ld-real").onchange = (e) => { LD_REAL = e.target.checked; ldSave(); loadLeaders(); };

async function loadLeaders() {
  let d; try { d = await cjTrack(`/api/leaders?period=${LD_PER}&real_only=${LD_REAL ? 1 : 0}`, 10000); } catch { return; }
  const per = LD_PER, since = d.tracking_since;
  const partial = since && Date.now() / 1000 - since < { hour: 3600, day: 86400, week: 7 * 86400, month: 30 * 86400 }[per]
    ? `<div class="pop-sec" style="padding:0 0 8px">Tracking since ${new Date(since * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}: this ${per} fills in as the app keeps running.</div>` : "";
  const body = $("#ld-body");
  if (!d.count) { body.innerHTML = partial + `<div class="empty">no runners this ${per}${LD_REAL ? " that pass the REAL checks" : ""} yet</div>`; return; }
  const open = (el) => el.querySelectorAll("[data-mint]").forEach(x => {
    x.onmouseenter = () => prefetchCoin(x.dataset.mint);
    x.onclick = (e) => { e.stopPropagation(); openCoin(x.dataset.mint); openPop(x.dataset.mint, x); };
  });
  if (LD_VIEW === "overall") {
    const h = d.overall[0];
    body.innerHTML = partial + `<div class="ld-wrap">
      <div class="hero" data-mint="${esc(h.mint)}">
        <div class="crown">♛ RUNNER OF THE ${per.toUpperCase()} · out of ${d.count} runners</div>
        <div class="hero-top">${coinImg(h.image, h.symbol, true, h.mint).replace('class="cimg big"', 'class="cimg huge"')}
          <div style="min-width:0"><h3>${esc(h.name || h.symbol)} ${statusPill(h)}</h3><div class="sub">$${esc(h.symbol)} · ${esc(CAT_NICE[h.primary_catalyst] || "Classifying…")}${(h.themes || []).length ? " · " + esc(h.themes.join(", ")) : ""}</div>
            <div class="ath">${usd(h.ath_mcap)}</div><div class="sub">peak market cap · now ${usd(h.usd_market_cap)}</div></div></div>
        <div class="hero-stats"><div><span>To $100k</span><b>${mins(h.time_to_100k_min)}</b></div><div><span>Holders</span><b>${h.holders != null ? Number(h.holders).toLocaleString() : "—"}</b></div>
          <div><span>Top 10</span><b>${h.top10_pct != null ? h.top10_pct + "%" : "—"}</b></div><div><span>Launched</span><b>${ago(h.created_ts)} ago</b></div></div>
        ${h.fake_headline ? `<div class="fake-box compact" style="margin:12px 0 0"><div class="fake-h">⚠ Flagged</div><div class="fake-head">${esc(h.fake_headline)}</div></div>` : ""}
        ${(h.why || []).length ? `<ul>${h.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="rank-list">${d.overall.slice(1).map((c, i) => coinRowHtml(c).replace('<div class="coin-row', `<div class="coin-row ld-r"`).replace(/(<div class="coin-row[^>]*>)/, `$1<span class="rank-n r${i + 2}">#${i + 2}</span>`)).join("")}</div></div>`;
  } else {
    const all = (STATE?.themes?.themes || []).map(t => t.theme);
    body.innerHTML = partial + `<div class="th-grid">${d.by_theme.map(t => {
      const c = t.top[0], col = window.Synapse.PALETTE[Math.max(0, all.indexOf(t.theme)) % window.Synapse.PALETTE.length];
      return `<div class="th-card"><h4><i style="background:${col};box-shadow:0 0 8px ${col}"></i>${esc(t.theme)}<em>${t.runners} runner${t.runners === 1 ? "" : "s"} · ${t.real} real</em></h4>
        <div class="th-crown" data-mint="${esc(c.mint)}">${coinImg(c.image, c.symbol, true, c.mint)}
          <div style="min-width:0"><span style="color:#ffd23d">♛ #1 of the ${per}</span><b>${esc(c.name || c.symbol)} ${statusPill(c)}</b>
            <span>$${esc(c.symbol)} · ${esc(CAT_NICE[c.primary_catalyst] || "")}</span><div class="v">${usd(c.ath_mcap)}</div></div></div>
        ${t.top.slice(1).map((x, i) => coinRowHtml(x).replace(/(<div class="coin-row[^>]*>)/, `$1<span class="rank-n r${i + 2}">#${i + 2}</span>`).replace('<div class="coin-row', '<div class="coin-row ld-r"')).join("")}
      </div>`; }).join("")}</div>`;
  }
  open(body);
}
ldTabs(); loadLeaders(); setInterval(loadLeaders, 30000);

// ---- STRATEGY LIBRARY: the AI's living model of each KOL's strategy, versioned ----------------
let LIB = [], LIB_SEL = null, LIB_VER = null;
document.querySelectorAll("#kollab .kl-tabs button").forEach(b => b.onclick = () => {
  const lib = b.dataset.tab === "library";
  document.querySelectorAll("#kollab .kl-tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
  $("#kolstage").hidden = lib; $("#libstage").hidden = !lib;
  $("#btn-addkol").hidden = b.dataset.tab === "devs";
  if (lib) loadLibrary(); else setSysMode(b.dataset.tab === "devs" ? "dev" : "kol");
});

async function openDev(wallet, pre) {
  KOL_SEL = wallet;
  $("#koldetail-body").innerHTML = `<div class="empty"><span class="spin">◌</span> studying dev ${esc(wallet.slice(0, 4))}…${esc(wallet.slice(-4))}…</div>`;
  let d; try { d = await (pre || j("/api/devs/" + encodeURIComponent(wallet))); } catch {
    $("#koldetail-body").innerHTML = '<div class="empty">couldn\'t load this dev, try again in a moment</div>'; return; }
  if (KOL_SEL !== wallet) return;
  const cell = (k, v) => `<div><span>${k}</span><b>${v}</b></div>`;
  $("#koldetail").dataset.status = "done";
  $("#koldetail-body").innerHTML = `<div class="kd-top">${coinImg(d.best?.image, d.best?.symbol || d.name, true, d.best?.mint).replace('class="cimg big"', 'class="cimg big" style="width:84px;height:84px;border-radius:50%"')}
      <div style="min-width:0"><div class="kd-name">DEV ${esc(d.name)}</div>
        <a class="dim" href="https://solscan.io/account/${esc(d.wallet)}" target="_blank" rel="noopener noreferrer">${esc(d.wallet.slice(0, 10))}… on Solscan ↗</a>
        <div class="tags" style="margin-top:6px">${(d.labels || []).map(l => `<span class="tag ${["Fake factory", "Serial launcher"].includes(l) ? "bad" : ["Hit maker", "Clean runners"].includes(l) ? "good" : "s"}">${esc(l)}</span>`).join("")}</div></div>
      <div class="kd-actions"><button id="dev-copy">COPY WALLET</button></div></div>
    <div class="kd-desc" style="margin-top:12px">${esc(d.description)}</div>
    <div class="sec"><span>TRACK RECORD</span><span>launch + graduation counts from Jupiter; runners from what Trench Scope has seen</span></div>
    <div class="grid4">${cell("LAUNCHES", d.launches)}${cell("GRADUATED", d.migrations != null ? `${d.migrations} (${d.migration_rate}%)` : "—")}
      ${cell("RUNNERS SEEN", d.runners)}${cell("RUNNER RATE", d.runner_rate != null ? d.runner_rate + "%" : "—")}
      ${cell("REAL", d.real)}${cell("FLAGGED FAKE", d.fake)}${cell("BEST PEAK", usd(d.best?.ath))}${cell("AVG PEAK", usd(d.avg_ath))}
      ${cell("LAST LAUNCH", d.last_launch_ts ? ago(d.last_launch_ts) + " ago" : "—")}${cell("THEMES", (d.themes || []).join(", ") || "—")}
      ${cell("CATALYSTS", (d.catalysts || []).map(c => CAT_NICE[c] || c).join(", ") || "—")}${cell("COINS SEEN", d.coins_seen)}</div>
    <div class="sec"><span>THEIR RUNNERS</span><span>${d.runner_coins.length}</span></div>
    <div class="clean">${d.runner_coins.map(c => coinRowHtml(c)).join("") || '<div class="dim">none yet</div>'}</div>
    ${d.other_coins.length ? `<div class="sec"><span>OTHER LAUNCHES WE'VE SEEN</span><span>${d.other_coins.length}</span></div>
      <div class="clean">${d.other_coins.map(c => coinRowHtml(c, { sub: "launched " + ago(c.created_ts) + " ago" })).join("")}</div>` : ""}`;
  $("#dev-copy").onclick = async () => { try { await navigator.clipboard.writeText(d.wallet); $("#dev-copy").textContent = "COPIED"; } catch { prompt("Dev wallet:", d.wallet); } };
  document.querySelectorAll("#koldetail-body .coin-row").forEach(el => {
    el.onmouseenter = () => prefetchCoin(el.dataset.mint);
    el.onclick = (e) => { e.stopPropagation(); openPop(el.dataset.mint, el); };
  });
}

async function loadLibrary() {
  try { LIB = await j("/api/strategies"); } catch { return; }
  const now = Date.now() / 1000;
  $("#libshelf").innerHTML = LIB.length ? LIB.map((x, i) => `<button class="codex ${x.name === LIB_SEL ? "sel" : ""}" data-name="${esc(x.name)}" style="--d:${(i % 5) * 0.7}s">
      <div class="cx-cover">${pfpHtml(x.pfp, x.name)}<span class="cx-ver">v${x.version}</span></div>
      <div class="cx-meta"><b>${esc(x.name)}</b><span>${esc(x.title || "")}</span>
        <div class="cx-tags">${x.shift.length ? '<span class="pill fake" style="margin:0">STRATEGY SHIFT</span>' : ""}
          ${now - x.ts < 86400 && x.version > 1 ? '<span class="pill wait" style="margin:0;background:#2b2147;color:#c9b8ff">UPDATED</span>' : ""}
          <span class="cx-conf"><i style="width:${x.confidence || 0}%"></i></span><em>${x.confidence || 0}% conf</em></div>
        <span class="cx-when">checked ${ago(x.checked_ts || x.ts)} ago</span></div></button>`).join("")
    : `<div class="empty" style="padding:40px 12px">no strategies yet<br><br><span class="dim">add KOLs in the solar system tab; the agent writes a strategy for each one after studying their wallets</span></div>`;
  document.querySelectorAll(".codex").forEach(el => el.onclick = () => openCodex(el.dataset.name));
  if (!LIB_SEL && LIB.length) openCodex(LIB[0].name); else if (LIB_SEL) openCodex(LIB_SEL, LIB_VER, true);
}

async function openCodex(name, ver, quiet) {
  LIB_SEL = name; LIB_VER = ver || null;
  document.querySelectorAll(".codex").forEach(el => el.classList.toggle("sel", el.dataset.name === name));
  let d; try { d = await cj("/api/strategies/" + encodeURIComponent(name), quiet ? 20000 : 3000); } catch { return; }
  const v = d.versions.find(x => x.version === ver) || d.versions[0], sp = v.spec, e = sp.entry || {}, x = sp.exit || {}, r = sp.risk || {};
  const latest = v === d.versions[0];
  const mcap = (a, b) => `${usd(a)} – ${usd(b)}`;
  const ladder = (x.tp_ladder || []), maxS = Math.max(1, ...ladder.map(l => l.sells));
  $("#libbook").innerHTML = `
    <div class="bk-head"><div><span class="bk-k">STRATEGY CODEX · ${esc(name)}</span><h3>${esc(sp.title || "")}</h3>
      <span class="dim">v${v.version}${latest ? " (current)" : ""} · formed ${ago(v.ts)} ago · last checked ${ago(v.checked_ts || v.ts)} ago · re-checked hourly · ${sp.sample?.closed ?? 0} closed trades</span></div>
      <div class="bk-conf"><b>${sp.confidence || 0}%</b><span>confidence</span></div></div>
    ${(sp.shift || []).length ? `<div class="bk-shift">⚡ <b>Strategy shift in progress.</b> The last 7 days differ from their long-run pattern: <ul>${sp.shift.map(s => `<li>${esc(s)}</li>`).join("")}</ul>If it holds, the next hourly check writes a new version.</div>` : ""}
    <div class="bk-grid">
      <section><h4>ENTRY</h4><dl>
        <dt>Market cap</dt><dd>${mcap(e.mcap_low, e.mcap_high)} <i>typ. ${usd(e.mcap_mid)}</i></dd>
        <dt>After launch</dt><dd>${dur(e.age_low_s)} – ${dur(e.age_high_s)} <i>typ. ${dur(e.age_mid_s)}</i></dd>
        <dt>Size</dt><dd>${e.size_low != null ? e.size_low.toFixed(2) + " – " + e.size_high.toFixed(2) + " SOL" : "—"} <i>typ. ${e.size_mid != null ? e.size_mid.toFixed(2) : "—"}</i></dd>
        <dt>Themes</dt><dd>${(e.themes || []).map(esc).join(", ") || "—"}</dd>
        <dt>Launchpads</dt><dd>${(e.launchpads || []).map(esc).join(", ") || "—"}</dd></dl></section>
      <section><h4>EXIT STRATEGY</h4><dl>
        <dt>First take-profit</dt><dd>${x.first_sell_x != null ? x.first_sell_x + "x" : "—"}${x.first_sell_pct != null ? ` · sells ~${x.first_sell_pct}%` : ""} <i>${dur(x.first_sell_after_s)} in</i></dd>
        <dt>One-shot exits</dt><dd>${x.single_exit_pct != null ? x.single_exit_pct + "% of trades" : "—"}</dd>
        <dt>Initials out</dt><dd>${x.initials_out_pct != null ? x.initials_out_pct + "% of winners" : "—"}</dd>
        <dt>Keeps a runner bag</dt><dd>${x.runner_bag_pct != null ? x.runner_bag_pct + "% of winners" : "—"}</dd>
        <dt>Stop-loss</dt><dd>${x.stop_loss_x != null ? Math.round((x.stop_loss_x - 1) * 100) + "%" : "—"} <i>after ${dur(x.cut_after_s)}</i></dd>
        <dt>Rides to zero</dt><dd>${x.rides_to_zero_pct != null ? x.rides_to_zero_pct + "% of losers" : "—"}</dd>
        <dt>Holds winners</dt><dd>${dur(x.win_hold_s)} <i>max ~${dur(x.max_hold_s)}</i></dd></dl>
        <div class="bk-ladder"><span class="bk-k">TAKE-PROFIT LADDER · where they sell</span>
          ${ladder.map(l => `<div class="lad"><span>${esc(l.band)}</span><div><i style="width:${l.sells / maxS * 100}%"></i></div><em>${l.sells} sells · ~${l.avg_frac}% of bag</em></div>`).join("") || '<div class="dim">no profitable sells yet</div>'}</div></section>
      <section><h4>RISK</h4><dl>
        <dt>Win rate</dt><dd>${r.win_rate != null ? r.win_rate + "%" : "—"}</dd><dt>Avg winner</dt><dd>${r.avg_win_x ? r.avg_win_x + "x" : "—"}</dd>
        <dt>Median loser</dt><dd>${r.median_loss_pct != null ? r.median_loss_pct + "%" : "—"}</dd>
        <dt>Sizing</dt><dd>${r.size_cv == null ? "—" : r.size_cv < 0.5 ? "consistent" : "erratic"}</dd>
        <dt>Trades / day</dt><dd>${r.trades_per_day ?? "—"}</dd><dt>Open bags -50%</dt><dd>${r.bag_hold_pct != null ? r.bag_hold_pct + "%" : "—"}</dd></dl>
        <div class="tags" style="margin-top:8px">${tagsHtml(sp.style, sp.risk_labels)}</div></section>
    </div>
    <section class="bk-play"><h4>PLAYBOOK <i>${sp.playbook_by === "claude" ? "written by Claude" : "auto-written"}</i></h4><div>${esc(sp.playbook || (sp.rules || []).map(r => "• " + r).join("\n"))}</div></section>
    <section class="bk-hist"><h4>VERSION HISTORY <i>a new version is written only when their strategy actually changes</i></h4>
      ${d.versions.map(h => `<button class="ver ${h === v ? "sel" : ""}" data-v="${h.version}"><b>v${h.version}</b><span>${new Date(h.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
        <ul>${h.changes.map(c => `<li>${esc(c)}</li>`).join("")}</ul></button>`).join("")}</section>`;
  document.querySelectorAll("#libbook .ver").forEach(b => b.onclick = () => openCodex(name, +b.dataset.v));
}
setInterval(() => { if (!$("#libstage").hidden) loadLibrary(); }, 30000);

// ---- coin popover (Claude-style menu card) ------------------------------------------
function fakeBlock(c, compact) {
  const f = c.fake || {};
  if (c.is_real !== false || !f.headline) return "";
  const m = f.metrics || {}, n = (v) => v == null ? "—" : Number(v).toLocaleString();
  const cell = (k, v, bad) => `<div><span>${k}</span><b${bad ? ' style="color:#ff9aa9"' : ""}>${v}</b></div>`;
  const failed = new Set(f.failed || []);
  return `<div class="fake-box${compact ? " compact" : ""}">
    <div class="fake-h">⚠ Why it's flagged fake</div>
    <div class="fake-head">${esc(f.headline)}</div>
    <ul>${(f.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul>
    <div class="fake-grid">
      ${cell("Volume 24h", usd(m.volume_24h))}${cell("Fees paid", m.fees_sol != null ? m.fees_sol + " SOL" : "—", failed.has("fees"))}
      ${cell("Fees / volume", m.fee_volume_pct != null ? m.fee_volume_pct + "%" : "—", failed.has("fees"))}${cell("Volume paying fees", m.fee_paying_volume_pct != null ? m.fee_paying_volume_pct + "%" : "—", failed.has("fees"))}
      ${cell("Holders", n(m.holders), failed.has("holders"))}${cell("Top 10 hold", m.top10_pct != null ? m.top10_pct + "%" : "—", failed.has("top10"))}
      ${cell("Traders", n(m.unique_traders), failed.has("traders"))}${cell("Volume / trader", m.volume_per_trader != null ? usd(m.volume_per_trader) : "—", failed.has("wash"))}
      ${cell("Round-trip wallets", m.round_trip_pct != null ? m.round_trip_pct + "%" : "—", failed.has("wash"))}${cell("Same-size trades", m.size_mode_pct != null ? m.size_mode_pct + "%" : "—", failed.has("wash"))}
      ${cell("Staircase", m.staircase_score != null ? m.staircase_score : "—", failed.has("chart"))}${cell("Cliff dump", m.cliff_dump == null ? "—" : m.cliff_dump ? "yes" : "no", failed.has("chart") && m.cliff_dump)}
    </div></div>`;
}

function statusPill(c) {
  return c.is_real === true ? '<span class="pill real">Real</span>' : c.is_real === false ? '<span class="pill fake">Fake</span>' : '<span class="pill wait">Analyzing</span>';
}
function coinRowHtml(c, o = {}) {
  const sub = o.sub || [c.primary_catalyst ? CAT_NICE[c.primary_catalyst] || c.primary_catalyst : "Classifying…",
                        c.holders ? Number(c.holders).toLocaleString() + " holders" : null].filter(Boolean).join(" · ");
  return `<div class="coin-row ${o.sel ? "sel" : ""} ${o.isNew ? "new" : ""}" data-mint="${esc(c.mint)}" tabindex="0" role="option">
    ${coinImg(c.image, c.symbol, false, c.mint)}
    <div class="cn"><b>${esc(c.name || c.symbol)}${statusPill(c)}</b><span>$${esc(c.symbol)} · ${esc(sub)}</span></div>
    <div class="cv"><b>${usd(c.ath_mcap)}</b><span>now ${usd(c.usd_market_cap)}${c.time_to_100k_min != null ? " · " + mins(c.time_to_100k_min) : ""}</span></div></div>`;
}
function coinImg(url, sym, big, mint) {
  const ini = esc((sym || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?");
  // with a mint, the server fetches + caches the picture (own link -> Jupiter -> DexScreener CDN)
  if (mint && /^[1-9A-HJ-NP-Za-km-z]{32,48}$/.test(mint)) url = "https://local/api/img/" + mint;
  url = url && url.replace("https://local/api/img/", "/api/img/");
  return url && /^(https:\/\/|\/api\/img\/)/.test(url)
    ? `<img class="cimg${big ? " big" : ""}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=&quot;cimg${big ? " big" : ""}&quot;>${ini}</div>'">`
    : `<div class="cimg${big ? " big" : ""}">${ini}</div>`;
}
let POP_MINT = null, POP_ACTIONS = {}, POP_ANCHOR = null;
async function openPop(mint, anchor, back) {
  const pop = $("#coinpop");
  const url = "/api/coin/" + encodeURIComponent(mint);
  let c = peek(url);
  if (!c) {
    const row = [...(STATE?.tape || []), ...(LAST_LIST || [])].find(x => x.mint === mint);
    POP_MINT = mint;
    pop.innerHTML = `<div class="pop-head">${coinImg(row?.image, row?.symbol, true, mint)}<div style="min-width:0"><b>${esc(row?.name || row?.symbol || "Loading…")}</b>
      <span>${row ? "$" + esc(row.symbol) + " · " : ""}loading details…</span></div><button class="pop-x" onclick="closePop()">✕</button></div>
      ${row ? `<div class="pop-stats"><div><span>ATH</span><b>${usd(row.ath_mcap)}</b></div><div><span>Now</span><b>${usd(row.usd_market_cap)}</b></div></div>` : ""}
      <div class="pop-sec" style="padding-bottom:12px"><span class="spin">◌</span> fetching…</div>`;
    placePop(anchor);
    try { c = await cjTrack(url, 30000); } catch { return; }
    if (POP_MINT !== mint) return;  // user moved on
  }
  POP_MINT = mint; POP_ANCHOR = anchor;
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
  if (back) POP_ACTIONS.backspace = back.fn;
  pop.innerHTML = `${back ? `<button class="pop-item pop-back" onclick="POP_ACTIONS.backspace()"><span class="ic">←</span>${esc(back.label)}<kbd>⌫</kbd></button><div class="pop-sep"></div>` : ""}
    <div class="pop-head">${coinImg(c.image, c.symbol, true, c.mint)}
      <div style="min-width:0"><b>${esc(c.name || c.symbol)}</b><span>$${esc(c.symbol)} · ${esc(CAT_NICE[c.primary_catalyst] || "Classifying…")}</span>${status}</div>
      <button class="pop-x" aria-label="Close" onclick="closePop()">✕</button></div>
    <div class="pop-stats">${stat("ATH", usd(c.ath_mcap))}${stat("Now", usd(c.usd_market_cap))}
      ${stat("To $100k", mins(c.time_to_100k_min))}${stat("Age", ago(c.created_ts))}
      ${stat("Holders", c.holders != null ? Number(c.holders).toLocaleString() : "—")}${stat("Top 10", c.top10_pct != null ? c.top10_pct + "%" : "—")}
      ${stat("Traders", c.unique_traders != null ? Number(c.unique_traders).toLocaleString() : "—")}${stat("Vol 24h", usd(c.volume_24h))}</div>
    ${fakeBlock(c, true)}
    ${(c.why || c.why_template || []).length ? `<div class="pop-sep"></div><div class="pop-sec">Why it ran</div><ul class="pop-why">${(c.why || c.why_template).map(b => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
    ${sig ? `<div class="pop-sec">Checks</div><div class="pop-sigs">${sig}</div>` : ""}
    <div class="pop-sep"></div>
    ${items.map(i => `<a class="pop-item" href="${esc(i[3])}" target="_blank" rel="noopener noreferrer"><span class="ic">${i[1]}</span>${i[2]}<kbd>${i[0]}</kbd></a>`).join("")}
    <button class="pop-item" onclick="POP_ACTIONS.c()"><span class="ic">⧉</span><span id="pop-copy">Copy contract address</span><kbd>C</kbd></button>
    <div class="pop-sep"></div>
    <button class="pop-item" onclick="POP_ACTIONS.f()"><span class="ic">≡</span>Show full coin file<kbd>F</kbd></button>`;
  placePop(anchor);
}

function placePop(anchor) {
  // next to what was clicked, like a context menu: to its left if there's room, else right, else below
  const pop = $("#coinpop"); pop.hidden = false;
  const r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect()
    : { left: anchor.left, right: anchor.left, top: anchor.top, bottom: anchor.top };
  const pw = pop.offsetWidth, ph = pop.offsetHeight, vw = innerWidth, vh = innerHeight;
  let x = r.left - pw - 14, y = r.top - 20;
  if (x < 8) x = r.right + 14;
  if (x + pw > vw - 8) { x = Math.max(8, Math.min(r.left, vw - pw - 8)); y = r.bottom + 8; }
  y = Math.max(8, Math.min(y, vh - ph - 8));
  pop.style.left = x + "px"; pop.style.top = y + "px";
  pop.scrollTop = 0;
  pop.querySelector(".pop-item, .coin-row")?.focus({ preventScroll: true });
}

// ---- panel popovers: narratives, agents, sources -------------------------------------
const AGENT_BLURB = { SCOUT: "Discovery · finds launches, migrations and runners", HOLDERS: "Holder base · counts, top-10 concentration",
  KOLS: "KOL wallets · who bought what, KOL LAB profiles", FEES: "Fake-chart filter · fees, wash trading, staircase charts",
  CATALYST: "Why it ran · catalyst classification", THESIS: "Narrative thesis · daily + hourly meta" };
const SOURCE_BLURB = { pumpportal: "Live launches + migrations (websocket)", jupiter: "Top traded / trending pump.fun tokens",
  jupiter_tokens: "Holders, top-holder %, traders, organic score", gecko_discovery: "PumpSwap + pump.fun pool lists",
  geckoterminal: "Price history → ATH, time to $100k", chart_shape: "1-minute candles → staircase detector",
  dexscreener: "Market cap, volume, pair, socials", dex_feeds: "Profile + boost feeds (socials)", metadata: "Descriptions, X links, images",
  solana_rpc: "Free trade sample → fees + wash forensics", helius_txs: "Swap history → fees + wash forensics",
  helius_holders: "Holder list → holders, top-10 %", kols: "KOL wallet buys", twitter: "X mentions, big accounts",
  pumpfun: "pump.fun API (Cloudflare often blocks it)", pumpfun_details: "pump.fun coin details", kol_research: "KOL LAB wallet history" };

function panelHtml({ dot, img, title, sub, body }) {
  return `<div class="pop-head" style="grid-template-columns:${img ? "56px " : dot ? "28px " : ""}1fr auto">
      ${img || ""}${dot ? `<span style="width:16px;height:16px;border-radius:50%;background:${dot};box-shadow:0 0 12px ${dot};margin-left:6px"></span>` : ""}
      <div style="min-width:0"><b>${title}</b><span>${sub || ""}</span></div>
      <button class="pop-x" aria-label="Close" onclick="closePop()">✕</button></div><div class="pop-sep"></div>${body}`;
}

function showPanel(html, anchor, wire) {
  const pop = $("#coinpop");
  POP_MINT = "panel"; POP_ACTIONS = {}; POP_ANCHOR = anchor;
  pop.innerHTML = html; placePop(anchor);
  pop.querySelectorAll(".coin-row").forEach(el => {
    el.onmouseenter = () => prefetchCoin(el.dataset.mint);
    el.onclick = (e) => { e.stopPropagation(); openPop(el.dataset.mint, anchor, wire.back); };
    el.onkeydown = (e) => { if (e.key === "Enter") el.click(); };
  });
  wire.after && wire.after(pop);
}

let LAST_LIST = [];
const coinList = (cs, empty, subFn) => (LAST_LIST = cs, cs).length ? cs.map(c => coinRowHtml(c, { sub: subFn && subFn(c) })).join("")
  : `<div class="pop-sec" style="padding-bottom:10px">${empty}</div>`;
const tfQ = () => "tf=" + encodeURIComponent(TF);

async function themePop(name, anchor) {
  const row = (STATE.themes.themes || []).find(t => t.theme === name) || {};
  const idx = STATE.themes.themes.findIndex(t => t.theme === name);
  const color = window.Synapse.PALETTE[idx % window.Synapse.PALETTE.length];
  const turl = `/api/coins?${tfQ()}&theme=${encodeURIComponent(name)}&limit=40`;
  let d = peek(turl);
  if (!d) {
    showPanel(panelHtml({ dot: color, title: esc(name), sub: `${row.runners ?? "…"} runners in ${TF}`,
      body: '<div class="pop-sec" style="padding-bottom:12px"><span class="spin">◌</span> loading coins…</div>' }), anchor, {});
    try { d = await cjTrack(turl); } catch { return; }
  }
  const mom = STATE.themes.daily.no_baseline ? "momentum n/a (no earlier data)" : `${row.momentum ?? 0}x momentum`;
  const sub = `${row.runners ?? d.count} runners in ${TF} · ${row.hit_rate ?? 0}% hit rate · ${mom}${row.real_pct != null ? ` · ${row.real_pct}% real` : ""}`;
  const back = { label: `Back to ${name}`, fn: () => themePop(name, anchor) };
  showPanel(panelHtml({ dot: color, title: esc(name), sub: esc(sub),
    body: `<div class="pop-stats"><div><span>Launches</span><b>${row.launches ?? "—"}</b></div><div><span>Share</span><b>${row.share ?? 0}%</b></div>
      <div><span>Last ${tfWords(STATE.themes.tail_s)}</span><b>${row.h_runners ?? 0} runners</b></div><div><span>Prev hit rate</span><b>${row.hit_rate_base ?? 0}%</b></div></div>
      <div class="pop-sep"></div><div class="pop-sec">Coins · biggest ATH first</div>${coinList(d.coins, `No ${esc(name)} runners in the last ${TF}. Try a longer timeframe.`)}` }),
    anchor, { back });
}

async function agentPop(name, anchor) {
  if (POP_MINT !== "panel" || !$("#coinpop").textContent.includes(name)) {
    showPanel(panelHtml({ dot: window.Synapse.AGENT_COLORS[name], title: name, sub: esc(AGENT_BLURB[name] || ""),
      body: '<div class="pop-sec" style="padding-bottom:12px"><span class="spin">◌</span> loading…</div>' }), anchor, {});
  }
  const back = { label: `Back to ${name}`, fn: () => agentPop(name, anchor) };
  const a = (STATE.agents || []).find(x => x.name === name) || {};
  const head = (body, sub) => panelHtml({ dot: window.Synapse.AGENT_COLORS[name], title: name,
    sub: esc(sub || AGENT_BLURB[name]) + (a.status ? ` · <span style="color:#a9adb6">${esc(a.status)}${a.last_ts ? " " + ago(a.last_ts) + " ago" : ""}</span>` : ""), body });
  if (name === "SCOUT") {
    const d = await cjTrack(`/api/coins?${tfQ()}&sort=new&limit=8`), ls = STATE.last_scan || {};
    const src = (STATE.sources || []).map(s => `<button class="pop-item" data-src="${esc(s.name)}"><span class="ic" style="color:${s.ok ? "#7ee2a8" : "#ff9aa9"}">●</span>${esc(s.name)}<kbd>${s.calls} calls</kbd></button>`).join("");
    showPanel(head(`<div class="pop-stats"><div><span>Last scan</span><b>${ls.ts ? ago(ls.ts) + " ago" : "—"}</b></div><div><span>Took</span><b>${ls.seconds ?? "—"}s</b></div>
      <div><span>Coins seen</span><b>${ls.discovered ?? "—"}</b></div><div><span>Runners</span><b>${ls.runners ?? "—"}</b></div></div>
      <div class="pop-sep"></div><div class="pop-sec">Newest runners · ${TF}</div>${coinList(d.coins, "No runners in this timeframe yet.")}
      <div class="pop-sep"></div><div class="pop-sec">Sources</div>${src}`), anchor,
      { back, after: (pop) => pop.querySelectorAll("[data-src]").forEach(b => b.onclick = (e) => { e.stopPropagation(); sourcePop(b.dataset.src, anchor, back); }) });
  } else if (name === "HOLDERS") {
    const [top, bad] = await Promise.all([cjTrack(`/api/coins?${tfQ()}&sort=holders&limit=8`), cjTrack(`/api/coins?${tfQ()}&fail=holders&limit=6`)]);
    const sub = (c) => `${c.holders != null ? Number(c.holders).toLocaleString() + " holders" : "holders —"} · top 10 ${c.top10_pct != null ? c.top10_pct + "%" : "—"}`;
    showPanel(head(`<div class="pop-sec">Most holders · ${TF}</div>${coinList(top.coins, "No runners in this timeframe.", sub)}
      <div class="pop-sep"></div><div class="pop-sec">Failing the holder checks</div>${coinList(bad.coins, "None failing.", sub)}`), anchor, { back });
  } else if (name === "FEES") {
    const d = await cjTrack(`/api/coins?${tfQ()}&fail=any&limit=40`);
    const fake = d.coins.filter(c => c.fails.some(f => ["fees", "wash", "chart"].includes(f.key))).slice(0, 15);
    const sub = (c) => c.fake_headline || c.fails.filter(f => ["fees", "wash", "chart"].includes(f.key)).map(f => f.note).join(" | ");
    showPanel(head(`<div class="pop-sec">Flagged as fake / botted · ${TF}</div>${coinList(fake, "Nothing flagged in this timeframe (or the fee/chart samples are still coming in).", sub)}`), anchor, { back });
  } else if (name === "CATALYST") {
    const cats = (STATE.themes.daily.catalysts || []);
    showPanel(head(cats.map(c => `<button class="pop-item" data-cat="${esc(c.category)}"><span class="ic">◆</span>${esc(CAT_NICE[c.category] || c.label)}<kbd>${c.count}</kbd></button>`).join("")
      || '<div class="pop-sec">No runners classified in this timeframe yet.</div>', `Catalyst mix · ${TF} · click one to see its coins`), anchor,
      { back, after: (pop) => pop.querySelectorAll("[data-cat]").forEach(b => b.onclick = async (e) => {
          e.stopPropagation(); const k = b.dataset.cat, d = await cjTrack(`/api/coins?${tfQ()}&catalyst=${encodeURIComponent(k)}&limit=30`);
          showPanel(panelHtml({ dot: window.Synapse.AGENT_COLORS.CATALYST, title: esc(CAT_NICE[k] || k), sub: `${d.count} runners · ${TF}`,
            body: `<button class="pop-item pop-back"><span class="ic">←</span>Back to CATALYST<kbd>⌫</kbd></button><div class="pop-sep"></div>${coinList(d.coins, "None.")}` }),
            anchor, { back: { label: `Back to ${CAT_NICE[k] || k}`, fn: () => b.onclick(e) }, after: (p2) => { p2.querySelector(".pop-back").onclick = (ev) => { ev.stopPropagation(); agentPop("CATALYST", anchor); }; POP_ACTIONS.backspace = () => agentPop("CATALYST", anchor); } });
        }) });
  } else if (name === "THESIS") {
    const dl = STATE.daily, h = STATE.hourly, sh = h && h.data.meta_shift;
    const list = (xs) => `<ul class="pop-why">${(xs || []).map(x => `<li>${esc(x)}</li>`).join("") || "<li>—</li>"}</ul>`;
    showPanel(head(dl ? `<div class="pop-sec">Daily thesis · ${ago(dl.ts)} ago · leader <b style="color:#7ee2a8">${esc(dl.leader || "—")}</b></div>
      <div class="pop-why" style="padding-left:12px;white-space:pre-wrap">${esc(dl.text)}</div>
      <div class="pop-sec" style="color:#7ee2a8">Look for</div>${list(dl.data.look_for)}<div class="pop-sec" style="color:#ff9aa9">Avoid</div>${list(dl.data.avoid)}
      ${h ? `<div class="pop-sep"></div><div class="pop-sec">Hourly meta ${sh && sh.shift ? '<span class="pill fake" style="background:#4a1f45;color:#ff9de6">META SHIFT</span>' : ""}</div>
      <div class="pop-why" style="padding-left:12px;white-space:pre-wrap">${esc(h.text)}</div>` : ""}` : '<div class="pop-sec">No thesis yet.</div>'), anchor, { back });
  } else if (name === "KOLS") {
    const [ks, buys] = await Promise.all([j("/api/kols"), cjTrack(`/api/coins?${tfQ()}&kols=1&limit=8`)]);
    const kolRows = ks.map(k => `<button class="pop-item" data-kol="${esc(k.name)}" style="padding:6px 10px">${pfpHtml(k.pfp, k.name).replace('class="pfp"', 'class="pfp" style="width:30px;height:30px;font-size:11px"')}
        <span style="min-width:0;overflow:hidden;text-overflow:ellipsis">${esc(k.name)}<br><span style="color:#8b8f98;font-size:11.5px">${esc((k.style || []).join(" · ") || k.status)}${k.summary.win_rate != null ? " · WR " + k.summary.win_rate + "%" : ""}</span></span><kbd>open ›</kbd></button>`).join("");
    showPanel(head(`<div class="pop-sec">Your KOLs · click to open their profile</div>${kolRows || '<div class="pop-sec">No KOLs yet: add some in the KOL LAB below.</div>'}
      <button class="pop-item" data-addkol><span class="ic">+</span>Add a KOL</button>
      <div class="pop-sep"></div><div class="pop-sec">Recent KOL buys on runners · ${TF}</div>${coinList(buys.coins, "No tracked KOL buys in this timeframe.", (c) => "bought by " + c.kols.join(", "))}`), anchor,
      { back, after: (pop) => {
          pop.querySelectorAll("[data-kol]").forEach(b => b.onclick = (e) => { e.stopPropagation(); closePop(); $("#kollab").scrollIntoView({ behavior: "smooth", block: "start" }); setTimeout(() => zoomTo(b.dataset.kol), 650); });
          pop.querySelector("[data-addkol]").onclick = (e) => { e.stopPropagation(); closePop(); openKolForm(null); };
        } });
  }
}

function sourcePop(name, anchor, back) {
  const s = (STATE.sources || []).find(x => x.name === name); if (!s) return;
  showPanel(panelHtml({ img: `<img class="cimg big" src="/api/srcicon/${encodeURIComponent(s.name)}" alt="" onerror="this.outerHTML='<div class=&quot;cimg big&quot;>${esc(s.name.slice(0, 2).toUpperCase())}</div>'" style="object-fit:contain;padding:10px;background:#fff1">`,
    title: esc(s.name), sub: esc(SOURCE_BLURB[s.name.replace(/ \(demo\)$/, "")] || "Data source") + ` · ${esc(s.agent)}`,
    body: `${back ? `<button class="pop-item pop-back"><span class="ic">←</span>${esc(back.label)}</button><div class="pop-sep"></div>` : ""}
      <div class="pop-stats"><div><span>Status</span><b style="color:${s.ok ? "#7ee2a8" : "#ff9aa9"}">${s.ok ? "OK" : "Error"}</b></div><div><span>Latency</span><b>${s.latency_ms ? Math.round(s.latency_ms) + "ms" : "—"}</b></div>
      <div><span>Calls</span><b>${s.calls}</b></div><div><span>Errors</span><b>${s.errors}</b></div>
      <div><span>Last OK</span><b>${s.last_ok_ts ? ago(s.last_ok_ts) + " ago" : "—"}</b></div><div><span>Last error</span><b>${s.last_err_ts ? ago(s.last_err_ts) + " ago" : "—"}</b></div></div>
      ${s.last_error ? `<div class="pop-sep"></div><div class="pop-sec">Last error</div><div class="pop-why" style="padding-left:12px;word-break:break-word">${esc(s.last_error)}</div>` : ""}
      ${s.note ? `<div class="pop-sec">Note</div><div class="pop-why" style="padding-left:12px">${esc(s.note)}</div>` : ""}` }),
    anchor, { after: (pop) => { const b = pop.querySelector(".pop-back"); if (b && back) { b.onclick = (e) => { e.stopPropagation(); back.fn(); }; POP_ACTIONS.backspace = back.fn; } } });
}

async function holdingPop(k, h, anchor) {
  const curl = "/api/coin/" + encodeURIComponent(h.mint);
  let inDb = !!peek(curl);
  if (!inDb) cjTrack(curl, 30000).then(() => { if (POP_MINT === "panel" && $("#coinpop").textContent.includes(h.symbol) && !$("#coinpop [data-coin]")) holdingPop(k, h, anchor); }, () => {});
  const x = h.multiple, cls = x == null ? "" : x >= 1 ? "pos" : "neg";
  const back = { label: `Back to ${k.name}'s $${h.symbol}`, fn: () => holdingPop(k, h, anchor) };
  showPanel(panelHtml({ img: coinImg(h.image, h.symbol, true, h.mint), title: `$${esc(h.symbol)}${h.name ? ` <span style="color:#a9adb6;font-weight:500">${esc(h.name)}</span>` : ""}`,
    sub: `${esc(k.name)} is holding · ${holdAge(h)}`,
    body: `<div class="pop-stats"><div><span>Holding for</span><b>${holdAge(h)}</b></div><div><span>Bought in</span><b>${h.invested_sol} SOL</b></div>
      <div><span>Worth now</span><b>${h.unrealized_sol ?? "—"} SOL</b></div><div><span>Multiple</span><b class="${cls}">${x != null ? x + "x" : "—"}</b></div>
      <div><span>Entry mcap</span><b>${usd(h.entry_mcap)}</b></div><div><span>Mcap now</span><b>${usd(h.mcap_now)}</b></div></div>
      <div class="pop-sec">As of the last wallet research (${k.researched_ts ? ago(k.researched_ts) + " ago" : "—"})</div>
      <div class="pop-sep"></div>
      ${inDb ? `<button class="pop-item" data-coin><span class="ic">≡</span>Open coin card<kbd>↵</kbd></button>` : ""}
      <a class="pop-item" href="https://pump.fun/coin/${esc(h.mint)}" target="_blank" rel="noopener noreferrer"><span class="ic">◎</span>Open in pump.fun</a>
      <a class="pop-item" href="https://dexscreener.com/solana/${esc(h.mint)}" target="_blank" rel="noopener noreferrer"><span class="ic">▦</span>Open in DexScreener</a>
      <a class="pop-item" href="https://solscan.io/token/${esc(h.mint)}" target="_blank" rel="noopener noreferrer"><span class="ic">⌕</span>Open on Solscan</a>
      <button class="pop-item" data-kolp><span class="ic">◉</span>Open ${esc(k.name)}'s profile</button>` }),
    anchor, { after: (pop) => {
      const c = pop.querySelector("[data-coin]"); if (c) c.onclick = (e) => { e.stopPropagation(); openPop(h.mint, anchor, back); };
      pop.querySelector("[data-kolp]").onclick = (e) => { e.stopPropagation(); closePop(); zoomTo(k.name); };
    } });
}

window.Synapse.onPick((hit, at) => hit.type === "theme" ? themePop(hit.name, at) : agentPop(hit.name, at));
function closePop() { $("#coinpop").hidden = true; POP_MINT = null; }
document.addEventListener("click", (e) => {
  // ignore clicks whose target was just replaced by a re-render inside the popover
  if (POP_MINT && document.contains(e.target) && !e.target.closest("#coinpop")) closePop();
});
document.addEventListener("keydown", (e) => {
  if (!POP_MINT || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "Escape") { closePop(); return; }
  if (e.target.closest && e.target.closest("input, textarea")) return;
  const fn = POP_ACTIONS[e.key.toLowerCase()]; if (fn) { e.preventDefault(); fn(); }
});
window.addEventListener("scroll", () => POP_MINT && closePop(), { passive: true });
