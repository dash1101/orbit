package app.novalabs.orbit

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONObject
import java.util.Locale

/**
 * Orbit's languages. Orbit is written in English; another language is assets/i18n/<code>.json, {"English text":
 * "translation"}, the same files as Orbit for desktop (tools/i18n.py). {} marks a part that changes ("{} is out"); {1} {2}
 * put the parts in another order. Text is translated where it is drawn ([Txt], which [TuiButton] uses too), so the rest
 * of the app doesn't have to know about languages.
 */
object I18n {
    val LANGS = listOf("en" to "English", "es" to "Español", "fr" to "Français", "de" to "Deutsch", "pt-BR" to "Português (Brasil)",
        "it" to "Italiano", "ru" to "Русский", "ja" to "日本語", "zh-CN" to "简体中文", "ko" to "한국어")

    /** The language in use; drawing reads it, so changing it draws everything again. */
    var code by mutableStateOf("en")
        private set
    private var map: Map<String, String> = emptyMap()
    private var tpl: List<Pair<Regex, String>> = emptyList()
    private val memo = HashMap<String, String>()

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("orbit", Context.MODE_PRIVATE)
    fun setting(ctx: Context): String = prefs(ctx).getString("language", "auto") ?: "auto"

    /** "auto": the phone's language when Orbit has it (Chinese: Simplified only), else English. */
    fun pick(setting: String, locale: Locale = Locale.getDefault()): String {
        if (setting != "auto" && LANGS.any { it.first == setting }) return setting
        val lang = locale.language.lowercase()
        if (lang == "zh") {
            val tag = locale.toString().lowercase()
            return if (locale.script.equals("Hant", true) || listOf("_tw", "_hk", "_mo").any { tag.contains(it) }) "en" else "zh-CN"
        }
        if (lang == "pt") return "pt-BR"
        return LANGS.firstOrNull { it.first == lang }?.first ?: "en"
    }

    fun load(ctx: Context) = use(ctx, pick(setting(ctx)))

    fun choose(ctx: Context, setting: String) {
        prefs(ctx).edit().putString("language", setting).apply()
        load(ctx)
    }

    private fun use(ctx: Context, c: String) {
        if (c == code && (c == "en" || map.isNotEmpty())) return
        val text = if (c == "en") null else runCatching { ctx.assets.open("i18n/$c.json").bufferedReader().use { it.readText() } }.getOrNull()
        apply(if (text == null) "en" else c, text ?: "{}")
    }

    /** (tests, and [use]) */
    internal fun apply(c: String, json: String) {
        val o = runCatching { JSONObject(json) }.getOrNull() ?: JSONObject()
        val m = HashMap<String, String>()
        for (k in o.keys()) o.optString(k, null)?.let { m[k] = it }
        map = m
        tpl = m.keys.filter { it.contains("{}") }.sortedByDescending { it.replace("{}", "").length }.map { k ->
            Regex("^" + k.split("{}").joinToString("([\\s\\S]*?)") { Regex.escape(it) } + "$") to m.getValue(k)
        }
        synchronized(memo) { memo.clear() }
        code = if (m.isEmpty()) "en" else c
    }

    private fun fillIn(t: String, vals: List<String>): String {
        var i = 0
        return Regex("""\{(\d*)\}""").replace(t) { r ->
            val n = r.groupValues[1]
            (if (n.isNotEmpty()) vals.getOrNull(n.toInt() - 1) else vals.getOrNull(i++)) ?: r.value
        }
    }

    private fun core(s: String): String? {
        map[s]?.let { return it }
        for ((re, out) in tpl) re.matchEntire(s)?.let { m -> return fillIn(out, m.groupValues.drop(1).map { tr(it) }) }
        return null
    }

    private val SPACE = Regex("""^(\s*)([\s\S]*?)(\s*)$""")
    private val AROUND = Regex("""^([^A-Za-z0-9]*)([\s\S]*?)([^A-Za-z0-9.!?)”'"]*)$""")
    private val LETTERS = Regex("[A-Za-z]{2}")

    /** A text in the language chosen (as it is when there's no translation): the whole text, then without what's
     *  around the words ("( ) paper", "[x] "), then each part of "a · b". */
    fun tr(s: String): String {
        if (map.isEmpty() || !LETTERS.containsMatchIn(s)) return s
        synchronized(memo) { memo[s] }?.let { return it }
        val m = SPACE.matchEntire(s)!!
        val c = m.groupValues[2]
        var t = core(c)
        if (t == null) {
            val d = AROUND.matchEntire(c)
            if (d != null && (d.groupValues[1].isNotEmpty() || d.groupValues[3].isNotEmpty()) && d.groupValues[2].isNotEmpty())
                core(d.groupValues[2])?.let { t = d.groupValues[1] + it + d.groupValues[3] }
        }
        if (t == null && c.contains(" · ")) {
            val parts = c.split(" · ")
            val out = parts.map { tr(it) }
            if (out != parts) t = out.joinToString(" · ")
        }
        val r = if (t == null) s else m.groupValues[1] + t + m.groupValues[3]
        synchronized(memo) { if (memo.size > 5000) memo.clear(); memo[s] = r }
        return r
    }
}

/** Short for [I18n.tr]. */
fun tr(s: String) = I18n.tr(s)
