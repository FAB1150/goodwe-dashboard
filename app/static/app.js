"use strict";

const $ = (s) => document.querySelector(s);
const DAYS = ["Dom", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab"];
const DAY_ORDER = [1, 2, 3, 4, 5, 6, 0]; // mostra lun..dom
let view = {};
let config = {};

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function banner(msg, ok = false) {
  const b = $("#banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg;
  b.classList.toggle("ok", ok);
  b.classList.remove("hidden");
}

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch { /* vuoto */ }
  if (!r.ok) throw new Error((data && data.detail) || `Errore HTTP ${r.status}`);
  return data;
}

async function busy(btn, fn) {
  const all = document.querySelectorAll("main button, header button");
  all.forEach((b) => (b.disabled = true));
  const old = btn ? btn.textContent : "";
  if (btn) btn.textContent = "Attendere…";
  try { return await fn(); }
  catch (e) { banner(e.message); }
  finally { all.forEach((b) => (b.disabled = false)); if (btn) btn.textContent = old; }
}

// --- rendering --------------------------------------------------------------

function renderKV(el, fields) {
  el.innerHTML = (fields || []).map((f) => {
    const unit = f.unit ? ` ${esc(f.unit)}` : "";
    const raw = f.raw !== null && f.raw !== undefined && f.raw !== f.value ? ` <span class="raw">(grezzo ${esc(f.raw)})</span>` : "";
    const note = f.note ? `<br><span class="raw">${esc(f.note)}</span>` : "";
    return `<dt>${esc(f.label)}</dt><dd>${esc(f.value ?? "—")}${unit}${raw}${note}</dd>`;
  }).join("");
}

function field(group, key) {
  return ((view.settings || {})[group] || []).find((f) => f.key === key);
}

function render() {
  const d = view;
  renderKV($("#device"), d.device);
  const dw = $("#device-warning");
  dw.textContent = d.device_warning || "";
  dw.classList.toggle("hidden", !d.device_warning);

  if (d.settings) {
    const wm = d.work_mode;
    const sel = $("#work-mode");
    if ([0, 2, 3].includes(wm)) sel.value = String(wm);
    $("#work-mode-current").textContent = `Attuale: ${field("mode", "work_mode").value}`;
    renderKV($("#mode-extra"), d.settings.mode.filter((f) => f.key !== "work_mode"));
    $("#soc-protect").value = d.soc_protect_on ? "1" : "0";
    const dod = field("battery", "ongrid_dod");
    $("#ongrid-dod").value = dod.value;
    $("#ongrid-dod-hint").textContent = d.soc_protect_on ? `SoC minimo ${dod.raw} %` : "Attiva la protezione SoC per modificarla";
    renderKV($("#battery-extra"), d.settings.battery.filter((f) => !["soc_protect", "ongrid_dod"].includes(f.key)));
    $("#export-limit").value = field("grid", "export_limit_w").value;
    renderKV($("#grid-extra"), d.settings.grid.filter((f) => f.key !== "export_limit_w"));
  }
  if (d.offgrid_dod) $("#offgrid-dod").value = d.offgrid_dod.value;
  if (d.eco) renderEco(d.eco);
  if (d.protection) renderKV($("#protection"), d.protection);
  if (d.connect) renderKV($("#connect"), d.connect);

  const times = Object.values(d.read_at || {});
  if (times.length) {
    const t = new Date(Math.max(...times) * 1000);
    $("#readinfo").textContent = `Ultima lettura dall'inverter: ${t.toLocaleString("it-IT")}. I valori non si aggiornano da soli.`;
  }
}

function renderEco(groups) {
  const tl = $("#timeline");
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  tl.innerHTML = groups.map((g, i) => {
    const s = toMin(g.start), e = toMin(g.end);
    if (e <= s) return "";
    const cls = g.enabled ? g.mode : "off";
    return `<div class="bar ${cls}" style="top:${4 + i * 18}px;left:${(s / 1440) * 100}%;width:${((e - s) / 1440) * 100}%"
      title="Fascia ${i + 1}: ${g.start}-${g.end} ${g.mode} ${g.power_abs}%">${i + 1}</div>`;
  }).join("") + `<div class="hours">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span>${h}</span>`).join("")}</div>`;

  $("#eco").innerHTML = groups.map((g) => {
    const days = DAY_ORDER.map((i) =>
      `<label><input type="checkbox" data-day="${i}" ${g.days & (1 << i) ? "checked" : ""}><span>${DAYS[i]}</span></label>`).join("");
    const color = g.enabled ? (g.mode === "carica" ? "var(--charge)" : "var(--discharge)") : "var(--off)";
    return `<div class="eco-card" data-index="${g.index}">
      <header><span><span class="dot" style="background:${color}"></span>Fascia ${g.index + 1}</span>
        <span class="small ${g.enabled ? "ok-text" : "muted"}">${g.enabled ? "attiva" : "non attiva"}</span></header>
      <div class="row"><input type="time" data-k="start" value="${esc(g.start)}" aria-label="Inizio"> →
        <input type="time" data-k="end" value="${esc(g.end)}" aria-label="Fine"></div>
      <div class="row"><select data-k="mode" aria-label="Carica o scarica">
          <option value="carica" ${g.mode === "carica" ? "selected" : ""}>Carica</option>
          <option value="scarica" ${g.mode === "scarica" ? "selected" : ""}>Scarica</option></select>
        <input type="number" data-k="power_abs" min="0" max="100" value="${g.power_abs}" aria-label="Potenza"> %</div>
      <div class="days">${days}</div>
      <div class="row">
        <button class="primary" data-eco-save>Salva e attiva</button>
        <button class="secondary" data-eco-toggle="${g.enabled ? 0 : 1}">${g.enabled ? "Disattiva" : "Attiva"}</button>
      </div>
    </div>`;
  }).join("");
}

function renderExchanges(list) {
  if (!list || !list.length) return;
  $("#exchanges").innerHTML = list.map((x) => `<div class="ex">
    <div><b>${esc(x.description)}</b> ${x.sent ? "" : "<i>(non inviato)</i>"} ${x.attempts ? `· tentativi ${x.attempts}` : ""} ${x.elapsed_ms ? `· ${x.elapsed_ms} ms` : ""}</div>
    <div>→ ${esc(x.request_hex)}</div>
    ${x.response_hex ? `<div>← ${esc(x.response_hex)}</div>` : ""}
    ${x.error ? `<div class="err-text">${esc(x.error)}</div>` : ""}</div>`).join("");
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    if ("start" in v) return `${v.start}-${v.end} ${v.mode} ${v.power_abs}% ${(v.days_list || []).join(" ")} ${v.enabled ? "(attiva)" : "(non attiva)"}`;
    return JSON.stringify(v);
  }
  return String(v);
}

async function loadLog() {
  const { changes } = await api("/api/registro");
  $("#log tbody").innerHTML = changes.map((c) => `<tr>
    <td>${esc(new Date(c.quando).toLocaleString("it-IT"))}</td><td>${esc(c.etichetta)}</td>
    <td>${esc(fmt(c.prima))}</td><td>${esc(fmt(c.richiesto))}</td><td>${esc(fmt(c.riletto))}</td>
    <td class="${c.esito === "ok" ? "ok-text" : c.prova_a_secco ? "muted" : "err-text"}">${esc(c.esito)}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">Nessuna modifica registrata.</td></tr>`;
}

// --- azioni -----------------------------------------------------------------

async function readAll(btn) {
  await busy(btn, async () => {
    banner("");
    const r = await api("/api/leggi", {});
    view = r.data; render(); renderExchanges(r.exchanges);
    const errs = Object.entries(r.errors || {});
    if (errs.length) banner(errs.map(([k, v]) => `${k}: ${v}`).join("\n"));
  });
}

async function save(setting, payload, btn) {
  await busy(btn, async () => {
    banner("");
    const plan = await api("/api/prepara", { setting, ...payload });
    const dry = $("#dry").checked || !plan.writes_enabled;
    $("#c-title").textContent = plan.label;
    $("#c-before").textContent = fmt(plan.before);
    $("#c-after").textContent = fmt(plan.after);
    $("#c-warning").textContent = plan.warning || "";
    $("#c-warning").classList.toggle("hidden", !plan.warning);
    $("#c-mode").textContent = !plan.writes_enabled
      ? "Le scritture sono disattivate sul server (WRITES_ENABLED=false): verrà solo registrata una prova a secco."
      : dry ? "Prova a secco: i frame qui sotto NON verranno inviati." : "Verranno inviati questi frame, poi il valore verrà riletto:";
    $("#c-frames").innerHTML = plan.frames.map((f) => `<li>${esc(f.description)}<br>${esc(f.request_hex)}</li>`).join("");
    $("#c-go").textContent = dry ? "Registra prova a secco" : "Invia all'inverter";
    const dlg = $("#confirm");
    dlg.returnValue = "";
    dlg.showModal();
    const choice = await new Promise((res) => dlg.addEventListener("close", () => res(dlg.returnValue), { once: true }));
    if (choice !== "go") return;
    const r = await api("/api/esegui", { id: plan.id, dry_run: dry });
    view = r.data; render(); renderExchanges(r.exchanges);
    const ok = r.outcome === "ok" || r.outcome === "prova a secco";
    banner(`${plan.label}: ${r.outcome}${r.reread !== null && r.reread !== undefined ? ` — valore riletto: ${fmt(r.reread)}` : ""}${r.message ? `\n${r.message}` : ""}`, ok);
    loadLog().catch(() => {});
  });
}

function ecoPayload(card, enabledOverride) {
  const g = { index: Number(card.dataset.index), enabled: true };
  card.querySelectorAll("[data-k]").forEach((el) => (g[el.dataset.k] = el.value));
  g.power_abs = Number(g.power_abs);
  g.days = 0;
  card.querySelectorAll("[data-day]").forEach((el) => { if (el.checked) g.days |= 1 << Number(el.dataset.day); });
  if (enabledOverride !== undefined) g.enabled = enabledOverride;
  return g;
}

document.addEventListener("click", (ev) => {
  const t = ev.target;
  if (!(t instanceof HTMLButtonElement)) return;
  const s = t.dataset.save;
  if (s === "work_mode") save(s, { value: Number($("#work-mode").value) }, t);
  else if (s === "soc_protect") save(s, { value: $("#soc-protect").value === "1" }, t);
  else if (s === "ongrid_dod") save(s, { value: Number($("#ongrid-dod").value) }, t);
  else if (s === "offgrid_dod") save(s, { value: Number($("#offgrid-dod").value) }, t);
  else if (s === "export_limit_w") save(s, { value: Number($("#export-limit").value) }, t);
  else if (t.hasAttribute("data-eco-save")) {
    const g = ecoPayload(t.closest(".eco-card"));
    if (!g.days) { banner("Seleziona almeno un giorno."); return; }
    save("eco_group", { group: g }, t);
  } else if (t.dataset.ecoToggle !== undefined) {
    const card = t.closest(".eco-card");
    save("eco_switch", { group: { index: Number(card.dataset.index), enabled: t.dataset.ecoToggle === "1" } }, t);
  }
});

$("#refresh").addEventListener("click", (e) => readAll(e.currentTarget));
$("#read-protection").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  banner("");
  const r = await api("/api/leggi/protezioni", {});
  view = r.data; render(); renderExchanges(r.exchanges);
  const errs = Object.values(r.errors || {});
  if (errs.length) banner(errs.join("\n"));
}));
$("#load-log").addEventListener("click", (e) => busy(e.currentTarget, loadLog));
$("#cfg").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    config = await api("/api/config", { host: $("#host").value.trim(), port: Number($("#port").value) || 8899 });
    banner(`IP salvato: ${config.host}`, true);
    await readAll(null);
  });
});

(async function init() {
  try {
    config = await api("/api/config");
    $("#host").value = config.host || "";
    $("#port").value = config.port || 8899;
    const b = $("#wr-badge");
    b.textContent = config.writes_enabled ? "scritture abilitate" : "sola lettura";
    b.classList.toggle("on", !!config.writes_enabled);
    loadLog().catch(() => {});
    if (config.host) await readAll(null);
    else banner("Inserisci l'IP dell'inverter e premi «Salva IP».");
  } catch (e) { banner(e.message); }
})();
