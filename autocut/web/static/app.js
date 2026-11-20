// autocut phone app — vanilla JS, no build step. Hash routes:
//   #/login   #/   #/p/<pid>   #/p/<pid>/review/<eid>
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`;
const fmtDur = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const app = $("#app");

// ------------------------------------------------------------------ API
class ApiError extends Error { constructor(msg, status, body) { super(msg); this.status = status; this.body = body; } }
async function api(path, { method = "GET", json, body, headers = {} } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (json !== undefined) { opts.body = JSON.stringify(json); opts.headers["Content-Type"] = "application/json"; }
  else if (body !== undefined) opts.body = body;
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (res.status === 401 && !path.endsWith("/login")) { go("#/login"); throw new ApiError("Please log in.", 401, data); }
  if (!res.ok) throw new ApiError(data?.detail && typeof data.detail === "string" ? data.detail : `Something went wrong (${res.status})`, res.status, data);
  return data;
}

// ------------------------------------------------------------------ UI helpers
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`; t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 6000 : 3500);
}
function sheet(html, onMount) {
  const s = $("#sheet"), b = $("#sheet-backdrop");
  s.innerHTML = `<div class="sheet-head"><div class="grab"></div><button class="btn small-btn ghost sheet-done" type="button">Done</button></div>${html}`;
  s.hidden = false; b.hidden = false;
  const close = () => { s.querySelectorAll("video").forEach((v) => v.pause()); s.hidden = true; b.hidden = true; s.innerHTML = ""; };
  b.onclick = close;
  s.querySelector(".sheet-done").onclick = close;
  onMount?.(s, close);
  return close;
}
function setTop(title, { back = null, menu = true } = {}) {
  $("#title").textContent = title;
  const bk = $("#back");
  bk.hidden = !back; bk.onclick = () => go(back);
  $("#menu-btn").hidden = !menu;
}
const go = (hash) => { if (location.hash !== hash) location.hash = hash; else route(); };
const icon = {
  film: `<svg viewBox="0 0 24 24" width="22" height="22"><rect x="3" y="5" width="18" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M10 9.5v5l4-2.5z" fill="currentColor"/></svg>`,
  mic: `<svg viewBox="0 0 24 24" width="28" height="28"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
  x: `<svg viewBox="0 0 24 24" width="18" height="18"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`,
};

// ------------------------------------------------------------------ uploads (resumable, survive screen changes)
const CHUNK = 4 * 1024 * 1024;
const uploader = {
  tasks: [], running: 0, max: 1, wake: null,
  add(pid, kind, files) {
    for (const f of files) {
      const fp = `${f.name}|${f.size}|${f.lastModified}`;
      if (this.tasks.some((t) => t.fp === fp && t.kind === kind && t.pid === pid && t.status !== "error")) continue;
      this.tasks.push({ id: Math.random().toString(36).slice(2), pid, kind, file: f, fp, offset: 0, status: "waiting", error: null });
    }
    this.pump(); renderUploads();
  },
  active() { return this.tasks.filter((t) => ["waiting", "uploading", "retrying"].includes(t.status)); },
  pump() {
    while (this.running < this.max) {
      const t = this.tasks.find((x) => x.status === "waiting");
      if (!t) break;
      this.running++;
      this.run(t).finally(() => { this.running--; this.pump(); renderUploads(); });
    }
    this.keepAwake(); updatePill();
  },
  async keepAwake() {
    try {
      if (this.active().length && !this.wake && "wakeLock" in navigator) this.wake = await navigator.wakeLock.request("screen");
      else if (!this.active().length && this.wake) { await this.wake.release(); this.wake = null; }
    } catch { /* not supported */ }
  },
  async run(t) {
    t.status = "uploading";
    let fails = 0;
    try {
      const r = await api(`/api/projects/${t.pid}/uploads`, { method: "POST", json: { filename: t.file.name, size: t.file.size, kind: t.kind, fingerprint: t.fp } });
      t.uid = r.upload_id; t.offset = r.offset;
      while (t.offset < t.file.size) {
        const end = Math.min(t.offset + CHUNK, t.file.size);
        let res;
        try {
          res = await fetch(`/api/uploads/${t.uid}?offset=${t.offset}`, { method: "PUT", body: t.file.slice(t.offset, end), headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin" });
        } catch {
          fails++; t.status = "retrying"; renderUploads(); updatePill();
          await sleep(Math.min(30000, 1000 * 2 ** Math.min(fails, 5)));
          t.status = "uploading"; continue;  // network dropped: keep trying, resume where we left off
        }
        if (res.status === 409) { t.offset = (await res.json()).offset; continue; }
        if (res.status === 401) { go("#/login"); throw new Error("Logged out"); }
        if (!res.ok) { let d = ""; try { d = (await res.json()).detail; } catch { } throw new Error(d || `Upload failed (${res.status})`); }
        fails = 0;
        const j = await res.json();
        t.offset = j.offset;
        renderUploads(); updatePill();
        if (j.done) break;
      }
      t.status = "done";
      if (current.pid === t.pid) refreshProject();
    } catch (e) { t.status = "error"; t.error = e.message; toast(`${t.file.name}: ${e.message}`, "err"); }
  },
};
window.addEventListener("online", () => uploader.tasks.filter((t) => t.status === "retrying").forEach(() => uploader.pump()));
window.addEventListener("beforeunload", (e) => { if (uploader.active().length) { e.preventDefault(); e.returnValue = ""; } });
function updatePill() {
  const a = uploader.active(), p = $("#upload-pill");
  if (!a.length) { p.hidden = true; return; }
  const total = a.reduce((s, t) => s + t.file.size, 0), done = a.reduce((s, t) => s + t.offset, 0);
  p.hidden = false;
  p.textContent = `↑ ${a.length} · ${Math.floor((100 * done) / Math.max(1, total))}%${a.some((t) => t.status === "retrying") ? " · reconnecting" : ""}`;
}

// ------------------------------------------------------------------ state + polling
const current = { route: null, pid: null, project: null, sig: "", edStatus: {}, timer: null };
async function refreshProject() {
  if (!current.pid) return;
  try {
    const p = await api(`/api/projects/${current.pid}`);
    current.project = p;
    renderProjectDynamic(p);
  } catch (e) { if (e.status === 404) go("#/"); }
}
function schedulePoll() {
  clearTimeout(current.timer);
  if (!current.pid) return;
  const p = current.project;
  const busy = uploader.active().length || p?.jobs?.some((j) => j.status !== "done" && j.status !== "error")
    || p?.footage?.some((f) => ["queued", "processing"].includes(f.status)) || p?.edits?.some((e) => ["queued", "running"].includes(e.status));
  current.timer = setTimeout(async () => { if (!document.hidden) await refreshProject(); schedulePoll(); }, busy ? 2000 : 8000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && current.pid) { refreshProject(); schedulePoll(); } });

// ------------------------------------------------------------------ router
async function route() {
  clearTimeout(current.timer);
  current.pid = null; current.project = null; current.sig = ""; current.edStatus = {};
  const h = location.hash || "#/";
  let m;
  if (h === "#/login") return screenLogin();
  try { await api("/api/me"); } catch { return; }
  if ((m = h.match(/^#\/p\/([a-z0-9]+)\/review\/([a-z0-9]+)$/))) return window.screenReview ? window.screenReview(m[1], m[2]) : go(`#/p/${m[1]}`);
  if ((m = h.match(/^#\/p\/([a-z0-9]+)$/))) return screenProject(m[1]);
  return screenProjects();
}
window.addEventListener("hashchange", route);
$("#menu-btn").onclick = openMenu;

// ------------------------------------------------------------------ login
function screenLogin() {
  setTop("autocut", { menu: false });
  app.innerHTML = `
    <form class="login card" id="login-form">
      <img class="logo" src="/static/icon-192.png" alt="">
      <h2 class="center">Welcome to autocut</h2>
      <p class="sub center">Your computer does the editing. Log in to use it from here.</p>
      <label class="field" for="pw">Password</label>
      <input id="pw" type="password" autocomplete="current-password" required autofocus>
      <button class="btn primary block" style="margin-top:14px">Log in</button>
      <p class="small muted center" style="margin-top:14px">First time? The password is in <b>FIRST_LOGIN.txt</b> in autocut's data folder on your computer.</p>
    </form>`;
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/login", { method: "POST", json: { password: $("#pw").value } }); go("#/"); }
    catch (err) { toast(err.message, "err"); }
  };
}

// ------------------------------------------------------------------ projects list
async function screenProjects() {
  setTop("Your projects");
  app.innerHTML = `<div id="plist"><p class="muted">Loading…</p></div>
    <div class="actionbar"><div class="inner"><button class="btn primary block" id="new-proj">+ New project</button></div></div>`;
  $("#new-proj").onclick = () => sheet(`
      <h2>New project</h2>
      <form id="np"><label class="field" for="pn">Name</label>
      <input id="pn" type="text" placeholder="e.g. KL airport lunch" maxlength="80" autofocus>
      <button class="btn primary block" style="margin-top:14px">Create</button></form>`,
    (s, close) => {
      $("#np", s).onsubmit = async (e) => {
        e.preventDefault();
        const p = await api("/api/projects", { method: "POST", json: { name: $("#pn", s).value || "Untitled project" } });
        close(); go(`#/p/${p.id}`);
      };
    });
  const list = await api("/api/projects");
  $("#plist").innerHTML = list.length ? list.map((p) => `
      <a class="card proj" href="#/p/${p.id}">
        <div class="badge">${esc((p.name || "?")[0].toUpperCase())}</div>
        <div class="grow"><div class="name" style="font-weight:700">${esc(p.name)}</div>
        <div class="small muted">${p.footage} video${p.footage === 1 ? "" : "s"} · ${p.edits} edit${p.edits === 1 ? "" : "s"} · ${new Date(p.created * 1000).toLocaleDateString()}</div></div>
      </a>`).join("") : `<div class="card center"><h2>No projects yet</h2><p class="sub">Create one, add videos from your camera roll, and tell autocut what you want.</p></div>`;
}

// ------------------------------------------------------------------ project screen
const SETTINGS = [
  { key: "aspect", label: "Shape", type: "seg", options: [[null, "Original"], ["16:9", "16:9"], ["9:16", "Vertical"], ["1:1", "Square"], ["4:5", "4:5"]] },
  { key: "captions", label: "Captions", type: "seg", options: [[null, "Like reference"], [true, "On"], [false, "Off"]] },
  { key: "caption_style", label: "Caption style", type: "seg", options: [["static", "Whole line"], ["karaoke", "Word by word"]] },
  { key: "caption_case", label: "Caption letters", type: "seg", options: [["original", "As spoken"], ["lower", "lowercase"], ["upper", "UPPERCASE"]] },
  { key: "caption_scale", label: "Caption size", type: "range", min: 0.6, max: 1.8, step: 0.1, fmt: (v) => `${Math.round(v * 100)}%` },
  { key: "caption_position", label: "Caption position", type: "seg", options: [["auto", "Auto"], ["bottom", "Bottom"], ["middle", "Middle"], ["top", "Top"]] },
  { key: "punch_ins", label: "Zoom cuts", hint: "Small zoom jumps inside long clips", type: "toggle" },
  { key: "punch_in_spacing", label: "Zoom cut spacing", type: "range", min: 0, max: 12, step: 1, fmt: (v) => (+v ? `every ${v}s or more` : "Auto"), toValue: (v) => (+v ? +v : null), fromValue: (v) => v || 0 },
  { key: "color_match", label: "Match colours to the reference", hint: "Off keeps your original colours", type: "toggle" },
  { key: "face_follow", label: "Keep faces framed when cropping", type: "toggle" },
  { key: "loudness_lufs", label: "Volume level", type: "seg", options: [[-14, "Social media"], [-16, "Podcast"], [-23, "TV"]] },
];

async function screenProject(pid) {
  current.pid = pid;
  setTop("Project", { back: "#/" });
  app.innerHTML = `<p class="muted">Loading…</p>`;
  let p;
  try { p = await api(`/api/projects/${pid}`); } catch { return; }
  current.project = p;
  setTop(p.name, { back: "#/" });
  app.innerHTML = `
    <div class="two-col">
      <section class="card">
        <h2>1 · Your footage</h2>
        <p class="sub">Add videos straight from your camera roll. Big files are fine — uploads resume if the connection drops.</p>
        <ul class="list" id="footage-list"></ul>
        <label class="btn ghost block" style="margin-top:10px">+ Add videos
          <input type="file" accept="video/*" multiple hidden data-kind="footage"></label>
      </section>
      <section class="card">
        <h2>2 · Style</h2>
        <p class="sub">Add videos whose style you like, or pick a saved style.</p>
        <select id="style-pick" aria-label="Style"></select>
        <ul class="explain" id="style-explain"></ul>
        <ul class="list" id="refs-list" style="margin-top:8px"></ul>
        <label class="btn ghost block" style="margin-top:10px">+ Add reference videos
          <input type="file" accept="video/*" multiple hidden data-kind="refs"></label>
      </section>
    </div>
    <section class="card">
      <h2>3 · What should it be?</h2>
      <p class="sub">Type it, or hold the mic and say it.</p>
      <textarea id="instr" placeholder="e.g. funny 60 second vlog with captions">${esc(p.instruction || "")}</textarea>
      <div class="mic-row">
        <button class="mic" id="mic" aria-label="Hold to talk">${icon.mic}</button>
        <div class="small muted" id="mic-hint">Hold to talk · release to send</div>
      </div>
      <div class="understood" id="understood" hidden></div>
    </section>
    <details class="card" id="settings-card">
      <summary style="cursor:pointer;font-weight:700;font-size:17px;min-height:32px">Settings <span class="small muted" style="font-weight:400">— change anything</span></summary>
      <div id="settings"></div>
    </details>
    <section class="card"><h2>Your edits</h2><div id="edits"></div></section>
    <div class="actionbar"><div class="inner">
      <button class="btn primary block" id="make">Make my edit</button>
    </div></div>`;

  $$('input[type=file]').forEach((inp) => inp.onchange = () => { uploader.add(pid, inp.dataset.kind, inp.files); inp.value = ""; });
  setupInstruction(p);
  setupMic();
  renderSettings(p.options || {});
  setupStylePicker(p);
  $("#make").onclick = makeEdit;
  renderProjectDynamic(p);
  schedulePoll();
}

function itemRow(it, job, upload) {
  let chip = "", bar = "", sub = "";
  if (upload) {
    const pct = Math.floor((100 * upload.offset) / Math.max(1, upload.file.size));
    chip = upload.status === "error" ? `<span class="chip err">upload failed</span>` : `<span class="chip busy">${upload.status === "retrying" ? "reconnecting…" : `uploading ${pct}%`}</span>`;
    bar = `<div class="bar"><i style="width:${pct}%"></i></div>`;
    sub = fmtSize(upload.file.size) + (upload.error ? ` · ${esc(upload.error)}` : "");
  } else {
    const st = it.status;
    if (st === "ready") chip = `<span class="chip ok">ready</span>`;
    else if (st === "error") chip = `<span class="chip err">problem</span>`;
    else chip = `<span class="chip busy">${job ? "preparing" : "waiting"}</span>`;
    if (job && job.status === "running") { bar = `<div class="bar"><i style="width:${Math.round(job.progress * 100)}%"></i></div>`; sub = esc(job.stage); }
    else sub = [it.duration ? fmtDur(it.duration) : "", fmtSize(it.size), it.error ? esc(it.error) : ""].filter(Boolean).join(" · ");
  }
  const actions = upload ? "" : `${it.status === "error" ? `<button class="btn small-btn" data-retry="${it.id}">Retry</button>` : ""}
      <button class="icon-btn" data-del="${it.id}" aria-label="Remove ${esc(it.name)}">${icon.x}</button>`;
  return `<li class="item"><div class="thumb-icon">${icon.film}</div>
    <div class="grow"><div class="name">${esc(it.name)}</div><div class="small muted">${chip} ${sub}</div>${bar}</div>${actions}</li>`;
}

function renderUploads() { if (current.project) renderProjectDynamic(current.project); }

function renderProjectDynamic(p) {
  if (!$("#footage-list")) return;
  const jobs = p.jobs || [];
  for (const kind of ["footage", "refs"]) {
    const el = $(kind === "footage" ? "#footage-list" : "#refs-list");
    const ups = uploader.tasks.filter((t) => t.pid === p.id && t.kind === kind && t.status !== "done");
    const rows = ups.map((u) => itemRow({ name: u.file.name }, null, u))
      .concat(p[kind].map((it) => itemRow(it, jobs.find((j) => j.target === it.id && j.status !== "done"), null)));
    el.innerHTML = rows.join("") || (kind === "footage" ? `<li class="muted small">No videos yet.</li>` : "");
  }
  $$("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this video from the project?")) return;
    await api(`/api/projects/${p.id}/items/${b.dataset.del}`, { method: "DELETE" }); refreshProject();
  });
  $$("[data-retry]").forEach((b) => b.onclick = async () => { await api(`/api/projects/${p.id}/items/${b.dataset.retry}/retry`, { method: "POST" }); refreshProject(); });
  // style explanation
  const ex = $("#style-explain");
  const styleJob = jobs.find((j) => j.kind === "style" && j.status !== "done" && j.status !== "error");
  if (styleJob) ex.innerHTML = `<li>Studying your reference videos… ${Math.round(styleJob.progress * 100)}%</li>`;
  else if (p.style && p.style_explained && p.style === `project_${p.id}`) ex.innerHTML = p.style_explained.map((l) => `<li>${esc(l)}</li>`).join("");
  else if (!p.style) ex.innerHTML = "";
  if (p.style && $("#style-pick") && ![...$("#style-pick").options].some((o) => o.value === p.style)) setupStylePicker(p);
  // make button
  const ready = p.footage.some((f) => f.status === "ready");
  const mk = $("#make");
  mk.disabled = !ready;
  mk.textContent = ready ? "Make my edit" : p.footage.length || uploader.active().length ? "Preparing your footage…" : "Add footage first";
  renderEdits(p);
  updatePill();
}

function renderEdits(p) {
  const box = $("#edits");
  const jobs = p.jobs || [];
  const edits = [...p.edits].reverse();
  // notify when something finishes
  for (const e of edits) {
    const before = current.edStatus[e.id];
    if (before && before !== "done" && e.status === "done") notifyDone(e);
    current.edStatus[e.id] = e.status;
  }
  const sig = JSON.stringify(edits.map((e) => [e.id, e.status, e.rendered || e.finished || 0]));
  if (sig !== current.sig) {
    current.sig = sig;
    box.innerHTML = edits.length ? edits.map((e) => editCard(p, e)).join("") : `<p class="muted small">Nothing yet. Add footage, say what you want, then tap “Make my edit”.</p>`;
    $$("[data-export]", box).forEach((b) => b.onclick = () => openExport(p.id, b.dataset.export));
    $$("[data-del-edit]", box).forEach((b) => b.onclick = async () => {
      if (!confirm("Delete this edit?")) return;
      await api(`/api/projects/${p.id}/edits/${b.dataset.delEdit}`, { method: "DELETE" }); refreshProject();
    });
  }
  // live progress (in place, so video players aren't reset)
  for (const e of edits) {
    const j = jobs.find((x) => x.target === e.id && (x.status === "running" || x.status === "queued"));
    const el = $(`#prog-${e.id}`);
    if (el && j) { el.querySelector("i").style.width = `${Math.round(j.progress * 100)}%`; $(`#stage-${e.id}`).textContent = j.status === "queued" ? "Waiting for the computer…" : j.stage; }
  }
}

function editCard(p, e) {
  const base = `/api/projects/${p.id}/edits/${e.id}/video`;
  const head = `<div class="row"><div class="grow"><div style="font-weight:700">${esc(e.instruction)}</div>
      <div class="small muted">${e.summary ? esc(e.summary) : ""}${e.duration ? ` · ${fmtDur(e.duration)}` : ""}</div></div>
      <button class="icon-btn" data-del-edit="${e.id}" aria-label="Delete edit">${icon.x}</button></div>`;
  if (e.status === "done") {
    const v = e.rendered || e.finished || 0;
    return `<div class="edit card" style="box-shadow:none;border:1px solid var(--line)">${head}
      <video controls playsinline preload="metadata" src="${base}?quality=preview&v=${v}"></video>
      ${(e.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}
      <div class="actions">
        <a class="btn primary small-btn" href="${base}?quality=full&download=1" download>Download full quality</a>
        <a class="btn small-btn" href="#/p/${p.id}/review/${e.id}">Review &amp; change</a>
        <button class="btn small-btn" data-export="${e.id}">Export for editing apps</button>
      </div>
      <p class="small muted" style="margin:8px 0 0">iPhone: after downloading, open it from Files → Share → Save Video to put it in Photos.</p></div>`;
  }
  if (e.status === "error") return `<div class="edit card" style="box-shadow:none;border:1px solid var(--line)">${head}
      <div class="warn" style="color:var(--err)">Couldn't finish: ${esc(e.error || "unknown problem")}</div></div>`;
  return `<div class="edit card" style="box-shadow:none;border:1px solid var(--line)">${head}
      <div class="small muted" id="stage-${e.id}">Waiting for the computer…</div>
      <div class="bar" id="prog-${e.id}"><i></i></div>
      <p class="small muted" style="margin:8px 0 0">You can lock your phone — the computer keeps working and this updates when you come back.</p></div>`;
}

function openExport(pid, eid) {
  const base = `/api/projects/${pid}/edits/${eid}/export?format=`;
  sheet(`<h2>Export for editing apps</h2>
    <p class="sub">Open this edit in a pro editor and keep working. The files point at your original videos on the computer.</p>
    <div class="stack">
      <a class="btn primary block" href="${base}zip" download>Everything (zip)</a>
      <a class="btn block" href="${base}fcpxml" download>Final Cut Pro / DaVinci Resolve (.fcpxml)</a>
      <a class="btn block" href="${base}premiere" download>Premiere Pro (.xml)</a>
      <a class="btn block" href="${base}edl" download>Any editor (.edl)</a>
      <a class="btn block" href="${base}srt" download>Captions (.srt)</a>
    </div>`);
}

function notifyDone(e) {
  toast("🎬 Your edit is ready!");
  try { navigator.vibrate?.([80, 60, 80]); } catch { }
  if (document.hidden) {
    document.title = "✓ Edit ready — autocut";
    try { if (window.Notification && Notification.permission === "granted") new Notification("autocut", { body: "Your edit is ready", icon: "/static/icon-192.png" }); } catch { }
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) document.title = "autocut"; });

// ------------------------------------------------------------------ instruction, mic, settings, style
function setupInstruction(p) {
  const ta = $("#instr");
  let t = null;
  const update = async () => {
    const text = ta.value.trim();
    api(`/api/projects/${p.id}`, { method: "PATCH", json: { instruction: ta.value } }).catch(() => { });
    if (!text) { $("#understood").hidden = true; return; }
    try { showUnderstood(await api("/api/parse", { method: "POST", json: { text } })); } catch { }
  };
  ta.oninput = () => { clearTimeout(t); t = setTimeout(update, 450); };
  if (ta.value.trim()) update();
}
function showUnderstood(r, heard) {
  const s = r.spec, chips = [];
  if (s.mood !== "auto") chips.push(s.mood);
  if (s.target_duration) chips.push(s.target_duration < 90 ? `${Math.round(s.target_duration)} s` : `${(s.target_duration / 60).toFixed(1)} min`);
  if (s.platform) chips.push(s.platform);
  if (s.aspect_ratio) chips.push(s.aspect_ratio);
  if (s.captions === true) chips.push("captions on"); else if (s.captions === false) chips.push("no captions");
  s.include_keywords.forEach((k) => chips.push(`about “${k}”`));
  s.exclude_keywords.forEach((k) => chips.push(`without “${k}”`));
  if (s.visual_query) chips.push(`showing “${s.visual_query}”`);
  if (s.style_preset) chips.push(`style: ${s.style_preset}`);
  if (s.music === false) chips.push("no music");
  if (s.versions > 1) chips.push(`${s.versions} versions`);
  const u = $("#understood");
  u.hidden = false;
  u.innerHTML = `${heard ? `<div class="small muted">I heard: “${esc(heard)}”</div>` : ""}
    <div class="small" style="font-weight:700">What I understood</div>
    <div class="chips">${chips.map((c) => `<span>${esc(c)}</span>`).join("") || '<span>best moments</span>'}</div>
    ${s.warnings.map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}`;
}

function setupMic() {
  const btn = $("#mic"), hint = $("#mic-hint");
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    hint.textContent = "The mic needs a secure (https) connection — type instead for now.";
    btn.disabled = true; btn.style.opacity = .4; return;
  }
  let rec = null, chunks = [], started = 0, stream = null, toggled = false, timer = null;
  const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
  const start = async () => {
    if (rec) return;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { toast("Microphone permission was denied.", "err"); return; }
    chunks = []; rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = send;
    rec.start(); started = Date.now(); btn.classList.add("rec");
    timer = setInterval(() => hint.textContent = `Listening… ${Math.floor((Date.now() - started) / 1000)}s — release to send`, 250);
  };
  const stop = () => {
    if (!rec) return;
    clearInterval(timer);
    btn.classList.remove("rec");
    try { rec.stop(); } catch { }
    stream?.getTracks().forEach((t) => t.stop());
  };
  const send = async () => {
    const r = rec; rec = null;
    if (Date.now() - started < 500) { hint.textContent = "Hold the button a little longer while you speak."; return; }
    hint.textContent = "Understanding… (on your computer)";
    const type = r.mimeType || mime || "audio/webm";
    const fd = new FormData();
    fd.append("audio", new Blob(chunks, { type }), type.includes("mp4") ? "voice.m4a" : "voice.webm");
    try {
      const res = await api("/api/voice", { method: "POST", body: fd });
      const text = res.transcript.text;
      if (text) { $("#instr").value = text; $("#instr").dispatchEvent(new Event("input")); }
      showUnderstood(res, text);
      hint.textContent = "Hold to talk · release to send";
    } catch (e) { hint.textContent = "Hold to talk · release to send"; toast(e.message, "err"); }
  };
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); btn.setPointerCapture?.(e.pointerId); if (rec && toggled) { toggled = false; stop(); } else start(); });
  btn.addEventListener("pointerup", () => { if (!rec) return; if (Date.now() - started < 350) { toggled = true; hint.textContent = "Recording — tap again to send"; return; } stop(); });
  btn.addEventListener("pointercancel", stop);
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
}

function renderSettings(opts) {
  const box = $("#settings");
  box.innerHTML = SETTINGS.map((s) => {
    const v = opts[s.key];
    if (s.type === "seg") return `<label class="field">${s.label}</label><div class="seg" data-key="${s.key}">
        ${s.options.map(([val, lab]) => `<button type="button" data-val='${JSON.stringify(val)}' aria-pressed="${JSON.stringify(val) === JSON.stringify(v ?? null)}">${lab}</button>`).join("")}</div>`;
    if (s.type === "toggle") return `<div class="toggle"><div><div style="font-weight:600">${s.label}</div>${s.hint ? `<div class="small muted">${s.hint}</div>` : ""}</div>
        <label class="switch"><input type="checkbox" data-key="${s.key}" ${v ? "checked" : ""} aria-label="${s.label}"><span></span></label></div>`;
    const shown = s.fromValue ? s.fromValue(v) : v;
    return `<label class="field">${s.label}: <span class="muted" id="lbl-${s.key}">${s.fmt(shown)}</span></label>
      <input type="range" data-key="${s.key}" min="${s.min}" max="${s.max}" step="${s.step}" value="${shown}">`;
  }).join("") + `<p class="small muted" style="margin-top:14px">Settings apply to the next edit you make (and to “Review &amp; change” re-renders).</p>`;
  const save = async (key, value) => {
    try { const p = await api(`/api/projects/${current.pid}`, { method: "PATCH", json: { options: { [key]: value } } }); current.project.options = p.options; }
    catch (e) { toast(e.message, "err"); }
  };
  $$(".seg", box).forEach((g) => g.onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$("button", g).forEach((x) => x.setAttribute("aria-pressed", x === b));
    save(g.dataset.key, JSON.parse(b.dataset.val));
  });
  $$("input[type=checkbox]", box).forEach((c) => c.onchange = () => save(c.dataset.key, c.checked));
  $$("input[type=range]", box).forEach((r) => {
    const s = SETTINGS.find((x) => x.key === r.dataset.key);
    r.oninput = () => $(`#lbl-${s.key}`).textContent = s.fmt(r.value);
    r.onchange = () => save(s.key, s.toValue ? s.toValue(r.value) : +r.value);
  });
}

async function setupStylePicker(p) {
  const sel = $("#style-pick");
  let styles = [];
  try { styles = await api("/api/styles"); } catch { }
  const own = `project_${p.id}`;
  const opts = [["", "No style (just pick the best moments)"]];
  if (styles.some((s) => s.name === own)) opts.push([own, "From this project's reference videos"]);
  styles.filter((s) => !s.name.startsWith("project_")).forEach((s) => opts.push([s.name, `Saved style: ${s.name}`]));
  styles.filter((s) => s.name.startsWith("project_") && s.name !== own).forEach((s) => opts.push([s.name, `From another project (${s.references.join(", ").slice(0, 40)})`]));
  sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}" ${v === (p.style || "") ? "selected" : ""}>${esc(l)}</option>`).join("");
  sel.onchange = async () => {
    const q = await api(`/api/projects/${p.id}`, { method: "PATCH", json: { style: sel.value } });
    current.project.style = q.style;
    const st = styles.find((s) => s.name === sel.value);
    $("#style-explain").innerHTML = st ? st.explanation.map((l) => `<li>${esc(l)}</li>`).join("") : "";
  };
  const st = styles.find((s) => s.name === p.style);
  if (st && !(p.style === own && p.style_explained)) $("#style-explain").innerHTML = st.explanation.map((l) => `<li>${esc(l)}</li>`).join("");
}

async function makeEdit() {
  const text = $("#instr").value.trim();
  if (!text) { toast("Say or type what you want first.", "err"); $("#instr").focus(); return; }
  try { if (window.Notification && Notification.permission === "default") Notification.requestPermission(); } catch { }
  try {
    await api(`/api/projects/${current.pid}/edits`, { method: "POST", json: { instruction: text } });
    toast("Working on it — you can lock your phone.");
    await refreshProject(); schedulePoll();
    $("#edits").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { toast(e.message, "err"); }
}

// ------------------------------------------------------------------ menu
function openMenu() {
  sheet(`<h2>Menu</h2><div class="stack">
      <button class="btn block" id="m-projects">All projects</button>
      <button class="btn block" id="m-pw">Change password</button>
      <button class="btn block" id="m-theme">Switch light / dark</button>
      <button class="btn block danger" id="m-out">Log out</button></div>`, (s, close) => {
    $("#m-projects", s).onclick = () => { close(); go("#/"); };
    $("#m-theme", s).onclick = () => {
      const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      r.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", r.dataset.theme); } catch { }
      close();
    };
    $("#m-out", s).onclick = async () => { await api("/api/logout", { method: "POST" }).catch(() => { }); close(); go("#/login"); };
    $("#m-pw", s).onclick = () => {
      close();
      sheet(`<h2>Change password</h2><form id="pwf">
          <label class="field">Current password</label><input type="password" id="pw0" autocomplete="current-password" required>
          <label class="field">New password (6+ characters)</label><input type="password" id="pw1" autocomplete="new-password" minlength="6" required>
          <button class="btn primary block" style="margin-top:14px">Save</button></form>`, (s2, close2) => {
        $("#pwf", s2).onsubmit = async (e) => {
          e.preventDefault();
          try { await api("/api/password", { method: "POST", json: { current: $("#pw0", s2).value, new: $("#pw1", s2).value } }); close2(); toast("Password changed. Other devices need to log in again."); }
          catch (err) { toast(err.message, "err"); }
        };
      });
    };
  });
}

// ------------------------------------------------------------------ boot
try { const th = localStorage.getItem("theme"); if (th) document.documentElement.dataset.theme = th; } catch { }
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
window.autocut = { api, go, sheet, toast, esc, fmtDur, setTop, current, refreshProject, $, $$, icon, SETTINGS };
import("/static/review.js").catch(() => { }).finally(route);
