package app.novalabs.orbit

import android.content.Intent
import android.provider.Settings
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

// ------------------------------------------------------------------------------------------- shell
@Composable
fun OrbitApp(model: HubModel) {
    val t = LocalTokens.current
    var screen by rememberSaveable { mutableStateOf("home") }
    var reviewing by remember { mutableStateOf<Listing?>(null) }
    BackHandler(enabled = screen != "home") { screen = "home" }

    Column(Modifier.fillMaxSize().background(t.bg).padding(horizontal = 16.dp, vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Txt("[", bold = true); Txt("orbit", color = t.accent, bold = true); Txt("]", bold = true)
            Spacer(Modifier.width(10.dp))
            val n = Catalog.apps.count { it.onAndroid && !it.self }
            val upd = Catalog.apps.count { model.cards[it.id]?.update == true }
            Txt("── $n apps for android" + if (upd > 0) " · $upd update${if (upd > 1) "s" else ""}" else "",
                Modifier.weight(1f), color = t.muted, maxLines = 1)
            TuiButton(if (screen == "home") "⚙" else "×") { screen = if (screen == "home") "settings" else "home" }
        }
        AnimatedContent(targetState = screen, label = "screen", transitionSpec = {
            val dir = if (targetState == "settings") 1 else -1
            (slideInHorizontally(tween(320)) { it / 8 * dir } + fadeIn(tween(320))) togetherWith
                (slideOutHorizontally(tween(260)) { -it / 8 * dir } + fadeOut(tween(200)))
        }) { s ->
            if (s == "home") Home(model) { reviewing = it } else SettingsScreen(model)
        }
    }

    val job = model.job
    if (reviewing != null || job != null) {
        InstallSheet(model, reviewing ?: job!!.app, onClose = { reviewing = null; model.dismissJob() })
    }
}

// ------------------------------------------------------------------------------------------- home
@Composable
private fun Home(model: HubModel, install: (Listing) -> Unit) {
    val t = LocalTokens.current
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        item {
            LegendBox("this device") {
                Txt("${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL} · Android ${Build.VERSION.RELEASE}", bold = true)
                Txt("Apps come from their GitHub releases, are checked, then installed by Android: you confirm each one.",
                    color = t.muted, size = 12.5.sp)
                Spacer(Modifier.height(8.dp))
                TuiButton("check for new versions") { model.refresh(force = true) }
            }
        }
        appCards(model, install)
        item { Spacer(Modifier.height(24.dp)) }
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.appCards(model: HubModel, install: (Listing) -> Unit) {
    items(Catalog.apps, key = { it.id }) { app ->
        var shown by remember { mutableStateOf(false) }
        LaunchedEffect(Unit) { shown = true }
        AnimatedVisibility(shown, enter = fadeIn(tween(350, delayMillis = 70 * Catalog.apps.indexOf(app))) +
            slideInVertically(tween(350, delayMillis = 70 * Catalog.apps.indexOf(app))) { it / 6 }) {
            AppCard(model, app) { install(app) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AppCard(model: HubModel, app: Listing, review: () -> Unit) {
    val t = LocalTokens.current
    val card = model.cards[app.id] ?: Card()
    LegendBox(app.id, Modifier.animateContentSize()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AppIcon(app, 40.dp, dim = !app.onAndroid)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Txt(app.name, bold = true)
                Txt(app.blurb, color = t.muted, size = 12.5.sp)
            }
            Txt(card.installed ?: "", color = t.muted, size = 12.5.sp)
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Square(if (app.onAndroid) t.ok else t.off, 7.dp, hollow = !app.onAndroid)
            Txt(" android" + if (app.onAndroid) "" else " · not yet", color = t.muted, size = 12.5.sp)
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            when {
                !app.onAndroid -> { Square(t.off); Txt("  Not available on Android yet") }
                card.loading && card.release == null && card.installed == null -> { Spinner(); Txt(" checking…", color = t.muted) }
                card.update -> {
                    Txt("UPDATE", Modifier.background(t.accent).padding(horizontal = 4.dp), color = t.accentInk, size = 11.sp, bold = true)
                    Txt(" ${card.release!!.version} is out", maxLines = 1)
                }
                card.installed != null -> { Square(t.ok); Txt("  installed" + if (card.release != null) " · up to date" else "") }
                else -> { Square(t.off); Txt("  not installed" + (card.release?.let { " · latest ${it.version}" } ?: "")) }
            }
        }
        card.release?.takeIf { it.newer.isNotEmpty() && app.onAndroid }?.let {
            // a newer release came out without the Android app (for other systems first)
            Txt("${Versions.clean(it.newer)} is out for other systems; ${it.version} is the newest for Android", color = t.muted, size = 12.5.sp)
        }
        card.release?.takeIf { card.sameApp && card.installed != null && Versions.isNewer(it.version, card.installed) }?.let {
            // a release newer for other systems, with the same Android app in it
            Txt("${it.version} is out for other systems; its Android app is the one you have", color = t.muted, size = 12.5.sp)
        }
        if (card.error.isNotEmpty() && app.onAndroid) {
            Txt(card.error, color = t.warn, size = 12.5.sp)
            if (card.hint.isNotEmpty()) Txt(card.hint, color = t.muted, size = 12.5.sp)
        }
        if (!app.onAndroid) Txt(app.note, color = t.muted, size = 12.5.sp)
        if (app.extra.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Txt(app.extra, color = t.muted, size = 12.5.sp)
        }
        if (app.onAndroid) {
            Spacer(Modifier.height(10.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val busy = model.job?.state == JobState.RUNNING
                when {
                    app.self -> if (card.update) TuiButton("update to ${card.release!!.version}", kind = Kind.PRIMARY, enabled = !busy) { review() }
                    card.installed == null -> TuiButton("install", kind = Kind.PRIMARY, enabled = !busy) { review() }
                    card.update -> TuiButton("update to ${card.release!!.version}", kind = Kind.PRIMARY, enabled = !busy) { review() }
                    else -> TuiButton("reinstall", enabled = !busy && card.release != null) { review() }
                }
                if (card.installed != null && !app.self) {
                    TuiButton("open") { model.open(app) }
                    TuiButton("remove", kind = Kind.DANGER, enabled = !busy) { model.uninstall(app) }
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------- install
@Composable
fun InstallSheet(model: HubModel, app: Listing, onClose: () -> Unit) {
    val t = LocalTokens.current
    val job = model.job?.takeIf { it.app.id == app.id }
    val stage = when {
        job == null -> 0
        job.state == JobState.RUNNING -> 1
        else -> 2
    }
    BackHandler(enabled = stage != 1) { onClose() }
    Dialog(onDismissRequest = { if (stage != 1) onClose() },
        properties = DialogProperties(usePlatformDefaultWidth = false, dismissOnClickOutside = false)) {
        Box(Modifier.fillMaxSize().background(t.bg).padding(16.dp)) {
            LegendBox((if (job == null) "install · " else "${job.title.substringBefore(' ').lowercase()} · ") + app.id,
                Modifier.fillMaxSize(), borderColor = t.lineHi) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    AppIcon(app, 46.dp)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        val card = model.cards[app.id] ?: Card()
                        Txt(job?.title ?: when {
                            card.installed == null -> "Install ${app.name}"
                            card.update -> "Update ${app.name}"
                            else -> "Reinstall ${app.name}"
                        }, bold = true, size = 17.sp)
                        Txt(app.blurb, color = t.muted, size = 12.5.sp)
                    }
                }
                Stepper(stage, failed = job != null && job.state != JobState.DONE && job.state != JobState.RUNNING)
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    AnimatedContent(targetState = stage, label = "stage", transitionSpec = {
                        (slideInHorizontally(tween(340)) { it / 7 } + fadeIn(tween(340))) togetherWith
                            (slideOutHorizontally(tween(260)) { -it / 7 } + fadeOut(tween(220))) using SizeTransform(clip = false)
                    }) { st ->
                        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                            when (st) {
                                0 -> Review(model, app)
                                else -> job?.let { Progress(it) }
                            }
                        }
                    }
                }
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    when (stage) {
                        0 -> {
                            TuiButton("cancel", onClick = onClose)
                            Spacer(Modifier.weight(1f))
                            val card = model.cards[app.id] ?: Card()
                            if (!model.canInstall) TuiButton("allow installing apps", kind = Kind.GO) { model.openInstallPermission() }
                            else TuiButton(if (card.installed == null) "install" else if (card.update) "update" else "reinstall",
                                kind = Kind.GO, enabled = card.release != null) { model.install(app) }
                        }
                        1 -> {
                            TuiButton("cancel", kind = Kind.DANGER) { model.cancelJob() }
                            Spacer(Modifier.weight(1f))
                            val confirm = model.pendingConfirm
                            if (confirm != null) {
                                // Android's "install?" screen couldn't open by itself (Orbit was in the background)
                                val ctx = LocalContext.current
                                TuiButton("confirm install", kind = Kind.GO) {
                                    try { ctx.startActivity(confirm) } catch (e: RuntimeException) { }
                                }
                            }
                        }
                        else -> {
                            TuiButton("close", onClick = onClose)
                            Spacer(Modifier.weight(1f))
                            if (job?.state == JobState.DONE && !app.self) TuiButton("open ${app.name}", kind = Kind.GO) { model.open(app); onClose() }
                            else if (job?.state == JobState.DONE) TuiButton("done", kind = Kind.GO, onClick = onClose)
                            else TuiButton("try again", kind = Kind.GO) { model.dismissJob(); model.install(app) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Stepper(stage: Int, failed: Boolean) {
    val t = LocalTokens.current
    val names = listOf("review", "install", "done")
    val fill by animateFloatAsState(stage / 2f, tween(550, easing = FastOutSlowInEasing), label = "fill")
    Box(Modifier.fillMaxWidth().padding(vertical = 14.dp)) {
        Canvas(Modifier.fillMaxWidth().height(14.dp)) {
            val y = 6.dp.toPx()
            val x0 = size.width / 6f
            val x1 = size.width * 5f / 6f
            drawLine(t.line, Offset(x0, y), Offset(x1, y), 1.dp.toPx())
            drawLine(t.accent, Offset(x0, y), Offset(x0 + (x1 - x0) * fill, y), 1.dp.toPx())
        }
        Row(Modifier.fillMaxWidth()) {
            names.forEachIndexed { i, n ->
                val done = i < stage || (i == 2 && stage == 2 && !failed)
                val cur = i == stage && stage != 2
                val color by animateColorAsState(when {
                    i == 2 && stage == 2 && failed -> t.down
                    done -> t.accent
                    else -> t.bg
                }, tween(300), label = "dot")
                val turn by animateFloatAsState(if (cur) 45f else 0f, spring(dampingRatio = .55f), label = "turn")
                Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.size(13.dp).graphicsLayer { rotationZ = turn; scaleX = if (cur) 1.15f else 1f; scaleY = scaleX }
                        .background(color).border(1.dp, if (cur || done) t.accent else t.lineHi))
                    Spacer(Modifier.height(5.dp))
                    Txt(n, color = if (cur) t.text else t.muted, size = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun Review(model: HubModel, app: Listing) {
    val t = LocalTokens.current
    val card = model.cards[app.id] ?: Card()
    val rel = card.release
    Spacer(Modifier.height(4.dp))
    if (!model.canInstall) {
        Callout("First, Android needs your OK for Orbit to install apps.",
            "Tap “allow installing apps” below, turn on “Allow from this source” for Orbit, then come back here. " +
                "Nothing is downloaded until you do.", t.warn)
        Spacer(Modifier.height(10.dp))
    }
    if (rel == null) {
        if (card.error.isNotEmpty()) {
            Callout(card.error, card.hint, t.down)
        } else Row { Spinner(); Txt(" asking GitHub for the latest release…", color = t.muted) }
        return
    }
    Facts(listOf(
        "this device" to "Android ${Build.VERSION.RELEASE}",
        app.name to (card.installed?.let { "installed $it" } ?: "not installed yet"),
        "latest" to "${rel.tag}${if (rel.prerelease) " · pre-release" else ""}",
        "installing apps" to if (model.canInstall) "allowed for Orbit" else "not allowed yet (see above)",
    ))
    Spacer(Modifier.height(12.dp))
    Txt("Orbit will:")
    val apk = app.apk(rel.assets.keys)?.let { rel.assets[it] }
    val lines = listOfNotNull(
        "Download ${apk?.name ?: "the Android app"}" + (apk?.let { " (${HubModel.size(it.size)})" } ?: "") + " from github.com",
        if (app.signature != null) "Check the release's signature with ${app.name}'s release key (built into Orbit)" else null,
        "Check its SHA-256 against the checksums published with the release, and that it really is ${app.name}",
        "Ask Android to install it: you confirm on Android's own screen",
    )
    lines.forEachIndexed { i, line -> Bullet(line, i) }
    if (apk == null) Callout("This release has no Android app.", "", t.down)
    if (app.note.isNotEmpty()) { Spacer(Modifier.height(10.dp)); Txt(app.note, color = t.muted, size = 12.5.sp) }
}

@Composable
private fun Bullet(text: String, index: Int) {
    val t = LocalTokens.current
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    AnimatedVisibility(shown, enter = fadeIn(tween(350, delayMillis = index * 60)) +
        slideInVertically(tween(350, delayMillis = index * 60)) { it / 3 }) {
        Row(Modifier.padding(vertical = 3.dp)) { Txt("› ", color = t.accent, bold = true); Txt(text) }
    }
}

@Composable
private fun Facts(rows: List<Pair<String, String>>) {
    val t = LocalTokens.current
    Column {
        for ((k, v) in rows) Row(Modifier.padding(vertical = 2.dp)) {
            Txt(k, Modifier.width(118.dp), color = t.muted, maxLines = 1)
            Txt(v, maxLines = 2)
        }
    }
}

@Composable
private fun Callout(text: String, hint: String, edge: Color) {
    val t = LocalTokens.current
    Row(Modifier.fillMaxWidth().padding(top = 10.dp).background(t.raise).height(IntrinsicSize.Min)) {
        Box(Modifier.width(2.dp).fillMaxHeight().background(edge))
        Column(Modifier.padding(10.dp)) {
            Txt(text)
            if (hint.isNotEmpty()) Txt(hint, color = t.muted, size = 12.5.sp)
        }
    }
}

@Composable
private fun Progress(job: InstallJob) {
    val t = LocalTokens.current
    val finished = job.state != JobState.RUNNING
    if (finished) {
        Outcome(job)
        Spacer(Modifier.height(14.dp))
    }
    val target = if (job.state == JobState.DONE) 1f else job.progress
    val p by animateFloatAsState(target, tween(600, easing = FastOutSlowInEasing), label = "progress")
    val barColor by animateColorAsState(when (job.state) {
        JobState.DONE -> t.ok; JobState.RUNNING -> t.accent; else -> t.down
    }, label = "bar")
    Box(Modifier.fillMaxWidth().height(18.dp).border(1.dp, t.lineHi).padding(2.dp)) {
        Box(Modifier.fillMaxWidth(p).height(14.dp).background(barColor))
    }
    Row(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 10.dp)) {
        val cur = job.steps.firstOrNull { it.state == StepState.ACTIVE }
        Txt(when {
            cur != null -> cur.label
            job.state == JobState.DONE -> "finished"
            job.state == JobState.CANCELLED -> "stopped"
            else -> "stopped with a problem"
        }, Modifier.weight(1f), color = t.muted, size = 12.5.sp, maxLines = 1)
        Txt("${(p * 100).toInt()}%", bold = true, size = 12.5.sp)
    }
    Column(Modifier.border(1.dp, t.line)) {
        job.steps.forEach { StepRow(it) }
    }
}

@Composable
private fun StepRow(s: Step) {
    val t = LocalTokens.current
    val bg by animateColorAsState(if (s.state == StepState.ACTIVE) t.raise else Color.Transparent, label = "row")
    Column(Modifier.fillMaxWidth().background(bg).padding(horizontal = 10.dp, vertical = 7.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.width(22.dp), contentAlignment = Alignment.Center) {
                AnimatedContent(targetState = s.state, label = "icon", transitionSpec = {
                    scaleIn(spring(dampingRatio = Spring.DampingRatioMediumBouncy)) + fadeIn() togetherWith fadeOut(tween(100))
                }) { st ->
                    when (st) {
                        StepState.ACTIVE -> Spinner()
                        StepState.DONE -> Txt("✓", color = t.ok, bold = true)
                        StepState.SKIPPED -> Txt("↷", color = t.muted, bold = true)
                        StepState.FAILED -> Txt("✗", color = t.down, bold = true)
                        StepState.PENDING -> Txt("·", color = t.muted)
                    }
                }
            }
            Spacer(Modifier.width(6.dp))
            Txt(s.label, Modifier.weight(1f), color = if (s.state == StepState.PENDING) t.muted else t.text, maxLines = 1)
            if (s.ended > s.started && s.started > 0 && s.state != StepState.ACTIVE)
                Txt("%.1f s".format((s.ended - s.started) / 1000f), color = t.muted, size = 12.sp)
        }
        if (s.detail.isNotEmpty() && s.state != StepState.PENDING && s.state != StepState.DONE)
            Txt(s.detail, Modifier.padding(start = 28.dp), color = t.muted, size = 12.sp, maxLines = 2)
        val prog = s.progress
        if (s.state == StepState.ACTIVE && s.id == "download" && prog != null) {
            val w by animateFloatAsState(prog, tween(250), label = "dl")
            Box(Modifier.padding(start = 28.dp, top = 5.dp).fillMaxWidth().height(2.dp).background(t.line)) {
                Box(Modifier.fillMaxWidth(w).height(2.dp).background(t.accent))
            }
        }
    }
}

/** A ring that draws itself, then a check (or a cross), then a small burst of squares. */
@Composable
private fun Outcome(job: InstallJob) {
    val t = LocalTokens.current
    val ok = job.state == JobState.DONE
    val color = when (job.state) { JobState.DONE -> t.ok; JobState.CANCELLED -> t.warn; else -> t.down }
    val ring = remember { Animatable(0f) }
    val mark = remember { Animatable(0f) }
    val burst = remember { Animatable(0f) }
    LaunchedEffect(job.state) {
        ring.animateTo(1f, tween(700, easing = FastOutSlowInEasing))
        mark.animateTo(1f, tween(320))
        if (ok) burst.animateTo(1f, tween(900, easing = FastOutSlowInEasing))
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Canvas(Modifier.size(150.dp)) {
            val cx = size.width / 2
            val cy = size.height / 2
            val r = 42.dp.toPx()
            drawArc(color, -90f, 360f * ring.value, false, Offset(cx - r, cy - r),
                androidx.compose.ui.geometry.Size(r * 2, r * 2), style = Stroke(3.dp.toPx()))
            val u = r / 42f
            val pts = if (ok) listOf(Offset(-18f, 2f), Offset(-5f, 14f), Offset(19f, -12f))
                      else listOf(Offset(-14f, -14f), Offset(14f, 14f), Offset(14f, -14f), Offset(-14f, 14f))
            val segs = if (ok) listOf(pts[0] to pts[1], pts[1] to pts[2]) else listOf(pts[0] to pts[1], pts[2] to pts[3])
            val total = segs.sumOf { (a, b) -> hypot((b.x - a.x).toDouble(), (b.y - a.y).toDouble()) }.toFloat()
            var left = total * mark.value
            for ((a, b) in segs) {
                if (left <= 0f) break
                val len = hypot(b.x - a.x, b.y - a.y)
                val f = (left / len).coerceAtMost(1f)
                drawLine(color, Offset(cx + a.x * u, cy + a.y * u),
                    Offset(cx + (a.x + (b.x - a.x) * f) * u, cy + (a.y + (b.y - a.y) * f) * u),
                    5.dp.toPx(), cap = StrokeCap.Square)
                left -= len
            }
            if (ok && burst.value > 0f && burst.value < 1f) {
                val colors = listOf(t.accent, t.ok, Color(0xFF000000 or job.app.color))
                for (i in 0 until 14) {
                    val ang = Math.toRadians(i * 360.0 / 14 + if (i % 2 == 1) 8 else 0)
                    val dist = (30f + (if (i % 2 == 1) 28f else 46f) * burst.value) * u + r - 30f * u
                    val sq = 7.dp.toPx() * (1f - .8f * burst.value)
                    drawRect(colors[i % 3].copy(alpha = 1f - burst.value),
                        Offset(cx + (cos(ang) * dist).toFloat() - sq / 2, cy + (sin(ang) * dist).toFloat() - sq / 2),
                        androidx.compose.ui.geometry.Size(sq, sq))
                }
            }
        }
        Txt(when (job.state) {
            JobState.DONE -> job.headline
            JobState.CANCELLED -> "Stopped."
            else -> "That didn't work."
        }, bold = true, size = 17.sp)
        Spacer(Modifier.height(4.dp))
        Txt(if (ok) job.body else job.error, color = t.muted)
        if (!ok && job.hint.isNotEmpty()) Callout(job.hint, "", t.warn)
    }
}

// ------------------------------------------------------------------------------------------- settings
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SettingsScreen(model: HubModel) {
    val t = LocalTokens.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        LegendBox("theme") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                // the phone's own colours first, where it has them (Material You)
                val choices = (deviceTokens()?.let { listOf(Triple(DEVICE_THEME, "material you", it)) } ?: emptyList()) + THEMES
                for ((id, label, tk) in choices) {
                    val on = model.theme == id
                    Column(Modifier.width(140.dp).border(1.dp, if (on) t.accent else t.line).clickable { model.chooseTheme(id) }.padding(8.dp)) {
                        Box(Modifier.fillMaxWidth().height(46.dp).background(tk.bg).border(1.dp, tk.line)) {
                            Box(Modifier.padding(start = 7.dp, top = 8.dp).size(26.dp).background(tk.accent))
                            Box(Modifier.padding(start = 28.dp, top = 28.dp).size(8.dp).background(tk.ok))
                            Column(Modifier.padding(start = 42.dp, top = 10.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                                Box(Modifier.width(60.dp).height(4.dp).background(tk.text))
                                Box(Modifier.width(40.dp).height(4.dp).background(tk.line))
                                Box(Modifier.width(26.dp).height(4.dp).background(tk.accent))
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                        Txt((if (on) "(•) " else "( ) ") + label, size = 12.5.sp, maxLines = 1)
                    }
                }
            }
        }
        LegendBox("update notifications") { NotifyBox() }
        LegendBox("github") { GitHubBox(model) }
        LegendBox("about") {
            Txt("Orbit for Android, by NovaLabs, installs and updates the Android apps of YTM Starcast and Nova, and itself. " +
                "Orbit for desktop (Linux, Windows, macOS) manages the programs on your computer.", color = t.muted, size = 12.5.sp)
        }
        Spacer(Modifier.height(24.dp))
    }
}

/** A notification when an app you have gets an update: one switch per app, and Android's permission where it asks. */
@Composable
private fun NotifyBox() {
    val t = LocalTokens.current
    val ctx = LocalContext.current
    var allowed by remember { mutableStateOf(Updates.allowed(ctx)) }
    var wants by remember { mutableStateOf(Catalog.apps.associate { it.id to Updates.wanted(ctx, it.id) }) }
    val ask = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { allowed = Updates.allowed(ctx) }
    Txt("Orbit looks every few hours, in the background, and says when an app you have gets an update (once per version; " +
        "a release that's new for other systems only doesn't count).", color = t.muted, size = 12.5.sp)
    Spacer(Modifier.height(8.dp))
    if (!allowed) {
        Txt("Notifications are off for Orbit.", color = t.warn, size = 12.5.sp)
        Spacer(Modifier.height(4.dp))
        TuiButton("allow notifications", kind = Kind.PRIMARY) {
            if (Build.VERSION.SDK_INT >= 33) ask.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            else ctx.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
        Spacer(Modifier.height(8.dp))
    }
    for (a in Catalog.apps.filter { it.onAndroid }) {
        val on = wants[a.id] ?: Updates.wanted(ctx, a.id)
        Row(Modifier.fillMaxWidth().clickable { Updates.setWanted(ctx, a.id, !on); wants = wants + (a.id to !on) }.padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Txt(if (on) "[x] " else "[ ] ", color = if (on) t.accent else t.muted)
            Txt(a.name, modifier = Modifier.weight(1f))
            Txt(if (on) "on" else "off", color = t.muted, size = 12.5.sp)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GitHubBox(model: HubModel) {
    val t = LocalTokens.current
    val clip = LocalClipboardManager.current
    var showToken by remember { mutableStateOf(model.manualToken.isNotEmpty() || !GitHubLogin.available) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Square(if (model.token.isNotEmpty()) t.ok else t.off)
        Txt(when {
            model.loginToken.isNotEmpty() -> "  signed in as " + (model.login.takeIf { it.isNotEmpty() }?.let { "@$it" } ?: "you")
            model.manualToken.isNotEmpty() -> "  using your token"
            else -> "  not signed in"
        })
    }
    Txt("Public repositories need no sign-in. Signing in lets Orbit read releases from private ones: it can only read " +
        "repository contents, and stays in Orbit's private storage, sent only to api.github.com.", color = t.muted, size = 12.5.sp)
    Spacer(Modifier.height(10.dp))
    val d = model.device
    if (d != null) {
        Column(Modifier.fillMaxWidth().border(1.dp, t.accent).padding(12.dp)) {
            Txt("On github.com/login/device, enter:", color = t.muted, size = 12.5.sp)
            Txt(d.userCode, color = t.accent, size = 28.sp, bold = true)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                TuiButton("open GitHub", kind = Kind.PRIMARY) { model.openUrl(d.uri) }
                TuiButton("copy code") { clip.setText(AnnotatedString(d.userCode)) }
                TuiButton("cancel") { model.cancelSignIn() }
            }
            Spacer(Modifier.height(8.dp))
            Row { Spinner(); Txt(" waiting for you to approve it on GitHub…", color = t.muted, size = 12.5.sp) }
        }
    } else {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (GitHubLogin.available && model.loginToken.isEmpty()) TuiButton("sign in with GitHub", kind = Kind.PRIMARY) { model.signIn() }
            if (model.loginToken.isNotEmpty()) TuiButton("sign out", kind = Kind.DANGER) { model.signOut() }
            TuiButton("check access") { model.checkAccess() }
            if (GitHubLogin.available) TuiButton(if (showToken) "hide token" else "use a token instead") { showToken = !showToken }
        }
    }
    if (model.loginError.isNotEmpty()) { Spacer(Modifier.height(6.dp)); Txt(model.loginError, color = t.warn, size = 12.5.sp) }
    model.access?.let { acc ->
        Spacer(Modifier.height(10.dp))
        for ((repo, st) in acc) Row(Modifier.padding(vertical = 2.dp)) {
            Txt(repo, Modifier.width(170.dp), color = t.muted, size = 12.5.sp, maxLines = 1)
            Txt(when (st) {
                "no access" -> "✗ can't see it"
                "offline" -> "? couldn't reach GitHub"
                else -> "✓ $st"
            }, color = if (st == "no access") t.warn else t.text, size = 12.5.sp)
        }
        if (acc.values.any { it == "no access" }) Txt("A private repository needs the sign-in (or a token) to have access to it.",
            color = t.muted, size = 12.sp)
    }
    if (showToken) {
        Spacer(Modifier.height(12.dp))
        var text by remember { mutableStateOf(model.manualToken) }
        Txt("Token (instead of signing in)")
        Txt("On github.com: Settings → Developer settings → Fine-grained tokens → Generate. Under Repository access choose " +
            "“Only select repositories” and pick the private ones (the default, “Public repositories”, can't see them). " +
            "Under Permissions → Repository permissions set Contents to Read-only.", color = t.muted, size = 12.5.sp)
        Spacer(Modifier.height(8.dp))
        BasicTextField(text, { text = it }, singleLine = true, visualTransformation = PasswordVisualTransformation(),
            textStyle = TextStyle(fontFamily = Mono, fontSize = 14.sp, color = t.text), cursorBrush = SolidColor(t.accent),
            modifier = Modifier.fillMaxWidth().background(t.raise, RoundedCornerShape(t.radius.dp))
                .border(1.dp, t.line, RoundedCornerShape(t.radius.dp)).padding(8.dp))
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TuiButton("save", kind = Kind.PRIMARY) { model.saveManualToken(text); model.checkAccess() }
            if (model.manualToken.isNotEmpty()) TuiButton("remove", kind = Kind.DANGER) { text = ""; model.saveManualToken("") }
        }
    }
}
