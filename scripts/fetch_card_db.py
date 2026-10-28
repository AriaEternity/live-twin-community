# -*- coding: utf-8 -*-
"""兼容旧入口：读取全量属性、保留中文资料并验证后原子替换。"""
import sys
from refresh_card_data import main

if __name__ == '__main__':
    raise SystemExit(main(['--cards-only', *sys.argv[1:]]))
