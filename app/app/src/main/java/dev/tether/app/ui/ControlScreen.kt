package dev.tether.app.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.MediaRecorder
import android.media.MediaRecorder.AudioEncoder
import android.media.MediaRecorder.OutputFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.pointer.PointerEventType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.tether.app.AppSignal
import dev.tether.app.AutoUpdater
import dev.tether.app.BuildConfig
import dev.tether.app.Prefs
import dev.tether.app.TetherWs
import dev.tether.app.WifiPair
import java.io.File
import kotlinx.coroutines.delay
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

private val Dim = Color(0xFF888888)
private val Faint = Color(0xFF555555)
private val Line = Color(0xFF2A2A2A)
private val LineDim = Color(0xFF1C1C1C)

// ------------------------------------------------------------------- root

@Composable
fun ControlScreen() {
    val ctx = LocalContext.current
    // Rebuild the client when the saved pairing changes (LINK button).
    var wsVer by remember { mutableStateOf(0) }
    val ws = remember(wsVer) {
        TetherWs(Prefs.host(ctx), Prefs.port(ctx).toIntOrNull() ?: 8377, Prefs.token(ctx), Prefs.wss(ctx).ifBlank { null })
    }

    var up by remember { mutableStateOf(false) }
    var paused by remember { mutableStateOf(false) }
    var mode by remember { mutableStateOf("pad") }
    var note by remember { mutableStateOf<String?>(null) }
    var noteSent by remember { mutableStateOf<Boolean?>(null) }
    var proc by remember { mutableStateOf(false) }
    var connErr by remember { mutableStateOf<String?>(null) }
    var scan by remember { mutableStateOf(false) }
    var wifiMsg by remember { mutableStateOf<String?>(null) }

    // mic permission: requested when the user enters voice mode
    val micLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(mode) {
        if (mode == "voice" &&
            ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
        ) {
            micLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // Wi-Fi auto-join helper (declared before the launcher that calls it)
    fun startWifiJoin(ssid: String, psk: String) {
        wifiMsg = "joining '$ssid'…"
        WifiPair.join(
            ctx, ssid, psk,
            onProgress = { wifiMsg = it },
            onDone = { _ -> wifiMsg = null },
        )
    }

    // Wi-Fi auto-join permission (Android 12+); deferred join when granted
    val wifiPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            val s = Prefs.ssid(ctx); val k = Prefs.psk(ctx)
            if (s.isNotBlank() && k.isNotBlank()) startWifiJoin(s, k)
        }
    }

    /** Apply a pair QR / deep link: save creds (+hotspot/+remote tunnel), auto-join Wi-Fi, link. */
    fun applyPair(uri: String) {
        val spec = AppSignal.parsePairSpec(uri) ?: return
        Prefs.save(ctx, spec.host, spec.port, spec.token)
        Prefs.saveWss(ctx, spec.wss ?: "")
        Prefs.saveUpd(ctx, spec.upd ?: "")
        if (spec.wss != null) {
            // remote pairing: never auto-join a hotspot, forget old hotspot creds
            Prefs.saveHotspot(ctx, "", "")
        }
        if (spec.ssid != null && spec.psk != null) {
            Prefs.saveHotspot(ctx, spec.ssid, spec.psk)
            if (Build.VERSION.SDK_INT >= 31) {
                if (ctx.checkSelfPermission(Manifest.permission.NEARBY_WIFI_DEVICES) ==
                    PackageManager.PERMISSION_GRANTED
                ) {
                    startWifiJoin(spec.ssid, spec.psk)
                } else {
                    wifiMsg = "Wi-Fi permission needed to auto-join — requesting…"
                    wifiPerm.launch(Manifest.permission.NEARBY_WIFI_DEVICES)
                }
            }
        }
        connErr = null
        wsVer++
    }

    // ------------------------------------------------ deep link / qr arrival
    LaunchedEffect(AppSignal.resumedTick.value) {
        val uri = AppSignal.deepLink
        AppSignal.deepLink = null
        if (uri != null) {
            applyPair(uri)
        } else if (!up && Prefs.host(ctx).isNotBlank()) {
            // returning to the app after a network switch (hotspot/5G/Wi-Fi):
            // the auto-reconnect loop handles the dial, but a fresh instance
            // resets the backoff and picks up any Prefs edits made elsewhere
            wsVer++
        }
    }

    DisposableEffect(ws) {
        ws.setEvents(object : TetherWs.Events {
            override fun onConnected() { up = true; connErr = null }
            override fun onDisconnected() { up = false }
            override fun onSttPending() { proc = true }
            override fun onStt(text: String, sent: Boolean) {
                note = text; noteSent = sent; proc = false
                paused = false
            }
            override fun onSttError(msg: String) { note = "⚠ $msg"; noteSent = false; proc = false; paused = false }
            override fun onPaused(p: Boolean) { paused = p }
            override fun onConnectError(msg: String) { connErr = msg; up = false }
        })
        ws.connect()
        ws.send(TetherWs.HELLO)
        onDispose { ws.close() }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .systemBarsPadding()
            .imePadding(),
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
        ) {
        // ------------------------------------------------------- top bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 18.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(if (up) Color.White else Color(0xFF333333)),
            )
            Spacer(Modifier.weight(1f))
            Text(if (up) "LINKED" else "OFFLINE", color = if (up) Dim else Faint, fontSize = 9.sp, letterSpacing = 2.sp)
            if (up) {
                Spacer(Modifier.width(14.dp))
                // stop / resume the laptop cursor gate (only makes sense while linked)
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .border(width = 1.dp, color = if (paused) Color.White else Color(0xFF333333))
                        .background(if (paused) Color.White else Color.Black)
                        .clickable {
                            if (!paused) { ws.send(TetherWs.PAUSE); paused = true }
                            else { ws.send(TetherWs.RESUME); paused = false }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(if (paused) "ON" else "OFF", color = if (paused) Color.Black else Dim, fontSize = 9.sp, letterSpacing = 1.sp)
                }
            }
        }

        // transient auto-join / permission status (from a pair QR with hotspot creds)
        val wmsg = wifiMsg
        if (!wmsg.isNullOrBlank()) {
            Text(
                wmsg,
                color = Color(0xFF88CC88),
                fontSize = 10.sp,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 18.dp, vertical = 4.dp),
            )
        }

        // ------------------------------------------------------ mode pills
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 18.dp),
        ) {
            listOf("pad", "tilt", "voice").forEach { m ->
                val on = mode == m
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .padding(horizontal = 3.dp)
                        .clip(RoundedCornerShape(999.dp))
                        .background(if (on) Color.White else Color.Black)
                        .border(width = 1.dp, color = if (on) Color.White else Line)
                        .clickable { mode = m }
                        .padding(vertical = 9.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        m.uppercase(),
                        color = if (on) Color.Black else Dim,
                        fontSize = 11.sp,
                        letterSpacing = 3.sp,
                    )
                }
            }
        }

        // --------------------------------------------------------- surface
        when {
            !up -> SurfaceFrame(Modifier.weight(1f)) { LinkPanel(Modifier, connErr, { scan = true }) { Prefs.save(ctx, it.first, it.second, it.third); wsVer++ } }
            mode == "pad" -> SurfaceFrame(Modifier.weight(1f)) { PadSurface(ws, Modifier, up) }
            mode == "tilt" -> SurfaceFrame(Modifier.weight(1f)) { TiltSurface(ws, Modifier) }
            else -> SurfaceFrame(Modifier.weight(1f)) { VoiceSurface(ws, Modifier, note, noteSent, up, proc) }
        }

        // ------------------------------------------------- bottom strip
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 18.dp, vertical = 12.dp),
        ) {
            Text("LAST NOTE", color = Faint, fontSize = 9.sp, letterSpacing = 2.sp)
            Spacer(Modifier.width(12.dp))
            Text(
                text = when {
                    note == null && noteSent == null -> "—"
                    noteSent == true -> note + "  ✓"
                    else -> note ?: "—"
                },
                color = if (noteSent == false) Color(0xFFCC4444) else Color.White,
                fontSize = 11.sp,
                modifier = Modifier.weight(1f),
                maxLines = 2,
            )
        }

        // ------------------------------------------------- in-app update row
        UpdateRow(ctx)
        }

        // ------------------------------------------------- in-app qr scanner
        if (scan) {
            ScanScreen(
                onQr = { txt ->
                    applyPair(txt)
                    scan = false
                },
                onCancel = { scan = false },
            )
        }
    }
}

/** Compact in-app self-update row. The update server rides the bridge's own
 *  host on port 8400 — same transport, no extra infra. */
@Composable
private fun UpdateRow(ctx: android.content.Context) {
    // poll the updater's volatile state while a check/download is in flight
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            if (AutoUpdater.state == "checking" || AutoUpdater.state == "downloading") {
                tick++; delay(400)
            } else delay(1500)
        }
    }
    val host = Prefs.host(ctx)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 18.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("v${BuildConfig.VERSION_NAME}", color = Faint, fontSize = 9.sp)
        Spacer(Modifier.width(8.dp))
        when (AutoUpdater.state) {
            "checking" -> Text("checking for updates…", color = Dim, fontSize = 9.sp, modifier = Modifier.weight(1f))
            "downloading" -> Text(
                "updating ${if (AutoUpdater.progress >= 0) "${AutoUpdater.progress}%" else ""}",
                color = Dim, fontSize = 9.sp, modifier = Modifier.weight(1f),
            )
            "ready" -> {
                Text("new version ready", color = Color.White, fontSize = 9.sp, modifier = Modifier.weight(1f))
                Text(
                    "INSTALL",
                    color = Color.Black,
                    fontSize = 9.sp,
                    letterSpacing = 2.sp,
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(Color.White)
                        .clickable {
                            val f = AutoUpdater.readyFile
                            if (f != null) AutoUpdater.install(ctx, f)
                        }
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
            "error" -> {
                Text(AutoUpdater.message, color = Faint, fontSize = 9.sp, modifier = Modifier.weight(1f).padding(end = 8.dp), maxLines = 1)
                UpdateChip("CHECK", true) { startUpdateCheck(ctx, host) }
            }
            else -> {
                Text("", modifier = Modifier.weight(1f))
                UpdateChip("CHECK", true) { startUpdateCheck(ctx, host) }
            }
        }
    }
}

@Composable
private fun UpdateChip(label: String, enabled: Boolean, onClick: () -> Unit) {
    Text(
        label,
        color = if (enabled) Dim else Faint,
        fontSize = 9.sp,
        letterSpacing = 2.sp,
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .border(width = 1.dp, color = Line)
            .clickable(enabled = enabled) { onClick() }
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

private fun startUpdateCheck(ctx: android.content.Context, host: String) {
    if (AutoUpdater.state in listOf("checking", "downloading")) return
    Thread {
        // Refresh the discovery relay first — it may now carry live cloud
        // endpoints even when this phone has never paired with this laptop.
        AutoUpdater.refreshRelay(ctx)
        val upd = Prefs.upd(ctx)
        if (host.isBlank() && upd.isBlank()) {
            AutoUpdater.state = "error"
            AutoUpdater.message = "no update source (laptop off or relay down)"
            return@Thread
        }
        AutoUpdater.check(ctx, host, upd.ifBlank { null })?.let { rel ->
            AutoUpdater.download(ctx, rel) { }
        }
    }.start()
}

// ------------------------------------------------------------------- link

/** Centered, height-capped frame for the mode surfaces. Without the cap the
 *  surface is a giant tall black bar on tall phones (2408px) — which reads as
 *  "stretched" and makes vertical pad scaling feel dead. Capped to 1.5:1
 *  and 72% of available height, then centered. */
@Composable
private fun SurfaceFrame(modifier: Modifier, content: @Composable () -> Unit) {
    Box(modifier = modifier.fillMaxWidth()) {
        BoxWithConstraints {
            val h = minOf(maxHeight * 0.72f, maxWidth * 1.5f)
            Box(Modifier.align(Alignment.Center).fillMaxWidth().height(h)) {
                content()
            }
        }
    }
}

/** Shown instead of the surface while the socket is down. Lets you re-pair
 *  in place — the common failure is a host that changed (Wi-Fi vs Tailscale). */
@Composable
private fun LinkPanel(modifier: Modifier, connErr: String?, onScan: () -> Unit, onLink: (Triple<String, String, String>) -> Unit) {
    val ctx = LocalContext.current
    var host by remember { mutableStateOf(Prefs.host(ctx)) }
    var port by remember { mutableStateOf(Prefs.port(ctx)) }
    var token by remember { mutableStateOf(Prefs.token(ctx)) }
    var showManual by remember { mutableStateOf(false) }
    val paired = host.isNotBlank() || Prefs.wss(ctx).isNotBlank()

    Column(
        modifier = modifier
            .padding(horizontal = 18.dp, vertical = 14.dp)
            .background(Color(0xFF050505))
            .clip(RoundedCornerShape(14.dp))
            .border(width = 1.dp, color = LineDim)
            .padding(20.dp),
    ) {
        Text(if (paired) "OFFLINE — RETRYING" else "NOT CONNECTED", color = Color.White, fontSize = 13.sp, letterSpacing = 3.sp)
        Text(
            if (paired) "reconnecting automatically · rescan the QR if the laptop moved networks"
            else "scan the QR on the laptop screen — one tap, no typing",
            color = Faint,
            fontSize = 10.sp,
            modifier = Modifier.padding(top = 6.dp, bottom = 18.dp),
        )
        if (!connErr.isNullOrBlank()) {
            Text(
                connErr,
                color = Color(0xFFCC4444),
                fontSize = 10.sp,
                modifier = Modifier.padding(bottom = 14.dp),
            )
        }

        // --------------------------------------------- QR is the primary path
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(Color.White)
                .clickable { onScan() }
                .padding(vertical = 13.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text("SCAN QR", color = Color.Black, fontSize = 13.sp, letterSpacing = 5.sp)
        }

        // ------------------------------------------- manual config, folded
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 14.dp)
                .clickable { showManual = !showManual },
            contentAlignment = Alignment.Center,
        ) {
            Text(
                if (showManual) "HIDE MANUAL CONFIG" else "MANUAL CONFIG (IP + TOKEN)",
                color = Faint,
                fontSize = 9.sp,
                letterSpacing = 2.sp,
            )
        }
        if (showManual) {
            LkField("HOST", host) { host = it; Prefs.save(ctx, host, port, token) }
            Row(modifier = Modifier.padding(top = 12.dp)) {
                LkField("PORT", port, modifier = Modifier.weight(1f)) { port = it; Prefs.save(ctx, host, port, token) }
                LkField("TOKEN", token, modifier = Modifier.weight(1.6f).padding(start = 12.dp)) { token = it; Prefs.save(ctx, host, port, token) }
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 18.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .border(width = 1.dp, color = Color(0xFF444444))
                    .clickable(enabled = host.isNotBlank()) { onLink(Triple(host, port, token)) }
                    .padding(vertical = 11.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text("LINK", color = Color(0xFFAAAAAA), fontSize = 12.sp, letterSpacing = 4.sp)
            }
        }
    }
}

@Composable
private fun LkField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    onValue: (String) -> Unit,
) {
    Column(modifier = modifier) {
        Text(label, color = Faint, fontSize = 9.sp, letterSpacing = 2.sp)
        BasicTextField(
            value = value,
            onValueChange = onValue,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 6.dp)
                .border(width = 1.dp, color = Line)
                .background(Color.Black)
                .padding(horizontal = 8.dp),
            singleLine = true,
            textStyle = TextStyle(color = Color.White, fontSize = 13.sp),
            cursorBrush = SolidColor(Color.White),
        )
    }
}

// ------------------------------------------------------------------- pad

@Composable
fun PadSurface(ws: TetherWs, modifier: Modifier, up: Boolean) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 18.dp, vertical = 14.dp)
            .background(Color(0xFF050505))
            .clip(RoundedCornerShape(14.dp))
            .border(width = 1.dp, color = LineDim)
            .pointerInput(ws, up) {
                if (!up) return@pointerInput
                var lastPos: Offset? = null
                var lastTwoY: Float? = null
                // per-finger y positions — a Move frame may report only ONE of
                // the two pressed fingers, so "pressed[1]" crashes; the map is
                // the source of truth for two-finger scroll.
                val fingerY = mutableMapOf<Any, Float>()
                var active = 0          // pointers currently pressed
                var maxActive = 0       // peak simultaneous in this gesture
                var pressStart = 0L
                var movedPastSlop = false
                var lastSend = 0L      // coalesce pointer events to ~60Hz sends (LAN) / ~30Hz (cloud)
                var accX = 0f
                var accY = 0f
                var sX = 0f            // smoothed pointer speed, px/s
                var sY = 0f
                val slopPx = 24f
                // LAN: 16 ms batches so the host's 60 Hz pump gets enough
                // samples to interpolate; cloud: keep 33 ms (fewer hops).
                val coalesceMs = if (ws.isRemote) 33L else 16L
                // trackpad convention: finger swipe DOWN scrolls content down,
                // i.e. pynput wheel "up" (negative). 25 px per wheel step.
                val scrollPx = 25f

                awaitPointerEventScope {
                    while (true) {
                        val ev = awaitPointerEvent()
                        val ch = ev.changes ?: continue
                        // bookkeeping: who is pressed, where (y)
                        for (c in ch) {
                            if (c.pressed) fingerY[c.id] = c.position.y else fingerY.remove(c.id)
                        }
                        when (ev.type) {
                            PointerEventType.Press -> {
                                active++
                                maxActive = max(maxActive, active)
                                if (pressStart == 0L) pressStart = ch[0].uptimeMillis
                                if (active == 1) { lastPos = ch[0].position; lastTwoY = null }
                            }
                            PointerEventType.Move -> {
                                when {
                                    fingerY.size >= 2 -> {
                                        val cy = fingerY.values.sum() / fingerY.size
                                        lastTwoY?.let {
                                            val steps = (((cy - it) / scrollPx)).toInt()
                                            if (steps != 0) ws.send("""{"t":"scroll","dy":${-steps}}""")
                                        }
                                        lastTwoY = cy
                                        lastPos = null
                                        movedPastSlop = true
                                    }
                                    active == 1 && fingerY.size == 1 -> {
                                        val p = ch.firstOrNull { it.pressed } ?: continue
                                        val prev = lastPos
                                        if (prev != null) {
                                            accX += p.position.x - prev.x
                                            accY += p.position.y - prev.y
                                            if (kotlin.math.abs(accX) > slopPx || kotlin.math.abs(accY) > slopPx) {
                                                movedPastSlop = true
                                            }
                                            // coalesce to ~60Hz: the host's 60 Hz
                                            // interpolation pump smooths batches
                                            // into silky motion; 120Hz streaming
                                            // only adds jitter for no distance gain.
                                            val now = p.uptimeMillis
                                            if (movedPastSlop && now - lastSend >= coalesceMs) {
                                                val dt = (now - lastSend) / 1000f
                                                if (dt > 0f) {
                                                    val instX = accX / dt
                                                    val instY = accY / dt
                                                    // exponential smoothing of speed — the
                                                    // touchpad "glide" feel: the cursor eases
                                                    // toward the finger's rate instead of
                                                    // jumping in batches
                                                    sX = if (instX == 0f) sX * 0.8f else 0.55f * sX + 0.45f * instX
                                                    sY = if (instY == 0f) sY * 0.8f else 0.55f * sY + 0.45f * instY
                                                }
                                                val dx = accX / size.width
                                                val dy = accY / size.height
                                                ws.send(
                                                    """{"t":"pad","dx":${"%.4f".format(dx)},"dy":${"%.4f".format(dy)}}"""
                                                )
                                                accX = 0f; accY = 0f
                                                lastSend = now
                                            }
                                        }
                                        lastPos = p.position
                                    }
                                }
                            }
                            PointerEventType.Release -> {
                                active--
                                if (active == 0) {
                                    // flush the trailing un-sent delta batch
                                    if (accX != 0f || accY != 0f) {
                                        val dx = accX / size.width
                                        val dy = accY / size.height
                                        ws.send(
                                            """{"t":"pad","dx":${"%.4f".format(dx)},"dy":${"%.4f".format(dy)}}"""
                                        )
                                        accX = 0f; accY = 0f
                                    }
                                    val dt = ch[0].uptimeMillis - pressStart
                                    if (dt < 300L && !movedPastSlop) {
                                        val b = if (maxActive >= 2) "right" else "left"
                                        ws.send("""{"t":"click","b":"$b"}""")
                                    }
                                    // touchpad glide: keep coasting at the smoothed
                                    // speed, decaying, like a finger lifting off a
                                    // real trackpad. Local only (cloud RTT kills
                                    // the effect).
                                    if (!ws.isRemote && (kotlin.math.abs(sX) > 120f || kotlin.math.abs(sY) > 120f)) {
                                        val gs = sX; val gs2 = sY
                                        Thread {
                                            var gX = gs * 0.85f
                                            var gY = gs2 * 0.85f
                                            repeat(14) {
                                                Thread.sleep(16)
                                                if (kotlin.math.abs(gX) > 20f || kotlin.math.abs(gY) > 20f) {
                                                    ws.send(
                                                        """{"t":"pad","dx":${"%.4f".format(gX * 0.016f / size.width)},"dy":${"%.4f".format(gY * 0.016f / size.height)}}"""
                                                    )
                                                }
                                                gX *= 0.8f; gY *= 0.8f
                                            }
                                        }.start()
                                        sX = 0f; sY = 0f
                                    }
                                    pressStart = 0L; maxActive = 0; movedPastSlop = false
                                    lastPos = null; lastTwoY = null
                                }
                            }
                            else -> { }
                        }
                    }
                }
            },
        contentAlignment = Alignment.BottomCenter,
    ) {
        Text(
            if (!up) "NOT CONNECTED" else "drag · tap = click · 2 fingers = right / scroll",
            color = Faint,
            fontSize = 10.sp,
            letterSpacing = 1.sp,
            modifier = Modifier.padding(bottom = 12.dp),
        )
    }
}

// ----------------------------------------------------------------- tilt

@Composable
fun TiltSurface(ws: TetherWs, modifier: Modifier) {
    val ctx = LocalContext.current
    // Laser / air-pen: on entering the surface we capture your "neutral" pose
    // (wherever you're standing, however you hold the phone). Tilt VELOCITY
    // (change between sensor frames) becomes cursor velocity — move the phone
    // like a laser pointer: wave right, the cursor moves right; stop, it
    // stays where it is. Relative "pad" deltas ride the bridge's 60 Hz
    // interpolation pump, so no position is ever "held" that could snap back.
    var dotX by remember { mutableFloatStateOf(0f) }   // on-screen dot: smoothed velocity, -1..1
    var dotY by remember { mutableFloatStateOf(0f) }
    var recal by remember { mutableStateOf(0) }
    var calibrating by remember { mutableStateOf(true) }

    DisposableEffect(ctx, recal) {
        calibrating = true
        dotX = 0f
        dotY = 0f
        val sm = ctx.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val sensor = sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        var lastSend = 0L
        var lastTs = 0L
        // calibration: average the first ~500 ms of readings into a neutral
        var baseP = 0f
        var baseR = 0f
        var baseN = 0
        var basePSum = 0f
        var baseRSum = 0f
        var calibrated = false
        var lastRoll = 0f
        var lastPitch = 0f
        var haveLast = false
        // laser gain: radian-of-tilt-velocity (per second) -> screen-widths/s.
        // ~0.6 rad/s gentle wave crosses the screen in ~1.4 s.
        val gain = 1.3f
        // per-frame tilt delta smaller than this is hand/shelf noise, not a wave
        val frameDead = 0.0025f
        val listener = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                val now = SystemClock.elapsedRealtime()
                val q0 = e.values
                val m22 = 1 - 2 * (q0[0] * q0[0] + q0[1] * q0[1])
                val m00 = 1 - 2 * (q0[1] * q0[1] + q0[2] * q0[2])
                val m10 = 2 * (q0[0] * q0[1] + q0[2] * q0[3])
                val pitch = Math.asin(m22.toDouble()).toFloat()
                val roll = Math.atan2(m00.toDouble(), m10.toDouble()).toFloat()
                if (!calibrated) {
                    if (lastTs == 0L) { lastTs = now; return }
                    if (now - lastTs < 500L) {
                        // still in the calibration window: sum the neutral pose
                        basePSum += pitch; baseRSum += roll
                        baseN++
                        lastTs = now
                        return
                    }
                    baseP = basePSum / baseN
                    baseR = baseRSum / baseN
                    calibrated = true
                    calibrating = false
                    haveLast = false
                }
                if (now - lastSend < 33L) return
                lastSend = now
                // tilt offset from the calibrated neutral, velocity between frames
                val dr = if (haveLast) roll - lastRoll else 0f
                val dp = if (haveLast) pitch - lastPitch else 0f
                lastRoll = roll; lastPitch = pitch; haveLast = true
                // deadzone noise that isn't a real wave
                val dX = if (kotlin.math.abs(dr) > frameDead) (dr - if (dr > 0) frameDead else -frameDead) * gain else 0f
                val dY = if (kotlin.math.abs(dp) > frameDead) (dp - if (dp > 0) frameDead else -frameDead) * gain else 0f
                if (dX == 0f && dY == 0f) {
                    // motion stopped: decay the dot back to center
                    dotX *= 0.6f; dotY *= 0.6f
                    if (kotlin.math.abs(dotX) < 0.01f) dotX = 0f
                    if (kotlin.math.abs(dotY) < 0.01f) dotY = 0f
                    return
                }
                ws.send("""{"t":"pad","dx":${"%.4f".format(dX)}, "dy":${"%.4f".format(dY)}}""")
                // on-screen dot = smoothed velocity indicator (not a position — it
                // always decays home, so RESET AIM can never "snap back")
                val nx = (dX * 4f).coerceIn(-0.5f, 0.5f)
                val ny = (dY * 4f).coerceIn(-0.5f, 0.5f)
                dotX = 0.5f * dotX + 0.5f * nx
                dotY = 0.5f * dotY + 0.5f * ny
            }

            override fun onAccuracyChanged(e: Sensor?, a: Int) {}
        }
        if (sensor != null) sm.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_GAME)
        onDispose {
            sm.unregisterListener(listener)
            ws.send("""{"t":"tilt","active":false}""")
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 18.dp, vertical = 14.dp)
            .background(Color(0xFF050505))
            .clip(RoundedCornerShape(14.dp))
            .border(width = 1.dp, color = LineDim)
            .pointerInput(ws, recal) {
                var active = 0
                var maxActive = 0
                var pressStart = 0L
                awaitPointerEventScope {
                    while (true) {
                        val ev = awaitPointerEvent()
                        val ch = ev.changes ?: continue
                        when (ev.type) {
                            PointerEventType.Press -> {
                                active++
                                maxActive = max(maxActive, active)
                                if (pressStart == 0L) pressStart = ch[0].uptimeMillis
                            }
                            PointerEventType.Release -> {
                                active--
                                if (active == 0) {
                                    val dt = ch[0].uptimeMillis - pressStart
                                    if (dt < 300L) {
                                        val b = if (maxActive >= 2) "right" else "left"
                                        ws.send("""{"t":"click","b":"$b"}""")
                                    }
                                    pressStart = 0L; maxActive = 0
                                }
                            }
                            else -> { }
                        }
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        // crosshair frame + live aim dot
        BoxWithConstraints {
            val side = minOf(maxWidth * 0.45f, 170.dp)
            Box(
                modifier = Modifier
                    .size(side)
                    .clip(CircleShape)
                    .border(width = 1.dp, color = Color(0xFF2C2C2C)),
                contentAlignment = Alignment.Center,
            ) {
                Box(Modifier.fillMaxHeight().width(1.dp).background(Color(0xFF2C2C2C)))
                Box(Modifier.fillMaxWidth().height(1.dp).background(Color(0xFF2C2C2C)))
                Box(
                    Modifier
                        .align(Alignment.Center)
                        .size(side * 0.06f)
                        .clip(CircleShape)
                        .background(Color(0xFF333333)),
                )
                // laser dot — smoothed tilt-velocity indicator (decays to center)
                val dot = side * 0.14f
                Box(
                    Modifier
                        .size(dot)
                        .align(Alignment.TopStart)
                        .offset(
                            x = side * (dotX * 0.5f + 0.5f) - dot,
                            y = side * (dotY * 0.5f + 0.5f) - dot,
                        )
                        .clip(CircleShape)
                        .background(Color.White),
                )
            }
        }
        // re-calibrate chip — capture a new neutral pose wherever you are
        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(10.dp)
                .background(Color(0x33FFFFFF), shape = RoundedCornerShape(10.dp))
                .clickable { recal++ }
                .padding(horizontal = 10.dp, vertical = 6.dp),
        ) {
            Text("RESET AIM", color = Color(0xFFAAAAAA), fontSize = 9.sp, letterSpacing = 2.sp)
        }
        Text(
            when {
                calibrating -> "hold still — capturing your neutral pose…"
                else -> "move phone like a laser · tap = click · right-tap = right click"
            },
            color = Faint,
            fontSize = 10.sp,
            letterSpacing = 1.sp,
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp),
        )
    }
}

// ---------------------------------------------------------------- voice

@Composable
fun VoiceSurface(ws: TetherWs, modifier: Modifier, note: String?, noteSent: Boolean?, up: Boolean, proc: Boolean = false) {
    val ctx = LocalContext.current
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    val file = remember { File(ctx.cacheDir, "tether.m4a") }
    val main = remember { Handler(Looper.getMainLooper()) }
    // recorder + timestamps live in a volatile holder — created/stopped from
    // worker threads; Compose state stays on main.
    val recHolder = remember {
        object {
            @Volatile var r: MediaRecorder? = null
            @Volatile var start = 0L
        }
    }

    fun start() {
        if (recHolder.r != null || !up) return
        err = null
        busy = true
        Thread {
            // AAC first (best whisper fidelity); fall back to AMR if the
            // device's audio HAL rejects it. Explicit samplerate/channels —
            // leaving them implicit is what breaks prepare() on Exynos.
            val cfgs = listOf(
                intArrayOf(OutputFormat.MPEG_4, AudioEncoder.AAC, 44100, 128 * 1000),
                intArrayOf(OutputFormat.DEFAULT, AudioEncoder.AMR_NB, 8000, 12 * 1000),
            )
            for (cfg in cfgs) {
                val (fmt, enc, rate, br) = cfg
                try {
                    val r = if (Build.VERSION.SDK_INT >= 29) MediaRecorder(ctx)
                    else @Suppress("DEPRECATION") MediaRecorder()
                    r.apply {
                        setAudioSource(MediaRecorder.AudioSource.MIC)
                        setAudioChannels(1)
                        setAudioSamplingRate(rate)
                        setAudioEncodingBitRate(br)
                        setOutputFormat(fmt)
                        setAudioEncoder(enc)
                        setOutputFile(file.absolutePath)
                        prepare()
                        start()
                    }
                    recHolder.r = r
                    recHolder.start = SystemClock.elapsedRealtime()
                    return@Thread
                } catch (_: Exception) {
                    // this codec combo failed on this HAL — try the next
                }
            }
            main.post { busy = false; err = "mic could not start" }
        }.start()
    }

    fun stopAndSend() {
        val r = recHolder.r
        recHolder.r = null
        busy = false
        if (r == null) return
        val dur = SystemClock.elapsedRealtime() - recHolder.start
        Thread {
            try { r.stop() } catch (_: Exception) {}
            r.release()
            val bytes = if (file.exists()) file.readBytes() else ByteArray(0)
            file.delete()
            // AMR-NB fallback encodes ~1.5 KB/s, so a 2 s clip is only ~3 KB —
            // a high byte floor used to flag legit recordings "too short".
            // Only reject clips that are genuinely empty/fragmented.
            if (dur < 700L || bytes.size < 1024) {
                main.post { err = "too short — hold the mic ~1 second or more" }
                return@Thread
            }
            val sent = ws.sendAudio(bytes)
            if (sent > 0) {
                ws.send(TetherWs.AUDIO_DONE)
            } else {
                main.post { err = "audio not sent — link may have dropped, tap again" }
            }
        }.start()
    }

    DisposableEffect(note) {
        if (note != null) { err = null }
        onDispose { }
    }

    // pulsing ring while recording
    val pulse = rememberInfiniteTransition()
    val ring by pulse.animateFloat(
        initialValue = 1f,
        targetValue = 1.5f,
        animationSpec = infiniteRepeatable(
            animation = tween(650, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
    )
    val ringAlpha by pulse.animateFloat(
        initialValue = 0.6f,
        targetValue = 0.15f,
        animationSpec = infiniteRepeatable(
            animation = tween(650, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
    )

    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 18.dp, vertical = 14.dp)
            .background(Color(0xFF050505))
            .clip(RoundedCornerShape(14.dp))
            .border(width = 1.dp, color = LineDim),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            // mic button — proportional to surface width, capped
            BoxWithConstraints {
                val side = minOf(maxWidth * 0.40f, 140.dp)
                Box(contentAlignment = Alignment.Center) {
                    if (busy) {
                        // expanding pulse rings behind the mic
                        Box(
                            modifier = Modifier
                                .size(side * ring)
                                .clip(CircleShape)
                                .border(width = 1.dp, color = Color(0x66FFFFFF))
                                .alpha(ringAlpha),
                        )
                        Box(
                            modifier = Modifier
                                .size(side * (1 + 0.25f * ring))
                                .clip(CircleShape)
                                .border(width = 1.dp, color = Color(0x33FFFFFF))
                                .alpha((1.5f - ring) * 1.2f),
                        )
                    }
                    Box(
                        modifier = Modifier
                            .size(side)
                            .clip(CircleShape)
                            .border(width = 1.dp, color = if (busy || proc) Color.White else Line)
                            .background(Color.Black)
                            .clickable(enabled = up || busy) {
                                if (busy) stopAndSend() else start()
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        MicGlyph(busy)
                    }
                }
            }
            Text(
                when {
                    err != null -> err.orEmpty()
                    proc -> "PROCESSING…"
                    busy -> "LISTENING… TAP TO SEND"
                    !up -> "NOT CONNECTED"
                    else -> "TAP TO TALK"
                },
                color = when {
                    err != null -> Color(0xFFCC4444)
                    (busy || proc) -> Color.White
                    else -> Dim
                },
                fontSize = 11.sp,
                letterSpacing = 3.sp,
                modifier = Modifier.padding(top = 18.dp),
            )
            if (note != null) {
                Text(
                    "“$note” " + if (noteSent == true) "✓" else "",
                    color = Color.White,
                    fontSize = 12.sp,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 18.dp, vertical = 10.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .border(width = 1.dp, color = LineDim)
                        .padding(10.dp),
                )
            }
        }
    }
}

@Composable
private fun MicGlyph(recording: Boolean) {
    // Minimal monochrome mic drawn with boxes — no assets.
    Box(contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(
                Modifier
                    .width(14.dp)
                    .height(40.dp)
                    .clip(CircleShape)
                    .background(if (recording) Color.White else Color(0xFF333333)),
            )
            Box(
                Modifier
                    .width(44.dp)
                    .height(28.dp)
                    .clip(RoundedCornerShape(bottomStart = 22.dp, bottomEnd = 22.dp, topStart = 6.dp, topEnd = 6.dp))
                    .border(width = 1.dp, color = if (recording) Color.White else Color(0xFF333333)),
            )
            Box(Modifier.width(1.dp).height(8.dp).background(Color(0xFF333333)))
        }
    }
}
