# clglxt 部署方案（方案 C 终稿）

## 一、方案定位
- **目标**：宝塔面板上跑通 PostgreSQL + Redis + Celery 车辆维修系统
- **核心**：**不走「先复刻 SQLite 再优化」**，直接 PG + Redis + Celery 异步全链路
- **安装方式**：**浏览器安装向导（推荐）** —— 上传源码 → 访问域名 → 自动跳安装向导 → 填 DB/Redis/管理员 → 一键初始化 → 进系统
- **数据迁移**：**通过老 FMS API 拉取导入**（/import-fms 占位，FMS 停机前实施）
- **版本**：随意，能跑就行（requirements 已改宽松 `>=` 约束）

---

## 二、部署流程（安装向导版）

### 1. 环境准备
- Debian 12 + 宝塔面板
- PostgreSQL 15+（宝塔软件商店一键装，记下端口、postgres 密码）
- Redis 7+（宝塔软件商店一键装）
- Nginx（宝塔自带）
- Python 3.11+（`apt install python3-venv python3-dev build-essential`）

### 2. 宝塔建库
数据库 → PostgreSQL → 添加数据库
```
库名: clglxt
用户: clglxt
密码: <强密码>
```

### 3. 上传源码 + 装依赖
```bash
mkdir -p /www/wwwroot/clglxt
cd /www/wwwroot/clglxt
# 上传 server/ frontend/ client/ docs/ 整个项目
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 宝塔建站点
- 站点 → 添加站点，PHP 版本选「纯静态」
- 域名绑定你的域名，开启 SSL
- **反向代理**：目标 URL `http://127.0.0.1:8000`

或用 `server/deploy/nginx-clglxt.conf` 手动配置。

### 5. 启动后端
```bash
bash server/start.sh
```
systemd 方式：
```bash
cp server/deploy/clglxt-web.service      /etc/systemd/system/
cp server/deploy/clglxt-worker.service   /etc/systemd/system/
cp server/deploy/clglxt-beat.service     /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now clglxt-web clglxt-worker clglxt-beat
```

### 6. 访问域名 → 安装向导
浏览器打开 `https://你的域名/` → 自动跳 `/install`

1. **数据库**：地址 `127.0.0.1`、端口 `5432`、库 `clglxt`、用户 `clglxt`、密码 → 点「测试连接」
2. **Redis**：`redis://127.0.0.1:6379/0`
3. **站点**：站点名称、管理员用户名、密码（8 位以上，含字母数字）
4. 点「开始安装」

安装完成后：
- 自动建 45 张表
- seed 3 个内置角色（内部管理员/汽修厂管理员/超管）+ 管理员 + 超管通配权限
- 生成 32 字节 JWT_SECRET 写入 `.env`
- 写 `data/.install_done` 锁

**必须重启一次站点**（宝塔：站点 → 重启 / `systemctl restart clglxt-web`），让新 `.env` 生效，然后 `https://域名/login` 登录。首次登录强制改密。

### 7. 验证
```bash
curl -fsS https://你的域名/api/healthz
curl -fsS https://你的域名/api/install/status   # 应返回 installed=true
```

---

## 三、目录约定
```
/www/wwwroot/clglxt/server    后端代码
/www/wwwroot/clglxt/server/data   上传文件 + .install_done 安装锁
/www/wwwroot/clglxt/frontend  前端源码（方案 B）
/var/log/clglxt/              日志
```

---

## 四、参考链接
- `docs/deploy.md` —— 生产部署详解（含 systemd + nginx 配置）
- `docs/review-report-v1.md` —— 代码评审报告（P0/P1 全部修复清单）
