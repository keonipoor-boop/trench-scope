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
        <div class="dk-title"><b>NARRATIVE ENGINE</b><span>paper deving · ideas are checked against what launched next · nothing is launched</span></div>
        <div class="dk-ctl"><button class="dk-btn" id="lb-notify">🔔 NOTIFICATIONS</button><button class="dk-btn" id="lb-gen">↻ NEW IDEAS</button><button class="dk-btn" id="lb-set">⚙ DRAFTS & VOICE</button></div>
      </header>
      <section class="lb-main">
        <div class="dk-col"><div class="dk-card"><div class="dk-h">RUNNING NARRATIVES <i>today's runners and the coins that played them</i></div><div id="lb-narr"></div></div>
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
          <div class="lb-act"><button class="dk-btn" data-draft="${i.id}">✎ DRAFT TWEET</button><button class="dk-btn" data-copy="${esc(i.name + " ($" + i.ticker + ")")}">COPY</button></div></div>`; }).join("")}</div>`).join("")
      || '<div class="dk-empty">ideas appear as runners show up (every 10 minutes) · ↻ NEW IDEAS to run it now</div>';
    // readiness
    const rd = d.readiness, sc = rd.score, rc = sc < 40 ? "#ff8fb1" : sc < 70 ? "#ffcf8a" : "#7ee2a8";
    $("#lb-ready").innerHTML = `<div class="dk-rm"><b style="color:${rc}">${sc.toFixed(1)}</b><span>/ 100</span><em style="color:${rc}">${esc(rd.verdict.toUpperCase())}</em></div>
      <div class="dk-rbar"><i style="width:${sc}%;background:linear-gradient(90deg,#ff4fa3,${rc})"></i><s style="left:70%"></s></div>
      <div class="lb-tally"><span style="color:#7ee2a8">${rd.right} right</span><span style="color:#ffcf8a">${rd.partial} partial</span><span>${rd.n - rd.right - rd.partial} wrong</span><span>${rd.clone_risk} clone risk</span></div>
      <div class="dk-rnote">right = a coin playing the same narrative, launched after the idea, reached ${fmt(d.bars.right)} within 24h (partial: ${fmt(d.bars.partial)})</div>
      ${rd.caps.map(c => `<div class="dk-rcap">⚠ ${esc(c)}</div>`).join("")}
      <div class="dk-rparts">${Object.entries(rd.parts).map(([k, p]) => `<div><span>${k}</span><div><i style="width:${p.score}%"></i></div><em>${esc(p.note)}</em></div>`).join("")}</div>
      <div class="dk-rnote">Information only: the lab never launches a coin.</div>`;
    renderDrafts();
    bind();
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
    if (on) { mount(); load(); loadDrafts(); clearInterval(L.timer); L.timer = setInterval(load, 15000); } else clearInterval(L.timer);
  }
  window.VIEWS.lab = show;
  window.VIEWS.scope = () => {};
  loadDrafts(); setInterval(loadDrafts, 8000);
  let start = "scope"; try { start = localStorage.getItem("ts_view") || "scope"; } catch { /* ignore */ }
  window.setView(start);
})();
