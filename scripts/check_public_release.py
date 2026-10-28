"""扫描Git跟踪文件中的私有目录、凭据格式及意外运行产物，只输出问题类别。"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {'instance', 'uploads', 'backup', 'research', 'releases', 'alkey',
                 '.venv', '__pycache__', '.test-output', '.preview'}
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.woff', '.woff2'}
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'github-token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'openai-key': re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{40,}'),
    'cloud-key': re.compile(r'AKIA[0-9A-Z]{16}'),
    'deployment-path': re.compile(r'\x2fopt\x2f[a-zA-Z0-9_-]+|[A-Za-z]:[\\/](?:Users|Neko)[\\/]'),
    'registration-id': re.compile(r'(?:公网安备|ICP备)\s*\d{6,}'),
    'organization-share': re.compile(r'https://[^/\s]+\.feishu\.cn/share/'),
}


def candidates(staged):
    if (ROOT / '.git').exists():
        args = ['git', 'ls-files', '-z']
        if staged:
            args = ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']
        result = subprocess.run(args, cwd=ROOT, capture_output=True, check=True)
        return [name for name in result.stdout.decode('utf-8').split('\0') if name]
    return [p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
            and not any(part in PRIVATE_PARTS or part=='.git' for part in p.relative_to(ROOT).parts)]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--staged', action='store_true')
    args = cli.parse_args()
    failures = []
    names = candidates(args.staged)
    if not names:
        print('FAIL empty-file-list：请先将待检查文件加入Git索引')
        return 1
    for name in names:
        path = ROOT / name
        parts = Path(name).parts
        if (any(part in PRIVATE_PARTS for part in parts)
                or path.suffix.lower() in {'.pem','.key','.p12','.pfx','.db','.sqlite','.sqlite3','.log','.pyc'}
                or (path.name.startswith('.env') and path.name!='.env.example')
                or name=='data/groups.json'):
            failures.append((name,'private-file')); continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            if args.staged:
                content = subprocess.run(['git','show',':'+name],cwd=ROOT,
                                         capture_output=True,check=True).stdout
                text = content.decode('utf-8')
            else:
                text = path.read_text(encoding='utf-8')
        except (UnicodeError,OSError):
            failures.append((name,'unexpected-binary-or-missing')); continue
        if text.startswith('\ufeff'):
            failures.append((name,'utf8-bom'))
        for label,pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append((name,label))
    for name,label in failures:
        print('FAIL',label,name)
    print(f'公开文件检查：{len(names)}份文件，{len(failures)}项问题')
    return int(bool(failures))


if __name__=='__main__':
    sys.exit(main())
