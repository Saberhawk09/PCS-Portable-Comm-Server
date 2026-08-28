package com.saberhawk.pcscompanion.data

import java.io.IOException
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate

class PcsApiClientTlsTest {
    @Test
    fun acceptsTheImportedCertificateForItsDeclaredHostname() = runBlocking {
        val certificate = serverCertificate("localhost")
        withHttpsServer(certificate) { server ->
            server.enqueue(discoveryResponse())

            val discovery = PcsApiClient(certificate.certificate).discovery(
                endpoint(server),
                token = null,
            )

            assertEquals("v1", discovery.apiVersion)
            assertEquals("public", discovery.access)
            assertEquals("/api/v1", server.takeRequest().url.encodedPath)
        }
    }

    @Test
    fun rejectsAValidButDifferentServerCertificate() = runBlocking {
        val importedCertificate = serverCertificate("localhost")
        val actualServerCertificate = serverCertificate("localhost")
        withHttpsServer(actualServerCertificate) { server ->
            server.enqueue(discoveryResponse())

            val error = assertFailsWith<PcsApiException> {
                PcsApiClient(importedCertificate.certificate).discovery(endpoint(server), null)
            }

            assertEquals("certificate_rejected", error.code)
            assertEquals(null, error.httpStatus)
        }
    }

    @Test
    fun rejectsHostnameMismatchEvenWhenCertificateBytesMatch() = runBlocking {
        val certificate = serverCertificate("not-localhost.invalid")
        withHttpsServer(certificate) { server ->
            server.enqueue(discoveryResponse())

            val error = assertFailsWith<PcsApiException> {
                PcsApiClient(certificate.certificate).discovery(endpoint(server), null)
            }

            assertEquals("hostname_mismatch", error.code)
            assertEquals(null, error.httpStatus)
        }
    }

    @Test
    fun rejectsAResponseLargerThanTheClientSafetyLimit() = runBlocking {
        val certificate = serverCertificate("localhost")
        withHttpsServer(certificate) { server ->
            server.enqueue(
                MockResponse.Builder()
                    .code(200)
                    .addHeader("Content-Type", "application/vnd.pcs.v1+json")
                    .body("x".repeat(256 * 1024 + 1))
                    .build(),
            )

            val error = assertFailsWith<PcsApiException> {
                PcsApiClient(certificate.certificate).discovery(endpoint(server), null)
            }

            assertEquals("response_too_large", error.code)
        }
    }

    private fun endpoint(server: MockWebServer) = EndpointCandidate(
        kind = ConnectionKind.HOME_LAN,
        baseUrl = server.url("/").toString(),
    )

    private fun serverCertificate(subjectAlternativeName: String) = HeldCertificate.Builder()
        .commonName(subjectAlternativeName)
        .addSubjectAlternativeName(subjectAlternativeName)
        .build()

    private suspend fun withHttpsServer(
        certificate: HeldCertificate,
        block: suspend (MockWebServer) -> Unit,
    ) {
        val serverCertificates = HandshakeCertificates.Builder()
            .heldCertificate(certificate)
            .build()
        val server = MockWebServer()
        server.useHttps(serverCertificates.sslSocketFactory())
        server.start()
        try {
            block(server)
        } finally {
            try {
                server.close()
            } catch (_: IOException) {
                // The test assertion is more useful than a shutdown failure.
            }
        }
    }

    private fun discoveryResponse() = MockResponse.Builder()
        .code(200)
        .addHeader("Content-Type", "application/vnd.pcs.v1+json")
        .body(
            """
            {
              "api_version":"v1",
              "schema_version":"1.0",
              "resource":"discovery",
              "access":"public",
              "content_type":"application/vnd.pcs.v1+json",
              "authentication":{
                "public":"Omit Authorization for public status.",
                "authenticated":"Use a bearer token for administrative details."
              },
              "resources":{"status":"/api/v1/status"},
              "pairing":"/api/v1/pair",
              "actions":"/api/v1/actions",
              "password":"/api/v1/admin/password",
              "methods":["GET","POST"],
              "write_actions":true
            }
            """.trimIndent(),
        )
        .build()
}
