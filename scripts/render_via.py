"""Render the recorded VIA fastweb PoC run against ARGO's recorded desktopweb figures.

Numbers on the ARGO side are figures recorded in ARGO's own code comments and plan
docs (see docs/via-fastweb-design.md §7) -- a different machine, network, and task
wording. They are not a controlled re-measurement, and the video and comparison
image both say so.
"""

import argparse
import json
import statistics
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

# Recorded elsewhere, not measured in this run. See docs/argo-fusion.md §2.3.
ARGO_NAVER_TURN_MS = 48_000
ARGO_VIA_WEATHER_MS = 17_300

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path, help="Exact verified recording directory (output of record_via.py)")
args = parser.parse_args()
source = args.source.resolve()
state = json.loads((source / "state.json").read_text())
assert state["verification"]["passed"] and not state["recording_errors"], "record_via.py must pass verification first"

frames = [(0, Image.open(source / "frames/000000.jpg").convert("RGB"))]
frames += sorted((int(p.stem), Image.open(p).convert("RGB")) for p in (source / "screencast").glob("*.jpg"))
end = state["elapsed_ms"]
summary = state["summary"]
folder = source / "video-frames"
folder.mkdir(parents=True, exist_ok=False)

font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
font_bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
kr_path = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def font(n, bold=False):
    return ImageFont.truetype(font_bold if bold else font_path, n)


def kr(n, bold=False):
    # AppleSDGothicNeo.ttc is a collection; index 1 is close to a bold weight on macOS.
    return ImageFont.truetype(kr_path, n, index=1 if bold else 0)


def mono(n):
    return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", n)


ink, muted, green, gray = "#172a20", "#6a766c", "#2a743f", "#9aa295"

for i in range(round((end + 500) * 30 / 1000)):
    t = min(end, round(i * 1000 / 30))
    screenshot = next(im for ts, im in reversed(frames) if ts <= t)
    canvas = Image.new("RGB", (1536, 1000), "#f3f4ec")
    d = ImageDraw.Draw(canvas)
    d.text((36, 26), "jev ultrafast", font=font(23, True), fill=ink)
    d.text((222, 27), "×  VIA fastweb PoC", font=font(22), fill=muted)
    d.rounded_rectangle((1287, 24, 1499, 59), radius=17, fill="#dfebd9")
    d.text((1305, 32), "REAL WEB · LIVE", font=font(14, True), fill=green)
    d.text((36, 80), f"{state['goal'][:44]}", font=kr(30, True), fill=ink)
    d.text((38, 122), "One goal. Dynamic elements. No site-specific script.", font=font(17), fill=muted)

    # Browser panel.
    d.rounded_rectangle((35, 168, 1157, 943), radius=14, fill="#202124")
    for j, c in enumerate(["#de8278", "#d6bd6e", "#8dbd8a"]):
        d.ellipse((54 + j * 19, 182, 63 + j * 19, 191), fill=c)
    history = [h for h in state["history"] if h["executed_ms"] <= t]
    host = urlparse(history[-1]["url"]).hostname if history else urlparse(state["start_url"]).hostname
    d.text((145, 178), host or "", font=mono(13), fill="#d4d6d5")
    canvas.paste(screenshot.crop((0, 64, 1120, 780)), (36, 203))

    # Right panel: step trail.
    d.text((1192, 178), "JEV FASTWEB", font=font(16, True), fill=green)
    d.text((1189, 210), f"{t / 1000:05.2f}", font=mono(46), fill=ink)
    d.text((1193, 268), "SECONDS ELAPSED", font=font(13, True), fill=muted)
    for j, h in enumerate(history[-6:]):
        y = 300 + j * 26
        label = h["action"].replace("\n", " ").strip()[:22]
        d.text((1192, y), f"✓ {h['operation']}", font=font(12, True), fill=green)
        d.text((1192, y + 13), label, font=kr(12), fill=ink)
        d.text((1400, y + 6), f"{h['latency_ms']} ms", font=mono(12), fill=muted)

    # Comparison bars: the most visible improvement claim, so also the most hedged.
    bar_top = 470
    d.text((1192, bar_top), "같은 종류의 작업 · ARGO 기록값과 비교", font=kr(14, True), fill=ink)
    bar_x, bar_w = 1192, 255
    rows = [
        ("jev (이 영상)", t, ARGO_NAVER_TURN_MS, green),
        ("ARGO desktopweb (기록, 2026-09-08 Naver)", ARGO_NAVER_TURN_MS, ARGO_NAVER_TURN_MS, gray),
        ("ARGO VIA 날씨 질의 (기록)", ARGO_VIA_WEATHER_MS, ARGO_NAVER_TURN_MS, gray),
    ]
    for j, (label, value, scale, color) in enumerate(rows):
        y = bar_top + 28 + j * 34
        d.text((bar_x, y), label, font=kr(12), fill=muted)
        d.rounded_rectangle((bar_x, y + 15, bar_x + bar_w, y + 23), radius=4, fill="#e7e9df")
        width = max(3, round(bar_w * min(1.0, value / scale)))
        d.rounded_rectangle((bar_x, y + 15, bar_x + width, y + 23), radius=4, fill=color)
        d.text((bar_x + bar_w + 8, y + 12), f"{value / 1000:.1f}s", font=mono(12), fill=ink)
    d.text(
        (bar_x, bar_top + 28 + len(rows) * 34 + 4),
        "ARGO 값은 코드 주석/문서의 기록값이며 동일 조건 재측정이 아님",
        font=kr(11),
        fill=muted,
    )

    # Bottom stats, cumulative to t.
    calls_so_far = sum(1 for x in state["decisions"] if x["elapsed_ms"] <= t)
    text_calls_so_far = sum(1 for x in state["text_calls"] if x.get("latency_ms") is not None)
    latencies = [x["latency_ms"] for x in state["decisions"] if x["elapsed_ms"] <= t]
    median = f"{statistics.median(latencies):.0f} ms" if latencies else "—"
    d.rounded_rectangle((1189, 670, 1499, 789), radius=14, fill="#dfeeda" if t >= end else "#e7e9df")
    final = t >= end
    title = "결과 확인됨" if final else "선택 중…"
    d.text((1209, 691), title, font=kr(20, True), fill=green if final else ink)
    d.text(
        (1209, 730),
        f"TypeSafe {calls_so_far}회 · 텍스트 LLM {text_calls_so_far}회 · 중앙값 {median}",
        font=kr(14),
        fill=muted,
    )
    d.line((37, 960, 1498, 960), fill="#d3d9cc", width=2)
    d.line((37, 960, 37 + (1498 - 37) * t / end, 960), fill=green, width=3)
    text_model = state["text_calls"][0]["model"] if state["text_calls"] else "text helper"
    d.text(
        (37, 973),
        f"Operation + index by TypeSafe. Text by {text_model}. Original timing; waits included.",
        font=font(14),
        fill=muted,
    )
    d.text((1194, 973), "github.com/browser-use/jev-ultrafast", font=font(12), fill=muted)
    canvas.save(folder / f"{i:04d}.png")

canvas.save(ROOT / "docs/via-result.png")

subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "30",
        "-i",
        str(folder / "%04d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
        str(ROOT / "docs/via-demo.mp4"),
    ],
    check=True,
)
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(ROOT / "docs/via-demo.mp4"),
        "-vf",
        "fps=12,scale=1152:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop",
        "0",
        str(ROOT / "docs/via-demo.gif"),
    ],
    check=True,
)

# A standalone comparison image + a markdown table for docs (both jev-side and ARGO-side).
comparison = Image.new("RGB", (1200, 520), "#f3f4ec")
cd = ImageDraw.Draw(comparison)
cd.text((40, 30), "ARGO desktopweb vs jev fastweb", font=kr(30, True), fill=ink)
cd.text((40, 72), "PoC 실측 · ARGO 값은 기록값(동일 조건 아님)", font=kr(15), fill=muted)
rows = [
    ("작업", "Naver 웹 턴(2026-09-08) / VIA 날씨 질의", "네이버 \"서울 날씨\" 검색"),
    ("총 시간", "48 s / 17.3 s", f"{end / 1000:.1f} s"),
    ("결정 방식", "대형 LLM이 DSL 생성", "TypeSafe 선택 (1요청)"),
    ("결정 1회 지연", "draft 3.1 s · 검증 8.2 s", f"중앙값 {summary['decision_ms_median']} ms"),
    (
        "모델 호출",
        "ReAct 턴 + 완료 검증 턴(+재시도 최대 3)",
        f"TypeSafe {summary['typesafe_calls']} · 텍스트 {summary['text_calls']}",
    ),
    ("완료 판정", "LLM 검증 턴", "최종 페이지 독립 검증"),
]
y = 130
for label, argo_val, jev_val in rows:
    cd.text((40, y), label, font=kr(16, True), fill=ink)
    cd.text((300, y), argo_val, font=kr(15), fill=muted)
    cd.text((760, y), jev_val, font=kr(15, True), fill=green)
    y += 58
comparison.save(ROOT / "docs/via-comparison.png")

md_lines = ["| 항목 | ARGO desktopweb (기록) | jev fastweb (실측) |", "|---|---|---|"]
for label, argo_val, jev_val in rows:
    md_lines.append(f"| {label} | {argo_val} | {jev_val} |")
print("\n".join(md_lines))

print("Rendered", len(frames), "source frames at original timing:", end, "ms, plus a 500ms end hold.")
