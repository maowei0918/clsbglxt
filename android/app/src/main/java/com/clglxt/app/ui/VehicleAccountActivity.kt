package com.clglxt.app.ui

import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.clglxt.app.Api
import com.clglxt.app.R
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder

/** 一车一账：车辆费用台账 + 异常里程提醒 */
class VehicleAccountActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_vehicle_account)
        findViewById<View>(R.id.btn_query).setOnClickListener { load() }
        findViewById<EditText>(R.id.et_plate).setOnEditorActionListener { _, _, _ -> load(); true }
    }

    private fun load() {
        val plate = findViewById<EditText>(R.id.et_plate).text.toString().trim().uppercase()
        if (plate.isEmpty()) {
            Toast.makeText(this, "请输入车牌号", Toast.LENGTH_SHORT).show()
            return
        }
        val path = "/api/vehicle-account?plate_no=${URLEncoder.encode(plate, "utf-8")}"
        Api.getAsync(this, path,
            onOk = { d -> runOnUiThread { render(d) } },
            onErr = { e -> runOnUiThread { Toast.makeText(this, e, Toast.LENGTH_SHORT).show() } }
        )
    }

    private fun render(d: JSONObject) {
        findViewById<TextView>(R.id.tv_stats).text = buildString {
            append("车牌：").append(d.optString("plate_no", "")).append("\n\n")
            append("维修：").append(d.optInt("repair_count")).append(" 次 / ¥")
                .append(fmt(d.optDouble("repair_amount", 0.0))).append("\n")
            append("加油：").append(d.optInt("fuel_count")).append(" 次 / ¥")
                .append(fmt(d.optDouble("fuel_amount", 0.0))).append(" / ")
                .append(fmt(d.optDouble("fuel_volume", 0.0))).append(" 升\n")
            append("用车：").append(d.optInt("usage_count")).append(" 次\n")
            append("里程阈值：").append(fmt(d.optDouble("mileage_warn_km", 0.0))).append(" 公里")
        }

        val warns = d.optJSONArray("mileage_warnings") ?: JSONArray()
        val box = findViewById<TextView>(R.id.tv_warnings)
        if (warns.length() > 0) {
            val sb = StringBuilder()
            sb.append("⚠ 异常里程提醒（阈值 ").append(fmt(d.optDouble("mileage_warn_km", 0.0))).append(" 公里）\n\n")
            for (i in 0 until warns.length()) {
                sb.append("• ").append(warns.getString(i)).append("\n")
            }
            box.visibility = View.VISIBLE
            box.text = sb.toString()
        } else {
            box.visibility = View.GONE
        }
    }

    private fun fmt(v: Double): String = String.format("%.2f", v)
}
