"""Write the manufacturing package and the documentation renders.

    python tools/make_outputs.py

Uses kicad-cli only, so any Python will do. Gerbers and drill files land in manufacturing/gerbers/
and in one zip for ordering; the schematic PDF and the three renders sit next to the project.
"""
from pathlib import Path
import os, shutil, subprocess, sys, zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'solderdemon_6502-audio'
REV = 'r1'
CLI = os.environ.get('KICAD_CLI', 'C:/Program Files/KiCad/10.0/bin/kicad-cli.exe')
OUT = ROOT / 'manufacturing'
GERBERS = OUT / 'gerbers'
shutil.rmtree(GERBERS, ignore_errors=True)
GERBERS.mkdir(parents=True)
for old in OUT.glob('*-gerbers.zip'):
    old.unlink()

pcb = str(ROOT / f'{NAME}.kicad_pcb')
sch = str(ROOT / f'{NAME}.kicad_sch')


def run(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    if r.returncode:
        print(' '.join(args), '->', r.stdout.strip(), r.stderr.strip())
    return r.returncode == 0


ok = [
    run('pcb', 'export', 'gerbers', '-o', str(GERBERS),
        '-l', 'F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,Edge.Cuts', pcb),
    run('pcb', 'export', 'drill', '-o', str(GERBERS) + '/', '--format', 'excellon',
        '--drill-origin', 'absolute', '--excellon-units', 'mm', '--excellon-separate-th',
        '--generate-report', '--report-path', str(OUT / 'drill-report.txt'), pcb),
    run('sch', 'export', 'pdf', '-o', str(ROOT / f'{NAME}.pdf'), sch),
]
with zipfile.ZipFile(OUT / f'{NAME}-{REV}-gerbers.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for f in sorted(GERBERS.iterdir()):
        z.write(f, f.name)
for side in ('top', 'bottom'):
    ok.append(run('pcb', 'render', '-o', str(ROOT / f'{NAME}-{side}.png'), '--side', side,
                  '--quality', 'high', '--width', '1376', '--height', '1232', pcb))
ok.append(run('pcb', 'render', '-o', str(ROOT / f'{NAME}-3d.png'), '--side', 'top', '--rotate', '-40,0,25',
              '--quality', 'high', '--width', '1600', '--height', '1200', pcb))

print(f'{len(list(GERBERS.iterdir()))} files in manufacturing/gerbers/:', ', '.join(sorted(p.name for p in GERBERS.iterdir())))
sys.exit(0 if all(ok) else 1)
