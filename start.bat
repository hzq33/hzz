@echo off
chcp 65001 >nul
title Agent Project - 启动中...
rem 切换到脚本所在目录（支持任意路径部署）
cd /d "%~dp0"

rem 检查虚拟环境是否存在
if not exist "venv\Scripts\python.exe" (
    echo [警告] 未找到 venv 虚拟环境，请先执行：
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    echo.
)

echo ========================================
echo   Agent Project 启动
echo ========================================
echo.

echo [1/2] 启动 Agent Server (port 8080)...
start "Agent Server" cmd /c "venv\Scripts\python.exe -m uvicorn agent_server:app --reload --host 0.0.0.0 --port 8080"

echo [2/2] 启动前端 (port 3001)...
start "Frontend" cmd /c "cd frontend && npm run dev"

echo.
echo ========================================
echo   启动完成！
echo   Agent Server: http://localhost:8080
echo   Frontend:     http://localhost:3001
echo ========================================
echo.
pause
