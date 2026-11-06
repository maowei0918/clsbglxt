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

class VehicleUsesActivity : AppCompatActivity() {
    private lateinit var rv: RecyclerView
    private lateinit var etPlate: EditText
    private val items = mutableListOf<JSONObject>()
    private lateinit var adapter: VehicleUseAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_vehicle_uses)
        rv = findViewById(R.id.rv)
        etPlate = findViewById(R.id.et_plate)
        adapter = VehicleUseAdapter(items)
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter
        findViewById<View>(R.id.btn_search).setOnClickListener { load() }
        load()
    }

    private fun load() {
        val plate = etPlate.text.toString().trim()
        val path = "/api/vehicle-uses?plate_no=${URLEncoder.encode(plate, "utf-8")}&page_size=100"
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
