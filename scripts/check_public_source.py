from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'data', '.venv', 'node_modules', '.sources', '.env', 'test-results', 'playwright-report', 'dist'}
PATTERNS = [r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{30,}', r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{32,}', r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']


def main():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    files = [name for name in result.stdout.decode().split('\0') if name]
    if not files:
        raise SystemExit('No tracked files to audit')
    problems = []
    for name in files:
        path = Path(name)
        if FORBIDDEN.intersection(path.parts) or path.suffix in {'.enc', '.db', '.log', '.zip'} or path.name in {'local.key', 'accounts.json'}:
            problems.append(f'Forbidden runtime file: {name}')
            continue
        text = (ROOT / path).read_text(encoding='utf-8', errors='replace')
        if any(re.search(pattern, text) for pattern in PATTERNS):
            problems.append(f'Possible credential in: {name}')
        if re.search(r'[CD]:[/\\]Users[/\\]|wxid_[a-z0-9]+', text, re.I):
            problems.append(f'Personal machine path or identifier in: {name}')
    if problems:
        print('\n'.join(problems))
        return 1
    print(f'Public source audit passed: {len(files)} files; no runtime data or known credential patterns.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
