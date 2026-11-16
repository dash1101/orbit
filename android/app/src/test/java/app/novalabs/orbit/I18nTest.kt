package app.novalabs.orbit

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File
import java.util.Locale

/** Orbit's languages: the same matching as the desktop window, and every language file loads. */
class I18nTest {
    @After
    fun back() = I18n.apply("en", "{}")

    @Test
    fun wholeTextsPatternsAndWhatsAroundThem() {
        I18n.apply("de", """{"install": "installieren", "{} is out": "{} ist da", "Download {} from {}": "{2}: {1} laden",
            "settings": "Einstellungen", "on": "an"}""")
        assertEquals("installieren", tr("install"))
        assertEquals("Orbit 0.7.2 ist da", tr("Orbit 0.7.2 is out"))
        assertEquals("GitHub: Nova laden", tr("Download Nova from GitHub"))
        assertEquals("── Einstellungen", tr("── settings"))
        assertEquals("(•) Einstellungen", tr("(•) settings"))
        assertEquals("installieren · an", tr("install · on"))
        assertEquals("  installieren ", tr("  install "))
        assertEquals("something new", tr("something new"))
        assertEquals("1.2.3", tr("1.2.3"))
    }

    @Test
    fun theLanguageForAuto() {
        assertEquals("de", I18n.pick("auto", Locale.GERMANY))
        assertEquals("pt-BR", I18n.pick("auto", Locale("pt", "PT")))
        assertEquals("zh-CN", I18n.pick("auto", Locale.SIMPLIFIED_CHINESE))
        assertEquals("en", I18n.pick("auto", Locale.TRADITIONAL_CHINESE))
        assertEquals("en", I18n.pick("auto", Locale("nl", "NL")))
        assertEquals("ja", I18n.pick("ja", Locale.GERMANY))
    }

    @Test
    fun everyLanguageFileLoads() {
        for ((c, _) in I18n.LANGS.drop(1)) {
            I18n.apply(c, File("src/main/assets/i18n/$c.json").readText())
            assertEquals(c, I18n.code)
        }
    }
}
