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
  const all = [...document.querySelectorAll("main button, header button, main input, main select")];
  const prev = new Map(all.map((b) => [b, b.disabled]));
  all.forEach((b) => (b.disabled = true));
  const old = btn ? btn.textContent : "";
  if (btn) btn.textContent = "Please wait…";
  try { return await fn(); }
  catch (e) { banner(e.message); }
  finally {
    if (btn) btn.textContent = old;
    all.forEach((b) => { if (b.isConnected) b.disabled = prev.get(b); });
    Schedule.refreshButtons();
  }
}

// --- "?" explanations ---------------------------------------------------------

const tip = $("#tip");
let tipOwner = null;

function showTip(btn) {
  tipOwner = btn;
  tip.textContent = btn.dataset.help;
  tip.classList.remove("hidden");
  const r = btn.getBoundingClientRect();
  const w = Math.min(360, window.innerWidth - 32);
  tip.style.width = `${w}px`;
  const left = Math.max(16, Math.min(r.left + r.width / 2 - w / 2, window.innerWidth - w - 16));
  tip.style.left = `${left + window.scrollX}px`;
  const below = r.bottom + 8;
  tip.style.top = `${below + window.scrollY}px`;
  const h = tip.offsetHeight;
  if (below + h > window.innerHeight && r.top - h - 8 > 0) tip.style.top = `${r.top - h - 8 + window.scrollY}px`;
  btn.setAttribute("aria-expanded", "true");
}

function hideTip() {
  if (tipOwner) tipOwner.setAttribute("aria-expanded", "false");
  tipOwner = null;
  tip.classList.add("hidden");
}

// hover only for a real mouse: on touch screens the emulated mouse events would close the tip right after a tap
document.addEventListener("pointerover", (e) => {
  if (e.pointerType !== "mouse") return;
  const b = e.target.closest?.(".help"); if (b) showTip(b);
});
document.addEventListener("pointerout", (e) => {
  if (e.pointerType !== "mouse") return;
  const b = e.target.closest?.(".help"); if (b && b === tipOwner && !b.contains(e.relatedTarget)) hideTip();
});
document.addEventListener("focusin", (e) => { if (e.target.classList?.contains("help")) showTip(e.target); });
document.addEventListener("focusout", (e) => { if (e.target === tipOwner) hideTip(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
document.addEventListener("click", (e) => {
  const b = e.target.closest(".help");
  // a tap also fires "mouseover" just before: always show here, close by tapping elsewhere or Esc
  if (b) { e.preventDefault(); showTip(b); return; }
  if (!tip.contains(e.target)) hideTip();
});
window.addEventListener("scroll", () => { if (tipOwner && !tipOwner.matches(":focus")) hideTip(); }, { passive: true });

function helpBtn(text) {
  return text ? `<button type="button" class="help" aria-label="Explain" aria-expanded="false" data-help="${esc(text)}">?</button>` : "";
}

// --- rows ---------------------------------------------------------------------

function valueText(f) {
  const unit = f.unit ? ` ${esc(f.unit)}` : "";
  return `${esc(f.value ?? "—")}${unit}`;
}

function controlHtml(f) {
  const c = f.control;
  const raw = f.raw !== null && f.raw !== undefined && f.raw !== f.value ? `<span class="raw">raw ${esc(f.raw)}</span>` : "";
  if (!c) {
    const lock = f.protected && !f.key.startsWith("reg_") ? ` <span class="badge lock">read-only</span>` : "";
    return `<span class="val">${valueText(f)}</span>${lock}${raw}`;
  }
  const dis = c.disabled ? "disabled" : "";
  const reason = c.disabled && c.disabled_reason ? `<span class="raw">${esc(c.disabled_reason)}</span>` : "";
  const id = `ctl-${c.setting}`;
  if (c.type === "switch") {
    return `<label class="toggle"><input type="checkbox" role="switch" id="${id}" data-setting="${c.setting}" data-kind="switch"
      ${c.on ? "checked" : ""} ${dis} aria-label="${esc(f.label)}"><span class="track"></span></label>
      <span class="val">${valueText(f)}</span>${raw}${reason}`;
  }
  if (c.type === "number") {
    return `<span class="inline"><input type="number" id="${id}" min="${c.min}" max="${c.max}" step="${c.step}" value="${esc(f.value)}" ${dis}
      aria-label="${esc(f.label)}">${f.unit ? `<span>${esc(f.unit)}</span>` : ""}
      <button type="button" class="primary" data-setting="${c.setting}" data-kind="number" ${dis}>Save</button></span>${raw}${reason}`;
  }
  if (c.type === "select") {
    const opts = c.options.map(([v, l]) => `<option value="${esc(v)}" ${v === c.current ? "selected" : ""}>${esc(l)}</option>`).join("");
    const unknown = c.options.some(([v]) => v === c.current) ? "" : `<option value="" selected disabled>${esc(f.value)}</option>`;
    return `<span class="inline"><select id="${id}" aria-label="${esc(f.label)}" ${dis}>${unknown}${opts}</select>
      <button type="button" class="primary" data-setting="${c.setting}" data-kind="select" ${dis}>Save</button></span>${raw}${reason}`;
  }
  if (c.type === "action") {
    return `<button type="button" class="secondary" data-setting="${c.setting}" data-kind="action">${esc(c.button)}</button>`;
  }
  return valueText(f);
}

function renderRows(el, fields) {
  el.innerHTML = (fields || []).map((f) => `<div class="srow${f.protected ? " protected" : ""}">
      <div class="slabel"><span>${esc(f.label)}</span>${helpBtn(f.help)}${f.note ? `<div class="raw">${esc(f.note)}</div>` : ""}</div>
      <div class="sval">${controlHtml(f)}</div>
    </div>`).join("");
}

function render() {
  const d = view;
  hideTip();
  renderRows($("#device"), d.device);
  const dw = $("#device-warning");
  dw.textContent = d.device_warning || "";
  dw.classList.toggle("hidden", !d.device_warning);
  if (d.settings) {
    renderRows($("#mode"), d.settings.mode);
    renderRows($("#battery"), d.settings.battery);
    renderRows($("#grid"), d.settings.grid);
  }
  if (d.eco) Schedule.load(d.eco);
  if (d.protection) renderRows($("#protection"), d.protection);
  if (d.connect) renderRows($("#connect"), d.connect);

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

// --- actions ------------------------------------------------------------------

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
  let applied = false;
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
    applied = !dry;
    view = r.data; render(); renderExchanges(r.exchanges);
    const ok = r.outcome === "ok" || r.outcome === "dry run";
    banner(`${plan.label}: ${r.outcome}${r.reread !== null && r.reread !== undefined ? ` — value read back: ${fmt(r.reread)}` : ""}${r.message ? `\n${r.message}` : ""}`, ok);
    loadLog().catch(() => {});
  });
  // cancelled, dry run or error: put switches and fields back to the values read from the inverter
  if (!applied && setting !== "eco_schedule") render();
}

document.addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.dataset?.kind === "switch") save(t.dataset.setting, { value: t.checked }, null);
});

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("button[data-kind]");
  if (!t) return;
  const setting = t.dataset.setting;
  if (t.dataset.kind === "number") {
    const v = $(`#ctl-${setting}`).value;
    if (v === "") { banner("Enter a value first."); return; }
    save(setting, { value: Number(v) }, t);
  } else if (t.dataset.kind === "select") {
    const v = $(`#ctl-${setting}`).value;
    if (v === "") { banner("Choose a value first."); return; }
    save(setting, { value: Number(v) }, t);
  } else if (t.dataset.kind === "action") {
    save(setting, {}, t);
  }
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
    config = { ...config, ...(await api("/api/config", { host: $("#host").value.trim(), port: Number($("#port").value) || 8899 })) };
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
