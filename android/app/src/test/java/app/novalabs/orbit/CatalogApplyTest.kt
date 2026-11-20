package app.novalabs.orbit

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/** Orbit's catalog from its repository: what it may change, what it can't, and that a bad one changes nothing. */
class CatalogApplyTest {
    private val icons = Files.createTempDirectory("orbit-icons").toFile()

    @After
    fun back() {
        Catalog.reset()
        icons.deleteRecursively()
    }

    @Test
    fun theRepositorysCatalogKeepsEveryApp() {
        // packaging/catalog.json, as published (the tests run in android/app)
        val text = File("../../packaging/catalog.json").readText()
        assertTrue(Catalog.apply(text, icons))
        assertTrue(Catalog.apps.map { it.id }.containsAll(Catalog.builtIn.map { it.id }))
        for (b in Catalog.builtIn) {
            val a = Catalog.apps.first { it.id == b.id }
            assertEquals(b.name, a.name)
            assertEquals(b.repo, a.repo)
            assertEquals(b.pkgs, a.pkgs)
            assertEquals(b.sums, a.sums)
            assertEquals(b.signingKey, a.signingKey)   // signing keys only ever come from Orbit itself
            assertEquals(b.self, a.self)
            assertTrue(File(a.iconFile!!).readText().startsWith("<svg"))
        }
        val starcast = Catalog.apps.first { it.id == "ytm-starcast" }
        assertEquals("ytm-starcast-android.apk", starcast.apk(listOf("ytm-starcast-android-unshrunk.apk", "ytm-starcast-android.apk")))
        val orbit = Catalog.apps.first { it.id == "orbit" }
        assertEquals("orbit-android.apk", orbit.apk(listOf("orbit-linux-x86_64", "orbit-android.apk")))
        // xmas-tui's live wallpaper comes from the catalog alone (no built-in listing, no signing key)
        val xmas = Catalog.apps.first { it.id == "xmas-tui" }
        assertEquals(listOf("io.github.dash1101.xmastui"), xmas.pkgs)
        assertEquals("xmas-tui-android.apk", xmas.apk(listOf("xmas-tui-linux-x86_64", "xmas-tui-android.apk")))
        assertEquals("SHA256SUMS", xmas.sums)
        assertNull(xmas.signingKey)
        assertTrue(File(xmas.iconFile!!).readText().startsWith("<svg"))
    }

    @Test
    fun aRenameComesThroughAndBadValuesDont() {
        val text = """{"apps": [{"id": "ytm-starcast", "name": "Starcast", "repo": "dash1101/starcast", "color": "#123456",
            "android": {"pkgs": ["not a package"], "apk": ["starcast.apk"], "sums": "../x"}}]}"""
        assertTrue(Catalog.apply(text, icons))
        val a = Catalog.apps.first { it.id == "ytm-starcast" }
        assertEquals("Starcast", a.name)
        assertEquals("dash1101/starcast", a.repo)
        assertEquals(0xFF123456, a.color)
        assertEquals(listOf("app.novalabs.pocketfader", "app.pocketfader.client"), a.pkgs)   // the bad id is left out
        assertEquals("SHA256SUMS.txt", a.sums)                                              // so is a path
        assertEquals("starcast.apk", a.apk(listOf("starcast.apk")))
        assertEquals(Catalog.builtIn.size, Catalog.apps.size)   // the ones it leaves out stay
    }

    @Test
    fun aNewAppComesWhenItCanBeInstalled() {
        val text = """{"apps": [{"id": "lumen", "name": "Lumen", "repo": "dash1101/lumen",
            "android": {"pkgs": ["app.novalabs.lumen"], "apk": ["re:lumen-.*\\.apk"]}},
            {"id": "nothing-here", "name": "No Android", "repo": "dash1101/x"}]}"""
        assertTrue(Catalog.apply(text, icons))
        val l = Catalog.apps.firstOrNull { it.id == "lumen" }
        assertNotNull(l)
        assertEquals("lumen-1.0.apk", l!!.apk(listOf("SHA256SUMS", "lumen-1.0.apk")))
        assertFalse(Catalog.apps.any { it.id == "nothing-here" })
    }

    @Test
    fun aBadCatalogChangesNothing() {
        assertFalse(Catalog.apply("not json", icons))
        assertFalse(Catalog.apply("""{"apps": []}""", icons))
        assertEquals(Catalog.builtIn, Catalog.apps)
    }
}
