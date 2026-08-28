package com.saberhawk.pcscompanion.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class EndpointCandidateTest {
    @Test
    fun defaultsMatchTheReleasedBootstrapOrder() {
        val validated = EndpointCandidate.defaults.map(EndpointCandidate::validated)

        assertEquals(ConnectionKind.HOME_LAN, validated[0].kind)
        assertEquals("https://192.168.50.236:9443", validated[0].baseUrl)
        assertEquals(ConnectionKind.PCS_LAN, validated[1].kind)
        assertEquals("https://10.42.0.1:9443", validated[1].baseUrl)
        assertEquals(ConnectionKind.WIREGUARD, validated[2].kind)
        assertEquals("https://10.6.0.7:9443", validated[2].baseUrl)
    }

    @Test
    fun normalizesTrailingSlashWithoutWeakeningHttps() {
        val endpoint = EndpointCandidate(ConnectionKind.HOME_LAN, " https://pcs.local:9443/ ").validated()
        assertEquals("https://pcs.local:9443", endpoint.baseUrl)
    }

    @Test
    fun rejectsCleartextCredentialsPathsQueriesAndFragments() {
        listOf(
            "http://10.42.0.1:9443",
            "https://user@example.test:9443",
            "https://example.test:9443/api/v1",
            "https://example.test:9443?unsafe=yes",
            "https://example.test:9443#fragment",
        ).forEach { value ->
            assertFailsWith<IllegalArgumentException>(value) {
                EndpointCandidate(ConnectionKind.PCS_LAN, value).validated()
            }
        }
    }
}
