#!/usr/bin/env python3
"""Explicit disposable-VM test of systemd's pre-start baud environment handoff.

Creates only temporary test units. Never run as appliance acceptance or deploy.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main():
    if sys.argv[1:] != ['--run'] or os.geteuid() != 0:
        raise SystemExit('Use --run as root in a disposable systemd VM only')
    for baud in (115200, 38400):
        unit = 'pcs-gnss-env-fixture-' + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='gnss-env-fixture-', dir='/run') as temporary:
            root = Path(temporary)
            env, writer, checker = (root / name for name in ('gpsd.env', 'writer.py', 'checker.py'))
            expected = f'-n -b -s {baud} -f 8N1'
            writer.write_text('from pathlib import Path\nPath(' + repr(str(env)) + ').write_text(' +
                              repr('GPSD_OPTIONS="' + expected + '"\n') + ')\n')
            checker.write_text('import os\nassert os.environ["GPSD_OPTIONS"] == ' + repr(expected) + '\n')
            try:
                subprocess.run(['systemd-run', '--quiet', '--unit=' + unit, '--wait', '--pipe', '--collect',
                    '--property=EnvironmentFile=-' + str(env),
                    '--property=ExecStartPre=/usr/bin/python3 ' + str(writer),
                    '/usr/bin/python3', str(checker)], check=True, timeout=30)
            finally:
                subprocess.run(['systemctl', 'stop', unit], capture_output=True, timeout=10)
                subprocess.run(['systemctl', 'reset-failed', unit], capture_output=True, timeout=10)
        print('PASS: systemd ExecStart received freshly prepared baud', baud)


if __name__ == '__main__':
    main()
