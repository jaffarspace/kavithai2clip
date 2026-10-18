import io
import json
import shutil
import subprocess
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


def wav_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * (8000 * seconds))
    return output.getvalue()


def moving_video(path, seconds=0.7):
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=120x160:rate=8:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return path.read_bytes()


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
        {"scenes": [{"text": "கவிதை", "duration": 31}]},
        {"scenes": [{"text": " "}]},
        {"scenes": [{"text": "கவிதை", "image_id": "../private"}]},
        {"scenes": [{"text": "கவிதை", "video_id": "../private"}]},
        {"scenes": [{"text": "கவிதை", "image_id": "a" * 32, "video_id": "b" * 32}]},
        {"scenes": [{"text": "அ" * 401}]},
        {"scenes": [{"text": "கவிதை"}] * 11},
        {"narration": "upload"},
        {"narration": "celebrity-voice"},
        {"narration_style": "celebrity-reading"},
        {"transition_seconds": -0.1},
        {"transition_seconds": 1.1},
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
        ("fake.mp4", b"this is not a video", "video/mp4"),
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
    request = render_request()
    request["scenes"][0]["video_id"] = "c" * 32
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
    assert capabilities["ai_video_available"] is False
    assert capabilities["max_video_seconds"] == 30


def test_real_moving_video_upload_normalizes_media_provides_poster_and_can_render(client, tmp_path):
    source = moving_video(tmp_path / "source.mp4")
    response = client.post("/api/assets", files={"file": ("moving-scene.mp4", source, "video/mp4")})
    assert response.status_code == 200
    asset = response.json()
    assert asset["kind"] == "video"
    stored = client.get(asset["url"])
    assert stored.status_code == 200
    assert stored.headers["content-type"] == "video/mp4"
    normalized = tmp_path / "normalized.mp4"
    normalized.write_bytes(stored.content)
    metadata = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,width,height:format=duration",
                "-of",
                "json",
                str(normalized),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    )
    assert metadata["streams"][0]["codec_name"] == "h264"
    assert max(metadata["streams"][0]["width"], metadata["streams"][0]["height"]) <= 1280
    assert 0.5 < float(metadata["format"]["duration"]) < 1
    frames = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(normalized),
            "-vf",
            "scale=16:16",
            "-frames:v",
            "12",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout
    frame_size = 16 * 16 * 3
    assert len(frames) >= frame_size * 2
    assert frames[:frame_size] != frames[-frame_size:], (
        "Uploaded background must retain real motion."
    )
    poster = client.get(asset["poster_url"])
    assert poster.status_code == 200
    assert poster.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(poster.content)) as thumbnail:
        assert thumbnail.width == 480
    request = render_request()
    request["scenes"][0]["video_id"] = asset["id"]
    request["scenes"][0]["duration"] = 30
    assert client.post("/api/render", json=request).status_code == 202
    assert not list(main.ASSETS.glob("*.upload"))


def test_overlong_real_video_upload_is_rejected_without_partial_outputs(client, tmp_path):
    source = moving_video(tmp_path / "too-long.mp4", seconds=31)
    response = client.post("/api/assets", files={"file": ("too-long.mp4", source, "video/mp4")})
    assert response.status_code == 400
    assert "30 seconds" in response.json()["detail"]
    assert not list(main.ASSETS.iterdir())


def test_narration_recordings_can_exceed_two_minutes_but_stay_within_six_minutes(client):
    recording = wav_bytes(seconds=121)
    response = client.post(
        "/api/assets", files={"file": ("paced-reading.wav", recording, "audio/wav")}
    )
    assert response.status_code == 200
    assert client.get(response.json()["url"]).content == recording
    overlong = client.post(
        "/api/assets", files={"file": ("too-long.wav", wav_bytes(seconds=361), "audio/wav")}
    )
    assert overlong.status_code == 400
    assert "six minutes" in overlong.json()["detail"]
    assert len(list(main.ASSETS.iterdir())) == 1


def test_narration_plan_preserves_poem_order_and_includes_deliberate_silences(client):
    texts = ["வண்ண விழா;\nமௌன விழா.", "மீண்டும்\nவசந்தத்தின் வாசம்…"]
    scenes = [{"text": text, "duration": 12} for text in texts]
    response = client.post(
        "/api/narration-plan", json={"scenes": scenes, "mood": "reflective", "narration": "male"}
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["voice"] == "ta-IN-ValluvarNeural"
    segments = plan["segments"]
    assert "\n".join(segment["text"] for segment in segments) == "\n".join(texts)
    assert segments[0]["pause_before_ms"] > 0
    assert segments[-1]["pause_after_ms"] >= 1000
    assert len({segment["pitch"] for segment in segments}) > 1
    assert len({segment["rate"] for segment in segments}) > 1
    assert not main.jobs
    main.app.state.executor.submit.assert_not_called()


def test_plain_narration_plan_matches_continuous_reading_without_added_poetic_pauses(
    client,
    monkeypatch,
):
    from app import narration

    poetic_plan = Mock(side_effect=AssertionError("Plain direction must not use the poetic score."))
    monkeypatch.setattr(narration, "plan_narration", poetic_plan)
    texts = ["வண்ண விழா;\nமௌன விழா.", "மீண்டும்\n\nவசந்தத்தின் வாசம்…"]
    response = client.post(
        "/api/narration-plan",
        json={
            "scenes": [{"text": text, "duration": 12} for text in texts],
            "narration": "female",
            "narration_style": "plain",
            "mood": "melancholic",
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["voice"] == "ta-IN-PallaviNeural"
    assert "continuous" in plan["direction"].lower()
    assert [segment["text"] for segment in plan["segments"]] == texts
    assert {segment["rate"] for segment in plan["segments"]} == {"-12%"}
    assert {segment["pitch"] for segment in plan["segments"]} == {"+0Hz"}
    assert all(
        segment["pause_before_ms"] == segment["pause_after_ms"] == 0 for segment in plan["segments"]
    )
    poetic_plan.assert_not_called()
    assert not main.jobs


def test_video_plan_is_available_without_credentials_and_never_submits_paid_generation(
    client,
    monkeypatch,
):
    from app import ai_video

    monkeypatch.delenv("KAVITHAI_RUNWAY_API_KEY", raising=False)
    submit = Mock(side_effect=AssertionError("Planning must never submit a paid generation."))
    monkeypatch.setattr(ai_video.RunwayVideoProvider, "submit", submit)
    scenes = [
        {"text": "நிலவின் ஒளியில் என் நினைவு", "theme": "moon", "duration": 12},
        {"text": "கடலில் அலைகள் பேசுகின்றன", "theme": "ocean", "duration": 15},
    ]
    response = client.post("/api/video-plan", json={"scenes": scenes})
    assert response.status_code == 200
    plan = response.json()
    assert plan["generation_started"] is False
    assert plan["configured"] is False
    assert len(plan["scenes"]) == len(scenes)
    assert all(scene["prompt"].strip() for scene in plan["scenes"])
    submit.assert_not_called()
    assert not main.jobs


def test_poetic_narration_passes_measured_scene_timings_and_transition_to_renderer(
    client,
    monkeypatch,
):
    from app import narration, rendering

    seen = {"progress": []}
    update_job = main.update_job

    def track_progress(job_id, **changes):
        if "progress" in changes:
            seen["progress"].append(changes["progress"])
        update_job(job_id, **changes)

    monkeypatch.setattr(main, "update_job", track_progress)

    async def expressive(scenes, voice, output_dir, progress, mood):
        seen.update(voice=voice, mood=mood)
        progress(1, 2)
        progress(2, 2)
        return {
            "narration_path": output_dir / "narration.wav",
            "scene_durations": [8.25, 12.75],
            "plan": [{"text": scene["text"]} for scene in scenes],
        }

    def fake_render(request, output_dir, assets, progress, narration_path):
        seen.update(request=request, narration_path=narration_path)
        for value in [0, 20, 50, 99]:
            progress(value, "Rendering timed scenes")
        return {"duration": 21}

    monkeypatch.setattr(narration, "synthesize_expressive", expressive)
    monkeypatch.setattr(rendering, "render_video", fake_render)
    plain = Mock(side_effect=AssertionError("Poetic reading must use measured expressive speech."))
    monkeypatch.setattr(main, "synthesize_narration", plain)
    request = render_request(
        narration="male",
        mood="melancholic",
        transition_seconds=0.8,
        scenes=[{"text": "மீண்டும்", "duration": 3}, {"text": "வசந்தத்தின் வாசம்…", "duration": 3}],
    )
    response = client.post("/api/render", json=request)
    job_id = response.json()["job_id"]
    main.run_render(job_id, main.RenderRequest(**request).model_dump())
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "complete"
    assert seen["voice"] == "male"
    assert seen["mood"] == "melancholic"
    assert [scene["duration"] for scene in seen["request"]["scenes"]] == [8.25, 12.75]
    assert seen["request"]["transition_seconds"] == 0.8
    assert seen["narration_path"].name == "narration.wav"
    assert seen["progress"] == sorted(seen["progress"])
    assert seen["progress"][-1] == 100
    saved_plan = json.loads((main.JOBS_DIR / job_id / "narration-plan.json").read_text())
    assert [segment["text"] for segment in saved_plan] == [
        scene["text"] for scene in request["scenes"]
    ]
    plain.assert_not_called()


def test_plain_reading_preserves_plain_synthesis_and_requested_scene_timings(client, monkeypatch):
    from app import narration, rendering

    seen = {}

    async def plain(text, voice, path):
        seen.update(text=text, voice=voice, path=path)

    def fake_render(request, output_dir, assets, progress, narration_path):
        seen["durations"] = [scene["duration"] for scene in request["scenes"]]
        return {"duration": 24}

    expressive = Mock(
        side_effect=AssertionError("Plain reading must not synthesize expressive speech.")
    )
    monkeypatch.setattr(narration, "synthesize_expressive", expressive)
    monkeypatch.setattr(main, "synthesize_narration", plain)
    monkeypatch.setattr(rendering, "render_video", fake_render)
    request = render_request(
        narration="female",
        narration_style="plain",
        scenes=[
            {"text": "நிலவின் ஒளியில்", "duration": 11},
            {"text": "என் கவிதை மலர்கிறது.", "duration": 13},
        ],
    )
    response = client.post("/api/render", json=request)
    job_id = response.json()["job_id"]
    main.run_render(job_id, main.RenderRequest(**request).model_dump())
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "complete"
    assert seen["text"] == "நிலவின் ஒளியில்\n\nஎன் கவிதை மலர்கிறது."
    assert seen["voice"] == "female"
    assert seen["durations"] == [11, 13]
    expressive.assert_not_called()


def test_failed_online_poetic_narration_does_not_fall_back_to_silent_video(client, monkeypatch):
    from app import narration, rendering

    async def unavailable(*args, **kwargs):
        raise narration.NarrationUnavailableError("The online service is unavailable.")

    renderer = Mock()
    monkeypatch.setattr(narration, "synthesize_expressive", unavailable)
    monkeypatch.setattr(rendering, "render_video", renderer)
    request = render_request(narration="male")
    response = client.post("/api/render", json=request)
    job_id = response.json()["job_id"]
    main.run_render(job_id, main.RenderRequest(**request).model_dump())
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "voice service" in job["error"]
    assert "video_url" not in job
    renderer.assert_not_called()
