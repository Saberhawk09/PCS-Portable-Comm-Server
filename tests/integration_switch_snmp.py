#!/usr/bin/env python3
"""Real Net-SNMP round trip, exclusively inside a new network namespace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pcs_switch_monitor as monitor


def main():
    if len(sys.argv) == 1:
        parent = os.readlink('/proc/self/ns/net')
        subprocess.run(['unshare', '--net', sys.executable, __file__, parent], check=True)
        return
    assert os.readlink('/proc/self/ns/net') != sys.argv[1], 'refusing host network'
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    subprocess.run(['ip', 'address', 'add', '10.42.0.4/32', 'dev', 'lo'], check=True)
    with tempfile.TemporaryDirectory(prefix='pcs-snmp-test-') as d:
        folder = Path(d)
        secret = folder / 'community'
        monitor.main(['--generate-community', '--output', str(secret)])
        agent = folder / 'agent.conf'
        agent.write_text('agentaddress udp:10.42.0.4:161\nrocommunity ' + secret.read_text().strip() + '\nsysDescr Synthetic PCS switch fixture\nsysObjectID .1.3.6.1.4.1.8072.3.2.10\n')
        agent.chmod(0o600)
        env = os.environ | {'SNMP_PERSISTENT_DIR': d, 'CREDENTIALS_DIRECTORY': d}
        proc = subprocess.Popen(['/usr/sbin/snmpd', '-f', '-C', '-c', str(agent)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.environ['CREDENTIALS_DIRECTORY'] = d
            cfg = monitor.DEFAULTS | {'enabled': True}
            transport = monitor.NetSnmp(cfg)
            for attempt in range(20):
                try:
                    result = monitor.sample(cfg, transport)
                    break
                except (ValueError, TimeoutError):
                    if proc.poll() is not None: raise AssertionError('synthetic agent exited')
                    if attempt == 19: raise
                    time.sleep(0.1)
            assert result['ports'], 'real walk must decode interface inventory'
            index = int(next(iter(result['ports'])))
            cfg['mapping'] = dict(physical_port=2, ifindex=index, identity=result['identity'], verified_at=time.time())
            status = monitor.Monitor(cfg, monitor.boot_id()).update(monitor.sample(cfg, transport), time.monotonic(), time.time())
            assert status['state'] == 'up', status
            assert status['reachable'] is True
            assert secret.read_text().strip() not in json.dumps(status)
            proc.terminate(); proc.wait(timeout=5)
            start = time.monotonic()
            try: monitor.sample(cfg, transport)
            except TimeoutError: pass
            else: raise AssertionError('stopped agent unexpectedly answered')
            assert time.monotonic() - start < cfg['poll_seconds'] + 1
            print('PASS: isolated real GET/WALK, optional objects, private credentials and bounded agent loss')
        finally:
            if proc.poll() is None:
                proc.terminate(); proc.wait(timeout=5)


if __name__ == '__main__': main()
