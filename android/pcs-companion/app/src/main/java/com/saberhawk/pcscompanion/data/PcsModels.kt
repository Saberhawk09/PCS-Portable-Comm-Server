package com.saberhawk.pcscompanion.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class Health(
    val severity: String,
    val offline: Boolean = false,
)

@Serializable
data class Discovery(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val access: String,
    @SerialName("content_type") val contentType: String,
    val authentication: JsonObject,
    val resources: Map<String, String>,
    val pairing: String,
    val actions: String,
    val password: String,
    val methods: List<String>,
    @SerialName("write_actions") val writeActions: Boolean,
)

@Serializable
data class StatsEnvelope(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    @SerialName("generated_at") val generatedAt: String?,
    val health: Health,
    val access: String,
    val data: JsonObject,
    val details: JsonObject?,
)

@Serializable
data class PairingRequest(
    @SerialName("admin_password") val adminPassword: String,
    @SerialName("device_id") val deviceId: String,
)

@Serializable
data class PairingEnvelope(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val access: String,
    @SerialName("device_id") val deviceId: String,
    @SerialName("token_type") val tokenType: String,
    val token: String,
    val scopes: List<String>,
)

@Serializable
data class ActionCatalog(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val access: String,
    @SerialName("required_scope") val requiredScope: String,
    val actions: List<ActionMetadata>,
)

@Serializable
data class ActionMetadata(
    val name: String,
    val label: String,
    val description: String,
    val group: String,
    val dangerous: Boolean,
    @SerialName("challenge_required") val challengeRequired: Boolean,
    @SerialName("execute_path") val executePath: String,
    @SerialName("challenge_path") val challengePath: String?,
)

@Serializable
data class ActionChallenge(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val action: String,
    val challenge: String,
    @SerialName("expires_in") val expiresIn: Int,
    val confirmation: String,
)

@Serializable
data class ConfirmedActionRequest(
    val confirmation: String,
    val challenge: String,
)

@Serializable
data class ActionResult(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val action: String,
    @SerialName("request_id") val requestId: String,
    @SerialName("completed_at") val completedAt: String,
    @SerialName("exit_code") val exitCode: Int,
    val ok: Boolean,
    val output: String,
    @SerialName("output_truncated") val outputTruncated: Boolean,
    @SerialName("duration_ms") val durationMs: Int,
)

@Serializable
data class PasswordChangeRequest(
    @SerialName("current_password") val currentPassword: String,
    @SerialName("new_password") val newPassword: String,
)

@Serializable
data class PasswordChangeResponse(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val changed: Boolean,
)

@Serializable
data class BackupSettingsData(
    val enabled: Boolean,
    @SerialName("interval_minutes") val intervalMinutes: Int,
    @SerialName("keep_history") val keepHistory: Boolean,
    @SerialName("minimum_interval_minutes") val minimumIntervalMinutes: Int,
    @SerialName("maximum_interval_minutes") val maximumIntervalMinutes: Int,
    @SerialName("non_destructive") val nonDestructive: Boolean,
)

@Serializable
data class BackupSettingsEnvelope(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("schema_version") val schemaVersion: String,
    val resource: String,
    val access: String,
    val data: BackupSettingsData,
    @SerialName("request_id") val requestId: String? = null,
)

@Serializable
data class BackupSettingsRequest(
    val enabled: Boolean,
    @SerialName("interval_minutes") val intervalMinutes: Int,
    @SerialName("keep_history") val keepHistory: Boolean,
)

@Serializable
data class Problem(
    val type: String,
    val title: String,
    val status: Int,
    val code: String,
    @SerialName("request_id") val requestId: String,
)
