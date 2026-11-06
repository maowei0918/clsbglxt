package com.clglxt.app.ui

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.clglxt.app.R
import org.json.JSONArray
import org.json.JSONObject

/** 单条记录详情：把整条 JSON 按字段名逐行展示 */
class DetailActivity : AppCompatActivity() {

    private val labels = mapOf(
        "id" to "ID", "order_no" to "工单号", "plate_no" to "车牌号",
        "vehicle_id" to "车辆ID", "status" to "状态", "current_seq" to "当前步骤",
        "created_at" to "创建时间", "updated_at" to "更新时间",
        "name" to "名称", "title" to "标题", "supplier" to "供应商",
        "supplier_id" to "供应商ID", "contact" to "联系人", "phone" to "电话",
        "address" to "地址", "remark" to "备注", "username" to "用户名",
        "department_id" to "部门ID", "user_id" to "用户ID", "user_name" to "操作人",
        "check_date" to "检查日期", "last_run_at" to "上次运行", "enabled" to "启用",
        "input_text" to "原始地址", "lng" to "经度", "lat" to "纬度",
        "radius_m" to "半径(米)", "results" to "检查项", "photos" to "照片",
        "has_abnormal" to "有异常", "is_active" to "有效", "code" to "编码",
        "total" to "总数", "kind" to "类型", "filename" to "文件名"
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_detail)
        title = intent.getStringExtra("title") ?: "详情"
        val tv = findViewById<TextView>(R.id.tv_detail)
        val raw = intent.getStringExtra("json") ?: "{}"
        val jo = try { JSONObject(raw) } catch (e: Exception) { JSONObject() }
        val sb = StringBuilder()
        val keys = jo.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            sb.append(labels[k] ?: k).append("：").append(format(jo.opt(k))).append("\n\n")
        }
        tv.text = sb.toString().ifEmpty { "无数据" }
    }

    private fun format(v: Any?): String = when (v) {
        null, JSONObject.NULL -> "-"
        is JSONArray -> if (v.length() == 0) "-" else v.toString()
        is JSONObject -> v.toString(2)
        else -> v.toString()
    }
}
