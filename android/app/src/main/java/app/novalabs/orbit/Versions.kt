package app.novalabs.orbit

/** "v0.1.4", "0.9.13-alpha", "0.1.0a9", "v0.1.0-alpha.10" -> comparable lists (the same rules as Orbit for desktop). */
object Versions {
    private val number = Regex("""^(\d+(?:\.\d+)*)(.*)$""")
    private val pre = Regex("""(alpha|beta|rc|a|b)[.\-]?(\d*)""")

    fun parse(v: String?): List<Int>? {
        val s = (v ?: "").trim().trimStart('v', 'V')
        val m = number.find(s) ?: return null
        val nums = (m.groupValues[1].split(".").map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0, 0)).take(4)
        val rest = m.groupValues[2].lowercase()
        val p = pre.find(rest)
        return when {
            p != null -> {
                val rank = when (p.groupValues[1]) { "alpha", "a" -> 0; "beta", "b" -> 1; else -> 2 }
                nums + listOf(0, rank, p.groupValues[2].toIntOrNull() ?: 0)
            }
            rest.trim('~', '-', '.').isNotEmpty() -> nums + listOf(0, 0, 0)   // an unknown suffix: a pre-release
            else -> nums + listOf(1, 0, 0)
        }
    }

    fun compare(a: List<Int>, b: List<Int>): Int {
        for (i in 0 until minOf(a.size, b.size)) {
            val c = a[i].compareTo(b[i])
            if (c != 0) return c
        }
        return a.size.compareTo(b.size)
    }

    fun isNewer(latest: String?, current: String?): Boolean {
        val a = parse(latest) ?: return false
        val b = parse(current) ?: return false
        return compare(a, b) > 0
    }

    fun clean(tag: String) = tag.trimStart('v', 'V')
}
