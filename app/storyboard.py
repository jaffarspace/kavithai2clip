"""Offline, editable storyboard suggestions for Tamil and English poems.

The rules here identify explicit imagery. They are deliberately not presented as
a language model's interpretation of a poem; the person making the film keeps
the final say over each scene.
"""

from __future__ import annotations

import math
import re
import unicodedata

MAX_POEM_CHARACTERS = 4_000
MAX_SCENES = 10
TARGET_CAPTION_CHARACTERS = 160
MAX_CAPTION_CHARACTERS = 400

THEMES = [
    {"id": "moon", "name": "Moonlight", "description": "Moon, stars, and a quiet night sky."},
    {"id": "ocean", "name": "Ocean", "description": "Rolling waves and a distant horizon."},
    {"id": "rain", "name": "Rain", "description": "Rainfall, clouds, and soft reflections."},
    {"id": "forest", "name": "Nature", "description": "Trees, leaves, and a living landscape."},
    {"id": "sunrise", "name": "Dawn", "description": "A rising sun and warm light."},
    {"id": "city", "name": "City", "description": "A skyline and lights along a street."},
    {"id": "love", "name": "Love", "description": "Warm light and gentle heart shapes."},
    {"id": "abstract", "name": "Abstract", "description": "Quiet shapes and changing colors."},
]

# Tamil terms are stems, so inflections such as "கடலில்" and "நிலவின்" match.
# English terms use word boundaries to avoid matching "sea" inside "season".
_LEXICON = {
    "moon": (
        ("நிலா", "நிலவ", "வெண்ணில", "பிறை", "சந்திர", "நட்சத்திர", "விண்மீன்", "இரவு"),
        ("moon", "moonlight", "lunar", "star", "stars", "night"),
    ),
    "ocean": (
        ("கடல்", "கடல", "அலை", "கரை", "ஆழி", "சமுத்திர"),
        ("sea", "ocean", "wave", "waves", "shore", "tide", "beach"),
    ),
    "rain": (
        ("மழை", "தூறல்", "துளி", "மேக", "முகில்", "கார்மேக"),
        ("rain", "rains", "rainfall", "raindrop", "raindrops", "drizzle", "cloud", "clouds"),
    ),
    "forest": (
        ("காடு", "காட்டி", "மரம்", "மரங்", "இலை", "மலர்", "பூக்", "பறவை", "வனம்", "காற்று"),
        ("forest", "tree", "trees", "leaf", "leaves", "flower", "flowers", "bird", "birds", "wind"),
    ),
    "sunrise": (
        ("விடியல்", "விடிய", "காலை", "கதிர", "சூரிய", "உதயம்", "உதய", "ஒளி"),
        ("sun", "sunrise", "sunlight", "dawn", "morning", "daybreak", "light"),
    ),
    "city": (
        ("நகர", "தெரு", "சாலை", "ரயில்", "பேருந்து", "வீதி", "விளக்"),
        ("city", "cities", "street", "streets", "road", "train", "traffic", "skyline"),
    ),
    "love": (
        ("காதல்", "அன்பு", "அன்பே", "இதய", "நேச", "முத்தம்", "பாசம்"),
        ("love", "loved", "beloved", "heart", "hearts", "kiss", "affection"),
    ),
}

_VISUALS = {
    "moon": "A luminous moon above a deep night sky, with a few slowly moving stars.",
    "ocean": "Layered waves roll toward the shore beneath a wide, open horizon.",
    "rain": "Fine rain falls beneath soft clouds, catching light as it passes.",
    "forest": "Leafy trees frame a quiet landscape with a gentle breeze.",
    "sunrise": "A warm sun rises over the horizon and gradually fills the sky with light.",
    "city": "A distant skyline and softly glowing windows suggest a quiet city street.",
    "love": "Warm rose-colored light and subtle heart shapes suggest affection.",
    "abstract": "Soft shapes drift through a quiet field of color, leaving room for the words.",
}

_MOOD_FALLBACKS = {
    "hopeful": "sunrise",
    "romantic": "love",
    "peaceful": "forest",
    "melancholic": "rain",
    "sad": "rain",
}


def _normalize_text(value: str) -> str:
    """Normalize encoding and line whitespace without touching Tamil letters."""
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(" ".join(line.split()) for line in value.split("\n")).strip()


def _split_long_line(line: str, target: int = TARGET_CAPTION_CHARACTERS) -> list[str]:
    """Split only at whitespace: never cut a Tamil letter or combining mark."""
    chunks: list[str] = []
    current = ""
    for word in line.split():
        if len(word) > MAX_CAPTION_CHARACTERS:
            raise ValueError(
                f"A word is longer than {MAX_CAPTION_CHARACTERS} characters. "
                "Add spaces or shorten that line so the captions remain readable."
            )
        if current and len(current) + 1 + len(word) > target:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        chunks.append(current)
    return chunks


def _pack_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        for part in _split_long_line(line):
            if current and len(current) + 1 + len(part) > TARGET_CAPTION_CHARACTERS:
                chunks.append(current)
                current = part
            else:
                current = f"{current}\n{part}" if current else part
    if current:
        chunks.append(current)
    return chunks


def _captions(poem: str, style: str) -> list[str]:
    stanzas = [stanza for stanza in re.split(r"\n\s*\n", poem) if stanza.strip()]
    lines = [line for stanza in stanzas for line in stanza.splitlines() if line.strip()]
    if len(stanzas) > 1:
        chunks = [chunk for stanza in stanzas for chunk in _pack_lines(stanza.splitlines())]
    elif len(lines) <= 4 and style == "cinematic":
        chunks = [chunk for line in lines for chunk in _split_long_line(line)]
    else:
        chunks = [chunk for index in range(0, len(lines), 2) for chunk in _pack_lines(lines[index:index + 2])]

    # Keep short poems' stanza boundaries. Longer poems can need denser captions;
    # repack at whitespace, aiming for 300 characters before using the hard cap.
    # Greedy packing yields the fewest ordered captions possible at that cap.
    for target in (300, MAX_CAPTION_CHARACTERS):
        if len(chunks) <= MAX_SCENES:
            break
        chunks = _split_long_line(" ".join(lines), target)
    if len(chunks) > MAX_SCENES:
        raise ValueError(
            f"This poem needs more than {MAX_SCENES} readable scenes. "
            "Shorten the poem or add spaces between long words, then try again."
        )
    return chunks


def _theme_scores(text: str) -> dict[str, int]:
    lower = text.casefold()
    scores: dict[str, int] = {}
    for theme, (tamil, english) in _LEXICON.items():
        # Overlapping stems for one concept should not count twice.
        tamil_hits = {term for term in tamil if term in lower}
        tamil_hits = {term for term in tamil_hits if not any(term != other and term in other for other in tamil_hits)}
        english_hits = {term for term in english if re.search(rf"\b{re.escape(term)}\b", lower)}
        score = len(tamil_hits) + len(english_hits)
        if score:
            scores[theme] = score
    return scores


def generate_storyboard(
    poem: str,
    title: str = "",
    author: str = "",
    style: str = "cinematic",
    mood: str = "reflective",
) -> dict:
    """Build a bounded, editable storyboard without making network requests.

    Blank stanzas establish scene boundaries when their captions fit; short
    cinematic poems receive one scene per line. Explicit imagery determines a
    scene's theme. Lines without imagery inherit the closest imagery in the
    poem, or a mood-based background when none is present.
    """
    if not isinstance(poem, str):
        raise TypeError("Please enter your poem as text.")
    poem = _normalize_text(poem)
    if not poem:
        raise ValueError("Please enter a Tamil poem before making a storyboard.")
    if len(poem) > MAX_POEM_CHARACTERS:
        raise ValueError(f"Please keep the poem within {MAX_POEM_CHARACTERS:,} characters.")
    if not all(isinstance(value, str) for value in (title, author, style, mood)):
        raise TypeError("Title, author, style, and mood must be text.")

    captions = _captions(poem, style)
    score_sets = [_theme_scores(caption) for caption in captions]
    explicit: dict[int, str] = {}
    previous_theme: str | None = None
    for index, scores in enumerate(score_sets):
        if scores:
            highest = max(scores.values())
            tied = [theme for theme, score in scores.items() if score == highest]
            selected = previous_theme if previous_theme in tied else tied[0]
            explicit[index] = selected
            previous_theme = selected

    scenes = []
    for index, caption in enumerate(captions):
        theme = explicit.get(index)
        if theme is None:
            if explicit:
                closest = min(explicit, key=lambda other: abs(other - index))
                theme = explicit[closest]
            else:
                theme = _MOOD_FALLBACKS.get(mood.casefold(), "abstract")
        word_count = len(caption.split())
        duration = max(3, min(10, math.ceil(word_count / 2.2) + 1))
        visual = _VISUALS[theme]
        if index not in explicit:
            visual += " This background follows the surrounding imagery or selected mood; it is a suggestion."
        scenes.append({"id": index + 1, "text": caption, "theme": theme, "visual": visual, "duration": duration})

    first_line = poem.splitlines()[0]
    title_words: list[str] = []
    for word in first_line.split():
        if len(" ".join([*title_words, word])) > 48:
            break
        title_words.append(word)
    generated_title = " ".join(title_words)
    if generated_title and generated_title != first_line:
        generated_title += "…"
    # Avoid cutting a Tamil word or its combining marks for a title excerpt.
    generated_title = generated_title or "என் கவிதை"
    return {
        "title": _normalize_text(title) or generated_title,
        "author": _normalize_text(author),
        "scenes": scenes,
        "duration": sum(scene["duration"] for scene in scenes),
        "interpretation": (
            "Editable offline suggestions based on Tamil and English imagery keywords, "
            "stanza boundaries, and your selected mood. These rules do not interpret every "
            "metaphor. Review the scenes and reading time before rendering."
        ),
    }
