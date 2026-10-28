# -*- coding: utf-8 -*-
"""只在独占源码沙箱运行既有冒烟；原业务库、上传和日志不进入副本。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['app.py', 'config.py', 'models.py', 'security.py', 'emotes.py', 'wordfilter.py',
         'requirements.txt', 'index.html', 'install.html', 'offline.html', 'manifest.json', 'sw.js']
DIRECTORIES = ['routes', 'templates', 'js', 'css', 'scripts', 'tests', 'cards', 'img']
CASES = ['smoke_forum_fix.py', 'smoke_messages.py', 'smoke_social.py', 'smoke_decks.py', 'smoke_cards_filter.py']


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('沙箱目标已存在，拒绝覆盖')
    destination.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(ROOT / name, destination / name)
    for name in DIRECTORIES:
        shutil.copytree(ROOT / name, destination / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
    (destination / 'data').mkdir()
    for name in ['cards_db.json', 'banlist_ocg.json', 'card_texts.json']:
        shutil.copy2(ROOT / 'data' / name, destination / 'data' / name)
    (destination / 'data' / 'groups.json').write_text('{"deck_groups":[],"college_groups":[]}', encoding='utf-8')
    for name in ['instance', 'uploads', 'private-temp']:
        (destination / name).mkdir()
    (destination / 'instance' / 'synthetic-words.txt').write_text('测试违规词\n', encoding='utf-8')
    return destination


def environment(destination):
    env = os.environ.copy()
    env.update(FLASK_DEBUG='1', SECRET_KEY='isolated-demo-key-not-for-production', PYTHONDONTWRITEBYTECODE='1',
               DATABASE_URL='sqlite:///' + (destination / 'instance' / 'isolated.sqlite').as_posix(),
               UPLOAD_FOLDER=str(destination / 'uploads'),
               SENSITIVE_WORDS_FILE=str(destination / 'instance' / 'synthetic-words.txt'),
               TEMP=str(destination / 'private-temp'), TMP=str(destination / 'private-temp'), PYTHONUTF8='1')
    return env


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--case', choices=CASES, action='append')
    cli.add_argument('--prepare-only', action='store_true')
    args = cli.parse_args()
    output = ROOT / '.test-output'
    output.mkdir(parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix='isolated-', dir=output))
    if args.prepare_only:
        target = prepare(container / 'preview')
        print(str(target))
        return 0
    results = []
    for name in args.case or CASES:
        target = prepare(container / name.removesuffix('.py'))
        run = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(target / 'scripts' / name)],
                             cwd=target, env=environment(target), capture_output=True, text=True,
                             encoding='utf-8', timeout=360)
        log = container / (name + '.log')
        log.write_text(run.stdout + run.stderr, encoding='utf-8')
        lines = run.stdout.splitlines()
        result = {'case': name, 'exit_code': run.returncode, 'sandbox': str(target), 'log': str(log),
                  'failed_lines': [line for line in lines if line.lstrip().startswith(('FAIL', '[失败]'))], 'tail': lines[-8:]}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    (container / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(any(result['exit_code'] != 0 or result['failed_lines'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
