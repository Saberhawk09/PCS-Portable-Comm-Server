# Security Policy

## Runtime Boundary

PCS is designed for a trusted local field network. The unauthenticated LAN
homepage intentionally exposes operational health and current GNSS position to
connected LAN clients. It is called "public" only in contrast to the
password-protected administrator view; it is not intended for Internet
publication. Administrative diagnostics and actions require the local PCS
administrator password.

The current field interface uses HTTP. Do not expose ports `80`, `8080`, `9090`, Samba, GPSD, or SSH directly to the public internet, and do not bridge untrusted clients onto the PCS LAN without an additional security boundary.

The commissioned PCS uses the repository's outbound WireGuard management
client with explicit management-peer `/32` routes and firewall-enforced
asymmetric access: authorized WireGuard sources may administer PCS, while PCS
LAN clients cannot initiate traffic into the tunnel or home network. Fresh
installs remain default-off. Do not weaken this to a default route, a home-LAN
`AllowedIPs` prefix, permissive hub forwarding, or an Internet-exposed PCS
listener. A successful VPN handshake is not sufficient validation; routing and
both endpoint firewalls must be tested.

## Reporting a Vulnerability

Use the repository's private vulnerability-reporting form under **Security > Advisories > Report a vulnerability**. Do not publish an unpatched vulnerability, credential, device identifier, or precise private deployment detail in a public issue.

If private reporting is unavailable, open a minimal issue asking the maintainer to establish private contact. Do not include exploit details or secrets in that issue.

## Repository Secrets

PCS includes cellular modem configuration, network settings, private IP
addressing, and device-specific setup files, and it may later add VPN
configuration. Runtime credentials and appliance backups remain outside Git.

Do not commit:

- Passwords
- API keys
- WireGuard private keys
- Deployment-local WireGuard endpoints, peer inventories, and home firewall dumps
- SIM identifiers, phone numbers, or carrier-account/billing information
- Runtime network dumps from private deployments; the documented `10.42.0.0/24` PCS design is intentionally versioned
- Real modem carrier/account details
- Full router backup files
- Personal identifying information

Example configs should use placeholder values only.

The local PCS Stats API source is default-disabled and requires TLS before its
standalone server starts. Unauthenticated responses use an independent strict
allowlist. Authenticated `stats:read` responses may add existing admin-visible
status details, but authentication never authorizes private or preshared keys,
passcodes, raw logs, arbitrary dispatcher actions, or other credential material.
The separate `admin:actions` scope authorizes only the fixed action names
documented by the API; `admin:password` authorizes only the exact password
change route. Neither scope authorizes arbitrary dispatcher arguments or shell
commands. API tokens are random bearer credentials; only their
SHA-256 digests belong in the root-controlled runtime token store, and neither
raw tokens nor that runtime store belong in Git.

`POST /api/v1/pair` is the only credential-exchange route. It accepts the
existing administrator password only over TLS, applies a separate five-attempt
per-source/five-minute limit, sends the exact request to a fixed root helper
over standard input, and returns a `stats:read` + `admin:actions` +
`admin:password` token once. The local candidate's
`POST /api/v1/admin/password` route separately sends the current and new
password to the fixed root-owned password helper; it does not return either
value. The API account's sudo rule cannot select another helper command, token path, or
system action. Administrative actions require that scope, a separate rate
limit, a one-time 60-second challenge for every state-changing button, a fixed
dispatcher action name, bounded output, and a journal audit record. A
`stats:read`-only token cannot invoke them.
Clients must validate/pin the PCS certificate, erase the administrator password
after enrollment, store the bearer token in platform-protected storage, and
never log either value.

For base setup, the expected secret client export is
`private-config/wg-pcs.conf`, which is ignored by Git. It must be a normal
user-owned, non-symlink file with mode `0600` or `0400`. The importer accepts
only `Address`/`PrivateKey`/a narrowly validated ignored `DNS` value and one
peer's `PublicKey`/optional `PresharedKey`/`Endpoint`/`AllowedIPs`/
`PersistentKeepalive`; command hooks and other `wg-quick` extensions are
rejected. Imported PSKs are stored separately as root-owned mode `0600` and
never written to the readable policy file.

Good example values:

- `example.local`
- `192.0.2.10`
- `10.0.0.1`
- `CHANGE_ME`
- `YOUR_PRIVATE_KEY_HERE`

If sensitive information is accidentally committed, rotate or replace the exposed credential immediately.
