//! Orbit's languages. The window translates its own text (web/index.html); these are the same files, served
//! to it and used for the few words Orbit shows outside the window (its desktop notifications). See tools/i18n.py.
use serde_json::Value;
use std::collections::HashMap;
use std::sync::OnceLock;

/// The languages Orbit has besides English: code, and the file built into the program.
pub const LANGS: &[(&str, &[u8])] = &[
    ("en", b"{}"),
    ("es", include_bytes!("../../web/i18n/es.json")),
    ("fr", include_bytes!("../../web/i18n/fr.json")),
    ("de", include_bytes!("../../web/i18n/de.json")),
    ("pt-BR", include_bytes!("../../web/i18n/pt-BR.json")),
    ("it", include_bytes!("../../web/i18n/it.json")),
    ("ru", include_bytes!("../../web/i18n/ru.json")),
    ("ja", include_bytes!("../../web/i18n/ja.json")),
    ("zh-CN", include_bytes!("../../web/i18n/zh-CN.json")),
    ("ko", include_bytes!("../../web/i18n/ko.json")),
];

pub fn file(code: &str) -> Option<&'static [u8]> {
    LANGS.iter().find(|(c, _)| *c == code).map(|(_, b)| *b)
}

/// The language a setting means: the code itself, or for "auto" the system's (LC_ALL, LC_MESSAGES, LANG; English when
/// Orbit doesn't have it).
pub fn chosen(setting: &str) -> String {
    if setting != "auto" && file(setting).is_some() {
        return setting.to_string();
    }
    let env = ["LC_ALL", "LC_MESSAGES", "LANG"].iter().filter_map(|k| std::env::var(k).ok()).find(|v| !v.is_empty()).unwrap_or_default();
    from_locale(&env)
}

pub fn from_locale(loc: &str) -> String {
    let s = loc.split(['.', '@']).next().unwrap_or("").replace('_', "-").to_lowercase();
    if s.starts_with("zh") {
        return if ["hant", "-tw", "-hk", "-mo"].iter().any(|x| s.contains(x)) { "en".into() } else { "zh-CN".into() };
    }
    if s.starts_with("pt") {
        return "pt-BR".into();
    }
    LANGS.iter().map(|(c, _)| *c).find(|c| s.len() >= 2 && c.len() == 2 && s[..2] == **c).unwrap_or("en").to_string()
}

fn table(code: &str) -> Option<&'static HashMap<String, String>> {
    static ALL: OnceLock<HashMap<&'static str, HashMap<String, String>>> = OnceLock::new();
    ALL.get_or_init(|| {
        LANGS.iter().map(|(c, b)| {
            let m = serde_json::from_slice::<Value>(b).ok().and_then(|v| v.as_object().cloned()).unwrap_or_default();
            (*c, m.into_iter().filter_map(|(k, v)| v.as_str().map(|v| (k, v.to_string()))).collect())
        }).collect()
    }).get(code)
}

/// A whole text (no {} parts) in a language, or as it is.
pub fn tr(code: &str, text: &str) -> String {
    table(code).and_then(|m| m.get(text)).cloned().unwrap_or_else(|| text.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn locales() {
        assert_eq!(from_locale("de_DE.UTF-8"), "de");
        assert_eq!(from_locale("pt_PT.UTF-8"), "pt-BR");
        assert_eq!(from_locale("zh_CN.UTF-8"), "zh-CN");
        assert_eq!(from_locale("zh_TW.UTF-8"), "en");
        assert_eq!(from_locale("C"), "en");
        assert_eq!(from_locale(""), "en");
        assert_eq!(chosen("ja"), "ja");
        assert_eq!(tr("en", "Nova: approval waiting"), "Nova: approval waiting");
        for (c, b) in LANGS {
            assert!(serde_json::from_slice::<Value>(b).map(|v| v.is_object()).unwrap_or(false), "{c}.json isn't an object");
        }
    }
}
