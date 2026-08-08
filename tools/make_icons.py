#!/usr/bin/env python3
"""홈 화면 아이콘 생성 (아이폰 웹앱용).

iOS 는 apple-touch-icon 에 SVG·data URI 를 제대로 안 먹으므로 실제 PNG 가 필요하다.
바이너리를 손으로 커밋하는 대신 이 스크립트로 재생성할 수 있게 남긴다.

  python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "icons"
BG = (14, 22, 38)          # --panel
GOLD = (212, 175, 55)      # --gold
GOLD2 = (240, 210, 122)    # --gold2
MUT = (125, 138, 163)      # --mut

# 화면과 같은 은유 — 왼쪽은 낮고 오른쪽으로 갈수록 높아지는 막대(가속).
BARS = [(0.30, GOLD.__class__((140, 115, 32))), (0.55, GOLD), (0.85, GOLD2)]


def font(px):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, px)
            except OSError:
                pass
    return ImageFont.load_default()


def make(size: int, label: str) -> Image.Image:
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    # 은은한 대각 하이라이트 (화면 배경의 라디얼 그라디언트 느낌만 살짝)
    for i in range(size):
        t = i / size
        d.line([(0, i), (size, i)],
               fill=(int(BG[0] + 9 * (1 - t)), int(BG[1] + 12 * (1 - t)),
                     int(BG[2] + 18 * (1 - t))))

    pad = size * 0.20
    w = size - pad * 2
    bw = w / 3 * 0.62
    gap = (w - bw * 3) / 2
    base = size - pad - size * 0.13          # 라벨 자리를 아래에 비워둔다
    for i, (h, col) in enumerate(BARS):
        x = pad + i * (bw + gap)
        top = base - (base - pad) * h
        r = bw * 0.28
        d.rounded_rectangle([x, top, x + bw, base], radius=r, fill=col)

    f = font(int(size * 0.155))
    tw = d.textbbox((0, 0), label, font=f)
    d.text(((size - (tw[2] - tw[0])) / 2, base + size * 0.025),
           label, font=f, fill=MUT)
    return img


def main():
    OUT.mkdir(exist_ok=True)
    made = []
    for market, label in (("kr", "KR"), ("us", "US")):
        for size in (180, 192, 512):
            p = OUT / f"icon-{market}-{size}.png"
            make(size, label).save(p, optimize=True)
            made.append(f"{p.name} ({p.stat().st_size:,}B)")
    print("생성:", ", ".join(made))


if __name__ == "__main__":
    main()
