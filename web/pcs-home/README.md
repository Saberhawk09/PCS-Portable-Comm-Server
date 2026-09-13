# PCS static portal

Plain HTML, CSS and JavaScript. No build step, framework, external font, CDN,
analytics or Internet requirement. Node.js is used only for development tests.

The quick readings include current input watts/volts and GNSS coordinates/grid.
AP clients use `network.ap_client_count`, sourced from the LCD's shared
`pcs_network_clients` reader (`ip neigh show dev eth0`, excluding .1/.2/.3,
FAILED and INCOMPLETE entries). This is a neighbor-based count, not a router
association-table query. Unknown counts remain unavailable; the older total
`connected_client_count` is preserved in the public API for compatibility.
The public `/status/` page uses this stylesheet; admin styling is unchanged.

- `index.html`: appliance header, four quick readings and eight service tiles.
- `css/pcs.css`, `js/pcs.js`: shared responsive styles and public status polling.
- `files/`, `docs/`: Samba instructions and the offline field guide.
- `radio/`, `pistar/`: optional radio-service information and configured links.

The normal PCS installer copies only named public files into a versioned
`/var/www/pcs-releases/site-*` directory and atomically switches `/var/www/pcs`.
CSS/JS are installed under `assets/`. Repository files, this README, credentials
and private configuration are not published. See the
[deployment runbook](../../docs/pcs-web-frontend.md).

The existing `/api/public-status` is the sole dynamic data source. Text uses
`textContent`; Pi-Star links allow only HTTP(S) without embedded credentials.
Existing `ok`/`warn`/`bad` and offline flags drive health. Missing readings are
unavailable, never assumed zero. Optional services remain optional.

Requests have a 15-second deadline; the next poll follows eight seconds after
settlement. Requests do not overlap. Failures label retained readings as last
known while keeping navigation functional. Without JavaScript, static pages and
links still work; `/status/` serves the existing Python public status view.

From the repository root:

```bash
node --test tests/test_pcs_home.js
python3 -m unittest discover -s tests -p test_pcs_frontend.py -v
```

Set `PCS_TEST_NGINX` when nginx is not discoverable. Tests use temporary loopback
ports and fixture backend actions. Linux is needed for the transaction and
systemd rehearsal tests. No Node runtime is installed on PCS by this update.
