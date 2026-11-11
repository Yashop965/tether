package dev.tether.app

import android.content.Context
import android.net.Network
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.os.Handler
import android.os.Looper

/**
 * Auto-joins the laptop's Wi-Fi hotspot when a pair QR carries `n=<ssid>&k=<psk>`.
 * Android 12+ only (createNetwork/requestNetwork APIs); on older devices this
 * returns [Result.Unsupported] and the user joins the Wi-Fi manually — the QR
 * still auto-links the bridge by IP.
 *
 * Caller must hold NEARBY_WIFI_DEVICES (runtime permission); the system shows
 * a one-time "app is about to connect to …" notice on first use.
 */
object WifiPair {
    private val main = Handler(Looper.getMainLooper())

    @Volatile private var deadlineRef: Runnable? = null
    @Volatile private var requestRef: NetworkRequest? = null
    @Volatile private var cbRef: android.net.ConnectivityManager.NetworkCallback? = null
    @Volatile private var ctxRef: Context? = null

    sealed class Result {
        data class Joined(val ssid: String) : Result()
        data class Unsupported(val reason: String) : Result()
    }

    fun join(
        ctx: Context,
        ssid: String,
        psk: String,
        onProgress: (String) -> Unit,
        onDone: (Result) -> Unit,
    ) {
        if (Build.VERSION.SDK_INT < 31) {
            main.post { onDone(Result.Unsupported("auto-join needs Android 12+ — join '$ssid' manually; the app still links by IP")) }
            return
        }
        stop()
        ctxRef = ctx
        main.post { onProgress("connecting to '$ssid'…") }

        val spec = WifiNetworkSpecifier.Builder()
            .setSsid(ssid)
            .setWpa2Passphrase(psk)
            .build()
        val request = NetworkRequest.Builder().setNetworkSpecifier(spec).build()
        requestRef = request

        val cb = object : android.net.ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                val deadline = deadlineRef
                if (deadline != null) main.removeCallbacks(deadline)
                deadlineRef = null
                main.post { onDone(Result.Joined(ssid)) }
            }

            override fun onUnavailable() {
                main.post { onProgress("join finished — connecting to bridge…") }
            }
        }

        val conn = ctx.getSystemService(android.net.ConnectivityManager::class.java)!!
        cbRef = cb
        conn.requestNetwork(request, cb)

        deadlineRef = Runnable {
            main.post { onProgress("still connecting to '$ssid'…") }
        }
        main.postDelayed(deadlineRef!!, 15_000)
    }

    fun stop() {
        deadlineRef?.let { main.removeCallbacks(it) }
        deadlineRef = null
        val cb = cbRef
        val c = ctxRef
        if (cb != null && c != null && Build.VERSION.SDK_INT >= 31) {
            try {
                c.getSystemService(android.net.ConnectivityManager::class.java)!!.unregisterNetworkCallback(cb)
            } catch (_: Exception) {
            }
        }
        cbRef = null
        requestRef = null
    }
}
