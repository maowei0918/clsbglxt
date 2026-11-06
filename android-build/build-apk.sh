#!/bin/sh
# 本地构建 APK 脚本（需 ANDROID_HOME 与 JDK 17+）
set -e
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME:-/usr/local/bin/java}"
./gradlew assembleRelease
echo "APK 输出: app/build/outputs/apk/release/"
