package com.clglxt.app

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/** 站点设置：修改服务器地址 */
class ServerConfigActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_server_config)
        val et = findViewById<EditText>(R.id.et_server_url)
        et.setText(ServerConfig.get(this))
        findViewById<Button>(R.id.btn_save).setOnClickListener {
            val u = ServerConfig.normalize(et.text.toString())
            if (u.isEmpty()) {
                Toast.makeText(this, "请填写站点地址", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            ServerConfig.save(this, u)
            Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
