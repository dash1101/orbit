package app.orbit.hub

import android.util.Base64
import java.security.KeyFactory
import java.security.NoSuchAlgorithmException
import java.security.Signature
import java.security.spec.InvalidKeySpecException
import java.security.spec.X509EncodedKeySpec

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

    /** true = signed with the key, false = not, null = this Android can't check ed25519 signatures. */
    fun ed25519(publicKeyB64: String, message: ByteArray, signatureB64: String): Boolean? {
        val key = try {
            KeyFactory.getInstance("Ed25519").generatePublic(X509EncodedKeySpec(Base64.decode(publicKeyB64, Base64.DEFAULT)))
        } catch (e: NoSuchAlgorithmException) {
            return null
        } catch (e: InvalidKeySpecException) {
            return null
        }
        return try {
            val s = Signature.getInstance("Ed25519")
            s.initVerify(key)
            s.update(message)
            s.verify(Base64.decode(signatureB64.trim(), Base64.DEFAULT))
        } catch (e: NoSuchAlgorithmException) {
            null
        } catch (e: Exception) {
            false
        }
    }
}
