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
 */
object AutoUpdater {
    const val UPDATE_PORT = 8400

    data class Release(
        val versionCode: Int,
        val versionName: String,
        val apkUrl: String,
        val sha256: String,
        val size: Long,
    )

    val main = Handler(Looper.getMainLooper())
    @Volatile var progress = -1            // -1 idle, 0..100 downloading
    @Volatile var state = "idle"           // idle | checking | downloading | ready | error
    @Volatile var message = ""
    @Volatile var readyFile: File? = null

    /** Check for a newer build. Run from a worker.
     *  [updBase] is an optional full update-server base (e.g. an https cloud
     *  tunnel from a pair QR's `u=`); without it we fall back to the bridge
     *  host on port 8400 (LAN/hotspot/tailnet). */
    fun check(ctx: Context, host: String, updBase: String? = null): Release? {
        state = "checking"; progress = -1
        val base = (if (!updBase.isNullOrBlank()) updBase else "http://$host:$UPDATE_PORT").trimEnd('/')
        val url = "$base/tether.json"
        try {
            val conn = URL(url).openConnection() as HttpURLConnection
            conn.connectTimeout = 5000; conn.readTimeout = 5000
            if (conn.responseCode != 200) { state = "error"; message = "update server not reachable ($base)"; return null }
            val json = conn.inputStream.bufferedReader().readText()
            conn.disconnect()
            val o = org.json.JSONObject(json)
            val code = o.getInt("version_code")
            if (code <= BuildConfig.VERSION_CODE) {
                state = "idle"; message = "up to date (v${BuildConfig.VERSION_NAME})"
                return null
            }
            return Release(
                versionCode = code,
                versionName = o.optString("version_name"),
                apkUrl = "$base/" + o.optString("apk", "tether.apk"),
                sha256 = o.optString("sha256"),
                size = o.optLong("size"),
            )
        } catch (e: Exception) {
            state = "error"; message = "check failed ($base): ${e.message}"
            return null
        }
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

    fun reset() { state = "idle"; progress = -1; message = "" }
}
