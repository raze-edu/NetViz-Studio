package org.netviz.survey.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import org.netviz.survey.model.SurveySession
import org.netviz.survey.model.netVizJson
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.net.ConnectException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import kotlin.coroutines.cancellation.CancellationException

@Serializable
data class QrPairingPayload(
    @SerialName("v") val version: Int = 2,
    @SerialName("ips") val ips: List<String> = emptyList(),
    @SerialName("ip") val singleIp: String? = null,
    @SerialName("port") val port: Int = 8765,
    @SerialName("token") val token: String = "",
    @SerialName("project") val project: String = ""
) {
    fun resolvedIps(): List<String> =
        (ips + listOfNotNull(singleIp)).map { it.trim() }.filter { it.isNotEmpty() }.distinct()
}

@Serializable
data class LanPingResponse(
    @SerialName("ok") val ok: Boolean = false,
    @SerialName("app") val app: String = "",
    @SerialName("schema_version") val schemaVersion: String = "",
    @SerialName("project_name") val projectName: String = "",
    @SerialName("bundle_bytes") val bundleBytes: Long = 0L,
    @SerialName("bundle_sha256") val bundleSha256: String = "",
    @SerialName("token_required") val tokenRequired: Boolean = true,
    @SerialName("token_valid") val tokenValid: Boolean? = null
)

@Serializable
data class UdpDiscoveryReply(
    @SerialName("app") val app: String = "",
    @SerialName("host") val host: String = "",
    @SerialName("ips") val ips: List<String> = emptyList(),
    @SerialName("port") val port: Int = 8765,
    @SerialName("token") val token: String = "",
    @SerialName("project_name") val projectName: String = ""
)

@Serializable
data class UploadSurveyResponse(
    @SerialName("ok") val ok: Boolean = false,
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("sample_count") val sampleCount: Int = 0,
    @SerialName("error") val error: String? = null
)

data class DownloadResult(
    val file: File,
    val bytesRead: Long,
    val sha256: String
)

enum class LanSyncErrorKind {
    TIMEOUT,
    CONNECTION_REFUSED,
    UNAUTHORIZED,
    SHA256_MISMATCH,
    HTTP_ERROR,
    CANCELLED,
    INVALID_PAYLOAD,
    NETWORK_ERROR
}

class LanSyncException(
    val kind: LanSyncErrorKind,
    override val message: String,
    val httpStatus: Int? = null,
    override val cause: Throwable? = null
) : Exception(message, cause)

interface LanHttpTransport {
    fun openConnection(url: URL, host: String): HttpURLConnection
}

class StandardHttpTransport : LanHttpTransport {
    override fun openConnection(url: URL, host: String): HttpURLConnection =
        url.openConnection() as HttpURLConnection
}

class AndroidWifiBoundTransport(private val appContext: Context) : LanHttpTransport {
    override fun openConnection(url: URL, host: String): HttpURLConnection {
        if (LanSyncClient.isPrivateRfc1918Ipv4(host)) {
            val wifiNet = findActiveWifiNetwork(appContext)
            if (wifiNet != null) {
                return wifiNet.openConnection(url) as HttpURLConnection
            }
        }
        return url.openConnection() as HttpURLConnection
    }

    private fun findActiveWifiNetwork(context: Context): Network? {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return null
        val active = cm.activeNetwork
        if (active != null) {
            val caps = cm.getNetworkCapabilities(active)
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                return active
            }
        }
        for (net in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(net)
            if (caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                return net
            }
        }
        return null
    }
}

class LanSyncClient(
    private val transport: LanHttpTransport = StandardHttpTransport()
) {
    constructor(context: Context) : this(AndroidWifiBoundTransport(context.applicationContext))

    fun parseQrPayload(rawText: String): QrPairingPayload {
        val trimmed = rawText.trim()
        if (trimmed.isEmpty()) {
            throw LanSyncException(LanSyncErrorKind.INVALID_PAYLOAD, "QR payload is empty.")
        }
        return try {
            val payload = netVizJson.decodeFromString<QrPairingPayload>(trimmed)
            if (payload.resolvedIps().isEmpty() || payload.port !in 1..65535) {
                throw LanSyncException(
                    LanSyncErrorKind.INVALID_PAYLOAD,
                    "QR payload missing valid IP address or port."
                )
            }
            payload
        } catch (e: LanSyncException) {
            throw e
        } catch (e: Exception) {
            throw LanSyncException(
                LanSyncErrorKind.INVALID_PAYLOAD,
                "Unrecognized NetViz QR code format: ${e.message}",
                cause = e
            )
        }
    }

    suspend fun pingHost(
        host: String,
        port: Int,
        token: String? = null
    ): LanPingResponse = withContext(Dispatchers.IO) {
        val cleanHost = host.trim()
        val query = if (!token.isNullOrBlank()) {
            "?token=${URLEncoder.encode(token.trim(), "UTF-8")}"
        } else {
            ""
        }
        val url = URL("http://$cleanHost:$port/api/ping$query")
        var conn: HttpURLConnection? = null
        try {
            conn = transport.openConnection(url, cleanHost).apply {
                requestMethod = "GET"
                connectTimeout = PING_TIMEOUT_MS
                readTimeout = PING_TIMEOUT_MS
                useCaches = false
                if (!token.isNullOrBlank()) {
                    setRequestProperty(HEADER_TOKEN, token.trim())
                }
            }
            val status = conn.responseCode
            val bodyText = readResponseBody(conn, status >= 400)
            if (status != HttpURLConnection.HTTP_OK) {
                throw mapHttpError(status, cleanHost, port, bodyText)
            }
            netVizJson.decodeFromString<LanPingResponse>(bodyText)
        } catch (e: CancellationException) {
            throw e
        } catch (e: LanSyncException) {
            throw e
        } catch (e: Exception) {
            throw mapNetworkException(e, cleanHost, port)
        } finally {
            conn?.disconnect()
        }
    }

    suspend fun probeFirstReachableHost(
        ips: List<String>,
        port: Int,
        token: String? = null
    ): Pair<String, LanPingResponse> = withContext(Dispatchers.IO) {
        val candidates = ips.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        if (candidates.isEmpty()) {
            throw LanSyncException(
                LanSyncErrorKind.INVALID_PAYLOAD,
                "No IP addresses provided to probe."
            )
        }

        val winner = CompletableDeferred<Pair<String, LanPingResponse>>()
        val errors = java.util.Collections.synchronizedList(mutableListOf<LanSyncException>())
        val remaining = java.util.concurrent.atomic.AtomicInteger(candidates.size)
        val probeScope = CoroutineScope(kotlinx.coroutines.SupervisorJob() + Dispatchers.IO)

        val callerJob = coroutineContext[Job]
        val callerCancelHandle = callerJob?.invokeOnCompletion { cause ->
            if (cause != null) {
                probeScope.cancel()
                winner.cancel(cause as? CancellationException)
            }
        }

        try {
            for (ip in candidates) {
                probeScope.launch {
                    try {
                        val resp = pingHost(ip, port, token)
                        if (resp.ok) {
                            winner.complete(ip to resp)
                        } else {
                            errors.add(
                                LanSyncException(
                                    LanSyncErrorKind.HTTP_ERROR,
                                    "Desktop at $ip:$port returned ok=false."
                                )
                            )
                        }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: LanSyncException) {
                        errors.add(e)
                    } catch (e: Exception) {
                        errors.add(mapNetworkException(e, ip, port))
                    } finally {
                        if (remaining.decrementAndGet() == 0 && !winner.isCompleted) {
                            val chosen = errors.firstOrNull { it.kind == LanSyncErrorKind.TIMEOUT }
                                ?: errors.firstOrNull { it.kind == LanSyncErrorKind.CONNECTION_REFUSED }
                                ?: errors.firstOrNull()
                                ?: LanSyncException(
                                    LanSyncErrorKind.TIMEOUT,
                                    buildTimeoutMessage(candidates.first(), port)
                                )
                            winner.completeExceptionally(chosen)
                        }
                    }
                }
            }
            winner.await()
        } finally {
            callerCancelHandle?.dispose()
            probeScope.cancel()
        }
    }

    suspend fun downloadProjectNetviz(
        host: String,
        port: Int,
        token: String,
        destFile: File,
        expectedSha256: String? = null,
        onProgress: (bytesRead: Long, totalBytes: Long) -> Unit = { _, _ -> }
    ): DownloadResult = withContext(Dispatchers.IO) {
        val cleanHost = host.trim()
        val cleanToken = token.trim()
        val url = URL("http://$cleanHost:$port/api/project.netviz")
        destFile.parentFile?.mkdirs()
        val tmpFile = File(destFile.parentFile, "${destFile.name}.download.tmp")
        var conn: HttpURLConnection? = null

        try {
            conn = transport.openConnection(url, cleanHost).apply {
                requestMethod = "GET"
                connectTimeout = TRANSFER_CONNECT_TIMEOUT_MS
                readTimeout = TRANSFER_READ_TIMEOUT_MS
                useCaches = false
                setRequestProperty(HEADER_TOKEN, cleanToken)
            }

            val status = conn.responseCode
            if (status != HttpURLConnection.HTTP_OK) {
                val errBody = readResponseBody(conn, isError = true)
                throw mapHttpError(status, cleanHost, port, errBody)
            }

            val headerSha = conn.getHeaderField(HEADER_SHA256)?.trim()?.lowercase().orEmpty()
            val targetSha = expectedSha256?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
                ?: headerSha.takeIf { it.isNotEmpty() }
            val totalBytes = conn.contentLengthLong

            val digest = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(STREAM_CHUNK_BYTES)
            var bytesRead = 0L

            conn.inputStream.use { input ->
                FileOutputStream(tmpFile).use { fos ->
                    while (true) {
                        coroutineContext.ensureActive()
                        val n = input.read(buffer)
                        if (n <= 0) break
                        fos.write(buffer, 0, n)
                        digest.update(buffer, 0, n)
                        bytesRead += n
                        onProgress(bytesRead, totalBytes)
                    }
                    fos.flush()
                    fos.fd.sync()
                }
            }

            val actualSha = digest.digest().joinToString("") { "%02x".format(it) }
            if (targetSha != null && actualSha != targetSha) {
                tmpFile.delete()
                throw LanSyncException(
                    LanSyncErrorKind.SHA256_MISMATCH,
                    "Transfer corrupted in transit (SHA-256 mismatch) — tap Retry."
                )
            }

            if (!tmpFile.renameTo(destFile)) {
                Files.move(
                    tmpFile.toPath(),
                    destFile.toPath(),
                    StandardCopyOption.REPLACE_EXISTING
                )
            }

            DownloadResult(file = destFile, bytesRead = bytesRead, sha256 = actualSha)
        } catch (e: CancellationException) {
            tmpFile.delete()
            throw e
        } catch (e: LanSyncException) {
            tmpFile.delete()
            throw e
        } catch (e: Exception) {
            tmpFile.delete()
            throw mapNetworkException(e, cleanHost, port)
        } finally {
            conn?.disconnect()
        }
    }

    suspend fun uploadSurveySession(
        host: String,
        port: Int,
        token: String,
        session: SurveySession,
        onProgress: (bytesSent: Long, totalBytes: Long) -> Unit = { _, _ -> }
    ): UploadSurveyResponse = withContext(Dispatchers.IO) {
        val cleanHost = host.trim()
        val cleanToken = token.trim()
        val payloadBytes = session.toJson(pretty = true).toByteArray(Charsets.UTF_8)
        val sha256 = computeSha256Hex(payloadBytes)
        val totalBytes = payloadBytes.size.toLong()
        val url = URL("http://$cleanHost:$port/api/upload-survey")
        var conn: HttpURLConnection? = null

        try {
            conn = transport.openConnection(url, cleanHost).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = TRANSFER_CONNECT_TIMEOUT_MS
                readTimeout = TRANSFER_READ_TIMEOUT_MS
                useCaches = false
                setFixedLengthStreamingMode(payloadBytes.size)
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty(HEADER_TOKEN, cleanToken)
                setRequestProperty(HEADER_SHA256, sha256)
            }

            conn.outputStream.use { out ->
                var offset = 0
                while (offset < payloadBytes.size) {
                    coroutineContext.ensureActive()
                    val chunkLen = minOf(STREAM_CHUNK_BYTES, payloadBytes.size - offset)
                    out.write(payloadBytes, offset, chunkLen)
                    offset += chunkLen
                    onProgress(offset.toLong(), totalBytes)
                }
                out.flush()
            }

            val status = conn.responseCode
            val bodyText = readResponseBody(conn, status >= 400)
            if (status != HttpURLConnection.HTTP_OK) {
                throw mapHttpError(status, cleanHost, port, bodyText)
            }
            netVizJson.decodeFromString<UploadSurveyResponse>(bodyText)
        } catch (e: CancellationException) {
            throw e
        } catch (e: LanSyncException) {
            throw e
        } catch (e: Exception) {
            throw mapNetworkException(e, cleanHost, port)
        } finally {
            conn?.disconnect()
        }
    }

    suspend fun discoverDesktopOnLan(
        udpPort: Int = DEFAULT_UDP_DISCOVERY_PORT,
        timeoutMs: Int = PING_TIMEOUT_MS
    ): List<UdpDiscoveryReply> = withContext(Dispatchers.IO) {
        val found = LinkedHashMap<String, UdpDiscoveryReply>()
        val probeBytes = UDP_DISCOVER_MAGIC.toByteArray(Charsets.US_ASCII)

        DatagramSocket().use { socket ->
            socket.broadcast = true
            socket.soTimeout = 400

            val targets = collectBroadcastAddresses()
            for (addr in targets) {
                runCatching {
                    socket.send(DatagramPacket(probeBytes, probeBytes.size, addr, udpPort))
                }
            }

            val deadline = System.currentTimeMillis() + timeoutMs
            val recvBuf = ByteArray(4096)
            while (System.currentTimeMillis() < deadline) {
                coroutineContext.ensureActive()
                val packet = DatagramPacket(recvBuf, recvBuf.size)
                try {
                    socket.receive(packet)
                    val jsonText = String(packet.data, 0, packet.length, Charsets.UTF_8).trim()
                    val reply = runCatching {
                        netVizJson.decodeFromString<UdpDiscoveryReply>(jsonText)
                    }.getOrNull()
                    if (reply != null && reply.app == "NetVizStudio") {
                        val senderIp = packet.address?.hostAddress
                        val normalizedIps = if (senderIp != null && senderIp !in reply.ips) {
                            listOf(senderIp) + reply.ips
                        } else {
                            reply.ips
                        }
                        val key = "${reply.host}:${reply.port}:${reply.token}"
                        found[key] = reply.copy(ips = normalizedIps)
                    }
                } catch (_: SocketTimeoutException) {
                    // Continue polling until deadline
                }
            }
        }
        found.values.toList()
    }

    private fun collectBroadcastAddresses(): List<InetAddress> {
        val addrs = LinkedHashSet<InetAddress>()
        runCatching { addrs.add(InetAddress.getByName("255.255.255.255")) }
        runCatching {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val iface = interfaces.nextElement()
                if (!iface.isUp || iface.isLoopback) continue
                for (ifAddr in iface.interfaceAddresses) {
                    val broadcast = ifAddr.broadcast
                    if (broadcast is Inet4Address) {
                        addrs.add(broadcast)
                    }
                }
            }
        }
        return addrs.toList()
    }

    private fun readResponseBody(conn: HttpURLConnection, isError: Boolean): String {
        val stream: InputStream? = if (isError) conn.errorStream else conn.inputStream
        if (stream == null) return ""
        return stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
    }

    private fun mapHttpError(
        status: Int,
        host: String,
        port: Int,
        bodyText: String
    ): LanSyncException = when (status) {
        HttpURLConnection.HTTP_UNAUTHORIZED -> LanSyncException(
            kind = LanSyncErrorKind.UNAUTHORIZED,
            message = "Pairing token mismatch — rescan QR code or update token.",
            httpStatus = status
        )
        HttpURLConnection.HTTP_BAD_REQUEST -> {
            if (bodyText.contains("SHA-256", ignoreCase = true)) {
                LanSyncException(
                    kind = LanSyncErrorKind.SHA256_MISMATCH,
                    message = "Transfer corrupted in transit — tap Retry.",
                    httpStatus = status
                )
            } else {
                LanSyncException(
                    kind = LanSyncErrorKind.HTTP_ERROR,
                    message = "Desktop rejected request (HTTP 400): $bodyText",
                    httpStatus = status
                )
            }
        }
        else -> LanSyncException(
            kind = LanSyncErrorKind.HTTP_ERROR,
            message = "HTTP $status from $host:$port${if (bodyText.isNotBlank()) ": $bodyText" else ""}",
            httpStatus = status
        )
    }

    private fun mapNetworkException(
        exc: Exception,
        host: String,
        port: Int
    ): LanSyncException = when (exc) {
        is SocketTimeoutException -> LanSyncException(
            kind = LanSyncErrorKind.TIMEOUT,
            message = buildTimeoutMessage(host, port),
            cause = exc
        )
        is ConnectException -> LanSyncException(
            kind = LanSyncErrorKind.CONNECTION_REFUSED,
            message = "Connection refused at $host:$port — verify Desktop LAN Sync dialog is open and port matches.",
            cause = exc
        )
        else -> LanSyncException(
            kind = LanSyncErrorKind.NETWORK_ERROR,
            message = "Network error communicating with $host:$port: ${exc.message ?: exc::class.java.simpleName}",
            cause = exc
        )
    }

    private fun buildTimeoutMessage(host: String, port: Int): String =
        "Connection timed out to $host:$port. Check: (1) Same Wi-Fi network? " +
            "(2) Click 'Allow Port in Windows Firewall' on Desktop. " +
            "(3) Corporate Wi-Fi may block device-to-device traffic — try USB Sync or Save File (SAF)."

    companion object {
        const val PING_TIMEOUT_MS = 2500
        const val TRANSFER_CONNECT_TIMEOUT_MS = 5000
        const val TRANSFER_READ_TIMEOUT_MS = 60000
        const val STREAM_CHUNK_BYTES = 65536
        const val DEFAULT_UDP_DISCOVERY_PORT = 8766
        const val UDP_DISCOVER_MAGIC = "NETVIZ_DISCOVER_V1"
        const val HEADER_TOKEN = "X-NetViz-Token"
        const val HEADER_SHA256 = "X-NetViz-SHA256"

        fun computeSha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256")
                .digest(bytes)
                .joinToString("") { "%02x".format(it) }

        fun isPrivateRfc1918Ipv4(host: String): Boolean {
            val parts = host.trim().split('.')
            if (parts.size != 4) return false
            val octets = parts.map { it.toIntOrNull() ?: return false }
            if (octets.any { it !in 0..255 }) return false
            val a = octets[0]
            val b = octets[1]
            return a == 10 ||
                (a == 172 && b in 16..31) ||
                (a == 192 && b == 168)
        }
    }
}
