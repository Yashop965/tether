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
class TetherWs(private val host: String, private val port: Int, private val token: String) {

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

    fun setEvents(e: Events) { events = e }

    fun connect() {
        ws?.close(1000, "reconnect")
        val url = "ws://$host:$port/?token=$token"
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
                main.post { events?.onConnected() }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                main.post { events?.onDisconnected() }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                main.post { events?.onDisconnected() }
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

    fun sendAudio(bytes: ByteArray) {
        val chunk = 64 * 1024
        var off = 0
        while (off < bytes.size) {
            val n = minOf(chunk, bytes.size - off)
            val buf = okio.Buffer()
            buf.write(bytes, off, n)
            ws?.send(buf.readByteString()) ?: break
            off += n
        }
    }

    fun close() {
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
