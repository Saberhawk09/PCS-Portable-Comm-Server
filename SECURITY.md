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
