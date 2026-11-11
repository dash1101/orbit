package app.novalabs.orbit

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

    val apps = listOf(
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
}
