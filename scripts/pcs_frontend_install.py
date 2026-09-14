#!/usr/bin/env python3
"""Transactional Debian nginx/control-panel handoff, called by the PCS installer.

Run only on the deployment target as root. This does not install credentials or
change the privileged dispatcher. Backups survive failures for manual recovery.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


REPO = Path(__file__).resolve().parents[1]
NGINX = Path("/etc/nginx")
UNITS = Path("/etc/systemd/system")
BACKUP_ROOT = Path("/var/backups/pcs-frontend")
WEB_ROOT = Path("/var/www/pcs")
WEB_RELEASES = Path("/var/www/pcs-releases")
SERVICES = ("pcs-control-panel.service", "pcs-dashboard-redirect.service", "nginx.service")


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True)


def state(service, operation):
    result = subprocess.run(
        ["systemctl", operation, service], text=True, capture_output=True, check=False
    )
    value = result.stdout.strip()
    if not value and operation == "is-enabled":
        # Debian systemd versions can report missing units only on stderr.
        loaded = run("systemctl", "show", "--property=LoadState", "--value", service).stdout.strip()
        if loaded == "not-found":
            return "not-found"
        raise RuntimeError(f"Cannot determine enablement of {service}: {result.stderr.strip()}")
    return value


def install_nginx():
    if shutil.which("nginx"):
        return
    # Prevent package postinst from taking :80 before validation and handoff.
    if state("nginx.service", "is-enabled").startswith("masked"):
        raise RuntimeError("nginx is masked; resolve that explicit policy before installation")
    run("systemctl", "mask", "--runtime", "nginx.service")
    try:
        apt("update")
        apt("install", "-y", "nginx")
    finally:
        run("systemctl", "unmask", "--runtime", "nginx.service")


def apt(*args):
    # A fresh image may still have cloud-init or apt-daily holding apt's lock.
    # Retry lock contention only; report network/package failures immediately.
    for attempt in range(31):
        try:
            return run("apt-get", "-o", "DPkg::Lock::Timeout=60", *args)
        except subprocess.CalledProcessError as error:
            if attempt == 30 or not any(term in (error.stderr or "") for term in ("Could not get lock", "Unable to acquire")):
                raise
            time.sleep(2)


def atomic_copy(source, target):
    """Replace a file in its own filesystem without exposing partial contents."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pcs-", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(paths, backup):
    records = []
    for index, path in enumerate(paths):
        saved = backup / str(index)
        if path.is_symlink():
            records.append((path, "link", os.readlink(path)))
        elif path.exists():
            if not path.is_file():
                raise RuntimeError(f"Refusing to replace non-file: {path}")
            shutil.copy2(path, saved)
            records.append((path, "file", str(saved)))
        else:
            records.append((path, "absent", ""))
    (backup / "files.json").write_text(
        json.dumps([(str(p), kind, value) for p, kind, value in records], indent=2) + "\n"
    )
    return records


def restore(records):
    for path, kind, value in reversed(records):
        if kind == "file":
            atomic_copy(Path(value), path)
        else:
            path.unlink(missing_ok=True)
            if kind == "link":
                path.symlink_to(value)


def stage_site():
    """Copy only public site assets; never serve the repo or follow symlinks."""
    source = REPO / "web/pcs-home"
    required = ["index.html", "css/pcs.css", "js/pcs.js"] + [
        f"{page}/index.html" for page in ("files", "docs", "radio", "pistar", "starlink")
    ]
    for relative in required:
        path = source / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing or unsafe static source: {path}")
    WEB_RELEASES.mkdir(parents=True, exist_ok=True)
    release = Path(tempfile.mkdtemp(prefix="site-", dir=WEB_RELEASES))
    release.chmod(0o755)
    for relative in required:
        target = release / ("assets/" + relative if relative.startswith(("css/", "js/")) else relative)
        atomic_copy(source / relative, target)
        target.chmod(0o644)
    for directory in release.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o755)
    return release


def activate_site(release):
    WEB_ROOT.parent.mkdir(parents=True, exist_ok=True)
    temporary = WEB_ROOT.with_name(".pcs-link-" + release.name)
    try:
        temporary.symlink_to(release, target_is_directory=True)
        os.replace(temporary, WEB_ROOT)
    finally:
        temporary.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, method="GET", timeout=90):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(urllib.request.Request(url, method=method), timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, response.headers, response.read()


def check_endpoints():
    for attempt in range(15):
        try:
            status, _, body = request("http://127.0.0.1:8081/health", timeout=5)
            if status == 200 and json.loads(body).get("service") == "pcs-control-panel":
                break
        except (OSError, ValueError):
            pass
        if attempt == 14:
            raise RuntimeError("Local backend did not become healthy")
        time.sleep(1)
    status, _, body = request("http://127.0.0.1/")
    if status != 200 or b"LOCAL OPERATIONS" not in body:
        raise RuntimeError("Static homepage failed")
    for path, mime in (("/assets/css/pcs.css", "text/css"), ("/assets/js/pcs.js", "application/javascript"), ("/files/", "text/html"), ("/docs/", "text/html"), ("/radio/", "text/html"), ("/pistar/", "text/html"), ("/starlink/", "text/html")):
        status, headers, body = request("http://127.0.0.1" + path)
        if status != 200 or mime not in headers.get("Content-Type", "") or not body:
            raise RuntimeError(f"Static asset/page failed: {path}")
    status, _, body = request("http://127.0.0.1/status/")
    if status != 200 or b"Field Network Status" not in body:
        raise RuntimeError("Detailed public status failed")
    status, _, body = request("http://127.0.0.1/api/public-status")
    if status != 200 or json.loads(body).get("overall") not in {"ok", "warn", "bad"}:
        raise RuntimeError("Public status JSON failed")
    for path, target in (("/admin", "/admin/"), ("/admin/", "/admin/login")):
        status, headers, _ = request("http://127.0.0.1" + path, "HEAD")
        if status != 303 or headers.get("Location") != target:
            raise RuntimeError(f"Admin redirect failed: {path}")
    status, headers, body = request("http://127.0.0.1/admin/login")
    if status != 200 or "pcs_login_csrf=" not in headers.get("Set-Cookie", ""):
        raise RuntimeError("Admin login/CSRF cookie failed")
    status, headers, _ = request("http://127.0.0.1:8080/", "HEAD")
    if status != 308 or headers.get("Location") != "http://127.0.0.1/admin/":
        raise RuntimeError("Legacy redirect failed")
    listeners = run("ss", "-H", "-ltnp").stdout.splitlines()
    backend = [line for line in listeners if line.split()[3].endswith(":8081")]
    frontend = [line for line in listeners if line.split()[3].endswith(":80")]
    if not backend or any(line.split()[3] != "127.0.0.1:8081" or "python" not in line for line in backend):
        raise RuntimeError("Backend is not exclusively Python on 127.0.0.1:8081")
    if not frontend or any("nginx" not in line for line in frontend):
        raise RuntimeError("nginx is not the exclusive port 80 listener")


def migrate():
    sources = [REPO / "config/nginx/pcs.conf"] + [
        REPO / "systemd" / name for name in SERVICES[:2]
    ]
    for source in sources:
        if not source.is_file():
            raise RuntimeError(f"Missing source: {source}")
    install_nginx()
    previous = {name: (state(name, "is-active"), state(name, "is-enabled")) for name in SERVICES}
    for name, (_, enabled) in previous.items():
        if enabled not in {"enabled", "disabled", "not-found"}:
            raise RuntimeError(f"Resolve nonstandard service policy before migration: {name}: {enabled}")
    backup_root = BACKUP_ROOT
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="migration-", dir=backup_root))
    site = NGINX / "sites-available/pcs"
    enabled_site = NGINX / "sites-enabled/pcs"
    default_site = NGINX / "sites-enabled/default"
    records = snapshot([site, enabled_site, default_site, WEB_ROOT] + [UNITS / name for name in SERVICES[:2]], backup)
    (backup / "services.json").write_text(json.dumps(previous, indent=2) + "\n")
    # Standalone parse before touching the installed configuration.
    staged = backup / "nginx.conf"
    staged.write_text("events {}\nhttp {\n" + sources[0].read_text() + "\n}\n")
    run("nginx", "-t", "-c", str(staged))
    release = stage_site()
    services_changed = False
    try:
        activate_site(release)
        atomic_copy(sources[0], site)
        enabled_site.parent.mkdir(parents=True, exist_ok=True)
        enabled_site.unlink(missing_ok=True)
        enabled_site.symlink_to(site)
        default_site.unlink(missing_ok=True)
        # Also validate all other installed vhosts/includes, before downtime.
        run("nginx", "-t")
        for source in sources[1:]:
            atomic_copy(source, UNITS / source.name)
        services_changed = True
        run("systemctl", "stop", *SERVICES[:2])
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", *SERVICES)
        run("systemctl", "restart", SERVICES[0])
        run("systemctl", "restart", SERVICES[1])
        run("nginx", "-t")
        run("systemctl", "reload-or-restart", "nginx.service")
        check_endpoints()
    except BaseException:
        print(f"Frontend migration failed; restoring backup: {backup}", flush=True)
        try:
            if services_changed:
                run("systemctl", "stop", *SERVICES)
                for name, (_, enabled) in previous.items():
                    if enabled == "not-found":
                        # Remove newly created enablement links while the unit
                        # still exists; restore() then removes its unit file.
                        run("systemctl", "disable", name)
            restore(records)
            if services_changed:
                run("systemctl", "daemon-reload")
                for name, (active, enabled) in previous.items():
                    # A newly installed unit has been removed by restore().
                    if enabled == "not-found":
                        continue
                    run("systemctl", "enable" if enabled == "enabled" else "disable", name)
                    if active == "active":
                        if name == "nginx.service":
                            run("nginx", "-t")
                        run("systemctl", "start", name)
        except BaseException as rollback_error:
            raise RuntimeError(f"Rollback failed; recover using {backup}: {rollback_error}") from rollback_error
        raise
    print(f"Frontend migration verified. Previous configuration retained at {backup}")


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run via setup-pcs-control-panel.sh (root required for the handoff).")
    try:
        migrate()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Command failed: {error.cmd}\n{error.stdout}\n{error.stderr}") from error
