// Review screen: touch-friendly clip cards. Remove (✕ or swipe left), swap for the next-best moment,
// move up/down, add from runner-ups, trim with +/- (snaps to gaps between words), edit captions,
// change this edit's settings, then render.
const A = window.autocut;
const { api, esc, toast, sheet, $, $$ } = A;
const t2 = (s) => { s = Math.max(0, s); const m = Math.floor(s / 60); return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`; };

let R = { pid: null, eid: null, sel: null };

window.screenReview = async function (pid, eid) {
  R = { pid, eid, sel: null };
  A.setTop("Review & change", { back: `#/p/${pid}` });
  $("#app").innerHTML = `<p class="muted">Loading…</p>`;
  try { R.sel = await api(`/api/projects/${pid}/edits/${eid}/review`); } catch (e) { toast(e.message, "err"); return; }
  $("#app").innerHTML = `
    <section class="card" id="rv-player"></section>
    <section class="card"><div class="row"><div class="grow"><h2>Clips in your edit</h2>
      <p class="sub" id="rv-total"></p></div></div>
      <p class="small muted" style="margin:0 0 6px">Swipe a clip left (or tap ✕) to remove it. Tap ▶ to preview. Trims snap to the gaps between words.</p>
      <div id="rv-clips"></div>
      <button class="btn ghost block" id="rv-add" style="margin-top:10px">+ Add a moment</button>
      <div id="rv-removed"></div>
    </section>
    <section class="card"><h2>Fix captions by telling me</h2>
      <p class="sub">Names, places, or what you really said, e.g. <i>At 1:43 I said "you want some?"</i> — I'll re-listen to that moment.</p>
      <textarea id="rv-ctx" placeholder='At 1:43 I said "you want some?"'></textarea>
      <button class="btn block" id="rv-ctx-go" style="margin-top:10px">Apply</button>
    </section>
    <section class="card"><button class="btn block" id="rv-settings">Settings for this edit</button></section>
    <div class="actionbar"><div class="inner"><button class="btn primary block" id="rv-render">Render my changes</button></div></div>`;
  $("#rv-add").onclick = openRunnerUps;
  $("#rv-ctx-go").onclick = applyContext;
  $("#rv-settings").onclick = openEditSettings;
  $("#rv-render").onclick = renderNow;
  draw();
};

function draw() {
  const s = R.sel, e = s.edit || {};
  const base = `/api/projects/${R.pid}/edits/${R.eid}/video`;
  $("#rv-player").innerHTML = e.status === "done"
    ? `<h2>Current version</h2><p class="sub">${s.changed ? "You've made changes — tap “Render my changes” to see them." : "This is the latest render."}</p>
       <video controls playsinline preload="metadata" src="${base}?quality=preview&v=${e.rendered || e.finished || 0}"></video>`
    : `<h2>Rendering…</h2><p class="sub">The current version will appear here when it's ready.</p>`;
  const target = s.target ? ` · target ${Math.round(s.target)}s` : "";
  $("#rv-total").textContent = `${s.clips.length} clip${s.clips.length === 1 ? "" : "s"} · ${A.fmtDur(s.total_duration)} total${target}`;
  $("#rv-clips").innerHTML = s.clips.map((c, i) => clipCard(c, i, s.clips.length)).join("") || `<p class="muted">No clips — add a moment below.</p>`;
  $("#rv-removed").innerHTML = s.removed?.length ? `<h3 style="margin:18px 0 6px;font-size:15px">Removed</h3>` + s.removed.map((c) => `
      <div class="item removed-row"><img src="${c.thumb}" alt="" style="width:64px;border-radius:8px;opacity:.6">
      <div class="grow small"><b>${t2(c.start)}–${t2(c.end)}</b> <span class="muted">${esc((c.text || "").slice(0, 50))}</span></div>
      <button class="btn small-btn" data-restore="${c.id}">Restore</button></div>`).join("") : "";
  wire();
}

function clipCard(c, i, n) {
  return `<div class="card swipe" data-id="${c.id}" style="box-shadow:none;border:1px solid var(--line);margin:10px 0">
    <div class="clip">
      <button data-play="${c.id}" aria-label="Preview clip ${i + 1}" style="border:0;padding:0;background:none;position:relative">
        <img src="${c.thumb}" alt="" loading="lazy">
        <span style="position:absolute;inset:0;display:grid;place-items:center;color:#fff;font-size:22px;text-shadow:0 1px 4px #000">▶</span></button>
      <div style="min-width:0">
        <div class="time">#${i + 1} · ${t2(c.start)}–${t2(c.end)} <span class="muted small">(${(c.end - c.start).toFixed(1)}s)</span></div>
        <div class="reason">${esc(c.reason || "")}</div>
      </div>
    </div>
    <div class="trim" style="margin-top:8px;justify-content:space-between;flex-wrap:wrap;gap:10px">
      <span>Start <button data-trim="start:-0.5" aria-label="Start earlier">−</button><button data-trim="start:0.5" aria-label="Start later">+</button></span>
      <span>End <button data-trim="end:-0.5" aria-label="End earlier">−</button><button data-trim="end:0.5" aria-label="End later">+</button></span>
    </div>
    <div class="clip-tools">
      <button class="btn small-btn" data-caps="${c.id}">Captions</button>
      <button class="btn small-btn" data-swap="${c.id}">⇄ Swap</button>
      <button class="btn small-btn" data-move="${c.id}:-1" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
      <button class="btn small-btn" data-move="${c.id}:1" ${i === n - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
      <button class="btn small-btn danger" data-remove="${c.id}" aria-label="Remove clip">✕ Remove</button>
    </div></div>`;
}

async function op(body, msg) {
  try {
    const sel = await api(`/api/projects/${R.pid}/edits/${R.eid}/clips`, { method: "POST", json: body });
    sel.edit = R.sel.edit; R.sel = sel; draw();
    if (msg) toast(msg);
  } catch (e) { toast(e.message, "err"); }
}

function wire() {
  $$("[data-remove]").forEach((b) => b.onclick = () => op({ op: "remove", clip_id: b.dataset.remove }, "Removed — you can restore it below."));
  $$("[data-restore]").forEach((b) => b.onclick = () => op({ op: "restore", clip_id: b.dataset.restore }));
  $$("[data-swap]").forEach((b) => b.onclick = () => op({ op: "swap", clip_id: b.dataset.swap }, "Swapped for the next-best moment."));
  $$("[data-move]").forEach((b) => b.onclick = () => { const [id, d] = b.dataset.move.split(":"); op({ op: "move", clip_id: id, dir: +d }); });
  $$("[data-trim]").forEach((b) => b.onclick = () => {
    const id = b.closest("[data-id]").dataset.id, [edge, delta] = b.dataset.trim.split(":");
    op({ op: "trim", clip_id: id, edge, delta: +delta });
  });
  $$("[data-play]").forEach((b) => b.onclick = () => preview(R.sel.clips.find((c) => c.id === b.dataset.play)));
  $$("[data-caps]").forEach((b) => b.onclick = () => editCaptions(R.sel.clips.find((c) => c.id === b.dataset.caps)));
  // swipe left to remove
  $$(".swipe").forEach((card) => {
    let x0 = null, y0 = 0, dx = 0;
    card.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse" || e.target.closest("button")) return; x0 = e.clientX; y0 = e.clientY; dx = 0; });
    card.addEventListener("pointermove", (e) => {
      if (x0 === null) return;
      dx = e.clientX - x0;
      if (Math.abs(e.clientY - y0) > 30 && Math.abs(dx) < 30) { x0 = null; card.style.transform = ""; return; }
      if (dx < 0) { card.style.transform = `translateX(${dx}px)`; card.style.opacity = String(Math.max(.3, 1 + dx / 300)); }
    });
    const end = () => {
      if (x0 === null) return;
      x0 = null;
      if (dx < -110) { card.style.transform = "translateX(-110%)"; setTimeout(() => op({ op: "remove", clip_id: card.dataset.id }, "Removed — you can restore it below."), 150); }
      else { card.style.transform = ""; card.style.opacity = ""; }
    };
    card.addEventListener("pointerup", end); card.addEventListener("pointercancel", end);
  });
}

function preview(c) {
  sheet(`<h2>${t2(c.start)}–${t2(c.end)}</h2><p class="sub">${esc(c.text || "No talking in this bit.")}</p>
    <video id="pv" playsinline controls autoplay muted style="width:100%;border-radius:12px;background:#000" src="${c.proxy}#t=${c.start.toFixed(2)},${c.end.toFixed(2)}"></video>
    <p class="small muted">Preview from the light copy — the final render uses your original file.</p>`, (s) => {
    const v = $("#pv", s);
    v.addEventListener("loadedmetadata", () => { if (Math.abs(v.currentTime - c.start) > 0.3) v.currentTime = c.start; v.muted = false; v.play().catch(() => { }); });
    v.addEventListener("timeupdate", () => { if (v.currentTime >= c.end) { v.pause(); v.currentTime = c.start; } });
  });
}

async function editCaptions(c) {
  let lines = [];
  try { lines = await api(`/api/projects/${R.pid}/captions?hash=${c.hash}&start=${c.start}&end=${c.end}`); } catch (e) { toast(e.message, "err"); return; }
  sheet(`<h2>Captions in this clip</h2><p class="sub">Retype anything that's wrong, or tap “Re-listen” and I'll listen to that moment again (optionally with a hint).</p>
    <div id="cap-lines">${lines.map((l, i) => `<div class="caption-line" data-i="${i}">
      <span class="small muted" style="width:44px;flex:none">${t2(l.start)}</span>
      <input type="text" value="${esc(l.text)}" aria-label="Caption at ${t2(l.start)}">
      <button class="btn small-btn" data-save="${i}">Save</button>
      <button class="btn small-btn ghost" data-re="${i}">Re-listen</button></div>`).join("") || '<p class="muted">Nobody talks in this clip.</p>'}</div>`, (s) => {
    $$("[data-save]", s).forEach((b) => b.onclick = async () => {
      const l = lines[+b.dataset.save], text = b.parentElement.querySelector("input").value;
      try { await api(`/api/projects/${R.pid}/captions`, { method: "PUT", json: { hash: c.hash, start: l.start, end: l.end, text } }); toast("Caption saved — it'll be in the next render."); b.textContent = "Saved ✓"; markChanged(); }
      catch (e) { toast(e.message, "err"); }
    });
    $$("[data-re]", s).forEach((b) => b.onclick = async () => {
      const l = lines[+b.dataset.re], inp = b.parentElement.querySelector("input");
      const hint = inp.value !== l.text ? inp.value : "";
      b.textContent = "Listening…"; b.disabled = true;
      try { const r = await api(`/api/projects/${R.pid}/captions/relisten`, { method: "POST", json: { hash: c.hash, start: l.start, end: l.end, hint } }); inp.value = r.edit.text; l.text = r.edit.text; toast("Re-listened."); markChanged(); }
      catch (e) { toast(e.message, "err"); }
      b.textContent = "Re-listen"; b.disabled = false;
    });
  });
}

function markChanged() { R.sel.changed = true; draw(); }

function openRunnerUps() {
  const ru = R.sel.runner_ups || [];
  sheet(`<h2>Add a moment</h2><p class="sub">The next-best moments I found.</p>
    ${ru.map((c) => `<div class="item"><img src="${c.thumb}" alt="" style="width:96px;border-radius:8px" data-pv="${c.id}">
      <div class="grow"><div class="small"><b>${t2(c.start)}–${t2(c.end)}</b> · ${(c.end - c.start).toFixed(1)}s</div>
      <div class="small muted">${esc((c.reason || "").slice(0, 110))}</div></div>
      <button class="btn small-btn primary" data-add="${c.id}">Add</button></div>`).join("") || '<p class="muted">No more candidates.</p>'}`, (s, close) => {
    $$("[data-add]", s).forEach((b) => b.onclick = async () => { close(); await op({ op: "add", runner_id: b.dataset.add }, "Added at the end — move it with ↑."); });
    $$("[data-pv]", s).forEach((im) => im.onclick = () => { close(); preview(ru.find((c) => c.id === im.dataset.pv)); });
  });
}

async function applyContext() {
  const text = $("#rv-ctx").value.trim();
  if (!text) return;
  const b = $("#rv-ctx-go"); b.disabled = true; b.textContent = "Re-listening on your computer…";
  try {
    const r = await api(`/api/projects/${R.pid}/context`, { method: "POST", json: { text } });
    toast(r.changes.length ? `Updated ${r.changes.length} caption${r.changes.length === 1 ? "" : "s"}: “${r.changes[0].text}”` : "Nothing needed changing.");
    if (r.changes.length) markChanged();
  } catch (e) { toast(e.message, "err"); }
  b.disabled = false; b.textContent = "Apply";
}

function openEditSettings() {
  const opts = R.sel.options || {};
  sheet(`<h2>Settings for this edit</h2><div id="es"></div>`, (s) => {
    const box = $("#es", s);
    box.innerHTML = A.SETTINGS.map((st) => {
      const v = opts[st.key];
      if (st.type === "seg") return `<label class="field">${st.label}</label><div class="seg" data-key="${st.key}">${st.options.map(([val, lab]) => `<button type="button" data-val='${JSON.stringify(val)}' aria-pressed="${JSON.stringify(val) === JSON.stringify(v ?? null)}">${lab}</button>`).join("")}</div>`;
      if (st.type === "toggle") return `<div class="toggle"><div style="font-weight:600">${st.label}</div><label class="switch"><input type="checkbox" data-key="${st.key}" ${v ? "checked" : ""} aria-label="${st.label}"><span></span></label></div>`;
      const shown = st.fromValue ? st.fromValue(v) : v;
      return `<label class="field">${st.label}: <span class="muted" id="es-${st.key}">${st.fmt(shown)}</span></label><input type="range" data-key="${st.key}" min="${st.min}" max="${st.max}" step="${st.step}" value="${shown}">`;
    }).join("");
    const save = async (key, value) => {
      try { const r = await api(`/api/projects/${R.pid}/edits/${R.eid}/options`, { method: "PATCH", json: { options: { [key]: value } } }); R.sel.options = r.options; markChanged(); }
      catch (e) { toast(e.message, "err"); }
    };
    $$(".seg", box).forEach((g) => g.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; $$("button", g).forEach((x) => x.setAttribute("aria-pressed", x === b)); save(g.dataset.key, JSON.parse(b.dataset.val)); });
    $$("input[type=checkbox]", box).forEach((c) => c.onchange = () => save(c.dataset.key, c.checked));
    $$("input[type=range]", box).forEach((r) => { const st = A.SETTINGS.find((x) => x.key === r.dataset.key); r.oninput = () => $(`#es-${st.key}`, box).textContent = st.fmt(r.value); r.onchange = () => save(st.key, st.toValue ? st.toValue(r.value) : +r.value); });
  });
}

async function renderNow() {
  if (!R.sel.clips.length) { toast("Add at least one clip first.", "err"); return; }
  try {
    await api(`/api/projects/${R.pid}/edits/${R.eid}/render`, { method: "POST" });
    toast("Rendering your changes — you can lock your phone.");
    A.go(`#/p/${R.pid}`);
  } catch (e) { toast(e.message, "err"); }
}
