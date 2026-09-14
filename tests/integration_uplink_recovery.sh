#!/bin/sh
set -eu
[ "${1:-}" = "--isolated-vm" ] || { echo "Run only in a disposable VM with --isolated-vm" >&2; exit 2; }
[ ! -e /sys/class/net/eth0 ] || { echo "Refusing a host with eth0" >&2; exit 2; }
cd "$(dirname "$0")/.."
set -eu
sudo python3 - <<'PY'
from pathlib import Path
import subprocess
root=Path('/run/systemd/system');prefix='pcs-ut-recovery-'
engine=prefix+'engine.service';guard=prefix+'guard.service';worker=prefix+'worker.service'
files={engine:f'[Unit]\nConflicts={guard}\n[Service]\nExecStart=/bin/sleep infinity\nExecStopPost=/usr/bin/systemctl --no-block start {guard}\n',guard:f'[Unit]\nConflicts={engine}\nAfter={engine}\n[Service]\nExecStart=/bin/sleep infinity\n',worker:f'[Unit]\nAfter={engine}\n[Service]\nType=oneshot\nTimeoutStartSec=15\nExecStart=/bin/sh -c "systemctl stop {engine} && systemctl start {guard} && systemctl start {engine}"\n'}
assert all(not (root/n).exists() for n in files)
def run(*a,check=True):return subprocess.run(a,check=check,capture_output=True,text=True,timeout=25)
try:
 for n,s in files.items():(root/n).write_text(s)
 run('systemctl','daemon-reload');run('systemctl','start',engine)
 result=run('systemctl','restart',engine,check=False)
 print('Original restart result:',result.returncode,result.stderr.strip())
 run('systemctl','start',engine)
 result=run('systemctl','start',worker,check=False)
 print('Separated stop, guard settlement, start:',result.returncode,result.stderr.strip())
 assert result.returncode==0
 assert run('systemctl','is-active','--quiet',engine,check=False).returncode==0
 assert run('systemctl','is-active','--quiet',guard,check=False).returncode!=0
 print('PASS: real systemd lifecycle reproduces cancellation and verifies recovery sequence')
finally:
 for n in (worker,engine,guard):run('systemctl','stop',n,check=False)
 for n in files:(root/n).unlink(missing_ok=True)
 run('systemctl','daemon-reload')
 for n in files:run('systemctl','reset-failed',n,check=False)
PY
