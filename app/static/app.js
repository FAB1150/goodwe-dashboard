"use strict";

const $ = (s) => document.querySelector(s);
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
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP error ${r.status}`);
  return data;
}

async function busy(btn, fn) {
  const all = [...document.querySelectorAll("main button, header button")];
  const prev = new Map(all.map((b) => [b, b.disabled]));
  all.forEach((b) => (b.disabled = true));
  const old = btn ? btn.textContent : "";
  if (btn) btn.textContent = "Please wait…";
  try { return await fn(); }
  catch (e) { banner(e.message); }
  finally {
    // restore the previous state, unless the button was re-rendered meanwhile
    if (btn) btn.textContent = old;
    all.forEach((b) => (b.disabled = prev.get(b)));
    Schedule.refreshButtons();
  }
}

// --- rendering --------------------------------------------------------------

function renderKV(el, fields) {
  el.innerHTML = (fields || []).map((f) => {
    const unit = f.unit ? ` ${esc(f.unit)}` : "";
    const raw = f.raw !== null && f.raw !== undefined && f.raw !== f.value ? ` <span class="raw">(raw ${esc(f.raw)})</span>` : "";
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
    $("#work-mode-current").textContent = `Current: ${field("mode", "work_mode").value}`;
    renderKV($("#mode-extra"), d.settings.mode.filter((f) => f.key !== "work_mode"));
    $("#soc-protect").value = d.soc_protect_on ? "1" : "0";
    const dod = field("battery", "ongrid_dod");
    $("#ongrid-dod").value = dod.value;
    $("#ongrid-dod-hint").textContent = d.soc_protect_on ? `minimum SoC ${dod.raw} %` : "Turn SoC protection on to change it";
    renderKV($("#battery-extra"), d.settings.battery.filter((f) => !["soc_protect", "ongrid_dod"].includes(f.key)));
    $("#export-limit").value = field("grid", "export_limit_w").value;
    renderKV($("#grid-extra"), d.settings.grid.filter((f) => f.key !== "export_limit_w"));
  }
  if (d.offgrid_dod) $("#offgrid-dod").value = d.offgrid_dod.value;
  if (d.eco) Schedule.load(d.eco);
  if (d.protection) renderKV($("#protection"), d.protection);
  if (d.connect) renderKV($("#connect"), d.connect);

  const times = Object.values(d.read_at || {});
  if (times.length) {
    const t = new Date(Math.max(...times) * 1000);
    $("#readinfo").textContent = `Last read from the inverter: ${t.toLocaleString()}. Values do not refresh on their own.`;
  }
}

function renderExchanges(list) {
  if (!list || !list.length) return;
  $("#exchanges").innerHTML = list.map((x) => `<div class="ex">
    <div><b>${esc(x.description)}</b> ${x.sent ? "" : "<i>(not sent)</i>"} ${x.attempts ? `· attempts ${x.attempts}` : ""} ${x.elapsed_ms ? `· ${x.elapsed_ms} ms` : ""}</div>
    <div>→ ${esc(x.request_hex)}</div>
    ${x.response_hex ? `<div>← ${esc(x.response_hex)}</div>` : ""}
    ${x.error ? `<div class="err-text">${esc(x.error)}</div>` : ""}</div>`).join("");
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.map(fmt).join("; ") : "—";
  if (typeof v === "object") {
    if ("start" in v) return `#${v.index + 1} ${v.start}-${v.end} ${v.mode} ${v.power_abs}% ${(v.days_list || []).join(" ")} ${v.enabled ? "(enabled)" : "(disabled)"}`;
    return JSON.stringify(v);
  }
  return String(v);
}

async function loadLog() {
  const { changes } = await api("/api/changes");
  $("#log tbody").innerHTML = changes.map((c) => `<tr>
    <td>${esc(new Date(c.at).toLocaleString())}</td><td>${esc(c.label)}</td>
    <td>${esc(fmt(c.before))}</td><td>${esc(fmt(c.requested))}</td><td>${esc(fmt(c.reread))}</td>
    <td class="${c.outcome === "ok" ? "ok-text" : c.dry_run ? "muted" : "err-text"}">${esc(c.outcome)}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">No changes logged yet.</td></tr>`;
}

// --- actions ----------------------------------------------------------------

async function readAll(btn) {
  if (btn && Schedule.hasChanges() && !confirm("Discard the unsaved changes to the charge/discharge windows?")) return;
  await busy(btn, async () => {
    banner("");
    const r = await api("/api/read", {});
    view = r.data; render(); renderExchanges(r.exchanges);
    const errs = Object.entries(r.errors || {});
    if (errs.length) banner(errs.map(([k, v]) => `${k}: ${v}`).join("\n"));
  });
}

async function save(setting, payload, btn) {
  await busy(btn, async () => {
    banner("");
    const plan = await api("/api/prepare", { setting, ...payload });
    const dry = $("#dry").checked || !plan.writes_enabled;
    $("#c-title").textContent = plan.label;
    $("#c-before").textContent = fmt(plan.before);
    $("#c-after").textContent = fmt(plan.after);
    $("#c-warning").textContent = plan.warning || "";
    $("#c-warning").classList.toggle("hidden", !plan.warning);
    $("#c-mode").textContent = !plan.writes_enabled
      ? "Writes are disabled on the server (WRITES_ENABLED=false): only a dry run will be logged."
      : dry ? "Dry run: the frames below will NOT be sent." : "These frames will be sent, then the value will be read back:";
    $("#c-frames").innerHTML = plan.frames.map((f) => `<li>${esc(f.description)}<br>${esc(f.request_hex)}</li>`).join("");
    $("#c-go").textContent = dry ? "Log dry run" : "Send to inverter";
    const dlg = $("#confirm");
    dlg.returnValue = "";
    dlg.showModal();
    const choice = await new Promise((res) => dlg.addEventListener("close", () => res(dlg.returnValue), { once: true }));
    if (choice !== "go") return;
    const r = await api("/api/execute", { id: plan.id, dry_run: dry });
    view = r.data; render(); renderExchanges(r.exchanges);
    const ok = r.outcome === "ok" || r.outcome === "dry run";
    banner(`${plan.label}: ${r.outcome}${r.reread !== null && r.reread !== undefined ? ` — value read back: ${fmt(r.reread)}` : ""}${r.message ? `\n${r.message}` : ""}`, ok);
    loadLog().catch(() => {});
  });
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
});

Schedule.init($("#schedule"), (groups, btn) => save("eco_schedule", { groups }, btn), (msg) => banner(msg));

$("#refresh").addEventListener("click", (e) => readAll(e.currentTarget));
$("#read-protection").addEventListener("click", (e) => busy(e.currentTarget, async () => {
  banner("");
  const r = await api("/api/read/protection", {});
  view = r.data; render(); renderExchanges(r.exchanges);
  const errs = Object.values(r.errors || {});
  if (errs.length) banner(errs.join("\n"));
}));
$("#load-log").addEventListener("click", (e) => busy(e.currentTarget, loadLog));
$("#cfg").addEventListener("submit", (e) => {
  e.preventDefault();
  busy(e.submitter, async () => {
    config = await api("/api/config", { host: $("#host").value.trim(), port: Number($("#port").value) || 8899 });
    banner(`IP saved: ${config.host}`, true);
    await readAll(null);
  });
});

(async function init() {
  try {
    config = await api("/api/config");
    $("#host").value = config.host || "";
    $("#port").value = config.port || 8899;
    const b = $("#wr-badge");
    b.textContent = config.writes_enabled ? "writes enabled" : "read-only";
    b.classList.toggle("on", !!config.writes_enabled);
    loadLog().catch(() => {});
    if (config.host) await readAll(null);
    else banner("Enter the inverter IP and press “Save IP”.");
  } catch (e) { banner(e.message); }
})();
