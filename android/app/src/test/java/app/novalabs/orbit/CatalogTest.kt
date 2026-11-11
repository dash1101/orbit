package app.novalabs.orbit

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The apps Orbit lists: YTM Starcast's entry (which replaced Pocketfader's) finds the right release file and
 * package.
 */
class CatalogTest {
    private val starcast = Catalog.apps.first { it.id == "ytm-starcast" }

    @Test
    fun starcastPicksItsPhoneApp() {
        assertEquals("ytm-starcast-android.apk",
            starcast.apk(listOf("ytm-starcast-android-unshrunk.apk", "ytm-starcast-android.apk", "SHA256SUMS.txt")))
        assertEquals("ytm-starcast-android-v0.2.0-alpha.1.apk",
            starcast.apk(listOf("ytm-starcast-android-unshrunk-v0.2.0-alpha.1.apk",
                "ytm-starcast-android-v0.2.0-alpha.1.apk")))
        assertNull(starcast.apk(listOf("ytm-starcast-android-unshrunk.apk", "ytm_starcast-0.2.0a1-py3-none-any.whl")))
    }

    @Test
    fun starcastComesFromItsOwnRepository() {
        // spelled exactly as GitHub does: downloads are checked against it
        assertEquals("dash1101/ytm-starcast", starcast.repo)
        assertEquals("SHA256SUMS.txt", starcast.sums)
        assertEquals("ytm-starcast.svg", starcast.icon)
        // the phone app kept Pocketfader's package id, so it updates in place
        assertEquals(listOf("app.novalabs.pocketfader", "app.pocketfader.client"), starcast.pkgs)
        assertTrue(starcast.onAndroid)
    }

    @Test
    fun theOldEntriesAreGone() {
        val ids = Catalog.apps.map { it.id }
        assertFalse("pocketfader" in ids)
        assertFalse("ytm-beacon" in ids)
        assertFalse(Catalog.apps.any { it.repo.equals("dash1101/pocketfader", ignoreCase = true) })
        assertEquals(ids.size, ids.toSet().size)
    }
}
