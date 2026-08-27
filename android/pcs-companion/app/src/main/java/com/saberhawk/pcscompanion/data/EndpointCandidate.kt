package com.saberhawk.pcscompanion.data

import java.net.URI

enum class ConnectionKind(val displayName: String) {
    HOME_LAN("LOCAL · HOME"),
    PCS_LAN("LOCAL · PCS"),
    WIREGUARD("REMOTE · VPN"),
}

data class EndpointCandidate(
    val kind: ConnectionKind,
    val baseUrl: String,
) {
    fun validated(): EndpointCandidate {
        val parsed = runCatching { URI(baseUrl.trim()) }
            .getOrElse { throw IllegalArgumentException("Endpoint is not a valid URL.") }
        require(parsed.scheme.equals("https", ignoreCase = true)) {
            "PCS endpoints must use HTTPS."
        }
        require(!parsed.host.isNullOrBlank()) { "PCS endpoint must include a host." }
        require(parsed.userInfo == null) { "PCS endpoint cannot contain credentials." }
        require(parsed.query == null && parsed.fragment == null) {
            "PCS endpoint cannot contain a query or fragment."
        }
        require(parsed.path.isNullOrEmpty() || parsed.path == "/") {
            "PCS endpoint must not contain a path."
        }
        require(parsed.port == -1 || parsed.port in 1..65535) {
            "PCS endpoint port is invalid."
        }
        val normalized = URI(
            "https",
            null,
            parsed.host,
            parsed.port,
            null,
            null,
            null,
        ).toASCIIString()
        return copy(baseUrl = normalized)
    }

    companion object {
        val defaults = listOf(
            EndpointCandidate(ConnectionKind.HOME_LAN, "https://192.168.50.236:9443"),
            EndpointCandidate(ConnectionKind.PCS_LAN, "https://10.42.0.1:9443"),
            EndpointCandidate(ConnectionKind.WIREGUARD, "https://10.6.0.7:9443"),
        )
    }
}
