package org.netviz.survey

import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.netviz.survey.data.LanHttpTransport
import org.netviz.survey.data.LanSyncClient
import org.netviz.survey.data.LanSyncErrorKind
import org.netviz.survey.data.LanSyncException
import org.netviz.survey.model.Point2D
import org.netviz.survey.model.PositionSource
import org.netviz.survey.model.SurveySample
import org.netviz.survey.model.SurveySession
import org.netviz.survey.model.WifiReading
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URL
import java.nio.file.Files
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class LanSyncProtocolTest {
    private class MiniHttpServer(
        private val handler: (method: String, target: String, headers: Map<String, String>, body: ByteArray) -> HttpReply
    ) {
        data class HttpReply(
            val status: Int,
            val statusText: String,
            val headers: Map<String, String> = emptyMap(),
            val body: ByteArray = ByteArray(0)
        )

        private val serverSocket = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        private val executor: ExecutorService = Executors.newCachedThreadPool()
        private val running = AtomicBoolean(true)

        val port: Int get() = serverSocket.localPort

        fun start() {
            executor.submit {
                while (running.get()) {
                    val sock = try {
                        serverSocket.accept()
                    } catch (_: Exception) {
                        break
                    }
                    executor.submit { handleClient(sock) }
                }
            }
        }

        fun stop() {
            running.set(false)
            runCatching { serverSocket.close() }
            executor.shutdownNow()
        }

        private fun handleClient(sock: Socket) {
            sock.use { s ->
                val input = BufferedInputStream(s.getInputStream())
                val requestLine = readLineCrLf(input) ?: return
                val parts = requestLine.split(' ')
                if (parts.size < 2) return
                val method = parts[0].trim().uppercase(Locale.ROOT)
                val target = parts[1].trim()

                val headers = LinkedHashMap<String, String>()
                while (true) {
                    val line = readLineCrLf(input) ?: break
                    if (line.isEmpty()) break
                    val idx = line.indexOf(':')
                    if (idx > 0) {
                        headers[line.substring(0, idx).trim().lowercase(Locale.ROOT)] =
                            line.substring(idx + 1).trim()
                    }
                }

                val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                val body = if (contentLength > 0) readExactBytes(input, contentLength) else ByteArray(0)
                val reply = handler(method, target, headers, body)

                val out = s.getOutputStream()
                val headerText = buildString {
                    append("HTTP/1.1 ${reply.status} ${reply.statusText}\r\n")
                    append("Content-Length: ${reply.body.size}\r\n")
                    append("Connection: close\r\n")
                    reply.headers.forEach { (k, v) ->
                        append("$k: $v\r\n")
                    }
                    append("\r\n")
                }
                out.write(headerText.toByteArray(Charsets.US_ASCII))
                if (reply.body.isNotEmpty()) {
                    out.write(reply.body)
                }
                out.flush()
            }
        }

        private fun readLineCrLf(input: InputStream): String? {
            val buf = ByteArrayOutputStream()
            while (true) {
                val b = input.read()
                if (b == -1) {
                    return if (buf.size() == 0) null else buf.toString(Charsets.US_ASCII.name())
                }
                if (b == '\n'.code) {
                    val raw = buf.toByteArray()
                    val len = if (raw.isNotEmpty() && raw.last() == '\r'.code.toByte()) raw.size - 1 else raw.size
                    return String(raw, 0, len, Charsets.US_ASCII)
                }
                buf.write(b)
            }
        }

        private fun readExactBytes(input: InputStream, count: Int): ByteArray {
            val out = ByteArray(count)
            var offset = 0
            while (offset < count) {
                val n = input.read(out, offset, count - offset)
                if (n <= 0) break
                offset += n
            }
            return if (offset == count) out else out.copyOf(offset)
        }
    }

    private lateinit var server: MiniHttpServer
    private var serverPort: Int = 0
    private val token = "A7K9P2"
    private val bundleBytes = "PK\u0003\u0004MOCK_NETVIZ_ZIP_PAYLOAD_1234567890".toByteArray(Charsets.UTF_8)
    private val bundleSha256 = LanSyncClient.computeSha256Hex(bundleBytes)
    @Volatile
    private var forceCorruptHeaderSha = false
    private val uploadedBodies = java.util.Collections.synchronizedList(mutableListOf<String>())

    @Before
    fun setUp() {
        server = MiniHttpServer { _, target, headers, reqBytes ->
            val path = target.substringBefore('?')
            val query = target.substringAfter('?', "")
            val provided = headers["x-netviz-token"]?.trim()
                ?: query.split('&')
                    .firstOrNull { it.startsWith("token=") }
                    ?.substringAfter("token=")
                    .orEmpty()

            when (path) {
                "/api/ping" -> {
                    val tokenField = if (provided.isNotEmpty()) {
                        ""","token_valid":${provided == token}"""
                    } else {
                        ""
                    }
                    val json = """{"ok":true,"app":"NetVizStudio","schema_version":"2.1.0","project_name":"Mock Campus","bundle_bytes":${bundleBytes.size},"bundle_sha256":"$bundleSha256","token_required":true$tokenField}"""
                    MiniHttpServer.HttpReply(
                        status = 200,
                        statusText = "OK",
                        headers = mapOf("Content-Type" to "application/json; charset=utf-8"),
                        body = json.toByteArray(Charsets.UTF_8)
                    )
                }
                "/api/project.netviz" -> {
                    if (provided != token) {
                        MiniHttpServer.HttpReply(
                            status = 401,
                            statusText = "Unauthorized",
                            body = """{"ok":false,"error":"Invalid pairing token"}""".toByteArray(Charsets.UTF_8)
                        )
                    } else {
                        val shaHeader = if (forceCorruptHeaderSha) "0".repeat(64) else bundleSha256
                        MiniHttpServer.HttpReply(
                            status = 200,
                            statusText = "OK",
                            headers = mapOf(
                                "Content-Type" to "application/octet-stream",
                                "X-NetViz-SHA256" to shaHeader
                            ),
                            body = bundleBytes
                        )
                    }
                }
                "/api/upload-survey" -> {
                    if (provided != token) {
                        MiniHttpServer.HttpReply(
                            status = 401,
                            statusText = "Unauthorized",
                            body = """{"ok":false,"error":"Invalid pairing token"}""".toByteArray(Charsets.UTF_8)
                        )
                    } else {
                        val expectedSha = headers["x-netviz-sha256"]?.trim()?.lowercase().orEmpty()
                        val actualSha = LanSyncClient.computeSha256Hex(reqBytes)
                        if (expectedSha.isNotEmpty() && expectedSha != actualSha) {
                            MiniHttpServer.HttpReply(
                                status = 400,
                                statusText = "Bad Request",
                                body = """{"ok":false,"error":"SHA-256 checksum mismatch"}""".toByteArray(Charsets.UTF_8)
                            )
                        } else {
                            val jsonText = reqBytes.toString(Charsets.UTF_8)
                            uploadedBodies.add(jsonText)
                            val parsed = SurveySession.fromJson(jsonText)
                            val ok = """{"ok":true,"session_id":"${parsed.id}","sample_count":${parsed.samples.size}}"""
                            MiniHttpServer.HttpReply(
                                status = 200,
                                statusText = "OK",
                                headers = mapOf("Content-Type" to "application/json; charset=utf-8"),
                                body = ok.toByteArray(Charsets.UTF_8)
                            )
                        }
                    }
                }
                else -> MiniHttpServer.HttpReply(status = 404, statusText = "Not Found")
            }
        }
        server.start()
        serverPort = server.port
    }

    @After
    fun tearDown() {
        server.stop()
    }

    @Test
    fun parseQrPayloadSupportsMultiIpAndRejectsInvalid() {
        val client = LanSyncClient()
        val parsed = client.parseQrPayload(
            """{"v":2,"ips":["192.168.1.42","192.168.1.43"],"port":8765,"token":"A7K9P2","project":"Campus"}"""
        )
        assertEquals(listOf("192.168.1.42", "192.168.1.43"), parsed.resolvedIps())
        assertEquals(8765, parsed.port)
        assertEquals("A7K9P2", parsed.token)

        val err = runCatching { client.parseQrPayload("not-json") }.exceptionOrNull() as LanSyncException
        assertEquals(LanSyncErrorKind.INVALID_PAYLOAD, err.kind)
    }

    @Test
    fun pingAndMultiIpFailoverSelectFirstReachableHost() = runTest {
        val closedPort = ServerSocket(0).use { it.localPort }
        val transport = object : LanHttpTransport {
            override fun openConnection(url: URL, host: String): HttpURLConnection {
                return when (host) {
                    "192.168.1.99" -> throw SocketTimeoutException("Simulated timeout")
                    "192.168.1.98" -> URL("http://127.0.0.1:$closedPort${url.file}").openConnection() as HttpURLConnection
                    "192.168.1.42" -> URL("http://127.0.0.1:$serverPort${url.file}").openConnection() as HttpURLConnection
                    else -> url.openConnection() as HttpURLConnection
                }
            }
        }
        val client = LanSyncClient(transport)

        val pingNoToken = client.pingHost("127.0.0.1", serverPort)
        assertTrue(pingNoToken.ok)
        assertEquals("Mock Campus", pingNoToken.projectName)
        assertEquals(null, pingNoToken.tokenValid)

        val pingBadToken = client.pingHost("127.0.0.1", serverPort, "WRONG1")
        assertEquals(false, pingBadToken.tokenValid)

        val (winningIp, winPing) = client.probeFirstReachableHost(
            ips = listOf("192.168.1.99", "192.168.1.98", "192.168.1.42"),
            port = serverPort,
            token = token
        )
        assertEquals("192.168.1.42", winningIp)
        assertEquals(true, winPing.tokenValid)
        assertEquals(bundleSha256, winPing.bundleSha256)
    }

    @Test
    fun downloadRejectsBadTokenAndShaMismatchAndSucceedsOnValidTransfer() = runTest {
        val client = LanSyncClient()
        val tempDir = Files.createTempDirectory("netviz_dl_test").toFile()
        try {
            val badAuthDest = File(tempDir, "unauth.netviz")
            val authErr = runCatching {
                client.downloadProjectNetviz("127.0.0.1", serverPort, "WRONG1", badAuthDest)
            }.exceptionOrNull() as LanSyncException
            assertEquals(LanSyncErrorKind.UNAUTHORIZED, authErr.kind)
            assertFalse(badAuthDest.exists())

            forceCorruptHeaderSha = true
            val badShaDest = File(tempDir, "corrupt.netviz")
            val shaErr = runCatching {
                client.downloadProjectNetviz("127.0.0.1", serverPort, token, badShaDest)
            }.exceptionOrNull() as LanSyncException
            assertEquals(LanSyncErrorKind.SHA256_MISMATCH, shaErr.kind)
            assertFalse(badShaDest.exists())

            forceCorruptHeaderSha = false
            val validDest = File(tempDir, "valid.netviz")
            var lastProgressBytes = 0L
            val dl = client.downloadProjectNetviz(
                host = "127.0.0.1",
                port = serverPort,
                token = token,
                destFile = validDest,
                expectedSha256 = bundleSha256
            ) { read, _ ->
                lastProgressBytes = read
            }
            assertTrue(validDest.isFile)
            assertEquals(bundleBytes.size.toLong(), dl.bytesRead)
            assertEquals(bundleBytes.size.toLong(), lastProgressBytes)
            assertEquals(bundleSha256, dl.sha256)
            assertArrayEquals(bundleBytes, validDest.readBytes())
        } finally {
            tempDir.deleteRecursively()
        }
    }

    @Test
    fun uploadSurveyVerifiesTokenShaAndRoundTripsSession() = runTest {
        val client = LanSyncClient()
        val session = SurveySession(
            name = "Protocol Test Walk",
            createdUtc = "2026-11-26T00:00:00Z",
            deviceModel = "Test Device",
            samples = listOf(
                SurveySample(
                    timestampUtc = "2026-11-26T00:00:05Z",
                    buildingId = "bldg-1",
                    floorId = "floor-1",
                    posM = Point2D(4.5, 6.25),
                    source = PositionSource.GPS_CALIBRATED,
                    gpsLat = 52.52,
                    gpsLon = 13.405,
                    gpsAccuracyM = 2.8,
                    calibrationOffsetM = Point2D(0.4, -0.2),
                    readings = listOf(
                        WifiReading("Corp-WiFi", "00:11:22:33:44:55", -52, 5180)
                    )
                )
            )
        )

        val authErr = runCatching {
            client.uploadSurveySession("127.0.0.1", serverPort, "BADTOK", session)
        }.exceptionOrNull() as LanSyncException
        assertEquals(LanSyncErrorKind.UNAUTHORIZED, authErr.kind)

        var sentFinal = 0L
        val resp = client.uploadSurveySession("127.0.0.1", serverPort, token, session) { sent, _ ->
            sentFinal = sent
        }
        assertTrue(resp.ok)
        assertEquals(session.id, resp.sessionId)
        assertEquals(1, resp.sampleCount)
        assertTrue(sentFinal > 0L)
        assertEquals(1, uploadedBodies.size)
        val decoded = SurveySession.fromJson(uploadedBodies.first())
        assertEquals(session.name, decoded.name)
        assertEquals(PositionSource.GPS_CALIBRATED, decoded.samples.first().source)
    }

    @Test
    fun actionableDiagnosticsForRefusedAndTimeout() = runTest {
        val closedPort = ServerSocket(0).use { it.localPort }
        val client = LanSyncClient()
        val refused = runCatching {
            client.pingHost("127.0.0.1", closedPort)
        }.exceptionOrNull() as LanSyncException
        assertEquals(LanSyncErrorKind.CONNECTION_REFUSED, refused.kind)
        assertTrue(refused.message.contains("Connection refused"))

        val timeoutClient = LanSyncClient(
            object : LanHttpTransport {
                override fun openConnection(url: URL, host: String): HttpURLConnection {
                    throw SocketTimeoutException("connect timed out")
                }
            }
        )
        val timedOut = runCatching {
            timeoutClient.pingHost("192.168.1.42", 8765)
        }.exceptionOrNull() as LanSyncException
        assertEquals(LanSyncErrorKind.TIMEOUT, timedOut.kind)
        assertTrue(timedOut.message.contains("Allow Port in Windows Firewall"))
        assertTrue(timedOut.message.contains("USB Sync"))
    }
}
