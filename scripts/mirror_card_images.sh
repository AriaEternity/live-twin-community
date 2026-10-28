#!/usr/bin/env bash
# ============================================================
# mirror_card_images.sh — 卡图/素材图片本地化镜像脚本
#
# 用途：把 js/data.js 引用的第三方 CDN 图片镜像到本地目录：
#   cards/{id}.jpg          ← YGOPRODeck cards/（整卡大图，421×614）
#   cards/small/{id}.jpg    ← YGOPRODeck cards_small/（268×391 小图）
#   cards/cropped/{id}.jpg  ← YGOPRODeck cards_cropped/（艺术裁切图）
#   img/*.png               ← fandom 静态 CDN（异画立绘/决斗垫等素材）
#
# 用法（Git Bash 中从项目根执行）：
#   bash scripts/mirror_card_images.sh
#
# 幂等：已存在且 >5KB 的文件跳过；下载失败自动重试一次；
# 结束时打印统计与失败清单。卡片密码直接读取 js/data.js，不另维护重复清单。
# 注意：必须在 Git Bash 里跑 curl 循环（踩坑 #1，勿在 Python subprocess 调 curl）。
# ============================================================
set -u
cd "$(dirname "$0")/.." || exit 1

IDS="$(python -X utf8 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path('scripts').resolve()))
from refresh_card_data import theme_ids
print(' '.join(map(str, theme_ids(Path('js/data.js').read_text(encoding='utf-8')))))
PY
)" || exit 1

FANDOM_ITEMS="md_kisikil.png|https://static.wikia.nocookie.net/yugioh/images/1/10/Evil%E2%98%85Twin_Ki-sikil.png
md_lilla.png|https://static.wikia.nocookie.net/yugioh/images/5/58/Evil%E2%98%85Twin_Lil-la.png
playmat.png|https://static.wikia.nocookie.net/yugioh/images/d/d8/Live%E2%98%86TwinsKisikil-Lilla-mat.png
sunny_card.png|https://static.wikia.nocookie.net/yugioh/images/7/74/EvilTwinsTroubleSunny-MP22-EN-PScR-1E.png"

mkdir -p cards/small cards/cropped img

ok=0; skip=0; fail=0; failed_list=""

# dl <url> <dest>：存在且 >5KB 跳过；下载失败重试一次；仍失败记录
dl() {
  local url="$1" dest="$2"
  if [ -f "$dest" ] && [ "$(stat -c%s "$dest")" -gt 5120 ]; then
    skip=$((skip+1)); return 0
  fi
  for attempt in 1 2; do
    if curl -fsSL --connect-timeout 15 --max-time 60 -o "$dest" "$url" \
       && [ -f "$dest" ] && [ "$(stat -c%s "$dest")" -gt 5120 ]; then
      ok=$((ok+1)); return 0
    fi
    [ "$attempt" -eq 1 ] && echo "  重试: $dest"
  done
  rm -f "$dest"
  fail=$((fail+1)); failed_list="$failed_list\n  $dest  <-  $url"
  echo "  失败: $dest"
  return 1
}

echo "== 整卡大图 cards/ =="
for id in $IDS; do
  dl "https://images.ygoprodeck.com/images/cards/${id}.jpg" "cards/${id}.jpg"
done

echo "== 小图 cards/small/ =="
for id in $IDS; do
  dl "https://images.ygoprodeck.com/images/cards_small/${id}.jpg" "cards/small/${id}.jpg"
done

echo "== 裁切图 cards/cropped/ =="
for id in $IDS; do
  dl "https://images.ygoprodeck.com/images/cards_cropped/${id}.jpg" "cards/cropped/${id}.jpg"
done

echo "== fandom 素材 img/ =="
while IFS='|' read -r name url; do
  [ -z "$name" ] && continue
  dl "$url" "img/$name"
done <<EOF
$FANDOM_ITEMS
EOF

# 裁切图统一重压 q85（2026-07-24 起，省 ~34% 体积；只保留变小的，已压过的重跑为 no-op）
echo "== 裁切图重压 q85 =="
python -X utf8 - <<'PY'
from PIL import Image
import os, glob
saved = 0
for f in sorted(glob.glob('cards/cropped/*.jpg')):
    orig = os.path.getsize(f)
    tmp = f + '.tmp'
    Image.open(f).save(tmp, 'JPEG', quality=85, optimize=True, subsampling=2)
    if os.path.getsize(tmp) < orig:
        saved += orig - os.path.getsize(tmp)
        os.replace(tmp, f)
    else:
        os.remove(tmp)
print(f'cropped recompress saved {saved//1024}KB')
PY

echo ""
echo "== 统计 =="
printf '整图 %s 张 / 小图 %s 张 / 裁切 %s 张 / fandom %s 张\n' \
  "$(ls cards/*.jpg 2>/dev/null | grep -vc crop_sunny)" \
  "$(ls cards/small/*.jpg 2>/dev/null | wc -l)" \
  "$(ls cards/cropped/*.jpg 2>/dev/null | wc -l)" \
  "$(ls img/* 2>/dev/null | wc -l)"
du -sh cards img 2>/dev/null
printf '本次: 下载 %d / 跳过 %d / 失败 %d\n' "$ok" "$skip" "$fail"
if [ "$fail" -gt 0 ]; then
  printf '失败清单:%b\n' "$failed_list"
  exit 1
fi
