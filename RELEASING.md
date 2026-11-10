# Releasing

1. Bump `__version__` in `orbit/__init__.py`.
2. Tag and push: `git tag -a v0.2.0 -m "Orbit v0.2.0" && git push origin v0.2.0`.

The `android` workflow builds the APK. What happens next depends on where the signing key is.

## The signing key

Android only installs an update over an installed app when both are signed with the **same key**. Keep the key
safe: if it is lost, existing installs must be removed before a build signed with a new key can be installed.

**Stored in the repository (automatic releases).** Add it once as secrets:

```bash
base64 -w0 orbit-release.jks | gh secret set ORBIT_KEYSTORE_B64
gh secret set ORBIT_KEYSTORE_PASSWORD < password.txt
printf '%s' orbit | gh secret set ORBIT_KEY_ALIAS
```

Each tag then publishes a pre-release with the signed APK, the desktop archive and `SHA256SUMS`. If the release
already exists, its files are replaced and the checksums updated. To sign an existing tag's APK after adding the
secrets, re-run that tag's `android` workflow.

**Kept on your own computer.** Without the secrets, a tag build is left unsigned (the `orbit-android` artifact,
`orbit-android-unsigned.apk`). Sign it with `tools/sign-apk/Sign.java`, which uses Google's `apksig` library
(`com.android.tools.build:apksig` from Google's Maven repository) and verifies the result:

```bash
java -cp apksig-8.5.2.jar tools/sign-apk/Sign.java orbit-release.jks password.txt orbit \
     orbit-android-unsigned.apk orbit-android-v0.2.0.apk
```

Then upload it to the release with its checksum (`gh release upload v0.2.0 orbit-android-v0.2.0.apk`, and update
`SHA256SUMS`).
