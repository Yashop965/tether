package dev.tether.app

import android.content.Context

/** Remembered pairing settings. */
object Prefs {
    private const val FILE = "tether"

    private fun sp(c: Context) = c.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun host(c: Context): String = sp(c).getString("host", "") ?: ""
    fun port(c: Context): String = sp(c).getString("port", "8377") ?: "8377"
    fun token(c: Context): String = sp(c).getString("token", "") ?: ""
    fun ssid(c: Context): String = sp(c).getString("ssid", "") ?: ""
    fun psk(c: Context): String = sp(c).getString("psk", "") ?: ""
    /** Remote cloud-tunnel WS URL (`r=` in a pair QR); empty = local mode. */
    fun wss(c: Context): String = sp(c).getString("wss", "") ?: ""
    /** Cloud update-server base (`u=` in a pair QR); empty = host:8400. */
    fun upd(c: Context): String = sp(c).getString("upd", "") ?: ""

    fun save(c: Context, host: String, port: String, token: String) {
        sp(c).edit()
            .putString("host", host.trim())
            .putString("port", port.trim())
            .putString("token", token.trim())
            .apply()
    }

    /** Set (or clear, with empty string) the remote tunnel URL. */
    fun saveWss(c: Context, url: String) {
        sp(c).edit().putString("wss", url.trim()).apply()
    }

    /** Set (or clear, with empty string) the cloud update-server base URL. */
    fun saveUpd(c: Context, url: String) {
        sp(c).edit().putString("upd", url.trim()).apply()
    }

    /** Remember the hotspot credentials from a pair QR (auto-join on resume). */
    fun saveHotspot(c: Context, ssid: String, psk: String) {
        sp(c).edit()
            .putString("ssid", ssid.trim())
            .putString("psk", psk.trim())
            .apply()
    }
}
