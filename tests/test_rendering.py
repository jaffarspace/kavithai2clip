"""Exercise exported media and the Tamil layout edge cases, without network calls."""

import json
import math
import shutil
import struct
import subprocess
import wave

import numpy as np
import pytest
from PIL import Image

from app.rendering import _caption, _font, _wrap, render_video


def test_long_tamil_word_wrap_keeps_vowel_signs_and_text():
    text = "காதலின்நினைவுகளின்ஆழத்தில்" * 12
    font = _font(24)
    lines = _wrap(text, font, 270)
    assert len(lines) > 1
    assert "".join(lines) == text
    assert all(font.getlength(line) <= 270 for line in lines)
    assert all(
        not line.startswith(("்", "ா", "ி", "ீ", "ு", "ூ", "ெ", "ே", "ை", "ொ", "ோ", "ௌ"))
        for line in lines
    )


def test_caption_rejects_unreadable_overflow_instead_of_clipping():
    with pytest.raises(ValueError, match="Split it into two scenes"):
        _caption((360, 640), "\n".join(["கனவு"] * 90), "", "", 0, 1, "cinematic")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for media export")
def test_real_export_with_photo_narration_and_soundtrack(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    photo = assets / "photo-1.png"
    Image.new("RGB", (640, 360), (113, 51, 86)).save(photo)
    photo_before = photo.read_bytes()
    narration = tmp_path / "narration.wav"
    sample_rate = 16000
    with wave.open(str(narration), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(2000 * math.sin(2 * math.pi * 220 * i / sample_rate)))
                for i in range(int(6.2 * sample_rate))
            )
        )
    updates = []
    result = render_video(
        {
            "title": "நினைவுகள்",
            "author": "தமிழ்",
            "music": True,
            "quality": "draft",
            "scenes": [
                {
                    "text": "நிலவின் மௌனத்தில்\nஉன் நினைவுகள்",
                    "theme": "moon",
                    "duration": 2.5,
                    "image_id": "photo-1",
                },
                {"text": "கடல் அலைகளில்\nஎன் கனவுகள்", "theme": "ocean", "duration": 2.5},
            ],
        },
        tmp_path / "output",
        assets,
        lambda value, stage: updates.append((value, stage)),
        narration_path=narration,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            result["video_path"],
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    media = json.loads(probe.stdout)
    video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in media["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"], video["pix_fmt"]) == (
        "h264",
        360,
        640,
        "yuv420p",
    )
    assert audio["codec_name"] == "aac"
    # A longer audio tail extends only the final scene; earlier boundaries stay put.
    assert 6.2 <= float(media["format"]["duration"]) < 6.2 + 1 / 24
    assert Image.open(result["poster_path"]).size == (360, 640)
    assert photo.read_bytes() == photo_before
    assert not (tmp_path / "output" / "frames").exists()
    assert updates[-1][0] == 100


def _video_frame(path, timestamp):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(640, 360, 3)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for media export")
def test_moving_video_dissolve_and_complete_narration(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    source = assets / "moving-video.mp4"
    # Every pixel changes colour over time. A camera move on a frozen frame could
    # never reproduce this, so the test verifies content motion rather than zoom.
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            "2",
            "-filter_threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=640x360:r=24:d=1.5",
            "-vf",
            "format=gbrp,geq=r='180+60*sin(2*PI*T/1.5)':g=15:b=15",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    original = source.read_bytes()
    Image.new("RGB", (400, 700), (12, 20, 230)).save(assets / "blue-photo.png")
    narration = tmp_path / "narration.wav"
    rate = 16000
    with wave.open(str(narration), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        samples = np.sin(2 * np.pi * 220 * np.arange(19 * rate) / rate) * 2400
        audio.writeframes(samples.astype("<i2").tobytes())
    result = render_video(
        {
            "quality": "draft",
            "music": False,
            "transition_seconds": 0.6,
            "scenes": [
                {"text": "கடல் அசைகிறது", "video_id": "moving-video", "duration": 16.2},
                {"text": "நினைவுகள் தொடர்கின்றன", "image_id": "blue-photo", "duration": 2.8},
            ],
        },
        tmp_path / "output",
        assets,
        lambda *_: None,
        narration_path=narration,
    )
    assert result["duration"] == 19
    assert result["transition_seconds"] == 0.6
    early = _video_frame(result["video_path"], 0.25)[140:180, 20:60, 0].mean()
    later = _video_frame(result["video_path"], 0.7)[140:180, 20:60, 0].mean()
    assert abs(early - later) > 35, "Source content motion must survive portrait rendering"
    midpoint = _video_frame(result["video_path"], 16.2)[140:180, 20:60].mean(axis=(0, 1))
    assert midpoint[0] > 25 and midpoint[2] > 25, "Dissolve must blend scenes without black"
    final_audio = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "18.75",
            "-i",
            result["video_path"],
            "-t",
            "0.2",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    samples = np.frombuffer(final_audio.stdout, dtype="<f4")
    assert len(samples) >= 3000
    assert np.sqrt(np.mean(samples**2)) > 0.015, "The final narration must remain audible"
    assert source.read_bytes() == original
    assert Image.open(result["poster_path"]).size == (360, 640)
