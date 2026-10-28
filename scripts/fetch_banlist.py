# -*- coding: utf-8 -*-
"""兼容旧入口：按官方完整名单核验OCG禁限，统一异画密码。"""
import sys
from refresh_card_data import main

if __name__ == '__main__':
    raise SystemExit(main(['--banlist-only', *sys.argv[1:]]))
