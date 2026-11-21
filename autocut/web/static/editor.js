// CapCut-style timeline editor: preview with draggable/resizable text & stickers, scrolling
// multi-track timeline (clips, text, stickers, sounds, effects), context toolbar, undo/redo.
// Route: #/p/<pid>/edit/<eid>. Changes save automatically; "Export" renders on the computer.
const A = window.autocut;
const { api, esc, toast, sheet, $, $$ } = A;

const ICON = {
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
  pen: '<svg viewBox="0 0 24 24"><path d="M4 20l4-1 11-11-3-3L5 16l-1 4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  dup: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  rot: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-3-6.2M20 4v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M7 4.5v15l13-7.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  pause: '<svg viewBox="0 0 24 24" width="30" height="30"><rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor"/><rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor"/></svg>',
  undo: '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  redo: '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  text: '<svg viewBox="0 0 24 24"><path d="M5 6h10M10 6v13M15 13h5M17.5 10v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  sticker: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="9" cy="10" r="1.3" fill="currentColor"/><circle cx="15" cy="10" r="1.3" fill="currentColor"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  sound: '<svg viewBox="0 0 24 24"><path d="M4 10v4h4l5 4V6L8 10H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  fx: '<svg viewBox="0 0 24 24"><path d="M12 3l2.2 5.6L20 9.3l-4.4 3.9 1.3 5.8L12 16l-4.9 3 1.3-5.8L4 9.3l5.8-.7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  cc: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10.5 10.5a2 2 0 1 0 0 3M16.5 10.5a2 2 0 1 0 0 3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  split: '<svg viewBox="0 0 24 24"><path d="M8 4h3v16H8M16 4h-3v16h3" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  style: '<svg viewBox="0 0 24 24"><text x="3" y="18" font-size="15" font-weight="700" fill="currentColor" font-family="sans-serif">Aa</text></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="M14 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="M10 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M13 6l-6 6 6 6M19 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  vol: '<svg viewBox="0 0 24 24"><path d="M4 10v4h4l5 4V6L8 10H4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
};
const SWATCHES = ["#FFFFFF", "#111111", "#FFD84D", "#FF5A5F", "#45C9E0", "#7BE07B", "#C38BFF", "#FF9F43"];

let E = null;
const fmt = (s) => { s = Math.max(0, s); const m = Math.floor(s / 60); return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };
const uid = (p) => p + Math.random().toString(36).slice(2, 10);
const clone = (o) => JSON.parse(JSON.stringify(o));

// ------------------------------------------------------------------ time model (same as the server)
const fps = () => E.doc.canvas.fps;
const cdur = (c) => Math.max(1, Math.round((c.end - c.start) * fps())) / fps();
function starts() { const m = {}; let t = 0; for (const c of E.doc.clips) { m[c.id] = t; t += cdur(c); } return m; }
const total = () => E.doc.clips.reduce((s, c) => s + cdur(c), 0);
function span(it, st = starts()) {
  const c = E.doc.clips.find((x) => x.id === it.clip); if (!c) return null;
  const d = cdur(c), off = it.src !== undefined ? it.src - c.start : (it.offset || 0), du = it.dur || 0;
  if (off >= d - 0.02 || off + Math.max(du, 0.01) <= 0) return null;
  const a = Math.max(0, off), b = du ? Math.min(d, off + du) : a;
  return [st[c.id] + a, st[c.id] + b];
}
function clipAt(t) {
  const st = starts(); let last = null;
  for (const c of E.doc.clips) { last = c; if (t < st[c.id] + cdur(c) - 1e-6) return { clip: c, local: Math.max(0, t - st[c.id]), st }; }
  return last ? { clip: last, local: cdur(last) - 0.01, st } : null;
}
function anchorAt(it, t) {
  t = Math.max(0, Math.min(total() - 0.05, t));
  const { clip, local } = clipAt(t);
  it.clip = clip.id;
  if (it.src !== undefined) it.src = +(clip.start + local).toFixed(3); else it.offset = +local.toFixed(3);
}
const items = (track) => E.doc[track] || [];
function findSel() {
  if (!E.sel) return null;
  if (E.sel.type === "clip") return E.doc.clips.find((c) => c.id === E.sel.id);
  return items(E.sel.track).find((x) => x.id === E.sel.id);
}

// ------------------------------------------------------------------ history + saving
function commit(fn, { redraw = true } = {}) {
  E.hist.push(JSON.stringify(E.doc)); if (E.hist.length > 80) E.hist.shift();
  E.future = [];
  fn();
  E.version++;
  if (redraw) drawAll();
  scheduleSave();
}
function undo() { if (!E.hist.length) return; E.future.push(JSON.stringify(E.doc)); E.doc = JSON.parse(E.hist.pop()); E.version++; E.sel = null; drawAll(); scheduleSave(); }
function redo() { if (!E.future.length) return; E.hist.push(JSON.stringify(E.doc)); E.doc = JSON.parse(E.future.pop()); E.version++; E.sel = null; drawAll(); scheduleSave(); }
function scheduleSave() { clearTimeout(E.saveTimer); E.saveTimer = setTimeout(save, 700); $("#ed-saved").textContent = "Saving…"; }
async function save() {
  clearTimeout(E.saveTimer);
  const v = E.version;
  try {
    const doc = await api(`/api/projects/${E.pid}/edits/${E.eid}/timeline`, { method: "PUT", json: E.doc });
    if (v === E.version) { E.doc = doc; drawAll(); }  // server snapped clip edges to word gaps
    $("#ed-saved").textContent = "Saved";
  } catch (e) { $("#ed-saved").textContent = "Not saved"; toast(e.message, "err"); }
}

// ------------------------------------------------------------------ screen
window.screenEditor = async function (pid, eid) {
  document.body.classList.add("ed-dark");
  $("#app").innerHTML = `<p class="muted">Opening the editor…</p>`;
  let doc;
  try { doc = await api(`/api/projects/${pid}/edits/${eid}/timeline`); } catch (e) { toast(e.message, "err"); return; }
  E = { pid, eid, doc, pps: 60, t: 0, playing: false, sel: null, hist: [], future: [], version: 0, lib: null, played: new Set(), audio: {} };
  $("#app").innerHTML = `
  <div class="ed">
    <div class="ed-top">
      <button class="ed-icon" id="ed-close" aria-label="Close editor">${ICON.x.replace("<svg", '<svg width="26" height="26"')}</button>
      <div class="grow"></div>
      <span class="ed-pill" id="ed-saved">Saved</span>
      <button class="ed-export" id="ed-export">Export</button>
    </div>
    <div class="ed-stage" id="ed-stage"><div class="ed-box" id="ed-box">
      <video id="ed-video" playsinline muted preload="auto"></video>
      <div class="ed-flash" id="ed-flash"></div><div id="ed-ovs"></div></div></div>
    <div class="ed-transport">
      <span class="ed-time" id="ed-time"></span>
      <button class="ed-icon" id="ed-play" aria-label="Play">${ICON.play}</button>
      <div class="ed-ur"><button class="ed-icon" id="ed-undo" aria-label="Undo">${ICON.undo}</button><button class="ed-icon" id="ed-redo" aria-label="Redo">${ICON.redo}</button></div>
    </div>
    <div class="ed-tl" id="ed-tl"><div class="ed-tl-inner" id="ed-tl-inner"></div></div>
    <div class="ed-playhead" id="ed-playhead"></div>
    <div class="ed-tools" id="ed-tools"></div>
  </div>`;
  $("#ed-close").onclick = async () => { await save(); stop(); document.body.classList.remove("ed-dark"); A.go(`#/p/${pid}`); };
  $("#ed-export").onclick = exportNow;
  $("#ed-play").onclick = () => (E.playing ? stop() : play());
  $("#ed-undo").onclick = undo; $("#ed-redo").onclick = redo;
  const tl = $("#ed-tl");
  tl.addEventListener("scroll", () => { if (E.progScroll) { E.progScroll = false; return; } if (!E.playing) { E.t = tl.scrollLeft / E.pps; seek(); drawPreview(); drawTime(); } }, { passive: true });
  setupPinch(tl);
  window.addEventListener("resize", layout);
  if (window.ResizeObserver) new ResizeObserver(() => fitBox()).observe($("#ed-stage"));
  const v = $("#ed-video");
  v.muted = false;
  layout(); drawAll(); seek();
};

function fitBox() {
  const st = $("#ed-stage"), box = $("#ed-box"); if (!E || !st || !box) return;
  const W = st.clientWidth, H = Math.max(60, st.clientHeight - 12), ar = E.doc.canvas.w / E.doc.canvas.h;
  let w = W, h = W / ar; if (h > H) { h = H; w = H * ar; }
  if (Math.abs(box.clientWidth - w) > 1 || Math.abs(box.clientHeight - h) > 1) {
    box.style.width = `${w}px`; box.style.height = `${h}px`; drawPreview();
  }
}
function layout() {
  if (!E || !$("#ed-stage")) return;
  fitBox();
  const tl = $("#ed-tl"), ph = $("#ed-playhead"), r = tl.getBoundingClientRect();
  ph.style.position = "fixed"; ph.style.top = `${r.top + 18}px`; ph.style.height = `${r.height - 18}px`; ph.style.left = `${r.left + r.width / 2}px`;
  drawAll();
}

function drawAll() { if (!E) return; drawTimeline(); drawPreview(); drawTools(); drawTime(); $("#ed-undo").disabled = !E.hist.length; $("#ed-redo").disabled = !E.future.length; }
function drawTime() { $("#ed-time").innerHTML = `<b>${fmt(E.t)}</b> / ${fmt(total())}`; }

// ------------------------------------------------------------------ preview
function seek() {
  const at = clipAt(E.t); if (!at) return;
  const v = $("#ed-video"), want = at.clip.start + at.local;
  if (v.dataset.hash !== at.clip.hash) { v.dataset.hash = at.clip.hash; v.src = at.clip.proxy; v.addEventListener("loadedmetadata", () => { v.currentTime = want; }, { once: true }); }
  else if (Math.abs(v.currentTime - want) > 0.05) v.currentTime = want;
  E.cur = at.clip.id;
}
function play() {
  if (E.t >= total() - 0.05) E.t = 0;
  E.playing = true; E.played = new Set(); $("#ed-play").innerHTML = ICON.pause;
  seek(); const v = $("#ed-video"); v.play().catch(() => { });
  const tick = () => {
    if (!E || !E.playing) return;
    const at = clipAt(E.t), c = E.doc.clips.find((x) => x.id === E.cur) || at.clip, st = starts();
    let t = st[c.id] + (v.currentTime - c.start);
    if (v.currentTime >= c.end - 0.5 / fps()) {
      const i = E.doc.clips.indexOf(c);
      if (i >= E.doc.clips.length - 1) { stop(); E.t = total(); drawTime(); return; }
      E.t = st[E.doc.clips[i + 1].id] + 0.001; seek(); v.play().catch(() => { });
      t = E.t;
    }
    E.t = Math.max(st[c.id], t);
    for (const s of items("sounds")) {  // sound effects while previewing
      const sp = span({ ...s, dur: 0.01 }, st);
      if (sp && !E.played.has(s.id) && E.t >= sp[0] && E.t < sp[0] + 0.3) { E.played.add(s.id); sfxPlay(s); }
    }
    const tl = $("#ed-tl"); E.progScroll = true; tl.scrollLeft = E.t * E.pps;
    drawPreview(); drawTime();
    E.raf = requestAnimationFrame(tick);
  };
  E.raf = requestAnimationFrame(tick);
}
function stop() { if (!E) return; E.playing = false; cancelAnimationFrame(E.raf); const v = $("#ed-video"); v && v.pause(); const b = $("#ed-play"); if (b) b.innerHTML = ICON.play; }
function sfxPlay(s) {
  const lib = (E.lib && E.lib.sfx) || [];
  const url = (lib.find((x) => x.id === s.asset) || {}).url || `/api/library/file?kind=sfx&id=${encodeURIComponent(s.asset)}`;
  const a = E.audio[url] || (E.audio[url] = new Audio(url));
  a.volume = Math.min(1, Math.pow(10, (s.gain_db || -6) / 20) * 1.6); a.currentTime = 0; a.play().catch(() => { });
}
const stickerUrl = (id) => `/api/library/file?kind=stickers&id=${encodeURIComponent(id)}`;

function drawPreview() {
  const box = $("#ed-box"); if (!box) return;
  const ovs = $("#ed-ovs"), W = box.clientWidth, H = box.clientHeight, st = starts(), opts = E.doc.options || {};
  // effects at the playhead: zoom cut, flash, shake
  let zoom = 1, flash = 0, shake = false;
  for (const e of items("effects")) {
    const sp = span(e, st); if (!sp || E.t < sp[0] || E.t > sp[1]) continue;
    if (e.kind === "zoom") zoom = e.scale || 1.15; else if (e.kind === "flash") flash = 0.8; else if (e.kind === "shake") shake = true;
  }
  const jx = shake ? Math.sin(E.t * 47) * 6 : 0, jy = shake ? Math.cos(E.t * 39) * 6 : 0;
  $("#ed-video").style.transform = `translate(${jx}px,${jy}px) scale(${zoom * (shake ? 1.06 : 1)})`;
  $("#ed-flash").style.opacity = flash;
  let html = "";
  for (const it of items("stickers")) {
    const sp = span(it, st); if (!sp || E.t < sp[0] || E.t > sp[1]) continue;
    const w = (it.w || 0.12) * (it.scale || 1) * W;
    html += `<div class="ed-ov" data-track="stickers" data-id="${it.id}" style="left:${it.x * W}px;top:${it.y * H}px;width:${w}px;transform:translate(-50%,-50%) rotate(${it.rotation || 0}deg)">
      <img class="ed-sticker" src="${stickerUrl(it.asset)}" style="width:100%" alt=""></div>`;
  }
  for (const it of items("texts")) {
    const sp = span(it, st); if (!sp || E.t < sp[0] || E.t > sp[1]) continue;
    const s = it.style || {}, size = (s.size || 0.045) * H * (it.scale || 1);
    let text = it.text;
    if (it.kind === "caption") text = opts.caption_case === "lower" ? text.toLowerCase() : opts.caption_case === "upper" ? text.toUpperCase() : text;
    const outline = s.outline || "#111";
    html += `<div class="ed-ov ed-text" data-track="texts" data-id="${it.id}" style="left:${it.x * W}px;top:${it.y * H}px;font-size:${size}px;color:${s.color || "#fff"};
      ${s.bold ? "font-weight:800;" : ""}${s.box ? `background:${s.box_color || "rgba(0,0,0,.6)"};border-radius:.25em;` : ""}
      text-shadow:-1px -1px 0 ${outline},1px -1px 0 ${outline},-1px 1px 0 ${outline},1px 1px 0 ${outline};
      transform:translate(-50%,-50%) rotate(${it.rotation || 0}deg)">${esc(text)}</div>`;
  }
  ovs.innerHTML = html;
  const sel = E.sel && E.sel.type !== "clip" ? ovs.querySelector(`[data-id="${E.sel.id}"]`) : null;
  if (sel) {
    const r = sel.getBoundingClientRect(), b = box.getBoundingClientRect();
    // keep the box big enough that the handles never cover small text
    const cx = (r.left + r.right) / 2 - b.left, cy = (r.top + r.bottom) / 2 - b.top;
    const hw = Math.max(r.width / 2 + 6, 34), hh = Math.max(r.height / 2 + 6, 26);
    const x0 = cx - hw, y0 = cy - hh, x1 = cx + hw, y1 = cy + hh;
    ovs.insertAdjacentHTML("beforeend", `<div class="ed-sel" style="left:${x0}px;top:${y0}px;width:${x1 - x0}px;height:${y1 - y0}px"></div>
      <div class="ed-h" data-h="del" style="left:${x0}px;top:${y0}px">${ICON.x}</div>
      <div class="ed-h" data-h="edit" style="left:${x1}px;top:${y0}px">${ICON.pen}</div>
      <div class="ed-h" data-h="dup" style="left:${x0}px;top:${y1}px">${ICON.dup}</div>
      <div class="ed-h" data-h="rot" style="left:${x1}px;top:${y1}px">${ICON.rot}</div>`);
  }
  wirePreview();
}

function wirePreview() {
  const box = $("#ed-box");
  $$(".ed-ov", box).forEach((el) => {
    el.onpointerdown = (ev) => {
      ev.preventDefault(); stop();
      const track = el.dataset.track, id = el.dataset.id, it = items(track).find((x) => x.id === id);
      const W = box.clientWidth, H = box.clientHeight, x0 = ev.clientX, y0 = ev.clientY, ix = it.x, iy = it.y;
      let moved = false, snap = null;
      el.setPointerCapture(ev.pointerId);
      el.onpointermove = (m) => {
        const dx = (m.clientX - x0) / W, dy = (m.clientY - y0) / H;
        if (!moved && Math.hypot(m.clientX - x0, m.clientY - y0) < 4) return;
        if (!moved) { moved = true; snap = JSON.stringify(E.doc); E.sel = { type: "item", track, id }; }
        it.x = +(ix + dx).toFixed(4); it.y = +(iy + dy).toFixed(4);
        el.style.left = `${it.x * W}px`; el.style.top = `${it.y * H}px`;
      };
      el.onpointerup = () => {
        el.onpointermove = null; el.onpointerup = null;
        if (moved) { E.hist.push(snap); E.future = []; E.version++; it.edited = true; scheduleSave(); }
        E.sel = { type: "item", track, id }; drawAll();
      };
    };
  });
  $$(".ed-h", box).forEach((h) => {
    h.onpointerdown = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const it = findSel(); if (!it) return;
      const kind = h.dataset.h;
      if (kind === "del") return commit(() => { E.doc[E.sel.track] = items(E.sel.track).filter((x) => x.id !== it.id); E.sel = null; });
      if (kind === "dup") return commit(() => { const c = clone(it); c.id = uid(E.sel.track[0]); c.x = Math.min(0.95, it.x + 0.04); c.y = Math.min(0.95, it.y + 0.04); c.edited = true; c.auto = false; items(E.sel.track).push(c); E.sel = { ...E.sel, id: c.id }; });
      if (kind === "edit") return E.sel.track === "texts" ? editText(it) : pickSticker((a) => commit(() => { it.asset = a; it.edited = true; }));
      // rotate + scale around the element's centre
      const b = box.getBoundingClientRect(), cx = b.left + it.x * b.width, cy = b.top + it.y * b.height;
      const a0 = Math.atan2(ev.clientY - cy, ev.clientX - cx), d0 = Math.hypot(ev.clientX - cx, ev.clientY - cy);
      const r0 = it.rotation || 0, s0 = it.scale || 1, snap = JSON.stringify(E.doc);
      h.setPointerCapture(ev.pointerId);
      h.onpointermove = (m) => {
        const a = Math.atan2(m.clientY - cy, m.clientX - cx), d = Math.hypot(m.clientX - cx, m.clientY - cy);
        it.rotation = +(r0 + (a - a0) * 180 / Math.PI).toFixed(1);
        it.scale = +Math.max(0.2, Math.min(6, s0 * d / Math.max(10, d0))).toFixed(3);
        drawPreview();
      };
      h.onpointerup = () => { h.onpointermove = null; E.hist.push(snap); E.future = []; E.version++; it.edited = true; scheduleSave(); drawAll(); };
    };
  });
  box.onpointerdown = (ev) => { if (ev.target === box || ev.target.tagName === "VIDEO") { if (E.sel && E.sel.type !== "clip") { E.sel = null; drawAll(); } } };
}

// ------------------------------------------------------------------ timeline
const ROWS = { clips: 24, texts: 78, stickers: 112, sounds: 146, effects: 174 };
function drawTimeline() {
  const tl = $("#ed-tl"), inner = $("#ed-tl-inner"); if (!tl) return;
  const vw = tl.clientWidth, pad = vw / 2, T = total(), pps = E.pps, st = starts();
  inner.style.width = `${T * pps + vw}px`;
  const X = (t) => pad + t * pps;
  let h = `<div class="ed-ruler">`;
  const step = pps >= 90 ? 1 : pps >= 40 ? 2 : pps >= 20 ? 5 : 10;
  for (let t = 0; t <= T + 0.01; t += step) h += `<span style="left:${X(t)}px">${fmt(t)}</span>`;
  h += `</div>`;
  for (const c of E.doc.clips) {
    const w = cdur(c) * pps, n = Math.max(1, Math.ceil(w / 100)), sel = E.sel && E.sel.type === "clip" && E.sel.id === c.id;
    let imgs = "";
    for (let k = 0; k < n; k++) imgs += `<img loading="lazy" src="${c.thumb}${(c.start + (k + 0.5) * (c.end - c.start) / n).toFixed(1)}" alt="">`;
    h += `<div class="ed-clip ${sel ? "sel" : ""}" data-clip="${c.id}" style="left:${X(st[c.id])}px;top:${ROWS.clips}px;width:${w - 2}px;${sel ? "touch-action:none" : ""}">${imgs}
      ${sel ? `<div class="ed-trim l" data-trim="l">|</div><div class="ed-trim r" data-trim="r">|</div>` : ""}</div>`;
  }
  h += `<button class="ed-addclip" id="ed-addclip" style="left:${X(T) + 10}px;top:${ROWS.clips + 6}px" aria-label="Add a moment">+</button>`;
  const blk = (track, it, cls, label) => {
    const sp = span(it, st); if (!sp) return "";
    const sel = E.sel && E.sel.id === it.id, w = Math.max(18, (sp[1] - sp[0]) * pps);
    return `<div class="ed-blk ${cls} ${sel ? "sel" : ""}" data-track="${track}" data-id="${it.id}" style="left:${X(sp[0])}px;top:${ROWS[track]}px;width:${w}px;${sel ? "touch-action:none" : ""}">${label}
      ${sel && it.dur ? `<div class="ed-trim l" data-trim="l" style="width:16px">‹</div><div class="ed-trim r" data-trim="r" style="width:16px">›</div>` : ""}</div>`;
  };
  for (const it of items("texts")) h += blk("texts", it, it.kind === "caption" ? "caption" : "text", esc(it.text));
  for (const it of items("stickers")) h += blk("stickers", it, "sticker", `<img src="${stickerUrl(it.asset)}" alt="">`);
  for (const it of items("sounds")) h += blk("sounds", { ...it, dur: 0.6 }, "sound", "♪ " + esc((it.asset.split(":")[1] || "").replace(/_/g, " ").replace(/\.\w+$/, "")));
  for (const it of items("effects")) h += blk("effects", it, "effect", { zoom: "Zoom", flash: "Flash", shake: "Shake" }[it.kind] || it.kind);
  inner.innerHTML = h;
  E.progScroll = true; tl.scrollLeft = E.t * pps;
  wireTimeline();
}

function wireTimeline() {
  const inner = $("#ed-tl-inner");
  $("#ed-addclip").onclick = openRunnerUps;
  $$(".ed-clip", inner).forEach((el) => {
    el.onclick = (ev) => { if (E.dragging) return; stop(); const c = E.doc.clips.find((x) => x.id === el.dataset.clip); E.sel = { type: "clip", id: c.id }; drawAll(); };
    if (!el.classList.contains("sel")) return;
    // drag the selected clip to reorder it
    el.onpointerdown = (ev) => {
      if (ev.target.dataset.trim) return trimStart(ev, "clip", el.dataset.clip, ev.target.dataset.trim);
      ev.preventDefault();
      const x0 = ev.clientX; let moved = false;
      el.setPointerCapture(ev.pointerId);
      el.onpointermove = (m) => { if (Math.abs(m.clientX - x0) > 8) { moved = true; E.dragging = true; el.classList.add("drag"); el.style.transform = `translateX(${m.clientX - x0}px)`; } };
      el.onpointerup = (m) => {
        el.onpointermove = null; el.onpointerup = null;
        setTimeout(() => (E.dragging = false), 0);
        if (!moved) return;
        const st = starts(), c = E.doc.clips.find((x) => x.id === el.dataset.clip);
        const center = st[c.id] + cdur(c) / 2 + (m.clientX - x0) / E.pps;
        commit(() => {
          const rest = E.doc.clips.filter((x) => x.id !== c.id);
          let idx = rest.length, acc = 0;
          for (let i = 0; i < rest.length; i++) { if (center < acc + cdur(rest[i]) / 2) { idx = i; break; } acc += cdur(rest[i]); }
          rest.splice(idx, 0, c); E.doc.clips = rest;
        });
      };
    };
  });
  $$(".ed-blk", inner).forEach((el) => {
    el.onclick = () => { if (E.dragging) return; stop(); E.sel = { type: "item", track: el.dataset.track, id: el.dataset.id }; const it = findSel(); const sp = span({ ...it, dur: it.dur || 0.6 }); if (sp && (E.t < sp[0] || E.t > sp[1])) { E.t = sp[0] + 0.01; seek(); } drawAll(); };
    if (!el.classList.contains("sel")) return;
    el.onpointerdown = (ev) => {
      if (ev.target.dataset.trim) return trimStart(ev, el.dataset.track, el.dataset.id, ev.target.dataset.trim);
      ev.preventDefault();
      const it = items(el.dataset.track).find((x) => x.id === el.dataset.id), sp0 = span({ ...it, dur: it.dur || 0.6 }), x0 = ev.clientX;
      let moved = false; const snap = JSON.stringify(E.doc);
      el.setPointerCapture(ev.pointerId);
      el.onpointermove = (m) => { const dx = m.clientX - x0; if (Math.abs(dx) > 6) { moved = true; E.dragging = true; el.style.transform = `translateX(${dx}px)`; } };
      el.onpointerup = (m) => {
        el.onpointermove = null; el.onpointerup = null; setTimeout(() => (E.dragging = false), 0);
        if (!moved) return;
        E.hist.push(snap); E.future = []; E.version++;
        anchorAt(it, sp0[0] + (m.clientX - x0) / E.pps); it.edited = true; drawAll(); scheduleSave();
      };
    };
  });
}

function trimStart(ev, track, id, side) {
  ev.preventDefault(); ev.stopPropagation(); stop();
  const target = track === "clip" ? E.doc.clips.find((c) => c.id === id) : items(track).find((x) => x.id === id);
  const snap = JSON.stringify(E.doc), x0 = ev.clientX, orig = clone(target), el = ev.target;
  const sp0 = track === "clip" ? null : span(target);
  el.setPointerCapture(ev.pointerId);
  el.onpointermove = (m) => {
    const d = (m.clientX - x0) / E.pps;
    if (track === "clip") {
      if (side === "l") target.start = +Math.max(0, Math.min(orig.end - 0.3, orig.start + d)).toFixed(3);
      else target.end = +Math.max(orig.start + 0.3, Math.min(target.src_duration || 1e9, orig.end + d)).toFixed(3);
    } else if (side === "r") target.dur = +Math.max(0.1, orig.dur + d).toFixed(3);
    else { const ns = Math.min(sp0[1] - 0.1, sp0[0] + d); anchorAt(target, ns); target.dur = +(sp0[1] - ns).toFixed(3); }
    drawTimeline(); drawPreview(); drawTime();
  };
  el.onpointerup = () => { el.onpointermove = null; el.onpointerup = null; E.hist.push(snap); E.future = []; E.version++; if (track !== "clip") target.edited = true; drawAll(); scheduleSave(); };
}

function setupPinch(tl) {
  const pts = new Map(); let d0 = 0, p0 = 60;
  tl.addEventListener("pointerdown", (e) => { pts.set(e.pointerId, e.clientX); if (pts.size === 2) { const [a, b] = [...pts.values()]; d0 = Math.abs(a - b); p0 = E.pps; } });
  tl.addEventListener("pointermove", (e) => { if (!pts.has(e.pointerId)) return; pts.set(e.pointerId, e.clientX); if (pts.size === 2 && d0 > 20) { const [a, b] = [...pts.values()]; E.pps = Math.max(8, Math.min(240, p0 * Math.abs(a - b) / d0)); drawTimeline(); } });
  const end = (e) => pts.delete(e.pointerId);
  tl.addEventListener("pointerup", end); tl.addEventListener("pointercancel", end);
  tl.addEventListener("wheel", (e) => { if (e.ctrlKey) { e.preventDefault(); E.pps = Math.max(8, Math.min(240, E.pps * (e.deltaY < 0 ? 1.1 : 0.9))); drawTimeline(); } }, { passive: false });
}

// ------------------------------------------------------------------ toolbar
function tool(id, icon, label, cls = "") { return `<button class="ed-tool ${cls}" data-tool="${id}">${ICON[icon]}<span>${label}</span></button>`; }
function drawTools() {
  const el = $("#ed-tools"); if (!el) return;
  const s = E.sel, it = findSel();
  let h = "";
  if (!s || !it) {
    h = tool("addtext", "text", "Add text") + tool("stickers", "sticker", "Stickers") + tool("sounds", "sound", "Sounds") + tool("effects", "fx", "Effects")
      + tool("captions", "cc", "Captions") + tool("settings", "gear", "Settings") + tool("zoomin", "right", "Zoom in") + tool("zoomout", "left", "Zoom out");
  } else {
    h = tool("deselect", "back", "", "back");
    if (s.type === "clip") h += tool("split", "split", "Split") + tool("moveL", "left", "Move left") + tool("moveR", "right", "Move right") + tool("dupclip", "dup", "Duplicate") + tool("delete", "trash", "Delete", "danger");
    else if (s.track === "texts") h += tool("edittext", "pen", "Edit") + tool("style", "style", "Style") + tool("splititem", "split", "Split") + tool("dupitem", "dup", "Duplicate") + tool("delete", "trash", "Delete", "danger");
    else if (s.track === "stickers") h += tool("replace", "sticker", "Replace") + tool("dupitem", "dup", "Duplicate") + tool("delete", "trash", "Delete", "danger");
    else if (s.track === "sounds") h += tool("replacesound", "sound", "Replace") + tool("voldown", "vol", "Quieter") + tool("volup", "vol", "Louder") + tool("preview", "sound", "Listen") + tool("delete", "trash", "Delete", "danger");
    else if (s.track === "effects") h += tool("fxkind", "fx", "Change") + tool("dupitem", "dup", "Duplicate") + tool("delete", "trash", "Delete", "danger");
  }
  el.innerHTML = h;
  $$("[data-tool]", el).forEach((b) => b.onclick = () => runTool(b.dataset.tool));
}

function runTool(t) {
  const it = findSel();
  const at = clipAt(E.t);
  switch (t) {
    case "deselect": E.sel = null; drawAll(); break;
    case "zoomin": E.pps = Math.min(240, E.pps * 1.5); drawTimeline(); break;
    case "zoomout": E.pps = Math.max(8, E.pps / 1.5); drawTimeline(); break;
    case "addtext": {
      const n = { id: uid("t"), kind: "title", text: "text", x: 0.5, y: 0.4, scale: 1, rotation: 0, dur: 3, style: { size: 0.08, color: "#FFFFFF", outline: "#111111", bold: true }, edited: true, auto: false };
      commit(() => { anchorAt(n, E.t); E.doc.texts.push(n); E.sel = { type: "item", track: "texts", id: n.id }; });
      editText(n); break;
    }
    case "stickers": pickSticker((a) => { const n = { id: uid("k"), asset: a, x: 0.75, y: 0.3, w: 0.14, scale: 1, rotation: 0, dur: 2, edited: true, auto: false }; commit(() => { anchorAt(n, E.t); E.doc.stickers.push(n); E.sel = { type: "item", track: "stickers", id: n.id }; }); }); break;
    case "sounds": pickSound((a) => { const n = { id: uid("s"), asset: a, gain_db: -6, edited: true, auto: false }; commit(() => { anchorAt(n, E.t); E.doc.sounds.push(n); E.sel = { type: "item", track: "sounds", id: n.id }; }); }); break;
    case "effects": pickEffect((k) => { const n = { id: uid("e"), kind: k, dur: k === "zoom" ? 2 : k === "flash" ? 0.12 : 0.4, scale: 1.15, edited: true, auto: false }; commit(() => { anchorAt(n, E.t); E.doc.effects.push(n); E.sel = { type: "item", track: "effects", id: n.id }; }); }); break;
    case "captions": captionsSheet(); break;
    case "settings": settingsSheet(); break;
    case "delete":
      commit(() => {
        if (E.sel.type === "clip") { if (E.doc.clips.length <= 1) { toast("An edit needs at least one clip.", "err"); return; } E.doc.clips = E.doc.clips.filter((c) => c.id !== it.id); }
        else E.doc[E.sel.track] = items(E.sel.track).filter((x) => x.id !== it.id);
        E.sel = null;
      }); break;
    case "dupitem": commit(() => { const c = clone(it); c.id = uid(E.sel.track[0]); c.edited = true; c.auto = false; const sp = span({ ...it, dur: it.dur || 0.6 }); anchorAt(c, sp[1] + 0.05); items(E.sel.track).push(c); E.sel = { ...E.sel, id: c.id }; }); break;
    case "dupclip": commit(() => { const i = E.doc.clips.indexOf(it), c = { ...clone(it), id: uid("c") }; E.doc.clips.splice(i + 1, 0, c); E.sel = { type: "clip", id: c.id }; }); break;
    case "moveL": case "moveR":
      commit(() => { const i = E.doc.clips.indexOf(it), j = i + (t === "moveL" ? -1 : 1); if (j < 0 || j >= E.doc.clips.length) return; [E.doc.clips[i], E.doc.clips[j]] = [E.doc.clips[j], E.doc.clips[i]]; }, { redraw: false });
      E.t = starts()[it.id] + Math.min(0.5, cdur(it) / 2); seek(); drawAll();  // keep the moved clip in view
      break;
    case "split": {
      if (!at || at.clip.id !== it.id || at.local < 0.3 || cdur(it) - at.local < 0.3) { toast("Move the playhead inside the clip (not too close to its ends) to split it.", "err"); break; }
      commit(() => {
        const cut = +(it.start + at.local).toFixed(3), b = { ...clone(it), id: uid("c"), start: cut }; it.end = cut;
        E.doc.clips.splice(E.doc.clips.indexOf(it) + 1, 0, b);
        for (const tr of ["texts", "stickers", "sounds", "effects"]) for (const x of items(tr)) {
          if (x.clip !== it.id) continue;
          if (x.src !== undefined) { if (x.src >= cut) x.clip = b.id; } else if ((x.offset || 0) >= at.local) { x.clip = b.id; x.offset = +(x.offset - at.local).toFixed(3); }
        }
      });
      toast("Split. Edges snap to the nearest gap between words when saved."); break;
    }
    case "splititem": {
      const sp = span(it); if (!sp || E.t <= sp[0] + 0.1 || E.t >= sp[1] - 0.1) { toast("Move the playhead inside the text to split it.", "err"); break; }
      commit(() => { const c = clone(it); c.id = uid("t"); it.dur = +(E.t - sp[0]).toFixed(3); anchorAt(c, E.t); c.dur = +(sp[1] - E.t).toFixed(3); it.edited = c.edited = true; E.doc.texts.push(c); });
      break;
    }
    case "edittext": editText(it); break;
    case "style": styleText(it); break;
    case "replace": pickSticker((a) => commit(() => { it.asset = a; it.edited = true; })); break;
    case "replacesound": pickSound((a) => commit(() => { it.asset = a; it.edited = true; })); break;
    case "voldown": commit(() => { it.gain_db = Math.max(-30, (it.gain_db ?? -6) - 3); it.edited = true; }); toast(`Volume ${it.gain_db} dB`); break;
    case "volup": commit(() => { it.gain_db = Math.min(6, (it.gain_db ?? -6) + 3); it.edited = true; }); toast(`Volume ${it.gain_db} dB`); break;
    case "preview": sfxPlay(it); break;
    case "fxkind": pickEffect((k) => commit(() => { it.kind = k; it.dur = k === "flash" ? 0.12 : it.dur; it.edited = true; })); break;
  }
}

// ------------------------------------------------------------------ sheets
async function library() { if (!E.lib) E.lib = await api("/api/library"); return E.lib; }
function editText(it) {
  sheet(`<h2>Edit text</h2><textarea id="tx" style="min-height:80px">${esc(it.text)}</textarea>
    <button class="btn primary block" id="tx-ok" style="margin-top:10px">Done</button>`, (s, close) => {
    const ta = $("#tx", s); ta.focus(); ta.select();
    $("#tx-ok", s).onclick = () => { close(); if (ta.value !== it.text) commit(() => { it.text = ta.value || " "; it.edited = true; }); };
  });
}
function styleText(it) {
  const st = it.style || (it.style = {});
  sheet(`<h2>Style</h2>
    <label class="field">Size</label><input type="range" id="sz" min="0.02" max="0.2" step="0.005" value="${st.size || 0.045}">
    <label class="field">Colour</label><div class="ed-swatches" id="sw">${SWATCHES.map((c) => `<button style="background:${c}" data-c="${c}" aria-pressed="${(st.color || "#FFFFFF").toUpperCase() === c}" aria-label="${c}"></button>`).join("")}</div>
    <label class="field">Outline</label><div class="ed-swatches" id="ol">${SWATCHES.map((c) => `<button style="background:${c}" data-c="${c}" aria-pressed="${(st.outline || "#111111").toUpperCase() === c}" aria-label="${c}"></button>`).join("")}</div>
    <div class="toggle"><span>Bold</span><label class="switch"><input type="checkbox" id="bd" ${st.bold ? "checked" : ""}><span></span></label></div>
    <div class="toggle"><span>Background box</span><label class="switch"><input type="checkbox" id="bx" ${st.box ? "checked" : ""}><span></span></label></div>
    ${it.kind === "caption" ? `<button class="btn block" id="all" style="margin-top:10px">Use this style for all captions</button>` : ""}`, (s) => {
    const upd = (fn) => { commit(() => { fn(); it.edited = true; }, { redraw: false }); drawPreview(); drawTimeline(); };
    $("#sz", s).oninput = (e) => { st.size = +e.target.value; drawPreview(); };
    $("#sz", s).onchange = (e) => upd(() => (st.size = +e.target.value));
    $$("#sw button", s).forEach((b) => b.onclick = () => { $$("#sw button", s).forEach((x) => x.setAttribute("aria-pressed", x === b)); upd(() => (st.color = b.dataset.c)); });
    $$("#ol button", s).forEach((b) => b.onclick = () => { $$("#ol button", s).forEach((x) => x.setAttribute("aria-pressed", x === b)); upd(() => (st.outline = b.dataset.c)); });
    $("#bd", s).onchange = (e) => upd(() => (st.bold = e.target.checked));
    $("#bx", s).onchange = (e) => upd(() => (st.box = e.target.checked));
    const all = $("#all", s);
    if (all) all.onclick = () => { commit(() => { for (const x of E.doc.texts) if (x.kind === "caption") { x.style = { ...clone(st) }; x.y = it.y; x.x = it.x; } }); toast("Applied to all captions."); };
  });
}
async function pickSticker(cb) {
  const lib = await library();
  sheet(`<h2>Stickers</h2><div class="ed-grid">${lib.stickers.map((s) => `<button data-a="${esc(s.id)}" aria-label="${esc(s.name)}"><img src="${s.url}" alt="" loading="lazy"></button>`).join("")}</div>
    <label class="btn block" style="margin-top:12px">Add your own sticker (PNG)<input type="file" accept="image/png,image/webp,image/gif" hidden id="up"></label>`, (s, close) => {
    $$("[data-a]", s).forEach((b) => b.onclick = () => { close(); cb(b.dataset.a); });
    $("#up", s).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; const fd = new FormData(); fd.append("file", f); try { const r = await api("/api/library/stickers", { method: "POST", body: fd }); E.lib = null; close(); cb(r.id); } catch (er) { toast(er.message, "err"); } };
  });
}
async function pickSound(cb) {
  const lib = await library();
  sheet(`<h2>Sounds</h2><div class="stack">${lib.sfx.map((s) => `<div class="row"><button class="btn small-btn" data-play="${esc(s.url)}" aria-label="Listen">▶</button>
      <div class="grow">${esc(s.name)} <span class="small muted">${s.source}</span></div><button class="btn small-btn primary" data-a="${esc(s.id)}">Add</button></div>`).join("")}</div>
    <label class="btn block" style="margin-top:12px">Add your own sound<input type="file" accept="audio/*" hidden id="up"></label>`, (s, close) => {
    $$("[data-play]", s).forEach((b) => b.onclick = () => { const a = new Audio(b.dataset.play); a.play().catch(() => { }); });
    $$("[data-a]", s).forEach((b) => b.onclick = () => { close(); cb(b.dataset.a); });
    $("#up", s).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; const fd = new FormData(); fd.append("file", f); try { const r = await api("/api/library/sfx", { method: "POST", body: fd }); E.lib = null; close(); cb(r.id); } catch (er) { toast(er.message, "err"); } };
  });
}
function pickEffect(cb) {
  sheet(`<h2>Effects</h2><div class="stack">
    <button class="btn block" data-k="zoom">Zoom cut — jump in closer</button>
    <button class="btn block" data-k="flash">Flash — quick white flash</button>
    <button class="btn block" data-k="shake">Shake — camera shake</button></div>`, (s, close) => { $$("[data-k]", s).forEach((b) => b.onclick = () => { close(); cb(b.dataset.k); }); });
}
function captionsSheet() {
  const caps = E.doc.texts.filter((t) => t.kind === "caption");
  const o = E.doc.options || {};
  sheet(`<h2>Captions</h2><p class="sub">${caps.length} caption line${caps.length === 1 ? "" : "s"} (your voice only). Tap one on the timeline to edit or move it.</p>
    <label class="field">Letters</label><div class="seg" id="cs">${[["original", "As spoken"], ["lower", "lowercase"], ["upper", "UPPERCASE"]].map(([v, l]) => `<button data-v="${v}" aria-pressed="${(o.caption_case || "original") === v}">${l}</button>`).join("")}</div>
    <label class="field">Position for all captions</label><div class="seg" id="cp"><button data-y="0.1">Top</button><button data-y="0.5">Middle</button><button data-y="${E.doc.canvas.h > E.doc.canvas.w ? 0.76 : 0.9}">Bottom</button></div>
    <div class="stack" style="margin-top:14px"><button class="btn block" id="cap-reset">Re-create captions & automatic effects</button>
    <button class="btn block danger" id="cap-del">Remove all captions</button></div>`, (s, close) => {
    $$("#cs button", s).forEach((b) => b.onclick = () => { $$("#cs button", s).forEach((x) => x.setAttribute("aria-pressed", x === b)); commit(() => { E.doc.options.caption_case = b.dataset.v; }); });
    $$("#cp button", s).forEach((b) => b.onclick = () => commit(() => { for (const t of E.doc.texts) if (t.kind === "caption") t.y = +b.dataset.y; }));
    $("#cap-del", s).onclick = () => { close(); commit(() => { E.doc.texts = E.doc.texts.filter((t) => t.kind !== "caption"); }); };
    $("#cap-reset", s).onclick = async () => {
      close(); await save();
      try { E.hist.push(JSON.stringify(E.doc)); E.doc = await api(`/api/projects/${E.pid}/edits/${E.eid}/timeline/reset`, { method: "POST" }); E.version++; drawAll(); toast("Re-created. Things you added or edited were kept."); }
      catch (e) { toast(e.message, "err"); }
    };
  });
}
function settingsSheet() {
  const o = E.doc.options;
  const S = A.SETTINGS.filter((s) => ["aspect", "color_match", "face_follow", "loudness_lufs"].includes(s.key));
  sheet(`<h2>Settings</h2>${S.map((st) => st.type === "seg"
    ? `<label class="field">${st.label}</label><div class="seg" data-key="${st.key}">${st.options.map(([v, l]) => `<button data-val='${JSON.stringify(v)}' aria-pressed="${JSON.stringify(v) === JSON.stringify(o[st.key] ?? null)}">${l}</button>`).join("")}</div>`
    : `<div class="toggle"><span>${st.label}</span><label class="switch"><input type="checkbox" data-key="${st.key}" ${o[st.key] ? "checked" : ""}><span></span></label></div>`).join("")}
    <p class="small muted">Shape changes take effect in the preview after saving.</p>`, (s) => {
    $$(".seg", s).forEach((g) => g.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; $$("button", g).forEach((x) => x.setAttribute("aria-pressed", x === b)); commit(() => { E.doc.options[g.dataset.key] = JSON.parse(b.dataset.val); }); save().then(layout); });
    $$("input[type=checkbox]", s).forEach((c) => c.onchange = () => commit(() => { E.doc.options[c.dataset.key] = c.checked; }));
  });
}
async function openRunnerUps() {
  let rv;
  try { rv = await api(`/api/projects/${E.pid}/edits/${E.eid}/review`); } catch (e) { toast(e.message, "err"); return; }
  const used = new Set(E.doc.clips.map((c) => `${c.hash}:${c.start}`));
  const ru = (rv.runner_ups || []).concat(rv.removed || []).filter((c) => !used.has(`${c.hash}:${c.start}`));
  sheet(`<h2>Add a moment</h2>${ru.map((c, i) => `<div class="item"><img src="${c.thumb}" style="width:96px;border-radius:8px" alt="">
      <div class="grow small">${fmt(c.start)}–${fmt(c.end)} · ${(c.end - c.start).toFixed(1)}s<div class="muted">${esc((c.reason || "").slice(0, 90))}</div></div>
      <button class="btn small-btn primary" data-i="${i}">Add</button></div>`).join("") || '<p class="muted">No more moments.</p>'}`, (s, close) => {
    $$("[data-i]", s).forEach((b) => b.onclick = () => {
      const c = ru[+b.dataset.i]; close();
      const at = clipAt(E.t), idx = at ? E.doc.clips.indexOf(at.clip) + 1 : E.doc.clips.length;
      commit(() => { E.doc.clips.splice(idx, 0, { id: uid("c"), hash: c.hash, start: c.start, end: c.end, proxy: c.proxy, thumb: `/api/projects/${E.pid}/thumb?hash=${c.hash}&t=`, text: c.text || "", reason: c.reason || "" }); });
    });
  });
}
async function exportNow() {
  stop(); await save();
  try {
    await api(`/api/projects/${E.pid}/edits/${E.eid}/render`, { method: "POST" });
    toast("Exporting on your computer — you can lock your phone.");
    document.body.classList.remove("ed-dark"); A.go(`#/p/${E.pid}`);
  } catch (e) { toast(e.message, "err"); }
}
