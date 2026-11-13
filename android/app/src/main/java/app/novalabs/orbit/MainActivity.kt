package app.novalabs.orbit

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb

class MainActivity : ComponentActivity() {
    private val model: HubModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            // the phone's own colours when chosen (and the phone has them), else one of Orbit's themes
            val device = if (model.theme == DEVICE_THEME) deviceTokens() else null
            val tokens = device ?: tokensFor(model.theme)
            val lightBars = model.theme == "paper" || (device != null && !isSystemInDarkTheme())
            SideEffect {
                @Suppress("DEPRECATION")
                window.statusBarColor = tokens.bg.toArgb()
                @Suppress("DEPRECATION")
                window.navigationBarColor = tokens.bg.toArgb()
                if (Build.VERSION.SDK_INT >= 23) {
                    // dark icons on the light theme
                    @Suppress("DEPRECATION")
                    window.decorView.systemUiVisibility = if (lightBars)
                        android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR else 0
                }
            }
            CompositionLocalProvider(LocalTokens provides tokens, LocalImages provides rememberImages()) {
                OrbitApp(model)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // back from Android's installer or uninstaller, or from another app: re-read what's installed
        model.refresh()
    }
}
