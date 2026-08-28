package com.saberhawk.pcscompanion.data

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull

class PcsModelsTest {
    @Test
    fun parsesEveryRequiredDiscoveryFieldFromTheOpenApiContract() {
        val discovery = PcsJson.format.decodeFromString<Discovery>(
            """
            {
              "api_version":"v1",
              "schema_version":"1.0",
              "resource":"discovery",
              "access":"public",
              "content_type":"application/vnd.pcs.v1+json",
              "authentication":{"public":"Omit Authorization.","authenticated":"Use a bearer token."},
              "resources":{"status":"/api/v1/status"},
              "pairing":"/api/v1/pair",
              "actions":"/api/v1/actions",
              "password":"/api/v1/admin/password",
              "methods":["GET","HEAD"],
              "write_actions":false
            }
            """.trimIndent(),
        )

        assertEquals("Omit Authorization.", discovery.authentication["public"]?.toString()?.trim('"'))
        assertEquals(listOf("GET", "HEAD"), discovery.methods)
    }

    @Test
    fun parsesTheStrictPublicStatusEnvelope() {
        val status = PcsJson.format.decodeFromString<StatsEnvelope>(
            """
            {
              "api_version":"v1",
              "schema_version":"1.0",
              "resource":"status",
              "generated_at":"2026-08-27T20:30:48Z",
              "health":{"severity":"warn","offline":false},
              "access":"public",
              "data":{"system":{"uptime":"5h 25m"}},
              "details":null
            }
            """.trimIndent(),
        )

        assertEquals("v1", status.apiVersion)
        assertEquals("warn", status.health.severity)
        assertFalse(status.health.offline)
        assertEquals("5h 25m", status.data["system"]?.let { displayTestValue(it.toString()) })
        assertNull(status.details)
    }

    @Test
    fun parsesServerProvidedActionMetadataWithoutHardcodingTheCatalog() {
        val catalog = PcsJson.format.decodeFromString<ActionCatalog>(
            """
            {
              "api_version":"v1",
              "schema_version":"1.0",
              "resource":"actions",
              "access":"authenticated",
              "required_scope":"admin:actions",
              "actions":[{
                "name":"reboot-system",
                "label":"Reboot PCS",
                "description":"Reboot the PCS host.",
                "group":"system",
                "dangerous":true,
                "challenge_required":true,
                "execute_path":"/api/v1/actions/reboot-system",
                "challenge_path":"/api/v1/actions/reboot-system/challenge"
              }]
            }
            """.trimIndent(),
        )

        assertEquals("admin:actions", catalog.requiredScope)
        assertEquals("reboot-system", catalog.actions.single().name)
        assertEquals(true, catalog.actions.single().challengeRequired)
    }

    private fun displayTestValue(raw: String): String = Regex("\"uptime\":\"([^\"]+)\"")
        .find(raw)?.groupValues?.get(1).orEmpty()
}
