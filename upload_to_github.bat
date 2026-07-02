@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: 保证在脚本所在目录执行
cd /d "%~dp0"

echo [检查] 正在检查 Git 环境...

:: 1. 检查 Git 是否安装
where git >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到 Git，请先安装 Git: https://git-scm.com/download/win
    pause
    exit /b
)

:: 2. 检查是否初始化
if not exist ".git" (
    echo [初始化] 正在初始化 Git 仓库...
    git init
)

:: 3. 统一分支为 main（避免无分支导致推送失败）
git branch -M main 2>nul

:: 4. 配置远程仓库地址（强制覆盖为用户提供地址）
set "REPO_URL=https://github.com/awaqwq233-233/J.A.C..git"
for /f "tokens=*" %%R in ('git remote') do (
    if /i "%%R"=="origin" (
        git remote set-url origin %REPO_URL%
    )
)
git remote add origin %REPO_URL% 2>nul

:: 5. 检查并配置提交用户信息（首次使用必需）
for /f "delims=" %%A in ('git config --global user.name 2^>nul') do set "GNAME=%%A"
for /f "delims=" %%A in ('git config --global user.email 2^>nul') do set "GMAIL=%%A"
if "!GNAME!"=="" (
    echo [提示] 第一次使用需要配置 Git 用户信息
    set /p GNAME="请输入 Git 用户名（显示在提交记录中）: "
    git config --global user.name "!GNAME!"
)
if "!GMAIL!"=="" (
    set /p GMAIL="请输入 Git 邮箱（用于标识提交者）: "
    git config --global user.email "!GMAIL!"
)

:: 6. 添加更改
echo [同步] 正在添加文件...
git add -A

:: 7. 生成提交信息（可传参覆盖）
set "commit_msg=Auto sync: %date% %time%"
if not "%~1"=="" set "commit_msg=%~1"
echo [同步] 提交信息: !commit_msg!

:: 8. 如果有变更就提交；如果是新仓库则创建初始提交
git diff --cached --quiet
if %errorlevel% neq 0 (
    git commit -m "!commit_msg!"
) else (
    git rev-parse --verify HEAD >nul 2>nul
    if %errorlevel% neq 0 (
        echo [提示] 没有变更，将创建空提交以初始化仓库
        git commit --allow-empty -m "Initial commit"
    ) else (
        echo [提示] 没有需要提交的变更，继续推送
    )
)

:: 9. 拉取远程（避免历史不一致导致推送冲突）
git pull --rebase origin main --allow-unrelated-histories 2>nul

:: 10. 推送到远程
echo [同步] 正在推送到 GitHub...
git push -u origin main
if %errorlevel% neq 0 (
    echo [警告] 推送到 main 分支失败，尝试推送到 master 分支...
    git push -u origin master
    if !errorlevel! neq 0 (
        echo [错误] 推送失败。可能原因：无权限/仓库不存在/网络问题。
        echo [建议] 若是私人仓库，请先在 GitHub 上创建仓库并确认有写入权限。
        echo [建议] 若需凭据管理，可运行：git credential-manager-core configure
    )
)

echo.
echo [完成] 脚本执行完毕。
pause
