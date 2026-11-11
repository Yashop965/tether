package dev.tether.app.ui

import android.Manifest
import android.content.Context
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.tether.app.Prefs

private val Dim = Color(0xFF888888)
private val Faint = Color(0xFF555555)
private val Line = Color(0xFF2A2A2A)

enum class View { Connect, Control }

@Composable
fun TetherApp() {
    val ctx = LocalContext.current
    val mic = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            Toast.makeText(ctx, "Microphone permission is required for voice mode", Toast.LENGTH_LONG).show()
        }
    }

    // Open straight into Control when a pairing was already made.
    var view by remember {
        mutableStateOf(if (Prefs.host(ctx).isNotBlank()) View.Control else View.Connect)
    }

    when (view) {
        View.Connect -> ConnectScreen(
            ctx = ctx,
            onConnected = {
                mic.launch(Manifest.permission.RECORD_AUDIO)
                view = View.Control
            },
        )
        View.Control -> ControlScreen(onExit = { view = View.Connect })
    }
}

// ---------------------------------------------------------------- connect

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectScreen(ctx: Context, onConnected: () -> Unit) {
    var host by remember { mutableStateOf(Prefs.host(ctx)) }
    var port by remember { mutableStateOf(Prefs.port(ctx)) }
    var token by remember { mutableStateOf(Prefs.token(ctx)) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .padding(horizontal = 22.dp, vertical = 40.dp),
    ) {
        Text("TETHER", color = Color.White, fontSize = 26.sp, letterSpacing = 8.sp, fontWeight = FontWeight.Light)
        Text(
            "REMOTE POINTER + VOICE",
            color = Faint,
            fontSize = 10.sp,
            letterSpacing = 3.sp,
            modifier = Modifier.padding(top = 4.dp),
        )

        Field(label = "HOST", value = host, onValue = { host = it }, modifier = Modifier.padding(top = 36.dp))
        Row(modifier = Modifier.padding(top = 14.dp)) {
            Field(label = "PORT", value = port, onValue = { port = it }, isNumber = true, modifier = Modifier.weight(1f))
            Field(label = "TOKEN", value = token, onValue = { token = it }, modifier = Modifier.weight(1f).padding(start = 12.dp))
        }

        OutlinedButton(
            onClick = {
                if (host.isBlank()) {
                    Toast.makeText(ctx, "Host is empty", Toast.LENGTH_SHORT).show()
                    return@OutlinedButton
                }
                Prefs.save(ctx, host, port, token)
                onConnected()
            },
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 30.dp),
        ) {
            Text("CONNECT", color = Color.White, fontSize = 13.sp, letterSpacing = 4.sp)
        }

        Text(
            "from  hermes tether show   ·   LAN only   ·   no cloud",
            color = Faint,
            fontSize = 10.sp,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 14.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    onValue: (String) -> Unit,
    isNumber: Boolean = false,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        Text(label, color = Faint, fontSize = 9.sp, letterSpacing = 2.5.sp)
        BasicTextField(
            value = value,
            onValueChange = onValue,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 6.dp)
                .border(width = 1.dp, color = Line)
                .background(Color.Black),
            singleLine = true,
            textStyle = TextStyle(color = Color.White, fontSize = 14.sp),
            cursorBrush = SolidColor(Color.White),
        )
    }
}
