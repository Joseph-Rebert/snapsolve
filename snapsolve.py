#!/usr/bin/env python3
"""snapsolve: screenshot -> Claude -> answer card (with a graph when useful).

Usage:
  snapsolve.py            capture the main display
  snapsolve.py --select   drag to select a region first
"""
import base64
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HOME = Path(__file__).resolve().parent
OUT = HOME / "out"
LOG = HOME / "log.txt"
MODEL = "claude-opus-5-5"

SYSTEM = """You are looking at a screenshot of the user's screen. Somewhere on it is a problem
they want solved (math, physics, chemistry, a homework/quiz question, code question, etc.).
If several problems are visible, solve the one that is most prominent / most clearly the focus.

Return ONLY the final answer. Never explain, never show steps, never add words around it,
even if the question asks "explain" or "show your work". Be as short as possible:
"x = 4", "B) 42 J", "12.5 m/s", "True". For multiple choice give the letter and choice text.
For multi-part problems put each part on its own line, e.g. "a) 4" newline "b) 7".
When you include a graph, the graph IS the answer: the text should be just the key values asked
for (e.g. "x = 1, 3"), or an empty string if the sketch alone answers it.

Write math in plain Unicode text (x², √2, π, ≤, ∫, →, θ), never LaTeX or $ signs.

Set graph.needed = true when the answer is a graph/sketch, or when a graph is genuinely the
clearest way to show the answer (e.g. "sketch f(x)", "graph the inequality", "find the
intersection"). Otherwise graph.needed = false and leave the lists empty.

Graph curves are parametric: x_expr and y_expr are Python/numpy expressions in the variable t,
using np (e.g. "np.sin(t)", "t**2 - 3", "np.exp(-t)"). For an ordinary function y=f(x) use
x_expr="t", y_expr="<f(t)>", with t_min/t_max = the x-window. Use np.nan to break curves
(e.g. near vertical asymptotes: "np.where(abs(t-2)<0.02, np.nan, 1/(t-2))").
Use points for intercepts, vertices, intersections, extrema worth labelling.
Use vlines/hlines (dashed) for asymptotes. Pick a window that shows the important features."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "graph": {
            "type": "object",
            "properties": {
                "needed": {"type": "boolean"},
                "title": {"type": "string"},
                "x_min": {"type": "number"},
                "x_max": {"type": "number"},
                "y_min": {"type": "number"},
                "y_max": {"type": "number"},
                "curves": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "x_expr": {"type": "string"},
                            "y_expr": {"type": "string"},
                            "t_min": {"type": "number"},
                            "t_max": {"type": "number"},
                        },
                        "required": ["label", "x_expr", "y_expr", "t_min", "t_max"],
                        "additionalProperties": False,
                    },
                },
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "label": {"type": "string"},
                        },
                        "required": ["x", "y", "label"],
                        "additionalProperties": False,
                    },
                },
                "vlines": {"type": "array", "items": {"type": "number"}},
                "hlines": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["needed", "title", "x_min", "x_max", "y_min", "y_max",
                         "curves", "points", "vlines", "hlines"],
            "additionalProperties": False,
        },
    },
    "required": ["answer", "graph"],
    "additionalProperties": False,
}


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def notify(text, title="SnapSolve"):
    text = text.replace('"', "'")[:200]
    subprocess.run(["/usr/bin/osascript", "-e",
                    f'display notification "{text}" with title "{title}"'])


def load_key():
    env = HOME / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")


PENDING = OUT / "pending"


def capture(select: bool, shot: Path | None = None) -> Path | None:
    OUT.mkdir(exist_ok=True)
    shot = shot or OUT / "shot.png"
    shot.unlink(missing_ok=True)
    args = ["/usr/sbin/screencapture", "-x"] + (["-i"] if select else ["-m"]) + [str(shot)]
    subprocess.run(args)
    if not shot.exists():  # user pressed Esc during selection
        return None
    # Downscale retina captures so the request is smaller/faster; text stays legible.
    subprocess.run(["/usr/bin/sips", "--resampleHeightWidthMax", "2000", str(shot)],
                   capture_output=True)
    return shot


def pending_shots() -> list[Path]:
    """Screens saved with --add in the last 10 minutes, oldest first."""
    if not PENDING.exists():
        return []
    shots = sorted(PENDING.glob("*.png"))
    fresh = [p for p in shots if time.time() - p.stat().st_mtime < 600]
    for p in set(shots) - set(fresh):
        p.unlink()
    return fresh


def ask_claude(shots: list[Path]) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    content = []
    for shot in shots:
        img = base64.standard_b64encode(shot.read_bytes()).decode()
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img}})
    if len(shots) > 1:
        prompt = (f"These {len(shots)} screenshots are consecutive parts of the same page, "
                  "in order from top to bottom (the user scrolled between them; parts may "
                  "overlap). Treat them as one page and solve the problem on it.")
    else:
        prompt = "Solve the problem on my screen."
    content.append({"type": "text", "text": prompt})
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": content,
        }],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined this one.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Ran out of tokens before answering.")
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def safe_eval(expr: str, t):
    import numpy as np
    return eval(expr, {"__builtins__": {}}, {"np": np, "t": t, "pi": np.pi, "e": np.e})


def render(result: dict) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams["text.usetex"] = False
    plt.rcParams["text.parse_math"] = False  # treat $ literally
    plt.rcParams["font.family"] = ["Helvetica Neue", "Arial Unicode MS", "DejaVu Sans"]

    import matplotlib.patheffects as pe

    answer = result["answer"].strip()
    g = result["graph"]
    has_graph = bool(g["needed"] and g["curves"])
    if not answer and not has_graph:
        answer = "?"

    fg = "white"
    halo = [pe.withStroke(linewidth=3, foreground=(0, 0, 0, 0.6))]
    lines = answer.count("\n") + 1 if answer else 0
    text_h = (0.35 + 0.5 * lines) if answer else 0
    graph_h = 4.2 if has_graph else 0
    width = 6.0 if has_graph else min(7.0, max(1.6, 0.2 * max(len(l) for l in answer.split("\n"))))
    fig = plt.figure(figsize=(width, max(text_h + graph_h, 0.6)), dpi=150)
    fig.patch.set_alpha(0)

    if answer:
        top = 1 - 0.1 / fig.get_figheight()
        fig.text(0.02 if has_graph else 0.5, top, answer, va="top",
                 ha="left" if has_graph else "center", fontsize=24, fontweight="bold",
                 color=fg, path_effects=halo)

    if has_graph:
        frac = graph_h / fig.get_figheight()
        ax = fig.add_axes([0.08, 0.06 * frac, 0.9, frac * 0.9])
        ax.patch.set_alpha(0)
        colors = ["#4fc3f7", "#ffb74d", "#81c784", "#f06292", "#ba68c8"]
        for i, c in enumerate(g["curves"]):
            t = np.linspace(c["t_min"], c["t_max"], 2000)
            try:
                x = np.broadcast_to(safe_eval(c["x_expr"], t), t.shape)
                y = np.broadcast_to(safe_eval(c["y_expr"], t), t.shape)
                ax.plot(x, y, lw=2.6, color=colors[i % len(colors)])
            except Exception as ex:
                log(f"curve failed {c}: {ex}")
        for v in g["vlines"]:
            ax.axvline(v, ls="--", color=(1, 1, 1, 0.6), lw=1.2)
        for h in g["hlines"]:
            ax.axhline(h, ls="--", color=(1, 1, 1, 0.6), lw=1.2)
        for p in g["points"]:
            ax.plot(p["x"], p["y"], "o", color="#ff5252", ms=7, zorder=5)
            if p["label"]:
                ax.annotate(p["label"], (p["x"], p["y"]), textcoords="offset points",
                            xytext=(6, 6), fontsize=11, color=fg, path_effects=halo)
        ax.axhline(0, color=(1, 1, 1, 0.8), lw=1)
        ax.axvline(0, color=(1, 1, 1, 0.8), lw=1)
        ax.grid(color=(1, 1, 1, 0.15))
        ax.tick_params(colors=fg, labelsize=10)
        for s in ax.spines.values():
            s.set_visible(False)
        if g["x_min"] < g["x_max"]:
            ax.set_xlim(g["x_min"], g["x_max"])
        if g["y_min"] < g["y_max"]:
            ax.set_ylim(g["y_min"], g["y_max"])

    OUT.mkdir(exist_ok=True)
    path = OUT / f"answer-{time.time_ns()}.png"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.25, transparent=True)
    plt.close(fig)
    return path


PIDFILE = HOME / "out" / "popup.pid"


def show(path: Path):
    """Borderless, see-through floating window. Click it or press Esc to close."""
    import signal
    import AppKit
    import objc

    # Close any popup left over from a previous run.
    try:
        os.kill(int(PIDFILE.read_text()), signal.SIGTERM)
    except Exception:
        pass
    PIDFILE.write_text(str(os.getpid()))

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    # Non-activating panel: never steals keyboard focus, so you can keep typing
    # in your browser while it stays on screen. Click it to close.
    class Popup(AppKit.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

        def sendEvent_(self, event):
            if event.type() in (AppKit.NSEventTypeLeftMouseDown, AppKit.NSEventTypeRightMouseDown):
                app.terminate_(None)
                return
            objc.super(Popup, self).sendEvent_(event)

    img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
    rep = img.representations()[0]
    w, h = rep.pixelsWide() * 80 / 150, rep.pixelsHigh() * 80 / 150  # rendered at 150 dpi
    vis = AppKit.NSScreen.mainScreen().visibleFrame()
    w, h = min(w, vis.size.width * 0.5), min(h, vis.size.height * 0.8)
    x = vis.origin.x + vis.size.width - w - 24
    y = vis.origin.y + vis.size.height - h - 24

    win = Popup.alloc().initWithContentRect_styleMask_backing_defer_(
        ((x, y), (w, h)), AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
        AppKit.NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(AppKit.NSColor.clearColor())
    win.setHasShadow_(False)
    win.setLevel_(AppKit.NSStatusWindowLevel)
    win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                               | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)

    bg = AppKit.NSBox.alloc().initWithFrame_(((0, 0), (w, h)))
    bg.setBoxType_(AppKit.NSBoxCustom)
    bg.setTitlePosition_(AppKit.NSNoTitle)
    bg.setBorderWidth_(0)
    bg.setCornerRadius_(14)
    bg.setContentViewMargins_((0, 0))
    bg.setFillColor_(AppKit.NSColor.colorWithWhite_alpha_(0, 0.35))
    iv = AppKit.NSImageView.alloc().initWithFrame_(((0, 0), (w, h)))
    iv.setImage_(img)
    iv.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
    bg.addSubview_(iv)
    win.setContentView_(bg)

    win.setHidesOnDeactivate_(False)
    win.setFloatingPanel_(True)
    win.orderFrontRegardless()
    app.run()


def close_popup():
    try:
        os.kill(int(PIDFILE.read_text()), 15)
    except Exception:
        pass


def main():
    if "--close" in sys.argv:
        close_popup()
        return
    load_key()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        notify(f"No API key. Put ANTHROPIC_API_KEY=... in {HOME}/.env")
        return
    select = "--select" in sys.argv
    if "--add" in sys.argv:
        # Save this screen as one part of a longer page; solve later with ⌥⇧A / ⌥⇧X.
        PENDING.mkdir(parents=True, exist_ok=True)
        n = len(pending_shots()) + 1
        if capture(select, PENDING / f"{time.time_ns()}.png"):
            notify(f"Part {n} saved. Scroll down and add more, or solve.")
        return
    if "--clear" in sys.argv:
        for p in pending_shots():
            p.unlink()
        notify("Saved parts cleared.")
        return
    shot = capture(select)
    if shot is None:
        return
    shots = pending_shots() + [shot]
    notify("Solving…" if len(shots) == 1 else f"Solving ({len(shots)} parts)…")
    try:
        result = ask_claude(shots)
        for p in shots[:-1]:
            p.unlink()
        log(f"answer: {result['answer']!r} graph={result['graph']['needed']}")
        subprocess.run(["/usr/bin/pbcopy"], input=result["answer"].encode())
        show(render(result))
    except Exception as ex:
        log(traceback.format_exc())
        notify(f"Failed: {ex}")


if __name__ == "__main__":
    main()
