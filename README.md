# SnapSolve

Press a hotkey, and SnapSolve screenshots your screen, sends it to Claude, and shows the answer in
a see-through floating window: just the answer, or a sketch when the answer is a graph.

macOS only.

## Setup

```sh
git clone https://github.com/Joseph-Rebert/snapsolve ~/snapsolve
cd ~/snapsolve
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then paste your Anthropic API key into .env
```

Hotkeys use [skhd](https://github.com/koekeishiya/skhd) (`brew install koekeishiya/formulae/skhd`).
Append `skhdrc.example` to `~/.config/skhd/skhdrc` and run `skhd --reload`.
Give skhd **Screen Recording** permission in System Settings → Privacy & Security.

## Hotkeys

| Keys | Action |
|------|--------|
| ⌥⇧A | Screenshot the main display and solve |
| ⌥⇧X | Drag to select a region, then solve |
| ⌥⇧D | Save this screen as one part of a long page (scroll, repeat, then ⌥⇧A) |
| ⌥⇧C | Clear saved parts |
| ⌥⇧Z | Close the answer popup (or click it) |

The popup never takes keyboard focus, so you can keep typing while it's up. The answer is also
copied to the clipboard.

## How it works

- `screencapture` takes the screenshot, and `sips` downscales it.
- Claude (`claude-opus-5-5`) gets the image(s) and returns structured JSON: an answer string plus
  an optional graph spec (parametric numpy expressions, labelled points, asymptotes).
- matplotlib renders the answer onto a transparent PNG.
- A PyObjC non-activating `NSPanel` shows it in the top-right corner.

Graph expressions come from the model and are evaluated locally with `eval` (builtins disabled).

Errors are logged to `log.txt`. Answer images are saved in `out/`.
