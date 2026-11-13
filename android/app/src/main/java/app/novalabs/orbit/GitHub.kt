package app.novalabs.orbit

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

data class Asset(val name: String, val url: String, val api: String, val size: Long)
data class Release(val tag: String, val published: String, val prerelease: Boolean, val page: String,
                   val assets: Map<String, Asset>) {
    val version get() = Versions.clean(tag)
}

/** A problem the person can understand, with how to fix it when there's something to do. */
class Problem(message: String, val hint: String = "") : Exception(message)

/**
 * GitHub releases. A token (from settings) lets private repositories work: it is only ever sent to
 * api.github.com, never to the file host a download is redirected to.
 */
object GitHub {
    private const val API = "https://api.github.com"
    private const val UA = "orbit-android"

    /** GitHub said no: its hourly limit used up (said so in its headers, with when it resets), or the sign-in. */
    private fun refused(c: HttpURLConnection, token: String?): Problem {
        val reset = c.getHeaderField("X-RateLimit-Reset")?.toLongOrNull()
        if (c.getHeaderField("X-RateLimit-Remaining") == "0" && reset != null) {
            val minutes = ((reset - System.currentTimeMillis() / 1000) / 60 + 1).coerceAtLeast(1)
            return Problem("GitHub's limit for requests is used up for now.", if (token.isNullOrBlank())
                "Without a sign-in GitHub answers 60 requests an hour per network. Try again in about $minutes min, or sign " +
                "in with GitHub in Orbit's settings." else "Try again in about $minutes min.")
        }
        return Problem("GitHub refused the request.", if (token.isNullOrBlank())
            "Without a sign-in GitHub answers about 60 requests an hour per network: wait a few minutes, or sign in with " +
            "GitHub in Orbit's settings." else "Your sign-in or token may have expired: sign in again in Orbit's settings.")
    }

    fun latest(repo: String, token: String?): Release {
        val c = open("$API/repos/$repo/releases?per_page=10", token, "application/vnd.github+json")
        val body = try {
            when (c.responseCode) {
                200 -> c.inputStream.bufferedReader().readText()
                404 -> throw Problem("Couldn't find $repo on GitHub.", if (token.isNullOrBlank())
                    "If it's private, sign in with GitHub in Orbit's settings." else
                    "Your sign-in or token can't see it. Settings → check access shows what it can read. A fine-grained " +
                    "token needs Repository access → this repository, and Permissions → Contents: Read-only.")
                401, 403 -> throw refused(c, token)
                else -> throw Problem("GitHub answered ${c.responseCode}.", "Try again in a moment.")
            }
        } catch (e: IOException) {
            throw Problem("Couldn't reach GitHub.", "Check your internet connection.")
        } finally {
            c.disconnect()
        }
        val arr = JSONArray(body)
        val rels = (0 until arr.length()).map { arr.getJSONObject(it) }.filter { !it.optBoolean("draft") }
        // GitHub's list isn't reliably newest-first: take the highest version.
        val byVersion = Comparator<org.json.JSONObject> { a, b ->
            Versions.compare(Versions.parse(a.optString("tag_name")) ?: emptyList(),
                Versions.parse(b.optString("tag_name")) ?: emptyList())
        }
        val best = rels.maxWithOrNull(byVersion.thenBy { it.optString("published_at") })
            ?: throw Problem("$repo has no releases yet.")
        val assets = best.optJSONArray("assets") ?: JSONArray()
        val map = (0 until assets.length()).map { assets.getJSONObject(it) }.associate {
            it.getString("name") to Asset(it.getString("name"), it.optString("browser_download_url"),
                it.optString("url"), it.optLong("size"))
        }
        return Release(best.optString("tag_name"), best.optString("published_at"), best.optBoolean("prerelease"),
            best.optString("html_url"), map)
    }

    /** What [token] can see of [repo]: "public", "private", "no access" or "offline". */
    fun access(repo: String, token: String?): String {
        val c = open("$API/repos/$repo", token, "application/vnd.github+json")
        return try {
            when (c.responseCode) {
                200 -> if (JSONObject(c.inputStream.bufferedReader().readText()).optBoolean("private")) "private" else "public"
                401, 403, 404 -> "no access"
                else -> "offline"
            }
        } catch (e: IOException) {
            "offline"
        } finally {
            c.disconnect()
        }
    }

    private fun open(url: String, token: String?, accept: String): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 10_000
        c.readTimeout = 30_000
        c.instanceFollowRedirects = false
        c.setRequestProperty("User-Agent", UA)
        c.setRequestProperty("Accept", accept)
        if (!token.isNullOrBlank() && url.startsWith("$API/")) c.setRequestProperty("Authorization", "Bearer $token")
        return c
    }

    /** Opens a release file, following GitHub's redirect without the token. */
    private fun openAsset(a: Asset, repo: String, token: String?): HttpURLConnection {
        var url = if (!token.isNullOrBlank() && a.api.startsWith("$API/repos/$repo/releases/assets/")) a.api else a.url
        if (url == a.url && !url.startsWith("https://github.com/$repo/releases/download/"))
            throw Problem("${a.name} isn't hosted where Orbit expects, so it won't be downloaded.")
        repeat(6) {
            val c = open(url, token, if (url == a.api) "application/octet-stream" else "*/*")
            val code = c.responseCode
            if (code in 300..399) {
                val next = c.getHeaderField("Location") ?: throw Problem("GitHub sent a broken redirect.")
                c.disconnect()
                if (!next.startsWith("https://")) throw Problem("GitHub redirected to an insecure address.")
                url = next
                return@repeat
            }
            if (code != 200) {
                c.disconnect()
                throw Problem("The download of ${a.name} failed (GitHub answered $code).", "Try again in a moment.")
            }
            return c
        }
        throw Problem("Too many redirects downloading ${a.name}.")
    }

    fun text(a: Asset, repo: String, token: String?): String {
        val c = openAsset(a, repo, token)
        try {
            return c.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } catch (e: IOException) {
            throw Problem("Couldn't download ${a.name}.", "Check your internet connection.")
        } finally {
            c.disconnect()
        }
    }

    /** Downloads into [out]; returns its SHA-256 (hex). */
    fun download(a: Asset, repo: String, token: String?, out: File, progress: (Long, Long) -> Unit,
                 cancelled: () -> Boolean): String {
        if (a.size > 300L shl 20) throw Problem("${a.name} is unexpectedly large; not downloading it.")
        val c = openAsset(a, repo, token)
        val md = MessageDigest.getInstance("SHA-256")
        try {
            val total = c.contentLength.toLong().takeIf { it > 0 } ?: a.size
            var got = 0L
            c.inputStream.use { inp ->
                out.outputStream().use { o ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        if (cancelled()) throw Problem("Stopped.")
                        val n = inp.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n)
                        md.update(buf, 0, n)
                        got += n
                        progress(got, total)
                    }
                }
            }
        } catch (e: IOException) {
            throw Problem("The download of ${a.name} stopped.", "Check your internet connection and try again.")
        } finally {
            c.disconnect()
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
