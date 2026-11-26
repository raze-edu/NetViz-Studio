package org.netviz.survey.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.netviz.survey.state.SurveyUiState
import org.netviz.survey.state.SurveyViewModel
import org.netviz.survey.ui.theme.NetVizColors

@Composable
fun ImportSyncDialog(
    state: SurveyUiState,
    viewModel: SurveyViewModel,
    onOpenQrScanner: () -> Unit,
    onOpenSaf: () -> Unit,
    onDismiss: () -> Unit
) {
    var host by remember(state.lastSyncHost) { mutableStateOf(state.lastSyncHost) }
    var portText by remember(state.lastSyncPort) { mutableStateOf(state.lastSyncPort.toString()) }
    var token by remember(state.lastSyncToken) { mutableStateOf(state.lastSyncToken) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Import Project (.netviz)") },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = onOpenQrScanner,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Scan Desktop QR Code")
                }

                OutlinedButton(
                    onClick = { viewModel.discoverDesktopsOnLan() },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Auto-Discover Desktop PC (UDP 8766)")
                }

                state.discoveredHosts.forEach { reply ->
                    Surface(
                        color = NetVizColors.SurfaceVariant,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(8.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(reply.projectName.ifBlank { reply.host }, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                                Text("${reply.ips.firstOrNull().orEmpty()}:${reply.port} · ${reply.token}", fontSize = 11.sp, color = NetVizColors.TextMuted)
                            }
                            TextButton(
                                onClick = {
                                    viewModel.importFromLanCandidates(reply.ips, reply.port, reply.token) { ok ->
                                        if (ok) onDismiss()
                                    }
                                }
                            ) {
                                Text("Connect")
                            }
                        }
                    }
                }

                HorizontalDivider()
                Text("Manual / USB ADB Sync", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(
                        value = host,
                        onValueChange = { host = it },
                        label = { Text("Host IP") },
                        singleLine = true,
                        modifier = Modifier.weight(1.6f)
                    )
                    OutlinedTextField(
                        value = portText,
                        onValueChange = { portText = it },
                        label = { Text("Port") },
                        singleLine = true,
                        modifier = Modifier.weight(1f)
                    )
                }
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text("Pairing Token") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    OutlinedButton(
                        onClick = {
                            host = "127.0.0.1"
                            portText = "8765"
                        },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("USB (127.0.0.1)", fontSize = 11.sp)
                    }
                    OutlinedButton(
                        onClick = {
                            val port = portText.toIntOrNull() ?: 8765
                            viewModel.testLanConnection(host, port, token)
                        },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Test Ping", fontSize = 11.sp)
                    }
                }
                Button(
                    onClick = {
                        val port = portText.toIntOrNull() ?: 8765
                        viewModel.importFromLanCandidates(listOf(host), port, token) { ok ->
                            if (ok) onDismiss()
                        }
                    },
                    enabled = !state.transferProgress.active,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Download from Desktop")
                }

                if (state.transferProgress.active) {
                    TransferProgressCard(state, onCancel = { viewModel.cancelActiveTransfer() })
                }

                if (!state.syncDiagnosticsText.isNullOrBlank()) {
                    Text(state.syncDiagnosticsText, fontSize = 12.sp, color = NetVizColors.Primary)
                }

                HorizontalDivider()
                OutlinedButton(onClick = onOpenSaf, modifier = Modifier.fillMaxWidth()) {
                    Text("Open .netviz File (SAF)")
                }
                OutlinedButton(
                    onClick = {
                        viewModel.loadDemoProject()
                        onDismiss()
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Load Demo Campus Plan")
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        }
    )
}

@Composable
fun ExportSyncDialog(
    state: SurveyUiState,
    viewModel: SurveyViewModel,
    onSaveSaf: () -> Unit,
    onShareSheet: () -> Unit,
    onDismiss: () -> Unit
) {
    var host by remember(state.lastSyncHost) { mutableStateOf(state.lastSyncHost) }
    var portText by remember(state.lastSyncPort) { mutableStateOf(state.lastSyncPort.toString()) }
    var token by remember(state.lastSyncToken) { mutableStateOf(state.lastSyncToken) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Export Survey (${state.session.samples.size} samples)") },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text("Push to Desktop LAN Sync", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(
                        value = host,
                        onValueChange = { host = it },
                        label = { Text("Host IP") },
                        singleLine = true,
                        modifier = Modifier.weight(1.6f)
                    )
                    OutlinedTextField(
                        value = portText,
                        onValueChange = { portText = it },
                        label = { Text("Port") },
                        singleLine = true,
                        modifier = Modifier.weight(1f)
                    )
                }
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text("Pairing Token") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Button(
                    onClick = {
                        val port = portText.toIntOrNull() ?: 8765
                        viewModel.pushSurveyToDesktop(host, port, token) { ok ->
                            if (ok) onDismiss()
                        }
                    },
                    enabled = !state.transferProgress.active,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Push to Desktop")
                }

                if (state.transferProgress.active) {
                    TransferProgressCard(state, onCancel = { viewModel.cancelActiveTransfer() })
                }

                if (!state.syncDiagnosticsText.isNullOrBlank()) {
                    Text(state.syncDiagnosticsText, fontSize = 12.sp, color = NetVizColors.Primary)
                }

                HorizontalDivider()
                OutlinedButton(onClick = onSaveSaf, modifier = Modifier.fillMaxWidth()) {
                    Text("Save .netviz-survey.json (SAF)")
                }
                OutlinedButton(onClick = onShareSheet, modifier = Modifier.fillMaxWidth()) {
                    Text("Android Share Sheet")
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        }
    )
}

@Composable
private fun TransferProgressCard(
    state: SurveyUiState,
    onCancel: () -> Unit
) {
    val prog = state.transferProgress
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(prog.label, fontSize = 12.sp)
        Spacer(Modifier.height(4.dp))
        if (prog.totalBytes > 0L) {
            val frac = (prog.bytesTransferred.toFloat() / prog.totalBytes.toFloat()).coerceIn(0f, 1f)
            LinearProgressIndicator(progress = { frac }, modifier = Modifier.fillMaxWidth())
            Text(
                "${"%.2f".format(prog.bytesTransferred / 1048576.0)} MB / " +
                    "${"%.2f".format(prog.totalBytes / 1048576.0)} MB",
                fontSize = 11.sp,
                color = NetVizColors.TextMuted
            )
        } else {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        }
        TextButton(onClick = onCancel) {
            Text("Cancel", color = NetVizColors.Error)
        }
    }
}
