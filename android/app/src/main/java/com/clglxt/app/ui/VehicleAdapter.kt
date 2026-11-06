package com.clglxt.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.clglxt.app.R
import org.json.JSONObject

class VehicleAdapter(private val items: MutableList<JSONObject>) :
    RecyclerView.Adapter<VehicleAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val plate: TextView = v.findViewById(R.id.tv_plate)
        val brand: TextView = v.findViewById(R.id.tv_brand)
        val status: TextView = v.findViewById(R.id.tv_status)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_vehicle, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val it = items[position]
        h.plate.text = it.optString("plate_no", "")
        h.brand.text = buildString {
            append(it.optString("brand", ""))
            if (it.optString("vehicle_type_name").isNotEmpty()) append(" · ").append(it.optString("vehicle_type_name"))
        }
        h.status.text = it.optString("status", "")
    }
}
