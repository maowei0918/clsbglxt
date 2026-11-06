package com.clglxt.app

import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.clglxt.app.ui.LoginActivity

/** 设置页：显示本地站点、账号、版本。 */
class SettingsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        findViewById<TextView>(R.id.tv_server).text = "站点地址：${ServerConfig.get(this)}"
        findViewById<TextView>(R.id.tv_user).text = "登录账号：${Auth.name(this).ifEmpty { "未登录" }} (uid=${Auth.uid(this)})"
        val vn = try {
            packageManager.getPackageInfo(packageName, 0).versionName
        } catch (e: Exception) { "1.0" }
        findViewById<TextView>(R.id.tv_version).text = "版本：$vn"
        findViewById<android.view.View>(R.id.btn_server).setOnClickListener {
            startActivity(Intent(this, ServerConfigActivity::class.java))
        }
    }
}
