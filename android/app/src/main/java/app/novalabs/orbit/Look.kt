package app.novalabs.orbit

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material3.Text
import coil.ImageLoader
import coil.compose.AsyncImage
import coil.decode.SvgDecoder
import kotlinx.coroutines.delay

/** The same colour tokens as Orbit for desktop; every theme is a "tui" look. */
data class Tokens(
    val bg: Color, val raise: Color, val line: Color, val lineHi: Color, val text: Color, val muted: Color,
    val legend: Color, val accent: Color, val accentInk: Color, val ok: Color, val warn: Color, val down: Color,
    val off: Color, val sel: Color, val radius: Int = 0,
)

private fun c(hex: Long) = Color(0xFF000000 or hex)

val THEMES: List<Triple<String, String, Tokens>> = listOf(
    Triple("tui", "dusk", Tokens(c(0x1c1c1e), c(0x262629), c(0x3d3d42), c(0x5b5b62), c(0xd6d6d8), c(0x8c8c93),
        c(0x9a9aa1), c(0xf0668e), c(0x1c1c1e), c(0x23a55a), c(0xf0b232), c(0xf23f43), c(0x6d6f78), c(0xe8e8ea))),
    Triple("phosphor", "phosphor", Tokens(c(0x070c08), c(0x0d1710), c(0x1d3a23), c(0x2f6a3b), c(0xa8ffb0), c(0x4f9a5a),
        c(0x62b96e), c(0x39ff7a), c(0x04140a), c(0x39ff7a), c(0xe6ff5c), c(0xff5c5c), c(0x2f5a36), c(0xa8ffb0))),
    Triple("amber", "amber", Tokens(c(0x120d05), c(0x1d1508), c(0x3e2e12), c(0x6b4e1c), c(0xffcf7a), c(0xa07a3c),
        c(0xc4913f), c(0xffb000), c(0x120d05), c(0xffd36b), c(0xff8a3d), c(0xff4d3d), c(0x5e4722), c(0xffcf7a))),
    Triple("paper", "paper", Tokens(c(0xf3f0e8), c(0xe9e5da), c(0xc4bdae), c(0x8f877a), c(0x2a2826), c(0x6b655e),
        c(0x6a645c), c(0xc2366b), c(0xffffff), c(0x16733d), c(0x8f5b12), c(0xc53030), c(0xa39d92), c(0x2a2826))),
    Triple("frost", "frost", Tokens(c(0x1b2028), c(0x232a35), c(0x36404f), c(0x4f5d72), c(0xdbe3ee), c(0x8592a6),
        c(0x93a4bb), c(0x7cc4e8), c(0x12171e), c(0x8fcf8a), c(0xebcb7d), c(0xe0707a), c(0x5d6879), c(0xe6edf5))),
    Triple("ember", "ember", Tokens(c(0x1a1412), c(0x251c18), c(0x3f302a), c(0x644a3f), c(0xecd9cf), c(0x9a8276),
        c(0xb08d7e), c(0xff7a45), c(0x1a1412), c(0x9ccc65), c(0xffc24b), c(0xff4f4f), c(0x6b574e), c(0xf3e3da))),
    Triple("ocean", "ocean", Tokens(c(0x0d1b24), c(0x13252f), c(0x20394a), c(0x315670), c(0xcfe6f0), c(0x6f93a5),
        c(0x7fa8bb), c(0x2ec4b6), c(0x062027), c(0x3ddc97), c(0xffbf46), c(0xff6b6b), c(0x3b5566), c(0xd8eef7))),
    Triple("lilac", "lilac", Tokens(c(0x1d1a24), c(0x26222f), c(0x3b3448), c(0x594e6d), c(0xe4ddf0), c(0x9389a6),
        c(0xa596c2), c(0xb794f6), c(0x1d1a24), c(0x7bd88f), c(0xf2c66d), c(0xf7768e), c(0x625a73), c(0xefe9f9))),
    Triple("mono", "mono", Tokens(c(0x111111), c(0x1c1c1c), c(0x333333), c(0x555555), c(0xe0e0e0), c(0x8a8a8a),
        c(0x9e9e9e), c(0xffffff), c(0x111111), c(0xcfcfcf), c(0xa8a8a8), c(0xffffff), c(0x4a4a4a), c(0xffffff))),
)

fun tokensFor(id: String) = (THEMES.firstOrNull { it.first == id } ?: THEMES[0]).third

/** The theme in the phone's own colours (Material You). */
const val DEVICE_THEME = "device"

/**
 * The "tui" look in the colours Android makes from the wallpaper (Material You, Android 12 and newer), light or dark as
 * the phone is set; null on a phone without them. Green and amber have no place in that palette, so they stay Orbit's
 * own, in shades that read well on it.
 */
@Composable
fun deviceTokens(): Tokens? {
    if (Build.VERSION.SDK_INT < 31) return null
    val ctx = LocalContext.current
    val dark = isSystemInDarkTheme()
    val s = if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
    return Tokens(bg = s.surface, raise = s.surfaceContainer, line = s.outlineVariant, lineHi = s.outline, text = s.onSurface,
        muted = s.onSurfaceVariant, legend = s.secondary, accent = s.primary, accentInk = s.onPrimary,
        ok = if (dark) c(0x7bd88f) else c(0x16733d), warn = if (dark) c(0xf2c66d) else c(0x8f5b12), down = s.error,
        off = s.outline, sel = s.onSurface)
}

val LocalTokens = staticCompositionLocalOf { THEMES[0].third }
val LocalImages = staticCompositionLocalOf<ImageLoader?> { null }
val Mono = FontFamily.Monospace

@Composable
fun Txt(text: String, modifier: Modifier = Modifier, color: Color = LocalTokens.current.text, size: TextUnit = 14.sp,
        bold: Boolean = false, maxLines: Int = Int.MAX_VALUE) {
    Text(text, modifier, color = color, maxLines = maxLines, overflow = TextOverflow.Ellipsis,
        style = TextStyle(fontFamily = Mono, fontSize = size, lineHeight = size * 1.45f,
            fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal))
}

/** A box whose title is cut into its top border. */
@Composable
fun LegendBox(title: String, modifier: Modifier = Modifier, borderColor: Color? = null,
              content: @Composable ColumnScope.() -> Unit) {
    val t = LocalTokens.current
    Box(modifier.padding(top = 9.dp)) {
        Column(
            Modifier.fillMaxWidth().border(1.dp, borderColor ?: t.line, RoundedCornerShape(t.radius.dp))
                .padding(start = 14.dp, end = 14.dp, top = 16.dp, bottom = 12.dp),
            content = content,
        )
        Txt(title, Modifier.offset(x = 10.dp, y = (-9).dp).background(t.bg).padding(horizontal = 5.dp),
            color = t.legend, size = 13.sp)
    }
}

enum class Kind { NORMAL, PRIMARY, GO, DANGER, DANGER_GO }

@Composable
fun TuiButton(label: String, modifier: Modifier = Modifier, kind: Kind = Kind.NORMAL, enabled: Boolean = true,
              onClick: () -> Unit) {
    val t = LocalTokens.current
    val src = remember { MutableInteractionSource() }
    val pressed by src.collectIsPressedAsState()
    val edge = when (kind) {
        Kind.PRIMARY, Kind.GO -> t.accent
        Kind.DANGER, Kind.DANGER_GO -> t.down
        Kind.NORMAL -> t.lineHi
    }
    val fill = when {
        pressed -> edge
        kind == Kind.GO -> t.accent
        kind == Kind.DANGER_GO -> t.down
        else -> Color.Transparent
    }
    val ink = when {
        pressed || kind == Kind.GO -> t.accentInk
        kind == Kind.DANGER_GO -> t.bg
        kind == Kind.PRIMARY -> t.accent
        kind == Kind.DANGER -> t.down
        else -> t.text
    }
    val shape = RoundedCornerShape(t.radius.dp)
    Box(
        modifier.alpha(if (enabled) 1f else .45f).clip(shape).background(fill).border(1.dp, edge, shape)
            .clickable(interactionSource = src, indication = null, enabled = enabled, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 7.dp)
    ) { Txt(label, color = ink, maxLines = 1) }
}

@Composable
fun Square(color: Color, size: Dp = 8.dp, hollow: Boolean = false, modifier: Modifier = Modifier) {
    Box(if (hollow) modifier.size(size).border(1.dp, color) else modifier.size(size).background(color))
}

private const val SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

@Composable
fun Spinner(color: Color = LocalTokens.current.accent) {
    val i by produceState(0) { while (true) { delay(80); value = (value + 1) % SPIN.length } }
    Txt(SPIN[i].toString(), color = color)
}

/** The app's own icon (from assets/icons), drawn by Coil, SVG included. */
@Composable
fun AppIcon(app: Listing, size: Dp, modifier: Modifier = Modifier, dim: Boolean = false) {
    val loader = LocalImages.current ?: rememberImages()
    AsyncImage(model = app.iconFile?.let { java.io.File(it) } ?: "file:///android_asset/icons/${app.icon}", contentDescription = null, imageLoader = loader,
        modifier = modifier.size(size).alpha(if (dim) .45f else 1f))
}

@Composable
fun rememberImages(): ImageLoader {
    val ctx = LocalContext.current
    return remember { ImageLoader.Builder(ctx).components { add(SvgDecoder.Factory()) }.build() }
}
