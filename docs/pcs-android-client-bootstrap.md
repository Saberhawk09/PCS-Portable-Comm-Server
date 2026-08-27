# PCS Android Client Bootstrap Contract

This is the implementation boundary for the first Android companion. The PCS
backend is versioned independently; the app must not scrape HTML, open SSH, or
manage WireGuard keys.

## Reachability order

Try a short HTTPS connection to these operator-configured candidates in order:

1. home-LAN PCS address, currently `https://192.168.50.236:9443`;
2. PCS local LAN, `https://10.42.0.1:9443`;
3. PCS WireGuard address, `https://10.6.0.7:9443`.

Use the first endpoint whose certificate is trusted and whose `GET /api/v1`
response has `api_version: v1`. Do not treat a TLS failure as permission to
downgrade to HTTP or disable hostname/certificate checks. The first app build
should let the operator import or confirm the deployment certificate out of
band and pin its public key or certificate. Certificate replacement needs an
explicit re-trust flow; it must never silently accept a changed identity.

## Enrollment

The app prompts for the existing PCS administrator password and a stable,
user-visible device ID, then sends exactly one `POST /api/v1/pair` request with
`Content-Type: application/json`:

```json
{"admin_password":"ADMIN_PASSWORD","device_id":"andre-phone"}
```

Accept only a `201` response with `resource: pairing`, `token_type: Bearer`,
and scopes `stats:read`, `admin:actions`, and `admin:password`. Store the returned token in Android Keystore,
clear the password field and in-memory request data, and never log either
value. A `401` means the password was rejected, `409` means the device ID must
be revoked on PCS before reuse, `429` must honor `Retry-After`, and `503` is a
temporary server-side enrollment failure.

## Normal operation

Without a token, `GET` resources return the public allowlist. With a token,
send `Authorization: Bearer TOKEN`; a `401` means the token is invalid or
revoked and the app must return to an unpaired state. Cache only bounded status
data and always display its UTC `generated_at` time when offline.

The v1 read resources, action catalog, challenge flow, and schemas are authoritative in
[the OpenAPI 3.1 contract](pcs-stats-api-v1.openapi.json). No v1 endpoint can
run arbitrary commands. State-changing actions require the one-time challenge
flow described by `GET /api/v1/actions` and the action catalog; the app should
show a confirmation/biometric prompt before requesting a challenge for reboot,
shutdown, storage, network, cellular, or service changes.

Changing the PCS administrator password uses the separate
`POST /api/v1/admin/password` route and requires the current password plus the
`admin:password` scope. Never save either password in app storage or logs.

## Revocation and recovery

An operator revokes a device on PCS with:

```bash
sudo /usr/local/sbin/pcs-stats-api-setup --revoke-token DEVICE_ID
```

Deleting the credential from Android does not revoke the server-side digest.
The app should offer “forget this PCS” and clearly remind the operator to
revoke the device ID if the phone was lost or the token may have escaped.
