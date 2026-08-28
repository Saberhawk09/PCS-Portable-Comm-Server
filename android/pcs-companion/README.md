# PCS Companion for Android

PCS Companion is the native Android client for the PCS HTTPS API released in
PCS v1.4. It is intentionally a management client, not a WireGuard client: the
existing Android WireGuard app remains responsible for the tunnel.

The current release version is `0.1.5`. Version `0.1.0` predates the live
discovery contract's required `authentication` object. Version `0.1.1` targets
Android 17 but does not request its new Local network runtime permission, so
Android blocks private PCS LAN and WireGuard addresses before connection.
Version `0.1.2` adds that permission. Version `0.1.3` reports a safe, specific
permission, routing, timeout, hostname, certificate, or TLS failure category
for each configured endpoint instead of hiding every cause behind the same
connection message.

Version `0.1.4` uses a fresh, explicitly closed TLS connection for each API
request. PCS serves HTTP/1.0 and closes every response; disabling Android HTTP
connection pooling prevents discovery's closed socket from being reused for
the following status request.

Version `0.1.5` is the first production-signed APK. It is bundled with the PCS
v1.4.1 GitHub Release and retains the live-accepted v0.1.4 behavior.

The first development slice provides:

- strict HTTPS-only endpoint selection in home-LAN, PCS-LAN, then WireGuard order;
- Android 17 Local network runtime-permission gating for every private PCS request;
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
debug APK. Without signing properties, `assembleRelease` verifies shrinking and
creates an unsigned APK. For a signed release, keep a durable private keystore
outside Git and provide an ignored Java properties file:

```properties
storeFile=C:/secure/path/pcs-companion-release.jks
storePassword=REDACTED
keyAlias=pcs-companion-release
keyPassword=REDACTED
```

```powershell
.\gradlew.bat clean testDebugUnitTest lintDebug assembleRelease `
  -PpcsSigningPropertiesFile=C:\secure\pcs-companion-signing.properties
```

The signing property file is required to contain all four values. A first-time
maintainer can create both ignored files with
`scripts/initialize-android-signing.ps1`; the script refuses to replace an
existing signing identity. The keystore and its passwords are release
credentials: back them up securely and never commit or attach them to a
release. Future APK updates must use the same key.

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
issues, and both debug and minified release builds complete.

Operator acceptance on 2026-08-28 confirmed the debug APK on a real phone over
the trusted home LAN, direct PCS LAN, and cellular/WireGuard routes. Pairing,
authenticated status, backup synchronization, and challenge-protected shutdown
also completed successfully.

Before calling the Android client production-ready, exercise both modern and
pre-Android-11 biometric/device-credential paths as applicable, confirm
Keystore-backed token recovery after process death and reboot, revoke the test
device on PCS, verify the revoked token returns the app to public/unpaired
mode. The v0.1.5 APK is production-signed; biometric compatibility and token
lifecycle checks remain post-release hardening work rather than signing gates.
