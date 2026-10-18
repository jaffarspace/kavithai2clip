"""Runway image-to-video adapter; no generation is submitted on import.

The API contract was checked against the official ``runwayml`` 5.22.0 wheel
published on PyPI, including its SHA256. Source references:
https://github.com/runwayml/sdk-python/blob/main/src/runwayml/resources/image_to_video.py
https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/image_to_video_create_params.py
https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/task_retrieve_response.py

This module has no price assumptions. Callers must obtain spending authorization
before calling ``submit`` or ``generate``. Provider credentials stay server-side.
"""

import asyncio
import base64
import ipaddress
import math
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

API_ORIGIN = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
DEFAULT_MODEL = "gen4_turbo"
SUPPORTED_RATIOS = {"1280:720", "720:1280", "1104:832", "832:1104", "960:960", "1584:672"}
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_DATA_URI = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024


class VideoProviderError(RuntimeError):
    """A deliberately redacted provider error, safe to display to the user."""


class VideoTaskError(VideoProviderError):
    def __init__(self, task_id: str, status: str):
        self.task_id = task_id
        self.status = status
        super().__init__(f"Runway task {task_id} ended with status {status}.")


class VideoTaskTimeout(VideoProviderError):
    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            f"Runway task {task_id} is still processing. Resume polling this task; "
            "do not submit another generation."
        )


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not TASK_ID.fullmatch(value):
        raise VideoProviderError("Runway returned an invalid task identifier.")
    return value


def output_hostname(url: str) -> str:
    """Accept only ordinary public HTTPS hostnames, with no embedded credentials."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except (ValueError, AttributeError):
        raise VideoProviderError("Runway returned an invalid video download URL.") from None
    if (
        parsed.scheme != "https"
        or not host
        or "." not in host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    ):
        raise VideoProviderError("Runway returned an unsafe video download URL.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise VideoProviderError("Video downloads must use an approved public hostname.")
    return host.lower()


class RunwayVideoProvider:
    """Generate a real moving scene and optionally download its resulting MP4.

    ``allowed_download_hosts`` is an exact, operator-controlled allowlist. Unknown
    output hosts are reported without being fetched. Redirects are checked against
    the same allowlist; the API key is never sent to download hosts. ``transport``
    exists for deterministic contract tests, not to disable HTTPS verification.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        allowed_download_hosts: Iterable[str] = (),
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = 5,
        max_wait: float = 900,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not api_key or not api_key.strip() or any(char in api_key for char in "\r\n"):
            raise ValueError("Set KAVITHAI_RUNWAY_API_KEY in the environment settings.")
        if model != DEFAULT_MODEL:
            raise ValueError("This adapter currently supports the verified gen4_turbo model.")
        if not math.isfinite(poll_interval) or poll_interval < 5:
            raise ValueError("Runway task polling must be at least five seconds apart.")
        if not math.isfinite(max_wait) or max_wait <= 0:
            raise ValueError("The task timeout must be positive.")
        hosts = set()
        for host in allowed_download_hosts:
            if not isinstance(host, str) or output_hostname("https://" + host) != host.lower():
                raise ValueError("Use exact public download hostnames without URL paths.")
            if any(character in host for character in "/?#:@"):
                raise ValueError("Use exact public download hostnames without URL paths.")
            hosts.add(host.lower())
        self._api_key = api_key.strip()
        self.model = model
        self.allowed_download_hosts = frozenset(hosts)
        self._transport = transport
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self._sleep = sleep
        self._clock = clock

    async def _api(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=60, follow_redirects=False
            ) as client:
                response = await client.request(
                    method,
                    API_ORIGIN + path,
                    headers={
                        "Authorization": "Bearer " + self._api_key,
                        "X-Runway-Version": API_VERSION,
                        "Accept": "application/json",
                    },
                    json=payload,
                )
            if response.status_code < 200 or response.status_code >= 300:
                raise VideoProviderError(
                    f"Runway API returned HTTP {response.status_code}. "
                    "Check provider access, credits, and network settings."
                )
            data = response.json()
            if not isinstance(data, dict):
                raise VideoProviderError("Runway returned an invalid API response.")
            return data
        except (httpx.HTTPError, ValueError):
            raise VideoProviderError(
                "Runway API could not be reached or returned invalid data. "
                "Check network settings and provider access."
            ) from None

    async def submit(
        self, image_path: Path, prompt: str, *, duration: int = 10, ratio: str = "720:1280"
    ) -> str:
        """Submit exactly once. HTTP failures are not retried to avoid duplicate charges."""
        if type(duration) is not int or duration not in (5, 10):
            raise ValueError("Choose a five- or ten-second image-to-video scene.")
        if ratio not in SUPPORTED_RATIOS:
            raise ValueError("Choose a supported Runway output resolution.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-16-le")) > 2000:
            raise ValueError("The motion prompt must contain 1–1000 UTF-16 code units.")
        path = Path(image_path)
        if path.stat().st_size > MAX_DATA_URI:
            raise ValueError("The scene image exceeds the provider's five-megabyte input limit.")
        try:
            with Image.open(path) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                    image.format
                )
                image.verify()
        except (UnidentifiedImageError, OSError):
            raise ValueError("Choose a valid PNG, JPEG, or WebP scene image.") from None
        if mime is None:
            raise ValueError("Choose a PNG, JPEG, or WebP scene image.")
        image_uri = "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        if len(image_uri) > MAX_DATA_URI:
            raise ValueError("The encoded scene image exceeds the provider's five-megabyte limit.")
        data = await self._api(
            "POST",
            "/v1/image_to_video",
            {
                "model": self.model,
                "promptImage": image_uri,
                "promptText": prompt,
                "duration": duration,
                "ratio": ratio,
            },
        )
        return _task_id(data.get("id"))

    async def poll(self, task_id: str) -> dict:
        """Resume an existing task without spending credits on another generation."""
        task_id = _task_id(task_id)
        deadline = self._clock() + self.max_wait
        while True:
            if self._clock() >= deadline:
                raise VideoTaskTimeout(task_id)
            data = await self._api("GET", "/v1/tasks/" + task_id)
            if _task_id(data.get("id")) != task_id:
                raise VideoProviderError("Runway returned a different task identifier.")
            status = data.get("status")
            if not isinstance(status, str):
                raise VideoProviderError("Runway returned an invalid task status.")
            if status in {"FAILED", "CANCELLED"}:
                raise VideoTaskError(task_id, status)
            if status == "SUCCEEDED":
                outputs = data.get("output")
                if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], str):
                    raise VideoProviderError("The completed Runway task has no video output.")
                url = outputs[0]
                result = {
                    "task_id": task_id,
                    "status": status,
                    "model": self.model,
                    "download_url": url,
                    "download_host": output_hostname(url),
                }
                cost = (
                    data["cost"].get("credits") if isinstance(data.get("cost"), dict) else None
                )
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost):
                    result["cost_credits"] = cost
                return result
            if status not in {"PENDING", "THROTTLED", "RUNNING"}:
                raise VideoProviderError("Runway returned an unknown task status.")
            remaining = deadline - self._clock()
            if remaining < self.poll_interval:
                raise VideoTaskTimeout(task_id)
            await self._sleep(self.poll_interval)

    async def download(self, url: str, destination: Path) -> Path:
        """Download an approved output without forwarding the provider credential."""
        temporary_path = None
        destination = Path(destination)
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=120, follow_redirects=False
            ) as client:
                for _ in range(4):
                    if output_hostname(url) not in self.allowed_download_hosts:
                        raise VideoProviderError("Add the video output hostname to the download allowlist.")
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise VideoProviderError("The video download returned an invalid redirect.")
                            url = urljoin(url, location)
                            continue
                        if response.status_code != 200:
                            raise VideoProviderError(
                                f"The video download returned HTTP {response.status_code}."
                            )
                        content_type = response.headers.get("content-type", "").split(";", 1)[0]
                        if content_type not in {"video/mp4", "application/octet-stream", "video/quicktime"}:
                            raise VideoProviderError("The provider output is not an MP4 video.")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.NamedTemporaryFile(
                            dir=destination.parent, suffix=".mp4.part", delete=False
                        ) as handle:
                            temporary_path = Path(handle.name)
                            length = 0
                            prefix = bytearray()
                            async for chunk in response.aiter_bytes():
                                length += len(chunk)
                                if length > MAX_VIDEO_BYTES:
                                    raise VideoProviderError("The video output exceeds the download size limit.")
                                if len(prefix) < 32:
                                    prefix.extend(chunk[: 32 - len(prefix)])
                                handle.write(chunk)
                        if len(prefix) < 12 or b"ftyp" not in prefix:
                            raise VideoProviderError("The downloaded output is not a recognizable MP4.")
                        os.replace(temporary_path, destination)
                        temporary_path = None
                        return destination
                raise VideoProviderError("The video download returned too many redirects.")
        except httpx.HTTPError:
            raise VideoProviderError(
                "The generated video could not be downloaded. Resume the existing task "
                "after checking output-host network access."
            ) from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def generate(
        self,
        image_path: Path,
        prompt: str,
        destination: Path,
        *,
        duration: int = 10,
        ratio: str = "720:1280",
    ) -> dict:
        task_id = await self.submit(image_path, prompt, duration=duration, ratio=ratio)
        result = await self.poll(task_id)
        if result["download_host"] not in self.allowed_download_hosts:
            return {**result, "download_status": "network_configuration_required"}
        path = await self.download(result["download_url"], destination)
        return {**result, "download_status": "downloaded", "video_path": str(path)}


SEASONAL_MOTIONS = (
    "Fine snow falls between bare frost-covered branches; distant clouds drift slowly.",
    "Raindrops strike the dark soil and ripple in puddles around the roots.",
    "Young green leaves flutter in a soft breeze as morning sunlight shifts through them.",
    "A gentle breeze turns green and gold leaves, revealing their autumn edges.",
    "Golden leaves drift naturally from the branches while distant visitors walk slowly.",
    "Autumn leaves fall in the foreground; the photographer gently lifts their camera.",
    "A few leaves detach and spiral onto the thick carpet beneath the thinning crown.",
    "One leaf slowly settles beside the roots while tiny raindrops touch the soil.",
    "Thin clouds drift behind the bare branches; a final leaf drifts away.",
    "Soft frost glints around the roots and a sleeping bud; sunrise light gently strengthens.",
)

THEME_MOTIONS = {
    "moon": "Existing clouds drift gently across the moonlit sky; reflected light ripples naturally.",
    "ocean": "Existing ocean waves roll and break continuously; foam spreads and recedes naturally.",
    "rain": "Raindrops fall naturally through the existing scene and ripple in any visible puddles.",
    "forest": "Existing leaves and branches sway gently in a soft breeze, with natural layered motion.",
    "sunrise": "Existing light and shadows slowly shift as any visible clouds drift across the dawn.",
    "city": "Existing people and vehicles move slowly and naturally through the established scene.",
    "love": "Any existing hair, fabric, or foliage moves softly in a breeze; retain each person's pose.",
    "abstract": "Existing visible textures and light move subtly and continuously within the scene.",
}


def _motion_theme(scene: dict) -> str:
    theme = scene.get("theme")
    if theme in THEME_MOTIONS and theme != "abstract":
        return theme
    text = str(scene.get("text", "")).lower()
    for candidate, tamil, english in (
        ("ocean", ("கடல்", "கடல", "அலை"), r"\b(ocean|sea|waves?)\b"),
        ("rain", ("மழை", "தூறல்"), r"\b(rain|rainfall|raindrops?)\b"),
        ("forest", ("மரம்", "கிளை", "இலை", "காடு"), r"\b(trees?|leaves|forest)\b"),
        ("moon", ("நிலவ", "நிலா", "இரவு"), r"\b(moon|night|moonlight)\b"),
        ("sunrise", ("விடிய", "சூரிய", "ஆதவன்"), r"\b(dawn|sunrise|sunlight)\b"),
    ):
        if any(word in text for word in tamil) or re.search(english, text):
            return candidate
    return "abstract"


def build_motion_plan(project: dict) -> list[dict]:
    """Plan genuine in-scene motion without starting a generation or needing a key."""
    scenes = project.get("request", project).get("scenes", [])
    visuals = project.get("visual_plan", [])
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 10:
        raise ValueError("The motion plan requires between one and ten scenes.")
    seasonal = (
        len(scenes) == 10
        and len(visuals) == 10
        and (
            project.get("working_title") == "ஒரு வசந்தத்தின் வாசம்"
            or (
                "உறைபனியின் பிடியில்" in scenes[0].get("text", "")
                and "ஒரு வசந்தத்தின் வாசம்" in scenes[-1].get("text", "")
            )
        )
    )
    subject = "the original tree, composition, people, and season" if seasonal else (
        "the original subjects, composition, identities, and season"
    )
    continuity = (
        f"Cinematic realistic moving video. Preserve {subject} shown in the reference image. "
        "Animate only elements already present; do not add new objects. Stable natural geometry, "
        "restrained camera, continuous physical motion, no text, no sudden cuts. "
    )
    plan = []
    for index, scene in enumerate(scenes):
        visual = visuals[index] if index < len(visuals) else {}
        motion = SEASONAL_MOTIONS[index] if seasonal else THEME_MOTIONS[_motion_theme(scene)]
        plan.append(
            {
                "scene": index + 1,
                "image": visual.get("image") or scene.get("image_id"),
                "prompt": continuity + motion,
                "duration": 10,
                "target_duration": scene.get("duration", 10),
                "ratio": "720:1280",
            }
        )
    return plan
