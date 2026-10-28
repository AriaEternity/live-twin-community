"""生成占位表情包（开发/联调用，正式表情到位后可删）

在 img/emotes/sample/ 下生成 4 张静态 PNG + 2 张动态 GIF（128x128 透明底），
并写出清单 img/emotes/emotes.json。用户后续提供正式表情后：
把文件放进 img/emotes/<包名>/ 并改写 emotes.json 即可（注意同名替换有 nginx 30d 缓存，建议新文件名）。

运行：.venv/Scripts/python scripts/make_emote_placeholders.py
"""
import json
import math
import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'img', 'emotes', 'sample')
MANIFEST = os.path.join(BASE, 'img', 'emotes', 'emotes.json')

SIZE = 128
PINK = (238, 91, 166, 255)    # --ksk-primary 双子粉
BLUE = (74, 111, 208, 255)    # 双子蓝
GOLD = (255, 200, 60, 255)
WHITE = (255, 255, 255, 255)


def new_canvas():
    return Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))


def draw_star(d, cx, cy, r_outer, r_inner, color, rotation=-90):
    """五角星"""
    pts = []
    for i in range(10):
        r = r_outer if i % 2 == 0 else r_inner
        a = math.radians(rotation + i * 36)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=color)


def draw_heart(d, cx, cy, s, color):
    """爱心：两个圆 + 下方三角"""
    r = s * 0.28
    d.ellipse([cx - s * 0.5, cy - s * 0.42, cx - s * 0.5 + 2 * r, cy - s * 0.42 + 2 * r], fill=color)
    d.ellipse([cx + s * 0.5 - 2 * r, cy - s * 0.42, cx + s * 0.5, cy - s * 0.42 + 2 * r], fill=color)
    d.polygon([(cx - s * 0.47, cy - s * 0.12), (cx + s * 0.47, cy - s * 0.12), (cx, cy + s * 0.5)], fill=color)


def img_star():
    im = new_canvas()
    d = ImageDraw.Draw(im)
    draw_star(d, 64, 64, 52, 22, GOLD)
    return im


def img_heart():
    im = new_canvas()
    d = ImageDraw.Draw(im)
    draw_heart(d, 64, 60, 96, PINK)
    return im


def img_ok():
    im = new_canvas()
    d = ImageDraw.Draw(im)
    d.ellipse([14, 14, 114, 114], fill=BLUE)
    d.line([(38, 66), (58, 86), (92, 44)], fill=WHITE, width=12, joint='curve')
    return im


def img_cry():
    im = new_canvas()
    d = ImageDraw.Draw(im)
    d.ellipse([14, 14, 114, 114], fill=(120, 170, 235, 255))
    # 眼睛（闭眼下弯）
    d.arc([34, 46, 54, 62], 200, 340, fill=(21, 16, 43, 255), width=6)
    d.arc([74, 46, 94, 62], 200, 340, fill=(21, 16, 43, 255), width=6)
    # 嘴（下弯）
    d.arc([46, 84, 82, 108], 200, 340, fill=(21, 16, 43, 255), width=6)
    # 泪滴
    d.ellipse([24, 62, 36, 80], fill=(210, 235, 255, 255))
    return im


def gif_twirl():
    """转圈圈：旋转的星星，8 帧"""
    frames = []
    for i in range(8):
        im = new_canvas()
        d = ImageDraw.Draw(im)
        draw_star(d, 64, 64, 52, 22, PINK, rotation=-90 + i * 45)
        frames.append(im)
    return frames, 90


def gif_bounce():
    """蹦蹦：上下弹跳的爱心，8 帧"""
    frames = []
    for i in range(8):
        im = new_canvas()
        d = ImageDraw.Draw(im)
        dy = int(14 * math.sin(i * math.pi / 4))  # 上下 ±14px
        draw_heart(d, 64, 60 + dy, 96, BLUE)
        frames.append(im)
    return frames, 90


def save_png(im, name):
    im.save(os.path.join(OUT_DIR, name + '.png'), optimize=True)


def save_gif(frames, duration, name):
    frames[0].save(
        os.path.join(OUT_DIR, name + '.gif'),
        save_all=True, append_images=frames[1:],
        duration=duration, loop=0, disposal=2, transparency=0, optimize=True,
    )


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    save_png(img_star(), 'star')
    save_png(img_heart(), 'heart')
    save_png(img_ok(), 'ok')
    save_png(img_cry(), 'cry')
    frames, dur = gif_twirl()
    save_gif(frames, dur, 'twirl')
    frames, dur = gif_bounce()
    save_gif(frames, dur, 'bounce')

    manifest = {
        'packs': [{
            'id': 'sample',
            'name': '示例包',
            'stickers': [
                {'id': 'star', 'name': '星星', 'file': 'sample/star.png', 'w': 128, 'h': 128},
                {'id': 'heart', 'name': '心心', 'file': 'sample/heart.png', 'w': 128, 'h': 128},
                {'id': 'ok', 'name': 'OK', 'file': 'sample/ok.png', 'w': 128, 'h': 128},
                {'id': 'cry', 'name': '哭哭', 'file': 'sample/cry.png', 'w': 128, 'h': 128},
                {'id': 'twirl', 'name': '转圈圈', 'file': 'sample/twirl.gif', 'w': 128, 'h': 128},
                {'id': 'bounce', 'name': '蹦蹦', 'file': 'sample/bounce.gif', 'w': 128, 'h': 128},
            ],
        }],
    }
    with open(MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    for fn in sorted(os.listdir(OUT_DIR)):
        print(fn, os.path.getsize(os.path.join(OUT_DIR, fn)))
    print('manifest ->', MANIFEST)


if __name__ == '__main__':
    main()
