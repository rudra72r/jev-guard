# Brand assets

| file | what it's for | size |
|---|---|---|
| `logo.svg` | the mark on its own — favicon, avatars, docs site | 64×64 |
| `banner.svg` | README header | 1280×320 |
| `demo.svg` | animated terminal, the real offline transcript; CSS keyframes, no JS | 900×496 |
| `social-preview.svg` / `.png` | GitHub Settings → Social preview, and link cards on LinkedIn, X and Slack | 1280×640 |

The palette is the product: **allow** `#34D399`, **review** `#FBBF24`, **block** `#F87171`,
on `#0B0E14` with `#8B5CF6` for Jev. The mark is three bars — a probability distribution,
and a gate.

`demo.svg` is generated, so the transcript can't drift from reality: regenerate it with
`python scripts/make_demo.py` after re-running `examples/00_try_it_offline.py`.

MIT, same as the code. Please don't use the mark to imply the project endorses something.
