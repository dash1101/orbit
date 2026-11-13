package app.novalabs.orbit

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import java.io.File
import java.security.MessageDigest

/**
 * The apps Orbit lists as they are on this phone: which is installed (under which package id, at which version), and
 * whether a release's Android app is the very one installed, which a release that is newer for other systems only
 * carries (Nova's server-only releases). Used by the home screen and by the update check in the background.
 */
class Phone(private val ctx: Context) {
    private val prefs = ctx.getSharedPreferences("orbit", Context.MODE_PRIVATE)

    /** Orbit's GitHub sign-in, or a token typed in by hand (only ever sent to api.github.com). */
    val token: String get() = (prefs.getString("loginToken", "") ?: "").ifBlank { prefs.getString("token", "") ?: "" }

    fun info(pkg: String): PackageInfo? = try {
        if (Build.VERSION.SDK_INT >= 33) ctx.packageManager.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        else @Suppress("DEPRECATION") ctx.packageManager.getPackageInfo(pkg, 0)
    } catch (e: PackageManager.NameNotFoundException) {
        null
    }

    fun code(p: PackageInfo): Long = if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else @Suppress("DEPRECATION") p.versionCode.toLong()

    /** The package id of the copy that's installed (an app may be installed under an older id). */
    fun installedPackage(a: Listing): String? = a.pkgs.firstOrNull { info(it) != null }

    fun installedVersion(a: Listing): String? = a.pkgs.firstNotNullOfOrNull { info(it)?.let { p -> p.versionName ?: "?" } }

    /** SHA-256 of the installed app's file, worked out once per installed version (kept in Orbit's settings). */
    private fun installedSha(a: Listing): String? {
        val pkg = installedPackage(a) ?: return null
        val p = info(pkg) ?: return null
        val key = "apksha:$pkg"
        val kept = prefs.getString(key, "") ?: ""
        if (kept.startsWith("${p.lastUpdateTime}:")) return kept.substringAfter(":")
        val file = p.applicationInfo?.sourceDir?.let { File(it) }?.takeIf { it.canRead() } ?: return null
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { ins -> val buf = ByteArray(1 shl 16); while (true) { val n = ins.read(buf); if (n < 0) break; md.update(buf, 0, n) } }
        val sha = md.digest().joinToString("") { "%02x".format(it) }
        prefs.edit().putString(key, "${p.lastUpdateTime}:$sha").apply()
        return sha
    }

    /** Is [rel]'s Android app the one installed? (Its published checksum against the installed file's; the answer is
     *  kept per release and installed version, so the checksum list is only fetched once.) */
    fun sameAsInstalled(a: Listing, rel: Release): Boolean {
        val pkg = installedPackage(a) ?: return false
        val stamp = info(pkg)?.lastUpdateTime ?: return false
        val key = "same:${a.id}"
        val kept = prefs.getString(key, "") ?: ""
        if (kept.startsWith("${rel.tag}|$stamp|")) return kept.endsWith("|true")
        val apkName = a.apk(rel.assets.keys) ?: return false
        val sumsAsset = rel.assets[a.sums] ?: return false
        val expected = Checks.parseSums(GitHub.text(sumsAsset, a.repo, token))[apkName] ?: return false
        val same = expected.equals(installedSha(a) ?: return false, ignoreCase = true)
        prefs.edit().putString(key, "${rel.tag}|$stamp|$same").apply()
        return same
    }

    /** Remember that [rel]'s app is the one installed (found out by installing it). */
    fun rememberSame(a: Listing, rel: Release) {
        val stamp = installedPackage(a)?.let { info(it) }?.lastUpdateTime ?: return
        prefs.edit().putString("same:${a.id}", "${rel.tag}|$stamp|true").apply()
    }

    /** Is [rel] an update to the installed [a]: a newer version, with an Android app other than the one installed? */
    fun isUpdate(a: Listing, rel: Release): Boolean {
        val have = installedVersion(a) ?: return false
        return Versions.isNewer(rel.version, have) && !sameAsInstalled(a, rel)
    }
}
