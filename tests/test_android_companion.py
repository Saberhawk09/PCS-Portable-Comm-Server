import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android" / "pcs-companion"


class AndroidCompanionTests(unittest.TestCase):
    def test_project_is_native_compose_and_matches_api_version(self):
        build = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        models = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "data" / "PcsModels.kt").read_text(encoding="utf-8")
        contract = json.loads((ROOT / "docs" / "pcs-stats-api-v1.openapi.json").read_text(encoding="utf-8"))

        self.assertIn('id("org.jetbrains.kotlin.plugin.compose")', build)
        self.assertIn('implementation("androidx.compose.material3:material3")', build)
        self.assertEqual(contract["info"]["version"], "1.1.0")
        self.assertIn('@SerialName("api_version")', models)
        self.assertIn('val apiVersion: String', models)
        self.assertIn('val authentication: JsonObject', models)

    def test_bootstrap_endpoint_order_is_exact_and_https_only(self):
        source = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "data" / "EndpointCandidate.kt").read_text(encoding="utf-8")
        expected = [
            "https://192.168.50.236:9443",
            "https://10.42.0.1:9443",
            "https://10.6.0.7:9443",
        ]
        positions = [source.index(endpoint) for endpoint in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('parsed.scheme.equals("https"', source)
        self.assertNotIn('http://', source)

    def test_app_never_enables_cleartext_or_logs_credentials(self):
        manifest = (ANDROID / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
        all_kotlin = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ANDROID / "app" / "src" / "main" / "java").rglob("*.kt")
        )
        build = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")

        self.assertIn('android:usesCleartextTraffic="false"', manifest)
        self.assertIn('android:allowBackup="false"', manifest)
        self.assertNotIn("logging-interceptor", build)
        self.assertNotIn("HttpLoggingInterceptor", all_kotlin)
        self.assertNotIn("hostnameVerifier", all_kotlin)

    def test_certificate_and_token_paths_preserve_bootstrap_security(self):
        security_dir = ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "security"
        certificate = (security_dir / "CertificateTrust.kt").read_text(encoding="utf-8")
        token = (security_dir / "SecureTokenStore.kt").read_text(encoding="utf-8")

        self.assertIn("MessageDigest.isEqual", certificate)
        self.assertIn("leaf.checkValidity()", certificate)
        self.assertIn('const val ANDROID_KEYSTORE = "AndroidKeyStore"', token)
        self.assertIn('const val TRANSFORMATION = "AES/GCM/NoPadding"', token)
        self.assertIn("setIsStrongBoxBacked(true)", token)

        repository = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "data" / "PcsRepository.kt").read_text(encoding="utf-8")
        self.assertIn("if (identityChanged) tokenStore.clear()", repository)

    def test_action_catalog_is_server_driven_and_challenges_are_bound(self):
        repository = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "data" / "PcsRepository.kt").read_text(encoding="utf-8")
        api = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "data" / "PcsApiClient.kt").read_text(encoding="utf-8")
        ui = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "ui" / "PcsCompanionApp.kt").read_text(encoding="utf-8")

        self.assertIn("catalog.actions", repository)
        self.assertIn("challenge.confirmation", api)
        self.assertIn("validateActionMetadata", repository)
        self.assertIn('action.executePath == "/api/v1/actions/${action.name}"', repository)
        self.assertIn('action.challengePath == expectedChallengePath', repository)
        self.assertIn("action.challengeRequired", ui)
        self.assertIn("authenticateAction", ui)
        self.assertNotIn("/bin/sh", api)

    def test_every_administrative_mutation_requires_device_authentication(self):
        ui = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "ui" / "PcsCompanionApp.kt").read_text(encoding="utf-8")
        activity = (ANDROID / "app" / "src" / "main" / "java" / "com" / "saberhawk" / "pcscompanion" / "MainActivity.kt").read_text(encoding="utf-8")

        self.assertIn('authenticateAction(\n                        action.label,', ui)
        self.assertIn('authenticateAction(\n                        "Change PCS administrator password",', ui)
        self.assertIn("BIOMETRIC_STRONG", activity)
        self.assertIn("createConfirmDeviceCredentialIntent", activity)


if __name__ == "__main__":
    unittest.main()
