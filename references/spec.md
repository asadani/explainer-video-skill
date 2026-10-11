# Spec reference (v0.2)

One JSON file: `video` settings, an optional `theme` override, and `scenes`.

```json
{
  "video": {"canvas": "linkedin", "style": "sketch", "theme": "dark", "audio": "none",
            "author": {"name": "Your Name", "handle": "@you", "avatar": "me.png"},
            "watermark": {"text": "yoursite.dev", "opacity": 0.3}},
  "scenes": [
    {"id": "intro", "heading": "the problem", "duration": 7,
     "elements": [{"type": "flow", "nodes": [{"title": "Question"}, {"title": "Answer"}]}]}
  ]
}
```

Set `"responsive": false` at the top level for specs that use fixed pixel positions (they are only checked on their own canvas by `tests/smoke.py`).

## `video`

| field | default | meaning |
|---|---|---|
| `canvas` | `landscape` | `landscape` 1600x900, `square` 1080x1080, `portrait` 1080x1350 (4:5), `story` 1080x1920 (9:16), or `[w, h]`. Aliases: `linkedin`, `feed` -> portrait; `youtube` -> landscape; `reels`, `shorts`, `vertical` -> story |
| `style` | `clean` | `clean` (flat, Inter font) or `sketch` (wobbly double-stroke lines, boxes drawn stroke by stroke, handwriting font, no step tabs) |
| `theme` | `light` (`dark` for sketch) | `light`, `warm`, `dark`, or an object of colours (see below) |
| `audio` | `auto` | `auto`, `none`, `files`, `generate` (the `--audio` flag overrides) |
| `captions` | on when there is no audio | burn narration text into the video (`--captions/--no-captions` overrides) |
| `caption_style` | `{}` | `{"box": true/false, "size": 26}`; the box is on for `clean`, off for `sketch` |
| `tts` | `{}` | `{"engine": "narrate-your-writing", "voice": "bm_george+bm_fable", "speed": 0.9, "narrate_path": "...", "python": "...", "command": "...", "ext": ".mp3", "stdin": false}` |
| `author` | none | text, or `{"name", "handle", "avatar", "position"}`. `position`: `top-right` (sketch default), `top-left`, `bottom-left` (clean default), `bottom-right`, `none`. Dict form without `avatar` draws a placeholder with initials; `"avatar": false` hides it |
| `watermark` | none | text, or `{"text", "image", "opacity" 0.3, "position" bottom-right, "size", "image_size", "color"}`. `position: "center"` makes it large |
| `kicker`, `footer_right` | none | small label top left / footer text bottom right (clean) |
| `tabs`, `dots` | on for clean (dots only with 2+ scenes) | STEP tabs row (scenes with `step`) and progress dots |
| `lead`, `tail` | 0.5, 0.9 | seconds before and after each scene's content |
| `fonts` | bundled | `{"regular": "a.ttf", "bold": "b.ttf", "hand": "c.ttf"}`, paths relative to the spec |

## Themes

Override any of `bg bg2 ink muted accent accent_soft card line warn blue white cyan coral yellow orange green` with hex colours, either as `video.theme` (an object) or top-level `theme`. Elements accept these names or raw hex.

## `scenes[]`

| field | meaning |
|---|---|
| `id` | file stem for audio (`audio/<id>.mp3`); default `scene-NN`. Must be unique |
| `heading`, `sub` | clean: large title and subtitle. sketch: small yellow title at the top centre (`sub` unused) |
| `step`, `tag` | clean only: tab label and top-right label |
| `narration` | spoken text; optional. Numbers as words |
| `duration` | seconds of content when there is no audio clip (default: estimated from narration, else 6) |
| `elements` | a list, or an object keyed by canvas (`landscape`, `square`, `portrait`, `story`, plus `wide`, `tall`, `default`) to hand-place a different layout per shape |

Scene length = `lead` + content + `tail`. Content is the audio clip if one exists.

## Units

Numbers are canvas units (see sizes above). Strings like `"50%"` are a share of the canvas width (`x`, `w`, `r`) or height (`y`, `h`), so `{"x": "50%", "align": "center"}` is centred on every canvas.

## Elements

Common: `type`, `at` (0-1, fraction of the scene content when it appears, default 0), `dur` (animate-in seconds), `hl` (`[from, to]` pulse window).

**flow** - nodes joined by arrows, auto-laid-out: a row on wide canvases, a column on tall ones.
`nodes` (each takes box fields plus `arrow_label` for the arrow to the next node), `arrows` (true), `direction` (`auto`, `row`, `column`), `loop` (true draws a return arrow), `loop_label`, `loop_color`, `span` (`[0.04, 0.82]`: window in which nodes appear one by one), `color`, `node_w`, `node_h`, `size`, and optional `x y w h` to place it in a region.

**box** - card with title and subtitle: `x y w h`, `title`, `sub`, `style` (`default accent dark muted cyan coral yellow orange green blue`), `dashed`, `size`, `sub_size`, `radius`.

**text** - `x y text`, `size`, `bold`, `color`, `align` (`left center right`), `w` (wrap), `\n` breaks lines, `mark`: `{"kind": "strike" | "underline", "at": 0.8, "color": "coral"}` draws a line across the text at that moment.

**arrow** - `points` `[[x,y],...]`, `color`, `width`, `head`, `label`, `label_size`, `dashed`. Draws itself.

**circle** - `x y` (centre), `r`, `style` (colour), `fill` (true), `label`.

**grid** - rows of cells for tokens, chunks, vectors: `x y cols rows`, `cell`, `gap`, `color`, `label`, `seed`, `lit` (`[from, to]` lights cells one by one).

**pill** - `x y text`, `size`, `color`, `filled`.

**image** - `x y w h path` (relative to the spec), `round` (circular crop), `opacity`.

## Layout guide

- The header takes the top of the canvas; the validator tells you the safe area for each scene (`validate` prints it when something is outside).
- Prefer `flow` for sequences; use `text` at `"50%"` positions for takeaways (for example `"y": "88%"`).
- Keep six or fewer elements per scene; reveal a one-line takeaway last (`at` 0.85-0.9).
- Tall canvases: stack things vertically. Hand-placed pixel layouts need a per-canvas `elements` object.

## Timing

- `at` follows the sentences: with five sentences, natural beats sit near 0.0, 0.2, 0.4, 0.6, 0.8.
- Let an arrow start slightly after the thing it leaves, and the next box when the arrow is about half drawn (flow does this for you).
- `hl` windows of 0.2-0.25 work well.

## CLI

```
evideo.py validate  SPEC [--canvas C] [--style S] [--theme T] [--captions]
evideo.py narration SPEC [--out narration]
evideo.py audio     SPEC [--tts ENGINE] [--tts-cmd CMD] [--narrate-path P] [--tts-python PY] [--voice V] [--speed X] [--audio-dir audio] [--force]
evideo.py still     SPEC [--scene N|all] [--at 0..1] [--out still.png] [--canvas C] [--style S] [--theme T] [--fast]
evideo.py render    SPEC [--out FILE.mp4|gif|png|pdf] [--audio none|files|generate|auto] [--captions|--no-captions]
                         [--canvas a,b,c] [--style S] [--theme T] [--scene N|all] [--at 0..1]
                         [--scale X] [--fps N] [--crf N] [--fast] [--ss 1|2] [--workers N] [--audio-dir audio]
```

`--canvas`, `--style` and `--theme` override the spec, so one file can produce every variant.
