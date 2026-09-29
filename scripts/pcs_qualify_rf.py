"""Read-only RF admission for the commissioned GPIO6 APRS arrangement."""
import re

from pcs_qualify_observe import command
from pcs_qualify_state import HarnessError

UNITS = ('direwolf.service', 'graywolf.service', 'pcs-aprs-ptt-safe.service',
         'pcs-direwolf-uplink-recovery.service')
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job')


class RFBlocked(HarnessError):
    pass


def states():
    rows = []
    for unit in UNITS:
        raw = command(['/usr/bin/systemctl', 'show', '--property=' + ','.join(FIELDS), unit])
        row = {}
        for line in raw.splitlines():
            key, separator, value = line.partition('=')
            if not separator or key not in FIELDS or key in row:
                raise HarnessError('rf_service_schema')
            row[key] = value
        if set(row) != set(FIELDS):
            raise HarnessError('rf_service_schema')
        rows.append(row)
    return rows


def idle(row, absent=False):
    return (row['LoadState'] in (('loaded', 'masked', 'not-found') if absent else ('loaded', 'masked'))
            and row['ActiveState'] == 'inactive' and row['SubState'] == 'dead'
            and row['MainPID'] == '0' and row['ControlPID'] == '0' and row['Job'] == '')


def observe():
    # Only fixed enums leave this module. Never export helper output or RF config.
    evidence = dict(gate='BLOCKED', reason='rf_state_unverified', direwolf='unknown',
                    graywolf='unknown', recovery='unknown', ptt_safe='unverified',
                    consequence='unverified')
    try:
        before = states()
        direwolf, graywolf, guard, recovery = before
        for name, row in (('direwolf', direwolf), ('graywolf', graywolf)):
            if row['ActiveState'] in ('active', 'inactive'):
                evidence[name] = row['ActiveState']
        evidence['recovery'] = {'loaded': 'installed', 'masked': 'masked',
                                'not-found': 'absent'}.get(recovery['LoadState'], 'unknown')
        if any(row['ActiveState'] == 'active' for row in (direwolf, graywolf)):
            evidence['reason'] = 'rf_engine_active'
            return evidence
        if not idle(direwolf) or not idle(graywolf, absent=True):
            return evidence
        # An already-running recovery may have passed its final active check and
        # be between stop and start. Wait for quiescence; never cancel that job.
        if not idle(recovery, absent=True):
            evidence['reason'] = 'rf_recovery_not_idle'
            return evidence
        evidence['reason'] = 'rf_ptt_unverified'
        if not (guard['LoadState'] == 'loaded' and guard['ActiveState'] == 'active'
                and guard['SubState'] == 'running' and guard['MainPID'].isdigit()
                and int(guard['MainPID']) > 0 and guard['ControlPID'] == '0' and guard['Job'] == ''):
            return evidence
        output = command(['/usr/local/sbin/pcs-aprs-ptt-safe', '--check'])
        lines = output.strip().splitlines()
        # gpioinfo v2 --chip output for exactly one line, then the helper summary.
        # This gate deliberately supports the commissioned gpiochip0/GPIO6 only.
        if (len(lines) != 2 or not re.fullmatch(
                r'gpiochip0\s+6\s+"GPIO6"\s+output\s+(?:bias=pull-down\s+)?consumer="pcs-ptt-safe"\s*', lines[0])
                or lines[1] not in ('PCS APRS PTT guard holds gpiochip0 line 6 low.',
                                    'PCS APRS PTT guard holds /dev/gpiochip0 line 6 low.')):
            return evidence
        # Same observation used by the PCS watchdog, never its corrective action.
        level = command(['/usr/bin/pinctrl', 'get', '6']).strip()
        if not re.fullmatch(r'6:\s+op\s+dl\s+pd\s+\| lo\s+// GPIO6 = output', level):
            return evidence
        if states() != before:
            evidence['reason'] = 'rf_state_changed'
            return evidence
        evidence.update(gate='PASS', reason='rf_quiescent', ptt_safe='PASS',
                        consequence='bounded_inactive_engines')
    except (OSError, ValueError, TypeError):
        pass
    return evidence


def require_safe(session=None, phase='preflight'):
    evidence = observe()
    if session is not None:
        session.event('rf_safety', dict(phase=phase, **evidence))
        session.manifest['rf_safety'] = evidence
    if evidence['gate'] != 'PASS':
        raise RFBlocked(evidence['reason'])
    return evidence
