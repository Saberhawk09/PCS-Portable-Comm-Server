package com.saberhawk.pcscompanion.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.saberhawk.pcscompanion.AppUiState
import com.saberhawk.pcscompanion.MainViewModel
import com.saberhawk.pcscompanion.data.ActionMetadata
import com.saberhawk.pcscompanion.data.ActionResult
import com.saberhawk.pcscompanion.data.ConnectionKind
import com.saberhawk.pcscompanion.data.EndpointCandidate
import com.saberhawk.pcscompanion.data.StatsEnvelope
import com.saberhawk.pcscompanion.security.CertificateInfo
import com.saberhawk.pcscompanion.security.CertificateInspector
import java.io.ByteArrayOutputStream
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

private enum class AppTab(val label: String, val symbol: String) {
    OVERVIEW("Overview", "●"),
    ACTIONS("Actions", "⚙"),
    SETTINGS("Settings", "≡"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PcsCompanionApp(
    authenticateAction: (String, () -> Unit, (String) -> Unit) -> Unit,
    requestLocalNetworkAccess: ((() -> Unit), (String) -> Unit) -> Unit,
    viewModel: MainViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var selectedTab by remember { mutableStateOf(AppTab.OVERVIEW) }
    var localMessage by remember { mutableStateOf<String?>(null) }
    val withLocalNetworkAccess: (() -> Unit) -> Unit = { action ->
        requestLocalNetworkAccess(action) { localMessage = it }
    }

    LaunchedEffect(state.certificateInfo) {
        if (state.certificateInfo != null) {
            withLocalNetworkAccess(viewModel::refresh)
        }
    }

    val message = state.error ?: state.notice ?: localMessage
    LaunchedEffect(message) {
        if (message != null) {
            snackbarHostState.showSnackbar(message)
            if (localMessage != null) localMessage = null else viewModel.clearMessage()
        }
    }

    if (state.certificateInfo == null) {
        Scaffold(snackbarHost = { SnackbarHost(snackbarHostState) }) { padding ->
            CertificateSetupScreen(
                state = state,
                modifier = Modifier.padding(padding),
                onSaveEndpoints = viewModel::saveEndpoints,
                onInspectCertificate = viewModel::inspectCertificate,
                onTrustCertificate = viewModel::trustPendingCertificate,
                onDiscardCertificate = viewModel::discardPendingCertificate,
                onLocalError = { localMessage = it },
            )
        }
        return
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("PCS Companion", fontWeight = FontWeight.SemiBold)
                        ConnectionLine(state)
                    }
                },
                actions = {
                    if (state.loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.padding(end = 16.dp).size(22.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        TextButton(onClick = { withLocalNetworkAccess(viewModel::refresh) }) {
                            Text("Refresh")
                        }
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
        bottomBar = {
            NavigationBar(modifier = Modifier.navigationBarsPadding()) {
                AppTab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = selectedTab == tab,
                        onClick = { selectedTab = tab },
                        icon = { Text(tab.symbol) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        when (selectedTab) {
            AppTab.OVERVIEW -> OverviewScreen(state, Modifier.padding(padding))
            AppTab.ACTIONS -> ActionsScreen(
                state = state,
                modifier = Modifier.padding(padding),
                onLoadActions = { withLocalNetworkAccess(viewModel::loadActions) },
                onRunAction = { action ->
                    authenticateAction(
                        action.label,
                        { withLocalNetworkAccess { viewModel.runAction(action) } },
                        { localMessage = it },
                    )
                },
                onDismissResult = viewModel::dismissActionResult,
            )
            AppTab.SETTINGS -> SettingsScreen(
                state = state,
                modifier = Modifier.padding(padding),
                onSaveEndpoints = viewModel::saveEndpoints,
                onInspectCertificate = viewModel::inspectCertificate,
                onTrustCertificate = viewModel::trustPendingCertificate,
                onDiscardCertificate = viewModel::discardPendingCertificate,
                onPair = { deviceId, password ->
                    withLocalNetworkAccess { viewModel.pair(deviceId, password) }
                },
                onChangePassword = { currentPassword, newPassword ->
                    authenticateAction(
                        "Change PCS administrator password",
                        {
                            withLocalNetworkAccess {
                                viewModel.changePassword(currentPassword, newPassword)
                            }
                        },
                        { localMessage = it },
                    )
                },
                onForget = viewModel::forgetPcs,
                onLocalError = { localMessage = it },
            )
        }
    }
}

@Composable
private fun ConnectionLine(state: AppUiState) {
    val label = when {
        state.fromCache -> "OFFLINE · cached"
        state.activeEndpoint != null -> state.activeEndpoint.kind.displayName
        else -> "CONNECTING"
    }
    val access = state.status?.access?.uppercase() ?: if (state.paired) "PAIRED" else "PUBLIC"
    Text(
        text = "$label · $access",
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun CertificateSetupScreen(
    state: AppUiState,
    modifier: Modifier,
    onSaveEndpoints: (List<EndpointCandidate>) -> Unit,
    onInspectCertificate: (ByteArray) -> Unit,
    onTrustCertificate: () -> Unit,
    onDiscardCertificate: () -> Unit,
    onLocalError: (String) -> Unit,
) {
    Column(
        modifier = modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        Text("Trust your PCS", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text(
            "PCS Companion will only connect over HTTPS to the exact certificate you approve. " +
                "It never falls back to cleartext or silently accepts a replacement certificate.",
        )
        EndpointEditor(state.endpoints, onSaveEndpoints)
        CertificatePanel(
            current = null,
            pending = state.pendingCertificateInfo,
            onInspectCertificate = onInspectCertificate,
            onTrustCertificate = onTrustCertificate,
            onDiscardCertificate = onDiscardCertificate,
            onLocalError = onLocalError,
        )
    }
}

@Composable
private fun OverviewScreen(state: AppUiState, modifier: Modifier = Modifier) {
    val status = state.status
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            SummaryCard(status, state.fromCache)
        }
        if (status == null) {
            item {
                EmptyCard("No status has been received yet. Use Refresh after checking network or VPN access.")
            }
        } else {
            val ordered = orderedStatusSections(status.data)
            items(ordered, key = { it.first }) { (name, value) ->
                JsonSectionCard(humanize(name), value)
            }
            status.details?.let { details ->
                item {
                    Text(
                        "Administrator details",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
                items(details.entries.toList(), key = { "detail-${it.key}" }) { entry ->
                    JsonSectionCard(humanize(entry.key), entry.value)
                }
            }
        }
    }
}

@Composable
private fun SummaryCard(status: StatsEnvelope?, fromCache: Boolean) {
    val severity = status?.health?.severity ?: "unknown"
    val color = when (severity) {
        "ok" -> Color(0xFF146C43)
        "warn" -> Color(0xFF8A5700)
        "bad" -> MaterialTheme.colorScheme.error
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(Modifier.size(12.dp).background(color))
                Text(
                    if (fromCache) "PCS offline" else "PCS ${severity.uppercase()}",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
            }
            Text("Generated: ${status?.generatedAt ?: "not available"}")
            if (fromCache) {
                Text(
                    "Cached data — do not treat this as current.",
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

@Composable
private fun JsonSectionCard(title: String, value: JsonElement) {
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            when (value) {
                is JsonObject -> value.entries.forEachIndexed { index, entry ->
                    if (index > 0) HorizontalDivider(color = MaterialTheme.colorScheme.surfaceVariant)
                    KeyValueRow(humanize(entry.key), displayValue(entry.value))
                }
                else -> Text(displayValue(value))
            }
        }
    }
}

@Composable
private fun KeyValueRow(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun ActionsScreen(
    state: AppUiState,
    modifier: Modifier,
    onLoadActions: () -> Unit,
    onRunAction: (ActionMetadata) -> Unit,
    onDismissResult: () -> Unit,
) {
    var pendingAction by remember { mutableStateOf<ActionMetadata?>(null) }
    LaunchedEffect(state.paired, state.activeEndpoint) {
        if (state.paired && state.activeEndpoint != null && state.actions.isEmpty()) onLoadActions()
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (!state.paired) {
            item { EmptyCard("Pair this phone in Settings to load administrative actions.") }
        } else if (state.actions.isEmpty() && !state.loading) {
            item {
                EmptyCard("The action catalog is not loaded.")
                Spacer(Modifier.height(8.dp))
                Button(onClick = onLoadActions) { Text("Load actions") }
            }
        } else {
            state.actions.groupBy { it.group }.forEach { (group, actions) ->
                item {
                    Text(humanize(group), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                }
                items(actions, key = { it.name }) { action ->
                    ActionCard(
                        action = action,
                        running = state.actionRunning == action.name,
                        enabled = state.actionRunning == null,
                        onClick = { pendingAction = action },
                    )
                }
            }
        }
    }

    pendingAction?.let { action ->
        AlertDialog(
            onDismissRequest = { pendingAction = null },
            title = { Text(if (action.dangerous) "Confirm dangerous action" else "Confirm action") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(action.label, fontWeight = FontWeight.Bold)
                    Text(action.description)
                    Text(
                        if (action.challengeRequired) {
                            "Android will require a biometric or device credential before requesting the one-time PCS challenge."
                        } else {
                            "Android will require a biometric or device credential before sending this request."
                        },
                    )
                }
            },
            confirmButton = {
                Button(onClick = {
                    pendingAction = null
                    onRunAction(action)
                }) { Text(if (action.dangerous) "Authorize and run" else "Run") }
            },
            dismissButton = { TextButton(onClick = { pendingAction = null }) { Text("Cancel") } },
        )
    }
    state.lastActionResult?.let { result ->
        ActionResultDialog(result, onDismissResult)
    }
}

@Composable
private fun ActionCard(
    action: ActionMetadata,
    running: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(action.label, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                if (action.dangerous) Text("DANGEROUS", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
            }
            Text(action.description)
            Button(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
                if (running) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Text("Review and authorize")
                }
            }
        }
    }
}

@Composable
private fun ActionResultDialog(result: ActionResult, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (result.ok) "Action completed" else "Action failed") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                KeyValueRow("Action", result.action)
                KeyValueRow("Request ID", result.requestId)
                KeyValueRow("Completed", result.completedAt)
                KeyValueRow("Duration", "${result.durationMs} ms")
                SelectionContainer {
                    Text(
                        result.output.ifBlank { "No output" } + if (result.outputTruncated) "\n[output truncated]" else "",
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun SettingsScreen(
    state: AppUiState,
    modifier: Modifier,
    onSaveEndpoints: (List<EndpointCandidate>) -> Unit,
    onInspectCertificate: (ByteArray) -> Unit,
    onTrustCertificate: () -> Unit,
    onDiscardCertificate: () -> Unit,
    onPair: (String, String) -> Unit,
    onChangePassword: (String, String) -> Unit,
    onForget: () -> Unit,
    onLocalError: (String) -> Unit,
) {
    var showForgetDialog by remember { mutableStateOf(false) }
    Column(
        modifier = modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        EndpointEditor(state.endpoints, onSaveEndpoints)
        CertificatePanel(
            current = state.certificateInfo,
            pending = state.pendingCertificateInfo,
            onInspectCertificate = onInspectCertificate,
            onTrustCertificate = onTrustCertificate,
            onDiscardCertificate = onDiscardCertificate,
            onLocalError = onLocalError,
        )
        PairingPanel(state.paired, state.loading, onPair)
        if (state.paired) PasswordPanel(state.loading, onChangePassword, onLocalError)
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Forget this PCS", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text("Deletes the local certificate, endpoints, cache, Keystore token, and encryption key. It does not revoke the server-side token digest.")
                OutlinedButton(onClick = { showForgetDialog = true }) { Text("Forget PCS") }
            }
        }
    }
    if (showForgetDialog) {
        AlertDialog(
            onDismissRequest = { showForgetDialog = false },
            title = { Text("Delete local PCS trust?") },
            text = { Text("If the phone was lost or the token may have escaped, revoke this device ID on PCS as well. This local action cannot perform server revocation.") },
            confirmButton = {
                Button(onClick = {
                    showForgetDialog = false
                    onForget()
                }) { Text("Delete local data") }
            },
            dismissButton = { TextButton(onClick = { showForgetDialog = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun EndpointEditor(
    endpoints: List<EndpointCandidate>,
    onSave: (List<EndpointCandidate>) -> Unit,
) {
    var home by remember(endpoints) { mutableStateOf(endpoints.first { it.kind == ConnectionKind.HOME_LAN }.baseUrl) }
    var pcs by remember(endpoints) { mutableStateOf(endpoints.first { it.kind == ConnectionKind.PCS_LAN }.baseUrl) }
    var wireGuard by remember(endpoints) { mutableStateOf(endpoints.first { it.kind == ConnectionKind.WIREGUARD }.baseUrl) }
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Connection order", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text("The first trusted v1 endpoint wins. HTTPS and normal hostname verification are mandatory.")
            OutlinedTextField(home, { home = it }, label = { Text("1 · Home LAN") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(pcs, { pcs = it }, label = { Text("2 · PCS local LAN") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(wireGuard, { wireGuard = it }, label = { Text("3 · WireGuard") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(
                onClick = {
                    onSave(
                        listOf(
                            EndpointCandidate(ConnectionKind.HOME_LAN, home),
                            EndpointCandidate(ConnectionKind.PCS_LAN, pcs),
                            EndpointCandidate(ConnectionKind.WIREGUARD, wireGuard),
                        ),
                    )
                },
            ) { Text("Save endpoints") }
        }
    }
}

@Composable
private fun CertificatePanel(
    current: CertificateInfo?,
    pending: CertificateInfo?,
    onInspectCertificate: (ByteArray) -> Unit,
    onTrustCertificate: () -> Unit,
    onDiscardCertificate: () -> Unit,
    onLocalError: (String) -> Unit,
) {
    val context = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) {
            runCatching {
                context.contentResolver.openInputStream(uri)?.use(::readCertificateBytes)
                    ?: error("Unable to open the selected certificate.")
            }.onSuccess(onInspectCertificate).onFailure { onLocalError(it.message ?: "Unable to read certificate.") }
        }
    }
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Certificate trust", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            current?.let {
                Text("Currently trusted", fontWeight = FontWeight.SemiBold)
                CertificateDetails(it)
            }
            OutlinedButton(
                onClick = {
                    launcher.launch(arrayOf("application/x-x509-ca-cert", "application/pkix-cert", "application/octet-stream", "text/plain"))
                },
            ) { Text(if (current == null) "Import certificate" else "Import replacement certificate") }
            pending?.let {
                HorizontalDivider()
                Text("Review before trusting", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                CertificateDetails(it)
                Text("Verify this fingerprint out of band. Trusting it changes the identity PCS Companion will accept.")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onTrustCertificate) { Text("Trust this certificate") }
                    TextButton(onClick = onDiscardCertificate) { Text("Cancel") }
                }
            }
        }
    }
}

@Composable
private fun CertificateDetails(info: CertificateInfo) {
    SelectionContainer {
        Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text(info.fingerprintSha256, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall)
            Text("SAN: ${info.identities.joinToString()}")
            Text("Valid: ${info.notBefore} — ${info.notAfter}")
            Text("Subject: ${info.subject}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun PairingPanel(
    paired: Boolean,
    loading: Boolean,
    onPair: (String, String) -> Unit,
) {
    var deviceId by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Device pairing", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            if (paired) {
                Text("Paired. The bearer token is encrypted by Android Keystore; the administrator password was not saved.")
            } else {
                Text("The existing PCS administrator password is sent once over pinned TLS and is never saved.")
                OutlinedTextField(deviceId, { deviceId = it }, label = { Text("Device ID") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(
                    password,
                    { password = it },
                    label = { Text("PCS administrator password") },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = !loading && deviceId.isNotBlank() && password.isNotEmpty(),
                    onClick = {
                        val submitted = password
                        password = ""
                        onPair(deviceId, submitted)
                    },
                ) { Text("Pair this device") }
            }
        }
    }
}

@Composable
private fun PasswordPanel(
    loading: Boolean,
    onChangePassword: (String, String) -> Unit,
    onLocalError: (String) -> Unit,
) {
    var current by remember { mutableStateOf("") }
    var replacement by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    Card {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Administrator password", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text("Passwords are kept only long enough to submit this pinned-TLS request.")
            OutlinedTextField(current, { current = it }, label = { Text("Current password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(replacement, { replacement = it }, label = { Text("New password · 12+ characters") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(confirmation, { confirmation = it }, label = { Text("Confirm new password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(
                enabled = !loading && current.isNotEmpty() && replacement.length >= 12 && confirmation.isNotEmpty(),
                onClick = {
                    if (replacement != confirmation) {
                        onLocalError("New password confirmation does not match.")
                    } else {
                        val submittedCurrent = current
                        val submittedReplacement = replacement
                        current = ""
                        replacement = ""
                        confirmation = ""
                        onChangePassword(submittedCurrent, submittedReplacement)
                    }
                },
            ) { Text("Change PCS password") }
        }
    }
}

@Composable
private fun EmptyCard(message: String) {
    Card {
        Text(message, modifier = Modifier.fillMaxWidth().padding(16.dp))
    }
}

private fun orderedStatusSections(data: JsonObject): List<Pair<String, JsonElement>> {
    val order = listOf(
        "system", "network", "cellular", "gnss", "time", "storage", "services",
        "aprs", "meshtastic", "pistar", "remote_management",
    )
    val ranks = order.withIndex().associate { it.value to it.index }
    return data.entries
        .sortedWith(compareBy({ ranks[it.key] ?: Int.MAX_VALUE }, { it.key }))
        .map { it.key to it.value }
}

private fun humanize(value: String): String = value
    .replace('_', ' ')
    .replace('-', ' ')
    .split(' ')
    .joinToString(" ") { word -> word.replaceFirstChar { it.uppercase() } }

private fun displayValue(value: JsonElement): String = when (value) {
    JsonNull -> "Unavailable"
    is JsonPrimitive -> value.booleanOrNull?.let { if (it) "Yes" else "No" }
        ?: value.contentOrNull
        ?: "Unavailable"
    is JsonArray -> value.joinToString(", ") { displayValue(it) }
    is JsonObject -> value.entries.joinToString(" · ") { "${humanize(it.key)}: ${displayValue(it.value)}" }
}

private fun readCertificateBytes(input: java.io.InputStream): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(4096)
    while (true) {
        val read = input.read(buffer)
        if (read < 0) break
        output.write(buffer, 0, read)
        require(output.size() <= CertificateInspector.MAX_CERTIFICATE_BYTES) {
            "Certificate exceeds the 64 KiB safety limit."
        }
    }
    return output.toByteArray()
}
