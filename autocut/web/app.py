"""FastAPI backend. Every route except login requires a valid session."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import auth


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


def create_app() -> FastAPI:
    app = FastAPI(title="autocut", docs_url=None, redoc_url=None, openapi_url=None)

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
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=secure, path="/")
        return {"ok": True, "token": token}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me", dependencies=[Depends(require_user)])
    def me():
        return {"ok": True}

    @app.post("/api/password", dependencies=[Depends(require_user)])
    def change_password(body: PasswordBody, response: Response, request: Request):
        if not auth.check_password(body.current):
            raise HTTPException(401, "Current password is wrong.")
        try:
            auth.set_password(body.new)
        except ValueError as e:
            raise HTTPException(400, str(e))
        token = auth.make_token()  # secret rotated: re-issue for this device
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https", path="/")
        return {"ok": True}

    # ------------------------------------------------------------------ instructions
    @app.post("/api/parse", dependencies=[Depends(require_user)])
    def parse_instruction(body: ParseBody):
        from ..intent import parse

        spec = parse(body.text)
        return {"spec": spec.model_dump(), "summary": spec.summary()}

    @app.post("/api/voice", dependencies=[Depends(require_user)])
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

    return app
