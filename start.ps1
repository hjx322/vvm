# ./start.ps1
# 管理(/api/v1)与对话(/api)已并入同一个 8001 服务，只需起 8001 + 前端
Start-Process powershell "cd F:\1Github\vm; .\.venv\Scripts\python.exe -m uvicorn backend.chat_server:app --port 8001"
Start-Process powershell "cd F:\1Github\vm\frontend; npm run dev"