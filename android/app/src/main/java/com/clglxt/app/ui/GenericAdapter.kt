package com.clglxt.app.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.clglxt.app.R
import org.json.JSONObject

/** 通用列表适配器：按传入的字段名从每条 JSON 提取标题/副标题 */
class GenericAdapter(
    private val items: MutableList<JSONObject>,
    private val titleKeys: List<String>,
    private val subKeys: List<String>,
    private val onClick: ((JSONObject) -> Unit)? = null
) : RecyclerView.Adapter<GenericAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val title: TextView = v.findViewById(R.id.tv_title)
        val sub: TextView = v.findViewById(R.id.tv_sub)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_list, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(h: VH, position: Int) {
        val item = items[position]
        h.title.text = titleKeys.joinToString(" · ") { k -> item.optString(k, "") }
        h.sub.text = subKeys.joinToString(" · ") { k -> item.optString(k, "") }
        h.itemView.setOnClickListener { onClick?.invoke(item) }
    }
}
