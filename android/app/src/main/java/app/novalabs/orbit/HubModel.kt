package app.novalabs.orbit

import android.app.Application
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File

/** One app's line on the home screen. */
/** [sameApp]: the release's Android app is the one installed (a release can be newer for other systems only, with the
 *  same app file in it, as Nova's server-only releases are). */
data class Card(val installed: String? = null, val release: Release? = null, val error: String = "",
                val hint: String = "", val loading: Boolean = false, val sameApp: Boolean = false) {
    val update get() = installed != null && release != null && !sameApp && Versions.isNewer(release.version, installed)
}

enum class StepState { PENDING, ACTIVE, DONE, SKIPPED, FAILED }

class Step(val id: String, val label: String) {
    var state by mutableStateOf(StepState.PENDING)
    var detail by mutableStateOf("")
    var progress by mutableStateOf<Float?>(null)
    var started = 0L
    var ended = 0L
}

enum class JobState { RUNNING, DONE, FAILED, CANCELLED }

class InstallJob(val app: Listing, val title: String, val steps: List<Step>) {
    var state by mutableStateOf(JobState.RUNNING)
    var error by mutableStateOf("")
    var hint by mutableStateOf("")
    var headline by mutableStateOf("")
    var body by mutableStateOf("")
    val log = mutableStateListOf<String>()
    @Volatile var cancelled = false
    val progress: Float
        get() = steps.sumOf {
            when (it.state) {
                StepState.DONE, StepState.SKIPPED -> 1.0
                StepState.ACTIVE -> (it.progress ?: 0f).toDouble()
                else -> 0.0
            }
        }.toFloat() / steps.size
}

class HubModel(app: Application) : AndroidViewModel(app) {
    private val ctx: Context get() = getApplication<Application>()
    private val prefs = app.getSharedPreferences("orbit", Context.MODE_PRIVATE)
    private val phone = Phone(app)   // (before init, which uses it)

    val cards = mutableStateMapOf<String, Card>()
    var theme by mutableStateOf(prefs.getString("theme", "tui") ?: "tui")
        private set
    var job by mutableStateOf<InstallJob?>(null)
        private set
    private var running: Job? = null
    private val fetched = HashMap<String, Long>()

    // GitHub: Orbit's own sign-in, or a token typed in by hand. Only ever sent to api.github.com.
    var loginToken by mutableStateOf(prefs.getString("loginToken", "") ?: "")
        private set
    var login by mutableStateOf(prefs.getString("login", "") ?: "")
        private set
    var manualToken by mutableStateOf(prefs.getString("token", "") ?: "")
        private set
    val token: String get() = loginToken.ifBlank { manualToken }
    var device by mutableStateOf<GitHubLogin.Device?>(null)
        private set
    var loginError by mutableStateOf("")
        private set
    private var loginJob: Job? = null
    var access by mutableStateOf<Map<String, String>?>(null)
        private set

    /** Whether Android lets Orbit install apps yet ("install unknown apps"); re-read whenever Orbit comes back. */
    var canInstall by mutableStateOf(true)
        private set
    /** Android's "install this app?" screen, when it couldn't open by itself. */
    var pendingConfirm by mutableStateOf<Intent?>(null)
        private set

    // Orbit's catalog (names, icons, which release file is each app): the copy kept from last time, then GitHub's
    private val catalogFile get() = File(ctx.filesDir, "catalog.json")
    private val catalogIcons get() = File(ctx.filesDir, "catalog-icons")

    init {
        runCatching { if (catalogFile.exists()) Catalog.apply(catalogFile.readText(), catalogIcons) }
        refresh()
        runCatching { Updates.schedule(app) }   // the update check in the background (see Updates)
    }

    /** At most every 12 hours (or when asked): the catalog from Orbit's repository, kept for when GitHub can't be reached. */
    private fun refreshCatalog(force: Boolean) {
        val last = prefs.getLong("catalogFetched", 0L)
        if (!force && System.currentTimeMillis() - last < 12 * 3_600_000L) return
        viewModelScope.launch {
            val text = runCatching { withContext(Dispatchers.IO) { GitHub.raw("dash1101/orbit", "packaging/catalog.json", token) } }.getOrNull()
                ?: return@launch
            val ok = runCatching { withContext(Dispatchers.IO) { Catalog.apply(text, catalogIcons) } }.getOrDefault(false)
            if (ok) {
                withContext(Dispatchers.IO) { runCatching { catalogFile.writeText(text) } }
                prefs.edit().putLong("catalogFetched", System.currentTimeMillis()).apply()
                refresh(force = false) // apps the catalog added get their cards
            }
        }
    }

    fun chooseTheme(id: String) { theme = id; prefs.edit().putString("theme", id).apply() }
    fun saveManualToken(t: String) {
        manualToken = t.trim(); prefs.edit().putString("token", manualToken).apply(); access = null; refresh(force = true)
    }

    // ---- GitHub sign-in (device flow) -------------------------------------------------------------
    fun signIn() {
        if (!GitHubLogin.available || loginJob?.isActive == true) return
        loginError = ""
        loginJob = viewModelScope.launch {
            try {
                val d = withContext(Dispatchers.IO) { GitHubLogin.start() }
                device = d
                openUrl(d.uri)
                while (true) {
                    delay(d.interval * 1000L)
                    when (val p = withContext(Dispatchers.IO) { GitHubLogin.poll(d) }) {
                        is GitHubLogin.Poll.Pending -> continue
                        is GitHubLogin.Poll.Failed -> { loginError = p.message; break }
                        is GitHubLogin.Poll.Token -> {
                            val who = withContext(Dispatchers.IO) { GitHubLogin.user(p.token) }
                            loginToken = p.token
                            login = who
                            prefs.edit().putString("loginToken", p.token).putString("login", who).apply()
                            access = null
                            refresh(force = true)
                            break
                        }
                    }
                }
            } catch (e: Problem) {
                loginError = e.message ?: "Couldn't sign in."
            } finally {
                device = null
            }
        }
    }

    fun cancelSignIn() { loginJob?.cancel(); device = null }

    fun signOut() {
        loginToken = ""; login = ""
        prefs.edit().remove("loginToken").remove("login").apply()
        access = null
        refresh(force = true)
    }

    /** What the current sign-in or token can read, repository by repository. */
    fun checkAccess() {
        viewModelScope.launch {
            access = withContext(Dispatchers.IO) {
                Catalog.apps.filter { it.onAndroid }.associate { it.repo to GitHub.access(it.repo, token) }
            }
        }
    }

    fun openUrl(url: String) {
        try {
            ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: RuntimeException) { }
    }

    /** Android's "install unknown apps" page for Orbit. */
    fun openInstallPermission() {
        if (Build.VERSION.SDK_INT < 26) return
        ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ---- what's installed and what's out ----------------------------------------------------------
    fun installedVersion(a: Listing): String? = phone.installedVersion(a)
    private fun installedPackage(a: Listing): String? = phone.installedPackage(a)

    }

    /** Re-reads installed versions (cheap) and, at most every 10 minutes unless forced, the latest releases. */
    fun refresh(force: Boolean = false) {
        canInstall = Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()
        refreshCatalog(force)
        for (a in Catalog.apps) {
            val old = cards[a.id] ?: Card()
            cards[a.id] = old.copy(installed = installedVersion(a))
            if (!a.onAndroid) continue
            val fresh = System.currentTimeMillis() - (fetched[a.id] ?: 0L) < 10 * 60_000
            if (fresh && !force && old.release != null) continue
            cards[a.id] = cards[a.id]!!.copy(loading = true)
            viewModelScope.launch {
                val r = runCatching { withContext(Dispatchers.IO) { GitHub.latest(a.repo, token) { a.apk(it) != null } } }
                val c = cards[a.id] ?: Card()
                // a release that looks newer may carry the very app that's installed (newer for other systems only)
                val same = r.getOrNull()?.takeIf { c.installed != null && Versions.isNewer(it.version, c.installed) }
                    ?.let { rel -> runCatching { withContext(Dispatchers.IO) { phone.sameAsInstalled(a, rel) } }.getOrDefault(false) } ?: false
                cards[a.id] = r.fold(
                    { fetched[a.id] = System.currentTimeMillis(); (cards[a.id] ?: c).copy(release = it, error = "", hint = "", loading = false, sameApp = same) },
                    { e -> (cards[a.id] ?: c).copy(error = e.message ?: "couldn't check", hint = (e as? Problem)?.hint ?: "", loading = false) })
            }
        }
    }

    // ---- open / remove ----------------------------------------------------------------------------
    fun open(a: Listing): Boolean {
        val i = installedPackage(a)?.let { ctx.packageManager.getLaunchIntentForPackage(it) } ?: return false
        ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return true
    }

    fun uninstall(a: Listing) {
        val pkg = installedPackage(a) ?: return
        @Suppress("DEPRECATION")
        ctx.startActivity(Intent(Intent.ACTION_UNINSTALL_PACKAGE, Uri.parse("package:$pkg"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ---- installing -------------------------------------------------------------------------------
    fun dismissJob() { if (job?.state != JobState.RUNNING) job = null }

    fun cancelJob() { job?.cancelled = true; running?.cancel() }

    fun install(a: Listing) {
        if (job?.state == JobState.RUNNING || !a.onAndroid) return
        val before = installedVersion(a)
        val steps = buildList {
            add(Step("allow", "Allow Orbit to install apps"))   // first: nothing is downloaded before this
            add(Step("find", "Find the latest release"))
            if (a.signature != null) add(Step("signature", "Check the release's signature"))
            add(Step("download", "Download"))
            add(Step("verify", "Check the download"))
            add(Step("install", "Install"))
        }
        val title = when {
            before == null -> "Install ${a.name}"
            cards[a.id]?.update == true -> "Update ${a.name}"
            else -> "Reinstall ${a.name}"
        }
        val j = InstallJob(a, title, steps)
        job = j
        val update = cards[a.id]?.update == true
        running = viewModelScope.launch { perform(j, before, update) }
    }

    private fun Step.begin() { state = StepState.ACTIVE; started = System.currentTimeMillis() }
    private fun Step.end(s: StepState = StepState.DONE) {
        state = s; ended = System.currentTimeMillis(); if (s == StepState.DONE) progress = 1f
    }

    private suspend fun perform(j: InstallJob, before: String?, update: Boolean = false) {
        val a = j.app
        val dir = File(ctx.cacheDir, "downloads").apply { mkdirs() }
        var file: File? = null
        var current: Step? = null
        fun step(id: String) = j.steps.first { it.id == id }.also { current = it; it.begin() }
        try {
            // 1 · permission to install (before anything is downloaded)
            var s = step("allow")
            if (Build.VERSION.SDK_INT >= 26 && !ctx.packageManager.canRequestPackageInstalls()) {
                s.detail = "turn on “Allow from this source”, then come back"
                ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                withTimeoutOrNull(10 * 60_000L) {
                    while (!ctx.packageManager.canRequestPackageInstalls()) delay(500)
                } ?: throw Problem("Orbit wasn't allowed to install apps.", "Allow it in Settings, then try again.")
                s.end()
            } else {
                s.detail = "already allowed"
                s.end(StepState.SKIPPED)
            }

            // 2 · release
            s = step("find")
            s.detail = "asking GitHub"
            val rel = withContext(Dispatchers.IO) { GitHub.latest(a.repo, token) { a.apk(it) != null } }
            val apkName = a.apk(rel.assets.keys) ?: throw Problem("The ${rel.tag} release has no Android app.")
            val apk = rel.assets[apkName]!!
            val sumsAsset = rel.assets[a.sums] ?: throw Problem("The ${rel.tag} release has no checksum list, so Orbit won't install it.")
            s.detail = "${rel.tag} · $apkName"
            j.log += "Latest release: ${rel.tag}"
            s.end()
            val sums = withContext(Dispatchers.IO) { GitHub.text(sumsAsset, a.repo, token) }

            // 3 · signature (Nova)
            if (a.signature != null) {
                s = step("signature")
                val sigAsset = rel.assets[a.signature] ?: throw Problem("This release isn't signed, so Orbit won't install it.")
                val sig = withContext(Dispatchers.IO) { GitHub.text(sigAsset, a.repo, token) }
                if (!Checks.ed25519(a.signingKey!!, sums.toByteArray(), sig))
                    throw Problem("The release's signature doesn't match ${a.name}'s release key, so it won't be installed.",
                        "Don't install this download. Check the ${a.name} repository for news.")
                s.detail = "signed with ${a.name}'s release key"
                s.end()
            }

            // 4 · download
            s = step("download")
            s.detail = "$apkName · ${size(apk.size)}"
            val out = File(dir, "${a.id}.apk").also { file = it; it.delete() }
            val sha = withContext(Dispatchers.IO) {
                GitHub.download(apk, a.repo, token, out, { got, total ->
                    s.progress = if (total > 0) got.toFloat() / total else null
                    s.detail = "${size(got)} / ${size(total)}"
                }, { j.cancelled })
            }
            s.end()

            // 5 · checks
            s = step("verify")
            val expected = Checks.parseSums(sums)[apkName]
                ?: throw Problem("The release doesn't list a checksum for $apkName, so it won't be installed.")
            if (expected != sha) throw Problem("$apkName doesn't match its published checksum, so it won't be installed.",
                "The download may be damaged. Try again.")
            val archived = if (Build.VERSION.SDK_INT >= 33)
                ctx.packageManager.getPackageArchiveInfo(out.path, PackageManager.PackageInfoFlags.of(0))
            else @Suppress("DEPRECATION") ctx.packageManager.getPackageArchiveInfo(out.path, 0)
            if (archived == null || archived.packageName !in a.pkgs) throw Problem("The download isn't ${a.name}'s app, so it won't be installed.")
            s.detail = "SHA-256 ${sha.take(12)}… · ${archived.versionName}"
            s.end()

            // an update whose app isn't newer than the one installed (a release newer for other systems only): done
            val have = installedPackage(a)?.let { phone.info(it) }
            if (update && have != null && phone.code(archived) <= phone.code(have)) {
                s = step("install")
                s.detail = "already installed: ${archived.versionName}"
                s.end(StepState.SKIPPED)
                phone.rememberSame(a, rel)
                j.headline = "${a.name} is already up to date."
                j.body = "The ${rel.tag} release's Android app is ${archived.versionName}, the one you have: that release is new for other systems."
                j.state = JobState.DONE
                return
            }

            // 6 · install
            s = step("install")
            s.detail = "Android asks you to confirm"
            val result = installApk(out, archived.packageName)
            if (result.status != PackageInstaller.STATUS_SUCCESS) throw installProblem(result, a)
            s.detail = "installed ${archived.versionName}"
            s.end()

            j.headline = if (before == null) "${a.name} is installed." else "${a.name} is updated."
            if (a.self) j.body = "Orbit restarts on the new version."
            if (!a.self) j.body = a.note
            j.state = JobState.DONE
        } catch (e: CancellationException) {
            current?.end(StepState.FAILED)
            j.error = "Stopped. Nothing was installed."
            j.state = JobState.CANCELLED
        } catch (e: Problem) {
            current?.end(StepState.FAILED)
            j.error = if (j.cancelled) "Stopped. Nothing was installed." else e.message ?: "Something went wrong."
            j.hint = if (j.cancelled) "" else e.hint
            j.state = if (j.cancelled) JobState.CANCELLED else JobState.FAILED
        } catch (e: Exception) {
            current?.end(StepState.FAILED)
            j.error = "Something unexpected went wrong: ${e.message ?: e.javaClass.simpleName}"
            j.state = JobState.FAILED
        } finally {
            file?.delete()
            refresh()
        }
    }

    private suspend fun installApk(apk: File, pkg: String): InstallResult = withContext(Dispatchers.IO) {
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(pkg)
        params.setSize(apk.length())
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        val id = installer.createSession(params)
        // listen before committing, so the result can't be missed
        val wait = async(start = CoroutineStart.UNDISPATCHED) {
            InstallEvents.results.first { it.session == id && it.status != PackageInstaller.STATUS_PENDING_USER_ACTION }
        }
        // Android's confirmation screen, kept so the sheet can offer it if it couldn't open by itself
        val watch = launch(start = CoroutineStart.UNDISPATCHED) {
            InstallEvents.results.collect {
                if (it.session == id && it.status == PackageInstaller.STATUS_PENDING_USER_ACTION) pendingConfirm = it.confirm
            }
        }
        installer.openSession(id).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val intent = Intent(ctx, InstallReceiver::class.java).setPackage(ctx.packageName)
            session.commit(PendingIntent.getBroadcast(ctx, id, intent, flags).intentSender)
        }
        try {
            wait.await()
        } finally {
            watch.cancel()
            pendingConfirm = null
        }
    }

    private fun installProblem(r: InstallResult, a: Listing) = when (r.status) {
        PackageInstaller.STATUS_FAILURE_ABORTED -> Problem("The install was cancelled, so nothing changed.")
        PackageInstaller.STATUS_FAILURE_CONFLICT, PackageInstaller.STATUS_FAILURE_INCOMPATIBLE ->
            Problem("Android won't install this over the ${a.name} you have: they're signed with different keys.",
                "Remove ${a.name} first (your settings in it are lost), then install again.")
        PackageInstaller.STATUS_FAILURE_STORAGE -> Problem("There isn't enough space on this device.")
        PackageInstaller.STATUS_FAILURE_BLOCKED -> Problem("This device blocks installing apps from Orbit.",
            "A device policy or parental control may be on.")
        else -> Problem("Android couldn't install ${a.name}" + (r.message?.let { ": $it" } ?: "."))
    }

    companion object {
        fun size(n: Long): String = when {
            n >= 1_000_000 -> "%.1f MB".format(n / 1e6)
            n >= 1_000 -> "${n / 1000} KB"
            else -> "$n B"
        }
    }
}
