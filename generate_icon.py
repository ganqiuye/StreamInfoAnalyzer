# -*- coding: utf-8 -*-
"""为 TS 帧分析工具生成 logo (logo.png + logo.ico)。

设计参考 StreamPtsAnalyzer 的风格：深色圆角方形背景 + 蓝色渐变条 + 霓虹青色脉冲线。
主题改为"帧分析"：视频帧序列(胶片帧格) + 帧序号 + 时间戳脉冲线。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "assets"
OUT_PNG = OUT_DIR / "logo.png"
OUT_ICO = OUT_DIR / "logo.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

CYAN = (94, 234, 255)
CYAN_GLOW = (77, 217, 255)


def rounded_rect_bg():
    """深色渐变圆角方形背景"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=200, fill=255)

    bg = Image.new("RGBA", (S, S))
    px = bg.load()
    for y in range(S):
        t = y / S
        # 顶部 #0A1428 -> 底部 #0D1B2E，略带对角渐变
        for x in range(S):
            u = x / S
            tt = (t * 0.7 + u * 0.3)
            r = int(10 + 3 * tt)
            g = int(20 + 7 * tt)
            b = int(40 + 6 * tt)
            px[x, y] = (r, g, b, 255)
    img.paste(bg, (0, 0), mask)
    return img


def draw_film_frames(img):
    """左上到右下的三个圆角'帧格'，蓝色渐变，中间帧高亮"""
    d = ImageDraw.Draw(img)
    frames = [
        # (x0, y0, x1, y1, alpha)
        (120, 300, 480, 540, 110),   # 后景帧(暗)
        (170, 380, 530, 620, 160),   # 中间帧
        (220, 460, 580, 700, 235),   # 前景帧(亮)
    ]
    for x0, y0, x1, y1, a in frames:
        # 蓝色渐变填充 #3B82F6 -> #1D4ED8
        w, h = x1 - x0, y1 - y0
        grad = Image.new("RGBA", (w, h))
        gp = grad.load()
        for yy in range(h):
            t = yy / max(h - 1, 1)
            gp_row = (
                int(59 + (29 - 59) * t),
                int(130 + (78 - 130) * t),
                int(246 + (216 - 246) * t),
                a,
            )
            for xx in range(w):
                gp[xx, yy] = gp_row
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=255)
        img.paste(grad, (x0, y0), m)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x1, y1], radius=40, outline=(140, 190, 255, 90), width=4)

    # 帧格左侧打孔(胶片感)
    d = ImageDraw.Draw(img)
    for fx0, fy0, fx1, fy1, a in frames[:2]:
        for hy in range(fy0 + 50, fy1 - 30, 90):
            d.ellipse([fx0 + 28, hy, fx0 + 58, hy + 30], fill=(10, 20, 40, 255))
    return img


def draw_pts_nodes(img):
    """前景帧上的 PTS 节点：横线 + 两端圆点 + 刻度"""
    d = ImageDraw.Draw(img)
    y = 580
    x0, x1 = 270, 530
    d.line([x0, y, x1, y], fill=(220, 240, 255, 220), width=10)
    for x in (x0, x1):
        d.ellipse([x - 20, y - 20, x + 20, y + 20], fill=(255, 255, 255, 255))
        d.ellipse([x - 10, y - 10, x + 10, y + 10], fill=(59, 130, 246, 255))
    # 中间小刻度
    for tx in (350, 400, 450):
        d.line([tx, y - 16, tx, y + 16], fill=(220, 240, 255, 150), width=6)
    return img


def make_pulse_layer():
    """霓虹脉冲线(独立图层以便加辉光)"""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 波形从左下穿过帧格到右上，代表帧间 PTS 递增
    pts = [
        (150, 760), (280, 760), (340, 640), (420, 850),
        (520, 560), (610, 700), (700, 430), (790, 520), (880, 330),
    ]
    d.line(pts, fill=CYAN + (255,), width=26, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - 22, p[1] - 22, p[0] + 22, p[1] + 22], fill=CYAN + (255,))
    return layer


def main():
    OUT_DIR.mkdir(exist_ok=True)
    img = rounded_rect_bg()
    img = draw_film_frames(img)
    img = draw_pts_nodes(img)

    glow = make_pulse_layer().filter(ImageFilter.GaussianBlur(22))
    # 辉光层加两次增强霓虹感
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, glow.point(lambda a: a))  # 叠加
    pulse = make_pulse_layer()
    img = Image.alpha_composite(img, pulse)

    img = img.resize((512, 512), Image.Resampling.LANCZOS)
    img.save(OUT_PNG)
    print(f"Wrote {OUT_PNG} ({OUT_PNG.stat().st_size} bytes)")

    big = Image.open(OUT_PNG).resize((256, 256), Image.Resampling.LANCZOS)
    big.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"Wrote {OUT_ICO} ({OUT_ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
