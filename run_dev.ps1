# VVM 数智医生智能体 —— 一键启动
#  8001 对话+管理服务（后台）+ 前端 dev（前台，Ctrl+C 可停）
# 说明：管理（/api/v1）与对话（/api）已合并到同一个 8001 服务，不再需要 8000。
# 用法：powershell ./run_dev.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 优先用项目 venv 的 python，否则回退系统 python
$python = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" }

Write-Host "[1/2] 启动 8001 服务（对话 + 医生/技能管理） ..."
$p8001 = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "backend.chat_server:app", "--port", "8001") -WorkingDirectory $root -WindowStyle Hidden -PassThru

# 给 uvicorn 留出初始化（连 MySQL、建表）时间
Start-Sleep -Seconds 5

if ($p8001.HasExited) {
    Write-Warning "8001 服务异常退出（pid=$($p8001.Id)）——请检查端口是否被占用、MySQL 是否可连、config 配置是否正确。"
}

Write-Host "后端已启动：8001 pid=$($p8001.Id)"
Write-Host "[2/2] 启动前端 dev（5173）..."
Set-Location (Join-Path $root "frontend")
npm run dev