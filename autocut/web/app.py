"""FastAPI backend for the phone app. Every /api route except login requires a valid session;
static files are only the app shell (no user data)."""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from . import jobs as J
from . import projects as P
from . import uploads as U

STATIC = Path(__file__).parent / "static"


def require_user(request: Request) -> None:
    token = request.cookies.get(auth.COOKIE)
    h = request.headers.get("authorization", "")
    if not token and h.lower().startswith("bearer "):
        token = h[7:]
    if not auth.verify_token(token):
        raise HTTPException(status_code=401, detail="Please log in.")


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    current: str
    new: str


class ParseBody(BaseModel):
    text: str


class ProjectBody(BaseModel):
    name: str = "Untitled project"


class ProjectPatch(BaseModel):
    name: str | None = None
    instruction: str | None = None
    options: dict | None = None
    style: str | None = None
    context: str | None = None
    watch_folder: str | None = None      # "" = stop watching
    auto_edit: bool | None = None


class ImportBody(BaseModel):
    url: str
    kind: str = "footage"


class UploadStart(BaseModel):
    filename: str
    size: int
    kind: str = "footage"
    fingerprint: str


class EditBody(BaseModel):
    instruction: str


class ClipOp(BaseModel):
    op: str
    clip_id: str | None = None
    runner_id: str | None = None
    dir: int | None = None
    edge: str | None = None
    delta: float | None = None
    position: int | None = None


class CaptionBody(BaseModel):
    hash: str
    start: float
    end: float
    text: str | None = None
    hint: str | None = None


class ContextBody(BaseModel):
    text: str


class SpeakerBody(BaseModel):
    hash: str
    start: float
    end: float
    me: bool


class OptionsBody(BaseModel):
    options: dict


def _set_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure, path="/")


def _project_or_404(pid: str) -> dict:
    try:
        return P.load(pid)
    except KeyError:
        raise HTTPException(404, "Project not found.")


def create_app() -> FastAPI:
    app = FastAPI(title="autocut", docs_url=None, redoc_url=None, openapi_url=None)
    user = [Depends(require_user)]

    @app.middleware("http")
    async def no_cache_api(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(U.UploadError)
    async def upload_error(_: Request, ex: U.UploadError):
        body = {"detail": str(ex)}
        if ex.offset is not None:
            body["offset"] = ex.offset
        return JSONResponse(body, status_code=ex.status)

    # ------------------------------------------------------------------ shell / PWA
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ auth
    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        ip = request.client.host if request.client else "?"
        if auth.limiter.blocked(ip):
            raise HTTPException(429, "Too many attempts. Wait 10 minutes and try again.")
        if not auth.check_password(body.password):
            auth.limiter.fail(ip)
            raise HTTPException(401, "Wrong password.")
        auth.limiter.reset(ip)
        token = auth.make_token()
        _set_cookie(response, request, token)
        return {"ok": True, "token": token}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me", dependencies=user)
    def me():
        from ..hardware import detect

        hw = detect()
        return {"ok": True, "busy": J.busy(), "hardware": hw.accelerator}

    @app.post("/api/password", dependencies=user)
    def change_password(body: PasswordBody, response: Response, request: Request):
        if not auth.check_password(body.current):
            raise HTTPException(401, "Current password is wrong.")
        try:
            auth.set_password(body.new)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _set_cookie(response, request, auth.make_token())  # secret rotated: re-issue
        return {"ok": True}

    # ------------------------------------------------------------------ instructions
    @app.post("/api/parse", dependencies=user)
    def parse_instruction(body: ParseBody):
        from ..intent import parse

        spec = parse(body.text)
        return {"spec": spec.model_dump(), "summary": spec.summary()}

    @app.post("/api/voice", dependencies=user)
    async def voice(audio: UploadFile = File(...)):
        """Upload a voice recording → local transcription → EditSpec."""
        from ..editspec import EditSpec
        from ..voice import voice_to_spec

        suffix = Path(audio.filename or "voice.webm").suffix or ".webm"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"voice{suffix}"
            with open(p, "wb") as f:
                shutil.copyfileobj(audio.file, f)
            if p.stat().st_size > 30 * 1024 * 1024:
                raise HTTPException(413, "Recording too large.")
            try:
                result = await run_in_threadpool(voice_to_spec, p)
            except ValueError as e:
                raise HTTPException(400, str(e))
        result["summary"] = EditSpec(**result["spec"]).summary()
        return result

    # ------------------------------------------------------------------ styles
    @app.get("/api/styles", dependencies=user)
    def styles():
        from ..style import explain, list_profiles, load_profile

        out = []
        for n in list_profiles():
            try:
                prof = load_profile(n)
                out.append({"name": n, "references": prof.references, "explanation": explain(prof)})
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------ projects
    @app.get("/api/projects", dependencies=user)
    def list_projects():
        return P.list_all()

    @app.post("/api/projects", dependencies=user)
    def create_project(body: ProjectBody):
        return P.create(body.name)

    @app.get("/api/projects/{pid}", dependencies=user)
    def get_project(pid: str):
        p = _project_or_404(pid)
        p["jobs"] = [j for j in J.for_project(pid) if j["status"] in ("queued", "running")
                     or (j["finished"] and time.time() - j["finished"] < 120)]
        return p

    @app.patch("/api/projects/{pid}", dependencies=user)
    def patch_project(pid: str, body: ProjectPatch):
        from ..options import RenderOptions

        _project_or_404(pid)

        def fn(p):
            if body.name is not None:
                p["name"] = body.name.strip()[:80] or p["name"]
            if body.instruction is not None:
                p["instruction"] = body.instruction[:2000]
            if body.context is not None:
                p["context"] = body.context[:4000]
            if body.style is not None:
                p["style"] = body.style or None
            if body.auto_edit is not None:
                p["auto_edit"] = body.auto_edit
            if body.watch_folder is not None:
                wf = body.watch_folder.strip()
                if wf:
                    path = Path(wf).expanduser()
                    if not path.is_absolute():
                        raise HTTPException(400, "Please give the full folder path.")
                    try:
                        path.mkdir(parents=True, exist_ok=True)
                    except OSError as e:
                        raise HTTPException(400, f"Can't use that folder: {e}")
                    p["watch_folder"] = str(path)
                else:
                    p["watch_folder"] = None
            if body.options is not None:
                try:
                    p["options"] = RenderOptions(**{**p.get("options", {}), **body.options}).model_dump()
                except Exception as e:
                    raise HTTPException(400, f"Bad setting: {e}")
        return P.update(pid, fn)

    @app.delete("/api/projects/{pid}", dependencies=user)
    def delete_project(pid: str):
        _project_or_404(pid)
        P.delete(pid)
        return {"ok": True}

    @app.delete("/api/projects/{pid}/items/{item_id}", dependencies=user)
    def delete_item(pid: str, item_id: str):
        p = _project_or_404(pid)
        found = P.find_item(p, item_id)
        if not found:
            raise HTTPException(404, "Not found.")
        kind, it = found

        def fn(q):
            q[kind] = [x for x in q[kind] if x["id"] != item_id]
        P.update(pid, fn)
        # only delete files autocut owns (uploads); never touch your Google Drive / original files
        f = Path(it["path"]).resolve()
        if P.project_dir(pid).resolve() in f.parents:
            f.unlink(missing_ok=True)
        if kind == "refs":
            if P.load(pid)["refs"]:
                J.queue_style(pid)
            else:
                P.update(pid, lambda q: q.update(style=None, style_explained=None))
        return {"ok": True}

    @app.post("/api/projects/{pid}/items/{item_id}/retry", dependencies=user)
    def retry_item(pid: str, item_id: str):
        p = _project_or_404(pid)
        if not P.find_item(p, item_id):
            raise HTTPException(404, "Not found.")
        return J.queue_analyze(pid, item_id)

    # ------------------------------------------------------------------ Google Drive
    @app.get("/api/drive", dependencies=user)
    def drive(pid: str | None = None):
        from .watch import drive_roots, suggested_folder

        name = _project_or_404(pid)["name"] if pid else "project"
        return {"roots": drive_roots(), "suggestion": suggested_folder(name)}

    @app.post("/api/projects/{pid}/watch/scan", dependencies=user)
    def scan_now(pid: str):
        from .watch import scan_project

        _project_or_404(pid)
        return {"added": scan_project(pid)}

    @app.post("/api/projects/{pid}/import", dependencies=user)
    async def import_link(pid: str, body: ImportBody):
        from .watch import import_link as _imp

        _project_or_404(pid)
        if body.kind not in ("footage", "refs"):
            raise HTTPException(400, "kind must be footage or refs")
        try:
            ids = await run_in_threadpool(_imp, pid, body.url.strip(), body.kind)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Couldn't download from Google Drive: {e}")
        return {"added": ids}

    # ------------------------------------------------------------------ uploads
    @app.post("/api/projects/{pid}/uploads", dependencies=user)
    def start_upload(pid: str, body: UploadStart):
        _project_or_404(pid)
        return U.start(pid, body.filename, body.size, body.kind, body.fingerprint)

    @app.get("/api/uploads/{uid}", dependencies=user)
    def upload_status(uid: str):
        return U.status(uid)

    @app.put("/api/uploads/{uid}", dependencies=user)
    async def upload_chunk(uid: str, offset: int, request: Request):
        return await U.append(uid, offset, request.stream())

    # ------------------------------------------------------------------ edits
    @app.post("/api/projects/{pid}/edits", dependencies=user)
    def make_edit(pid: str, body: EditBody):
        p = _project_or_404(pid)
        if not any(f.get("status") == "ready" for f in p["footage"]):
            raise HTTPException(400, "Add some footage first and wait until it says “ready”.")
        e = {"id": uuid.uuid4().hex[:10], "created": time.time(), "instruction": body.instruction,
             "status": "queued"}

        def fn(q):
            q["edits"].append(e)
            q["instruction"] = body.instruction
        P.update(pid, fn)
        J.queue_edit(pid, e["id"])
        return e

    @app.delete("/api/projects/{pid}/edits/{eid}", dependencies=user)
    def delete_edit(pid: str, eid: str):
        _project_or_404(pid)
        P.update(pid, lambda q: q.update(edits=[x for x in q["edits"] if x["id"] != eid]))
        shutil.rmtree(P.project_dir(pid) / "edits" / eid, ignore_errors=True)
        return {"ok": True}

    @app.get("/api/projects/{pid}/edits/{eid}/video", dependencies=user)
    def edit_video(pid: str, eid: str, quality: str = "preview", download: int = 0):
        p = _project_or_404(pid)
        e = P.find_edit(p, eid)
        if not e or e.get("status") != "done":
            raise HTTPException(404, "Not ready yet.")
        path = Path(e["preview"] if quality == "preview" and e.get("preview") else e["video"])
        if not path.exists():
            raise HTTPException(404, "File missing.")
        name = f"{p['name']}_{eid}.mp4".replace(" ", "_")
        return FileResponse(path, media_type="video/mp4",
                            filename=name if download else None,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/api/projects/{pid}/edits/{eid}/export", dependencies=user)
    def export_edit(pid: str, eid: str, format: str = "zip"):
        import json as _json
        import re as _re

        from ..export import FORMATS, export_zip, render_format

        p = _project_or_404(pid)
        e = P.find_edit(p, eid)
        if not e or not e.get("selection"):
            raise HTTPException(404, "Edit not found.")
        sel = _json.loads(Path(e["selection"]).read_text())
        name = _re.sub(r"[^\w\-]+", "_", f"{p['name']}_{eid}").strip("_")
        title = f"{p['name']} — {e['instruction']}"
        if format == "zip":
            data, ext, mt = export_zip(sel, title, name), ".zip", "application/zip"
        elif format in FORMATS:
            data = render_format(sel, format, title).encode("utf-8")
            ext = FORMATS[format][0]
            mt = {"fcpxml": "application/xml", "premiere": "application/xml", "edl": "text/plain",
                  "srt": "application/x-subrip"}[format]
        else:
            raise HTTPException(400, "Unknown format.")
        return Response(data, media_type=mt,
                        headers={"Content-Disposition": f'attachment; filename="{name}{ext}"'})

    @app.post("/api/projects/{pid}/edits/{eid}/render", dependencies=user)
    def rerender(pid: str, eid: str):
        p = _project_or_404(pid)
        e = P.find_edit(p, eid)
        if not e or not e.get("selection"):
            raise HTTPException(404, "Not found.")
        from .. import review as R

        try:
            R.log_kept(R.load(Path(e["selection"])))
        except Exception:
            pass
        J._set_edit(pid, eid, status="queued", changed=False)  # noqa: SLF001
        return J.queue_render(pid, eid)

    # ------------------------------------------------------------------ review (phase 14)
    def _footage_hash(p: dict, h: str) -> dict:
        from .. import db

        if not any(f.get("hash") == h for f in p["footage"]):
            raise HTTPException(404, "Not part of this project.")
        return db.get_media(h)

    def _edit_sel(pid: str, eid: str):
        from .. import review as R

        p = _project_or_404(pid)
        e = P.find_edit(p, eid)
        if not e or not e.get("selection"):
            raise HTTPException(404, "Edit not found.")
        return p, e, R.load(Path(e["selection"]))

    def _with_urls(pid: str, sel: dict) -> dict:
        for lst in ("clips", "runner_ups", "removed"):
            for c in sel.get(lst, []):
                mid = (c["start"] + c["end"]) / 2
                c["thumb"] = f"/api/projects/{pid}/thumb?hash={c['hash']}&t={mid:.2f}"
                c["proxy"] = f"/api/projects/{pid}/proxy/{c['hash']}"
                c.pop("features", None)
        return sel

    @app.get("/api/projects/{pid}/edits/{eid}/review", dependencies=user)
    def review(pid: str, eid: str):
        p, e, sel = _edit_sel(pid, eid)
        sel["edit"] = {k: e.get(k) for k in ("id", "instruction", "status", "summary", "duration",
                                               "rendered", "finished")}
        return _with_urls(pid, sel)

    @app.post("/api/projects/{pid}/edits/{eid}/clips", dependencies=user)
    def clip_op(pid: str, eid: str, body: ClipOp):
        from .. import review as R

        p, e, sel = _edit_sel(pid, eid)
        try:
            R.apply(sel, body.model_dump(exclude_none=True))
        except R.ReviewError as ex:
            raise HTTPException(400, str(ex))
        R.save(Path(e["selection"]), sel)
        J._set_edit(pid, eid, changed=True)  # noqa: SLF001
        return _with_urls(pid, sel)

    @app.patch("/api/projects/{pid}/edits/{eid}/options", dependencies=user)
    def edit_options(pid: str, eid: str, body: OptionsBody):
        from .. import review as R
        from ..options import RenderOptions

        p, e, sel = _edit_sel(pid, eid)
        try:
            sel["options"] = RenderOptions(**{**sel.get("options", {}), **body.options}).model_dump()
        except Exception as ex:
            raise HTTPException(400, f"Bad setting: {ex}")
        R.save(Path(e["selection"]), sel)
        return {"options": sel["options"]}

    @app.get("/api/projects/{pid}/thumb", dependencies=user)
    def thumb(pid: str, hash: str, t: float):
        from ..config import get_settings
        from ..ffmpeg import run_ffmpeg

        p = _project_or_404(pid)
        m = _footage_hash(p, hash)
        d = get_settings().home / "thumbs"
        d.mkdir(exist_ok=True)
        f = d / f"{hash[:16]}_{t:.1f}.jpg"
        if not f.exists():
            run_ffmpeg(["-ss", f"{max(0.0, t):.2f}", "-i", m["proxy_path"], "-frames:v", "1",
                        "-vf", "scale=320:-2", "-q:v", "5", str(f)])
        return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/projects/{pid}/proxy/{h}", dependencies=user)
    def proxy(pid: str, h: str):
        p = _project_or_404(pid)
        m = _footage_hash(p, h)
        return FileResponse(m["proxy_path"], media_type="video/mp4")

    @app.get("/api/projects/{pid}/captions", dependencies=user)
    def captions(pid: str, hash: str, start: float, end: float):
        from ..review import caption_lines

        _footage_hash(_project_or_404(pid), hash)
        return caption_lines(hash, start, end)

    @app.put("/api/projects/{pid}/captions", dependencies=user)
    def set_caption(pid: str, body: CaptionBody):
        from ..corrections import correct_text
        from ..review import caption_lines

        _footage_hash(_project_or_404(pid), body.hash)
        e = correct_text(body.hash, body.start, body.end + 1e-3, body.text or "")
        return {"edit": e, "lines": caption_lines(body.hash, body.start - 5, body.end + 5)}

    @app.post("/api/projects/{pid}/captions/speaker", dependencies=user)
    def set_speaker(pid: str, body: SpeakerBody):
        """Tap "this is me" / "not me" on a caption line (also teaches your voice profile)."""
        from ..speakers import mark

        _footage_hash(_project_or_404(pid), body.hash)
        mark(body.hash, body.start, body.end, body.me)
        return {"ok": True}

    @app.post("/api/projects/{pid}/captions/relisten", dependencies=user)
    async def relisten_caption(pid: str, body: CaptionBody):
        from ..corrections import relisten

        _footage_hash(_project_or_404(pid), body.hash)
        e = await run_in_threadpool(relisten, body.hash, body.start, body.end, body.hint)
        return {"edit": e}

    @app.post("/api/projects/{pid}/context", dependencies=user)
    async def give_context(pid: str, body: ContextBody):
        from ..corrections import apply_context

        p = _project_or_404(pid)
        P.update(pid, lambda q: q.update(context=body.text[:4000]))
        changes = []
        for f in p["footage"]:
            if f.get("hash"):
                ch = await run_in_threadpool(apply_context, f["hash"], body.text)
                changes += [{**c, "file": f["name"]} for c in ch]
        return {"changes": changes}

    return app
