"""群索引：卡组群 / 高校群目录，关键词搜索（前端过滤）
数据源 data/groups.json（mtime 热重载），结构：
  {"deck_groups": [{name,id,desc,tags}], "college_groups": [{name,id,desc,tags}]}
群号（id）以字符串存储——QQ 群号超出 JS 安全整数（2^53），数字会丢精度。"""
import json
import os
from flask import Blueprint, render_template

groups_bp = Blueprint('groups', __name__, url_prefix='/groups')

_GROUPS_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data', 'groups.json')

_groups_cache = {'mtime': None, 'data': {'deck_groups': [], 'college_groups': []}}


def get_groups():
    """取群数据 {deck_groups:[], college_groups:[]}，mtime 变化时热重载。
    文件缺失/损坏时降级为空列表，只打印 warning 不炸服务（同 decks.get_banlist）。"""
    try:
        mtime = os.path.getmtime(_GROUPS_PATH)
    except OSError:
        mtime = None
    if mtime is not None and mtime == _groups_cache['mtime']:
        return _groups_cache['data']

    data = {'deck_groups': [], 'college_groups': []}
    if mtime is not None:
        try:
            with open(_GROUPS_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for key in ('deck_groups', 'college_groups'):
                    val = raw.get(key)
                    if isinstance(val, list):
                        data[key] = val
        except (ValueError, OSError, AttributeError) as e:
            print(f'[groups] 警告：群数据 {_GROUPS_PATH} 损坏（{e}），降级为空')
    else:
        print(f'[groups] 警告：群数据 {_GROUPS_PATH} 不存在，降级为空')

    _groups_cache['mtime'] = mtime
    _groups_cache['data'] = data
    return data


@groups_bp.route('/')
def index():
    groups = get_groups()
    return render_template('groups/index.html',
                           deck_groups=groups['deck_groups'],
                           college_groups=groups['college_groups'])
