package dev.tether.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.core.view.WindowCompat
import dev.tether.app.ui.TetherApp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        AppSignal.deepLink = intent?.data?.toString()
        setContent {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black),
            ) {
                TetherApp()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        // singleTask: tapping a tether:// link while the app is open lands here
        super.onNewIntent(intent)
        AppSignal.deepLink = intent.data?.toString()
    }

    override fun onResume() {
        super.onResume()
        AppSignal.resumedTick.value += 1
    }
}
