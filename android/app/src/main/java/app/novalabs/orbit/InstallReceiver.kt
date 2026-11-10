package app.novalabs.orbit

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import kotlinx.coroutines.flow.MutableSharedFlow

/** What Android's installer reports back. */
data class InstallResult(val session: Int, val status: Int, val message: String?, val confirm: Intent? = null)

object InstallEvents {
    val results = MutableSharedFlow<InstallResult>(extraBufferCapacity = 16)
}

/**
 * Android's installer calls this with the result of a session. When it needs the person to confirm, it hands
 * over the confirmation screen, which is shown straight away.
 */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val session = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            val confirm: Intent? = if (Build.VERSION.SDK_INT >= 33)
                intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
            else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
            if (confirm != null) {
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                // Android may refuse this while Orbit is in the background; the install sheet then offers a button
                try { context.startActivity(confirm) } catch (e: RuntimeException) { }
            }
            InstallEvents.results.tryEmit(InstallResult(session, status, null, confirm))
            return
        }
        InstallEvents.results.tryEmit(InstallResult(session, status,
            intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)))
    }
}
