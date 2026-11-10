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
    val pkg: String?,
    val icon: String,              // file in assets/icons
    val color: Long,
    val sums: String,              // checksum list in each release
    val signature: String? = null, // signed checksums (Nova), checked against [signingKey]
    val signingKey: String? = null,
    val apk: (Collection<String>) -> String? = { null },
    val note: String = "",
) {
    val onAndroid get() = pkg != null
}

object Catalog {
    // packaging/release-key.pub in the Nova repository (ed25519)
    private const val NOVA_KEY = "MCowBQYDK2VwAyEAGk70hAkKUc/oE8yLsTv49yeIkSJkCeuDG0IqxnF69WI="

    val apps = listOf(
        Listing(
            id = "pocketfader", name = "Pocketfader",
            blurb = "Control your computer's volume and music from your phone",
            repo = "dash1101/pocketfader", pkg = "app.pocketfader.client", icon = "pocketfader.svg", color = 0xFFA78BFA,
            sums = "SHA256SUMS.txt",
            apk = { names -> names.firstOrNull { Regex("""pocketfader-android-v[^/]*\.apk""").matches(it) } },
            note = "The phone app talks to Pocketfader on your computer: install that with Orbit for desktop.",
        ),
        Listing(
            id = "nova", name = "Nova",
            blurb = "Secure remote for your Linux home server",
            repo = "dash1101/Nova", pkg = "app.novalabs.nova", icon = "nova.svg", color = 0xFF5EC4B6,
            sums = "SHA256SUMS", signature = "SHA256SUMS.sig", signingKey = NOVA_KEY,
            apk = { names -> names.firstOrNull { it == "nova-universal.apk" } ?: names.firstOrNull { it.endsWith("-universal.apk") } },
            note = "The Nova app pairs with a Nova server on Linux (sudo nova add shows the code).",
        ),
        Listing(
            id = "ytm-beacon", name = "YTM Beacon",
            blurb = "Show what you play on YouTube Music in your Discord status",
            repo = "dash1101/ytm-beacon", pkg = null, icon = "ytm-beacon.png", color = 0xFFF472B6,
            sums = "checksums.txt",
            note = "YTM Beacon runs on your computer, next to the Discord app. It isn't available on Android yet.",
        ),
    )
}
