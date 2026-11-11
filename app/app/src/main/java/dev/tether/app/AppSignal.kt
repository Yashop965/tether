package dev.tether.app

import androidx.compose.runtime.MutableIntState
import androidx.compose.runtime.mutableIntStateOf

/**
 * Cross-component signals that don't need lifecycle plumbing:
 *  - deepLink: a tether:// URI arriving from outside (QR app, shared link)
 *  - resumedTick: bumped on activity resume so composition re-checks pending signals
 */
object AppSignal {
    @Volatile
    var deepLink: String? = null

    val resumedTick: MutableIntState = mutableIntStateOf(0)

    /** Parse `tether://pair?h=<host>&p=<port>&t=<token>` (also accepts full
     *  host/port/token keys). Returns Triple or null if malformed. */
    fun parsePairUri(uri: String): Triple<String, String, String>? {
        val q = uri.substringAfter('?', "")
        if (q.isEmpty()) return null
        val params = q.split('&').associate {
            it.substringBefore('=') to it.substringAfter('=', "")
        }
        val host = params["h"] ?: params["host"] ?: return null
        val port = params["p"] ?: params["port"] ?: "8377"
        val token = params["t"] ?: params["token"] ?: return null
        return Triple(host, port, token)
    }

    /** Full pair spec incl. optional hotspot credentials (`n=` ssid, `k=` psk)
     *  and optional remote cloud-tunnel URL (`r=` wss://…). A pair is valid if
     *  it carries a host (LAN/hotspot) or a remote tunnel URL. */
    data class PairSpec(
        val host: String,
        val port: String,
        val token: String,
        val ssid: String? = null,
        val psk: String? = null,
        val wss: String? = null,
        val upd: String? = null,
    )

    fun parsePairSpec(uri: String): PairSpec? {
        val q = uri.substringAfter('?', "")
        if (q.isEmpty()) return null
        val p = q.split('&').associate { it.substringBefore('=') to it.substringAfter('=', "") }
        val host = p["h"] ?: p["host"] ?: ""
        val wss = p["r"]
        if (host.isEmpty() && wss == null) return null
        val token = p["t"] ?: p["token"] ?: return null
        return PairSpec(
            host = host,
            port = p["p"] ?: p["port"] ?: "8377",
            token = token,
            ssid = p["n"],
            psk = p["k"],
            wss = wss,
            upd = p["u"],
        )
    }
}
