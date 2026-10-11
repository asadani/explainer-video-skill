# Voice recipes

The renderer needs one audio file per scene: `audio/<scene-id>.mp3` (or `.wav .m4a .flac .ogg`). Anything that can turn
`narration/<scene-id>.txt` into that works. `evideo.py audio` automates the common engines; run it on your own machine
(this repo does not download voice models).

```bash
python scripts/evideo.py narration spec.json      # writes narration/<scene-id>.txt
python scripts/evideo.py audio spec.json --tts ...  # writes audio/<scene-id>.*
python scripts/evideo.py render spec.json --audio files --captions --out out/video.mp4
# or all in one:
python scripts/evideo.py render spec.json --audio generate --tts ... --out out/video.mp4
```

## Kokoro (local, free) via narrate-your-writing

[narrate-your-writing](https://github.com/asadani/narrate-your-writing) turns a folder of `.txt` files into `.mp3`
files with Kokoro (CPU or CUDA) and handles acronyms, pauses and chunk caching.

```bash
python scripts/evideo.py audio spec.json --tts narrate-your-writing \
    --narrate-path ../narrate-your-writing/narrate.py --voice "bm_george+bm_fable" --speed 0.9
```

`--tts kokoro` and `--tts nyw` are aliases. If narrate-your-writing lives in a different Python environment (for
example conda with `kokoro-onnx` installed), point at its interpreter: `--tts-python /path/to/env/python`. You can set
these once in the spec: `"video": {"tts": {"engine": "narrate-your-writing", "narrate_path": "...", "voice": "..."}}`.

## edge-tts (online, many voices)

```bash
pip install edge-tts
python scripts/evideo.py audio spec.json --tts edge --voice en-US-AriaNeural --speed 0.95
```

## Piper (local, fast, CPU)

```bash
python scripts/evideo.py audio spec.json --tts piper --voice /path/to/en_US-lessac-medium.onnx
```

## Any other engine (custom command)

Placeholders: `{text_file}` (path to the scene's text), `{text}` (the text itself), `{out}` (where to write),
`{voice}`, `{speed}`, `{rate}` (such as `-10%`).

```bash
python scripts/evideo.py audio spec.json --tts command --tts-cmd "mytts --in {text_file} --out {out} --voice {voice}"
```

Set `"ext": ".wav"` under `video.tts` if your engine writes WAV, and `"stdin": true` to feed the text on standard input.

## Your own recording

Record each scene, name the files `<scene-id>.m4a` (or any supported extension) and use `--audio files`.

Do not clone a voice without the speaker's consent.
