"""Bounded, private qualification records. No PCS configuration is stored here."""
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

RUNTIME = Path('/run/pcs-qualification')
SESSIONS = Path('/var/lib/pcs-qualification/sessions')
RESULTS = ('PASS', 'PASS WITH OBSERVATION', 'FAIL', 'INCONCLUSIVE', 'ABORTED',
           'HARNESS ERROR', 'BLOCKED')
ID = re.compile(r'^[0-9a-f]{32}$')
MAX_FILE = 2 * 1024 * 1024
MAX_SESSIONS = 32
MAX_TOTAL = 64 * 1024 * 1024
MIN_FREE = 128 * 1024 * 1024


class HarnessError(ValueError):
    """Only fixed codes, never raw OS/collector messages, go into evidence."""


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise HarnessError('invalid_session_id')
    return value


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def boot_id():
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def stamp():
    return {'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'monotonic': time.monotonic(), 'boot_id': boot_id()}


def private_dir(path):
    path = Path(path)
    # Check each existing ancestor, including symlinks to directories.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise HarnessError('unsafe_directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HarnessError('unsafe_directory_permissions')
    return path


def read_json(path, limit=MAX_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise HarnessError('not_regular_file')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise HarnessError('record_too_large')
    try:
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise HarnessError('invalid_json') from None


def atomic_json(path, data):
    path = Path(path)
    raw = (json.dumps(data, sort_keys=True, allow_nan=False) + '\n').encode()
    if len(raw) > MAX_FILE:
        raise HarnessError('record_too_large')
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise HarnessError('unsafe_record')
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path, blocking=False):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise HarnessError('unsafe_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise HarnessError('campaign_busy') from None
        yield
    finally:
        os.close(fd)


def storage_ready(root=SESSIONS):
    """Never prune unknown names or active sessions. Fail closed if capacity is full."""
    private_dir(root)
    entries = list(root.iterdir())
    if len(entries) >= MAX_SESSIONS:
        raise HarnessError('session_limit_export_and_remove_completed_sessions')
    total = 0
    for entry in entries:
        identifier(entry.name)
        if entry.is_symlink() or not entry.is_dir():
            raise HarnessError('unsafe_session_storage')
        for child in entry.iterdir():
            if child.is_symlink() or not child.is_file():
                raise HarnessError('unsafe_session_storage')
            total += child.stat().st_size
    if total > MAX_TOTAL - 3 * MAX_FILE or shutil.disk_usage(root).free < MIN_FREE:
        raise HarnessError('storage_limit')


class Session:
    def __init__(self, scenario, root=SESSIONS):
        storage_ready(root)
        self.id = uuid.uuid4().hex
        self.path = private_dir(root / self.id)
        self.seq = 0
        self.manifest = {'version': 1, 'session': self.id, 'scenario': scenario,
                         'start': stamp(), 'result': None, 'reason': 'running', 'complete': False}
        atomic_json(self.path / 'session.json', self.manifest)
        self.event('start', {'scenario': scenario})

    def event(self, kind, evidence):
        row = {'version': 1, 'session': self.id, 'seq': self.seq,
               **stamp(), 'event': kind, 'evidence': evidence}
        raw = (json.dumps(row, sort_keys=True, allow_nan=False) + '\n').encode()
        path = self.path / 'events.jsonl'
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as stream:
            if os.fstat(stream.fileno()).st_size + len(raw) > MAX_FILE - 8192:
                raise HarnessError('event_limit')
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.seq += 1

    def finish(self, result, reason):
        if result not in RESULTS:
            raise HarnessError('invalid_result')
        self.manifest.update(result=result, reason=reason, end=stamp(), complete=False)
        # Terminal manifest precedes report: an interrupted report never implies PASS.
        atomic_json(self.path / 'session.json', self.manifest)
        self.event('result', {'result': result, 'reason': reason})
        report(self.path, pending=True)
        self.manifest['complete'] = True
        atomic_json(self.path / 'session.json', self.manifest)


def report(path, pending=False):
    manifest = read_json(path / 'session.json')
    identifier(manifest['session'])
    result = manifest.get('result')
    if result not in RESULTS or (not pending and manifest.get('complete') is not True):
        raise HarnessError('unfinished_session')
    # Values here are fixed registry/codes written by the harness, not source messages.
    text = ('# PCS qualification\n\n'
            f"Session: {manifest['session']}\n\n"
            f"Result: **{result}**\n\n"
            f"Scenario: {manifest['scenario']}\n\n"
            f"Reason: {manifest['reason']}\n\n"
            'Evidence: `events.jsonl`; timestamps include UTC, monotonic seconds and boot ID.\n\n'
            'Observation results do not qualify WAN injection, RF, cold boot, or physical hardware.\n')
    if (path / 'witness.json').exists():
        witness = read_json(path / 'witness.json')
        status = witness.get('result')
        if status not in RESULTS:
            raise HarnessError('invalid_witness_result')
        text += ('\nIndependent client HTTP sampling: **' + status + '**. '
                 'See witness.json. No proven cross-host time alignment or WAN-route continuity.\n')
    fd = os.open(path / 'report.md', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
