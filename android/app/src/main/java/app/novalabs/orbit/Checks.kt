package app.novalabs.orbit

import android.util.Base64

object Checks {
    /** "<sha256>  <name>" lines -> name to sha256 (also under GitHub's spelling, which turns "~" into "."). */
    fun parseSums(text: String): Map<String, String> {
        val out = HashMap<String, String>()
        for (line in text.lines()) {
            val parts = line.trim().split(Regex("\\s+"), limit = 2)
            if (parts.size == 2 && parts[0].length == 64 && parts[0].all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }) {
                val name = parts[1].trim().trimStart('*')
                out[name] = parts[0].lowercase()
                out.putIfAbsent(name.replace('~', '.'), parts[0].lowercase())
            }
        }
        return out
    }

    /** Signed with the key? Checked by Orbit itself ([Ed25519]), so it works on every Android version. */
    fun ed25519(publicKeyB64: String, message: ByteArray, signatureB64: String): Boolean {
        val key = try {
            Ed25519.keyFromSpki(Base64.decode(publicKeyB64, Base64.DEFAULT))
        } catch (e: IllegalArgumentException) {
            null
        } ?: return false
        val sig = try {
            Base64.decode(signatureB64.trim(), Base64.DEFAULT)
        } catch (e: IllegalArgumentException) {
            return false
        }
        return Ed25519.verify(key, message, sig)
    }
}
