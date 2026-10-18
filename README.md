# Kavithai to Clip

A small Python studio for turning Tamil poetry into portrait video clips. Paste your
poem, review its scene suggestions, add your photographs, and export an MP4 with
Tamil captions, a gentle original soundtrack, and optional narration.

## Your films

- [Open the Tamil-narrated HD cut](exports/vasanthathin-vaasam/oru-vasanthathin-vaasam-voiced.mp4)
- [Listen to the Tamil poetry reading](exports/vasanthathin-vaasam/tamil-poetry-narration.mp3)
- [Get the narrated project ZIP](exports/vasanthathin-vaasam/vasanthathin-vaasam-voiced-project.zip)
- [Open the original 100-second music-only cut](exports/vasanthathin-vaasam/oru-vasanthathin-vaasam.mp4)
- [Get the editable project ZIP](exports/vasanthathin-vaasam/vasanthathin-vaasam-project.zip)
- [Browse the exported files](exports/vasanthathin-vaasam)

On GitHub, open either file and select **Download raw file** to save it. The film
preserves all 35 supplied Tamil lines and uses seasonal tree imagery and original
ambient music. The narrated cut adds a paced Valluvar reading and smooth scene
dissolves. These exported cuts use AI-generated still backgrounds; genuine
image-to-video footage has not yet been generated or substituted.

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
3. Upload a photo or moving video clip for any scene, or keep its original
   illustrated background. Landscape sources are cropped to fill the portrait
   frame. Clip sound is discarded so it cannot compete with narration.
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
availability and gives a clear error if online narration fails. In managed cloud
environments, the app uses the supplied proxy and system certificate trust while
keeping TLS verification enabled. Uploaded audio and music-only exports work
independently of that service.

**Poetry reading** keeps whole clauses together, varies the voice's supported
rate, pitch, and volume controls, and inserts real silences around meaningful
turns such as “மீண்டும்…” and “ஆயினும்…”. Use **View reading direction** to review
the phrasing. This is a directed stock neural voice; its delivery should be
listened to and refined rather than treated as a guaranteed actor performance.
**Plain reading** retains a steady rate without the added poetry score.

Narration is generated first. Captions follow the measured phrase and scene
timings; smooth background dissolves preserve that timeline. The final sentence
is never cut to fit an estimated duration. A failed voice request stops the
export rather than silently producing another unvoiced film.

For a specific person's voice, including Vairamuthu's, upload a recording that
you have permission to use. This app does not include a celebrity voice model.
You should also have permission to use the poem, photos, and other recordings
you upload. Uploads stay on the local application server; they are not sent to
the online voice provider.

Accepted photos: JPEG, PNG, and WebP. Audio: MP3, WAV, M4A, OGG, AAC, FLAC,
or an audio WebM file. Video: MP4, MOV, or video WebM, up to 30 seconds and 4K.
Each file may be at most 25 MB; narration may be up to six minutes. Short moving
clips are trimmed or smoothly looped to fit their scenes. Scene editing allows
3–30 seconds; measured narration can extend individual scenes to 60 seconds.
The final video is limited to six minutes.
Poems may contain up to 4,000 characters across 10 scenes, with up to 400
characters per caption. Short captions make better clips.

## Genuine AI video

Runway is a separate image-to-video service that can animate motion inside a
reference image: rain falling, leaves fluttering and detaching, and clouds
drifting. A camera zoom over a still photograph is not generated video.

`app/ai_video.py` contains a tested adapter for Runway's official `gen4_turbo`
API and a motion-plan builder. The API schema was checked against the official
Runway Python SDK; its contract tests use mocked responses. No paid video jobs
have been submitted. The studio does not start paid generation automatically.

Provider access, credits, current pricing, and a spending limit must be agreed
before generation. Keep an API key in environment settings as
`KAVITHAI_RUNWAY_API_KEY`, never in source files or chat. API access needs
`api.dev.runwayml.com`; documentation is at `docs.dev.runwayml.com`. The actual
returned download host must also be allowed before retrieving an output.
Unknown download hosts are reported without being fetched.

A developer should call `submit()`, persist the task ID, then `poll()` and
`download()` so an interrupted job can resume without a second paid submission.
Completed moving clips from any provider can be uploaded to the scene editor.
Neither a Runway account nor an API key is required for Tamil narration,
uploaded footage, or editing.

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
