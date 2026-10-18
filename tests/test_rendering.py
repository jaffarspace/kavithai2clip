"""Exercise exported media and the Tamil layout edge cases, without network calls."""

import json
import math
import shutil
import struct
import subprocess
import wave

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
    assert 6.7 <= float(media["format"]["duration"]) < 6.8
    assert Image.open(result["poster_path"]).size == (360, 640)
    assert photo.read_bytes() == photo_before
    assert not (tmp_path / "output" / "frames").exists()
    assert updates[-1][0] == 100
