package app.novalabs.orbit

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * The apps Orbit knows. For each: its GitHub repository, its Android package (null when it has no Android
 * version yet), which release file is the Android app, and the checksum list published with it.
 */
data class Listing(
    val id: String,
    val name: String,
    val blurb: String,
    val repo: String,
    val pkgs: List<String>,        // the app's Android package ids, newest first (older ids it had, after)
    val icon: String,              // file in assets/icons
    val iconFile: String? = null,  // the icon from the catalog (a file Orbit keeps), drawn instead when there
    val color: Long,
    val sums: String,              // checksum list in each release
    val signature: String? = null, // signed checksums (Nova), checked against [signingKey]
    val signingKey: String? = null,
    val apk: (Collection<String>) -> String? = { null },
    val note: String = "",
    val extra: String = "",        // shown on the app's card
    val self: Boolean = false,     // Orbit itself: only ever updated, never opened or removed from here
) {
    val onAndroid get() = pkgs.isNotEmpty()
}

/** YTM Starcast's phone app in a release: ytm-starcast-android.apk, else a name with the version; never the
 *  -unshrunk- build. */
fun starcastApk(names: Collection<String>): String? =
    names.firstOrNull { it == "ytm-starcast-android.apk" }
        ?: names.firstOrNull { Regex("""ytm-starcast-android-v[^/]*\.apk""").matches(it) }

object Catalog {
    // packaging/release-key.pub in the Nova repository (ed25519)
    private const val NOVA_KEY = "MCowBQYDK2VwAyEAGk70hAkKUc/oE8yLsTv49yeIkSJkCeuDG0IqxnF69WI="

    /** The apps built into this version: shown as they are until a catalog from Orbit's repository says more. */
    val builtIn = listOf(
        Listing(
            id = "ytm-starcast", name = "YTM Starcast",
            blurb = "YouTube Music companion: a remote for your phone, your Discord status and a better YouTube Music",
            repo = "dash1101/ytm-starcast",
            // YTM Starcast's phone app keeps the id it had as Pocketfader (so it updates in place), then alpha.13's
            pkgs = listOf("app.novalabs.pocketfader", "app.pocketfader.client"),
            icon = "ytm-starcast.svg", color = 0xFF38D9C4,
            sums = "SHA256SUMS.txt",
            apk = { names -> starcastApk(names) },
            note = "The phone app talks to YTM Starcast on your computer: install that with Orbit for desktop.",
            extra = "Includes your Discord status (formerly YTM Beacon): its controls are in YTM Starcast's app.",
        ),
        Listing(
            id = "nova", name = "Nova",
            blurb = "Secure remote for your home server",
            repo = "dash1101/Nova", pkgs = listOf("app.novalabs.nova"), icon = "nova.svg", color = 0xFF5EC4B6,
            sums = "SHA256SUMS", signature = "SHA256SUMS.sig", signingKey = NOVA_KEY,
            apk = { names -> names.firstOrNull { it == "nova-universal.apk" } ?: names.firstOrNull { it.endsWith("-universal.apk") } },
            note = "The Nova app pairs with a Nova server on Linux or Windows: Nova Server on that computer (or sudo nova add) shows the code.",
            extra = "Comes with five home-screen widgets: status, live stats, quick actions, inbox and storage. In Nova: Menu → Home-screen widgets.",
        ),
        Listing(
            id = "orbit", name = "Orbit",
            blurb = "This app",
            repo = "dash1101/orbit", pkgs = listOf("app.novalabs.orbit"), icon = "orbit.svg", color = 0xFFF0668E,
            sums = "SHA256SUMS",
            // orbit-android.apk from 0.3.5 on; releases before named it with the version (orbit-v0.3.4-android.apk)
            apk = { names -> names.firstOrNull { it == "orbit-android.apk" } ?: names.firstOrNull { Regex("""orbit-v[^/]*-android\.apk""").matches(it) } },
            note = "Orbit updates itself from its GitHub releases.",
            self = true,
        ),
    )

    /** The apps shown: [builtIn], with what Orbit's catalog (packaging/catalog.json in its repository) says. */
    var apps by mutableStateOf(builtIn)
        private set

    internal fun reset() { apps = builtIn }   // (tests)

    private val ID = Regex("""[a-z0-9][a-z0-9-]{0,39}""")
    private val REPO = Regex("""[\w.-]{1,100}/[\w.-]{1,100}""")
    private val PKG = Regex("""[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+""")
    private val COLOR = Regex("""#[0-9a-fA-F]{6}""")

    /** Release file names (or "re:" patterns) that are the app, in order: which one a release has. */
    private fun matcher(patterns: List<String>): ((Collection<String>) -> String?)? {
        val tests: List<(String) -> Boolean> = patterns.map { p ->
            if (p.startsWith("re:")) {
                val r = runCatching { Regex(p.substring(3)) }.getOrNull() ?: return null
                val test: (String) -> Boolean = { n -> r.matches(n) }
                test
            } else {
                val test: (String) -> Boolean = { n -> n == p }
                test
            }
        }
        return { names -> tests.firstNotNullOfOrNull { t -> names.firstOrNull(t) } }
    }

    private fun JSONObject.text(key: String, max: Int): String? = optString(key, "").takeIf { it.isNotEmpty() && it.length <= max }
    private fun JSONArray?.texts(): List<String> = if (this == null) emptyList() else (0 until length()).map { optString(it, "") }

    /**
     * Apply a catalog: names, descriptions, colours and icons, repositories, and on Android the package ids, the
     * release file that is the app and its checksum list. Anything it says that doesn't look right is left as built
     * in, and signing keys only ever come from [builtIn]. Icons are written to [iconDir]. True when it was a catalog.
     */
    fun apply(text: String, iconDir: File): Boolean {
        val root = runCatching { JSONObject(text) }.getOrNull() ?: return false
        val list = root.optJSONArray("apps") ?: return false
        iconDir.mkdirs()
        val out = mutableListOf<Listing>()
        for (k in 0 until list.length()) {
            val e = list.optJSONObject(k) ?: continue
            val id = e.optString("id").takeIf { ID.matches(it) } ?: continue
            if (out.any { it.id == id }) continue
            val base = builtIn.firstOrNull { it.id == id }
            val android = e.optJSONObject("android")
            val icon = e.optString("icon", "").trim().takeIf { it.startsWith("<svg") && it.length <= 65536 }?.let { svg ->
                runCatching { File(iconDir, "$id.svg").apply { writeText(svg) }.absolutePath }.getOrNull()
            }
            val pkgs = (android?.optJSONArray("pkgs")).texts().filter { PKG.matches(it) }
            val apk = (android?.optJSONArray("apk")).texts().filter { it.isNotEmpty() && it.length <= 200 }.let { if (it.isEmpty()) null else matcher(it) }
            val color = e.optString("color").takeIf { COLOR.matches(it) }?.let { 0xFF000000 or it.substring(1).toLong(16) }
            val l = (base ?: Listing(id = id, name = id, blurb = "", repo = "", pkgs = emptyList(), icon = "", color = 0xFF8C8C93, sums = "SHA256SUMS"))
            val merged = l.copy(
                name = e.text("name", 60) ?: l.name,
                blurb = e.text("blurb", 300) ?: l.blurb,
                repo = e.optString("repo").takeIf { REPO.matches(it) } ?: l.repo,
                color = color ?: l.color,
                iconFile = icon ?: l.iconFile,
                pkgs = if (pkgs.isNotEmpty()) pkgs else l.pkgs,
                apk = apk ?: l.apk,
                sums = android?.text("sums", 80)?.takeIf { !it.contains('/') } ?: l.sums,
                note = android?.text("note", 400) ?: l.note,
                extra = android?.text("extra", 400) ?: l.extra,
            )
            // a new app needs a repository and an Android file to be of use here
            if (base == null && (merged.repo.isEmpty() || !merged.onAndroid || apk == null)) continue
            out += merged
        }
        if (out.isEmpty()) return false
        out += builtIn.filter { b -> out.none { it.id == b.id } }   // (an app the catalog leaves out stays as built in)
        apps = out
        return true
    }
}
