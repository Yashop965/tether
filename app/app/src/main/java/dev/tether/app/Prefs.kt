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

    fun save(c: Context, host: String, port: String, token: String) {
        sp(c).edit()
            .putString("host", host.trim())
            .putString("port", port.trim())
            .putString("token", token.trim())
            .apply()
    }

    /** Remember the hotspot credentials from a pair QR (auto-join on resume). */
    fun saveHotspot(c: Context, ssid: String, psk: String) {
        sp(c).edit()
            .putString("ssid", ssid.trim())
            .putString("psk", psk.trim())
            .apply()
    }
}
