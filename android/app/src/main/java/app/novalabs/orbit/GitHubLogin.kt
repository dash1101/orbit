package app.novalabs.orbit

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Sign in with GitHub (the device flow): Orbit gets a short code, you enter it at github.com/login/device, and
 * Orbit receives a token. No password passes through Orbit and no client secret is involved. The login belongs
 * to the NovaLabs Orbit GitHub App, which can only read repository contents, in repositories it's installed on.
 */
object GitHubLogin {
    /** The NovaLabs Orbit GitHub App (a public identifier, not a secret). Empty: signing in is unavailable. */
    const val CLIENT_ID = ""
    val available get() = CLIENT_ID.isNotEmpty()

    data class Device(val deviceCode: String, val userCode: String, val uri: String, var interval: Int, val expiresAt: Long)

    sealed class Poll {
        object Pending : Poll()
        data class Token(val token: String) : Poll()
        data class Failed(val message: String) : Poll()
    }

    fun start(): Device {
        val r = post("https://github.com/login/device/code", mapOf("client_id" to CLIENT_ID))
        val code = r.optString("device_code")
        if (code.isEmpty()) throw Problem(r.optString("error_description").ifEmpty { "GitHub didn't start the sign-in." })
        return Device(code, r.optString("user_code"), r.optString("verification_uri", "https://github.com/login/device"),
            maxOf(5, r.optInt("interval", 5)), System.currentTimeMillis() + r.optInt("expires_in", 900) * 1000L)
    }

    fun poll(d: Device): Poll {
        if (System.currentTimeMillis() > d.expiresAt) return Poll.Failed("The code expired before it was entered. Start again.")
        val r = try {
            post("https://github.com/login/oauth/access_token", mapOf("client_id" to CLIENT_ID, "device_code" to d.deviceCode,
                "grant_type" to "urn:ietf:params:oauth:grant-type:device_code"))
        } catch (e: Problem) {
            return Poll.Pending                                   // a network blip: keep waiting
        }
        return when (r.optString("error")) {
            "" -> r.optString("access_token").takeIf { it.isNotEmpty() }?.let { Poll.Token(it) }
                ?: Poll.Failed("GitHub didn't send a token.")
            "authorization_pending" -> Poll.Pending
            "slow_down" -> { d.interval = r.optInt("interval", d.interval + 5); Poll.Pending }
            "expired_token" -> Poll.Failed("The code expired before it was entered. Start again.")
            "access_denied" -> Poll.Failed("The sign-in was cancelled on GitHub.")
            else -> Poll.Failed(r.optString("error_description").ifEmpty { r.optString("error") })
        }
    }

    /** The signed-in account's name. */
    fun user(token: String): String {
        val c = URL("https://api.github.com/user").openConnection() as HttpURLConnection
        c.connectTimeout = 10_000; c.readTimeout = 15_000
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", "orbit-android")
        c.setRequestProperty("Authorization", "Bearer $token")
        return try {
            if (c.responseCode != 200) "" else JSONObject(c.inputStream.bufferedReader().readText()).optString("login")
        } catch (e: IOException) {
            ""
        } finally {
            c.disconnect()
        }
    }

    private fun post(url: String, fields: Map<String, String>): JSONObject {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 10_000; c.readTimeout = 15_000
        c.requestMethod = "POST"
        c.doOutput = true
        c.setRequestProperty("Accept", "application/json")
        c.setRequestProperty("User-Agent", "orbit-android")
        c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
        try {
            val body = fields.entries.joinToString("&") { (k, v) -> "$k=" + URLEncoder.encode(v, "UTF-8") }
            c.outputStream.use { it.write(body.toByteArray()) }
            val stream = if (c.responseCode in 200..299) c.inputStream else c.errorStream
            val text = stream?.bufferedReader()?.readText() ?: "{}"
            return try { JSONObject(text) } catch (e: Exception) { JSONObject() }
        } catch (e: IOException) {
            throw Problem("Couldn't reach GitHub.", "Check your internet connection.")
        } finally {
            c.disconnect()
        }
    }
}
