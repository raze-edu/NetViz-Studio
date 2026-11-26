package org.netviz.survey.data

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.LruCache
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.netviz.survey.model.Project
import org.netviz.survey.model.SurveySession
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.zip.ZipInputStream

data class LoadedProjectBundle(
    val project: Project,
    val tilesDbFile: File? = null,
    val blueprintFiles: Map<String, File> = emptyMap(),
    val sourceLabel: String = ""
)

class BundleRepository(private val context: Context) {
    private val bundleCacheDir = File(context.filesDir, "bundle_cache").apply { mkdirs() }
    private val draftDir = File(context.filesDir, "drafts").apply { mkdirs() }
    private val exportDir = File(context.filesDir, "exports").apply { mkdirs() }

    private val activeSurveyFile = File(draftDir, "active_survey.json")
    private val draftTmpFile = File(draftDir, "draft.tmp")

    private val tileLock = Any()
    private var tilesDb: SQLiteDatabase? = null
    private val tileCache = object : LruCache<String, Bitmap>(TILE_CACHE_CAPACITY) {}

    suspend fun loadDemoProject(): LoadedProjectBundle = withContext(Dispatchers.IO) {
        closeTilesDatabase()
        val jsonText = context.assets.open(DEMO_ASSET_NAME).bufferedReader(Charsets.UTF_8).use {
            it.readText()
        }
        val project = Project.fromJson(jsonText)
        LoadedProjectBundle(
            project = project,
            tilesDbFile = null,
            blueprintFiles = emptyMap(),
            sourceLabel = "Demo Campus ($DEMO_ASSET_NAME)"
        )
    }

    suspend fun extractNetvizFile(
        netvizFile: File,
        sourceLabel: String = netvizFile.name
    ): LoadedProjectBundle = withContext(Dispatchers.IO) {
        FileInputStream(netvizFile).use { fis ->
            extractNetvizStream(fis, sourceLabel)
        }
    }

    suspend fun importFromUri(
        uri: Uri,
        sourceLabel: String = "Imported File"
    ): LoadedProjectBundle = withContext(Dispatchers.IO) {
        val rawBytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            ?: throw IllegalStateException("Unable to open selected document URI: $uri")

        if (looksLikeZip(rawBytes)) {
            rawBytes.inputStream().use { stream ->
                extractNetvizStream(stream, sourceLabel)
            }
        } else {
            closeTilesDatabase()
            val text = rawBytes.toString(Charsets.UTF_8)
            val project = Project.fromJson(text)
            LoadedProjectBundle(
                project = project,
                tilesDbFile = null,
                blueprintFiles = emptyMap(),
                sourceLabel = sourceLabel
            )
        }
    }

    private fun extractNetvizStream(
        inputStream: InputStream,
        sourceLabel: String
    ): LoadedProjectBundle {
        closeTilesDatabase()
        clearDirectory(bundleCacheDir)

        var projectJsonText: String? = null
        var tilesFile: File? = null
        val blueprintFiles = mutableMapOf<String, File>()

        ZipInputStream(BufferedInputStream(inputStream)).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val entryName = entry.name.replace('\\', '/').trimStart('/')
                if (!entry.isDirectory && !entryName.contains("..")) {
                    when {
                        entryName == PROJECT_JSON_ENTRY -> {
                            projectJsonText = readAllUtf8(zis)
                        }
                        entryName == TILES_SQLITE_ENTRY -> {
                            val target = File(bundleCacheDir, TILES_SQLITE_ENTRY)
                            copyStreamToFileAtomic(zis, target)
                            tilesFile = target
                        }
                        entryName.startsWith(BLUEPRINTS_PREFIX) -> {
                            val relPath = entryName.removePrefix(BLUEPRINTS_PREFIX)
                            if (relPath.isNotEmpty()) {
                                val target = File(File(bundleCacheDir, "blueprints"), relPath)
                                target.parentFile?.mkdirs()
                                copyStreamToFileAtomic(zis, target)
                                blueprintFiles[entryName] = target
                            }
                        }
                    }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }

        val jsonText = projectJsonText
            ?: throw IllegalArgumentException("Invalid .netviz archive: missing $PROJECT_JSON_ENTRY")
        val project = Project.fromJson(jsonText)

        if (tilesFile != null && tilesFile!!.isFile) {
            openTilesDatabase(tilesFile!!)
        }

        return LoadedProjectBundle(
            project = project,
            tilesDbFile = tilesFile,
            blueprintFiles = blueprintFiles,
            sourceLabel = sourceLabel
        )
    }

    fun openTilesDatabase(dbFile: File) {
        synchronized(tileLock) {
            tilesDb?.close()
            tilesDb = null
            tileCache.evictAll()
            if (dbFile.isFile) {
                tilesDb = SQLiteDatabase.openDatabase(
                    dbFile.absolutePath,
                    null,
                    SQLiteDatabase.OPEN_READONLY
                )
            }
        }
    }

    fun closeTilesDatabase() {
        synchronized(tileLock) {
            tilesDb?.close()
            tilesDb = null
            tileCache.evictAll()
        }
    }

    fun hasCachedTiles(): Boolean = synchronized(tileLock) { tilesDb?.isOpen == true }

    fun readTile(provider: String, z: Int, x: Int, y: Int): Bitmap? {
        val key = "$provider/$z/$x/$y"
        tileCache.get(key)?.let { return it }

        val blob: ByteArray = synchronized(tileLock) {
            val db = tilesDb ?: return null
            if (!db.isOpen) return null
            db.rawQuery(
                "SELECT data FROM tiles WHERE provider = ? AND z = ? AND x = ? AND y = ? LIMIT 1",
                arrayOf(provider, z.toString(), x.toString(), y.toString())
            ).use { cursor ->
                if (!cursor.moveToFirst()) return null
                cursor.getBlob(0)
            }
        } ?: return null

        val bmp = BitmapFactory.decodeByteArray(blob, 0, blob.size) ?: return null
        tileCache.put(key, bmp)
        return bmp
    }

    suspend fun saveDraftSession(session: SurveySession) = withContext(Dispatchers.IO) {
        val bytes = session.toJson(pretty = true).toByteArray(Charsets.UTF_8)
        writeBytesAtomic(draftTmpFile, activeSurveyFile, bytes)
    }

    suspend fun loadDraftSession(): SurveySession? = withContext(Dispatchers.IO) {
        if (!activeSurveyFile.isFile) return@withContext null
        runCatching {
            SurveySession.fromJson(activeSurveyFile.readText(Charsets.UTF_8))
        }.getOrNull()
    }

    suspend fun clearDraftSession() = withContext(Dispatchers.IO) {
        draftTmpFile.delete()
        activeSurveyFile.delete()
    }

    suspend fun exportSurveyToUri(session: SurveySession, uri: Uri) = withContext(Dispatchers.IO) {
        val bytes = session.toJson(pretty = true).toByteArray(Charsets.UTF_8)
        context.contentResolver.openOutputStream(uri)?.use { out ->
            out.write(bytes)
            out.flush()
        } ?: throw IllegalStateException("Unable to open destination URI for writing: $uri")
    }

    suspend fun exportSurveyForShare(session: SurveySession): File = withContext(Dispatchers.IO) {
        val safeName = session.name
            .replace(Regex("[^A-Za-z0-9._-]+"), "_")
            .trim('_')
            .ifEmpty { "netviz_survey" }
        val target = File(exportDir, "$safeName.netviz-survey.json")
        val tmp = File(exportDir, "$safeName.tmp")
        val bytes = session.toJson(pretty = true).toByteArray(Charsets.UTF_8)
        writeBytesAtomic(tmp, target, bytes)
        target
    }

    private fun writeBytesAtomic(tmpFile: File, targetFile: File, bytes: ByteArray) {
        tmpFile.parentFile?.mkdirs()
        targetFile.parentFile?.mkdirs()
        FileOutputStream(tmpFile).use { fos ->
            fos.write(bytes)
            fos.flush()
            fos.fd.sync()
        }
        if (!tmpFile.renameTo(targetFile)) {
            Files.move(
                tmpFile.toPath(),
                targetFile.toPath(),
                StandardCopyOption.REPLACE_EXISTING
            )
        }
    }

    private fun copyStreamToFileAtomic(input: InputStream, targetFile: File) {
        val tmpFile = File(targetFile.parentFile, "${targetFile.name}.tmp")
        targetFile.parentFile?.mkdirs()
        FileOutputStream(tmpFile).use { fos ->
            val buf = ByteArray(65536)
            while (true) {
                val n = input.read(buf)
                if (n <= 0) break
                fos.write(buf, 0, n)
            }
            fos.flush()
            fos.fd.sync()
        }
        if (!tmpFile.renameTo(targetFile)) {
            Files.move(
                tmpFile.toPath(),
                targetFile.toPath(),
                StandardCopyOption.REPLACE_EXISTING
            )
        }
    }

    private fun readAllUtf8(input: InputStream): String {
        val baos = ByteArrayOutputStream()
        val buf = ByteArray(16384)
        while (true) {
            val n = input.read(buf)
            if (n <= 0) break
            baos.write(buf, 0, n)
        }
        return baos.toByteArray().toString(Charsets.UTF_8)
    }

    private fun looksLikeZip(bytes: ByteArray): Boolean =
        bytes.size >= 4 &&
            bytes[0] == 0x50.toByte() &&
            bytes[1] == 0x4B.toByte() &&
            bytes[2] == 0x03.toByte() &&
            bytes[3] == 0x04.toByte()

    private fun clearDirectory(dir: File) {
        dir.listFiles()?.forEach { child ->
            if (child.isDirectory) child.deleteRecursively() else child.delete()
        }
    }

    companion object {
        private const val DEMO_ASSET_NAME = "sample_campus.json"
        private const val PROJECT_JSON_ENTRY = "project.json"
        private const val TILES_SQLITE_ENTRY = "tiles.sqlite"
        private const val BLUEPRINTS_PREFIX = "blueprints/"
        private const val TILE_CACHE_CAPACITY = 64
    }
}
