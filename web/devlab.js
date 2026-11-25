/* DEV LAB view (core/devlab.py, /api/devlab) + X DRAFTS (core/drafts.py, /api/drafts) + the draft notifier.
   Paper deving: the lab proposes narrative plays and checks whether they would have been right. Nothing is launched,
   nothing is posted: drafts open X's composer with the text filled in and you post them yourself. */
(() => {
  const L = { on: false, data: null, drafts: [], dset: null, lastDraft: null, timer: null };
  const VERD = { right: ["RIGHT", "#7ee2a8"], partial: ["PARTIAL", "#ffcf8a"], wrong: ["WRONG", "#ff8fb1"] };
  const TRIG = { runner: "RUNNER", sniper: "SNIPER", meta_shift: "META SHIFT", thesis: "THESIS", idea: "IDEA" };

  async function post(url, body) {
    const pw = await adminPw(); if (pw === null) return null;
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json", "x-admin-password": pw }, body: body ? JSON.stringify(body) : undefined });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { alert(r.status === 403 ? "Wrong admin password." : (out.detail || "Request failed (" + r.status + ")")); if (r.status === 403) ADMIN_PW = ""; return null; }
    return out;
  }

  function mount() {
    if ($("#lab").dataset.ready) return;
    $("#lab").dataset.ready = 1;
    $("#lab").innerHTML = `
      <header class="dk-top">
        <div class="dk-brand"><i></i><b>TRENCH</b> <span>Dev Lab</span></div>
        <div class="dk-title"><b>DEV LAB / NARRATIVE ENGINE</b><span>paper deving · ideas are checked against what launched next · nothing is launched</span></div>
        <div class="dk-ctl"><button class="dk-btn" id="lb-notify">🔔 NOTIFICATIONS</button><button class="dk-btn" id="lb-gen">↻ NEW IDEAS</button><button class="dk-btn" id="lb-set">⚙ DRAFTS & VOICE</button></div>
      </header>
      <section class="dk-field lb-field" id="lb-field"><canvas id="lb-canvas"></canvas>
        <div class="dk-fieldlbl"><b>NARRATIVE ENGINE / LANDSCAPE OF PLAYS</b><span>running narratives → read · score · play · originality · 24h verdict · draft — pulses are real lab events</span></div>
        <div class="dk-ops" id="lb-ops"></div>
        <div class="dk-floorlbl"><b>STATE</b> · hills = narratives by follow-on hit rate · lights = verdicts (green right · amber partial · red wrong)</div>
      </section>
      <div class="dk-ticker"><div class="dk-tick-track" id="lb-ticker"></div></div>
      <section class="lb-main">
        <div class="dk-col"><div class="dk-card"><div class="dk-h">RUNNING NARRATIVES <i>today's runners and the coins that played them</i></div><div id="lb-narr"></div></div>
          <div class="dk-card"><div class="dk-h">NEW PAIRS <i>words spiking in fresh launches</i></div><div id="lb-np"></div></div>
          <div class="dk-card"><div class="dk-h">DEV METAS <i>what your tracked devs launch + post</i></div><div id="lb-metas" class="lb-metas"></div></div></div>
        <div class="dk-col"><div class="dk-card"><div class="dk-h">NARRATIVE PLAYS <i>related, never clones · checked 24h later</i><em id="lb-in"></em></div><div id="lb-ideas"></div></div></div>
        <div class="dk-col">
          <div class="dk-card dk-ready"><div class="dk-h">DEV READINESS <i>would the plays have been right?</i></div><div id="lb-ready"></div></div>
          <div class="dk-card"><div class="dk-h">X DRAFTS <i>ready to post from your account</i><em id="lb-dn"></em></div><div id="lb-drafts"></div></div>
        </div>
      </section>
      <dialog id="lb-dlg"><form method="dialog" id="lb-form"><h3>◆ X DRAFTS · VOICE & ALERTS</h3>
        <label class="set-key"><span><input type="checkbox" name="enabled"> Draft automatically when something happens</span></label>
        <div id="lb-trig"></div>
        <label class="set-key">YOUR VOICE <i class="dim" style="font-style:normal">paste 5-20 of your own tweets: drafts copy your tone (never anyone else's)</i><textarea name="voice" rows="5"></textarea></label>
        <label class="set-key">DISCORD WEBHOOK (optional) <i class="dim" style="font-style:normal" id="lb-hook"></i><input name="discord_webhook" placeholder="https://discord.com/api/webhooks/… · type clear to remove"></label>
        <label class="set-key">MAX DRAFTS PER HOUR<input name="max_per_hour" type="number" min="1" max="60"></label>
        <label class="set-key"><span><input type="checkbox" name="include_ca"> Add the contract address</span></label>
        <p class="dim small">Drafts are written for your own account from the bot's data, and say so when the sniper holds the coin. Nothing is posted for you: POST ON X opens X with the text filled in.</p>
        <p class="small" id="lb-msg"></p>
        <div class="set-actions"><button value="cancel" formnovalidate>CLOSE</button><button value="default">SAVE</button></div></form></dialog>`;
    L.field = new SignalField($("#lb-canvas"), $("#lb-field"));
    $("#lb-gen").onclick = async () => { if (await post("/api/devlab/refresh")) load(); };
    $("#lb-set").onclick = openSettings;
    $("#lb-notify").onclick = askNotify;
    $("#lb-form").addEventListener("submit", saveSettings);
    paintNotify();
  }

  // ---- render ------------------------------------------------------------------------------
  function render() {
    const d = L.data; if (!d) return;
    const fmt = (v) => usd(v);
    $("#lb-narr").innerHTML = d.narratives.map(n => `<div class="lb-n"><div class="dk-row" data-mint="${esc(n.mint)}">${coinImg(n.image, n.symbol, false, n.mint)}
        <div class="dk-rn"><b>$${esc(n.symbol || "?")} <i class="dk-tag" style="color:${n.is_real ? "#7ee2a8" : "#ffcf8a"}">${n.is_real ? "REAL" : "ANALYZING"}</i></b><span>${esc(n.name || "")} · peak ${fmt(n.ath)} · ${n.age_h}h</span></div></div>
        <div class="lb-beta">${Object.entries(n.beta).map(([w, b]) => `<span title="coins launched after it reusing '${esc(w)}'"><b>${esc(w)}</b> ${b.launched} launched · ${b.ran} ran${b.rate != null ? ` (${b.rate}%)` : ""}${b.best && b.best.ath ? ` · best ${fmt(b.best.ath)}` : ""}</span>`).join("") || '<span>no key words</span>'}</div></div>`).join("")
      || '<div class="dk-empty">waiting for today\'s runners…</div>';
    $("#lb-metas").innerHTML = (d.dev_metas || []).map(m => `<div class="lb-meta"><b>★ ${esc(m.name)}</b>${m.handle ? ` <span class="dim">@${esc(m.handle)}</span>` : ""}
        <div class="dim">${m.needs_wallet ? "add their dev wallet in DEV SYSTEM to study their launches" : `${m.launches} launches in 3 days${m.best ? ` · best <button class="lb-coin" data-mint="${esc(m.best.mint)}">$${esc(m.best.symbol || "?")}</button> ${fmt(m.best.ath)}` : ""}`}</div>
        ${m.words.length || m.themes.length ? `<div class="w">${[...m.themes, ...m.words].map(w => `<span>${esc(w)}</span>`).join("")}</div>` : ""}
        ${m.x_pinned ? `<q>📌 ${esc(m.x_pinned.slice(0, 180))}</q>` : m.x_bio ? `<q>${esc(m.x_bio.slice(0, 160))}</q>` : ""}</div>`).join("")
      || '<div class="dk-empty">add devs in the KOL LAB → DEV SYSTEM (+ ADD DEV): the lab studies what they launch and post</div>';
    $("#lb-np").innerHTML = (d.new_pairs || []).map(w => `<div class="dk-row" data-mint="${esc(w.mint)}">${coinImg(w.image, w.symbol, false, w.mint)}
        <div class="dk-rn"><b>“${esc(w.word)}” <i class="dk-tag">${w.spike}x</i></b><span>${w.count} launches · lead $${esc(w.symbol || "?")} ${fmt(w.mcap)}</span></div></div>`).join("")
      || '<div class="dk-empty">no word is spiking in new pairs right now</div>';
    $("#lb-in").textContent = d.ideas.length;
    const byParent = {};
    d.ideas.forEach(i => (byParent[i.parent_symbol] = byParent[i.parent_symbol] || []).push(i));
    $("#lb-ideas").innerHTML = Object.entries(byParent).map(([p, list]) => `<div class="lb-group"><div class="lb-gh">from $${esc(p)} <i>${esc(list[0].parent_name || "")}</i></div>
      ${list.map(i => { const v = VERD[i.verdict], o = i.originality || {};
        return `<div class="lb-idea"><div class="lb-ih"><b>${esc(i.name)}</b><span>$${esc(i.ticker)}</span>
            ${o.ok === false ? `<em class="bad" title="${esc(o.why)}">CLONE RISK</em>` : `<em class="ok" title="${esc(o.why || "")}">ORIGINAL</em>`}
            ${v ? `<em style="background:${v[1]};color:#120610">${v[0]}</em>` : `<em class="wait">CHECKS IN ${dur(i.resolves_in)}</em>`}</div>
          <div class="lb-pitch">${esc(i.pitch)} <i>· plays “${esc(i.keyword)}” · ${i.by === "claude" ? "Claude" : "template"}</i></div>
          ${i.outcome ? `<div class="lb-out">${i.outcome.matches} “${esc(i.keyword)}” coins launched after${i.outcome.best ? `; best <button class="lb-coin" data-mint="${esc(i.outcome.best.mint)}">$${esc(i.outcome.best.symbol || "?")}</button> peaked ${fmt(i.outcome.best.ath)}` : ""}</div>` : ""}
          ${o.ok === false ? `<div class="lb-warn">⚠ ${esc(o.why)}. Pick a name that can't be mistaken for it.</div>` : ""}
          <div class="lb-act">${i.kit ? `<button class="dk-btn pri" data-kit="${i.id}">⚡ LAUNCH KIT</button>` : ""}<button class="dk-btn" data-draft="${i.id}">✎ DRAFT TWEET</button><button class="dk-btn" data-copy="${esc(i.name + " ($" + i.ticker + ")")}">COPY</button>${i.source === "new pairs" ? '<span class="lb-src">NEW PAIRS</span>' : ""}</div>
          ${i.kit ? kitHtml(i) : ""}</div>`; }).join("")}</div>`).join("")
      || '<div class="dk-empty">ideas appear as runners show up (every 10 minutes) · ↻ NEW IDEAS to run it now</div>';
    // readiness
    const rd = d.readiness, sc = rd.score, rc = sc < 40 ? "#ff8fb1" : sc < 70 ? "#ffcf8a" : "#7ee2a8";
    $("#lb-ready").innerHTML = `<div class="dk-rm"><b style="color:${rc}">${sc.toFixed(1)}</b><span>/ 100</span><em style="color:${rc}">${esc(rd.verdict.toUpperCase())}</em></div>
      <div class="dk-rbar"><i style="width:${sc}%;background:linear-gradient(90deg,#6b6b6b,${rc})"></i><s style="left:70%"></s></div>
      <div class="lb-tally"><span style="color:#7ee2a8">${rd.right} right</span><span style="color:#ffcf8a">${rd.partial} partial</span><span>${rd.n - rd.right - rd.partial} wrong</span><span>${rd.clone_risk} clone risk</span></div>
      <div class="dk-rnote">right = a coin playing the same narrative, launched after the idea, reached ${fmt(d.bars.right)} within 24h (partial: ${fmt(d.bars.partial)})</div>
      ${rd.caps.map(c => `<div class="dk-rcap">⚠ ${esc(c)}</div>`).join("")}
      <div class="dk-rparts">${Object.entries(rd.parts).map(([k, p]) => `<div><span>${k}</span><div><i style="width:${p.score}%"></i></div><em>${esc(p.note)}</em></div>`).join("")}</div>
      <div class="dk-rnote">Information only: the lab never launches a coin.</div>`;
    renderDrafts();
    bind();
    feedField(d);
  }

  // ---- signal field: lanes are the lab's real steps; pulses come from real state changes ----------------
  const LANES = [["READ", "Read", "narratives", "#d9d9d9"], ["SCORE", "Score", "beta rate", "#f2e14c"], ["PLAY", "Choice", "play", "#ffffff"],
    ["CHECK", "Check", "originality", "#e85cf0"], ["VERDICT", "Verdict", "24h", "#7ee2a8"], ["DRAFT", "Draft", "x post", "#7dd3fc"]];
  function feedField(d) {
    if (!L.field) return;
    const rd = d.readiness, right = rd.right, n = rd.n;
    const val = { READ: d.narratives.length, SCORE: d.base_rate + "%", PLAY: d.ideas.length, CHECK: rd.clone_risk, VERDICT: n ? Math.round(right / n * 100) + "%" : "—",
      DRAFT: L.drafts.filter(x => x.status === "new").length };
    $("#lb-ops").innerHTML = LANES.map(([k, verb, obj, col], i) => `<div class="dk-op ${L.hot === k ? "hot" : ""}" style="--oc:${col}">
      <div class="dk-op-h"><span>${String(i + 1).padStart(2, "0")}</span><b>${k}</b></div>
      <div class="dk-op-r"><div class="dk-bars">${Array.from({ length: 16 }, (_, q) => `<i style="height:${20 + ((q * 37 + i * 11) % 80)}%"></i>`).join("")}</div><b>${val[k]}</b></div>
      <div class="dk-op-l">${verb} · <b>${obj}</b></div></div>`).join("");
    const src = d.narratives.map(x => ({ id: x.symbol, label: "$" + (x.symbol || "?"), sub: usd(x.ath) }));
    L.field.layout(src, [...document.querySelectorAll("#lb-ops .dk-op")]);
    const floor = d.narratives.map((x, i) => { const best = Math.max(0, ...Object.values(x.beta).map(b => b.rate || 0));
      return { u: 0.08 + 0.84 * (i / Math.max(1, d.narratives.length - 1)), v: 0.32, h: Math.min(1, best / 12) }; })
      .concat(d.ideas.filter(i => i.verdict).slice(0, 18).map((i, q) => ({ u: 0.06 + 0.88 * (q / 17), v: 0.78, h: 0.35,
        color: i.verdict === "right" ? "#7ee2a8" : i.verdict === "partial" ? "#f2e14c" : "#ff6b6b" })));
    const fsig = JSON.stringify(floor.map(f => [f.u.toFixed(2), f.h.toFixed(2), f.color]));
    if (fsig !== L.fsig) { L.fsig = fsig; L.field.setFloor(floor); }
    // real lab events since the last poll -> pulses
    const prev = L.prevIdeas || new Map(), now = new Map(d.ideas.map(i => [i.id, i]));
    const evs = [];
    if (L.prevIdeas) {
      for (const i of d.ideas) {
        const p = prev.get(i.id);
        if (!p) evs.push([i.parent_symbol, 2, "#ffffff", i.name.slice(0, 16)], [i.parent_symbol, 3, (i.originality || {}).ok === false ? "#e85cf0" : "#d9d9d9", (i.originality || {}).ok === false ? "CLONE RISK" : "ORIGINAL"]);
        else if (!p.verdict && i.verdict) evs.push([i.parent_symbol, 4, i.verdict === "right" ? "#7ee2a8" : i.verdict === "partial" ? "#f2e14c" : "#ff6b6b", i.verdict.toUpperCase()]);
      }
    } else {
      d.narratives.forEach(x => evs.push([x.symbol, 0, "#d9d9d9", null]));  // first read of the field
    }
    L.prevIdeas = now;
    evs.forEach((e, q) => setTimeout(() => L.on && L.field.pulse(...e), q * 220));
    L.field.setRate(Math.max(1, evs.length * 4 + d.ideas.filter(i => Date.now() / 1000 - i.ts < 3600).length));
    const tk = d.ideas.slice(0, 24).map(i => `<span><b>${i.verdict ? i.verdict : "…"}</b> → play(${esc(i.name)}, $${esc(i.ticker)}) · from $${esc(i.parent_symbol || "?")}</span>`).join("");
    if (tk !== L.tick) { L.tick = tk; $("#lb-ticker").innerHTML = tk + tk; }
  }

  // Axiom Token Deploy, pre-filled: every field copyable; deploying stays your click in Axiom
  function kitHtml(i) {
    const k = i.kit, open = L.openKit === i.id;
    const f = (label, v, key, hint) => `<div class="kit-f"><span>${label}${hint ? ` <i>${hint}</i>` : ""}</span><code>${v ? esc(v) : '<i class="dim">—</i>'}</code>${v ? `<button class="dk-btn" data-kc="${i.id}:${key}">COPY</button>` : ""}</div>`;
    return `<div class="lb-kit" ${open ? "" : "hidden"} data-kitbox="${i.id}">
      <div class="kit-h"><b>TOKEN DEPLOY · launch kit</b><span>paste into Axiom's deploy form · dev readiness ${L.data ? L.data.readiness.score.toFixed(1) : "—"}/100${L.data && L.data.readiness.score < 70 ? " · paper record not ready yet" : ""}</span></div>
      ${f("NAME", k.name, "name", `${k.name.length}/32`)}${f("SYMBOL", k.symbol, "symbol", `${k.symbol.length}/13`)}
      ${f("DESCRIPTION", k.description, "description")}${f("POST", k.post, "post", "the narrative's X post")}${f("IMAGE PROMPT", k.image_prompt, "image_prompt", "for Axiom's AI image")}
      <div class="kit-f"><span>PLATFORM · DEV BUY</span><code>${esc(k.platform)} · ${k.dev_buy_presets.map(x => `<b class="${x === k.dev_buy_sol ? "on" : ""}">${x} SOL</b>`).join(" ")}</code></div>
      <div class="lb-act"><button class="dk-btn pri" data-kitall="${i.id}">COPY ALL</button><a class="dk-btn" href="https://axiom.trade/" target="_blank" rel="noopener noreferrer">OPEN AXIOM ↗</a>
        <button class="dk-btn" data-kc="${i.id}:symbol">COPY SYMBOL</button></div></div>`;
  }

  function renderDrafts() {
    if (!$("#lb-drafts")) return;
    const list = L.drafts.filter(x => x.status !== "dismissed");
    $("#lb-dn").textContent = list.filter(x => x.status === "new").length || "";
    $("#lb-drafts").innerHTML = list.slice(0, 20).map(x => `<div class="lb-draft ${x.status}"><div class="lb-dh"><em>${TRIG[x.trigger] || x.trigger}</em><span>${ago(x.ts)} ago · ${x.by === "claude" ? "Claude" : "template"}${x.status === "posted" ? " · posted" : ""}</span></div>
      <p>${esc(x.text)}</p><div class="lb-act"><a class="dk-btn pri" href="${esc(x.intent)}" target="_blank" rel="noopener noreferrer" data-posted="${x.id}">POST ON X ↗</a>
      <button class="dk-btn" data-copytext="${x.id}">COPY</button><button class="dk-btn" data-dismiss="${x.id}">DISMISS</button></div></div>`).join("")
      || '<div class="dk-empty">no drafts yet: they appear the moment a REAL runner crosses $100k, the sniper trades, the meta shifts or the hourly thesis lands</div>';
    $("#lb-drafts").querySelectorAll("[data-posted]").forEach(a => a.onclick = () => { post(`/api/drafts/${a.dataset.posted}/posted`).then(loadDrafts); });
    $("#lb-drafts").querySelectorAll("[data-dismiss]").forEach(b => b.onclick = async () => { if (await post(`/api/drafts/${b.dataset.dismiss}/dismissed`)) loadDrafts(); });
    $("#lb-drafts").querySelectorAll("[data-copytext]").forEach(b => b.onclick = async () => {
      const x = L.drafts.find(y => y.id === +b.dataset.copytext); try { await navigator.clipboard.writeText(x.text); b.textContent = "COPIED"; } catch { prompt("Draft:", x.text); } });
  }

  function bind() {
    document.querySelectorAll("#lab [data-mint]").forEach(r => { r.onclick = (e) => { e.stopPropagation(); openPop(r.dataset.mint, r); }; });
    document.querySelectorAll("#lab [data-draft]").forEach(b => b.onclick = async () => { b.textContent = "drafting…"; await post(`/api/devlab/ideas/${b.dataset.draft}/draft`); b.textContent = "✓ DRAFTED"; loadDrafts(); });
    const ideaBy = (id) => (L.data.ideas || []).find(x => x.id === +id);
    const clip = async (b, text) => { try { await navigator.clipboard.writeText(text); const t = b.textContent; b.textContent = "COPIED"; setTimeout(() => b.textContent = t, 1200); } catch { prompt("Copy:", text); } };
    document.querySelectorAll("#lab [data-kit]").forEach(b => b.onclick = () => {
      L.openKit = L.openKit === +b.dataset.kit ? null : +b.dataset.kit;
      document.querySelectorAll("#lab [data-kitbox]").forEach(x => x.hidden = +x.dataset.kitbox !== L.openKit);
    });
    document.querySelectorAll("#lab [data-kc]").forEach(b => b.onclick = () => { const [id, key] = b.dataset.kc.split(":"); clip(b, ideaBy(id).kit[key] || ""); });
    document.querySelectorAll("#lab [data-kitall]").forEach(b => b.onclick = () => { const k = ideaBy(b.dataset.kitall).kit;
      clip(b, `Name: ${k.name}\nSymbol: ${k.symbol}\nDescription: ${k.description}\nPost: ${k.post || ""}\nImage prompt: ${k.image_prompt}\nPlatform: ${k.platform}\nDev buy: ${k.dev_buy_sol} SOL`); });
    document.querySelectorAll("#lab [data-copy]").forEach(b => b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "COPIED"; } catch { prompt("Idea:", b.dataset.copy); } });
  }

  async function load() { try { L.data = await j("/api/devlab"); render(); } catch { /* keep last */ } }

  // ---- drafts: polled in every view, so a notification arrives wherever you are -------------------
  async function loadDrafts() {
    try {
      const d = await j("/api/drafts");
      const fresh = L.lastDraft == null ? [] : d.drafts.filter(x => x.id > L.lastDraft && x.status === "new");
      L.lastDraft = Math.max(L.lastDraft || 0, ...d.drafts.map(x => x.id), 0);
      L.drafts = d.drafts; L.dset = d;
      fresh.reverse().forEach(notify);
      renderDrafts();
    } catch { /* backend busy */ }
  }
  function notify(x) {
    if (L.field && L.on) { const sym = (x.text.match(/\$([A-Za-z0-9]+)/) || [])[1]; L.field.pulse(sym || null, 5, "#7dd3fc", "DRAFT"); }
    toast(x);
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        const n = new Notification(`New X draft · ${TRIG[x.trigger] || x.trigger}`, { body: x.text.slice(0, 180), tag: "draft" + x.id });
        n.onclick = () => { window.focus(); window.setView("lab"); n.close(); };
      }
    } catch { /* notifications blocked */ }
  }
  function toast(x) {
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<b>✎ New X draft · ${esc(TRIG[x.trigger] || x.trigger)}</b><p>${esc(x.text.slice(0, 160))}${x.text.length > 160 ? "…" : ""}</p>
      <div><a class="dk-btn pri" href="${esc(x.intent)}" target="_blank" rel="noopener noreferrer">POST ON X ↗</a><button class="dk-btn">OPEN DRAFTS</button><button class="dk-btn">✕</button></div>`;
    const [, open, close] = el.querySelectorAll("a, button");
    open.onclick = () => { window.setView("lab"); el.remove(); };
    close.onclick = () => el.remove();
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 20000);
  }
  function paintNotify() {
    const b = $("#lb-notify"); if (!b) return;
    const p = "Notification" in window ? Notification.permission : "unsupported";
    b.textContent = p === "granted" ? "🔔 NOTIFICATIONS ON" : p === "denied" ? "🔕 BLOCKED IN BROWSER" : p === "unsupported" ? "🔕 NOT SUPPORTED" : "🔔 TURN ON NOTIFICATIONS";
  }
  async function askNotify() {
    if (!("Notification" in window)) return;
    try { await Notification.requestPermission(); } catch { /* old browsers */ }
    paintNotify();
    if (Notification.permission === "granted") new Notification("Trench Scope", { body: "Draft notifications are on." });
  }

  function openSettings() {
    const s = (L.dset || {}).settings || {}, t = (L.dset || {}).triggers || {};
    const f = $("#lb-form");
    f.enabled.checked = !!s.enabled; f.voice.value = s.voice || ""; f.discord_webhook.value = ""; f.max_per_hour.value = s.max_per_hour || 12;
    f.include_ca.checked = s.include_ca !== false;
    $("#lb-hook").textContent = s.discord_webhook ? "a webhook is saved (leave blank to keep it)" : "pings your own Discord channel";
    $("#lb-trig").innerHTML = Object.entries(t).map(([k, label]) => `<label class="set-key"><span><input type="checkbox" name="t_${k}" ${(s.triggers || {})[k] !== false ? "checked" : ""}> ${esc(label)}</span></label>`).join("");
    $("#lb-msg").textContent = ""; $("#lb-dlg").showModal();
  }
  async function saveSettings(e) {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    const f = e.target, t = {};
    Object.keys((L.dset || {}).triggers || {}).forEach(k => t[k] = f["t_" + k].checked);
    const body = { enabled: f.enabled.checked, triggers: t, voice: f.voice.value, max_per_hour: +f.max_per_hour.value, include_ca: f.include_ca.checked };
    const hook = f.discord_webhook.value.trim();
    if (hook) body.discord_webhook = hook.toLowerCase() === "clear" ? "" : hook;
    if (await post("/api/drafts/settings", body)) { $("#lb-dlg").close(); loadDrafts(); }
  }

  function show(on) {
    L.on = on;
    if (L.field) L.field.active = on;
    if (on) { mount(); L.field.active = true; load(); loadDrafts(); clearInterval(L.timer); L.timer = setInterval(load, 15000); } else clearInterval(L.timer);
  }
  window.VIEWS.lab = show;
  window.VIEWS.scope = () => {};
  loadDrafts(); setInterval(loadDrafts, 8000);
  let start = "scope"; try { start = localStorage.getItem("ts_view") || "scope"; } catch { /* ignore */ }
  window.setView(start);
})();
