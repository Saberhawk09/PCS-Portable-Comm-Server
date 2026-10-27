# PCS web UI modernization

Development branch: `codex/pcs-ui-modernization`, based on current fetched main
`ba80c8dc067cb1c038e425cbc283a0167efc4fb9` (v2.2.2). The development validation
below preceded deployment. The operator subsequently authorized draft PR/CI and
PCS deployment; see [live deployment evidence](live-deployment.md).

The supplied brief and both HTML concepts were inspected. Their simulated data,
password inputs, command execution and fabricated history were excluded.

## Changes and navigation

The public launcher now has Overview, Network, Radio, Power, Storage and
Diagnostics. Public and admin dashboards now share Overview, Network, Radio, Power, Storage
and Diagnostics. Administration adds compact, genuine form controls, privileged
detail, and an Actions catalog.
Desktop uses a left sidebar; tablet/phone navigation scrolls horizontally.
Health, collection time, snapshot age, errors and alerts stay outside the selected
section. Four summary cards lead each overview.

The public page continues to poll only `/api/public-status`. Zero, unavailable,
not configured, offline and stale are distinct. Stale responses retain an obvious
last-sample label. Admin cards retain every original item/metric and use stable
IDs for placement. Unknown cards go into expandable diagnostics.

All 31 original actions, settings, mailbox detail, bandwidth controls, optional
Graywolf/Pi-Star access, four-rail accounting and protective behavior are retained.
See the [complete parity matrix](feature-parity.md).

Changes are restricted to:

- `web/pcs-home/index.html`, `css/pcs.css`, `js/pcs.js`: navigation, presentation,
  existing public-field bindings and stale-label clarity.
- `web/pcs-control-panel/pcs_control_panel.py`: presentation grouping, shared
  styling for admin/login/password/bandwidth/detail pages, inline backup settings,
  actual result console and progressive section navigation. Request handlers,
  authentication, dispatch, validation, API schema and collectors are unchanged.
- Existing control-panel/home tests, new parity tests, isolated preview/browser
  tooling, and this report/screenshots.

No runtime dependencies or static files were added, so the existing strict static
installer allowlist needs no change. nginx routes/CSP, Stats API, privileged
helpers, systemd units and business logic are unchanged.

## Validation

The complete Python suite ran as an ordinary user in a separate copy-on-write
Debian 13 QEMU guest, using Python 3.13.5 and actual nginx. It discovered **947
tests: 943 passed, four expected native-Windows skips**. This includes the real
nginx route/authentication/CSRF tests, public/Stats API privacy, snapshots,
installer transactions, power, radio and network regressions.

Final presentation changes were checked again using the focused commands below.
The control-panel **34/34**, actual-nginx/frontend **28/28**, and new parity
**5/5** suites passed again; standalone `nginx -t` also passed.
The public JavaScript suite passed **18/18**. The five new parity tests verify
every action's original ID/endpoint/token/confirmation/description, every card's
fields and metrics including future IDs, escaped content, real empty action
output with failed exit status, ordinary settings forms and public bindings.

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_pcs_control_panel.py' -v
python3 -m unittest discover -s tests -p 'test_pcs_frontend.py' -v
python3 -m unittest discover -s tests -p 'test_ui_modernization.py' -v
node --test tests/test_pcs_home.js
nginx -t
```

Chrome/Playwright checked every public/admin section at **1440, 768 and 360 px**:
no document horizontal overflow or JavaScript errors. It also checked protected
fixture login, returned POST section/output/focus, a cancelled reboot prompt
with zero submitted POSTs, keyboard skip navigation, and public/status/power/
Starlink/password/bandwidth secondary routes at phone width. With JavaScript
disabled, all 31 forms remain in the document, backup settings are visible,
an action submits through the genuine handler, and public status links work.
See [machine-readable browser results](screenshots/browser-results.json).

For a local fixture preview:

```sh
python tests/preview_web_ui.py
# Visit http://127.0.0.1:8766/ ; fixture login: ui-preview-password
# In another terminal, with development-only Playwright and Chrome available:
node tests/check_web_ui.cjs
```

`PCS_PLAYWRIGHT_MODULE` may identify an already-installed Playwright package;
`PCS_CHROME_PATH` may identify a Chrome-compatible executable. These are test
tools, never appliance dependencies. The preview binds only to loopback, labels
data as fixture data, mocks all action/helper paths, and blocks power changes.
Never deploy or expose the fixture server.

Windows full discovery also ran: 931 discovered, 785 passed, 143 platform skips
and three errors in existing POSIX-shell fixtures. Those three passed in Debian.
The Windows-only nginx skips were covered by the Linux run. Subsequent GitHub CI
passed all five jobs before the authorized live deployment. No physical mobile,
RF, WAN switching, electrical protection or live telemetry acceptance is claimed.

## Screenshots and reference differences

All screenshots use clearly labelled local fixtures, including a measured zero
and an unconfigured device. They are layout evidence, not live PCS readings.

| | Desktop | Tablet | Phone |
| --- | --- | --- | --- |
| Public | [1440 px](screenshots/public-1440.png) | [768 px](screenshots/public-768.png) | [360 px](screenshots/public-360.png) |
| Admin | [1440 px](screenshots/admin-1440.png) | [768 px](screenshots/admin-768.png) | [360 px](screenshots/admin-360.png) |

The reference's history/count badges and simulated command console were omitted.
The existing service launcher and detailed permalinks were retained. Backup
settings are inline and work without scripts. Password changes remain on the
dedicated protected route. Staged Starlink power-off remains explicitly staged.
Raw-versus-derived 12V readings and source-dependent Starlink accounting retain
their original meanings. All original confirmations are preserved.

## Deployment and rollback

See [the scoped deployment and exact rollback procedure](deployment.md).
Execution requires current authorization. Only the frontend/backend
presentation files need activation; the existing snapshots and all appliance
configuration/services can stay in place.

## Follow-up: shared overview and compact scaling

The revised homepage follows the supplied homepage.JPG reference: four primary
readings, then Connectivity, Radio services, Power distribution, and Storage &
time, followed by compact service shortcuts. Public and authenticated overviews
use the same server-rendered structure and stylesheet. Admin adds small real POST
buttons to the corresponding panels; all 31 allowlisted actions remain reachable.
Power settings live in Power; system/advanced tools live in Diagnostics. The old
`#system` and `#power-settings` anchors continue resolving to their new sections.

Content is bounded to 1480 px and cards use content-driven heights rather than
stretching across an ultrawide viewport. Desktop shortcuts are compact three-column
rows. Browser checks include 2560 px as well as desktop/tablet/phone, with explicit
bounds on content width, KPI width and shortcut height.

The authenticated overview reads the existing cached public projection for the
same core readings (including AP client count) and retains the separate privileged
snapshot for card details. This adds no hardware polling or collection and changes
no API schema, authentication, action validation or CSRF rules. No admin detail is
copied into the public page. Actual returned action output appears only when there
is a result, avoiding an empty console occupying the top of the dashboard.

## Clean dashboard and separate detailed diagnostics

The screenshot-marked decorative text has been removed. Subsystem panels use
an equal-width two-column grid and stretch to the same height within each row,
so the center gutter and horizontal edges align. Phone layouts remain one column.

Authenticated users keep the same clean public cards in every dashboard section,
with existing compact controls and settings. All original raw status cards,
authenticated mailbox messages, client addresses/MACs, Graywolf configuration and
future/unknown cards are now on **Detailed diagnostics** at `/admin/details`.
This page uses the existing session gate and escaped card renderer. It is a real
separate request, not hidden detail markup embedded in the dashboard. The public
API remains unchanged, and no extra collectors or hardware polls are introduced.

The installer smoke check now uses the stable homepage title element instead
of the removed decorative copy. Browser checks assert equal panel widths, row
positions and bottom edges on both public and admin dashboards.

## Unified power, Starlink and location navigation

Power and Starlink now share the main public/admin tab group. Power combines the
overview, battery estimates/protection and all four INA226 monitors, including
voltage, current, charge and energy since boot, plus both 12V accounting views.
Starlink includes cached dish telemetry and measured branch readings; its protected
operator controls stay on the authenticated Starlink tab. The old `/power/` and
`/starlink/` bookmarks open the same full workspace with the matching tab selected.

Location settings has a direct navigation entry on both dashboards and a compact
authenticated header button. The public link opens the existing login gate; it
does not publish private coordinates. Existing Diagnostics access also remains.

Browser validation covers navigation, Location settings access, legacy bookmarks,
all 31 actions, confirmation cancellation, no-JavaScript access and responsive
layouts at 2560, 1440, 768 and 360 px. Updated fixture screenshots include
`screenshots/admin-power-1440.png` and `screenshots/admin-starlink-360.png`.
