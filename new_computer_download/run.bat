@echo off
REM J.A.C. 新电脑一键依赖补全 —— Windows 启动器
REM 用法：双击本文件，或在 CMD/PowerShell 中运行  run.bat
REM 可附带参数原样传给 setup_new_computer.py，例如：run.bat --skip-models

cd /d "%~dp0"

IF "%PYTHON%"=="" (
  SET PYTHON=python
)

echo 使用 Python:
%PYTHON% --version
echo 项目根目录: %~dp0..
echo.

%PYTHON% "%~dp0setup_new_computer.py" %*
pause
