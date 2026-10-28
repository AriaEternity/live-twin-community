"""表情包（贴图）—— 预置表情的清单加载与正文渲染

约定：
- 表情文件放在 img/emotes/<包id>/<文件>，由 routes/main.py 的 /img/ 路由直接服务
- 清单 img/emotes/emotes.json 描述分包与表情（id 全站唯一），选择器（js/emotes.js）也读它
- 帖子里写 [em:表情id]，渲染时先整体 HTML 转义（防 XSS），再把白名单内的占位符
  替换为 <img>；未知 id 保留原文；每层楼最多渲染 MAX_PER_POST 个，超出部分保留原文
- 清单文件 mtime 变化时热重载，无需重启（同 wordfilter 词库模式）
"""
import json
import os
import re

from markupsafe import escape, Markup

BASE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(BASE, 'img', 'emotes', 'emotes.json')

# 每层楼最多渲染的表情数，防止刷屏/图片爆炸
MAX_PER_POST = 10

# 占位符语法：[em:表情id]，id 仅允许小写字母/数字/连字符/下划线
_CODE_RE = re.compile(r'\[em:([a-z0-9_-]{1,32})\]')

_cache = {'mtime': None, 'by_id': {}, 'packs': []}


def load_emotes(force=False):
    """加载清单（带 mtime 热重载）。返回 (packs, by_id)。

    packs: [{"id":..., "name":..., "stickers":[{"id","name","file","w","h"}]}]
    by_id: {表情id: sticker 字典}；id 重复时后出现的覆盖（加载即校验告警打印）
    """
    try:
        mtime = os.path.getmtime(MANIFEST_PATH)
    except OSError:
        mtime = None
    if not force and mtime == _cache['mtime']:
        return _cache['packs'], _cache['by_id']

    packs, by_id = [], {}
    if mtime is not None:
        try:
            with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for pack in data.get('packs', []):
                stickers = []
                for st in pack.get('stickers', []):
                    sid = str(st.get('id', '')).strip()
                    if not sid or not _CODE_RE.fullmatch('[em:' + sid + ']'):
                        continue  # 非法 id 直接跳过，防注入
                    item = {
                        'id': sid,
                        'name': str(st.get('name', sid))[:20],
                        'file': str(st['file']),
                        'w': int(st.get('w', 0)) or None,
                        'h': int(st.get('h', 0)) or None,
                    }
                    stickers.append(item)
                    by_id[sid] = item
                if stickers:
                    packs.append({'id': pack.get('id', ''), 'name': pack.get('name', ''), 'stickers': stickers})
        except (OSError, ValueError, KeyError, TypeError):
            packs, by_id = [], {}  # 清单损坏时降级为无表情，不影响发帖

    _cache.update({'mtime': mtime, 'packs': packs, 'by_id': by_id})
    return packs, by_id


def _img_tag(st):
    """生成单个表情 <img> 标签（src 仅来自白名单清单，属性值转义）"""
    attrs = 'src="/img/emotes/{}" class="emote" alt="[{}]" title="{}" loading="lazy"'.format(
        escape(st['file']), escape(st['name']), escape(st['name']))
    if st['w'] and st['h']:
        attrs += ' width="{}" height="{}"'.format(st['w'], st['h'])
    return '<img {}>'.format(attrs)


def render_content(text):
    """正文渲染过滤器：先转义，再把 [em:id] 替换为表情 <img>，返回 Markup。

    安全要点：escape 在替换之前执行，<img> 只由本函数按白名单清单拼接，
    用户输入的任何 HTML/属性都无法进入输出。
    """
    if text is None:
        return ''
    packs, by_id = load_emotes()
    escaped = escape(text)
    if not by_id:
        return escaped

    count = [0]  # 闭包内计数，超出上限保留原文

    def _sub(m):
        st = by_id.get(m.group(1))
        if st is None or count[0] >= MAX_PER_POST:
            return m.group(0)  # 未知 id / 超上限：保留转义后的占位符原文
        count[0] += 1
        return _img_tag(st)

    return Markup(_CODE_RE.sub(_sub, escaped))
