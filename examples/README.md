# Examples

| File | Look | Audio | Shows |
|---|---|---|---|
| `mcp-explained-sketch.json` | dark, hand-drawn, vertical 9:16 | none (text on screen) | `flow`, loop, dashed box, strike-through, author, avatar placeholder, watermark. Works on every canvas |
| `agent-loop-flow.json` | warm, clean, LinkedIn 4:5 | none | a single arrow-flow scene for a GIF or PNG |
| `rag-pipeline.json` | light, clean, landscape | narrated | fixed positions, grids, highlights, step tabs, burned-in captions |

Try them (from the repo root; `--fast` is for quick drafts):

```bash
python scripts/evideo.py render examples/mcp-explained-sketch.json --out out/mcp.mp4 --fast
python scripts/evideo.py render examples/mcp-explained-sketch.json --canvas linkedin,landscape --theme warm --style clean --out out/mcp.mp4
python scripts/evideo.py render examples/agent-loop-flow.json --out out/loop.gif
python scripts/evideo.py render examples/agent-loop-flow.json --out out/flow.png
python scripts/evideo.py render examples/mcp-explained-sketch.json --canvas linkedin --out out/carousel.pdf
python scripts/evideo.py render examples/rag-pipeline.json --audio none --out out/rag-silent.mp4   # captions burned in
```

Replace the placeholder author (`Your Name`, `@yourhandle`), add `"avatar": "me.png"` next to the spec, and change the
watermark text to your own.
