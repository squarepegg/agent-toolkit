package com.example.offers.api.clients

// Test support for the wire contract tests (from eval-tiering, 2026-10-14).
// A raw-socket HTTP/1.1 stub that can do what an embedded server cannot: drop a
// connection after reading the request, or stall past the client's timeout.
// No new dependencies (java.net only).
//
// Framing is byte-based: headers are read up to CRLF CRLF, and the body is read as exactly
// Content-Length bytes before the request is recorded or answered. close() closes every
// accepted connection as well as the listening socket, so no handler thread outlives a test.

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

sealed interface StubAction {
    /** Read the request, then close the socket without a response. */
    data object Drop : StubAction

    /** Read the request, then wait [millis] before answering 200 with [body]. */
    data class Stall(
        val millis: Long,
        val body: String = "{\"entries\":[]}",
    ) : StubAction

    data class Respond(
        val status: Int,
        val body: String = "",
        val contentType: String = "application/json",
    ) : StubAction
}

data class StubRequest(
    val requestLine: String,
    val headers: Map<String, String>,
    val body: String,
)

class RawHttpStub(
    vararg script: StubAction,
) : AutoCloseable {
    private val actions = ConcurrentLinkedQueue(script.toList())
    private val server = ServerSocket(0)
    private val open = ConcurrentHashMap.newKeySet<Socket>()

    @Volatile private var closed = false
    val requests = CopyOnWriteArrayList<StubRequest>()
    val baseUrl: String get() = "http://localhost:${server.localPort}"

    /** Action used once the script is exhausted. */
    var fallback: StubAction = StubAction.Respond(200, "{\"entries\":[]}")

    init {
        thread(isDaemon = true, name = "raw-http-stub") {
            while (!closed) {
                val socket = runCatching { server.accept() }.getOrNull() ?: break
                open += socket
                if (closed) {
                    // close() ran between accept and registration: it will not see this socket
                    socket.close()
                    open -= socket
                    break
                }
                thread(isDaemon = true, name = "raw-http-stub-conn") { handle(socket) }
            }
        }
    }

    fun enqueue(vararg more: StubAction) = actions.addAll(more)

    private fun handle(socket: Socket) {
        try {
            socket.use { s ->
                val input = s.getInputStream().buffered()
                while (!closed) {
                    val head = readHead(input) ?: return
                    val lines = head.split("\r\n").filter { it.isNotEmpty() }
                    if (lines.isEmpty()) continue
                    val headers =
                        lines.drop(1).associate {
                            it.substringBefore(":").trim().lowercase() to it.substringAfter(":").trim()
                        }
                    val len = headers["content-length"]?.toIntOrNull() ?: 0
                    val bodyBytes = input.readNBytes(len)
                    if (bodyBytes.size < len) return // peer closed mid-body
                    requests += StubRequest(lines.first(), headers, bodyBytes.toString(Charsets.UTF_8))
                    when (val a = actions.poll() ?: fallback) {
                        StubAction.Drop -> {
                            return
                        }

                        is StubAction.Stall -> {
                            Thread.sleep(a.millis)
                            write(s, 200, a.body, "application/json")
                        }

                        is StubAction.Respond -> {
                            write(s, a.status, a.body, a.contentType)
                        }
                    }
                }
            }
        } catch (_: SocketException) {
            // peer reset, or close() shut the socket while we were blocked reading
        } finally {
            open -= socket
        }
    }

    /** Header block up to (not including) CRLF CRLF, decoded as ISO-8859-1; null on EOF. */
    private fun readHead(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        var matched = 0
        val terminator = byteArrayOf('\r'.code.toByte(), '\n'.code.toByte(), '\r'.code.toByte(), '\n'.code.toByte())
        while (matched < terminator.size) {
            val b = input.read()
            if (b == -1) return null
            buf.write(b)
            matched =
                if (b.toByte() == terminator[matched]) {
                    matched + 1
                } else if (b.toByte() == terminator[0]) {
                    1
                } else {
                    0
                }
        }
        val bytes = buf.toByteArray()
        return String(bytes, 0, bytes.size - terminator.size, Charsets.ISO_8859_1)
    }

    private fun write(
        s: Socket,
        status: Int,
        body: String,
        type: String,
    ) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 $status X\r\nContent-Type: $type\r\nContent-Length: ${bytes.size}\r\n\r\n"
        s.getOutputStream().apply {
            write(head.toByteArray(Charsets.ISO_8859_1))
            write(bytes)
            flush()
        }
    }

    override fun close() {
        closed = true
        server.close()
        open.forEach { runCatching { it.close() } }
        open.clear()
    }
}
