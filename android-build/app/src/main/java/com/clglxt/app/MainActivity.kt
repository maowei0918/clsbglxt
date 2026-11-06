package com.clglxt.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import androidx.core.content.FileProvider
import android.util.Log
import android.view.KeyEvent
import android.webkit.JsResult
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.io.FileOutputStream

/**
 * clglxt 安卓客户端 —— WebView 壳 + 原生照片压缩。
 *
 * 关键能力：
 *  - 首次启动让用户填服务器地址（不用改代码重编译）
 *  - 网页 input type=file 且 accept 为图片时拍照 → 原生压缩后再交还给网页
 *  - 返回键在 SPA 内后退，退到底才退出 App
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraPhotoUri: Uri? = null

    companion object {
        private const val REQUEST_FILE = 1001
        private const val REQUEST_CAMERA = 1002
        private const val MAX_DIM = 1920       // 压缩长边
        private const val JPEG_QUALITY = 80    // 压缩质量
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mediaPlaybackRequiresUserGesture = false
            // 内网用 http 时仍允许混合内容（否则页面无法加载）；走 https 时不受影响。
            // 风险由 ServerConfig 的地址白名单兜底（仅允许内网/本地/已知域名）。
            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            settings.useWideViewPort = true
            settings.loadWithOverviewMode = true

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                    url?.let {
                        if (it.startsWith("intent://") || it.startsWith("market://")) {
                            try {
                                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it)))
                            } catch (e: Exception) {
                                Log.w("clglxt", "外部链接打开失败: $it", e)
                            }
                            return true
                        }
                    }
                    return false
                }

                override fun onReceivedError(
                    view: WebView?, request: android.webkit.WebResourceRequest?,
                    error: android.webkit.WebResourceError?
                ) {
                    // 首屏加载失败时给个离线提示
                    if (request?.isForMainFrame == true) {
                        view?.loadDataWithBaseURL(
                            null,
                            "<html><body style='font-family:sans-serif;text-align:center;padding-top:30%'>" +
                                    "<h3>无法连接服务器</h3><p>请检查网络或服务器地址</p>" +
                                    "<p style='color:#888;font-size:12px'>${request.url}</p></body></html>",
                            "text/html", "utf-8", null
                        )
                    }
                }
            }

            webChromeClient = object : WebChromeClient() {
                override fun onJsAlert(
                    view: WebView?, url: String?, message: String?, result: JsResult?
                ): Boolean {
                    AlertDialog.Builder(this@MainActivity)
                        .setMessage(message)
                        .setPositiveButton("OK") { _, _ -> result?.confirm() }
                        .setOnCancelListener { result?.cancel() }
                        .show()
                    return true
                }

                /** 拦截网页的文件选择：拍照 / 相册都能选，选中的图片原生压缩后交还。 */
                override fun onShowFileChooser(
                    view: WebView?,
                    callback: ValueCallback<Array<Uri>>?,
                    params: FileChooserParams?
                ): Boolean {
                    filePathCallback?.onReceiveValue(null)
                    filePathCallback = callback

                    // 拍照（输出到 FileProvider 的 cache 目录）
                    val cameraFile = File(cacheDir, "clglxt_camera_${System.currentTimeMillis()}.jpg")
                    cameraPhotoUri = FileProvider.getUriForFile(
                        this@MainActivity, "$packageName.fileprovider", cameraFile
                    )
                    val capture = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                        putExtra(MediaStore.EXTRA_OUTPUT, cameraPhotoUri)
                        addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                    }
                    val captureIntent = Intent.createChooser(
                        Intent(Intent.ACTION_GET_CONTENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "image/*"
                        }, "选择照片"
                    ).apply {
                        putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(capture))
                    }
                    startActivityForResult(captureIntent, REQUEST_FILE)
                    return true
                }
            }

            addJavascriptInterface(WebAppInterface(), "Android")
        }

        // 安全：外部链接/重定向不得在 WebView 内加载，一律交给系统浏览器，
        // 避免用户填写站点后被人劫持跳转到钓鱼页调用 Android 桥。
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView?, request: android.webkit.WebResourceRequest?
            ): Boolean {
                val url = request?.url?.toString() ?: return false
                if (url.startsWith("intent://") || url.startsWith("market://")) {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    } catch (e: Exception) {
                        Log.w("clglxt", "外部链接打开失败: $url", e)
                    }
                    return true
                }
                val scheme = request?.url?.scheme ?: ""
                // 非 http(s)（about:/blob:/data: 等）放行；http(s) 仅允许同源
                if (scheme != "http" && scheme != "https") return false
                return !isSameHost(url)
            }
        }

        setContentView(webView)
        val saved = ServerConfig.get(this)
        if (saved.isEmpty()) askServerUrl() else webView.loadUrl(saved)
    }

    /** 目标地址是否与已保存站点同 host（域名白名单）。 */
    private fun isSameHost(url: String): Boolean {
        return try {
            val target = Uri.parse(url)
            val server = Uri.parse(ServerConfig.get(this))
            val th = target.host
            th != null && th.equals(server.host, ignoreCase = true)
        } catch (e: Exception) {
            false
        }
    }

    /** 当前 WebView 页面是否来自可信站点（供 JS 桥鉴权）。 */
    private fun callerIsTrusted(): Boolean {
        val current = webView.url ?: return false
        return isSameHost(current)
    }

    /** 首次启动弹窗：填写站点地址。 */
    private fun askServerUrl() {
        val input = EditText(this).apply {
            hint = "http://192.168.1.100 或 https://clglxt.example.com"
            setText(ServerConfig.get(this@MainActivity))
            setSingleLine()
            setPadding(48, 32, 48, 8)
        }
        AlertDialog.Builder(this)
            .setTitle("设置服务器地址")
            .setMessage("填写 clglxt 站点地址（内网可填 IP）")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("连接") { _, _ ->
                val url = ServerConfig.normalize(input.text.toString())
                if (url.isNotEmpty()) {
                    ServerConfig.save(this, url)
                    webView.loadUrl(url)
                } else {
                    Toast.makeText(this, "地址为空，请重新填写", Toast.LENGTH_LONG).show()
                    askServerUrl()
                }
            }
            .show()
    }

    @Deprecated("兼容旧 API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQUEST_CAMERA -> {
                if (resultCode == Activity.RESULT_OK) {
                    cameraPhotoUri?.let { uri ->
                        val compressed = compressPhoto(uri)
                        if (compressed != null) {
                            filePathCallback?.onReceiveValue(arrayOf(Uri.fromFile(compressed)))
                        } else {
                            filePathCallback?.onReceiveValue(null)
                        }
                    } ?: filePathCallback?.onReceiveValue(null)
                } else {
                    filePathCallback?.onReceiveValue(null)
                }
                filePathCallback = null
                cameraPhotoUri = null
            }
            REQUEST_FILE -> {
                if (resultCode == Activity.RESULT_OK) {
                    // 若选的是图片，压缩后再交还；其它类型原样返回
                    val uri = data?.data
                    val mime = uri?.let { contentResolver.getType(it) } ?: ""
                    val outUri = if (uri != null && mime.startsWith("image/")) {
                        compressPhoto(uri)?.let { Uri.fromFile(it) } ?: uri
                    } else uri
                    filePathCallback?.onReceiveValue(if (outUri != null) arrayOf(outUri) else null)
                } else {
                    filePathCallback?.onReceiveValue(null)
                }
                filePathCallback = null
            }
        }
    }

    /**
     * 压缩照片：长边 ≤ 1920，JPEG 80%。
     * 12MP (4000x3000) ~13MB → ~950KB，省 93% 带宽与服务器 CPU。
     */
    private fun compressPhoto(uri: Uri): File? {
        return try {
            val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return null
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null

            val ratio = MAX_DIM.toFloat() / maxOf(bitmap.width, bitmap.height)
            val (tw, th) = if (ratio < 1f) {
                (bitmap.width * ratio).toInt() to (bitmap.height * ratio).toInt()
            } else {
                bitmap.width to bitmap.height
            }
            val scaled = Bitmap.createScaledBitmap(bitmap, tw, th, true)

            val outFile = File(cacheDir, "clglxt_photo_${System.currentTimeMillis()}.jpg")
            FileOutputStream(outFile).use { fos ->
                scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, fos)
            }
            Log.d("clglxt", "压缩 ${bitmap.width}x${bitmap.height} → ${tw}x${th} " +
                    "(${bytes.size / 1024}KB → ${outFile.length() / 1024}KB)")
            outFile
        } catch (e: Exception) {
            Log.e("clglxt", "照片压缩失败", e)
            null
        }
    }

    private inner class WebAppInterface {
        @android.webkit.JavascriptInterface
        fun showToast(message: String) {
            runOnUiThread {
                Toast.makeText(this@MainActivity, message, Toast.LENGTH_SHORT).show()
            }
        }

        /** 仅当页面来自可信站点时允许切换服务器（防钓鱼页劫持桥接）。 */
        @android.webkit.JavascriptInterface
        fun setServerUrl(url: String) {
            if (!callerIsTrusted()) return
            runOnUiThread {
                val u = ServerConfig.normalize(url)
                if (u.isNotEmpty()) {
                    ServerConfig.save(this@MainActivity, u)
                    webView.loadUrl(u)
                }
            }
        }

        @android.webkit.JavascriptInterface
        fun getServerUrl(): String = if (callerIsTrusted()) ServerConfig.get(this@MainActivity) else ""
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (webView.canGoBack()) {
                webView.goBack()
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onPause() {
        super.onPause()
        webView.onPause()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onDestroy() {
        super.onDestroy()
        webView.destroy()
    }
}
