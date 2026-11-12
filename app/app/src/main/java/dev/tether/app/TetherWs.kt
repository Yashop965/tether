package dev.tether.app

import android.os.Handler
import android.os.Looper
import okio.ByteString
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * OkHttp WebSocket client. JSON events out; JSON events in.
 * Audio: binary frames, then {"t":"audio_done"}.
 */
class TetherWs(private val host: String, private val port: Int, private val token: String, private val remote: String? = null) {

    /** True when the link rides the cloud tunnel (wss://) — higher RTT, so
     *  UI code can coalesce harder than on a LAN link. */
    val isRemote: Boolean get() = !remote.isNullOrBlank()

    interface Events {
        fun onConnected()
        fun onDisconnected()
        fun onSttPending()
        fun onStt(text: String, sent: Boolean)
        fun onSttError(msg: String)
        fun onPaused(paused: Boolean)
        fun onConnectError(msg: String) {}
    }

    private val client = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var events: Events? = null
    // ---- auto-reconnect: transient drops (Wi-Fi switch, laptop sleep) heal
    //      themselves without user re-LINKing. Backoff 2s -> 30s cap.
    private var shouldReconnect = true
    private var reconnectScheduled = false
    private var backoffMs = 2000L

    fun setEvents(e: Events) { events = e }

    /** Auto-reconnect on. When false, failures stay disconnected. */
    fun setAutoReconnect(on: Boolean) { shouldReconnect = on }

    fun connect() {
        ws?.close(1000, "reconnect")
        // remote cloud-tunnel URL (wss://…) wins over the local host:port
        val base = if (!remote.isNullOrBlank()) remote else "ws://$host:$port"
        val url = "$base/?token=$token"
        val req: Request
        try {
            req = Request.Builder().url(url).build()
        } catch (e: Exception) {
            // bad host/port typing must never crash the app
            main.post { events?.onConnectError("bad host: $host") }
            return
        }
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoffMs = 2000L
                main.post { events?.onConnected() }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                main.post {
                    events?.onDisconnected()
                    scheduleReconnect()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                main.post {
                    events?.onDisconnected()
                    if (code != 1000 || shouldReconnect) scheduleReconnect()
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    when (org.json.JSONObject(text).optString("t")) {
                        "stt" -> main.post {
                            val o = org.json.JSONObject(text)
                            events?.onStt(o.optString("text"), o.optBoolean("sent"))
                        }
                        "stt_pending" -> main.post { events?.onSttPending() }
                        "stt_error" -> main.post {
                            events?.onSttError(org.json.JSONObject(text).optString("error"))
                        }
                        "state" -> main.post {
                            events?.onPaused(org.json.JSONObject(text).optBoolean("paused"))
                        }
                    }
                } catch (_: Exception) { }
            }
        })
    }

    fun send(json: String) { ws?.send(json) }

    private fun scheduleReconnect() {
        if (!shouldReconnect || reconnectScheduled) return
        reconnectScheduled = true
        main.postDelayed({
            reconnectScheduled = false
            if (!shouldReconnect) return@postDelayed
            connect()
            backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
        }, backoffMs)
    }

    /** Chunked binary audio send. Returns bytes actually queued (0 = failed). */
    fun sendAudio(bytes: ByteArray): Int {
        val chunk = 64 * 1024
        var off = 0
        var sent = 0
        while (off < bytes.size) {
            val n = minOf(chunk, bytes.size - off)
            val buf = okio.Buffer()
            buf.write(bytes, off, n)
            val ok = ws?.send(buf.readByteString()) ?: false
            if (!ok) break
            off += n
            sent += n
        }
        return sent
    }

    fun close() {
        // deliberate close (new ws instance takes over / screen disposed) -
        // stop the auto-reconnect loop so stale instances don't keep dialing
        shouldReconnect = false
        ws?.close(1000, "bye")
        ws = null
    }

    companion object {
        const val HELLO = """{"t":"hello"}"""
        const val PAUSE = """{"t":"pause"}"""
        const val RESUME = """{"t":"resume"}"""
        const val AUDIO_DONE = """{"t":"audio_done"}"""
    }
}
