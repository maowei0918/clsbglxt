package com.clglxt.app.ui

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.clglxt.app.Api
import com.clglxt.app.Auth
import com.clglxt.app.MainActivity
import com.clglxt.app.R
import com.clglxt.app.ServerConfig
import org.json.JSONObject

/** 登录页：站点地址 + 账号密码 */
class LoginActivity : AppCompatActivity() {
    private lateinit var etUrl: EditText
    private lateinit var etUser: EditText
    private lateinit var etPass: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)
        etUrl = findViewById(R.id.et_url)
        etUser = findViewById(R.id.et_user)
        etPass = findViewById(R.id.et_pass)
        etUrl.setText(ServerConfig.get(this))

        findViewById<Button>(R.id.btn_save_url).setOnClickListener {
            val u = ServerConfig.normalize(etUrl.text.toString())
            if (u.isEmpty()) { toast("请填写站点地址"); return@setOnClickListener }
            ServerConfig.save(this, u)
            toast("站点已保存")
        }
        findViewById<Button>(R.id.btn_login).setOnClickListener { doLogin() }
    }

    private fun doLogin() {
        val url = ServerConfig.normalize(etUrl.text.toString())
        if (url.isEmpty()) { toast("请先填写并保存站点地址"); return }
        ServerConfig.save(this, url)
        val user = etUser.text.toString().trim()
        val pass = etPass.text.toString()
        if (user.isEmpty() || pass.isEmpty()) { toast("请输入账号密码"); return }

        val body = JSONObject().put("username", user).put("password", pass)
        Api.postAsync(this, "/api/auth/login", body,
            onOk = { d ->
                val token = d.optString("token", "")
                val uObj = d.optJSONObject("user")
                val uid = uObj?.optInt("id", 0) ?: 0
                val name = uObj?.optString("name", user) ?: user
                Auth.save(this, token, uid, name)
                runOnUiThread {
                    toast("登录成功")
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                }
            },
            onErr = { e -> runOnUiThread { toast(e) } }
        )
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
