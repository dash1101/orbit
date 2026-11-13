package app.novalabs.orbit

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * A notification when an app you have gets an update: Orbit looks every 6 hours or so while the phone is online, in the
 * background (Android decides exactly when). Only an update counts: a newer release with an Android app other than the
 * one installed (not one that is new for other systems only). Once per version, and per app as chosen in Orbit's
 * settings (all on, to begin with).
 */
object Updates {
    private const val CHANNEL = "updates"
    private const val WORK = "orbit-update-check"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("orbit", Context.MODE_PRIVATE)

    fun wanted(ctx: Context, id: String) = prefs(ctx).getBoolean("notify:$id", true)

    fun setWanted(ctx: Context, id: String, on: Boolean) {
        prefs(ctx).edit().putBoolean("notify:$id", on).apply()
        schedule(ctx)
    }

    /** Can Orbit show notifications at all (Android 13 asks; any phone can have them switched off for Orbit)? */
    fun allowed(ctx: Context) = NotificationManagerCompat.from(ctx).areNotificationsEnabled()

    /** The check in the background, while any app's notifications are wanted (none: no check). */
    fun schedule(ctx: Context) {
        val wm = WorkManager.getInstance(ctx)
        if (Catalog.apps.none { it.onAndroid && wanted(ctx, it.id) }) {
            wm.cancelUniqueWork(WORK)
            return
        }
        val req = PeriodicWorkRequestBuilder<UpdateCheck>(6, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    /** Look for updates and say so: one notification per app and version. */
    fun check(ctx: Context) {
        // the catalog kept from last time (the names and release files may have changed since this version)
        val kept = File(ctx.filesDir, "catalog.json")
        runCatching { if (kept.exists() && Catalog.apps === Catalog.builtIn) Catalog.apply(kept.readText(), File(ctx.filesDir, "catalog-icons")) }
        if (!allowed(ctx)) return
        val phone = Phone(ctx)
        val p = prefs(ctx)
        for (a in Catalog.apps) {
            if (!a.onAndroid || !wanted(ctx, a.id)) continue
            val have = phone.installedVersion(a) ?: continue
            val rel = runCatching { GitHub.latest(a.repo, phone.token) { a.apk(it) != null } }.getOrNull() ?: continue
            if (!runCatching { phone.isUpdate(a, rel) }.getOrDefault(false)) continue
            if (p.getString("notified:${a.id}", "") == rel.tag) continue   // said once already
            notify(ctx, a, rel.version, have)
            p.edit().putString("notified:${a.id}", rel.tag).apply()
        }
    }

    private fun notify(ctx: Context, a: Listing, version: String, have: String) {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Updates", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "When an app you have from Orbit gets an update" })
            }
        }
        val open = PendingIntent.getActivity(ctx, a.id.hashCode(),
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0))
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("${a.name} $version is out")
            .setContentText(if (a.self) "You have $have. Tap to update Orbit." else "You have $have. Tap to update it in Orbit.")
            .setContentIntent(open)
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify("update", a.id.hashCode(), n)
        } catch (e: SecurityException) {
            // notifications not allowed after all: nothing to say
        }
    }
}

/** The background update check (see [Updates]). */
class UpdateCheck(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        Updates.check(applicationContext)
        return Result.success()
    }
}
