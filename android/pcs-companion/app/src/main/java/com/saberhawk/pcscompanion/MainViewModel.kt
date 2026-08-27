package com.saberhawk.pcscompanion

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.saberhawk.pcscompanion.data.ActionMetadata
import com.saberhawk.pcscompanion.data.ActionResult
import com.saberhawk.pcscompanion.data.EndpointCandidate
import com.saberhawk.pcscompanion.data.PcsApiException
import com.saberhawk.pcscompanion.data.PcsRepository
import com.saberhawk.pcscompanion.data.StatsEnvelope
import com.saberhawk.pcscompanion.security.CertificateInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class AppUiState(
    val certificateInfo: CertificateInfo? = null,
    val pendingCertificateInfo: CertificateInfo? = null,
    val endpoints: List<EndpointCandidate> = EndpointCandidate.defaults,
    val status: StatsEnvelope? = null,
    val activeEndpoint: EndpointCandidate? = null,
    val fromCache: Boolean = false,
    val paired: Boolean = false,
    val actions: List<ActionMetadata> = emptyList(),
    val lastActionResult: ActionResult? = null,
    val loading: Boolean = false,
    val actionRunning: String? = null,
    val notice: String? = null,
    val error: String? = null,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = PcsRepository(application)
    private val mutableState = MutableStateFlow(
        AppUiState(
            certificateInfo = repository.certificateInfo(),
            endpoints = repository.endpoints(),
            paired = repository.isPaired(),
        ),
    )
    val state: StateFlow<AppUiState> = mutableState.asStateFlow()
    private var pendingCertificateBytes: ByteArray? = null

    init {
        if (mutableState.value.certificateInfo != null) refresh()
    }

    fun inspectCertificate(bytes: ByteArray) {
        pendingCertificateBytes?.fill(0)
        pendingCertificateBytes = null
        mutableState.update { it.copy(pendingCertificateInfo = null) }
        runCatching { repository.inspectCertificate(bytes) }
            .onSuccess { info ->
                pendingCertificateBytes = bytes.copyOf()
                mutableState.update {
                    it.copy(pendingCertificateInfo = info, error = null, notice = null)
                }
            }
            .onFailure { error -> showError(error) }
    }

    fun discardPendingCertificate() {
        pendingCertificateBytes?.fill(0)
        pendingCertificateBytes = null
        mutableState.update { it.copy(pendingCertificateInfo = null) }
    }

    fun trustPendingCertificate() {
        val bytes = pendingCertificateBytes ?: return
        runCatching { repository.trustCertificate(bytes) }
            .onSuccess { result ->
                bytes.fill(0)
                pendingCertificateBytes = null
                mutableState.update {
                    it.copy(
                        certificateInfo = result.info,
                        pendingCertificateInfo = null,
                        status = null,
                        activeEndpoint = null,
                        fromCache = false,
                        paired = repository.isPaired(),
                        actions = if (result.identityChanged) emptyList() else it.actions,
                        notice = if (result.identityChanged) {
                            "PCS certificate identity changed. The old device token was deleted; pair again after verifying the new identity."
                        } else {
                            "PCS certificate trusted. Connecting with strict TLS verification…"
                        },
                        error = null,
                    )
                }
                refresh()
            }
            .onFailure { error -> showError(error) }
    }

    fun saveEndpoints(endpoints: List<EndpointCandidate>) {
        runCatching {
            val validated = endpoints.map(EndpointCandidate::validated)
            repository.saveEndpoints(validated)
            validated
        }.onSuccess { validated ->
            mutableState.update {
                it.copy(endpoints = validated, notice = "Endpoint order saved.", error = null)
            }
            if (mutableState.value.certificateInfo != null) refresh()
        }.onFailure { error -> showError(error) }
    }

    fun refresh() {
        if (mutableState.value.certificateInfo == null || mutableState.value.loading) return
        viewModelScope.launch {
            mutableState.update { it.copy(loading = true, error = null) }
            runCatching { repository.refresh() }
                .onSuccess { result ->
                    mutableState.update {
                        it.copy(
                            status = result.status,
                            activeEndpoint = result.endpoint,
                            fromCache = result.fromCache,
                            paired = repository.isPaired(),
                            actions = if (result.tokenRevoked) emptyList() else it.actions,
                            loading = false,
                            notice = when {
                                result.tokenRevoked -> "The PCS token was rejected or revoked. Public mode is active."
                                result.fromCache -> "PCS is offline. Showing the last saved status."
                                else -> null
                            },
                            error = null,
                        )
                    }
                }
                .onFailure { error ->
                    mutableState.update { it.copy(loading = false) }
                    showError(error)
                }
        }
    }

    fun pair(deviceId: String, adminPassword: String) {
        if (mutableState.value.loading) return
        viewModelScope.launch {
            mutableState.update { it.copy(loading = true, error = null, notice = null) }
            runCatching { repository.pair(deviceId.trim(), adminPassword) }
                .onSuccess { result ->
                    mutableState.update {
                        it.copy(
                            paired = true,
                            activeEndpoint = result.endpoint,
                            loading = false,
                            notice = "${result.deviceId} paired. The token is encrypted by Android Keystore.",
                        )
                    }
                    refresh()
                }
                .onFailure { error ->
                    mutableState.update { it.copy(loading = false) }
                    showError(error)
                }
        }
    }

    fun loadActions() {
        val endpoint = mutableState.value.activeEndpoint ?: return
        if (!mutableState.value.paired || mutableState.value.loading) return
        viewModelScope.launch {
            mutableState.update { it.copy(loading = true, error = null) }
            runCatching { repository.actions(endpoint) }
                .onSuccess { actions ->
                    mutableState.update { it.copy(actions = actions, loading = false) }
                }
                .onFailure { error -> handleAuthenticatedFailure(error) }
        }
    }

    fun runAction(action: ActionMetadata) {
        val endpoint = mutableState.value.activeEndpoint ?: return
        if (mutableState.value.actionRunning != null) return
        viewModelScope.launch {
            mutableState.update {
                it.copy(actionRunning = action.name, error = null, lastActionResult = null)
            }
            runCatching { repository.runAction(endpoint, action) }
                .onSuccess { result ->
                    mutableState.update {
                        it.copy(
                            actionRunning = null,
                            lastActionResult = result,
                            notice = if (result.ok) "${action.label} completed." else "${action.label} reported a failure.",
                        )
                    }
                    if (action.name !in setOf("reboot-system", "shutdown-system")) refresh()
                }
                .onFailure { error -> handleAuthenticatedFailure(error, actionRunning = null) }
        }
    }

    fun changePassword(currentPassword: String, newPassword: String) {
        val endpoint = mutableState.value.activeEndpoint ?: return
        if (!mutableState.value.paired || mutableState.value.loading) return
        viewModelScope.launch {
            mutableState.update { it.copy(loading = true, error = null, notice = null) }
            runCatching { repository.changePassword(endpoint, currentPassword, newPassword) }
                .onSuccess {
                    mutableState.update {
                        it.copy(loading = false, notice = "PCS administrator password changed.")
                    }
                }
                .onFailure { error -> handleAuthenticatedFailure(error) }
        }
    }

    fun forgetPcs() {
        runCatching { repository.forget() }
            .onSuccess {
                pendingCertificateBytes?.fill(0)
                pendingCertificateBytes = null
                mutableState.value = AppUiState(
                    endpoints = EndpointCandidate.defaults,
                    notice = "Local PCS trust and token were deleted. Revoke this device ID on PCS if the token may have escaped.",
                )
            }
            .onFailure { error -> showError(error) }
    }

    fun dismissActionResult() = mutableState.update { it.copy(lastActionResult = null) }

    fun clearMessage() = mutableState.update { it.copy(notice = null, error = null) }

    private fun handleAuthenticatedFailure(error: Throwable, actionRunning: String? = mutableState.value.actionRunning) {
        if (error is PcsApiException && error.httpStatus == 401) {
            repository.clearToken()
            mutableState.update {
                it.copy(
                    paired = false,
                    actions = emptyList(),
                    actionRunning = actionRunning,
                    loading = false,
                    notice = "The PCS token was rejected or revoked. Public mode is active.",
                    error = null,
                )
            }
            refresh()
        } else {
            mutableState.update { it.copy(actionRunning = actionRunning, loading = false) }
            showError(error)
        }
    }

    private fun showError(error: Throwable) {
        val message = when (error) {
            is PcsApiException -> buildString {
                append(error.message ?: "PCS request failed.")
                error.retryAfterSeconds?.let { append(" Try again in $it seconds.") }
                append(" [${error.code}]")
            }
            else -> error.message ?: "Unexpected local error."
        }
        mutableState.update { it.copy(error = message, notice = null) }
    }
}
