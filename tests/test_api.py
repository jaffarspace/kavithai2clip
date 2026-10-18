import io
import shutil
import wave
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Keep uploads, jobs, and background exports isolated from the user's studio."""
    monkeypatch.setattr(main, "ASSETS", tmp_path / "assets")
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(main, "jobs", {})

    async def offline_voice():
        return False

    monkeypatch.setattr(main, "online_voice_available", offline_voice)
    with TestClient(main.app) as session:
        # Queue inspection is deterministic; media rendering has its own integration check.
        executor = main.app.state.executor
        monkeypatch.setattr(executor, "submit", Mock())
        yield session


def render_request(**changes):
    return {
        "scenes": [{"text": "நிலவின் ஒளியில் என் கவிதை", "theme": "moon", "duration": 3}],
        "narration": "none",
        "quality": "draft",
        **changes,
    }


def png_bytes():
    output = io.BytesIO()
    Image.new("RGBA", (40, 60), (20, 40, 80, 128)).save(output, format="PNG")
    return output.getvalue()


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    return output.getvalue()


def test_storyboard_keeps_tamil_text_and_is_directly_editable(client):
    poem = "நிலவின் ஒளியில் என் நினைவு\nகடலில் அலைகள் பேசுகின்றன"
    response = client.post("/api/storyboard", json={"poem": poem, "title": "என் கவிதை"})
    assert response.status_code == 200
    board = response.json()
    assert [scene["theme"] for scene in board["scenes"]] == ["moon", "ocean"]
    assert "\n".join(scene["text"] for scene in board["scenes"]) == poem
    assert "offline" in board["interpretation"]
    export = client.post("/api/render", json=render_request(scenes=board["scenes"]))
    assert export.status_code == 202


@pytest.mark.parametrize(
    "body",
    [
        {"poem": " \n "},
        {"poem": "அ" * 4001},
        {"poem": "கவிதை", "mood": "unknown"},
    ],
)
def test_invalid_storyboard_inputs_return_validation_errors(client, body):
    assert client.post("/api/storyboard", json=body).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"scenes": []},
        {"scenes": [{"text": "கவிதை", "theme": "not-a-theme"}]},
        {"scenes": [{"text": "கவிதை", "duration": 2}]},
        {"scenes": [{"text": "கவிதை", "duration": 11}]},
        {"scenes": [{"text": " "}]},
        {"scenes": [{"text": "கவிதை", "image_id": "../private"}]},
        {"scenes": [{"text": "அ" * 401}]},
        {"scenes": [{"text": "கவிதை"}] * 11},
        {"narration": "upload"},
        {"narration": "celebrity-voice"},
    ],
)
def test_invalid_edits_and_narration_choices_never_enter_queue(client, changes):
    response = client.post("/api/render", json=render_request(**changes))
    assert response.status_code == 422
    assert not main.jobs
    main.app.state.executor.submit.assert_not_called()


def test_photo_upload_decodes_normalizes_and_can_be_selected_for_a_scene(client):
    response = client.post(
        "/api/assets", files={"file": ("my-photo.png", png_bytes(), "image/png")}
    )
    assert response.status_code == 200
    asset = response.json()
    assert asset["kind"] == "image"
    stored = client.get(asset["url"])
    assert stored.status_code == 200
    assert stored.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(stored.content)) as image:
        assert image.mode == "RGB"
        assert image.size == (40, 60)
    request = render_request()
    request["scenes"][0]["image_id"] = asset["id"]
    assert client.post("/api/render", json=request).status_code == 202
    assert not list(main.ASSETS.glob("*.upload"))


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("fake.png", b"this is not a photo", "image/png"),
        ("fake.wav", b"this is not audio", "audio/wav"),
        ("fake.wav", png_bytes(), "audio/wav"),
        ("document.html", b"<script>alert('x')</script>", "text/html"),
    ],
)
def test_upload_rejects_forged_or_unsupported_media_without_leaving_files(
    client,
    filename,
    content,
    content_type,
):
    response = client.post("/api/assets", files={"file": (filename, content, content_type)})
    assert response.status_code == 400
    assert not list(main.ASSETS.iterdir())


def test_empty_and_oversized_uploads_are_rejected_and_cleaned_up(client, monkeypatch):
    empty = client.post("/api/assets", files={"file": ("empty.png", b"", "image/png")})
    assert empty.status_code == 400
    monkeypatch.setattr(main, "MAX_UPLOAD", 32)
    oversized = client.post("/api/assets", files={"file": ("large.png", png_bytes(), "image/png")})
    assert oversized.status_code == 413
    assert not list(main.ASSETS.iterdir())


def test_real_audio_upload_is_probed_and_selected_as_uploaded_narration(client):
    assert shutil.which("ffprobe"), "FFprobe is required to validate uploaded narration."
    response = client.post(
        "/api/assets", files={"file": ("my-voice.wav", wav_bytes(), "audio/wav")}
    )
    assert response.status_code == 200
    asset = response.json()
    assert asset["kind"] == "audio"
    assert client.get(asset["url"]).content == wav_bytes()
    request = render_request(narration="upload", narration_asset_id=asset["id"])
    assert client.post("/api/render", json=request).status_code == 202
    queued_request = main.app.state.executor.submit.call_args.args[2]
    assert queued_request["narration"] == "upload"
    assert queued_request["narration_asset_id"] == asset["id"]


def test_missing_selected_assets_and_unavailable_ffmpeg_fail_before_queuing(client, monkeypatch):
    request = render_request()
    request["scenes"][0]["image_id"] = "a" * 32
    assert client.post("/api/render", json=request).status_code == 400
    request = render_request(narration="upload", narration_asset_id="b" * 32)
    assert client.post("/api/render", json=request).status_code == 400
    monkeypatch.setattr(main.shutil, "which", lambda _: None)
    assert client.post("/api/render", json=render_request()).status_code == 503
    assert not main.jobs


def test_render_queue_status_and_output_routes(client):
    for _ in range(3):
        response = client.post("/api/render", json=render_request())
        assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert client.post("/api/render", json=render_request()).status_code == 429
    assert client.get(f"/api/jobs/{job_id}/video").status_code == 404
    assert client.get("/api/jobs/unknown").status_code == 404
    for path in ["/api/assets/invalid", "/api/jobs/invalid/video", f"/api/jobs/{job_id}/private"]:
        assert client.get(path).status_code == 404
    output = main.JOBS_DIR / job_id
    output.mkdir()
    # These fixtures check HTTP delivery only; they do not claim to be rendered media.
    (output / "video.mp4").write_bytes(b"test download fixture")
    (output / "poster.jpg").write_bytes(b"test poster fixture")
    video = client.get(f"/api/jobs/{job_id}/video")
    assert video.status_code == 200
    assert video.headers["content-type"] == "video/mp4"
    assert 'filename="kavithai-clip.mp4"' in video.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job_id}/poster").headers["content-type"] == "image/jpeg"


def test_background_render_failure_has_actionable_job_error(client, monkeypatch):
    from app import rendering

    def fail_render(*_args):
        raise RuntimeError("The recording is too long for these scenes.")

    monkeypatch.setattr(rendering, "render_video", fail_render)
    response = client.post("/api/render", json=render_request())
    job_id = response.json()["job_id"]
    request = main.RenderRequest(**render_request()).model_dump()
    main.run_render(job_id, request)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"] == "The recording is too long for these scenes."
    assert "video_url" not in job


def test_capabilities_explain_offline_storyboards_and_voice_fallback(client):
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    capabilities = response.json()
    assert capabilities["online_voice_available"] is False
    assert capabilities["ai_storyboard_available"] is False
    assert "Upload a narration recording" in capabilities["online_voice_note"]
    assert {theme["id"] for theme in capabilities["themes"]} >= {"moon", "ocean", "abstract"}
