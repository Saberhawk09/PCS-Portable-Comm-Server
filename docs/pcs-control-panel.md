# PCS Homepage and Control Panel

PCS provides a public field-status homepage and a password-protected administrative control panel on the trusted local PCS network.

```text
Public homepage:  http://10.42.0.1/
Admin login:      http://10.42.0.1/admin/
Cockpit:          https://10.42.0.1:9090
```

The public homepage contains a visible **Admin Login** button and administration panel. Operators do not need to enter the legacy port manually.

## Public Homepage

The homepage is read-only and does not require authentication. It shows an explicitly limited status data set:

- Overall health, uptime, local time, temperature, load, memory, and storage use
- Internet availability, active uplink, OpenWrt status, and client count
- Sanitized cellular connection, carrier, access technology, and signal category
- GNSS fix, satellites, exact coordinates, Maidenhead grid square, and GNSS time
- Chrony synchronization and current time source
- USB, `PCS-Share`, and `PCS-Backup` availability and free space
- Local file-share, NTP, GPSD, Cockpit, and OpenWrt access information
- Pi-Star status and link only when Pi-Star integration is configured

Exact coordinates and grid square are intentionally public to clients on site. The public data does not include modem or SIM identifiers, Wi-Fi secrets, detailed client identity or MAC addresses, arbitrary command output, or administrative actions.

The public JSON used by the page is also available read-only at:

```text
http://10.42.0.1/api/public-status
```

## Administrative Control Panel

The `/admin/` area contains the detailed dashboard and all operator action groups:

- Health
- Network
- Cellular
- Storage
- Services
- Time / GPS
- Power

Unauthenticated requests are redirected to the Admin Login page. Administrative POST requests require both a valid session and CSRF token. Direct unauthenticated POST requests are rejected before the dispatcher is called.

Dangerous operations retain confirmation prompts. Privileged work continues to use the allowlisted dispatcher:

```text
/usr/local/sbin/pcs-web-action
```

Repository source:

```text
scripts/pcs-web-action.sh
```

The web application cannot submit arbitrary shell commands.

## Authentication Storage

The local password is processed with PBKDF2-HMAC-SHA256 and a random salt. Only the resulting password record is stored.

```text
/etc/pcs-control-panel/admin.json
/etc/pcs-control-panel/session.key
```

These files are kept outside the repository and restricted to `root` and the web-service group. Password changes are written atomically by the root-owned `/usr/local/sbin/pcs-admin-password-helper`; plaintext passwords are passed only through local stdin and are not placed in process arguments or logs.

Sessions expire after 30 minutes by default. Session cookies are signed, HTTP-only, and restricted to `/admin` with `SameSite=Strict`. Logout invalidates the server-side session immediately.

The PCS field interface currently uses HTTP. This protects the administrative boundary from unauthenticated use but does not encrypt traffic on the LAN. Keep the interface on the trusted PCS network. HTTPS can be added later if protection against local packet capture becomes necessary.

## Install or Upgrade

Run as the normal `pi` user:

```bash
./scripts/setup-pcs-control-panel.sh
```

On a new interactive installation, the script requires an administrator password. When a password already exists, every interactive repeat install asks whether to preserve it or enter a replacement. This makes password configuration repeatable without silently overwriting a working credential.

During a noninteractive fresh install, the public homepage is installed but administration remains locked until a password is configured. This prevents a default or generated password from being exposed in logs.

## Reset the Admin Password

An authenticated operator can select **Change Admin Password** on the administration page. The current password, a new password, and confirmation are required. A successful change invalidates every active admin session and returns the operator to Admin Login.

A forgotten password cannot be recovered or replaced from the browser. Rerun the installer from an interactive Pi terminal:

Run from an interactive PCS terminal:

```bash
./scripts/setup-pcs-control-panel.sh --reset-admin-password
```

The password must be at least 12 characters. The reset command prompts securely, replaces only the local password hash, preserves the session signing key, and restarts the service. `--reset-admin-password` intentionally fails without an interactive terminal so a replacement password cannot leak into automation logs.

Restarting the service invalidates any sessions held in memory:

```bash
sudo systemctl restart pcs-control-panel.service
```

## Services and Ports

```text
pcs-control-panel.service          port 80, public and authenticated routes
pcs-dashboard-redirect.service    port 8080, legacy compatibility redirect
```

The old redirect service no longer owns port 80. After the unified service is installed, old bookmarks such as `http://10.42.0.1:8080/` redirect to `http://10.42.0.1/admin/`.

The compatibility redirect remains separate so it can be disabled later without affecting the homepage:

```bash
sudo systemctl disable --now pcs-dashboard-redirect.service
```

## Health Checks

```bash
systemctl status pcs-control-panel.service --no-pager -l
systemctl status pcs-dashboard-redirect.service --no-pager -l
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1/api/public-status
curl -I http://127.0.0.1/admin/
curl -fsS http://127.0.0.1:8080/health
```

An unauthenticated request to `/admin/` should return HTTP `303` and point to `/admin/login`.

## Public Data Collection

The dispatcher exposes two logical dashboard views:

```text
dashboard-public-json    explicitly allowlisted public status structure
dashboard-json           full administrative cards and client details
```

The public view is constructed deliberately from approved fields. It is not produced by sending unrestricted diagnostic output to the browser and hiding parts with CSS or JavaScript.

Optional subsystems return warning or unavailable values without preventing the rest of the homepage from loading. Pi-Star content is omitted unless configured. APRS is reserved as a future optional data card and remains hidden when unavailable.
