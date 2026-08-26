# Config Examples

This legacy directory is retained as a pointer. Maintained, versioned examples
live in [`config/`](../config/) so they stay beside the installer inputs and
templates that consume them:

- [`pcs-install.example.conf`](../config/pcs-install.example.conf) — non-secret PCS installer selections
- [`local-client-names.example.tsv`](../config/local-client-names.example.tsv) — friendly LAN client names
- [`direwolf.example.conf`](../config/direwolf.example.conf) — hardware-safe staged Dire Wolf template
- [`pcs-wireguard-management.example.conf`](../config/pcs-wireguard-management.example.conf) — fake, local-only WireGuard management inputs

All committed examples must use fake or non-secret values.

Do not commit real passwords, private keys, SIM information, carrier account details, or private network configuration.
