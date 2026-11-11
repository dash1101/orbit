package app.novalabs.orbit

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

/** The same checks as Orbit for desktop's tests/test_ed25519.py: RFC 8032's vectors, Nova's real release, one changed bit. */
class Ed25519Test {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private val rfc = listOf(
        Triple("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        Triple("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    )

    // Nova 0.9.16-alpha's published SHA256SUMS and SHA256SUMS.sig
    private val novaSums =
        "0d194dea8a822d117f7e1b58076c805a1c7c88b9a6350e09dc15fd23722c12f3  nova-server_0.9.16~alpha_all.deb\n" +
        "0d194dea8a822d117f7e1b58076c805a1c7c88b9a6350e09dc15fd23722c12f3  nova-server.deb\n" +
        "5b36b38b4bb5dd81adf5a3c5b791f561b986059d0efb67651904352300b0878a  nova-server_0.9.16~alpha.tar.gz\n" +
        "f8649119ed98af0ecde0e2fbb4577aab2e90103a7cd70e3381bb3bcd1bf052e9  nova-installer.run\n" +
        "c177903a5450d45870f7589b42f8f378d5060fa87b35cbadbaf1de9d1c154045  nova-server_0.9.16-alpha_windows.zip\n" +
        "c177903a5450d45870f7589b42f8f378d5060fa87b35cbadbaf1de9d1c154045  nova-windows.zip\n" +
        "01473204579303be91d1f198a1b624d044745a0214bd718ed9c56327abbde72c  nova-0.9.16-alpha-arm64.apk\n" +
        "b5ab686095143c11ad316953b9e761c859555b9b6fb9bbeefedeb9e37f186439  nova-0.9.16-alpha-universal.apk\n" +
        "b5ab686095143c11ad316953b9e761c859555b9b6fb9bbeefedeb9e37f186439  nova-universal.apk\n" +
        "a47455119bc6f763c0bcb64c2a823b767ee799c46c58fc1e92ac436f3e427501  nova-wear-0.9.16-alpha.apk\n" +
        "a47455119bc6f763c0bcb64c2a823b767ee799c46c58fc1e92ac436f3e427501  nova-wear.apk\n"
    private val novaSig = "Lv+1QTDepZ6+xKavsXdGiwwa98FucCqAdYb7dB/UDwYgOYH0PGCHcZEMEs6buMA1nkwzl7rYvCjwxtV+DkQTDw=="
    private val novaKey = "MCowBQYDK2VwAyEAGk70hAkKUc/oE8yLsTv49yeIkSJkCeuDG0IqxnF69WI="

    @Test fun rfcVectors() {
        for ((pk, msg, sig) in rfc) assertTrue(Ed25519.verify(hex(pk), hex(msg), hex(sig)))
    }

    @Test fun anyChangedBitFails() {
        val (pk, msg, sig) = rfc[1].let { Triple(hex(it.first), hex(it.second), hex(it.third)) }
        for (i in 0 until 64) {
            val bad = sig.copyOf()
            bad[i] = (bad[i].toInt() xor (1 shl (i % 8))).toByte()
            assertFalse("byte $i", Ed25519.verify(pk, msg, bad))
        }
        assertFalse(Ed25519.verify(pk, msg + byteArrayOf(33), sig))
        assertFalse(Ed25519.verify(hex(rfc[0].first), msg, sig))
        assertFalse(Ed25519.verify(pk.copyOf(31), msg, sig))
        assertFalse(Ed25519.verify(ByteArray(32) { -1 }, msg, sig))
    }

    @Test fun novasRealRelease() {
        val key = Ed25519.keyFromSpki(Base64.getDecoder().decode(novaKey))!!
        assertEquals(32, key.size)
        val sig = Base64.getDecoder().decode(novaSig)
        assertTrue(Ed25519.verify(key, novaSums.toByteArray(), sig))
        assertFalse(Ed25519.verify(key, novaSums.replaceFirst("a", "b").toByteArray(), sig))
        assertNull(Ed25519.keyFromSpki(ByteArray(44)))
    }

    @Test fun checksumList() {
        val m = Checks.parseSums(novaSums)
        assertTrue(m.containsKey("nova-universal.apk"))
        assertEquals(64, m["nova-universal.apk"]!!.length)
    }

    @Test fun versions() {
        assertTrue(Versions.isNewer("v0.1.0-alpha.15", "0.1.0a9"))
        assertTrue(Versions.isNewer("0.9.16-alpha", "0.9.13~alpha"))
        assertFalse(Versions.isNewer("v0.1.4", "0.1.4"))
        assertTrue(Versions.isNewer("0.2.0", "0.2.0-alpha.3"))
        assertFalse(Versions.isNewer("garbage", "0.1.0"))
    }
}
