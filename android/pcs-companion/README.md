# PCS Companion for Android

PCS Companion is the native Android client for the PCS HTTPS API released in
PCS v1.4. It is intentionally a management client, not a WireGuard client: the
existing Android WireGuard app remains responsible for the tunnel.

The current local source version is `0.1.1`. An older `0.1.0` APK can show
`invalid_response` immediately after connecting because it predates the live
discovery contract's required `authentication` object; update to the rebuilt
`0.1.1` APK when available.

The first development slice provides:

- strict HTTPS-only endpoint selection in home-LAN, PCS-LAN, then WireGuard order;
- explicit import and confirmation of the PCS X.509 certificate;
- exact-certificate trust plus normal TLS hostname verification;
- public and authenticated status views with visibly timestamped offline cache;
- administrator-approved pairing with a per-device token encrypted by Android Keystore;
- the server-provided fixed action catalog and one-time challenge execution;
- biometric or device-credential confirmation before every state-changing action;
- administrator-password rotation without persisting either password; and
- a destructive local “forget PCS” flow that never claims to revoke the server token.

The app never accepts cleartext HTTP, silently accepts a changed certificate,
scrapes the web panel, opens SSH, stores WireGuard keys, or sends arbitrary
commands.

## Local build

The checked-in Gradle wrapper is the authoritative build entry point. The
project currently targets Android API 37, has a minimum of Android 8.0 (API
26), and requires JDK 17 or newer.

```powershell
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-21'
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
Set-Location android\pcs-companion
.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease lintDebug
```

Build output is local and ignored by Git. `assembleDebug` creates an installable
debug APK; `assembleRelease` verifies shrinking but remains unsigned. Neither
command installs or publishes an APK.

## Certificate enrollment

Copy only the public PCS API certificate to the phone through a trusted local
path, then choose **Import certificate** in the app. Compare the displayed
SHA-256 fingerprint and SAN identities with an independently obtained value
before selecting **Trust this certificate**. A certificate replacement always
requires the same explicit re-trust flow and deletes any token associated with
the old certificate identity.

The certificate is public; the PCS private key must never leave
`/etc/pcs-stats-api/tls/server.key`.

## Remaining device gates

The local JVM suite verifies endpoint validation, strict response parsing,
certificate equality, hostname validation, rejection of a different
certificate, and the response-size ceiling. Android lint currently reports no
issues, and both debug and minified unsigned release builds complete.

Before calling the Android client field-ready, validate the debug APK on a real
phone across all three network paths, exercise both modern and pre-Android-11
biometric/device-credential paths as applicable, confirm Keystore-backed token
recovery after process death and reboot, revoke the test device on PCS, and
verify the revoked token returns the app to public/unpaired mode.
