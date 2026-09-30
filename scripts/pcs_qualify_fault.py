"""Fixed IPv4 fault lifecycle. Internal API; no CLI or arbitrary effect registry."""
import json
import os
from pathlib import Path
import stat
import time

from pcs_qualify_observe import command
from pcs_qualify_state import (HarnessError, RUNTIME, atomic_json, boot_id,
                               identifier, lock, read_json)
from pcs_qualify_wan import TABLE, OWNER, CELLULAR_OWNER, create_plan, create_cellular_plan, delete_plan, owned_handle


def table_owner(document, session):
    for owner in (OWNER, CELLULAR_OWNER):
        try:
            owned_handle(document, session, owner)
            return owner
        except HarnessError:
            pass
    raise HarnessError('firewall_ownership_unknown')


def table():
    """Absence is determined by a successful list, never an ignored nft error."""
    document = json.loads(command(['/usr/sbin/nft', '-j', 'list', 'tables']))
    rows = document.get('nftables') if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise HarnessError('firewall_inventory_unavailable')
    matches = [row['table'] for row in rows if isinstance(row, dict) and
               isinstance(row.get('table'), dict) and row['table'].get('family') == 'inet'
               and row['table'].get('name') == TABLE]
    if not matches:
        return None
    if len(matches) != 1:
        raise HarnessError('firewall_inventory_ambiguous')
    return json.loads(command(['/usr/sbin/nft', '-j', 'list', 'table', 'inet', TABLE]))


def apply(text, runtime=RUNTIME):
    # Only fixed compiler output is passed by callers. Bound subprocess time and
    # output using the existing collector; never put the transaction in argv/logs.
    path = runtime / 'nft.batch'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise HarnessError('unsafe_firewall_batch')
        os.ftruncate(fd, 0)
        with os.fdopen(fd, 'w', encoding='ascii', closefd=False) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(fd)
        command(['/usr/sbin/nft', '-f', str(path)])
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def cleanup(session, runtime=RUNTIME):
    identifier(session)
    document = table()
    if document is None:
        return
    apply(delete_plan(document, session, table_owner(document, session)), runtime)
    if table() is not None:
        raise HarnessError('firewall_cleanup_unverified')


def cleanup_record(runtime=RUNTIME):
    path = runtime / 'wan.json'
    if not path.exists():
        return
    value = read_json(path, 4096)
    if (not isinstance(value, dict) or set(value) != {'version', 'session', 'boot_id'} or
            value.get('version') != 1 or value.get('boot_id') != boot_id()):
        raise HarnessError('firewall_lease_corrupt')
    cleanup(identifier(value.get('session')), runtime)
    (runtime / 'nft.batch').unlink(missing_ok=True)
    # Keep the ownership record until cleanup has been verified.
    path.unlink()


def cleanup_orphan(runtime=RUNTIME):
    """Boot: runtime ledger is gone. Only our exact table comment is authority.

    A foreign same-name table is left untouched and blocks cleanup. No production
    table or manifest-specified name, handle, command or interface is trusted.
    """
    if not Path('/usr/sbin/nft').is_file():
        return  # Phase 1 installation does not require nftables.
    document = table()
    if document is None:
        return
    rows = [r['table'] for r in document.get('nftables', [])
            if isinstance(r, dict) and isinstance(r.get('table'), dict)]
    if len(rows) != 1 or not isinstance(rows[0].get('comment'), str):
        raise HarnessError('firewall_ownership_unknown')
    comment = rows[0]['comment']
    owners = [owner for owner in (OWNER, CELLULAR_OWNER) if comment.startswith(owner)]
    if len(owners) != 1:
        raise HarnessError('firewall_ownership_unknown')
    session = identifier(comment[len(owners[0]):])
    owned_handle(document, session, owners[0])
    cleanup(session, runtime)


def arm_wan(session, target, seconds, lan_cidr, wan_cidr, runtime=RUNTIME, verify=None):
    """Timer first, durable ownership before effect, kernel timeout as fallback.

    Caller must hold campaign.lock and complete control/witness preflight. No
    refresh/renewal exists. The extra ten seconds permit kernel expiry before
    independent table removal. Any uncertain nft commit is inspected in restore.
    """
    from pcs_qualify_safety import arm, lease_valid, restore
    plan = create_plan(session, target, seconds, lan_cidr, wan_cidr)
    _arm_plan(session, plan, seconds, runtime, verify, OWNER)


def arm_cellular(session, ethernet, wifi, seconds, lan, ethernet_net, wifi_net, runtime=RUNTIME, verify=None):
    plan = create_cellular_plan(session, ethernet, wifi, seconds, lan, ethernet_net, wifi_net)
    _arm_plan(session, plan, seconds, runtime, verify, CELLULAR_OWNER)


def _arm_plan(session, plan, seconds, runtime, verify, owner):
    from pcs_qualify_safety import arm, lease_valid, restore
    if table() is not None or (runtime / 'wan.json').exists():
        raise HarnessError('firewall_table_collision')
    arm(session, seconds + 10, runtime)
    try:
        with lock(runtime / 'mutation.lock'):
            if not lease_valid(session, runtime):
                raise HarnessError('lease_expired_before_injection')
            # Enough remaining timer lifetime for the full kernel timeout plus
            # bounded command execution. Refuse instead of extending a lease.
            lease = read_json(runtime / 'active.json', 4096)
            if lease['deadline'] - time.monotonic() < seconds + 5:
                raise HarnessError('lease_setup_too_slow')
            if verify is not None:
                verify()  # Internal fixed-scenario recheck, never a user-supplied command.
            if lease['deadline'] - time.monotonic() < seconds + 5:
                raise HarnessError('lease_setup_too_slow')
            atomic_json(runtime / 'wan.json', {'version': 1, 'session': session, 'boot_id': boot_id()})
            lease.update(effect='nft-v4', state='prepared')
            atomic_json(runtime / 'active.json', lease)
            apply(plan, runtime)
            document = table()
            owned_handle(document, session, owner)
            lease['state'] = 'active'
            atomic_json(runtime / 'active.json', lease)
    except BaseException:
        restore(runtime, expected_session=session)
        raise
