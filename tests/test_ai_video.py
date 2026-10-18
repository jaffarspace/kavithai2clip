"""Exercise the official Runway request contract without paid network calls."""

import asyncio
import json

import httpx
import pytest
from PIL import Image

from app.ai_video import (
    API_ORIGIN,
    API_VERSION,
    RunwayVideoProvider,
    VideoProviderError,
    VideoTaskError,
    VideoTaskTimeout,
    build_motion_plan,
)

KEY = "test-secret-never-echo"
OUTPUT = "https://media.example.com/generated.mp4?token=download-only"
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2" + b"sample-output"


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "scene.png"
    Image.new("RGB", (128, 228), (23, 62, 83)).save(path)
    return path


def run(awaitable):
    return asyncio.run(awaitable)


def completed():
    return {"id": "task-1", "status": "SUCCEEDED", "output": [OUTPUT], "cost": {"credits": 50}}


def test_create_poll_download_contract_and_credential_isolation(image, tmp_path):
    requests = []
    sleeps = []
    states = iter(["PENDING", "THROTTLED", "RUNNING", "SUCCEEDED"])

    async def sleep(seconds):
        sleeps.append(seconds)

    def handler(request):
        requests.append(request)
        if request.url.host == "api.dev.runwayml.com":
            assert request.headers["authorization"] == "Bearer " + KEY
            assert request.headers["x-runway-version"] == API_VERSION
            if request.method == "POST":
                payload = json.loads(request.content)
                assert request.url.path == "/v1/image_to_video"
                assert payload == {
                    "model": "gen4_turbo",
                    "promptImage": payload["promptImage"],
                    "promptText": "Autumn leaves fall naturally.",
                    "duration": 10,
                    "ratio": "720:1280",
                }
                assert payload["promptImage"].startswith("data:image/png;base64,")
                return httpx.Response(200, json={"id": "task-1"})
            assert request.url.path == "/v1/tasks/task-1"
            state = next(states)
            return httpx.Response(
                200, json=completed() if state == "SUCCEEDED" else {"id": "task-1", "status": state}
            )
        assert request.url.host == "media.example.com"
        assert "authorization" not in request.headers
        assert "x-runway-version" not in request.headers
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4"})

    provider = RunwayVideoProvider(
        KEY,
        transport=httpx.MockTransport(handler),
        allowed_download_hosts=["media.example.com"],
        sleep=sleep,
    )
    result = run(provider.generate(image, "Autumn leaves fall naturally.", tmp_path / "scene.mp4"))
    assert result["task_id"] == "task-1"
    assert result["download_status"] == "downloaded"
    assert result["cost_credits"] == 50
    assert (tmp_path / "scene.mp4").read_bytes() == MP4
    assert sleeps == [5, 5, 5]
    assert sum(request.method == "POST" for request in requests) == 1


def test_unknown_output_host_is_reported_without_download(image, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "task-1"} if request.method == "POST" else completed())

    result = run(
        RunwayVideoProvider(KEY, transport=httpx.MockTransport(handler)).generate(
            image, "Leaves move in the breeze.", tmp_path / "scene.mp4"
        )
    )
    assert result["status"] == "SUCCEEDED"
    assert result["download_status"] == "network_configuration_required"
    assert result["download_host"] == "media.example.com"
    assert all(str(request.url).startswith(API_ORIGIN) for request in requests)
    assert not (tmp_path / "scene.mp4").exists()


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
def test_terminal_failure_preserves_task_id_without_echoing_provider_body(status):
    provider = RunwayVideoProvider(
        KEY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"id": "task-1", "status": status, "failure": KEY}
            )
        ),
    )
    with pytest.raises(VideoTaskError) as error:
        run(provider.poll("task-1"))
    assert error.value.task_id == "task-1"
    assert error.value.status == status
    assert KEY not in str(error.value)


def test_timeout_is_resumable_without_submitting_another_generation():
    elapsed = 0
    requests = []

    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "task-1", "status": "RUNNING"})

    provider = RunwayVideoProvider(
        KEY,
        transport=httpx.MockTransport(handler),
        max_wait=10,
        sleep=sleep,
        clock=lambda: elapsed,
    )
    with pytest.raises(VideoTaskTimeout, match="Resume polling") as error:
        run(provider.poll("task-1"))
    assert error.value.task_id == "task-1"
    assert all(request.method == "GET" for request in requests)
    assert len(requests) == 2


@pytest.mark.parametrize("kind", ["status", "network", "json"])
def test_api_failures_do_not_expose_credentials_or_provider_response(image, kind):
    def handler(request):
        if kind == "network":
            raise httpx.ConnectError("transport message contains " + KEY, request=request)
        if kind == "status":
            return httpx.Response(401, json={"message": KEY})
        return httpx.Response(200, text=KEY)

    provider = RunwayVideoProvider(KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(VideoProviderError) as error:
        run(provider.submit(image, "A gentle breeze."))
    assert KEY not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "data",
    [
        {"id": "task-1", "status": "SURPRISE"},
        {"id": "wrong-task", "status": "RUNNING"},
        {"id": "task-1", "status": "SUCCEEDED", "output": []},
        {"id": "task-1", "status": "SUCCEEDED", "output": ["http://media.example.com/file.mp4"]},
        {"id": "task-1", "status": "SUCCEEDED", "output": ["https://127.0.0.1/file.mp4"]},
        {"id": "task-1", "status": "SUCCEEDED", "output": ["https://user:pass@media.example.com/f"]},
    ],
)
def test_invalid_or_unsafe_provider_output_is_rejected(data):
    provider = RunwayVideoProvider(
        KEY, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    )
    with pytest.raises(VideoProviderError):
        run(provider.poll("task-1"))


def test_redirect_to_unapproved_host_is_not_followed(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://unapproved.example.com/scene.mp4"})

    provider = RunwayVideoProvider(
        KEY,
        allowed_download_hosts=["media.example.com"],
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(VideoProviderError, match="allowlist"):
        run(provider.download(OUTPUT, tmp_path / "scene.mp4"))
    assert len(requests) == 1
    assert not (tmp_path / "scene.mp4").exists()


def test_bad_video_download_preserves_existing_file_and_removes_partial(tmp_path):
    output = tmp_path / "scene.mp4"
    output.write_bytes(b"existing user file")
    provider = RunwayVideoProvider(
        KEY,
        allowed_download_hosts=["media.example.com"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"This is not a video", headers={"content-type": "video/mp4"}
            )
        ),
    )
    with pytest.raises(VideoProviderError, match="recognizable MP4"):
        run(provider.download(OUTPUT, output))
    assert output.read_bytes() == b"existing user file"
    assert list(tmp_path.glob("*.part")) == []


def test_prompt_model_and_polling_validation_happen_before_network(image):
    def handler(request):
        pytest.fail("Invalid requests must not reach the provider")

    provider = RunwayVideoProvider(KEY, transport=httpx.MockTransport(handler))
    for prompt in ["", " ", "😀" * 501]:
        with pytest.raises(ValueError, match="UTF-16"):
            run(provider.submit(image, prompt))
    with pytest.raises(ValueError, match="five- or ten-second"):
        run(provider.submit(image, "Leaves move.", duration=8))
    with pytest.raises(ValueError, match="supports"):
        RunwayVideoProvider(KEY, model="invented-current-model")
    with pytest.raises(ValueError, match="five seconds"):
        RunwayVideoProvider(KEY, poll_interval=1)
    with pytest.raises(ValueError, match="environment settings"):
        RunwayVideoProvider("")


def test_seasonal_motion_plan_preserves_source_paths_and_describes_real_motion():
    project = {
        "working_title": "ஒரு வசந்தத்தின் வாசம்",
        "request": {"scenes": [{"text": "தமிழ் கவிதை"}] * 10},
        "visual_plan": [{"image": f"images/scene-{index:02}.png"} for index in range(1, 11)],
    }
    plan = build_motion_plan(project)
    assert len(plan) == 10
    assert [item["image"] for item in plan] == [item["image"] for item in project["visual_plan"]]
    assert all("Preserve the original tree" in item["prompt"] for item in plan)
    assert all(len(item["prompt"].encode("utf-16-le")) <= 2000 for item in plan)
    assert "Raindrops" in plan[1]["prompt"]
    assert "leaves drift" in plan[4]["prompt"]
    assert "snow" in plan[0]["prompt"]


def test_motion_plan_accepts_two_scenes_without_visual_manifest_or_credentials(monkeypatch):
    monkeypatch.delenv("KAVITHAI_RUNWAY_API_KEY", raising=False)
    scenes = [
        {"text": "நிலவின் ஒளியில் என் நினைவு", "theme": "moon", "duration": 12},
        {"text": "கடலில் அலைகள் பேசுகின்றன", "theme": "ocean", "duration": 15},
    ]
    plan = build_motion_plan({"request": {"scenes": scenes}})
    assert len(plan) == 2
    assert "moonlit" in plan[0]["prompt"]
    assert "ocean waves" in plan[1]["prompt"]
    assert [item["target_duration"] for item in plan] == [12, 15]
    assert all(item["image"] is None for item in plan)
    assert all("do not add new objects" in item["prompt"] for item in plan)


def test_motion_plan_infers_motion_from_tamil_when_theme_is_abstract():
    plan = build_motion_plan({"scenes": [{"text": "மழைத் துளிகளில் என் கனவு", "theme": "abstract"}]})
    assert "Raindrops" in plan[0]["prompt"]
