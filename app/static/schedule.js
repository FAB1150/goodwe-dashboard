"use strict";
/* Weekly timeline editor for the 4 eco groups ("charge/discharge windows").
 *
 * - drag a window to move it, drag its edges to resize it (15-minute steps);
 * - click a window to edit mode, power, exact times and days;
 * - click (or drag) on an empty spot to add a window, using a free slot;
 * - "Remove" frees the slot (the group is written as disabled).
 * Everything is a local draft until "Save changes", which sends all changed groups
 * in one confirmed operation (see app.js -> save("eco_schedule", ...)).
 */
const Schedule = (() => {
  const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const ROWS = [1, 2, 3, 4, 5, 6, 0]; // Mon..Sun
  const SNAP = 15;
  const MAX_END = 23 * 60 + 59; // the inverter cannot store 24:00
  const MIN_LEN = 15;
  const SLOTS = 4;

  let orig = [];
  let draft = [];
  let selected = null;
  let drag = null;
  let onSave = null;
  let notify = () => {};
  const el = {};

  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const toHHMM = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const clone = (g) => ({ ...g });
  const snap = (m) => Math.round(m / SNAP) * SNAP;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function same(a, b) {
    return a.start === b.start && a.end === b.end && a.power === b.power && a.enabled === b.enabled && a.days === b.days;
  }
  function changed() { return draft.filter((g, i) => !same(g, orig[i])); }
  function power(g) { return g.mode === "charge" ? -g.power_abs : g.power_abs; }
  function setPower(g, mode, abs) { g.mode = mode; g.power_abs = abs; g.power = mode === "charge" ? -abs : abs; }

  // Same rules as the backend / SolarGo: overlapping enabled windows sharing a day
  // must have the same mode and the same power.
  function conflicts() {
    const out = [];
    const on = draft.filter((g) => g.enabled);
    for (let i = 0; i < on.length; i++) {
      for (let j = i + 1; j < on.length; j++) {
        const a = on[i], b = on[j];
        if (!(a.days & b.days)) continue;
        if (!(toMin(a.start) < toMin(b.end) && toMin(b.start) < toMin(a.end))) continue;
        if (a.mode !== b.mode) out.push({ a: a.index, b: b.index, msg: `windows ${a.index + 1} and ${b.index + 1} overlap with opposite modes` });
        else if (power(a) !== power(b)) out.push({ a: a.index, b: b.index, msg: `windows ${a.index + 1} and ${b.index + 1} overlap with different powers` });
      }
    }
    return out;
  }

  // --- rendering ------------------------------------------------------------

  function build(root) {
    root.innerHTML = `
      <div class="sched-toolbar">
        <span class="legend"><i class="sw charge"></i>Charge <i class="sw discharge"></i>Discharge</span>
        <span id="sched-slots" class="muted small"></span>
        <span class="spacer"></span>
        <button type="button" class="secondary" id="sched-discard">Discard changes</button>
        <button type="button" class="primary" id="sched-save">Save changes</button>
      </div>
      <p class="muted small">Click or drag on an empty spot to add a window, drag a window to move it, drag its
        edges to resize it, click it to edit. Nothing is sent until you press “Save changes”.</p>
      <div class="sched" id="sched-grid">
        <div class="sched-hours"><span></span><div>${Array.from({ length: 9 }, (_, i) =>
          `<span style="left:${(i * 3 / 24) * 100}%">${i * 3}</span>`).join("")}</div></div>
        ${ROWS.map((d) => `<div class="sched-row"><span class="sched-day">${DAY_NAMES[d]}</span>
          <div class="sched-track" data-day="${d}"></div></div>`).join("")}
      </div>
      <p id="sched-conflicts" class="warn hidden"></p>
      <div id="sched-editor" class="sched-editor hidden"></div>`;
    el.grid = root.querySelector("#sched-grid");
    el.slots = root.querySelector("#sched-slots");
    el.save = root.querySelector("#sched-save");
    el.discard = root.querySelector("#sched-discard");
    el.conflicts = root.querySelector("#sched-conflicts");
    el.editor = root.querySelector("#sched-editor");
    el.tracks = [...root.querySelectorAll(".sched-track")];

    el.grid.addEventListener("pointerdown", onPointerDown);
    el.grid.addEventListener("keydown", onKey);
    el.discard.addEventListener("click", () => { draft = orig.map(clone); selected = null; renderAll(); });
    el.save.addEventListener("click", () => {
      const list = changed();
      if (!list.length || !onSave) return;
      onSave(list.map((g) => ({ index: g.index, start: g.start, end: g.end, mode: g.mode, power_abs: g.power_abs, days: g.days, enabled: g.enabled })), el.save);
    });
  }

  function renderBlocks() {
    const bad = conflicts();
    const badSet = new Set(bad.flatMap((c) => [c.a, c.b]));
    for (const track of el.tracks) {
      const day = Number(track.dataset.day);
      track.innerHTML = draft.filter((g) => g.enabled && g.days & (1 << day)).map((g) => {
        const s = toMin(g.start), e = toMin(g.end);
        const cls = ["win", g.mode, g.index === selected ? "sel" : "", same(g, orig[g.index]) ? "" : "dirty", badSet.has(g.index) ? "conflict" : ""].join(" ");
        return `<div class="${cls}" data-index="${g.index}" tabindex="0" role="button"
          aria-label="Window ${g.index + 1}: ${g.mode} ${g.power_abs}% ${g.start} to ${g.end}"
          style="left:${(s / 1440) * 100}%;width:${((e - s) / 1440) * 100}%" title="Window ${g.index + 1}: ${g.start}–${g.end}, ${g.mode} ${g.power_abs}%">
          <span class="h l"></span><span class="lbl">${g.index + 1} · ${g.mode === "charge" ? "▲" : "▼"}${g.power_abs}%</span><span class="h r"></span></div>`;
      }).join("");
    }
    const used = draft.filter((g) => g.enabled).length;
    el.slots.textContent = `${used}/${SLOTS} windows used`;
    const n = changed().length;
    el.save.disabled = !n || bad.length > 0;
    el.save.textContent = n ? `Save changes (${n})` : "Save changes";
    el.discard.disabled = !n;
    el.conflicts.textContent = bad.map((c) => c.msg).join("; ") + (bad.length ? ". SolarGo refuses this: fix it before saving." : "");
    el.conflicts.classList.toggle("hidden", !bad.length);
  }

  function renderEditor() {
    const g = draft[selected];
    if (selected === null || !g || !g.enabled) { el.editor.classList.add("hidden"); el.editor.innerHTML = ""; return; }
    el.editor.classList.remove("hidden");
    el.editor.innerHTML = `
      <header><b>Window ${g.index + 1}</b>
        <button type="button" class="secondary small-btn" data-ed="close" aria-label="Close editor">✕</button></header>
      <div class="ed-grid">
        <span>Action</span>
        <div class="seg" role="radiogroup" aria-label="Action">
          <button type="button" data-ed="mode" data-v="charge" class="${g.mode === "charge" ? "on charge" : ""}" aria-pressed="${g.mode === "charge"}">Charge battery</button>
          <button type="button" data-ed="mode" data-v="discharge" class="${g.mode === "discharge" ? "on discharge" : ""}" aria-pressed="${g.mode === "discharge"}">Discharge battery</button>
        </div>
        <label for="ed-power">Power</label>
        <div class="row"><input id="ed-power" type="range" min="1" max="100" value="${g.power_abs}" data-ed="power">
          <input type="number" min="1" max="100" value="${g.power_abs}" data-ed="power" aria-label="Power %"> % of rated power</div>
        <span>Time</span>
        <div class="row"><input type="time" value="${g.start}" data-ed="start" aria-label="Start"> →
          <input type="time" value="${g.end}" data-ed="end" aria-label="End"></div>
        <span>Days</span>
        <div>
          <div class="days">${ROWS.map((d) => `<label><input type="checkbox" data-ed="day" data-day="${d}" ${g.days & (1 << d) ? "checked" : ""}><span>${DAY_NAMES[d]}</span></label>`).join("")}</div>
          <div class="row quick">
            <button type="button" class="secondary small-btn" data-ed="days" data-v="127">Every day</button>
            <button type="button" class="secondary small-btn" data-ed="days" data-v="62">Weekdays</button>
            <button type="button" class="secondary small-btn" data-ed="days" data-v="65">Weekend</button>
          </div>
        </div>
      </div>
      <div class="row end"><button type="button" class="danger" data-ed="remove">Remove window</button></div>`;
    el.editor.oninput = onEditorInput;
    el.editor.onchange = onEditorInput;
    el.editor.onclick = onEditorClick;
  }

  function renderAll() { renderBlocks(); renderEditor(); }

  // --- editor ---------------------------------------------------------------

  function onEditorInput(ev) {
    const t = ev.target, g = draft[selected];
    if (!g || !t.dataset.ed) return;
    if (t.dataset.ed === "power") {
      const v = clamp(Number(t.value) || 1, 1, 100);
      setPower(g, g.mode, v);
      el.editor.querySelectorAll('[data-ed="power"]').forEach((x) => { if (x !== t) x.value = v; });
      renderBlocks();
    } else if ((t.dataset.ed === "start" || t.dataset.ed === "end") && ev.type === "change" && t.value) {
      const s = t.dataset.ed === "start" ? toMin(t.value) : toMin(g.start);
      const e = t.dataset.ed === "end" ? Math.min(toMin(t.value), MAX_END) : toMin(g.end);
      if (e <= s) { notify("The start time must be before the end time (a window cannot cross midnight: use two windows)."); renderEditor(); return; }
      g.start = toHHMM(s); g.end = toHHMM(e);
      renderBlocks();
    } else if (t.dataset.ed === "day") {
      const bit = 1 << Number(t.dataset.day);
      const days = t.checked ? g.days | bit : g.days & ~bit;
      if (!days) { t.checked = true; notify("A window needs at least one day."); return; }
      g.days = days;
      renderBlocks();
    }
  }

  function onEditorClick(ev) {
    const t = ev.target.closest("button");
    if (!t) return;
    const g = draft[selected];
    switch (t.dataset.ed) {
      case "close": selected = null; renderAll(); break;
      case "mode": setPower(g, t.dataset.v, g.power_abs); renderAll(); break;
      case "days": g.days = Number(t.dataset.v); renderAll(); break;
      case "remove": g.enabled = false; selected = null; renderAll(); break;
      default:
    }
  }

  // --- pointer interaction --------------------------------------------------

  function minutesAt(track, clientX) {
    const r = track.getBoundingClientRect();
    return clamp(((clientX - r.left) / r.width) * 1440, 0, 1440);
  }

  function onPointerDown(ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    const track = ev.target.closest(".sched-track");
    if (!track) return;
    const win = ev.target.closest(".win");
    const at = minutesAt(track, ev.clientX);
    if (win) {
      const g = draft[Number(win.dataset.index)];
      const r = win.getBoundingClientRect();
      const edge = Math.min(10, r.width / 4);
      const part = ev.clientX - r.left < edge ? "start" : r.right - ev.clientX < edge ? "end" : "move";
      drag = { g, part, track, x0: ev.clientX, s0: toMin(g.start), e0: toMin(g.end), at0: at, moved: false, created: false };
    } else {
      const free = draft.find((g) => !g.enabled);
      if (!free) { notify(`All ${SLOTS} windows are in use: remove one before adding another.`); return; }
      const s = clamp(Math.floor(at / SNAP) * SNAP, 0, MAX_END - MIN_LEN);
      const prev = draft.filter((g) => g.enabled && g !== free).pop();
      free.enabled = true;
      free.start = toHHMM(s);
      free.end = toHHMM(Math.min(s + 60, MAX_END));
      free.days = 1 << Number(track.dataset.day);
      setPower(free, prev ? prev.mode : "charge", prev ? prev.power_abs : 50);
      selected = free.index;
      drag = { g: free, part: "end", track, x0: ev.clientX, s0: s, e0: s + 60, at0: at, moved: false, created: true };
      renderBlocks();
    }
    ev.preventDefault();
    try { el.grid.setPointerCapture(ev.pointerId); } catch { /* ignore */ }
    el.grid.addEventListener("pointermove", onPointerMove);
    el.grid.addEventListener("pointerup", onPointerUp, { once: true });
    el.grid.addEventListener("pointercancel", onPointerUp, { once: true });
  }

  function onPointerMove(ev) {
    if (!drag) return;
    if (Math.abs(ev.clientX - drag.x0) > 3) drag.moved = true;
    if (!drag.moved) return;
    const at = minutesAt(drag.track, ev.clientX);
    const g = drag.g;
    let s = drag.s0, e = drag.e0;
    if (drag.part === "move") {
      const len = drag.e0 - drag.s0;
      s = clamp(snap(drag.s0 + (at - drag.at0)), 0, MAX_END - len);
      e = s + len;
      if (e > MAX_END) { e = MAX_END; s = e - len; }
    } else if (drag.part === "start") {
      s = clamp(snap(at), 0, drag.e0 - MIN_LEN);
    } else {
      e = clamp(snap(at), drag.s0 + MIN_LEN, MAX_END + 1);
      if (e > MAX_END) e = MAX_END;
    }
    g.start = toHHMM(s);
    g.end = toHHMM(e);
    renderBlocks();
  }

  function onPointerUp() {
    el.grid.removeEventListener("pointermove", onPointerMove);
    if (!drag) return;
    const g = drag.g;
    if (!drag.moved || drag.created) selected = g.index;
    drag = null;
    renderAll();
  }

  function onKey(ev) {
    const win = ev.target.closest(".win");
    if (!win) return;
    const g = draft[Number(win.dataset.index)];
    let s = toMin(g.start), e = toMin(g.end);
    const step = SNAP;
    if (ev.key === "Enter" || ev.key === " ") { selected = g.index; renderAll(); ev.preventDefault(); return; }
    if (ev.key === "Delete" || ev.key === "Backspace") { g.enabled = false; selected = null; renderAll(); ev.preventDefault(); return; }
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    const d = ev.key === "ArrowLeft" ? -step : step;
    if (ev.shiftKey) e = clamp(e + d, s + MIN_LEN, MAX_END);
    else if (s + d >= 0 && e + d <= MAX_END) { s += d; e += d; }
    g.start = toHHMM(s); g.end = toHHMM(e);
    selected = g.index;
    renderAll();
    const again = el.grid.querySelector(`.win[data-index="${g.index}"]`);
    if (again) again.focus();
    ev.preventDefault();
  }

  return {
    init(root, saveFn, notifyFn) { build(root); onSave = saveFn; notify = notifyFn; },
    load(groups) {
      orig = groups.map(clone);
      draft = groups.map(clone);
      if (selected !== null && !draft[selected]?.enabled) selected = null;
      renderAll();
    },
    hasChanges() { return changed().length > 0; },
    refreshButtons() { if (el.save) renderBlocks(); },
  };
})();
