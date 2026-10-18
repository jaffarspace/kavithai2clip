"""Local creative studio: storyboard, uploads, and background MP4 rendering."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import edge_tts
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator, model_validator

from app.storyboard import THEMES, generate_storyboard

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KAVITHAI_DATA_DIR", str(ROOT / ".data"))).resolve()
ASSETS = DATA / "assets"
JOBS_DIR = DATA / "jobs"
MAX_UPLOAD = 25 * 1024 * 1024
ID_RE = re.compile(r"^[a-f0-9]{32}$")
VOICE_IDS = {"female": "ta-IN-PallaviNeural", "male": "ta-IN-ValluvarNeural"}
THEME_IDS = {theme["id"] for theme in THEMES}
LOG = logging.getLogger(__name__)
jobs: dict[str, dict] = {}
jobs_lock = threading.RLock()
_voice_status: tuple[float, bool] = (0, False)


@asynccontextmanager
async def lifespan(_app):
    ASSETS.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kavithai-render")
    yield
    _app.state.executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Kavithai to Clip", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


class PoemRequest(BaseModel):
    poem: str = Field(min_length=1, max_length=4000)
    title: str = Field(default="", max_length=120)
    author: str = Field(default="", max_length=80)
    style: Literal["cinematic", "dreamy", "minimal"] = "cinematic"
    mood: Literal["reflective", "romantic", "hopeful", "melancholic"] = "reflective"

    @field_validator("poem")
    @classmethod
    def nonempty_poem(cls, value):
        if not value.strip():
            raise ValueError("Please enter your poem.")
        return value


class Scene(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    theme: str = "abstract"
    duration: float = Field(default=5, ge=3, le=10)
    image_id: str | None = None

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value):
        if not value.strip():
            raise ValueError("Every scene needs a caption.")
        return value

    @field_validator("theme")
    @classmethod
    def valid_theme(cls, value):
        if value not in THEME_IDS:
            raise ValueError("Choose an available scene theme.")
        return value

    @field_validator("image_id")
    @classmethod
    def valid_image(cls, value):
        if value and not ID_RE.fullmatch(value):
            raise ValueError("Invalid image reference.")
        return value or None


class RenderRequest(BaseModel):
    title: str = Field(default="என் கவிதை", max_length=120)
    author: str = Field(default="", max_length=80)
    style: Literal["cinematic", "dreamy", "minimal"] = "cinematic"
    mood: Literal["reflective", "romantic", "hopeful", "melancholic"] = "reflective"
    scenes: list[Scene] = Field(min_length=1, max_length=10)
    music: bool = True
    narration: Literal["none", "female", "male", "upload"] = "none"
    narration_asset_id: str | None = None
    quality: Literal["draft", "hd"] = "hd"

    @model_validator(mode="after")
    def validate_total(self):
        if sum(len(scene.text) for scene in self.scenes) > 4000:
            raise ValueError("Keep your poem within 4,000 characters.")
        if self.narration == "upload" and not self.narration_asset_id:
            raise ValueError("Upload a narration recording first.")
        if self.narration_asset_id and not ID_RE.fullmatch(self.narration_asset_id):
            raise ValueError("Invalid narration reference.")
        return self


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "ffmpeg": bool(shutil.which("ffmpeg"))}


async def online_voice_available():
    global _voice_status
    if time.monotonic() - _voice_status[0] < 300:
        return _voice_status[1]
    try:
        voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=6)
        names = {voice["ShortName"] for voice in voices}
        available = all(voice in names for voice in VOICE_IDS.values())
    except Exception:
        LOG.debug("Online Tamil voice lookup is unavailable", exc_info=True)
        available = False
    _voice_status = (time.monotonic(), available)
    return available


@app.get("/api/capabilities")
async def capabilities():
    online = await online_voice_available()
    return {
        "themes": THEMES,
        "online_voice_available": online,
        "ai_storyboard_available": False,
        "online_voice_note": (
            "Online narration sends the scene text to Microsoft's voice service."
            if online
            else "Online Tamil voices are unavailable here. Upload a narration recording, "
            "or export with captions and music."
        ),
        "max_upload_mb": 25,
    }


@app.post("/api/storyboard")
def storyboard(request: PoemRequest):
    try:
        return generate_storyboard(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def probe_audio(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_type:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0))
    if not data.get("streams") or not 0 < duration <= 120:
        raise ValueError("Use a valid audio recording up to two minutes long.")
    return duration


@app.post("/api/assets")
async def upload_asset(file: Annotated[UploadFile, File()]):
    asset_id = uuid.uuid4().hex
    temp = ASSETS / f"{asset_id}.upload"
    try:
        size = 0
        with temp.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Please use a file smaller than 25 MB.")
                stream.write(chunk)
        if not size:
            raise HTTPException(400, "The uploaded file is empty.")
        content_type = file.content_type or ""
        if content_type.startswith("image/"):
            try:
                with Image.open(temp) as image:
                    if image.width * image.height > 30_000_000:
                        raise ValueError("Please use an image under 30 megapixels.")
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.thumbnail((2400, 3200))
                    image.save(ASSETS / f"{asset_id}.png", "PNG")
                kind = "image"
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
                raise HTTPException(400, "Use a valid JPG, PNG, or WebP photo under 30 megapixels.") from exc
        elif content_type.startswith("audio/") or Path(file.filename or "").suffix.lower() in {
            ".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".webm",
        }:
            try:
                await asyncio.to_thread(probe_audio, temp)
            except (ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                raise HTTPException(400, "Use a valid audio recording up to two minutes long.") from exc
            temp.rename(ASSETS / f"{asset_id}.audio")
            kind = "audio"
        else:
            raise HTTPException(400, "Upload a photo (JPG, PNG, WebP) or an audio recording.")
        return {"id": asset_id, "kind": kind, "name": Path(file.filename or "upload").name,
                "url": f"/api/assets/{asset_id}"}
    finally:
        temp.unlink(missing_ok=True)
        await file.close()


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    if not ID_RE.fullmatch(asset_id):
        raise HTTPException(404, "Asset not found.")
    for suffix, media_type in ((".png", "image/png"), (".audio", "application/octet-stream")):
        path = ASSETS / f"{asset_id}{suffix}"
        if path.is_file():
            return FileResponse(path, media_type=media_type)
    raise HTTPException(404, "Asset not found.")


def update_job(job_id, **updates):
    with jobs_lock:
        jobs[job_id].update(updates)


async def synthesize_narration(text, voice, path):
    communicate = edge_tts.Communicate(text, VOICE_IDS[voice], rate="-12%")
    await asyncio.wait_for(communicate.save(str(path)), timeout=60)


def run_render(job_id, request):
    output_dir = JOBS_DIR / job_id
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        update_job(job_id, status="rendering", progress=2, stage="Preparing your poem")
        narration_path = None
        if request["narration"] == "upload":
            narration_path = ASSETS / f"{request['narration_asset_id']}.audio"
        elif request["narration"] in VOICE_IDS:
            update_job(job_id, progress=3, stage="Recording Tamil narration")
            narration_path = output_dir / "narration.mp3"
            text = "\n\n".join(scene["text"] for scene in request["scenes"])
            try:
                asyncio.run(synthesize_narration(text, request["narration"], narration_path))
            except Exception as exc:
                raise RuntimeError(
                    "The online Tamil voice service could not be reached. "
                    "Upload your own narration or choose captions and music, then try again."
                ) from exc
        from app.rendering import render_video

        def progress(value, stage):
            update_job(job_id, progress=min(99, max(5, int(value))), stage=stage)

        result = render_video(request, output_dir, ASSETS, progress, narration_path)
        update_job(
            job_id, status="complete", progress=100, stage="Your clip is ready",
            video_url=f"/api/jobs/{job_id}/video",
            poster_url=f"/api/jobs/{job_id}/poster", duration=result["duration"],
        )
    except Exception as exc:
        LOG.exception("Render failed for %s", job_id)
        detail = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Video rendering failed. Please try a shorter poem or draft quality."
        update_job(job_id, status="failed", error=detail[:500], stage="Export needs attention")


@app.post("/api/render", status_code=202)
def render(request: RenderRequest):
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "FFmpeg is required for video export.")
    for scene in request.scenes:
        if scene.image_id and not (ASSETS / f"{scene.image_id}.png").is_file():
            raise HTTPException(400, "A scene photo is missing. Please upload it again.")
    if request.narration == "upload" and not (ASSETS / f"{request.narration_asset_id}.audio").is_file():
        raise HTTPException(400, "The narration recording is missing. Please upload it again.")
    with jobs_lock:
        if sum(job["status"] in {"queued", "rendering"} for job in jobs.values()) >= 3:
            raise HTTPException(429, "The render queue is full. Wait for an export to finish.")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0,
                        "stage": "Waiting for the renderer"}
    app.state.executor.submit(run_render, job_id, request.model_dump())
    return {"job_id": job_id, "status": "queued", "progress": 0}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(404, "Export not found. Start a new export after a server restart.")
        return dict(jobs[job_id])


@app.get("/api/jobs/{job_id}/{kind}")
def get_output(job_id: str, kind: str):
    if not ID_RE.fullmatch(job_id) or kind not in {"video", "poster"}:
        raise HTTPException(404, "Output not found.")
    path = JOBS_DIR / job_id / ("video.mp4" if kind == "video" else "poster.jpg")
    if not path.is_file():
        raise HTTPException(404, "The export is not ready yet.")
    if kind == "video":
        return FileResponse(path, media_type="video/mp4", filename="kavithai-clip.mp4")
    return FileResponse(path, media_type="image/jpeg")
