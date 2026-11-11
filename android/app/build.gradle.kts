plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.novalabs.orbit"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.novalabs.orbit"
        // Android 5.0, like YTM Starcast's app: the oldest release Jetpack Compose supports.
        minSdk = 21
        targetSdk = 34
        versionCode = (findProperty("orbitVersionCode") ?: "1").toString().toInt()
        versionName = (findProperty("orbitVersionName") ?: "0.1.0").toString()
        vectorDrawables { useSupportLibrary = true }
    }

    // A persistent release key, so each new version installs over the last one. CI provides it through
    // environment variables; without them builds fall back to the throwaway debug key.
    val releaseKey = System.getenv("ORBIT_KEYSTORE")?.takeIf { file(it).exists() }
    signingConfigs {
        if (releaseKey != null) {
            create("orbit") {
                storeFile = file(releaseKey)
                storePassword = System.getenv("ORBIT_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ORBIT_KEY_ALIAS")
                keyPassword = System.getenv("ORBIT_KEYSTORE_PASSWORD")
            }
        }
    }
    // -PorbitUnsigned: leave the APK unsigned, to be signed with the release key outside the build
    val appSigning = when {
        releaseKey != null -> signingConfigs.getByName("orbit")
        findProperty("orbitUnsigned") != null -> null
        else -> signingConfigs.getByName("debug")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = appSigning
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    packaging {
        resources { excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "/META-INF/*.md") }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("io.coil-kt:coil-compose:2.6.0")
    implementation("io.coil-kt:coil-svg:2.6.0")
    testImplementation("junit:junit:4.13.2")
}
