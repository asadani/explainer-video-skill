# Contributing

Thanks for helping. Good first contributions:

- **TTS recipes**: a short doc or script showing how to turn `narration/*.txt` into `audio/<scene-id>.mp3` with a specific engine (XTTS, ElevenLabs, OpenAI, ...). Put them in `recipes/`, or add a preset to `generate_audio` in `scripts/evideo.py`.
- **Example specs**: real topics, any canvas. Put them in `examples/`; use `flow` and `%` units so they fit every canvas (otherwise set `"responsive": false`).
- **Element types**: add a `draw_*` function in `scripts/evideo.py`, register it in `ELEMENT_TYPES`, `REQUIRED` and `bbox()`, support both styles (`clean` and `sketch`), and document it in `references/spec.md`.
- **Themes, canvas presets, fonts** (OFL or similar only, include the licence), **transitions**.

## Ground rules

- Keep the dependency set to Python standard library, Pillow, numpy and ffmpeg.
- Run `python3 -I tests/smoke.py` before opening a pull request. It must print "all passed".
- If you change drawing code, attach before and after stills (ideally a portrait and a landscape one).
- Do not add voice-cloning samples of people who have not consented, or content and branding copied from other creators.

By contributing you agree your work is released under the MIT licence.
