package app.novalabs.orbit

/**
 * Short patch notes: what's new in an app's releases, from their GitHub release text, a few lines per version (the same
 * rules as the desktop Orbit's notes.rs). It keeps the top-level list items of the part about that version ("What's new",
 * "New", "Fixed"…, or "New in <version>" when one text covers several versions), each cut to its bold title or its first
 * sentence.
 */
object Notes {
    const val PER_VERSION = 5
    const val VERSIONS = 3
    private const val LINE = 140

    data class Version(val version: String, val lines: List<String>, val more: Int)

    private fun heading(line: String): String? {
        val t = line.trim()
        if (t.startsWith("#")) return t.trimStart('#').trim()
        if (t.length > 4 && t.startsWith("**") && t.endsWith("**") && !t.substring(2, t.length - 2).contains("**"))
            return t.substring(2, t.length - 2).trim().trimEnd(':')
        return null
    }

    private fun aboutChanges(h: String): Boolean {
        val l = h.lowercase()
        return listOf("new", "fix", "change", "improve", "add", "highlight", "update", "what").any { l.contains(it) } &&
            !l.contains("updating from") && !l.contains("install") && !l.contains("download")
    }

    private fun namesVersion(h: String): String? {
        val i = h.lowercase().indexOf(" in ")
        if (i < 0) return null
        val v = h.substring(i + 4).trim().trimStart('v', 'V').split(' ', ',', ':', '(').first()
        return if (Versions.parse(v) == null) null else v
    }

    private val LINK = Regex("!?\\[([^\\]]*)]\\([^)]*\\)")
    private fun plain(s: String): String = LINK.replace(s, "$1").replace("**", "").replace("__", "").replace("`", "")
        .replace(" *", " ").replace("* ", " ").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

    private fun shorten(item: String): String {
        val t = item.trim()
        var text = if (t.startsWith("**")) {
            val end = t.indexOf("**", 2)
            if (end > 5) t.substring(2, end).trim().trimEnd(':', '.') else plain(t)
        } else {
            val p = plain(t)
            val cut = p.indices.firstOrNull { i -> p[i] in ".!?" && i > 20 && p.startsWith(" ", i + 1) }
            if (cut != null) p.substring(0, cut) else p
        }
        text = plain(text)
        if (text.length > 90) {
            val i = text.indexOf(": ")
            if (i > 30) text = text.substring(0, i)
        }
        if (text.length <= LINE) return text
        var cut = text.take(LINE)
        val sp = cut.lastIndexOf(' ')
        if (sp > 0) cut = cut.substring(0, sp)
        return cut.trimEnd(',', ';', ':') + "…"
    }

    /** The short notes of one release: its lines, and how many more there were. */
    fun short(body: String, version: String): Pair<List<String>, Int> {
        val want = Versions.clean(version)
        val items = mutableListOf<String>()
        var cur: StringBuilder? = null
        var on = true
        var seenVersion = false
        fun push() { cur?.let { val s = shorten(it.toString()); if (s.isNotEmpty()) items.add(s) }; cur = null }
        for (raw in body.replace("\r\n", "\n").split('\n')) {
            if (raw.trimStart().startsWith("|")) { push(); break }
            val h = heading(raw)
            if (h != null) {
                push()
                val v = namesVersion(h)
                if (v != null) {
                    if (Versions.clean(v) != want) {
                        if (seenVersion || items.isNotEmpty()) break
                        on = false
                        continue
                    }
                    seenVersion = true; on = true
                    continue
                }
                on = aboutChanges(h) && (on || !seenVersion)
                continue
            }
            if (!on) continue
            val indent = raw.length - raw.trimStart().length
            val t = raw.trimStart()
            val bullet = (t.startsWith("- ") || t.startsWith("* ") || t.startsWith("+ ")) && indent < 2
            when {
                bullet -> { push(); cur = StringBuilder(t.substring(2)) }
                t.isEmpty() -> push()
                cur != null && !(t.startsWith("- ") || t.startsWith("* ")) -> cur!!.append(' ').append(t)
            }
        }
        push()
        val more = (items.size - PER_VERSION).coerceAtLeast(0)
        return items.take(PER_VERSION) to more
    }

    /** What's new after [installed] up to [latest], newest first: (tag, body) pairs from [Release.notes]. */
    fun between(releases: List<Pair<String, String>>, installed: String?, latest: String): List<Version> {
        if (installed.isNullOrEmpty() || !Versions.isNewer(latest, installed)) return emptyList()
        val out = mutableListOf<Version>()
        for ((tag, body) in releases) {
            val v = Versions.clean(tag)
            if (v.isEmpty() || Versions.isNewer(v, latest) || !Versions.isNewer(v, installed)) continue
            val (lines, more) = short(body, v)
            out.add(Version(v, lines, more))
            if (out.size >= VERSIONS) break
        }
        return out
    }
}
