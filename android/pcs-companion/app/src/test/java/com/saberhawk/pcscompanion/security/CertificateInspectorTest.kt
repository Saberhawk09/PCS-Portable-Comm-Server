package com.saberhawk.pcscompanion.security

import kotlin.test.Test
import kotlin.test.assertEquals

class CertificateInspectorTest {
    @Test
    fun formatsSha256FingerprintForOutOfBandComparison() {
        assertEquals(
            "BA:78:16:BF:8F:01:CF:EA:41:41:40:DE:5D:AE:22:23:" +
                "B0:03:61:A3:96:17:7A:9C:B4:10:FF:61:F2:00:15:AD",
            CertificateInspector.sha256Fingerprint("abc".toByteArray()),
        )
    }
}
