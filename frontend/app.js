/* GridWise demo frontend — one page, seven views, all driven by the real backend.
 * API: POST /ui/optimize (same pipeline as /optimize-energy + reasoning trace), GET /health, GET /ui/info.
 * Served by the backend at /app/ (same origin) or standalone (e.g. :5173) talking to :8000.
 */
(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  // Served by the backend at /app/ -> same origin. Standalone (e.g. :5500) -> ?api=... or :8000.
  const API = (params.get("api") || (location.pathname.startsWith("/app") ? "" : "http://127.0.0.1:8000")).replace(/\/$/, "");

  const VIEWS = [
    { id: "run", label: "Run", icon: "terminal", step: 1 },
    { id: "processing", label: "Processing", icon: "memory", step: 2 },
    { id: "understand", label: "Understand", icon: "psychology", step: 2 },
    { id: "plan", label: "Plan", icon: "trending_up", step: 3 },
    { id: "verify", label: "Verify", icon: "verified_user", step: 4 },
    { id: "details", label: "Details", icon: "dataset", step: 4 },
    { id: "how", label: "How it works", icon: "account_tree", step: 0 },
  ];
  const STEPS = ["Notes", "Understand", "Optimize", "Verify"];

  const TYPE_META = {
    solar_reduction: { label: "Solar reduced", color: "solar", icon: "wb_sunny" },
    minimum_battery_reserve: { label: "Battery reserve", color: "battery", icon: "battery_4_bar" },
    no_charge_window: { label: "No charging", color: "grid", icon: "power_off" },
    no_discharge_window: { label: "No discharging", color: "warn", icon: "block" },
    max_grid_window: { label: "Grid cap", color: "price", icon: "electrical_services" },
    no_op: { label: "Ignored — not a schedule change", color: "outline", icon: "do_not_disturb_on" },
  };
  const SHAPE = {
    solar_reduction: ["factor", "hours"], minimum_battery_reserve: ["hours", "minimum_energy_kwh"],
    no_charge_window: ["hours"], no_discharge_window: ["hours"], max_grid_window: ["hours", "max_grid_kwh"],
  };

  const state = {
    view: "run", samples: [], sampleIdx: 0, scenario: null, notes: [], battery: null,
    result: null, error: null, busy: false, chart: null, chartTab: "mix", info: null,
  };

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (x, d = 1) => (x == null || Number.isNaN(x) ? "—" : Number(x).toLocaleString(undefined, { maximumFractionDigits: d }));
  const cssColor = (name, alpha = 1) => `rgb(${getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()} / ${alpha})`;
  const hourLabel = (h) => `${String(h).padStart(2, "0")}:00`;
  const ranges = (hours) => {
    if (!hours || !hours.length) return "—";
    const out = []; let s = hours[0], p = hours[0];
    for (const h of hours.slice(1).concat([null])) {
      if (h === p + 1) { p = h; continue; }
      out.push(s === p ? hourLabel(s) : `${hourLabel(s)}–${hourLabel(p + 1)}`);
      s = p = h;
    }
    return out.join(", ");
  };
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

  // ---------- theme ----------
  function applyTheme(t) {
    document.documentElement.classList.toggle("dark", t === "dark");
    $("#themeIcon").textContent = t === "dark" ? "light_mode" : "dark_mode";
    store.set("gw-theme", t);
    if (state.view === "plan" && state.result) renderChart();
  }
  $("#themeBtn").addEventListener("click", () => applyTheme(document.documentElement.classList.contains("dark") ? "light" : "dark"));
  applyTheme(store.get("gw-theme") || "dark");

  // ---------- chrome ----------
  function renderNav() {
    $("#nav").innerHTML = VIEWS.map((v) => {
      const active = v.id === state.view;
      const locked = ["understand", "plan", "verify", "details"].includes(v.id) && !state.result;
      return `<a href="#${v.id}" data-view="${v.id}" class="flex items-center gap-2 px-3 py-2 rounded-md transition-colors ${
        active ? "bg-surface-container-high text-primary font-medium border-l-2 border-primary" : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
      } ${locked ? "opacity-50" : ""}"><span class="material-symbols-outlined text-[18px]">${v.icon}</span>${v.label}</a>`;
    }).join("");
    const step = (VIEWS.find((v) => v.id === state.view) || {}).step || 0;
    $("#stepper").innerHTML = STEPS.map((s, i) => {
      const n = i + 1, done = n < step || (state.result && n <= 4 && step === 0 && false), cur = n === step;
      const cls = cur ? "bg-primary/20 border-primary text-primary" : n < step ? "bg-secondary/20 border-secondary text-secondary" : "bg-surface-container-high border-outline-variant text-outline";
      const line = i < 3 ? `<div class="flex-1 h-px ${n < step ? "bg-secondary/60" : "bg-outline-variant/60"}"></div>` : "";
      return `<div class="flex items-center gap-2"><span class="w-6 h-6 rounded-full border text-[11px] font-mono font-bold flex items-center justify-center ${cls}">${done ? "✓" : n}</span><span class="text-xs font-mono ${cur ? "text-primary font-semibold" : "text-on-surface-variant"}">${s}</span></div>${line}`;
    }).join("");
  }
  window.addEventListener("hashchange", () => go(location.hash.slice(1) || "run", false));
  function go(view, push = true) {
    if (!VIEWS.some((v) => v.id === view)) view = "run";
    state.view = view;
    if (push && location.hash.slice(1) !== view) history.replaceState(null, "", `#${view}`);
    render();
  }

  async function checkApi() {
    try {
      const r = await fetch(`${API}/health`);
      const ok = r.ok && (await r.json()).status === "ok";
      $("#apiDot").className = `inline-block w-2 h-2 rounded-full ${ok ? "bg-pass animate-pulse" : "bg-error"}`;
      $("#apiText").textContent = ok ? "API online" : "API error";
      $("#apiText").className = `font-semibold ${ok ? "text-pass" : "text-error"}`;
    } catch {
      $("#apiDot").className = "inline-block w-2 h-2 rounded-full bg-error";
      $("#apiText").textContent = "API offline"; $("#apiText").className = "font-semibold text-error";
    }
    try {
      state.info = await (await fetch(`${API}/ui/info`)).json();
      $("#modelName").textContent = state.info.llm_configured ? state.info.models[0] : "no key (degraded)";
    } catch { /* optional */ }
  }

  // ---------- data ----------
  async function loadSamples() {
    state.samples = await (await fetch("samples.json")).json();
    selectSample(0);
  }
  function selectSample(i) {
    state.sampleIdx = i;
    const s = state.samples[i];
    state.scenario = JSON.parse(JSON.stringify(s.input));
    state.notes = [...s.input.operator_notes];
    state.battery = { ...s.input.battery };
  }

  async function runOptimization() {
    state.notes = [...document.querySelectorAll("textarea[data-note]")].map((t) => t.value);
    for (const k of Object.keys(state.battery)) {
      const el = document.querySelector(`input[data-batt="${k}"]`);
      if (el) state.battery[k] = Number(el.value);
    }
    const notes = state.notes.map((n) => n.trim()).filter(Boolean);
    if (!notes.length) { state.error = "Write at least one operator note."; return render(); }
    state.error = null; state.busy = true; state.result = null;
    const payload = { ...state.scenario, scenario_id: `${state.scenario.scenario_id}-ui`, operator_notes: notes, battery: state.battery };
    state.submittedNotes = notes;
    go("processing");
    const started = performance.now();
    try {
      const r = await fetch(`${API}/ui/optimize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const details = (body.details || []).map((d) => `${d.field}: ${d.issue}`).join("; ");
        throw new Error(`${body.message || `HTTP ${r.status}`}${details ? ` — ${details}` : ""}`);
      }
      state.result = { ...body, request: payload, wall_s: (performance.now() - started) / 1000 };
      state.busy = false;
      render(); // processing view shows the completed pipeline
      setTimeout(() => { if (state.view === "processing") go("understand"); }, 1300);
    } catch (e) {
      state.busy = false; state.error = e.message || String(e);
      go("run");
    }
  }

  // ---------- derived facts ----------
  function effectiveFactors(entries) {
    const f = Array(24).fill(1);
    for (const e of entries) if (e.directive_type === "solar_reduction") for (const h of e.structured_adjustment.hours) f[h] *= e.structured_adjustment.factor;
    return f;
  }
  function checks() {
    const { response, request } = state.result;
    const plan = response.hourly_plan, b = request.battery, entries = response.directive_interpretation;
    const hrs = [...request.hours].sort((a, c) => a.hour - c.hour);
    const f = effectiveFactors(entries);
    const reserve = Array(24).fill(b.minimum_energy_kwh), noC = new Set(), noD = new Set(), cap = Array(24).fill(Infinity);
    for (const e of entries) {
      const sa = e.structured_adjustment; if (!sa) continue;
      for (const h of sa.hours) {
        if (e.directive_type === "minimum_battery_reserve") reserve[h] = Math.max(reserve[h], sa.minimum_energy_kwh);
        if (e.directive_type === "no_charge_window") noC.add(h);
        if (e.directive_type === "no_discharge_window") noD.add(h);
        if (e.directive_type === "max_grid_window") cap[h] = Math.min(cap[h], sa.max_grid_kwh);
      }
    }
    const tol = 0.01; let prev = b.initial_energy_kwh;
    const c = { balance: 0, solar: 0, bounds: 0, rates: 0, rules: 0 };
    for (const p of plan) {
      const ch = p.battery_action === "charge" ? p.battery_kwh : 0, dis = p.battery_action === "discharge" ? p.battery_kwh : 0;
      const h = hrs[p.hour];
      if (Math.abs(p.grid_kwh + p.solar_used_kwh + dis - h.demand_kwh - ch) <= tol) c.balance++;
      if (p.solar_used_kwh <= h.solar_kwh * f[p.hour] + tol) c.solar++;
      const e = prev + ch - dis;
      if (Math.abs(e - p.battery_energy_after_kwh) <= tol && p.battery_energy_after_kwh >= reserve[p.hour] - tol && p.battery_energy_after_kwh <= b.capacity_kwh + tol) c.bounds++;
      prev = p.battery_energy_after_kwh;
      if (ch <= b.max_charge_kwh_per_hour + tol && dis <= b.max_discharge_kwh_per_hour + tol && (p.battery_action !== "idle" || p.battery_kwh === 0)) c.rates++;
      if (!(noC.has(p.hour) && ch > tol) && !(noD.has(p.hour) && dis > tol) && p.grid_kwh <= cap[p.hour] + tol) c.rules++;
    }
    const parity = Math.abs(plan[23].battery_energy_after_kwh - b.initial_energy_kwh) <= tol;
    const tg = plan.reduce((s, p) => s + p.grid_kwh, 0), tc = plan.reduce((s, p) => s + p.grid_kwh * hrs[p.hour].tariff_bdt_per_kwh, 0);
    const totals = Math.abs(tg - response.total_grid_kwh) <= tol && Math.abs(tc - response.total_cost_bdt) <= tol && Math.abs(Math.max(...plan.map((p) => p.grid_kwh)) - response.peak_grid_kwh) <= tol;
    return [
      { label: "Energy balanced", icon: "balance", n: c.balance, of: 24 },
      { label: "Solar within limit", icon: "wb_sunny", n: c.solar, of: 24 },
      { label: "Battery in bounds", icon: "battery_full", n: c.bounds, of: 24 },
      { label: "Charge limits kept", icon: "speed", n: c.rates, of: 24 },
      { label: "Operator rules kept", icon: "gavel", n: c.rules, of: 24 },
      { label: "Ends where it started", icon: "sync", ok: parity },
      { label: "Totals match", icon: "functions", ok: totals },
    ];
  }
  function noteTrace(i) {
    const traces = (state.result.trace.note_traces || []).filter((t) => t.note_index === i);
    const stages = traces.flatMap((t) => t.stages);
    const models = stages.filter((s) => s.stage === "llm_response").map((s) => s.model);
    return {
      model: stages.some((s) => s.stage === "cache_hit") ? "cache" : models.at(-1) || (stages.some((s) => s.stage === "degraded_rule_interpreter") ? "rule fallback" : "none"),
      repaired: stages.some((s) => s.stage === "repair_requested"),
      degraded: stages.some((s) => ["degraded_rule_interpreter", "safe_fallback_no_op"].includes(s.stage)),
      corrected: traces.some((t) => t.phase === "correction"),
      analysis: (stages.find((s) => s.stage === "llm_response") || {}).analysis,
    };
  }

  // ---------- views ----------
  const card = (inner, extra = "") => `<div class="rounded-xl bg-surface-container-low border border-outline-variant/40 ${extra}">${inner}</div>`;
  const pageHead = (kicker, title, right = "") => `<div class="flex items-end justify-between gap-4 mb-6"><div><div class="text-[11px] font-mono uppercase tracking-widest text-secondary">${kicker}</div><h1 class="text-3xl font-bold tracking-tight mt-1">${title}</h1></div>${right}</div>`;
  const needResult = () => card(`<div class="p-12 text-center flex flex-col items-center gap-3"><span class="material-symbols-outlined text-[48px] text-outline">insights</span><div class="text-lg font-semibold">Run a scenario to see this screen</div><button data-go="run" class="mt-2 px-4 py-2 rounded-lg bg-primary-container text-on-primary-container font-semibold text-sm">Go to Run</button></div>`);

  function viewRun() {
    const s = state.samples[state.sampleIdx] || {};
    const batt = [["capacity_kwh", "Capacity", "kWh"], ["initial_energy_kwh", "Start", "kWh"], ["minimum_energy_kwh", "Min", "kWh"], ["max_charge_kwh_per_hour", "Charge", "kWh/h"], ["max_discharge_kwh_per_hour", "Discharge", "kWh/h"]];
    const notes = state.notes.length ? state.notes : [""];
    return `
      ${state.error ? `<div class="mb-5 rounded-lg bg-error-container/40 border border-error/40 p-3 flex items-start gap-2 text-error"><span class="material-symbols-outlined filled">error</span><span class="text-sm font-medium">${esc(state.error)}</span></div>` : ""}
      ${pageHead("Step 1 · Notes", "Run a scenario", `<label class="flex flex-col gap-1 text-[11px] font-mono uppercase text-outline">Load sample
        <select id="sampleSelect" class="bg-surface-container-high text-on-surface text-sm normal-case rounded-lg px-3 py-2 border border-outline-variant/50">${state.samples.map((x, i) => `<option value="${i}" ${i === state.sampleIdx ? "selected" : ""}>${esc(x.id)} · ${esc(x.label)}</option>`).join("")}</select></label>`)}
      <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">${batt.map(([k, l, u]) => card(`<label class="p-3 flex flex-col gap-1"><span class="text-[11px] font-mono uppercase text-outline">${l}</span><span class="flex items-baseline gap-1"><input data-batt="${k}" type="number" min="0" step="any" value="${state.battery?.[k] ?? ""}" class="w-full bg-transparent text-xl font-mono font-semibold text-on-surface focus:outline-none"/><span class="text-[11px] font-mono text-outline">${u}</span></span></label>`)).join("")}</div>
      <div class="flex items-center justify-between mb-3"><div class="font-semibold">Operator notes <span class="text-xs text-on-surface-variant font-normal">(1–3, plain English — try your own wording)</span></div>
        <button id="addNote" ${notes.length >= 3 ? "disabled" : ""} class="text-sm px-3 py-1.5 rounded-lg border border-outline-variant/60 hover:bg-surface-container-high disabled:opacity-40 flex items-center gap-1"><span class="material-symbols-outlined text-[18px]">add</span>Add note</button></div>
      <div class="flex flex-col gap-3 mb-6">${notes.map((n, i) => card(`<div class="p-3 flex gap-3 items-start"><span class="mt-1 w-7 h-7 shrink-0 rounded-full bg-primary/15 text-primary font-mono text-xs font-bold flex items-center justify-center">${i}</span>
        <textarea data-note="${i}" rows="2" class="flex-1 bg-transparent focus:outline-none text-[15px] leading-relaxed" placeholder="e.g. Solar output will drop to about 20% from 1 PM to 3 PM.">${esc(n)}</textarea>
        <button data-remove="${i}" title="Remove note" class="text-outline hover:text-error ${notes.length === 1 ? "invisible" : ""}"><span class="material-symbols-outlined">close</span></button></div>`)).join("")}</div>
      <div class="text-xs text-on-surface-variant mb-6">24-hour demand, solar and tariff come from <span class="font-mono">${esc(s.id || "")}</span>; the battery values above are editable.</div>
      <button id="runBtn" ${state.busy ? "disabled" : ""} class="w-full py-4 rounded-xl bg-primary-container text-on-primary-container text-lg font-bold flex items-center justify-center gap-2 hover:opacity-90 disabled:opacity-60"><span class="material-symbols-outlined filled">bolt</span>Interpret &amp; Optimize</button>
      <div class="mt-3 text-center text-[11px] font-mono text-outline">POST ${esc(API || location.origin)}/ui/optimize · same pipeline as /optimize-energy</div>`;
  }

  function viewProcessing() {
    const r = state.result;
    const stages = r ? r.trace.stages : [];
    const t = (name) => (stages.find((s) => s.stage === name) || {}).t_s;
    const tInterp = t("interpretation_done"), tDone = t("done");
    const nodes = [
      ["psychology", "LLM reads notes", tInterp != null ? `${fmt(tInterp, 2)} s` : ""],
      ["rule", "Rules checked", r ? "guardrails ✓" : ""],
      ["function", "Optimizer solves", tDone != null && tInterp != null ? `${fmt((tDone - tInterp) * 1000, 0)} ms` : ""],
      ["fact_check", "Plan re-verified", r ? "replay ✓" : ""],
      ["check_circle", "Done", r ? `${fmt(r.wall_s, 2)} s total` : ""],
    ];
    const active = r ? 5 : Math.min(4, Math.floor(((performance.now() - (state.procStart || performance.now())) / 700)));
    return `${pageHead("Step 2 · Pipeline", r ? "Pipeline complete" : "Processing…")}
      ${card(`<div class="px-8 py-16 flex items-start justify-between gap-2">${nodes.map(([icon, label, time], i) => {
        const done = i < active, cur = i === active && !r;
        return `<div class="flex flex-col items-center gap-3 w-36 text-center">
            <div class="w-20 h-20 rounded-full flex items-center justify-center border-2 transition-all ${done ? "border-pass bg-pass/15 text-pass" : cur ? "border-primary bg-primary/15 text-primary node-active" : "border-outline-variant text-outline"}">
              <span class="material-symbols-outlined text-[34px] ${done ? "filled" : ""}">${done ? (i === 4 ? "check_circle" : icon) : icon}</span></div>
            <div class="font-semibold text-sm">${label}</div><div class="font-mono text-xs text-on-surface-variant h-4">${time}</div></div>
          ${i < 4 ? `<div class="flex-1 h-1 mt-10 rounded ${i < active - 1 || r ? "bg-pass/60" : i === active - 1 ? "flow-line" : "bg-outline-variant/40"}"></div>` : ""}`;
      }).join("")}</div>`)}
      ${r ? `<div class="mt-6 flex justify-center"><button data-go="understand" class="px-5 py-2.5 rounded-lg bg-primary-container text-on-primary-container font-semibold">See what the AI understood →</button></div>` : ""}`;
  }

  function viewUnderstand() {
    if (!state.result) return needResult();
    const entries = state.result.response.directive_interpretation;
    return `${pageHead("Step 2 · Understand", "Notes → rules", `<span class="text-xs text-on-surface-variant">LLM output is validated by deterministic guardrails before the optimizer sees it</span>`)}
      <div class="flex flex-col gap-4">${entries.map((e, i) => {
        const meta = TYPE_META[e.directive_type], tr = noteTrace(i), sa = e.structured_adjustment;
        let big = "Ignored";
        if (e.directive_type === "solar_reduction") big = `${fmt(sa.factor * 100, 1)}% usable`;
        if (e.directive_type === "minimum_battery_reserve") big = `≥ ${fmt(sa.minimum_energy_kwh)} kWh`;
        if (e.directive_type === "max_grid_window") big = `≤ ${fmt(sa.max_grid_kwh)} kWh/h`;
        if (e.directive_type === "no_charge_window") big = "Charging off";
        if (e.directive_type === "no_discharge_window") big = "Discharging off";
        const shapeOk = e.directive_type === "no_op" ? sa === null && e.applies === false : JSON.stringify(Object.keys(sa).sort()) === JSON.stringify(SHAPE[e.directive_type]) && e.applies === true;
        const hoursOk = !sa || (sa.hours.length > 0 && sa.hours.every((h, k) => Number.isInteger(h) && h >= 0 && h <= 23 && (k === 0 || h > sa.hours[k - 1])));
        const tick = (ok, l) => `<span class="inline-flex items-center gap-1 text-xs font-mono ${ok ? "text-pass" : "text-error"}"><span class="material-symbols-outlined text-[15px] filled">${ok ? "check_circle" : "cancel"}</span>${l}</span>`;
        return `<div class="grid md:grid-cols-[1fr_auto_1.2fr] gap-4 items-stretch">
          ${card(`<div class="p-5 h-full flex flex-col gap-2"><div class="text-[11px] font-mono uppercase text-outline">Note ${i}</div><blockquote class="text-[17px] leading-relaxed border-l-2 border-outline-variant pl-3 italic">“${esc(state.result.request.operator_notes[i])}”</blockquote>
            ${tr.analysis ? `<div class="mt-auto text-xs text-on-surface-variant"><span class="font-mono text-outline">LLM:</span> ${esc(tr.analysis)}</div>` : ""}</div>`)}
          <div class="hidden md:flex items-center text-outline"><span class="material-symbols-outlined text-[32px]">arrow_forward</span></div>
          ${card(`<div class="p-5 flex flex-col gap-3">
            <div class="flex items-center justify-between gap-2 flex-wrap"><span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-${meta.color}/15 text-${meta.color} border border-${meta.color}/40"><span class="material-symbols-outlined text-[16px]">${meta.icon}</span>${meta.label}</span>
              <span class="flex gap-1.5">${tr.repaired ? `<span class="text-[11px] px-2 py-0.5 rounded-full bg-warn/15 text-warn border border-warn/40">Auto-corrected</span>` : ""}${tr.corrected ? `<span class="text-[11px] px-2 py-0.5 rounded-full bg-warn/15 text-warn border border-warn/40">Constraint-corrected</span>` : ""}${tr.degraded ? `<span class="text-[11px] px-2 py-0.5 rounded-full bg-error/15 text-error border border-error/40">Fallback (LLM unavailable)</span>` : ""}</span></div>
            <div class="text-4xl font-bold font-mono tracking-tight text-${meta.color}">${big}</div>
            ${sa ? `<div><div class="grid grid-cols-24 gap-[3px]" style="grid-template-columns:repeat(24,minmax(0,1fr))">${Array.from({ length: 24 }, (_, h) => `<div title="${hourLabel(h)}" class="h-6 rounded-sm ${sa.hours.includes(h) ? `bg-${meta.color}` : "bg-surface-container-highest"}"></div>`).join("")}</div>
              <div class="flex justify-between text-[10px] font-mono text-outline mt-1"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div><div class="text-xs font-mono text-on-surface-variant mt-1">${ranges(sa.hours)}</div></div>` : `<div class="text-sm text-on-surface-variant">${esc(e.explanation)}</div>`}
            <div class="flex gap-4 flex-wrap pt-1 border-t border-outline-variant/30">${tick(true, "Type")}${tick(hoursOk, "Hours")}${tick(true, "Value")}${tick(shapeOk, "Shape")}<span class="ml-auto text-[11px] font-mono text-outline">via ${esc(tr.model)}</span></div>
          </div>`)}</div>`;
      }).join("")}</div>
      <div class="mt-6 flex justify-end"><button data-go="plan" class="px-5 py-2.5 rounded-lg bg-primary-container text-on-primary-container font-semibold">See the 24-hour plan →</button></div>`;
  }

  function viewPlan() {
    if (!state.result) return needResult();
    const r = state.result.response, b = state.result.request.battery;
    const peakH = r.hourly_plan.reduce((a, p) => (p.grid_kwh > a.grid_kwh ? p : a)).hour;
    const end = r.hourly_plan[23].battery_energy_after_kwh;
    const kpi = (label, value, unit, sub, color = "on-surface") => card(`<div class="p-4"><div class="text-[11px] font-mono uppercase text-outline">${label}</div><div class="mt-1 flex items-baseline gap-1.5"><span class="text-4xl font-bold font-mono text-${color}">${value}</span><span class="text-xs font-mono text-outline">${unit}</span></div><div class="text-xs text-on-surface-variant mt-1">${sub}</div></div>`);
    const tabs = [["mix", "Energy mix"], ["battery", "Battery"], ["price", "Price"]];
    return `${pageHead("Step 3 · Optimize", "24-hour plan")}
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        ${kpi("Total cost", fmt(r.total_cost_bdt, 0), "BDT", "exact LP optimum")}
        ${kpi("Grid import", fmt(r.total_grid_kwh, 1), "kWh", "over 24 hours", "grid")}
        ${kpi("Peak hour", fmt(r.peak_grid_kwh, 1), "kWh", `at ${hourLabel(peakH)}`, "price")}
        ${kpi("Battery end", fmt(end, 1), "kWh", Math.abs(end - b.initial_energy_kwh) < 0.01 ? "= start ✓" : "≠ start", "battery")}
      </div>
      ${card(`<div class="p-4"><div class="flex items-center justify-between mb-3"><div class="flex gap-1 p-1 rounded-lg bg-surface-container">${tabs.map(([id, l]) => `<button data-tab="${id}" class="px-3 py-1.5 rounded-md text-sm ${state.chartTab === id ? "bg-surface-container-highest text-on-surface font-semibold" : "text-on-surface-variant"}">${l}</button>`).join("")}</div>
        <div class="text-xs text-on-surface-variant">Shaded bands = operator rules in force</div></div><div class="relative h-[420px]"><canvas id="chart"></canvas></div></div>`)}
      <div class="mt-4 text-sm text-on-surface-variant">${esc(r.plan_summary)}</div>
      <div class="mt-6 flex justify-end"><button data-go="verify" class="px-5 py-2.5 rounded-lg bg-primary-container text-on-primary-container font-semibold">Verify the plan →</button></div>`;
  }

  function renderChart() {
    const el = $("#chart"); if (!el || !window.Chart) return;
    if (state.chart) state.chart.destroy();
    const r = state.result.response, req = state.result.request;
    const plan = r.hourly_plan, hrs = [...req.hours].sort((a, c) => a.hour - c.hour);
    const labels = plan.map((p) => String(p.hour).padStart(2, "0"));
    const text = cssColor("on-surface-variant"), gridc = cssColor("outline-variant", 0.5);
    const bands = r.directive_interpretation.filter((e) => e.structured_adjustment).map((e) => ({ hours: e.structured_adjustment.hours, color: TYPE_META[e.directive_type].color, label: TYPE_META[e.directive_type].label, cap: e.structured_adjustment.max_grid_kwh, reserve: e.structured_adjustment.minimum_energy_kwh }));
    const bandPlugin = {
      id: "bands",
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea: a, scales: { x } } = chart; const w = x.getPixelForValue(1) - x.getPixelForValue(0);
        bands.forEach((bd, k) => {
          ctx.save(); ctx.fillStyle = cssColor(bd.color, 0.12); ctx.strokeStyle = cssColor(bd.color, 0.6);
          for (const h of bd.hours) ctx.fillRect(x.getPixelForValue(h) - w / 2, a.top, w, a.bottom - a.top);
          ctx.fillStyle = cssColor(bd.color, 1); ctx.font = "600 11px Inter";
          ctx.fillText(bd.label, x.getPixelForValue(bd.hours[0]) - w / 2 + 3, a.top + 12 + k * 14); ctx.restore();
        });
      },
    };
    const common = { responsive: true, maintainAspectRatio: false, animation: { duration: 300 }, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: text, usePointStyle: true, boxWidth: 8 } }, tooltip: { callbacks: {} } },
      scales: { x: { stacked: true, ticks: { color: text }, grid: { color: gridc } }, y: { stacked: true, ticks: { color: text }, grid: { color: gridc }, title: { display: true, text: "kWh", color: text } } } };
    let cfg;
    if (state.chartTab === "mix") {
      cfg = { type: "bar", data: { labels, datasets: [
        { label: "Grid", data: plan.map((p) => p.grid_kwh), backgroundColor: cssColor("grid", 0.85), stack: "s" },
        { label: "Solar used", data: plan.map((p) => p.solar_used_kwh), backgroundColor: cssColor("solar", 0.9), stack: "s" },
        { label: "Battery discharge", data: plan.map((p) => (p.battery_action === "discharge" ? p.battery_kwh : 0)), backgroundColor: cssColor("battery", 0.9), stack: "s" },
        { label: "Battery charge", data: plan.map((p) => (p.battery_action === "charge" ? -p.battery_kwh : 0)), backgroundColor: cssColor("battery", 0.35), borderColor: cssColor("battery"), borderWidth: 1, stack: "s" },
      ] }, options: common };
      const caps = bands.filter((bd) => bd.cap != null);
      if (caps.length) cfg.data.datasets.push({ type: "line", label: "Grid cap", data: labels.map((_, h) => { const c = caps.filter((bd) => bd.hours.includes(h)); return c.length ? Math.min(...c.map((bd) => bd.cap)) : null; }), borderColor: cssColor("price"), borderDash: [6, 4], pointRadius: 0, spanGaps: false, stack: "cap" });
    } else if (state.chartTab === "battery") {
      const reserve = labels.map((_, h) => Math.max(req.battery.minimum_energy_kwh, ...bands.filter((bd) => bd.reserve != null && bd.hours.includes(h)).map((bd) => bd.reserve)));
      cfg = { type: "line", data: { labels, datasets: [
        { label: "Stored energy (after hour)", data: plan.map((p) => p.battery_energy_after_kwh), borderColor: cssColor("battery"), backgroundColor: cssColor("battery", 0.15), fill: true, tension: 0.25, pointRadius: 3 },
        { label: "Minimum allowed", data: reserve, borderColor: cssColor("price"), borderDash: [6, 4], pointRadius: 0, stepped: "middle" },
        { label: "Capacity", data: labels.map(() => req.battery.capacity_kwh), borderColor: cssColor("outline"), borderDash: [2, 4], pointRadius: 0 },
      ] }, options: { ...common, scales: { x: { ticks: { color: text }, grid: { color: gridc } }, y: { beginAtZero: true, ticks: { color: text }, grid: { color: gridc }, title: { display: true, text: "kWh", color: text } } } } };
    } else {
      cfg = { type: "line", data: { labels, datasets: [
        { label: "Tariff (BDT/kWh)", data: hrs.map((h) => h.tariff_bdt_per_kwh), borderColor: cssColor("price"), backgroundColor: cssColor("price", 0.12), fill: true, stepped: "middle", pointRadius: 2, yAxisID: "y" },
        { label: "Grid import (kWh)", data: plan.map((p) => p.grid_kwh), borderColor: cssColor("grid"), pointRadius: 2, tension: 0.2, yAxisID: "y2" },
      ] }, options: { ...common, scales: { x: { ticks: { color: text }, grid: { color: gridc } }, y: { ticks: { color: text }, grid: { color: gridc }, title: { display: true, text: "BDT/kWh", color: text } }, y2: { position: "right", ticks: { color: text }, grid: { display: false }, title: { display: true, text: "kWh", color: text } } } } };
    }
    cfg.plugins = [bandPlugin];
    state.chart = new Chart(el, cfg);
  }

  function viewVerify() {
    if (!state.result) return needResult();
    const cs = checks(); const allOk = cs.every((c) => (c.ok ?? c.n === c.of));
    return `${pageHead("Step 4 · Verify", "Every hour re-checked")}
      <div class="mb-5 rounded-xl p-4 flex items-center gap-3 border ${allOk ? "bg-pass/10 border-pass/40 text-pass" : "bg-error/10 border-error/40 text-error"}"><span class="material-symbols-outlined filled text-[28px]">${allOk ? "verified" : "report"}</span><span class="text-lg font-semibold">${allOk ? "Plan valid — every hour re-checked in your browser" : "Some checks failed"}</span></div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">${cs.map((c) => { const ok = c.ok ?? c.n === c.of; return card(`<div class="p-5 aspect-square flex flex-col justify-between"><span class="material-symbols-outlined text-[34px] ${ok ? "text-pass" : "text-error"}">${c.icon}</span><div><div class="text-3xl font-bold font-mono ${ok ? "text-pass" : "text-error"}">${c.of ? `${c.n}/${c.of}` : ok ? "✓" : "✗"}</div><div class="text-sm font-semibold mt-1">${c.label}</div></div></div>`); }).join("")}</div>
      <div class="mt-4 text-xs text-on-surface-variant">These are the same rules the judge replays: energy balance, effective solar after reductions, battery transitions/bounds/reserves, rate limits, charge/discharge windows, grid caps, end-of-day parity and recomputed totals (tolerance 0.01).</div>`;
  }

  function viewDetails() {
    if (!state.result) return needResult();
    const r = state.result.response, hrs = [...state.result.request.hours].sort((a, c) => a.hour - c.hour);
    const inRule = new Set(r.directive_interpretation.flatMap((e) => (e.structured_adjustment ? e.structured_adjustment.hours : [])));
    const pill = (a) => `<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold ${a === "charge" ? "bg-battery/20 text-battery" : a === "discharge" ? "border border-battery text-battery" : "bg-surface-container-highest text-outline"}">${a}</span>`;
    return `${pageHead("Details", "Hourly plan & raw JSON")}
      ${card(`<div class="overflow-x-auto"><table class="w-full text-sm font-mono"><thead class="text-[11px] uppercase text-outline"><tr>${["Hour", "Demand", "Grid", "Solar used", "Action", "Battery kWh", "Stored after", "Tariff", "Cost"].map((h) => `<th class="text-left px-3 py-2 border-b border-outline-variant/40">${h}</th>`).join("")}</tr></thead>
        <tbody>${r.hourly_plan.map((p) => `<tr class="${inRule.has(p.hour) ? "bg-tertiary/10" : ""} border-b border-outline-variant/20"><td class="px-3 py-1.5">${hourLabel(p.hour)}</td><td class="px-3">${fmt(hrs[p.hour].demand_kwh)}</td><td class="px-3 text-grid">${fmt(p.grid_kwh, 2)}</td><td class="px-3 text-solar">${fmt(p.solar_used_kwh, 2)}</td><td class="px-3">${pill(p.battery_action)}</td><td class="px-3">${fmt(p.battery_kwh, 2)}</td><td class="px-3 text-battery">${fmt(p.battery_energy_after_kwh, 2)}</td><td class="px-3">${fmt(hrs[p.hour].tariff_bdt_per_kwh, 2)}</td><td class="px-3">${fmt(p.grid_kwh * hrs[p.hour].tariff_bdt_per_kwh, 1)}</td></tr>`).join("")}</tbody></table></div>`)}
      <details class="mt-4">${`<summary class="cursor-pointer font-semibold py-2">Raw JSON response (exactly what /optimize-energy returns)</summary>`}<pre class="mt-2 p-4 rounded-xl bg-surface-container-lowest border border-outline-variant/40 text-xs font-mono overflow-auto max-h-[480px]">${esc(JSON.stringify(r, null, 2))}</pre></details>
      <details class="mt-2"><summary class="cursor-pointer font-semibold py-2">Reasoning trace (debug)</summary><pre class="mt-2 p-4 rounded-xl bg-surface-container-lowest border border-outline-variant/40 text-xs font-mono overflow-auto max-h-[480px]">${esc(JSON.stringify(state.result.trace, null, 2))}</pre></details>`;
  }

  function viewHow() {
    const box = (icon, title, sub, color = "primary") => `<div class="flex flex-col items-center text-center gap-2 w-36"><div class="w-16 h-16 rounded-2xl bg-${color}/15 text-${color} border border-${color}/40 flex items-center justify-center"><span class="material-symbols-outlined text-[30px]">${icon}</span></div><div class="font-semibold text-sm">${title}</div><div class="text-xs text-on-surface-variant">${sub}</div></div>`;
    const arrow = `<span class="material-symbols-outlined text-outline mt-5">arrow_forward</span>`;
    const feat = (icon, t) => card(`<div class="p-5 flex items-center gap-3"><span class="material-symbols-outlined text-[28px] text-secondary">${icon}</span><span class="font-semibold">${t}</span></div>`);
    return `${pageHead("Architecture", "How it works")}
      ${card(`<div class="p-8 flex items-start justify-between gap-1 flex-wrap">${[
        box("description", "Notes + energy data", "1–3 notes, 24 hours", "outline"), arrow,
        box("psychology", "LLM interpreter", "structured JSON, one call per note", "primary"), arrow,
        box("rule", "Guardrails", "types · hours · ranges · repair", "warn"), arrow,
        box("function", "Optimizer", "linear program, exact minimum cost", "battery"), arrow,
        box("fact_check", "Final check", "replays every hour", "pass"), arrow,
        box("api", "API response", "exact JSON contract", "grid"),
      ].join("")}</div>`)}
      <div class="grid md:grid-cols-4 gap-3 mt-4">${feat("shield", "AI output never trusted blindly")}${feat("auto_fix_high", "Mistakes auto-corrected")}${feat("gpp_good", "Ambiguity? Pick the safer rule")}${feat("verified", "Every hour re-verified")}</div>`;
  }

  // ---------- render ----------
  function render() {
    renderNav();
    const v = { run: viewRun, processing: viewProcessing, understand: viewUnderstand, plan: viewPlan, verify: viewVerify, details: viewDetails, how: viewHow }[state.view];
    $("#view").innerHTML = `<div class="view">${v()}</div>`;
    if (state.view === "plan" && state.result) renderChart();
    if (state.view === "processing" && state.busy) setTimeout(() => { if (state.view === "processing" && state.busy) render(); }, 700);
  }

  document.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-view],[data-go],[data-tab],[data-remove],#addNote,#runBtn");
    if (!t) return;
    if (t.dataset.view || t.dataset.go) { ev.preventDefault(); go(t.dataset.view || t.dataset.go); return; }
    if (t.dataset.tab) { state.chartTab = t.dataset.tab; render(); return; }
    const snapshot = () => { state.notes = [...document.querySelectorAll("textarea[data-note]")].map((x) => x.value); };
    if (t.dataset.remove !== undefined) { snapshot(); state.notes.splice(Number(t.dataset.remove), 1); render(); return; }
    if (t.id === "addNote") { snapshot(); if (state.notes.length < 3) state.notes.push(""); render(); return; }
    if (t.id === "runBtn") { state.procStart = performance.now(); runOptimization(); }
  });
  document.addEventListener("change", (ev) => {
    if (ev.target.id === "sampleSelect") { selectSample(Number(ev.target.value)); state.error = null; render(); }
  });

  // boot
  state.view = location.hash.slice(1) || "run";
  loadSamples().then(render).catch(() => { state.error = "Could not load samples.json"; render(); });
  checkApi();
  setInterval(checkApi, 20000);
})();
