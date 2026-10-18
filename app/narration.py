"""Tamil poetry phrasing and timed narration with the standard neural voices.

Direction notes are an editable reading score, not instructions understood by
Edge TTS. Only its supported rate, pitch, and volume controls are sent to the
service; the meaningful pauses below are inserted into the actual audio.
"""

import asyncio
import hashlib
import inspect
import json
import subprocess
import uuid
import wave
from pathlib import Path

import edge_tts

from app.speech_transport import configure_voice_transport

VOICE_IDS = {"female": "ta-IN-PallaviNeural", "male": "ta-IN-ValluvarNeural"}
SAMPLE_RATE = 24000
_MOODS = {
    "reflective": (-16, -2, -2, "Quiet, thoughtful delivery; let the image settle."),
    "romantic": (-13, 1, -2, "Warm, intimate delivery without a theatrical swell."),
    "hopeful": (-10, 3, 0, "A gentle lift, with space for the hopeful image."),
    "melancholic": (-20, -4, -3, "Restrained sadness; soften the end of the thought."),
}
_PIVOTS = ("மீண்டும்", "ஆயினும்", "காலம் கனிய")


class NarrationUnavailableError(RuntimeError):
    """The requested voice could not produce a complete, usable recording."""


def _pivot(text: str) -> str | None:
    plain = text.strip().rstrip(".…!?;: ")
    return plain if plain in _PIVOTS else None


def _phrases(text: str) -> list[str]:
    """Keep enjambed lines together; a caption line is not always a breath."""
    phrases, pending = [], []
    for line in text.splitlines():
        if not line.strip():
            if pending:
                phrases.append("\n".join(pending))
                pending = []
            continue
        if _pivot(line) and pending:
            phrases.append("\n".join(pending))
            pending = []
        pending.append(line)
        if _pivot(line) or line.rstrip().endswith((";", "!", "?", ".", "…", ":")):
            phrases.append("\n".join(pending))
            pending = []
    if pending:
        phrases.append("\n".join(pending))
    return phrases


def _reading(text: str, mood: str) -> tuple[int, int, int, str, str]:
    """Use concrete images to suggest a restrained seasonal emotional arc."""
    pivot = _pivot(text)
    if pivot == "ஆயினும்":
        return (-21, 2, -2, "resolve", "A quiet turn toward hope; hold before continuing.")
    if pivot == "மீண்டும்":
        return (-25, -5, -4, "recognition", "Recognize the returning emptiness; a long held breath.")
    if pivot == "காலம் கனிய":
        return (-22, -1, -2, "time", "Let time pass in the pause; do not hurry into autumn.")
    if "வசந்தத்தின் வாசம்" in text:
        return (-17, 3, -2, "hope", "Finish warmly, with a small upward lift and a lingering release.")
    if "மௌன விழா" in text:
        return (-23, -5, -4, "silence", "Turn inward on மௌன; contrast the earlier outward celebration.")
    if "வண்ண விழா" in text:
        return (-12, 2, -1, "outward", "An outward, admiring brightness; hold the semicolon for contrast.")
    if "கிளைகளில் குறைந்தேன்" in text:
        return (-25, -6, -4, "loss", "The final step of the three-part descent; soften and leave silence.")
    if "பூமியில் உதிர்ந்தேன்" in text:
        return (-21, -3, -3, "loss", "Lower the thought as the leaves reach the earth.")
    if "நிழற்படங்களில் நிறைந்தேன்" in text:
        return (-16, 0, -2, "bittersweet", "A measured first step; the apparent fullness is bittersweet.")
    if any(word in text for word in ("நினைவொன்று", "மௌனமாய்க்", "விடைபெறும்", "உயிர்த்துளி")):
        return (-23, -4, -4, "grief", "An intimate loss, spoken gently; let the last word fall away.")
    if any(word in text for word in ("வெறுமை", "உறைபனி", "கருவானம்", "வானம் மட்டும்")):
        return (-21, -5, -3, "solitude", "A low, spacious opening; feel the bare branches without overacting.")
    if any(word in text for word in ("அரும்பின", "துள்ளின", "ஆதவனின்", "தளிர்கள்")):
        return (-9, 4, 0, "spring", "Lighten the voice with the new leaves; a buoyant, gentle lift.")
    if any(word in text for word in ("மழைத்துளி", "உயிர் எழுத", "முகிழ்த்தது")):
        return (-13, 2, -1, "awakening", "Grow warmer as the roots come alive; open into பசுமை.")
    if any(word in text for word in ("பொன்னிறம்", "வண்ணம் மாறிய", "விழிகள் ஆயிரம்")):
        return (-16, 1, -2, "autumn", "Hold the beauty with a trace of foreboding, without a cheerful rush.")
    rate, pitch, volume, direction = _MOODS[mood]
    return rate, pitch, volume, mood, direction


def plan_narration(scenes: list[dict], mood: str = "reflective") -> list[dict]:
    """Return a reading score without a network call or changing the poem.

    Scene indices are zero based. Text remains in its original line order;
    full clauses stay together even when captions wrap across several lines.
    Duration is measured after synthesis, never guessed from caption timing.
    """
    if mood not in _MOODS:
        raise ValueError("Choose a supported narration mood.")
    if not scenes:
        raise ValueError("Add at least one scene to narrate.")
    result = []
    for scene_index, scene in enumerate(scenes):
        scene_text = scene.get("text")
        if not isinstance(scene_text, str) or not scene_text.strip():
            raise ValueError("Every narrated scene needs poem text.")
        for text in _phrases(scene_text):
            rate, pitch, volume, emotion, direction = _reading(text, mood)
            stripped = text.rstrip()
            pause_after = 300
            if stripped.endswith(";"):
                pause_after = 650
            elif stripped.endswith(("…", "...", "…!")):
                pause_after = 1150
            elif stripped.endswith(("!", "?", ".")):
                pause_after = 650
            pivot = _pivot(text)
            if pivot:
                pause_after = 1100 if pivot == "மீண்டும்" else 950
            result.append({
                "scene_index": scene_index,
                "text": text,
                "rate": f"{rate:+d}%",
                "pitch": f"{pitch:+d}Hz",
                "volume": f"{volume:+d}%",
                "pause_before_ms": 450 if pivot else 0,
                "pause_after_ms": pause_after,
                "emotion": emotion,
                "direction": direction,
            })
        # A complete scene gets a moment to land, even without punctuation.
        result[-1]["pause_after_ms"] = max(result[-1]["pause_after_ms"], 750)
    result[0]["pause_before_ms"] = max(result[0]["pause_before_ms"], 600)
    result[-1]["pause_after_ms"] = max(result[-1]["pause_after_ms"], 1500)
    return result


def _wave_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError("Narration cache has an unexpected audio format.")
        frames = audio.getnframes()
        if frames <= 0 or len(audio.readframes(frames)) != frames * 2:
            raise ValueError("Narration cache is empty or incomplete.")
        return frames


def _cache_valid(path: Path) -> bool:
    try:
        _wave_frames(path)
        return True
    except (OSError, EOFError, ValueError, wave.Error):
        return False


async def _synthesize_phrase(segment: dict, voice_id: str, target: Path) -> None:
    temporary = target.with_name(f".{uuid.uuid4().hex}")
    mp3_path, wav_path = temporary.with_suffix(".mp3"), temporary.with_suffix(".wav")
    try:
        speech = edge_tts.Communicate(
            segment["text"], voice_id, rate=segment["rate"],
            pitch=segment["pitch"], volume=segment["volume"],
            proxy=configure_voice_transport(),
        )
        await asyncio.wait_for(speech.save(str(mp3_path)), timeout=60)
        # Decode each phrase to the same PCM format. Concatenating compressed
        # MP3 files would introduce frame padding and unreliable scene timing.
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(mp3_path), "-vn", "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(wav_path),
            stdout=subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            raise NarrationUnavailableError("The voice service returned unreadable audio.")
        _wave_frames(wav_path)
        wav_path.replace(target)
    finally:
        mp3_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


async def synthesize_expressive(
    scenes: list[dict],
    voice: str,
    output_dir: Path,
    progress=None,
    mood: str = "reflective",
    *,
    cache_dir: Path | None = None,
) -> dict:
    """Produce actual phrase-directed speech and a sample-accurate timeline.

    ``progress(completed, total)`` may be sync or async. Cached phrases include
    all voice/prosody settings in their key and are validated before reuse.
    Provider failures raise; they never silently export a voiceless video.
    This uses a stock Tamil voice, not an imitation of a named person.
    """
    voice_id = VOICE_IDS.get(voice, voice)
    if voice_id not in VOICE_IDS.values():
        raise ValueError("Choose the standard Tamil male or female voice.")
    plan = plan_narration(scenes, mood)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir) if cache_dir is not None else output_dir.parent / ".narration-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    for index, segment in enumerate(plan):
        settings = {key: segment[key] for key in ("text", "rate", "pitch", "volume")}
        key = hashlib.sha256(json.dumps(
            {"version": 1, "voice": voice_id, **settings},
            ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        path = cache_dir / f"{key}.wav"
        if not _cache_valid(path):
            try:
                await _synthesize_phrase(segment, voice_id, path)
            except Exception as exc:
                raise NarrationUnavailableError(
                    "Tamil narration could not be completed. Check the online voice service "
                    "connection or upload a narration recording; the reading score is still available."
                ) from exc
        sources.append(path)
        if progress:
            reported = progress(index + 1, len(plan))
            if inspect.isawaitable(reported):
                await reported

    final_path = output_dir / "narration.wav"
    temporary = output_dir / f".narration-{uuid.uuid4().hex}.wav"
    cursor, scene_frames = 0, [0] * len(scenes)
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            for segment, source in zip(plan, sources, strict=True):
                before = segment["pause_before_ms"] * SAMPLE_RATE // 1000
                after = segment["pause_after_ms"] * SAMPLE_RATE // 1000
                with wave.open(str(source), "rb") as phrase:
                    frames = phrase.getnframes()
                    audio = phrase.readframes(frames)
                segment.update({
                    "start": cursor / SAMPLE_RATE,
                    "speech_start": (cursor + before) / SAMPLE_RATE,
                    "speech_end": (cursor + before + frames) / SAMPLE_RATE,
                    "end": (cursor + before + frames + after) / SAMPLE_RATE,
                    "speech_duration": frames / SAMPLE_RATE,
                })
                output.writeframesraw(b"\0\0" * before)
                output.writeframesraw(audio)
                output.writeframesraw(b"\0\0" * after)
                count = before + frames + after
                cursor += count
                scene_frames[segment["scene_index"]] += count
        _wave_frames(temporary)
        temporary.replace(final_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "narration_path": final_path,
        "voice_id": voice_id,
        "scene_durations": [frames / SAMPLE_RATE for frames in scene_frames],
        "duration": cursor / SAMPLE_RATE,
        "plan": plan,
    }
