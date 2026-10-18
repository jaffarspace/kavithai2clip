"""Exercise a running studio through a real Tamil video export."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("/tmp/kavithai-smoke.mp4"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=15, trust_env=False) as client:
        response = client.post("/api/storyboard", json={
            "title": "மீண்டும் விடியல்",
            "poem": "நிலவின் ஒளியில் என் கனவு மலர்கிறது.\nவிடியலின் வானம் நம்பிக்கை தருகிறது.",
        })
        response.raise_for_status()
        scenes = response.json()["scenes"]
        for scene in scenes:
            scene["duration"] = 3
        response = client.post("/api/render", json={
            "title": "மீண்டும் விடியல்", "scenes": scenes,
            "quality": "draft", "narration": "none", "music": True,
        })
        response.raise_for_status()
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = client.get(f"/api/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            if job["status"] == "failed":
                raise RuntimeError(job.get("error", "Export failed"))
            if job["status"] == "complete":
                response = client.get(job["video_url"])
                response.raise_for_status()
                args.output.write_bytes(response.content)
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("The video export did not complete within 90 seconds")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
        str(args.output),
    ], capture_output=True, text=True, check=True, timeout=15)
    media = json.loads(result.stdout)
    video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in media["streams"] if stream["codec_type"] == "audio")
    if (video["codec_name"], video["width"], video["height"]) != ("h264", 360, 640):
        raise RuntimeError("The export does not have the expected portrait H.264 video")
    if audio["codec_name"] != "aac" or float(media["format"]["duration"]) < 5.9:
        raise RuntimeError("The export is incomplete or missing AAC audio")
    print(f"Tamil export passed: {args.output} (H.264, 360×640, AAC)")


if __name__ == "__main__":
    main()
