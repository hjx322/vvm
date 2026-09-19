# 数智医生智能体 - 前端界面

基于 **Vue 3 + Vite** 的 Web 对话界面，通过后端对话服务（`backend/chat_server.py`，端口 8001）与数智医生智能体交互。

## 目录结构

```
frontend/
├── index.html          # 入口 HTML
├── vite.config.js      # Vite 配置（含 /api 代理到 8001）
├── package.json
└── src/
    ├── main.js         # Vue 应用入口
    ├── App.vue         # 对话界面（消息列表 + 流式渲染 + 参数配置）
    └── style.css       # 全局主题变量（对话框/深色模式）
```

## 运行

先启动后端对话服务（项目根目录，使用 `.venv`）：

```bash
# 方式一：uvicorn
.venv/Scripts/python.exe -m uvicorn backend.chat_server:app --port 8001

# 方式二：直接运行（需要 import 路径正确，推荐方式一）
```

再启动前端（`frontend/` 目录）：

```bash
cd frontend
npm install   # 首次需要
npm run dev   # 默认 http://localhost:5173
```

打开浏览器访问 http://localhost:5173 ，即可对话。

> 开发模式下，前端把 `/api` 请求通过 Vite proxy 转发到 `http://localhost:8001`，因此浏览器无需配置 CORS。

## 特性

- **流式回复**（SSE）：AI 回复逐字显示，打字机效果
- **多轮对话**：一次会话固定 `workflowId`，后端通过 `thread_id` 保留上下文记忆
- **"会话参数"**：可切换 CRM / 会话名 / 医生 id（折叠面板）
- **新会话**：重置并生成新的 `workflowId`
- **停止**：请求期间可中途停止流式输出

## 构建生产包

```bash
npm run build     # 产物在 frontend/dist/
```
