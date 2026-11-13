package dev.tether.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.core.content.FileProvider
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * In-app self-update. The bridge's host also serves the update server
 * (port 8400): tether.json describes the newest build, tether.apk is the
 * artifact. Both ride the same transport as the bridge (LAN/hotspot/tailnet),
 * so an update never needs a second network path.
 *
 * Discovery relay: the laptop's tunnel guard publishes the LIVE cloud
 * update/bridge URLs to a public Gist on every tunnel (re)start. Polling it
 * lets a phone CHECK for updates — and later discover the remote bridge —
 * with NO prior pairing and no LAN access. The URLs it carries are ephemeral
 * quick-tunnel links, so nothing sensitive is exposed.
 */
object AutoUpdater {
    const val UPDATE_PORT = 8400
    /** Stable relay URL baked into every build (published by tools/tunnel_guard.py). */
    const val RELAY_RAW =
        "https://gist.githubusercontent.com/Yashop965/98c806c159f0a914987adcfdb467098e/raw"

    data class Release(
        val versionCode: Int,
        val versionName: String,
        val apkUrl: String,
        val sha256: String,
        val size: Long,
    )

    /** Live cloud endpoints fetched from the relay (null fields = unavailable). */
    data class RelayUrls(val wss: String?, val upd: String?)

    val main = Handler(Looper.getMainLooper())
    @Volatile var progress = -1            // -1 idle, 0..100 downloading
    @Volatile var state = "idle"           // idle | checking | downloading | ready | error
    @Volatile var message = ""
    @Volatile var readyFile: File? = null

    /** Poll the relay and save its live cloud URLs (wss + upd) to Prefs.
     *  Returns the fresh URLs, or null if the relay is unreachable (caller
     *  falls back to whatever Prefs already holds). Run from a worker. */
    fun refreshRelay(ctx: Context, timeoutMs: Int = 8000): RelayUrls? {
        try {
            // cache-bust: gist raw is CDN-cached; a per-call query forces a hit
            val url = "$RELAY_RAW?cb=${System.currentTimeMillis()}"
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.connectTimeout = timeoutMs; conn.readTimeout = timeoutMs
            if (conn.responseCode != 200) { conn.disconnect(); return null }
            val o = org.json.JSONObject(conn.inputStream.bufferedReader().readText())
            conn.disconnect()
            val bridge = o.optString("bridge", "").trim()
            val upd = o.optString("upd", "").trim()
            // guard-side parse bug can publish "https://api.trycloudflare.com"
            // (an endpoint, not a tunnel) — never save that
            fun ok(u: String) = u.startsWith("https://") && !u.contains("api.trycloudflare")
            val bridgeOk = if (ok(bridge)) bridge else ""
            val updOk = if (ok(upd)) upd else ""
            val wss = if (bridgeOk.isNotEmpty()) "wss://${bridgeOk.removePrefix("https://")}" else null
            if ((wss == null || wss.isBlank()) && updOk.isEmpty()) return null
            wss?.let { Prefs.saveWss(ctx, it) }
            if (updOk.isNotEmpty()) Prefs.saveUpd(ctx, updOk)
            main.post { if (state == "checking") message = "relay: live endpoints found" }
            return RelayUrls(wss, updOk.ifEmpty { null })
        } catch (e: Exception) {
            return null
        }
    }

    /** Check for a newer build. Run from a worker.
     *  Tries every reachable source: the relay/cloud update base first
     *  (works from any network), then the paired LAN host. A quick-tunnel
     *  URL can go stale between checks — so one dead source must not
     *  shadow the others. */
    fun check(ctx: Context, host: String, updBase: String? = null): Release? {
        state = "checking"; progress = -1
        val bases = buildList {
            if (host.isNotBlank()) add("http://$host:$UPDATE_PORT")   // LAN first: instant when paired
            if (!updBase.isNullOrBlank()) add(updBase)                // cloud fallback
        }.map { it.trimEnd('/') }.distinct().filter { it.isNotBlank() }
        if (bases.isEmpty()) {
            state = "error"; message = "no update source (pair or relay first)"
            return null
        }
        var lastErr = ""
        for (base in bases) {
            try {
                val url = "$base/tether.json"
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = 6000; conn.readTimeout = 6000
                val httpCode = conn.responseCode
                if (httpCode != 200) {
                    conn.disconnect()
                    lastErr = "$base -> HTTP $httpCode"
                    continue
                }
                val json = conn.inputStream.bufferedReader().readText()
                conn.disconnect()
                val o = org.json.JSONObject(json)
                val newCode = o.getInt("version_code")
                if (newCode <= BuildConfig.VERSION_CODE) {
                    state = "idle"; message = "up to date (v${BuildConfig.VERSION_NAME})"
                    return null
                }
                return Release(
                    versionCode = newCode,
                    versionName = o.optString("version_name"),
                    apkUrl = "$base/" + o.optString("apk", "tether.apk"),
                    sha256 = o.optString("sha256"),
                    size = o.optLong("size"),
                )
            } catch (e: Exception) {
                lastErr = "$base -> ${(e.message ?: "unreachable").take(60)}"
            }
        }
        state = "error"; message = "update unreachable (${bases.size} source(s): $lastErr)"
        return null
    }

    /** Download + verify. Run from a worker thread; posts progress on main. */
    fun download(ctx: Context, rel: Release, onDone: (File?) -> Unit) {
        state = "downloading"
        try {
            val dir = File(ctx.cacheDir, "tether-updates").apply { mkdirs() }
            val out = File(dir, "tether-v${rel.versionCode}.apk")
            val conn = URL(rel.apkUrl).openConnection() as HttpURLConnection
            conn.connectTimeout = 10000
            conn.connect()
            val total = if (rel.size > 0) rel.size else conn.contentLength.toLong()
            val dig = if (rel.sha256.isNotEmpty()) MessageDigest.getInstance("SHA-256") else null
            var read = 0L
            conn.inputStream.use { input ->
                out.outputStream().use { output ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        output.write(buf, 0, n)
                        dig?.update(buf, 0, n)
                        read += n
                        val pct = if (total > 0) (read * 100 / total).toInt() else -1
                        main.post { progress = pct }
                    }
                }
            }
            conn.disconnect()
            if (dig != null) {
                val sum = dig.digest().joinToString("") { "%02x".format(it) }
                if (sum != rel.sha256) {
                    out.delete()
                    main.post { state = "error"; message = "checksum mismatch — not installing" }
                    onDone(null)
                    return
                }
            }
            main.post { state = "ready"; progress = 100; message = "v${rel.versionName} ready — tap INSTALL" }
            readyFile = out
            onDone(out)
        } catch (e: Exception) {
            main.post { state = "error"; message = "download failed: ${e.message}" }
            onDone(null)
        }
    }

    /** Launch the system installer (or ask for the OEM install permission first). */
    fun install(ctx: Context, apk: File) {
        if (!ctx.packageManager.canRequestPackageInstalls()) {
            val intent = Intent(
                android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${ctx.packageName}"),
            )
            ctx.startActivity(intent)
            return
        }
        val uri = FileProvider.getUriForFile(ctx, "dev.tether.app.filep", apk)
        val intent = Intent(Intent.ACTION_INSTALL_PACKAGE).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            ctx.startActivity(intent)
        } catch (_: Exception) {
            // very old OEMs: fall back to a file-manager-shareable path
            val fallback = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(Uri.fromFile(apk), "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            runCatching { ctx.startActivity(fallback) }
        }
    }
}
