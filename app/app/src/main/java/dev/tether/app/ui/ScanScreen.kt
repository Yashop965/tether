package dev.tether.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.util.concurrent.Executors
import java.util.concurrent.ExecutorService

/**
 * Full-screen in-app QR scanner (CameraX + MLKit, fully on-device, no cloud).
 * Scanning a `tether://pair?h=…&p=…&t=…` QR on a laptop screen pairs the
 * phone to that bridge in one motion — no typing, no host hunting.
 */
@Composable
fun ScanScreen(onQr: (String) -> Unit, onCancel: () -> Unit) {
    val ctx = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current

    var granted by remember {
        mutableStateOf(ctx.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    var denied by remember { mutableStateOf(false) }
    var locked by remember { mutableStateOf(false) } // qr captured

    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        granted = it
        denied = !it
    }
    LaunchedEffect(Unit) { if (!granted) permLauncher.launch(Manifest.permission.CAMERA) }

    val scanner: BarcodeScanner = remember {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build(),
        )
    }

    val previewView = remember { PreviewView(ctx) }

    // bind camera + analyzer for the lifetime of this screen
    DisposableEffect(lifecycle, granted) {
        var provider: ProcessCameraProvider? = null
        var analysis: ImageAnalysis? = null
        var executor: ExecutorService? = null
        if (granted) {
            val ex = Executors.newSingleThreadExecutor()
            executor = ex
            val fut = ProcessCameraProvider.getInstance(ctx)
            @Suppress("LeakingThis")
            fut.addListener({
                try {
                    val p = fut.get()
                    provider = p
                    val a = ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                    a.setAnalyzer(ex) { proxy: ImageProxy ->
                        val img = proxy.image
                        if (img != null && !locked) {
                            val input = InputImage.fromMediaImage(img, proxy.imageInfo.rotationDegrees)
                            scanner.process(input)
                                .addOnSuccessListener { codes ->
                                    val qr = codes?.firstOrNull()?.rawValue
                                    if (!qr.isNullOrEmpty()) {
                                        locked = true
                                        Handler(Looper.getMainLooper()).post { onQr(qr) }
                                    }
                                }
                                .addOnFailureListener { }
                        }
                        proxy.close()
                    }
                    analysis = a
                    val pv = Preview.Builder().build()
                    p.bindToLifecycle(lifecycle, CameraSelector.DEFAULT_BACK_CAMERA, pv, a)
                } catch (e: Exception) {
                    // camera unavailable / permissions - user can retry via cancel+scan
                }
            }, androidx.core.content.ContextCompat.getMainExecutor(ctx))
        }
        onDispose {
            runCatching { provider?.unbindAll() }
            analysis?.clearAnalyzer()
            executor?.shutdown()
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        if (granted && !locked) {
            AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
            // frame guide
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Box(
                    modifier = Modifier
                        .size(240.dp)
                        .border(width = 1.dp, color = Color(0x88FFFFFF)),
                )
            }
            Column(
                modifier = Modifier.fillMaxSize().padding(top = 44.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("SCAN THE LAPTOP'S QR", color = Color.White, fontSize = 14.sp, letterSpacing = 3.sp)
                Spacer(Modifier.height(6.dp))
                Text(
                    "open pairing.html on the laptop · or tap a shared tether:// link",
                    color = Color(0xFF666666),
                    fontSize = 10.sp,
                )
            }
            // cancel chip
            Box(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 34.dp)
                    .background(Color(0x99000000), shape = RoundedCornerShape(20.dp))
                    .clickable { onCancel() }
                    .padding(horizontal = 18.dp, vertical = 9.dp),
            ) {
                Text("CANCEL", color = Color(0xFFAAAAAA), fontSize = 11.sp, letterSpacing = 3.sp)
            }
        }
        if (denied && !granted) {
            Column(
                modifier = Modifier.fillMaxSize().padding(40.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("camera needed to scan", color = Color.White, fontSize = 14.sp, letterSpacing = 2.sp)
                Spacer(Modifier.height(10.dp))
                Text("grant camera permission, then relink", color = Color(0xFF666666), fontSize = 11.sp)
            }
        }
    }
}
