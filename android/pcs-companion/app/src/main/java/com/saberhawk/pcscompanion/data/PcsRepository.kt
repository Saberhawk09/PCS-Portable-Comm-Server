package com.saberhawk.pcscompanion.data

import android.content.Context
import com.saberhawk.pcscompanion.security.CertificateInfo
import com.saberhawk.pcscompanion.security.CertificateInspector
import com.saberhawk.pcscompanion.security.SecureTokenStore

data class RefreshResult(
    val status: StatsEnvelope,
    val endpoint: EndpointCandidate,
    val fromCache: Boolean,
    val tokenRevoked: Boolean,
)

data class PairingResult(
    val endpoint: EndpointCandidate,
    val deviceId: String,
)

data class TrustedCertificateResult(
    val info: CertificateInfo,
    val identityChanged: Boolean,
)

class PcsRepository(context: Context) {
    private val preferences = PcsPreferences(context)
    private val tokenStore = SecureTokenStore(context)

    fun endpoints(): List<EndpointCandidate> = preferences.endpoints()

    fun saveEndpoints(endpoints: List<EndpointCandidate>) = preferences.saveEndpoints(endpoints)

    fun isPaired(): Boolean = tokenStore.hasToken() && tokenStore.load() != null

    fun certificateInfo(): CertificateInfo? = preferences.certificateBytes()?.let { bytes ->
        runCatching { CertificateInspector.inspect(bytes) }.getOrNull()
    }

    fun inspectCertificate(bytes: ByteArray): CertificateInfo = CertificateInspector.inspect(bytes)

    fun trustCertificate(bytes: ByteArray): TrustedCertificateResult {
        val certificate = CertificateInspector.parse(bytes)
        val info = CertificateInspector.inspect(certificate)
        val existingCertificate = preferences.certificateBytes()
        val identityChanged = existingCertificate != null &&
            !java.security.MessageDigest.isEqual(existingCertificate, certificate.encoded)
        if (identityChanged) tokenStore.clear()
        preferences.saveCertificate(certificate.encoded)
        return TrustedCertificateResult(info, identityChanged)
    }

    suspend fun refresh(): RefreshResult {
        val certificate = trustedCertificate()
        val client = PcsApiClient(certificate)
        val token = tokenStore.load()
        return try {
            connect(client, token, tokenRevoked = false)
        } catch (error: PcsApiException) {
            if (token != null && error.httpStatus == 401) {
                tokenStore.clear()
                connect(client, token = null, tokenRevoked = true)
            } else {
                cachedResult() ?: throw error
            }
        } catch (error: Exception) {
            cachedResult() ?: throw error
        }
    }

    suspend fun pair(deviceId: String, adminPassword: String): PairingResult {
        require(DEVICE_ID.matches(deviceId)) {
            "Device ID must be 1–64 letters, digits, dots, underscores, or hyphens."
        }
        require(adminPassword.length in 1..1024) { "Administrator password length is invalid." }
        val client = PcsApiClient(trustedCertificate())
        val endpoint = findEndpoint(client, token = null)
        val response = client.pair(endpoint, deviceId, adminPassword)
        require(response.apiVersion == "v1" && response.schemaVersion == "1.0") {
            "Pairing response has an unsupported version."
        }
        require(response.resource == "pairing" && response.tokenType == "Bearer") {
            "Pairing response is outside the PCS contract."
        }
        require(response.deviceId == deviceId && response.scopes == REQUIRED_SCOPES) {
            "Pairing response did not grant the required fixed scopes."
        }
        try {
            tokenStore.save(response.token)
        } catch (error: Exception) {
            throw IllegalStateException(
                "Pairing succeeded, but secure token storage failed. Revoke device ID $deviceId on PCS before retrying.",
                error,
            )
        }
        return PairingResult(endpoint, deviceId)
    }

    suspend fun actions(endpoint: EndpointCandidate): List<ActionMetadata> {
        val token = requireToken()
        val catalog = PcsApiClient(trustedCertificate()).actions(endpoint, token)
        require(
            catalog.apiVersion == "v1" && catalog.schemaVersion == "1.0" &&
                catalog.resource == "actions" && catalog.requiredScope == "admin:actions",
        ) { "Action catalog is outside the PCS v1 contract." }
        require(catalog.actions.size <= MAX_ACTIONS) {
            "Action catalog exceeds the app safety limit."
        }
        catalog.actions.forEach(::validateActionMetadata)
        return catalog.actions
    }

    suspend fun runAction(endpoint: EndpointCandidate, action: ActionMetadata): ActionResult {
        validateActionMetadata(action)
        val token = requireToken()
        val client = PcsApiClient(trustedCertificate())
        val challenge = if (action.challengeRequired) {
            client.challenge(endpoint, token, action).also {
                require(
                    it.apiVersion == "v1" && it.resource == "action-challenge" &&
                        it.action == action.name && it.confirmation == action.name && it.expiresIn == 60,
                ) { "Action challenge is outside the PCS v1 contract." }
                require(it.challenge.length in MIN_CHALLENGE_LENGTH..MAX_CHALLENGE_LENGTH) {
                    "Action challenge is outside the PCS v1 contract."
                }
            }
        } else {
            null
        }
        return client.executeAction(endpoint, token, action, challenge).also {
            require(it.apiVersion == "v1" && it.resource == "action-result" && it.action == action.name) {
                "Action result is outside the PCS v1 contract."
            }
        }
    }

    suspend fun changePassword(
        endpoint: EndpointCandidate,
        currentPassword: String,
        newPassword: String,
    ) {
        require(currentPassword.length in 1..1024) { "Current password length is invalid." }
        require(newPassword.length in 12..1024) { "New password must contain at least 12 characters." }
        val result = PcsApiClient(trustedCertificate()).changePassword(
            endpoint = endpoint,
            token = requireToken(),
            currentPassword = currentPassword,
            newPassword = newPassword,
        )
        require(result.apiVersion == "v1" && result.resource == "password-change" && result.changed) {
            "Password response is outside the PCS v1 contract."
        }
    }

    fun forget() {
        tokenStore.clear(deleteKey = true)
        preferences.clearAll()
    }

    fun clearToken() = tokenStore.clear()

    private suspend fun connect(
        client: PcsApiClient,
        token: String?,
        tokenRevoked: Boolean,
    ): RefreshResult {
        val endpoint = findEndpoint(client, token)
        val status = client.status(endpoint, token)
        require(
            status.apiVersion == "v1" && status.schemaVersion == "1.0" && status.resource == "status",
        ) { "Status response is outside the PCS v1 contract." }
        preferences.saveStatus(status, endpoint.kind)
        return RefreshResult(status, endpoint, fromCache = false, tokenRevoked = tokenRevoked)
    }

    private suspend fun findEndpoint(client: PcsApiClient, token: String?): EndpointCandidate {
        val failures = mutableListOf<String>()
        for (candidate in endpoints()) {
            try {
                val endpoint = candidate.validated()
                val discovery = client.discovery(endpoint, token)
                require(
                    discovery.apiVersion == "v1" && discovery.schemaVersion == "1.0" &&
                        discovery.resource == "discovery" &&
                        discovery.contentType == "application/vnd.pcs.v1+json" &&
                        discovery.pairing == "/api/v1/pair" &&
                        discovery.actions == "/api/v1/actions" &&
                        discovery.password == "/api/v1/admin/password" &&
                        discovery.methods == listOf("GET", "HEAD"),
                ) { "Discovery response is outside the PCS v1 contract." }
                return endpoint
            } catch (error: PcsApiException) {
                if (error.httpStatus == 401) throw error
                failures += "${candidate.kind.displayName}: ${error.code}"
            } catch (error: Exception) {
                failures += "${candidate.kind.displayName}: endpoint_validation_failed"
            }
        }
        if (failures.isEmpty()) throw IllegalStateException("No PCS endpoint is configured.")
        throw PcsApiException(
            httpStatus = null,
            code = "all_endpoints_failed",
            message = "No PCS endpoint connected (${failures.joinToString("; ")}).",
        )
    }

    private fun cachedResult(): RefreshResult? = preferences.cachedStatus()?.let { (status, kind) ->
        val endpoint = endpoints().first { it.kind == kind }.validated()
        RefreshResult(status, endpoint, fromCache = true, tokenRevoked = false)
    }

    private fun trustedCertificate() = preferences.certificateBytes()?.let(CertificateInspector::parse)
        ?: throw IllegalStateException("Import and trust the PCS certificate first.")

    private fun requireToken(): String = tokenStore.load()
        ?: throw PcsApiException(401, "pairing_required", message = "Pair this device with PCS first.")

    private fun validateActionMetadata(action: ActionMetadata) {
        require(ACTION_NAME.matches(action.name)) { "Action name is outside the PCS v1 contract." }
        require(action.label.length in 1..MAX_ACTION_TEXT_LENGTH) {
            "Action label is outside the PCS v1 contract."
        }
        require(action.description.length in 1..MAX_ACTION_TEXT_LENGTH) {
            "Action description is outside the PCS v1 contract."
        }
        require(action.group.length in 1..MAX_ACTION_TEXT_LENGTH) {
            "Action group is outside the PCS v1 contract."
        }
        require(action.executePath == "/api/v1/actions/${action.name}") {
            "Action execute path is outside the PCS v1 contract."
        }
        val expectedChallengePath = "/api/v1/actions/${action.name}/challenge"
        if (action.challengeRequired) {
            require(action.challengePath == expectedChallengePath) {
                "Action challenge path is outside the PCS v1 contract."
            }
        } else {
            require(action.challengePath == null) {
                "Action without a challenge must not provide a challenge path."
            }
        }
    }

    private companion object {
        val DEVICE_ID = Regex("^[A-Za-z0-9._-]{1,64}$")
        val ACTION_NAME = Regex("^[a-z0-9]+(?:-[a-z0-9]+)*$")
        val REQUIRED_SCOPES = listOf("stats:read", "admin:actions", "admin:password")
        const val MAX_ACTIONS = 64
        const val MAX_ACTION_TEXT_LENGTH = 256
        const val MIN_CHALLENGE_LENGTH = 32
        const val MAX_CHALLENGE_LENGTH = 128
    }
}
