package app.novalabs.orbit

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotesTest {
    @Test fun titlesAndFirstSentences() {
        val body = "- **CPU power: run the server cooler when you want to.** Quick panel has three\n  controls:\n  - **Power plan:** Full\n" +
            "- **The alert no longer comes and goes.** Once it's hot…\n- Widgets can be moved all the way to the right. In the window they stopped short.\n"
        assertEquals(listOf("CPU power: run the server cooler when you want to", "The alert no longer comes and goes",
            "Widgets can be moved all the way to the right"), Notes.short(body, "1.0.0").first)
    }

    @Test fun onlyThisVersionsPart() {
        val orbit = "**Orbit** by NovaLabs: apps.\n\n**New in 0.8.2**\n\n- **Orbit has a new license.** From now…\n\n**New in 0.8.1**\n\n- **xmas-tui gets a page** once…\n\n| System |\n"
        assertEquals(listOf("Orbit has a new license"), Notes.short(orbit, "v0.8.2").first)
        assertEquals(listOf("xmas-tui gets a page"), Notes.short(orbit, "0.8.1").first)
        val starcast = "### What's new in 0.2.10-alpha\n\n- **New license:** now…\n- The About screens show who made it.\n\n### Updating from 0.2.0-alpha.10 or older\n\n- **Computer:** by hand\n"
        assertEquals(listOf("New license", "The About screens show who made it."), Notes.short(starcast, "v0.2.10-alpha").first)
    }

    @Test fun longAndMany() {
        val l = Notes.short("- " + "word ".repeat(60) + "\n", "1").first
        assertTrue(l[0].length <= 141 && l[0].endsWith("…"))
        val many = (1..8).joinToString("") { "- Change number $it here\n" }
        val (lines, more) = Notes.short(many, "1")
        assertEquals(Notes.PER_VERSION to 3, lines.size to more)
    }

    @Test fun versionsBetween() {
        val rels = listOf("1.4.0", "1.3.0", "1.2.0", "1.1.0").map { "v$it" to "- In $it.\n" }
        assertEquals(listOf("1.3.0", "1.2.0"), Notes.between(rels, "1.1.0", "1.3.0").map { it.version })
        assertTrue(Notes.between(rels, null, "1.4.0").isEmpty())
        assertTrue(Notes.between(rels, "1.4.0", "1.4.0").isEmpty())
    }
}
