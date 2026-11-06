package com.clglxt.app

import android.content.Context
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

/** 接口异常：携带后端 msg */
class ApiError(message: String) : Exception(message)

/**
 * 网络层：OkHttp + JWT。
 * 同步 raw/get/post/put/delete，异步 getAsync/getListAsync/postAsync。
 * 后端统一 {code,data,msg}，code!=0 抛 ApiError。
 */
object Api {
    private val client = OkHttpClient()
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    private fun base(ctx: Context): String = ServerConfig.get(ctx)

    /** 完整响应体（含 code/data/msg），供内部统一处理 */
    private fun raw(ctx: Context, method: String, path: String, body: JSONObject?): JSONObject {
        val url = base(ctx) + path
        val builder = Request.Builder().url(url)
            .header("Authorization", "Bearer " + Auth.token(ctx))
            .header("Content-Type", "application/json")
        val req = if (body != null) {
            builder.method(method, body.toString().toRequestBody(jsonType)).build()
        } else {
            builder.method(method, null).build()
        }
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            val jo = try {
                JSONObject(text)
            } catch (e: Exception) {
                throw ApiError("响应解析失败（HTTP ${resp.code}）")
            }
            val code = jo.optInt("code", -1)
            if (code != 0) throw ApiError(jo.optString("msg", "请求失败"))
            return jo
        }
    }

    /** GET，data 为对象 */
    fun get(ctx: Context, path: String): JSONObject =
        raw(ctx, "GET", path, null).optJSONObject("data") ?: JSONObject()

    /** POST，data 为对象 */
    fun post(ctx: Context, path: String, body: JSONObject? = null): JSONObject =
        raw(ctx, "POST", path, body).optJSONObject("data") ?: JSONObject()

    /** PUT，data 为对象 */
    fun put(ctx: Context, path: String, body: JSONObject? = null): JSONObject =
        raw(ctx, "PUT", path, body).optJSONObject("data") ?: JSONObject()

    /** DELETE，data 为对象 */
    fun delete(ctx: Context, path: String): JSONObject =
        raw(ctx, "DELETE", path, null).optJSONObject("data") ?: JSONObject()

    /** GET，data 为数组 */
    fun getList(ctx: Context, path: String): JSONArray =
        raw(ctx, "GET", path, null).optJSONArray("data") ?: JSONArray()

    // ── 异步封装：后台线程请求，回调仍为调用线程（页面需自行切主线程） ──

    fun getAsync(ctx: Context, path: String, onOk: (JSONObject) -> Unit, onErr: (String) -> Unit) {
        Thread {
            try { onOk(get(ctx, path)) } catch (e: Exception) { onErr(e.message ?: "请求失败") }
        }.start()
    }

    fun getListAsync(ctx: Context, path: String, onOk: (JSONArray) -> Unit, onErr: (String) -> Unit) {
        Thread {
            try { onOk(getList(ctx, path)) } catch (e: Exception) { onErr(e.message ?: "请求失败") }
        }.start()
    }

    /** GET，data 可能是对象或数组，原样返回（供兼容不同端点） */
    fun getAnyAsync(ctx: Context, path: String, onOk: (Any) -> Unit, onErr: (String) -> Unit) {
        Thread {
            try {
                val d = raw(ctx, "GET", path, null).opt("data") ?: JSONObject()
                onOk(d)
            } catch (e: Exception) { onErr(e.message ?: "请求失败") }
        }.start()
    }

    fun postAsync(ctx: Context, path: String, body: JSONObject?, onOk: (JSONObject) -> Unit, onErr: (String) -> Unit) {
        Thread {
            try { onOk(post(ctx, path, body)) } catch (e: Exception) { onErr(e.message ?: "请求失败") }
        }.start()
    }
}
