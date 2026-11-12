# Releasing

1. Bump the version in `desktop/Cargo.toml`, `desktop/tauri.conf.json` and `orbit/__init__.py` (the Python Orbit).
2. `python3 tools/sync-icons.py` pulls the apps' current icons from their repositories (a test fails if the desktop
   and Android copies differ, or if a file in Orbit's icon folders isn't one of the script's icons). Orbit's own icon
   is drawn by `python3 tools/make-icon.py` (`packaging/icon/`).
3. Tag and push: `git tag -a v0.5.0 -m "Orbit v0.5.0" && git push origin v0.5.0`.
4. After the release: run the *self-update-from-a-release* workflow, which updates the previous release to this one on Linux, Windows and macOS.

**Orbit 0.4.0 and YTM Starcast.** 0.4.0 replaces Pocketfader and YTM Beacon with YTM Starcast
(`dash1101/ytm-starcast`, the renamed `dash1101/pocketfader`). Release it right after YTM Starcast's first release,
once all of these are true, or its checks fail: `dash1101/ytm-starcast` exists under exactly that name (Orbit compares
download addresses with it letter for letter), its newest release has `ytm_starcast-<version>-py3-none-any.whl`,
`ytm-starcast-v<tag>-windows-x64.zip`, `ytm-starcast-android.apk` and `SHA256SUMS.txt`, and `starcast/static/icon.svg`
is on its default branch (for `tools/sync-icons.py`). The *apps-on-windows* workflow also moves a Windows machine from
Pocketfader `v0.1.0-alpha.15` (from YTM Starcast's releases) and YTM Beacon `v0.1.4` (from `dash1101/ytm-beacon`,
which must stay public) to YTM Starcast. Orbit 0.3.5 and older can't install YTM Starcast, so the release notes tell
people to update Orbit first.

The `build` workflow builds the Android app and standalone programs for Linux, Windows and macOS (Apple chips and Intel), starts each one on its own system as a check, and publishes a pre-release with all of them, each as a single file: the `.exe`, the Linux program, a `.dmg` per Mac type, and the `.apk`. The desktop programs are the Rust build in `desktop/` (see its README); each is a single file with nothing to install. The Android app is the one piece that depends on where the signing key is.

## The signing key

Android only installs an update over an installed app when both are signed with the **same key**. Keep the key
safe: if it is lost, existing installs must be removed before a build signed with a new key can be installed.

**Stored in the repository (automatic releases).** Add it once as secrets:

```bash
base64 -w0 orbit-release.jks | gh secret set ORBIT_KEYSTORE_B64
gh secret set ORBIT_KEYSTORE_PASSWORD < password.txt
printf '%s' orbit | gh secret set ORBIT_KEY_ALIAS
```

Each tag then publishes everything, the signed APK included. If the release
already exists, its files are replaced and the checksums updated. To sign an existing tag's APK after adding the
secrets, re-run that tag's `build` workflow.

**Kept on your own computer.** Without the secrets, the release is published without the Android app, and a tag build
leaves it unsigned (the `android` artifact, `orbit-android-unsigned.apk`). Sign it with `tools/sign-apk/Sign.java`, which uses Google's `apksig` library
(`com.android.tools.build:apksig` from Google's Maven repository) and verifies the result:

```bash
java -cp apksig-8.5.2.jar tools/sign-apk/Sign.java orbit-release.jks password.txt orbit \
     orbit-android-unsigned.apk orbit-android.apk
```

Then upload it to the release with its checksum (`gh release upload v0.4.0 orbit-android.apk`, and update
`SHA256SUMS`).

**File names.** Release files keep one name from release to release (`orbit-windows-x64.exe`, `orbit-linux-x86_64`,
`orbit-macos-apple-silicon.dmg`, `orbit-macos-intel.dmg`, `orbit-android.apk`), so a program that updates itself keeps
its name. Orbit 0.3.4 and older (desktop and Android) only find files named with the version, so through the 0.3 series
and for 0.4.0 each file is also published under that name (`orbit-v0.4.0-windows-x64.exe`; the workflow does this for
`v0.3.*` tags and `v0.4.0`; upload the signed APK as `orbit-v0.4.0-android.apk` too). 0.4.0 is included because it is
the release that moves Pocketfader and YTM Beacon to YTM Starcast, which older Orbits can't install. From 0.4.1 on,
only the plain names are published.

## Signing in with GitHub

Orbit's *sign in with GitHub* uses the **NovaLabs Orbit** GitHub App's client ID (public, not a secret), set in
`orbit/github_login.py` (`CLIENT_ID`) and `android/.../GitHubLogin.kt` (`CLIENT_ID`). The app has **Device Flow**
enabled, **Repository permissions → Contents: Read-only** and nothing else, no webhook, and user-token expiration
turned off (Orbit has no client secret to refresh tokens with). It must be **installed** on the repositories whose
private releases Orbit should read. With the client ID empty, the button is hidden and Orbit falls back to tokens.
