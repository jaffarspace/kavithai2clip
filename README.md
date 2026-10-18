# Kavithai to Clip

A small Python studio for turning Tamil poetry into portrait video clips. Paste your
poem, review its scene suggestions, add your photographs, and export an MP4 with
Tamil captions, a gentle original soundtrack, and optional narration.

## Your completed film

- [Open the 100-second HD video](exports/vasanthathin-vaasam/oru-vasanthathin-vaasam.mp4)
- [Get the editable project ZIP](exports/vasanthathin-vaasam/vasanthathin-vaasam-project.zip)
- [Browse the exported files](exports/vasanthathin-vaasam)

On GitHub, open either file and select **Download raw file** to save it. The film
preserves all 35 supplied Tamil lines and uses seasonal tree imagery and original
ambient music. This first cut has no spoken narration.

## Run

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), and FFmpeg/ffprobe on
your PATH. Tamil fonts are bundled under the SIL Open Font License. Pillow needs
RAQM support for Tamil shaping; the locked Linux wheels include it.

```bash
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In the prepared Codex environment, `./run.sh` uses the installed Python environment
and starts the app on port 8000. Set `PORT` to change the port.

## Make a clip

1. Enter a Tamil poem, its title, and optionally your name. A sample is included.
2. Generate scene suggestions. Review the captions, imagery, and timing.
3. Upload a photo for any scene, or keep its original illustrated background.
   Landscape photos are cropped to fill the portrait frame.
4. Choose a Tamil narrator, upload an audio recording, or use captions and music.
5. Export a quick draft or a 720 × 1280 HD MP4, then play and download it.

The scene planner recognizes Tamil and English imagery (moon, sea, rain, forest,
dawn, city, and love) and suggests a coherent visual sequence. These are editable
keyword-based suggestions. Review metaphors yourself; there is no language-model
or image-generation account required. Backgrounds are original illustrations with
slow camera movement. Your photos replace the corresponding illustrations.

## Narration and your recordings

Online narration uses Microsoft Edge's Tamil voices, **Valluvar** (male) and
**Pallavi** (female), through `edge-tts`. Selecting this option sends the scene text
to Microsoft's voice service. It needs HTTPS/WebSocket access to
`speech.platform.bing.com`, and service availability can change. The app checks
availability and gives a clear error if online narration fails. Uploaded audio
and music-only exports work independently of that service.

For a specific person's voice, including Vairamuthu's, upload a recording that
you have permission to use. This app does not include a celebrity voice model.
You should also have permission to use the poem, photos, and other recordings
you upload. Uploads stay on the local application server; they are not sent to
the online voice provider.

Accepted photos: JPEG, PNG, and WebP. Audio: MP3, WAV, M4A, OGG, AAC, FLAC,
or an audio WebM file. Each file may be at most 25 MB; narration may be up to
two minutes. Narration longer than the storyboard extends the video duration.
Poems may contain up to 4,000 characters across 10 scenes, with up to 400
characters per caption. Short captions make better clips.

## Development

```bash
uv run pytest -q
uv run ruff check app tests
uv run python scripts/smoke.py  # requires a running studio; creates a real draft clip
```

`app/storyboard.py` plans scenes; `app/rendering.py` creates captions, visuals,
audio, and H.264/AAC MP4s; `app/main.py` serves the web app and render queue.
The interface is plain HTML, CSS, and JavaScript with no frontend build step.

Local uploads and exports live in ignored `.data/`. Set `KAVITHAI_DATA_DIR` to
relocate that directory. Rendering runs one export at a time with a queue of up
to three jobs. In-progress job status is held in memory; restart the app only
after an export has finished. Completed media files remain on disk. This first
version is intended for a trusted local development environment.
