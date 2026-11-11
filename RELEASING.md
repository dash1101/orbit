# Releasing

1. Bump `__version__` in `orbit/__init__.py`.
2. `python3 tools/sync-icons.py` pulls the apps' current icons from their repositories (a test fails if the desktop
   and Android copies differ). Orbit's own icon is drawn by `python3 tools/make-icon.py` (`packaging/icon/`).
3. Tag and push: `git tag -a v0.3.5 -m "Orbit v0.3.5" && git push origin v0.3.5`.
4. After the release: run the *self-update-from-a-release* workflow, which updates the previous release to this one on Linux, Windows and macOS.

The `build` workflow builds the Android app and standalone programs for Linux, Windows and macOS (Apple chips and Intel), starts each one on its own system as a check, and publishes a pre-release with all of them, each as a single file: the `.exe`, the Linux program, a `.dmg` per Mac type, and the `.apk`. The programs bundle Python, so nothing needs installing to run them. The Android app is the one piece that depends on where the signing key is.

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

Then upload it to the release with its checksum (`gh release upload v0.3.5 orbit-android.apk`, and update
`SHA256SUMS`).

**File names.** Release files keep one name from release to release (`orbit-windows-x64.exe`, `orbit-linux-x86_64`,
`orbit-macos-apple-silicon.dmg`, `orbit-macos-intel.dmg`, `orbit-android.apk`), so a program that updates itself keeps
its name. Orbit 0.3.4 and older (desktop and Android) only find files named with the version, so through the 0.3 series
each file is also published under that name (`orbit-v0.3.5-windows-x64.exe`; the workflow does this for `v0.3.*` tags;
upload the signed APK as `orbit-v0.3.5-android.apk` too). From 0.4.0 on, only the plain names are published.

## Signing in with GitHub

Orbit's *sign in with GitHub* uses the **NovaLabs Orbit** GitHub App's client ID (public, not a secret), set in
`orbit/github_login.py` (`CLIENT_ID`) and `android/.../GitHubLogin.kt` (`CLIENT_ID`). The app has **Device Flow**
enabled, **Repository permissions → Contents: Read-only** and nothing else, no webhook, and user-token expiration
turned off (Orbit has no client secret to refresh tokens with). It must be **installed** on the repositories whose
private releases Orbit should read. With the client ID empty, the button is hidden and Orbit falls back to tokens.
