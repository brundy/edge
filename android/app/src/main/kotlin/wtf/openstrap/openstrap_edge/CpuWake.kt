package wtf.openstrap.openstrap_edge

import android.content.Context
import android.os.PowerManager
import android.util.Log

/**
 * A partial (CPU-only) wake lock held while the app is actually busy: a band
 * offload is draining or a derive pass is queued or running.
 *
 * The foreground service keeps the PROCESS alive, but it does not keep the CPU
 * awake. With the screen off the phone suspends between BLE notifications, so
 * Dart timers (the derive scheduler's settle timer, the engine's per-day
 * timeouts) and the CPU-bound derive itself only make progress in the slices
 * a radio interrupt happens to buy. A morning heavy pass that takes ~3 minutes
 * on screen could take an hour or never finish in a pocket.
 *
 * The lock is not reference counted: Dart sends the current busy state and
 * this mirrors it. Every acquire carries [TIMEOUT_MS], so a Dart side that
 * dies or forgets to release can never pin the CPU for longer than that; Dart
 * re-sends "busy" well inside the window while work is still going.
 */
object CpuWake {
    private const val TAG = "CpuWake"
    const val TIMEOUT_MS = 20L * 60L * 1000L

    private var lock: PowerManager.WakeLock? = null

    @Synchronized
    fun set(context: Context, on: Boolean): Boolean {
        val wl = lock ?: run {
            val pm = context.applicationContext
                .getSystemService(Context.POWER_SERVICE) as PowerManager
            pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "openstrap:sync-busy")
                .also {
                    it.setReferenceCounted(false)
                    lock = it
                }
        }
        return try {
            if (on) {
                // Re-acquiring a non-counted lock restarts its timeout.
                wl.acquire(TIMEOUT_MS)
            } else if (wl.isHeld) {
                wl.release()
            }
            wl.isHeld
        } catch (e: Exception) {
            Log.w(TAG, "wake lock ${if (on) "acquire" else "release"} failed: $e")
            false
        }
    }
}
