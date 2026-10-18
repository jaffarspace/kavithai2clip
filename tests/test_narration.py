import asyncio
import shutil
import ssl
import struct
import wave

import pytest

from app import narration, speech_transport

POEM_SCENES = [
    "உறைபனியின் பிடியில்\nவெறுமையாய் நின்ற\nஎன் கிளைகளில்\nகருவானம் மட்டும் மிச்சம்!",
    "மண் நனைத்த மழைத்துளி\nஎன் வேர்களில் உயிர் எழுத,\nமுகிழ்த்தது பசுமை!",
    "ஆதவனின் அரவணைப்பில்\nஅரும்பின தளிர்கள்;\nதென்றலின் தீண்டலில்\nதுள்ளின என் இலைகள்!",
    "காலம் கனிய…\nபசுமையின் விளிம்புகளில்\nபொன்னிறம் படர்ந்தது!",
    "வண்ணம் மாறிய என் மேனியில்\nவிழிகள் ஆயிரம்…\nஉதிர்ந்த இலைகளின் மடியில்\nவிடைபெறும் என் வசந்தங்கள்…",
    "காண்போரின் விழிகளில் வண்ண விழா;\nஎன் கிளைகளிலோ மௌன விழா!\nஉதிரும் என் உயிர்த்துளிகளை\nஉலகம் வண்ணமாய்ச் சிறைபிடிக்க…",
    "நிழற்படங்களில் நிறைந்தேன்;\nபூமியில் உதிர்ந்தேன்;\nகிளைகளில் குறைந்தேன்…",
    "ஒவ்வொரு இலையோடும்\nஎன்னுள் உறைந்த நினைவொன்று\nமண்ணோடு மௌனமாய்க் கலக்கிறது…",
    "மீண்டும்…\nவெறுமையான என் கிளைகளில்\nவானம் மட்டும் மிச்சம்!",
    "ஆயினும்…\nஉறைந்த என் வேர்களுக்குள்\nஉறங்குகிறது இன்னும்\nஒரு வசந்தத்தின் வாசம்…!",
]


def test_poem_score_preserves_all_35_lines_and_keeps_enjambed_clauses_together():
    plan = narration.plan_narration([{"text": text} for text in POEM_SCENES])
    original_lines = [line for text in POEM_SCENES for line in text.splitlines()]
    assert len(original_lines) == 35
    assert [line for segment in plan for line in segment["text"].splitlines()] == original_lines
    assert plan[0]["text"] == POEM_SCENES[0]
    assert next(segment for segment in plan if segment["scene_index"] == 1)["text"] == POEM_SCENES[1]
    assert [segment["text"] for segment in plan if segment["scene_index"] == 2] == [
        "ஆதவனின் அரவணைப்பில்\nஅரும்பின தளிர்கள்;",
        "தென்றலின் தீண்டலில்\nதுள்ளின என் இலைகள்!",
    ]


def test_score_has_emotional_turns_and_held_pauses_at_the_poems_meaning():
    plan = narration.plan_narration([{"text": text} for text in POEM_SCENES])
    by_text = {segment["text"]: segment for segment in plan}
    assert plan[0]["emotion"] == "solitude"
    assert any(segment["emotion"] == "spring" for segment in plan)
    assert plan[-1]["emotion"] == "hope"
    assert by_text["என் கிளைகளிலோ மௌன விழா!"]["pitch"] != by_text[
        "காண்போரின் விழிகளில் வண்ண விழா;"
    ]["pitch"]
    descent = [by_text[text]["rate"] for text in POEM_SCENES[6].splitlines()]
    assert [int(rate[:-1]) for rate in descent] == sorted(
        (int(rate[:-1]) for rate in descent), reverse=True,
    )
    for pivot in ("மீண்டும்…", "ஆயினும்…", "காலம் கனிய…"):
        assert by_text[pivot]["pause_before_ms"] >= 400
        assert by_text[pivot]["pause_after_ms"] >= 900
    assert plan[-1]["pause_after_ms"] >= 1500


def test_score_does_not_apply_a_pause_to_every_caption_line():
    plan = narration.plan_narration([{"text": "ஒரு\nநினைவை\nசொல்லும்\nநீண்ட\nவரி"}])
    assert len(plan) == 1
    assert plan[0]["text"] == "ஒரு\nநினைவை\nசொல்லும்\nநீண்ட\nவரி"


@pytest.mark.parametrize("scenes", [[], [{"text": " \n "}], [{"text": None}]])
def test_score_rejects_missing_poem(scenes):
    with pytest.raises(ValueError):
        narration.plan_narration(scenes)


def test_score_rejects_an_unknown_mood():
    with pytest.raises(ValueError, match="mood"):
        narration.plan_narration([{"text": "கவிதை"}], mood="unknown")


def _write_voice(path, frames=2400):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, narration.SAMPLE_RATE, 0, "NONE", "not compressed"))
        # A nonzero signal distinguishes generated speech from inserted silence.
        audio.writeframes(struct.pack("<h", 1200) * frames)


@pytest.fixture
def mock_voice(monkeypatch):
    calls = []

    class Voice:
        def __init__(self, text, voice, **settings):
            calls.append({"text": text, "voice": voice, **settings})

        async def save(self, filename):
            _write_voice(filename)

    monkeypatch.setattr(narration.edge_tts, "Communicate", Voice)
    return calls


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Narration decoding needs FFmpeg")
def test_synthesized_audio_contains_real_pauses_and_exact_scene_timing(tmp_path, mock_voice):
    scenes = [{"text": "மீண்டும்…\nவானம் மட்டும் மிச்சம்!"}, {"text": "ஆயினும்…\nஒரு வசந்தத்தின் வாசம்…!"}]
    progress = []
    result = asyncio.run(narration.synthesize_expressive(
        scenes, "male", tmp_path / "render", progress=lambda done, total: progress.append((done, total)),
    ))
    assert len(mock_voice) == len(result["plan"]) == 4
    assert progress[-1] == (4, 4)
    assert all(call["voice"] == "ta-IN-ValluvarNeural" for call in mock_voice)
    assert {"rate", "pitch", "volume"} <= mock_voice[0].keys()
    with wave.open(str(result["narration_path"]), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() == result["duration"]
        audio_bytes = audio.readframes(audio.getnframes())
    assert sum(result["scene_durations"]) == pytest.approx(result["duration"])
    last_end = 0
    for segment in result["plan"]:
        assert segment["start"] == last_end
        assert segment["speech_start"] - segment["start"] == pytest.approx(
            segment["pause_before_ms"] / 1000,
        )
        assert segment["end"] - segment["speech_end"] == pytest.approx(
            segment["pause_after_ms"] / 1000,
        )
        start = round(segment["start"] * narration.SAMPLE_RATE) * 2
        speech = round(segment["speech_start"] * narration.SAMPLE_RATE) * 2
        end_speech = round(segment["speech_end"] * narration.SAMPLE_RATE) * 2
        end = round(segment["end"] * narration.SAMPLE_RATE) * 2
        assert audio_bytes[start:speech] == b"\0" * (speech - start)
        assert any(audio_bytes[speech:end_speech])
        assert audio_bytes[end_speech:end] == b"\0" * (end - end_speech)
        last_end = segment["end"]
    for index, duration in enumerate(result["scene_durations"]):
        assert duration == pytest.approx(sum(
            segment["end"] - segment["start"] for segment in result["plan"]
            if segment["scene_index"] == index
        ))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Narration decoding needs FFmpeg")
def test_validated_cache_reuses_phrases_but_not_other_voices_or_corrupt_audio(tmp_path, mock_voice):
    scenes = [{"text": "வசந்தத்தின் வாசம்…!"}]
    first = asyncio.run(narration.synthesize_expressive(scenes, "male", tmp_path / "one"))
    asyncio.run(narration.synthesize_expressive(scenes, "male", tmp_path / "two"))
    assert len(mock_voice) == 1
    second_voice = asyncio.run(narration.synthesize_expressive(scenes, "female", tmp_path / "three"))
    assert len(mock_voice) == 2
    assert first["voice_id"] != second_voice["voice_id"]
    for cached in (tmp_path / ".narration-cache").glob("*.wav"):
        cached.write_bytes(b"corrupt cached audio")
    asyncio.run(narration.synthesize_expressive(scenes, "male", tmp_path / "four"))
    assert len(mock_voice) == 3


def test_provider_failure_never_returns_a_silent_narration(tmp_path, monkeypatch):
    class UnavailableVoice:
        def __init__(self, *args, **kwargs):
            pass

        async def save(self, filename):
            raise ConnectionError("service unavailable")

    monkeypatch.setattr(narration.edge_tts, "Communicate", UnavailableVoice)
    output_dir = tmp_path / "render"
    with pytest.raises(narration.NarrationUnavailableError, match="could not be completed"):
        asyncio.run(narration.synthesize_expressive([{"text": "கவிதை"}], "male", output_dir))
    assert not (output_dir / "narration.wav").exists()
    assert not list((tmp_path / ".narration-cache").glob("*.mp3"))


def test_only_standard_tamil_voice_ids_are_supported(tmp_path):
    with pytest.raises(ValueError, match="standard Tamil"):
        asyncio.run(narration.synthesize_expressive(
            [{"text": "கவிதை"}], "celebrity-clone", tmp_path,
        ))


def test_voice_transport_adds_system_trust_once_without_weakening_tls(monkeypatch):
    contexts = [ssl.create_default_context(), ssl.create_default_context()]
    for module, context in zip(
        (speech_transport.edge_tts.communicate, speech_transport.edge_tts.voices),
        contexts, strict=True,
    ):
        monkeypatch.setattr(module, "_SSL_CTX", context)
    calls = []
    original = ssl.SSLContext.load_default_certs

    def tracked_load(context, purpose=ssl.Purpose.SERVER_AUTH):
        calls.append(context)
        return original(context, purpose)

    monkeypatch.setattr(ssl.SSLContext, "load_default_certs", tracked_load)
    speech_transport.configure_voice_transport()
    speech_transport.configure_voice_transport()
    assert calls == contexts
    assert all(context.verify_mode == ssl.CERT_REQUIRED for context in contexts)
    assert all(context.check_hostname for context in contexts)
    assert all(context.cert_store_stats()["x509_ca"] > 0 for context in contexts)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, None),
        ({"https_proxy": "http://lowercase-proxy.invalid"}, "http://lowercase-proxy.invalid"),
        ({"HTTP_PROXY": "http://fallback.invalid"}, "http://fallback.invalid"),
        ({"HTTPS_PROXY": "http://preferred.invalid", "HTTP_PROXY": "http://fallback.invalid"},
         "http://preferred.invalid"),
    ],
)
def test_voice_transport_respects_proxy_configuration(monkeypatch, settings, expected):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    assert speech_transport.configure_voice_transport() == expected


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Narration decoding needs FFmpeg")
def test_synthesis_passes_platform_proxy_to_edge_without_logging_it(
    tmp_path, monkeypatch, mock_voice, caplog,
):
    proxy = "http://voice-user:private-test-password@proxy.invalid:8080"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    result = asyncio.run(narration.synthesize_expressive(
        [{"text": "கவிதை"}], "male", tmp_path / "render",
    ))
    assert mock_voice[0]["proxy"] == proxy
    assert "private-test-password" not in caplog.text
    assert "private-test-password" not in str(result)
