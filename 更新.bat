@echo off
cd /d "%~dp0"
if exist ".git" (
    echo 正在从 GitHub 拉取更新...
    git pull --ff-only
    if errorlevel 1 (
        echo 更新失败。请检查网络，或在 GitHub 上手动下载最新 Release。
    ) else (
        echo 更新完成。重新运行 启动.bat 即可。
    )
    pause
    exit /b
)

echo 当前不是 git 克隆版本，正在打开 Releases 页面...
start "" "https://github.com/YianBu/NightVoyage-Console/releases"
