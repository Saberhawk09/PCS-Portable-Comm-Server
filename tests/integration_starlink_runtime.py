#!/usr/bin/env python3
"""Exercise the installer's real venv provisioning in a disposable Debian container.

No PCS configuration, services, network, or dish is accessed. Debian python3-dbus
and python3-venv must already be installed by the container's test setup.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT / 'scripts/setup-starlink-telemetry.sh').read_text()
    provisioning = source.split('# Dependencies finish', 1)[1].split('install -d', 1)[0]
    provisioning = '# Dependencies finish' + provisioning
    with tempfile.TemporaryDirectory(prefix='pcs-starlink-runtime-') as folder:
        runtime = Path(folder) / 'runtime'
        subprocess.run(['/usr/bin/python3', '-m', 'venv', str(runtime)], check=True)
        python = str(runtime / 'bin/python')
        assert subprocess.run([python, '-c', 'import dbus'],
                              capture_output=True).returncode != 0, 'baseline must reproduce missing dbus'
        # Execute the actual installer section against the disposable venv.
        provisioning = provisioning.replace('/opt/pcs-starlink', shlex.quote(str(runtime)))
        assert '/opt/pcs-starlink' not in provisioning
        environment = os.environ | {'ROOT': str(ROOT), 'PATH': '/usr/bin:/bin'}
        for attempt in range(2):
            subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', provisioning],
                           env=environment, check=True)
            subprocess.run([python, '-c',
                'import dbus, grpc, yagrc.reflector; '
                'from google.protobuf import json_format; '
                'assert callable(dbus.SystemBus)'], check=True)
        # Check the RPC dependency closure. Debian's unrelated system packages
        # may expose pip metadata for dependencies satisfied by apt rather than
        # pip (for example apt-listchanges/debconf).
        subprocess.run([python, '-c', '''
from importlib.metadata import requires, version
from pip._vendor.packaging.requirements import Requirement
from pathlib import Path
import sys
pending = Path(sys.argv[1]).read_text().splitlines()
checked = set()
while pending:
    text = pending.pop().strip()
    if not text or text.startswith('#'):
        continue
    requirement = Requirement(text)
    if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
        continue
    assert requirement.specifier.contains(version(requirement.name)), text
    if requirement.name not in checked:
        checked.add(requirement.name)
        pending.extend(requires(requirement.name) or [])
print('Pinned RPC dependency closure consistent:', ', '.join(sorted(checked)))
''', str(ROOT / 'config/starlink-requirements.txt')], check=True)
    print('Starlink runtime: missing-dbus regression, repair and repeat provisioning passed')


if __name__ == '__main__':
    main()
