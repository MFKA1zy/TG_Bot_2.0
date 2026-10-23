"""Read-only Git/secret checks and Python compilation. Never prints secret values."""
import compileall
from pathlib import Path
import re
import subprocess
import sys

from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    for name in ('database','handlers','keyboards','services','states','tests','tools','utils'):
        if not compileall.compile_dir(str(ROOT/name),quiet=1):
            raise SystemExit('Compilation failed')
    for path in ROOT.glob('*.py'):
        if not compileall.compile_file(str(path),quiet=1):
            raise SystemExit('Compilation failed')
    files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    secrets=[value for key,value in dotenv_values(ROOT/'.env',interpolate=False).items()
             if key in {'BOT_TOKEN','IMGBB_API_KEY','ADMIN_ACTIVATION_CODE','TELEGRAM_PROXY'} and value and len(value)>=8]
    patterns=[re.compile(r'(?<!\d)\d{6,}:[A-Za-z0-9_-]{30,}\b'),re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
    allowed={'123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'} # synthetic redaction fixture
    violations=[]
    for name in files:
        path=ROOT/name
        if not path.is_file():
            continue
        text=path.read_text(encoding='utf-8',errors='replace')
        if any(value in text for value in secrets) or any(match.group() not in allowed for pattern in patterns for match in pattern.finditer(text)):
            violations.append(name)
    if any(name in {'.env','.env.test'} for name in files):
        violations.append('Tracked environment secret file')
    if violations:
        raise SystemExit('Secret-pattern matches in files: '+', '.join(violations))
    # Check repository history against current local secret values without exposing patches.
    history=subprocess.check_output(['git','log','--all','-p','--format='],cwd=ROOT)
    if any(value.encode() in history for value in secrets):
        raise SystemExit('A current secret appears in Git history; rotate before release')
    subprocess.run(['git','diff','--check'],cwd=ROOT,check=True)
    print('Python compilation, Git diff, tracked/new secret scan and current-secret history scan: OK')


if __name__=='__main__':
    main()
