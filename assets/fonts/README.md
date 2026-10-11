# Bundled fonts

These fonts ship with the repo so videos look the same on every machine. All are licensed under the
SIL Open Font License 1.1 (full text in the `OFL-*.txt` files next to them).

| File | Family | Used for |
|---|---|---|
| `Inter-Regular.ttf`, `Inter-Bold.ttf` | Inter (The Inter Project Authors) | `clean` style |
| `PatrickHand-Regular.ttf` | Patrick Hand (Patrick Wagesreiter) | `sketch` style, default |
| `Caveat-Bold.ttf` | Caveat (The Caveat Project Authors) | optional alternative hand font |

Use your own fonts with `video.fonts` in the spec, for example
`{"fonts": {"regular": "MyFont-Regular.ttf", "bold": "MyFont-Bold.ttf", "hand": "assets/fonts/Caveat-Bold.ttf"}}`.
Paths are relative to the spec file.
