package com.clglxt.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.clglxt.app.R
import org.json.JSONObject

class VehicleUseAdapter(private val items: MutableList<JSONObject>) :
    RecyclerView.Adapter<VehicleUseAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val plate: TextView = v.findViewById(R.id.tv_plate)
        val dept: TextView = v.findViewById(R.id.tv_dept)
        val date: TextView = v.findViewById(R.id.tv_date)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_vehicle_use, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val it = items[position]
        h.plate.text = it.optString("plate_no", "")
        h.dept.text = it.optString("dept_name", "")
        h.date.text = buildString {
            append(it.optString("start_date", ""))
            append(" ~ ").append(it.optString("end_date", ""))
        }
    }
}
