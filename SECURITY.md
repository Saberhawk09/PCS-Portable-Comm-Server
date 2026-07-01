# Security Policy

PCS may eventually involve cellular modem configuration, network settings, VPN configuration, IP addresses, and device-specific setup files.

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