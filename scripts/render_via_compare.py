"""Side-by-side comparison video: ARGO desktopweb vs ARGO+jev, same VIA start point.

Takes the representative run (median successful run) from each arm in a
`bench_via_argo.py` output directory and renders them synchronized to the
same T0 (the moment the utterance was submitted to VIA), at original speed,
with a shared HUD. See docs/via-argo-bench-design.md §6.

Usage:
  uv run python scripts/render_via_compare.py artifacts/via_bench/<timestamp>
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.via_bench_lib import clean_argo_steps, clean_jev_steps, load_events, markdown_table, stages  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
kr_path = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
mono_path = "/System/Library/Fonts/Menlo.ttc"
ink, muted, green, red, gray = "#172a20", "#6a766c", "#2a743f", "#b3452f", "#9aa295"
highlight_bg, highlight_border = "#eaf3e2", "#8fb37e"


def kr(n, bold=False):
    return ImageFont.truetype(kr_path, n, index=1 if bold else 0)


def mono(n):
    return ImageFont.truetype(mono_path, n)


def wrap_text(draw, text, font, max_width, max_lines=2):
    """Greedy word-wrap (space-delimited; Korean has no inter-word breaks
    finer than that without a real line-breaker, which is fine at this font
    size). Truncates with an ellipsis past `max_lines`."""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines:
        while lines and draw.textlength(lines[-1] + "…", font=font) > max_width and len(lines[-1]) > 1:
            lines[-1] = lines[-1][:-1]
        if " ".join(words) != " ".join(lines):
            lines[-1] = lines[-1].rstrip() + "…"
    return lines or [""]


def extract_frames(video_path, out_dir, fps=30):
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path), "-vf", f"fps={fps}", str(out_dir / "%05d.png")],
        check=True,
    )
    return sorted(out_dir.glob("*.png"))


def load_panel(run_dir, crop_scale, window_w=1280, window_h=860, window_x=0, window_y=40):
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    events = load_events(run_dir / "events.jsonl")
    st = stages(events)
    frames = extract_frames(run_dir / "screen.mp4", run_dir / "frames")
    crop_box = (
        int(window_x * crop_scale),
        int(window_y * crop_scale),
        int((window_x + window_w) * crop_scale),
        int((window_y + window_h) * crop_scale),
    )
    arm = run["arm"]
    steps = clean_argo_steps(events) if arm == "argo" else clean_jev_steps(events)
    return {
        "run": run,
        "events": events,
        "stages": st,
        "frames": frames,
        "crop_box": crop_box,
        "arm": arm,
        "steps": steps,
    }


def frame_at(panel, t_ms, fps=30):
    idx = max(0, round(t_ms * fps / 1000))
    idx = min(idx, len(panel["frames"]) - 1)
    if idx < 0:
        return None
    im = Image.open(panel["frames"][idx]).convert("RGB")
    return im.crop(panel["crop_box"])


def draw_side(canvas, x, panel, t_ms, w, label, total_end_ms):
    d = ImageDraw.Draw(canvas)
    d.text((x, 8), label, font=kr(28, True), fill=ink)
    # Freeze this side's own clock at ITS OWN completion time, not the
    # shared video clock -- jev finishing early and then still ticking up
    # alongside ARGO for another minute misrepresented how fast it actually
    # was (reported live, 2026-09-27).
    total_ms = panel["stages"].get("total_ms")
    finished = total_ms is not None and t_ms >= total_ms
    shown_ms = total_ms if finished else t_ms
    d.text((x + w - 210, 4), f"{shown_ms / 1000:05.2f} s", font=mono(40), fill=green if finished else ink)

    screenshot_y = 78
    frame = frame_at(panel, t_ms)
    if frame is not None:
        canvas.paste(frame.resize((w - 20, 780)), (x, screenshot_y))

    # --- Step feed: a dedicated panel below the screenshot, never overlaid
    # on the live screen (an earlier version overlaid it on the screenshot
    # itself and it collided with the real browser's own tab bar). ---
    feed_y = screenshot_y + 780 + 14
    feed_h = 300
    d.rounded_rectangle((x, feed_y, x + w - 20, feed_y + feed_h), radius=10, fill="#fbfbf8", outline="#e3e5da")
    d.text((x + 16, feed_y + 10), "모델 호출 로그", font=kr(16, True), fill=muted)

    visible = [s for s in panel["steps"] if s["t_ms"] <= t_ms]
    inner_w = w - 20 - 32
    y = feed_y + 42
    if visible:
        latest = visible[-1]
        badge = "🧠 방금 모델 호출" if not latest["tool"] else f"🧠 모델 호출 → 🔧 {latest['tool']}"
        d.rounded_rectangle((x + 16, y, x + 16 + d.textlength(badge, font=kr(14, True)) + 20, y + 26), radius=13,
                             fill=highlight_border)
        d.text((x + 26, y + 4), badge, font=kr(14, True), fill="white")
        y += 34
        lines = wrap_text(d, latest["text"], kr(20, True), inner_w, max_lines=2)
        d.rounded_rectangle((x + 16, y, x + w - 36, y + 24 * len(lines) + 16), radius=8, fill=highlight_bg)
        ty = y + 8
        for line in lines:
            d.text((x + 26, ty), line, font=kr(20, True), fill=ink)
            ty += 24
        y = ty + 14

        for s in reversed(visible[:-1][-3:]):
            line = wrap_text(d, s["text"], kr(15), inner_w, max_lines=1)[0]
            tag = f"🔧 {s['tool']}  " if s["tool"] else ""
            d.text((x + 16, y), f"{tag}{line}", font=kr(15), fill=muted)
            y += 22
    else:
        d.text((x + 16, y), "대기 중…", font=kr(15), fill=muted)

    if finished:
        run = panel["run"]
        status = "완료 (검증됨)" if run["passed"] else "완료 (검증 실패)"
        bw = d.textlength(status, font=kr(17, True)) + 28
        bx = x + w - 20 - bw
        by = feed_y + feed_h - 40
        d.rounded_rectangle((bx, by, x + w - 20, by + 32), radius=8, fill="#dfeeda" if run["passed"] else "#f3ddd6")
        d.text((bx + 14, by + 6), status, font=kr(17, True), fill=green if run["passed"] else red)

    bar_y = feed_y + feed_h + 14
    d.line((x, bar_y, x + w - 20, bar_y), fill="#d3d9cc", width=2)
    if total_end_ms:
        frac = min(1.0, t_ms / total_end_ms)
        d.line((x, bar_y, x + (w - 20) * frac, bar_y), fill=green, width=3)


def render(bench_dir, out_stub, fps=30, crop_scale=2.0):
    bench_dir = Path(bench_dir)
    summary = json.loads((bench_dir / "summary.json").read_text())["summary"]
    argo_dir = summary["argo"]["representative_run_dir"]
    jev_dir = summary["jev"]["representative_run_dir"]
    if not argo_dir or not jev_dir:
        raise SystemExit("no successful representative run for one or both arms -- check run.json/summary.json")

    panel_w = 1280
    left = load_panel(argo_dir, crop_scale)
    right = load_panel(jev_dir, crop_scale)
    end_ms = max(left["stages"]["total_ms"] or 0, right["stages"]["total_ms"] or 0) + 1500

    canvas_w, canvas_h = 2 * panel_w + 40, 1268
    out_frames = bench_dir / "compare-frames"
    out_frames.mkdir(parents=True, exist_ok=True)
    n_frames = round(end_ms * fps / 1000)
    for i in range(n_frames):
        t_ms = round(i * 1000 / fps)
        canvas = Image.new("RGB", (canvas_w, canvas_h), "#f3f4ec")
        d = ImageDraw.Draw(canvas)
        title = '🎤 "네이버에서 서울 날씨 검색해줘"  →  VIA (같은 Evaluator · 같은 Router · 같은 lane)'
        d.text((20, 8), title, font=kr(18, True), fill=ink)
        d.text((20, 32), "왼쪽: ARGO desktopweb (현재)   |   오른쪽: ARGO + jev (융합)", font=kr(14), fill=muted)

        for x, panel, label in ((20, left, "ARGO desktopweb"), (panel_w + 40, right, "ARGO + jev")):
            draw_side(canvas, x, panel, t_ms, panel_w, label, end_ms)

        d.text(
            (20, canvas_h - 22),
            "같은 Mac · 같은 Chrome 프로필 · 같은 네트워크 · 순차 실행(A/B 교대) · 원 속도",
            font=kr(12),
            fill=muted,
        )
        canvas.save(out_frames / f"{i:05d}.png")

    out_mp4 = ROOT / "docs" / f"{out_stub}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(out_frames / "%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(out_mp4),
        ],
        check=True,
    )
    out_gif = ROOT / "docs" / f"{out_stub}.gif"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(out_mp4), "-vf",
            "fps=10,scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0",
            str(out_gif),
        ],
        check=True,
    )

    all_runs = json.loads((bench_dir / "summary.json").read_text())["runs"]
    argo_run = next((r for r in all_runs if r["arm"] == "argo"), None)
    argo_model_label = f"Evaluator+desktopweb: {argo_run['llm_model']}" if argo_run else "(unknown)"
    table = markdown_table(summary, argo_model_label, "deepseek-flash")
    (ROOT / "docs" / f"{out_stub}.md").write_text(table + "\n")
    print(table)
    print(f"\nWritten {out_mp4}, {out_gif}, {out_stub}.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench_dir")
    parser.add_argument("--out-stub", default="via-vs-argo")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--crop-scale", type=float, default=2.0, help="avfoundation physical/logical pixel ratio (2.0 on Retina)"
    )
    args = parser.parse_args()
    render(args.bench_dir, args.out_stub, args.fps, args.crop_scale)


if __name__ == "__main__":
    main()
