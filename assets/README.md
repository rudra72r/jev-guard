# Brand assets

| file | what it's for | size |
|---|---|---|
| `logo.svg` | the mark on its own — favicon, avatars, docs site | 64×64 |
| `banner.svg` | README header | 1280×320 |
| `demo.svg` | animated terminal, the real offline transcript; CSS keyframes, no JS | 900×496 |
| `social-preview.svg` / `.png` | GitHub Settings → Social preview, and link cards on LinkedIn, X and Slack | 1280×640 |

A technical specimen sheet: paper, hairline rules, registration marks, monospace. Colour is
reserved for data — `#C82828` critical, `#B07000` high, `#14794E` allow — on `#FFFFFF` with
`#12120F` ink. Nothing is tinted for decoration.

The mark is the product in one glyph: three measured values against a single threshold line.
Two cross it, one doesn't.

`demo.svg` is generated, so the transcript can't drift from reality: regenerate it with
`python scripts/make_demo.py` after re-running `examples/00_try_it_offline.py`.

MIT, same as the code. Please don't use the mark to imply the project endorses something.
