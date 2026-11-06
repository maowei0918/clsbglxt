package com.clglxt.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
import com.clglxt.app.ui.LoginActivity
import com.clglxt.app.ui.ListActivity
import com.clglxt.app.ui.RawActivity
import com.clglxt.app.ui.VehiclesActivity
import com.clglxt.app.ui.VehicleUsesActivity
import com.clglxt.app.ui.VehicleAccountActivity
import com.google.android.material.navigation.NavigationView

/** 首页：导航抽屉 + 仪表盘快捷入口 */
class MainActivity : AppCompatActivity() {
    private lateinit var drawer: DrawerLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Auth.isLoggedIn(this)) {
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
            return
        }
        setContentView(R.layout.activity_main)
        drawer = findViewById(R.id.drawer)
        findViewById<View>(R.id.nav_toggle).setOnClickListener { drawer.openDrawer(GravityCompat.START) }
        val nav = findViewById<NavigationView>(R.id.nav)
        nav.setNavigationItemSelectedListener { item ->
            drawer.closeDrawer(GravityCompat.START)
            route(item.itemId)
            true
        }
        findViewById<TextView>(R.id.welcome).text = "你好，${Auth.name(this).ifEmpty { "用户" }}"
        findViewById<TextView>(R.id.server_info).text = "站点：${ServerConfig.get(this)}"
    }

    /** 每次回到首页刷新角标（保证已读后即时更新） */
    override fun onResume() {
        super.onResume()
        if (Auth.isLoggedIn(this)) refreshUnreadBadge()
    }

    /** 拉取未读通知数，把角标写到导航项右侧 */
    private fun refreshUnreadBadge() {
        Api.getAsync(this, "/api/notifications/unread-count",
            onOk = { jo ->
                val n = jo.optInt("unread", 0)
                runOnUiThread { setBadge(R.id.menu_notifications, n) }
            },
            onErr = { _ -> /* 静默：未登录或无权限时忽略 */ }
        )
    }

    private fun setBadge(itemId: Int, count: Int) {
        val nav = findViewById<NavigationView>(R.id.nav)
        val menu = nav.menu
        val item = menu.findItem(itemId) ?: return
        if (count > 0) {
            item.setActionView(R.layout.menu_badge)
            val badge = item.actionView?.findViewById<TextView>(R.id.badge_text)
            badge?.text = if (count > 99) "99+" else count.toString()
            item.actionView?.setOnClickListener { route(itemId) }
        } else {
            item.actionView = null
        }
    }

    private fun list(title: String, ep: String, t: List<String>, s: List<String>) =
        Intent(this, ListActivity::class.java).apply {
            putExtra("title", title)
            putExtra("endpoint", ep)
            putExtra("titleKeys", t.toTypedArray())
            putExtra("subKeys", s.toTypedArray())
        }

    private fun notifyList(title: String) =
        Intent(this, ListActivity::class.java).apply {
            putExtra("title", title)
            putExtra("endpoint", "/api/notifications")
            putExtra("titleKeys", arrayOf("title"))
            putExtra("subKeys", arrayOf("created_at"))
            putExtra("readEndpoint", "/api/notifications/{id}/read")
            putExtra("paged", true)
        }

    private fun raw(title: String, ep: String) =
        Intent(this, RawActivity::class.java).apply {
            putExtra("title", title)
            putExtra("endpoint", ep)
        }

    private fun route(id: Int) {
        if (id == R.id.menu_logout) {
            Auth.clear(this)
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
            return
        }
        val intent = when (id) {
            R.id.menu_vehicles -> Intent(this, VehiclesActivity::class.java)
            R.id.menu_vehicle_uses -> Intent(this, VehicleUsesActivity::class.java)
            R.id.menu_vehicle_account -> Intent(this, VehicleAccountActivity::class.java)
            R.id.menu_repairs -> list("维修工单", "/api/repairs?page_size=100",
                listOf("order_no"), listOf("status", "created_at"))
            R.id.menu_contracts -> list("合同", "/api/contracts", listOf("name"), listOf("supplier"))
            R.id.menu_suppliers -> list("供应商", "/api/suppliers", listOf("name"), listOf("contact", "phone"))
            R.id.menu_users -> list("用户", "/api/personnel?page_size=100",
                listOf("name"), listOf("username", "department_id"))
            R.id.menu_departments -> list("部门", "/api/departments", listOf("name"), listOf("remark"))
            R.id.menu_roles -> list("角色", "/api/system/roles", listOf("name"), listOf("remark"))
            R.id.menu_my_shop -> raw("我的店铺", "/api/my-shop")
            R.id.menu_fence -> list("围栏", "/api/fence/points", listOf("plate_no"), listOf("address"))
            R.id.menu_checks -> list("日检记录", "/api/daily-check/records?page_size=100",
                listOf("check_date"), listOf("vehicle_id", "user_id"))
            R.id.menu_backup -> list("备份任务", "/api/backup/jobs", listOf("name"), listOf("last_run_at"))
            R.id.menu_notifications -> notifyList("通知")
            R.id.menu_settings -> Intent(this, SettingsActivity::class.java)
            R.id.menu_server -> Intent(this, ServerConfigActivity::class.java)
            else -> null
        }
        if (intent != null) startActivity(intent) else toast("该模块即将上线")
    }

    fun openVehicles(view: android.view.View) = startActivity(Intent(this, VehiclesActivity::class.java))
    fun openVehicleUses(view: android.view.View) = startActivity(Intent(this, VehicleUsesActivity::class.java))
    fun openVehicleAccount(view: android.view.View) = startActivity(Intent(this, VehicleAccountActivity::class.java))
    fun openServerConfig(view: android.view.View) = startActivity(Intent(this, ServerConfigActivity::class.java))

    fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
