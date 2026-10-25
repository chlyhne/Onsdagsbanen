"""Build the local paper, with all intermediates in build/ and PDF in output/."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import os
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    build = root / 'build' / 'latex'
    logs = root / 'build' / 'logs'
    (build / 'tikz').mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['TEXINPUTS'] = str(build) + '//' + os.pathsep + environment.get('TEXINPUTS', '')
    stages = [
        ('initial', ['pdflatex', '-synctex=1', '-halt-on-error', '-interaction=nonstopmode',
                     '-output-directory=build/latex', 'main.tex']),
        ('figures', [sys.executable, 'build_external_plots.py', '--tex-file', 'main.tex']),
        ('final', ['latexmk', '-g', '-pdf', '-synctex=1', '-halt-on-error', '-interaction=nonstopmode', 'main.tex']),
    ]
    for name, command in stages:
        print(f'Paper: {name} (log: {logs / (name + ".log")})', flush=True)
        with (logs / (name + '.log')).open('w', encoding='utf8') as log:
            result = subprocess.run(command, cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            print('\n'.join((logs / (name + '.log')).read_text(errors='replace').splitlines()[-30:]))
            return result.returncode
    output = root / 'output' / 'main.pdf'
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and hashlib.sha256(output.read_bytes()).digest() != hashlib.sha256((build / 'main.pdf').read_bytes()).digest():
        archive = output.parent / 'archive'
        archive.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        shutil.copy2(output, archive / f'main-{stamp}.pdf')
    shutil.copy2(build / 'main.pdf', output)
    print(f'PDF: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
