package com.clglxt.app

import android.content.Context

/** 登录态：token + uid 持久化到 SharedPreferences */
object Auth {
    private const val PREF = "clglxt_auth"
    private const val KEY_TOKEN = "token"
    private const val KEY_UID = "uid"
    private const val KEY_NAME = "name"

    private fun sp(ctx: Context) = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)

    fun token(ctx: Context): String = sp(ctx).getString(KEY_TOKEN, "").orEmpty()
    fun uid(ctx: Context): Int = sp(ctx).getInt(KEY_UID, 0)
    fun name(ctx: Context): String = sp(ctx).getString(KEY_NAME, "").orEmpty()

    fun isLoggedIn(ctx: Context): Boolean = token(ctx).isNotEmpty()

    fun save(ctx: Context, token: String, uid: Int, name: String = "") {
        sp(ctx).edit()
            .putString(KEY_TOKEN, token)
            .putInt(KEY_UID, uid)
            .putString(KEY_NAME, name)
            .apply()
    }

    fun clear(ctx: Context) {
        sp(ctx).edit().clear().apply()
    }
}
