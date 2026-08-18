# Security Policy

## Runtime Boundary

PCS is designed for a trusted local field network. The public homepage intentionally exposes operational health and current GNSS position to connected LAN clients. Administrative diagnostics and actions require the local PCS administrator password.

The current field interface uses HTTP. Do not expose ports `80`, `8080`, `9090`, Samba, GPSD, or SSH directly to the public internet, and do not bridge untrusted clients onto the PCS LAN without an additional security boundary.

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
- SIM/account information
- Private IP configuration dumps
- Real modem carrier/account details
- Full router backup files
- Personal identifying information

Example configs should use placeholder values only.

Good example values:

- `example.local`
- `192.0.2.10`
- `10.0.0.1`
- `CHANGE_ME`
- `YOUR_PRIVATE_KEY_HERE`

If sensitive information is accidentally committed, rotate or replace the exposed credential immediately.
