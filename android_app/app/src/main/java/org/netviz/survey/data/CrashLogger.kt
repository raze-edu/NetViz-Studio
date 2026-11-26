package org.netviz.survey.data

import android.content.Context
import android.os.Build
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.time.Instant

object CrashLogger {
    private const val LOG_DIR = "logs"
    private const val CRASH_FILE = "last_crash.txt"

    @Volatile
    private var installed = false

    fun install(context: Context) {
        if (installed) return
        synchronized(this) {
            if (installed) return
            val appCtx = context.applicationContext
            val previousHandler = Thread.getDefaultUncaughtExceptionHandler()
            Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
                runCatching {
                    writeCrashReport(appCtx, thread, throwable)
                }
                previousHandler?.uncaughtException(thread, throwable)
            }
            installed = true
        }
    }

    fun crashFile(context: Context): File =
        File(File(context.applicationContext.filesDir, LOG_DIR), CRASH_FILE)

    fun readLastCrash(context: Context): String? {
        val file = crashFile(context)
        if (!file.isFile) return null
        return runCatching {
            file.readText(Charsets.UTF_8).takeIf { it.isNotBlank() }
        }.getOrNull()
    }

    fun clearLastCrash(context: Context) {
        runCatching {
            crashFile(context).delete()
        }
    }

    private fun writeCrashReport(context: Context, thread: Thread, throwable: Throwable) {
        val file = crashFile(context)
        file.parentFile?.mkdirs()

        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))

        val report = buildString {
            appendLine("NetViz Survey Uncaught Exception Report")
            appendLine("Timestamp (UTC): ${Instant.now()}")
            appendLine("Device: ${Build.MANUFACTURER} ${Build.MODEL} (${Build.DEVICE})")
            appendLine("Android: ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
            appendLine("Thread: ${thread.name} (id=${thread.id})")
            appendLine()
            append(sw.toString())
        }
        file.writeText(report, Charsets.UTF_8)
    }
}
