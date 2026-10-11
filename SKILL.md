---
name: explainer-video
description: Make animated explainer videos, GIFs, single-image flow diagrams and PDF carousels from a topic - narrated, captioned or silent. Choose landscape, square, LinkedIn 4:5 or vertical 9:16, a clean or hand-drawn sketch style, and a light, warm or dark theme. Use when the user wants a video or animated diagram that explains a technical idea, an arrow-flow image or GIF, a LinkedIn carousel, or to turn an article, repo or concept into a short visual explainer, with or without a voiceover.
---

# Explainer video

Turns a topic into animated diagrams (boxes, arrows, token grids, step tabs, hand-drawn sketches) timed to a voiceover, to captions, or to nothing but on-screen text. Claude writes a JSON scene spec; `scripts/evideo.py` renders it with Pillow and ffmpeg (no browser, no GPU).

It makes motion-graphic explainers. It does not make live-action footage, avatars or realistic AI video.

## Requirements

Python 3.9+, `pip install pillow numpy`, ffmpeg and ffprobe on PATH. Fonts are bundled. Check with `python3 scripts/evideo.py --version`.

## Pick the output first

Ask only for what the user has not said.

| Decision | Options | Default advice |
|---|---|---|
| Where it goes -> `canvas` | `linkedin` (4:5, 1080x1350), `square`, `story` (9:16 vertical for Reels, Shorts, mobile), `landscape` (16:9 for YouTube, blogs, slides) | LinkedIn feed: `linkedin` |
| Look -> `style` | `clean` (polished, work-friendly), `sketch` (hand-drawn whiteboard, friendlier) | `clean`; `sketch` for casual explainers |
| Colours -> `theme` | `light`, `warm` (cream, terracotta), `dark` | `light`; `dark` with `sketch` |
| File -> `--out` | `.mp4` video, `.gif` short loop, `.png` one image with everything shown, `.pdf` carousel (one page per scene, LinkedIn document post) | `.mp4` |
| Sound -> `--audio` | `none` (text on screen only), `files` (clips the user made), `generate` (TTS engine), plus `--captions` | `none` unless they want a voice |

One spec can export several shapes: `--canvas linkedin,story,landscape` writes one file per canvas.

## Workflow

1. **Scope.** Topic, audience, length (four to six scenes, 40-90 s is typical), plus the choices above. If they gave a source (article, repo, paper), read it first.
2. **Storyboard.** One idea per scene: a short `heading`, a diagram that builds in the order the idea is explained, and, if there is a voice, 35-60 words of narration. Show the list and continue unless they want changes.
3. **Write the spec** (`references/spec.md`; working examples in `examples/`). Rules that matter:
   - Use `flow` elements for sequences and `%` units for free elements (`"x": "50%"`). Then the same spec fits every canvas. Raw pixel positions only fit the canvas they were drawn for; set `"responsive": false` on such specs.
   - Silent video: give each scene a `duration` (seconds, 6-10) and put the message on screen. Narrated: write `narration` as spoken text, numbers as words, no markdown. `at` (0-1) sets when an element appears; order elements to match the sentences.
   - Colour carries meaning: pick one colour per concept (`cyan`, `coral`, `yellow`, `orange`, `green`) and keep it across scenes. Use `hl` to pulse the element being discussed.
   - Fill `video.author` (`name`, `handle`, `avatar` image path) and `video.watermark` (`text`, `image`, `opacity`) with the user's details. Without an avatar a placeholder with initials is drawn; use `"avatar": false` to hide it.
4. **Validate and look.** `python3 scripts/evideo.py validate spec.json --canvas linkedin` for every canvas they will export, fix each warning, then `still spec.json --scene all --out s.png --fast` and open the images. Check the end state of each scene. Cheap to fix here, expensive after a render.
5. **Audio** (only if wanted):
   - `--audio none`: nothing to do. Scenes use `duration`; narration, if present, shows as captions.
   - `--audio files`: `evideo.py narration spec.json` writes `narration/<scene-id>.txt`; the user makes `audio/<scene-id>.mp3` with any TTS.
   - `--audio generate --tts narrate-your-writing --narrate-path /path/to/narrate.py` (Kokoro, local), or `--tts edge`, `--tts piper`, or `--tts command --tts-cmd '...'`. See `recipes/tts.md`. Engines run on the user's machine; this sandbox usually cannot download voice models.
6. **Render.**
   - `evideo.py render spec.json --out out/video.mp4` (add `--captions` for muted-autoplay feeds)
   - `... --out out/loop.gif` (loops, 15 fps, half size) / `--out out/flow.png --scene 1` / `--out out/carousel.pdf`
   - `--fast` for drafts, `--scale 1.5` for larger output, `--canvas a,b,c` for several shapes.
   A `.srt` caption file is written next to any narrated mp4.
7. **Deliver** the file(s) with one line on size and length, and offer the post copy. The user's voice must be theirs: never clone someone else's voice.

## Pitfalls

- `validate` flags digits in narration, text that overflows its box, elements outside the safe area, and overlaps. Treat warnings as bugs.
- A mp4 with narration and no audio stops with the three options; do not silently drop to silent.
- `sketch` draws boxes stroke by stroke; keep text short because handwriting fonts read smaller.
- GIFs get large fast: keep them under 15 s and use the default half size.
- Custom fonts: `video.fonts` (`regular`, `bold`, `hand`), paths relative to the spec.
- Run the repo's `python3 tests/smoke.py` after changing `scripts/evideo.py`.
