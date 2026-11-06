# clglxt 安卓 APP 打包指南（Kotlin + Gradle WebView 壳）

## 1. 前置

- JDK 17+
- Android SDK 34（已下载到 `/tmp/android-sdk`）
- Gradle 8.7（wrapper 已准备好）

## 2. 本地打包

```bash
cd /vol1/1000/AI/octop/clglxt/android

# 首次生成 Gradle wrapper（可选）
./gradlew wrapper --gradle-version 8.7

# 编译 Release APK
./gradlew assembleRelease
```

输出：
- `android/app/build/outputs/apk/release/app-release.apk`

## 3. 签名（生产分发推荐）

```bash
keytool -genkey -v -keystore clglxt-release.keystore -keyalg RSA -keysize 2048 -validity 10000 -alias clglxt

# 在 app/build.gradle.kts 里配置 signingConfigs.release
# 然后 ./gradlew assembleRelease --sign
```

## 4. 站点地址

- 首次启动 App 会弹出「设置服务器地址」弹窗
- 填写 IP / 域名后记住，下次启动直接加载
- 网页端 `<input type=file accept="image/*">` 拍照后原生压缩后交还（端内压完再传）

## 5. 调试

- `adb logcat | grep clglxt` 看压缩日志
- `adb logcat -s WebViewClient` 看网页加载错误

## 6. 发布

- 包名 `com.clglxt.app`
- 最小 Android 7.0
- 只展示 WebView（无其他 Activity）
- 签名后上应用市场或内网分发