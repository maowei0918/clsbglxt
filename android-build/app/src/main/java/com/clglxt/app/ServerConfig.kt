package com.clglxt.app

import android.content.Context
import android.content.SharedPreferences

/** 站点地址存储：首次启动让用户填，之后记住，不用改代码重编译。 */
object ServerConfig {
    private const val PREF = "clglxt_prefs"
    private const val KEY_URL = "server_url"

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)

    /** 已保存的站点地址；空串表示还没设置。 */
    fun get(ctx: Context): String = sp(ctx).getString(KEY_URL, "") ?: ""

    fun save(ctx: Context, url: String) {
        val normalized = normalize(url)
        sp(ctx).edit().putString(KEY_URL, normalized).apply()
    }

    fun clear(ctx: Context) {
        sp(ctx).edit().remove(KEY_URL).apply()
    }

    /** 归一化：补 scheme、去尾部斜杠。支持内网 IP。 */
    fun normalize(raw: String): String {
        var u = raw.trim()
        if (u.isEmpty()) return ""
        if (!u.startsWith("http://") && !u.startsWith("https://")) {
            // 内网/公网默认 https；纯 IP 或 localhost 用 http
            val host = u.substringBefore('/')
            val useHttp = !host.contains('.') || host.startsWith("192.168.")
                || host.startsWith("10.") || host.startsWith("127.") || host.startsWith("localhost")
            u = (if (useHttp) "http://" else "https://") + u
        }
        return u.trimEnd('/')
    }
}
