import unicodedata

import pytest

from app.storyboard import THEMES, generate_storyboard


def words(text):
    return unicodedata.normalize("NFC", text).split()


def test_tamil_lines_keep_text_and_follow_concrete_imagery():
    poem = "நிலவின் ஒளியில் உன் நினைவு.\nகடலில் அலைகள் பேசுகின்றன!\nமழைத் துளியில் என் கனவு;\nவிடியல் நாளை அழைக்கிறது."
    board = generate_storyboard(poem)
    assert [scene["theme"] for scene in board["scenes"]] == ["moon", "ocean", "rain", "sunrise"]
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)
    assert board["title"] == "நிலவின் ஒளியில் உன் நினைவு."
    assert "offline" in board["interpretation"]


def test_stanzas_preserve_grouping_and_inherit_nearby_imagery():
    poem = "மழை மெதுவாக விழுகிறது\nஎன் நினைவுகள் திரும்புகின்றன\n\nஉன் பெயரைச் சொல்கிறேன்\nஎன் மனம் அமைதியாகிறது"
    board = generate_storyboard(poem, title="நினைவுகள்", author="கவிஞர்")
    assert len(board["scenes"]) == 2
    assert all(scene["theme"] == "rain" for scene in board["scenes"])
    assert board["title"] == "நினைவுகள்"
    assert board["author"] == "கவிஞர்"
    assert board["scenes"][0]["text"].count("\n") == 1
    assert "suggestion" in board["scenes"][1]["visual"]


@pytest.mark.parametrize(
    ("poem", "expected"),
    [
        ("காட்டில் பறவைகள் பாடும்", "forest"),
        ("நகரத்தின் தெருக்கள் காத்திருக்கின்றன", "city"),
        ("காதல் என் இதயத்தில்", "love"),
        ("The ocean carries a wave to shore", "ocean"),
        ("A new season begins", "abstract"),
    ],
)
def test_tamil_inflections_and_english_word_boundaries(poem, expected):
    assert generate_storyboard(poem)["scenes"][0]["theme"] == expected


def test_long_poem_preserves_every_word_within_scene_and_duration_bounds():
    poem = "\n\n".join(f"{index} நிலவின் நினைவில் என் கவிதை மலர்கிறது " * 3 for index in range(20))
    board = generate_storyboard(poem)
    assert len(board["scenes"]) <= 10
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)
    assert all(3 <= scene["duration"] <= 10 for scene in board["scenes"])
    assert board["duration"] == sum(scene["duration"] for scene in board["scenes"])
    assert [scene["id"] for scene in board["scenes"]] == list(range(1, len(board["scenes"]) + 1))


def test_normalizes_tamil_unicode_without_dropping_combining_marks():
    poem = unicodedata.normalize("NFD", "கொஞ்சும் மொழி!\r\nகோடை நினைவு.")
    board = generate_storyboard(poem)
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)
    assert all(unicodedata.is_normalized("NFC", scene["text"]) for scene in board["scenes"])


def test_long_line_splits_at_words_without_splitting_tamil_glyphs():
    poem = "கொஞ்சும் தமிழ் மொழி! " * 40
    board = generate_storyboard(poem)
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)
    assert all(len(scene["text"]) <= 160 for scene in board["scenes"])


def test_full_4000_character_poem_preserves_all_words_with_bounded_captions():
    poem = ("கவி " * 1000).rstrip() + "."
    assert len(poem) == 4000
    board = generate_storyboard(poem)
    assert len(board["scenes"]) == 10
    assert all(len(scene["text"]) <= 400 for scene in board["scenes"])
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)


def test_dense_stanzas_repack_without_oversized_scenes():
    poem = "\n\n".join("கொஞ்சும் தமிழ் மொழி! " * 8 for _ in range(19))
    board = generate_storyboard(poem)
    assert len(board["scenes"]) <= 10
    assert all(len(scene["text"]) <= 400 for scene in board["scenes"])
    assert words(" ".join(scene["text"] for scene in board["scenes"])) == words(poem)


def test_long_unsplit_tamil_word_fails_with_actionable_message():
    with pytest.raises(ValueError, match="Add spaces or shorten"):
        generate_storyboard("கவிதை" * 90)


def test_words_that_cannot_fit_ten_captions_fail_without_silent_truncation():
    poem = " ".join(["அ" * 201] * 11)
    with pytest.raises(ValueError, match="more than 10 readable scenes"):
        generate_storyboard(poem)


def test_neutral_words_stay_abstract_and_mood_can_suggest_a_background():
    assert generate_storyboard("என் நினைவுகள் மட்டும்")["scenes"][0]["theme"] == "abstract"
    assert generate_storyboard("என் நினைவுகள் மட்டும்", mood="hopeful")["scenes"][0]["theme"] == "sunrise"


def test_theme_catalog_matches_generated_values():
    assert {theme["id"] for theme in THEMES} == {
        "moon", "ocean", "rain", "forest", "sunrise", "city", "love", "abstract"
    }


@pytest.mark.parametrize("poem", ["", " \n \t ", "அ" * 4001])
def test_invalid_poems_are_rejected(poem):
    with pytest.raises(ValueError):
        generate_storyboard(poem)


def test_nontext_input_is_rejected():
    with pytest.raises(TypeError):
        generate_storyboard(None)


def test_long_title_excerpt_does_not_cut_a_tamil_word():
    board = generate_storyboard("கவிதை" * 30)
    assert board["title"] == "என் கவிதை"
