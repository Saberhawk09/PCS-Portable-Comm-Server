package com.saberhawk.pcscompanion.data

import android.annotation.SuppressLint
import android.content.Context
import android.util.Base64
import kotlinx.serialization.encodeToString

// These writes intentionally inspect commit() so pairing/trust never reports a
// successful durable transition when Android rejected the preference write.
@SuppressLint("UseKtx")
class PcsPreferences(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun endpoints(): List<EndpointCandidate> = listOf(
        EndpointCandidate(
            ConnectionKind.HOME_LAN,
            preferences.getString(KEY_HOME_ENDPOINT, null)
                ?: EndpointCandidate.defaults[0].baseUrl,
        ),
        EndpointCandidate(
            ConnectionKind.PCS_LAN,
            preferences.getString(KEY_PCS_ENDPOINT, null)
                ?: EndpointCandidate.defaults[1].baseUrl,
        ),
        EndpointCandidate(
            ConnectionKind.WIREGUARD,
            preferences.getString(KEY_WIREGUARD_ENDPOINT, null)
                ?: EndpointCandidate.defaults[2].baseUrl,
        ),
    )

    fun saveEndpoints(endpoints: List<EndpointCandidate>) {
        val byKind = endpoints.associateBy { it.kind }
        check(preferences.edit()
            .putString(KEY_HOME_ENDPOINT, byKind.getValue(ConnectionKind.HOME_LAN).validated().baseUrl)
            .putString(KEY_PCS_ENDPOINT, byKind.getValue(ConnectionKind.PCS_LAN).validated().baseUrl)
            .putString(KEY_WIREGUARD_ENDPOINT, byKind.getValue(ConnectionKind.WIREGUARD).validated().baseUrl)
            .commit()) { "Unable to save PCS endpoints." }
    }

    fun certificateBytes(): ByteArray? = preferences.getString(KEY_CERTIFICATE, null)?.let {
        runCatching { Base64.decode(it, Base64.NO_WRAP) }.getOrNull()
    }

    fun saveCertificate(certificateDer: ByteArray) {
        check(preferences.edit()
            .putString(KEY_CERTIFICATE, Base64.encodeToString(certificateDer, Base64.NO_WRAP))
            .remove(KEY_CACHED_STATUS)
            .remove(KEY_CACHED_KIND)
            .commit()) { "Unable to save the trusted PCS certificate." }
    }

    fun saveStatus(status: StatsEnvelope, kind: ConnectionKind) {
        preferences.edit()
            .putString(KEY_CACHED_STATUS, PcsJson.format.encodeToString(status))
            .putString(KEY_CACHED_KIND, kind.name)
            .apply()
    }

    fun cachedStatus(): Pair<StatsEnvelope, ConnectionKind>? {
        val raw = preferences.getString(KEY_CACHED_STATUS, null) ?: return null
        val kind = preferences.getString(KEY_CACHED_KIND, null)
            ?.let { runCatching { ConnectionKind.valueOf(it) }.getOrNull() }
            ?: return null
        val status = runCatching { PcsJson.format.decodeFromString<StatsEnvelope>(raw) }.getOrNull()
            ?: return null
        return status to kind
    }

    fun clearAll() {
        check(preferences.edit().clear().commit()) { "Unable to clear PCS settings." }
    }

    private companion object {
        const val PREFERENCES_NAME = "pcs_settings"
        const val KEY_HOME_ENDPOINT = "home_endpoint"
        const val KEY_PCS_ENDPOINT = "pcs_endpoint"
        const val KEY_WIREGUARD_ENDPOINT = "wireguard_endpoint"
        const val KEY_CERTIFICATE = "trusted_certificate_der"
        const val KEY_CACHED_STATUS = "cached_status"
        const val KEY_CACHED_KIND = "cached_connection_kind"
    }
}
