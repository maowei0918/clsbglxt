package com.clglxt.app.ui

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.clglxt.app.Api
import com.clglxt.app.R
import org.json.JSONArray
import org.json.JSONObject

/**
 * 通用列表页：endpoint + 标题/副标题字段名由 Intent 传入。
 * 支持下拉刷新、本地搜索、点击详情、可选分页加载。
 *
 * Intent extras:
 *   title / endpoint / titleKeys / subKeys
 *   readEndpoint  点击前先 POST 的模板（含 {id}）
 *   paged         true 时按 page/page_size 滚动加载
 */
class ListActivity : AppCompatActivity() {
    private lateinit var rv: RecyclerView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var etSearch: EditText
    private lateinit var tvEmpty: TextView
    private val items = mutableListOf<JSONObject>()   // 当前展示
    private val all = mutableListOf<JSONObject>()     // 已加载全量
    private lateinit var adapter: GenericAdapter
    private lateinit var endpoint: String
    private lateinit var readEndpoint: String
    private var paged = false
    private var page = 0
    private var hasMore = false
    private var loading = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_list)
        rv = findViewById(R.id.rv)
        refresh = findViewById(R.id.refresh)
        etSearch = findViewById(R.id.et_search)
        tvEmpty = findViewById(R.id.tv_empty)
        title = intent.getStringExtra("title") ?: "列表"
        endpoint = intent.getStringExtra("endpoint") ?: "/api/vehicles?page_size=100"
        readEndpoint = intent.getStringExtra("readEndpoint") ?: ""
        paged = intent.getBooleanExtra("paged", false)
        val titleKeys = intent.getStringArrayExtra("titleKeys") ?: arrayOf("name")
        val subKeys = intent.getStringArrayExtra("subKeys") ?: arrayOf("id")

        adapter = GenericAdapter(items, titleKeys.toList(), subKeys.toList()) { jo -> openDetail(jo) }
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = adapter
        refresh.setOnRefreshListener { load() }
        rv.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (!paged || loading || !hasMore) return
                val lm = recyclerView.layoutManager as? LinearLayoutManager ?: return
                if (lm.findLastVisibleItemPosition() >= items.size - 3) loadMore()
            }
        })
        etSearch.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) { filter(s?.toString().orEmpty()) }
        })
        showEmpty("加载中…")
        load()
    }

    /** 打开详情；配置了 readEndpoint 时先标记已读（{id} 会被替换） */
    private fun openDetail(jo: JSONObject) {
        if (readEndpoint.isNotEmpty()) {
            val id = jo.optInt("id", 0).toString()
            Api.postAsync(this, readEndpoint.replace("{id}", id), null,
                onOk = { _ -> startDetail(jo) },
                onErr = { _ -> startDetail(jo) }
            )
        } else {
            startDetail(jo)
        }
    }

    private fun startDetail(jo: JSONObject) {
        startActivity(Intent(this, DetailActivity::class.java).apply {
            putExtra("title", title)
            putExtra("json", jo.toString())
        })
    }

    private fun pageUrl(p: Int): String {
        if (!paged) return endpoint
        val sep = if (endpoint.contains("?")) "&" else "?"
        return "$endpoint${sep}page=$p&page_size=$PAGE_SIZE"
    }

    private fun load() {
        refresh.isRefreshing = true
        loading = true
        Api.getAnyAsync(this, pageUrl(1),
            onOk = { data ->
                val (arr, more) = extractPage(data)
                val list = ArrayList<JSONObject>()
                for (i in 0 until arr.length()) list.add(arr.getJSONObject(i))
                runOnUiThread {
                    page = 1
                    all.clear()
                    all.addAll(list)
                    hasMore = more
                    loading = false
                    filter(etSearch.text.toString())
                    refresh.isRefreshing = false
                }
            },
            onErr = { e ->
                runOnUiThread {
                    loading = false
                    refresh.isRefreshing = false
                    if (all.isEmpty()) showEmpty("加载失败：$e")
                    Toast.makeText(this, e, Toast.LENGTH_SHORT).show()
                }
            }
        )
    }

    private fun loadMore() {
        if (!paged || loading || !hasMore) return
        loading = true
        Api.getAnyAsync(this, pageUrl(page + 1),
            onOk = { data ->
                val (arr, more) = extractPage(data)
                val list = ArrayList<JSONObject>()
                for (i in 0 until arr.length()) list.add(arr.getJSONObject(i))
                runOnUiThread {
                    all.addAll(list)
                    page += 1
                    hasMore = more
                    loading = false
                    filter(etSearch.text.toString())
                }
            },
            onErr = { _ -> runOnUiThread { loading = false } }
        )
    }

    /** 本地过滤：整行 JSON 文本匹配关键字（忽略大小写） */
    private fun filter(kw: String) {
        items.clear()
        if (kw.isBlank()) {
            items.addAll(all)
        } else {
            items.addAll(all.filter { it.toString().contains(kw, ignoreCase = true) })
        }
        adapter.notifyDataSetChanged()
        when {
            items.isNotEmpty() -> tvEmpty.visibility = View.GONE
            all.isEmpty() -> showEmpty("暂无数据")
            else -> showEmpty("没有匹配的记录")
        }
    }

    private fun showEmpty(msg: String) {
        tvEmpty.text = msg
        tvEmpty.visibility = View.VISIBLE
    }

    /** 兼容多种返回：对象+items（含 has_more）、数组、[items,total] 元组 */
    private fun extractPage(data: Any): Pair<JSONArray, Boolean> = when (data) {
        is JSONObject -> {
            val arr = data.optJSONArray("items") ?: JSONArray()
            arr to data.optBoolean("has_more", false)
        }
        is JSONArray -> {
            val arr = if (data.length() > 0 && data.opt(0) is JSONArray) data.getJSONArray(0) else data
            arr to false
        }
        else -> JSONArray() to false
    }

    companion object {
        private const val PAGE_SIZE = 20
    }
}
