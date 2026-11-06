package com.clglxt.app.ui

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.clglxt.app.Api
import com.clglxt.app.R

/** 原始 JSON 详情页 */
class RawActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_raw)
        title = intent.getStringExtra("title") ?: "详情"
        val tv = findViewById<TextView>(R.id.tv_raw)
        val ep = intent.getStringExtra("endpoint") ?: ""
        Api.getAsync(this, ep,
            onOk = { data -> tv.text = data.toString(2) },
            onErr = { e -> tv.text = e }
        )
    }
}
