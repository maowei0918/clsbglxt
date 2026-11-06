package com.clglxt.app.ui

import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.clglxt.app.Api
import com.clglxt.app.R
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder

class VehiclesActivity : AppCompatActivity() {
    private lateinit var rv: RecyclerView
    private lateinit var etKw: EditText
    private val items = mutableListOf<JSONObject>()
    private lateinit var adapter: VehicleAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_vehicles)
        rv = findViewById(R.id.rv)
        etKw = findViewById(R.id.et_keyword)
        adapter = VehicleAdapter(items)
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter
        findViewById<View>(R.id.btn_search).setOnClickListener { load() }
        etKw.setOnEditorActionListener { _, _, _ -> load(); true }
        load()
    }

    private fun load() {
        val kw = etKw.text.toString().trim()
        val path = "/api/vehicles?keyword=${URLEncoder.encode(kw, "utf-8")}&page_size=100"
        Api.getAsync(this, path,
            onOk = { d ->
                val arr = d.optJSONArray("items") ?: JSONArray()
                items.clear()
                for (i in 0 until arr.length()) items.add(arr.getJSONObject(i))
                runOnUiThread { adapter.notifyDataSetChanged() }
            },
            onErr = { e -> runOnUiThread { Toast.makeText(this, e, Toast.LENGTH_SHORT).show() } }
        )
    }
}
