# -*- coding: utf-8 -*-
"""涉政/违禁词红线硬拦截 —— 词库加载与命中检测（纯标准库实现）。

词库文件：
- 主词库：环境变量 SENSITIVE_WORDS_FILE 指定，缺省为 <项目根>/instance/sensitive_words_red.txt
- 本地补充词库：<项目根>/instance/sensitive_words_local.txt（不存在则忽略）
两个文件一词一行，# 开头为注释，空行忽略；mtime 变化时自动热重载，匹配取两库并集。
"""
import os
import unicodedata
from datetime import datetime

# 项目根目录（本文件所在目录）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 默认词库与日志路径
_DEFAULT_MAIN_WORDS = os.path.join(_BASE_DIR, 'instance', 'sensitive_words_red.txt')
_LOCAL_WORDS = os.path.join(_BASE_DIR, 'instance', 'sensitive_words_local.txt')
_HIT_LOG = os.path.join(_BASE_DIR, 'instance', 'sensitive_hits.log')

# 片段截断长度
_SNIPPET_MAX = 100

# 模块级缓存：{(路径): (mtime, [(原词, 归一化词), ...])}
_cache = {}


def _main_words_path():
    """主词库路径：环境变量优先（测试用），否则取默认路径。"""
    return os.environ.get('SENSITIVE_WORDS_FILE') or _DEFAULT_MAIN_WORDS


def _build_translate_table():
    """构建归一化映射表：全角字符转半角，空白/标点/符号/控制与零宽字符全部剔除。"""
    table = {}
    # 全角 ASCII（！～）映射到半角（!~），全角空格映射到普通空格（随后会被剔除）
    for cp in range(0xFF01, 0xFF5F):
        table[cp] = chr(cp - 0xFEE0)
    # 全角空格直接剔除（注意：translate 的替换值不会二次查表，映射成普通空格会残留）
    table[0x3000] = None
    # 剔除 Unicode 空白(Zs)、标点(P*，含斜杠与中文标点)、符号(S*，含 ★♥ 与 emoji)、
    # 控制/格式字符(C*，含零宽空格 U+200B 等隐形绕过字符)、其他数字(No，含 ①②③ 等圈号)
    for cp in range(0x110000):
        cat = unicodedata.category(chr(cp))
        if cat in ('Zs', 'No') or cat[0] in ('P', 'S', 'C'):
            table.setdefault(cp, None)
    return table


_TRANSLATE_TABLE = _build_translate_table()


def _normalize(text):
    """归一化：全角转半角、大小写折叠、剔除所有空白/标点/符号/控制与零宽字符。"""
    if not text:
        return ''
    return text.translate(_TRANSLATE_TABLE).casefold()


def _load_file(path):
    """加载单个词库文件，返回 (mtime, [(原词, 归一化词), ...])；文件不存在返回 (None, [])。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, []
    words = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            word = line.strip()
            if not word or word.startswith('#'):
                continue
            norm = _normalize(word)
            if norm:
                words.append((word, norm))
    return mtime, words


def _get_words(path):
    """按路径取词库（带缓存），mtime 变化时热重载。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    if mtime is None:
        _cache[path] = (None, [])
        return []
    _, words = _load_file(path)
    _cache[path] = (mtime, words)
    return words


def load_words(path=None):
    """加载词库文件（默认主词库路径），一词一行，# 注释与空行忽略。

    记录文件 mtime 供热重载比对；返回加载的词数。文件不存在返回 0。
    """
    if path is None:
        path = _main_words_path()
    mtime, words = _load_file(path)
    _cache[path] = (mtime, words)
    return len(words)


def find_sensitive(text):
    """检测文本是否命中违禁词。

    先归一化文本与词库（去空白/标点/符号/零宽、全角转半角、大小写折叠），再做子串匹配；
    命中返回词库中的原词，未命中返回 None；词库缺失或为空返回 None。
    主词库与本地补充词库并集匹配，任一文件 mtime 变化都会触发热重载。
    """
    norm_text = _normalize(text)
    if not norm_text:
        return None
    for path in (_main_words_path(), _LOCAL_WORDS):
        for original, norm_word in _get_words(path):
            if norm_word in norm_text:
                return original
    return None


def log_hit(kind, word, snippet, user, ip):
    """向 instance/sensitive_hits.log 追加一行 UTF-8 命中日志。

    行格式：时间\t类型\t命中词\t用户\tIP\t内容片段（片段截断到 100 字符）。
    """
    os.makedirs(os.path.dirname(_HIT_LOG), exist_ok=True)
    snippet = (snippet or '')[:_SNIPPET_MAX].replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '\t'.join([timestamp, str(kind), str(word), str(user), str(ip), snippet])
    with open(_HIT_LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
