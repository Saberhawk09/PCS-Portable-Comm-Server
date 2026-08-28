package com.saberhawk.pcscompanion.data

import com.saberhawk.pcscompanion.security.ExactCertificateTrustManager
import java.io.EOFException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.ProtocolException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.ArrayDeque
import java.util.Collections
import java.util.IdentityHashMap
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.SerializationStrategy
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody

class PcsApiException(
    val httpStatus: Int?,
    val code: String,
    val retryAfterSeconds: Long? = null,
    message: String,
    cause: Throwable? = null,
) : Exception(message, cause)

class PcsApiClient(trustedCertificate: X509Certificate) {
    private val trustManager = ExactCertificateTrustManager(trustedCertificate)
    private val client: OkHttpClient

    init {
        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(trustManager), SecureRandom())
        }
        client = OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .connectionPool(ConnectionPool(0, 1, TimeUnit.NANOSECONDS))
            .connectTimeout(4, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .callTimeout(25, TimeUnit.SECONDS)
            .followRedirects(false)
            .followSslRedirects(false)
            .retryOnConnectionFailure(false)
            .build()
    }

    suspend fun discovery(endpoint: EndpointCandidate, token: String?): Discovery = execute(
        endpoint = endpoint,
        path = "/api/v1",
        token = token,
        responseSerializer = Discovery.serializer(),
    )

    suspend fun status(endpoint: EndpointCandidate, token: String?): StatsEnvelope = execute(
        endpoint = endpoint,
        path = "/api/v1/status",
        token = token,
        responseSerializer = StatsEnvelope.serializer(),
    )

    suspend fun pair(
        endpoint: EndpointCandidate,
        deviceId: String,
        adminPassword: String,
    ): PairingEnvelope = execute(
        endpoint = endpoint,
        path = "/api/v1/pair",
        method = "POST",
        body = jsonBody(PairingRequest(deviceId = deviceId, adminPassword = adminPassword), PairingRequest.serializer()),
        expectedStatus = setOf(201),
        responseSerializer = PairingEnvelope.serializer(),
    )

    suspend fun actions(endpoint: EndpointCandidate, token: String): ActionCatalog = execute(
        endpoint = endpoint,
        path = "/api/v1/actions",
        token = token,
        responseSerializer = ActionCatalog.serializer(),
    )

    suspend fun challenge(
        endpoint: EndpointCandidate,
        token: String,
        action: ActionMetadata,
    ): ActionChallenge = execute(
        endpoint = endpoint,
        path = action.challengePath ?: throw IllegalArgumentException("Action has no challenge path."),
        method = "POST",
        token = token,
        body = EMPTY_JSON_BODY,
        expectedStatus = setOf(201),
        responseSerializer = ActionChallenge.serializer(),
    )

    suspend fun executeAction(
        endpoint: EndpointCandidate,
        token: String,
        action: ActionMetadata,
        challenge: ActionChallenge?,
    ): ActionResult {
        val body = if (challenge == null) {
            EMPTY_JSON_BODY
        } else {
            jsonBody(
                ConfirmedActionRequest(
                    confirmation = challenge.confirmation,
                    challenge = challenge.challenge,
                ),
                ConfirmedActionRequest.serializer(),
            )
        }
        return execute(
            endpoint = endpoint,
            path = action.executePath,
            method = "POST",
            token = token,
            body = body,
            responseSerializer = ActionResult.serializer(),
        )
    }

    suspend fun changePassword(
        endpoint: EndpointCandidate,
        token: String,
        currentPassword: String,
        newPassword: String,
    ): PasswordChangeResponse = execute(
        endpoint = endpoint,
        path = "/api/v1/admin/password",
        method = "POST",
        token = token,
        body = jsonBody(
            PasswordChangeRequest(currentPassword = currentPassword, newPassword = newPassword),
            PasswordChangeRequest.serializer(),
        ),
        responseSerializer = PasswordChangeResponse.serializer(),
    )

    private fun <T> jsonBody(value: T, serializer: SerializationStrategy<T>): RequestBody =
        PcsJson.format.encodeToString(serializer, value).toRequestBody(JSON_MEDIA_TYPE)

    private suspend fun <T> execute(
        endpoint: EndpointCandidate,
        path: String,
        method: String = "GET",
        token: String? = null,
        body: RequestBody? = null,
        expectedStatus: Set<Int> = setOf(200),
        responseSerializer: DeserializationStrategy<T>,
    ): T = withContext(Dispatchers.IO) {
        val safeEndpoint = endpoint.validated()
        require(path.startsWith("/api/v1") && !path.contains('?') && !path.contains('#')) {
            "API path is outside the fixed v1 namespace."
        }
        val request = Request.Builder()
            .url(safeEndpoint.baseUrl + path)
            .header("Accept", PCS_MEDIA_TYPE.toString())
            .header("Connection", "close")
            .method(method, body)
            .apply {
                if (token != null) header("Authorization", "Bearer $token")
            }
            .build()

        try {
            client.newCall(request).execute().use { response ->
                val responseText = readBounded(response.body)
                if (response.code !in expectedStatus) {
                    val problem = runCatching {
                        PcsJson.format.decodeFromString(Problem.serializer(), responseText)
                    }.getOrNull()
                    throw PcsApiException(
                        httpStatus = response.code,
                        code = problem?.code ?: "http_${response.code}",
                        retryAfterSeconds = response.header("Retry-After")?.toLongOrNull(),
                        message = problem?.title ?: "PCS returned HTTP ${response.code}.",
                    )
                }
                val contentType = response.header("Content-Type")?.substringBefore(';')?.trim()
                if (contentType != PCS_MEDIA_TYPE.toString()) {
                    throw PcsApiException(
                        httpStatus = response.code,
                        code = "unexpected_content_type",
                        message = "PCS returned an unexpected content type.",
                    )
                }
                runCatching {
                    PcsJson.format.decodeFromString(responseSerializer, responseText)
                }.getOrElse { error ->
                    throw PcsApiException(
                        httpStatus = response.code,
                        code = "invalid_response",
                        message = "PCS returned a response outside the v1 contract.",
                        cause = error,
                    )
                }
            }
        } catch (error: PcsApiException) {
            throw error
        } catch (error: Exception) {
            throw classifyConnectionFailure(safeEndpoint, error)
        }
    }

    private fun classifyConnectionFailure(endpoint: EndpointCandidate, error: Exception): PcsApiException {
        val causes = throwableGraph(error)
        val host = runCatching { java.net.URI(endpoint.baseUrl).host }.getOrNull() ?: "configured address"
        val endpointName = endpoint.kind.displayName
        val code: String
        val message: String
        when {
            causes.any { it is SecurityException } -> {
                code = "local_network_denied"
                message = "Android blocked Local network access for $endpointName. Allow the permission in app settings."
            }
            causes.any { it is SSLPeerUnverifiedException } -> {
                code = "hostname_mismatch"
                message = "The PCS certificate does not authorize $host for $endpointName."
            }
            causes.any { it is CertificateException } -> {
                code = "certificate_rejected"
                message = "PCS presented a certificate different from the one trusted by the app at $host."
            }
            causes.any { it is SSLHandshakeException } -> {
                code = "tls_handshake_failed"
                message = "The trusted TLS handshake with PCS failed at $host."
            }
            causes.any { it is NoRouteToHostException } -> {
                code = "no_route"
                message = "Android has no network or VPN route to $host for $endpointName."
            }
            causes.any { it is UnknownHostException } -> {
                code = "unknown_host"
                message = "Android could not resolve the PCS address $host."
            }
            causes.any { it is SocketTimeoutException } -> {
                code = "connection_timeout"
                message = "$endpointName timed out while connecting to PCS at $host."
            }
            causes.any { it is ConnectException } -> {
                code = "connection_refused"
                message = "$endpointName could not open the PCS API connection at $host."
            }
            causes.any { it is ProtocolException || it is EOFException } -> {
                code = "connection_protocol_failed"
                message = "PCS closed the HTTPS connection at $host before the response completed."
            }
            causes.any { it is SSLException } -> {
                code = "tls_connection_failed"
                message = "The TLS connection to PCS failed at $host."
            }
            else -> {
                code = "connection_failed"
                message = "$endpointName could not establish the PCS HTTPS connection at $host."
            }
        }
        return PcsApiException(
            httpStatus = null,
            code = code,
            message = message,
            cause = error,
        )
    }

    private fun throwableGraph(root: Throwable): List<Throwable> {
        val pending = ArrayDeque<Throwable>().apply { add(root) }
        val seen = Collections.newSetFromMap(IdentityHashMap<Throwable, Boolean>())
        val result = mutableListOf<Throwable>()
        while (pending.isNotEmpty() && result.size < MAX_CAUSE_DEPTH) {
            val current = pending.removeFirst()
            if (!seen.add(current)) continue
            result += current
            current.cause?.let(pending::addLast)
            current.suppressed.forEach(pending::addLast)
        }
        return result
    }

    private fun readBounded(body: okhttp3.ResponseBody): String {
        val source = body.source()
        source.request(MAX_RESPONSE_BYTES + 1L)
        if (source.buffer.size > MAX_RESPONSE_BYTES) {
            throw PcsApiException(
                httpStatus = null,
                code = "response_too_large",
                message = "PCS response exceeded the app safety limit.",
            )
        }
        return source.readUtf8()
    }

    private companion object {
        const val MAX_RESPONSE_BYTES = 256 * 1024
        const val MAX_CAUSE_DEPTH = 12
        val PCS_MEDIA_TYPE = "application/vnd.pcs.v1+json".toMediaType()
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
        val EMPTY_JSON_BODY = "{}".toRequestBody(JSON_MEDIA_TYPE)
    }
}
