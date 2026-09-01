# VVM 数智医生智能体（Digital Smart Doctor Agent）

基于 **LangGraph + FastAPI + Vue3** 的多技能医疗对话智能体系统。系统以"数智医生"为核心，通过多轮对话理解患者意图，动态调度 **MySQL 患者档案查询、Milvus 医学知识检索、Tavily 互联网搜索、YOLO 皮肤病图片检测** 等技能，为医生与患者提供智能问诊辅助。



---

## 功能特性
***前端界面：***
![image](https://github.com/hjx322/image/blob/main/1.png?raw=True)

***特性：***
- 🧠 **LangGraph 状态机驱动**：预处理 → 图片处理 → 技能分发 → 对话 → 后处理 → 路由，节点可编排、可中断恢复
- 💾 **MySQL Checkpoint 多轮记忆**：同一 `workflow_id` 经 `AIOMySQLSaver` 自动保留会话上下文
- 🔧 **统一技能调度**：`UnifiedSkillDispatcher` 根据用户提问自动选择技能并生成结构化参数
- 🧬 **两阶段医学知识检索**：Milvus 召回（Top-10）+ LLM 语义重排（Top-3）
- 🩺 **皮肤病图片检测**：YOLOv10/v11 模型，返回 7 类皮肤病变与置信度
- 🏥 **患者档案查询**：通过病历号读取 CRM 库中的身份、就诊、检验数据（PHI 最小化返回）
- 🌐 **互联网搜索**：Tavily API 获取最新医学资讯与药物信息
- ⚡ **前后端分离**：Vue3 + Vite 前端（SSE 流式打字机），FastAPI 单端口 8001 提供对话 + 管理 API

---

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 语言/运行时 | Python **3.12**（[pyproject.toml](pyproject.toml) 强制）、Node.js（Vite 8 需 ≥ Node 20） |
| 依赖管理 | [uv](https://docs.astral.sh/uv/)（`uv.lock`）/ 前端 npm |
| 智能体框架 | LangChain 0.3、LangGraph 0.4、langgraph-checkpoint-mysql |
| Web 后端 | FastAPI、Uvicorn、SQLAlchemy、aiomysql / pymysql |
| LLM | 阿里云 DashScope（通义千问 `qwen-plus`，兼容 OpenAI SDK）、`text-embedding-v3` 向量模型 |
| 向量数据库 | Milvus 2.5（`langchain-milvus`），HNSW + COSINE |
| 技能 | Tavily、Ultralytics YOLO、OpenCV、DashScope ASR（腾讯云语音） |
| 前端 | Vue 3.5 + Vite 8（`frontend/`） |

---

## 系统架构

```
┌────────────┐       ┌─────────────────────────────────────────────┐
│  Vue3 前端  │  /api  │           FastAPI 对话服务 (8001)             │
│ (localhost  │──────▶│  backend/chat_server.py                     │
│  :5173)     │       │  ├── POST /api/chat         普通对话           │
│             │       │  ├── POST /api/chat/stream  SSE 流式          │
│             │       │  ├── POST /api/upload       图片上传           │
│             │       │  ├── GET  /api/patients     患者搜索           │
│             │       │  └── /api/v1/agents|skills 医生/技能管理       │
└────────────┘       └───────────────┬─────────────────────────────┘
                                     │
                         ┌───────────▼─────────────┐
                         │  DigitalSmartDoctorAgent │  (agent/)
                         │  LangGraph 状态图         │
                         └───┬────┬────┬────┬──────┘
                    ┌────────┘    │    │    └────────┐
                    ▼             ▼    ▼             ▼
            ┌────────────┐  ┌────────┐  ┌────────┐  ┌──────────────┐
            │ MySQL CRM   │  │ Milvus │  │ Tavily │  │  YOLO 皮肤    │
            │ 患者档案    │  │ 医学知识│  │ 网搜   │  │  病灶检测     │
            └────────────┘  └────────┘  └────────┘  └──────────────┘

多轮记忆：MySQL Checkpoint（thread_id ⇔ workflow_id）
配置来源：根目录 application.yaml → config/app_config.py（pydantic-settings）
```

---

## 项目结构

```
vvm/
├── main.py                          # 简单命令行对话入口
├── cli.py                           # 交互式 CLI Shell（prompt_toolkit + rich，推荐）
├── run_dev.ps1                      # 🚀 Windows 一键启动（8001 后端 + 5173 前端）
├── start.ps1                        # Windows 分步启动脚本
├── application.yaml                 # ⚠️ 全项目配置文件【含 API Key，请勿提交】
├── pyproject.toml / uv.lock         # Python 依赖声明与锁文件（uv 管理）
├── package.json                     # 前端/工具链元数据
├── babel.json                       # Babel 本地数据
├── config/
│   └── app_config.py                # pydantic-settings + YAML 配置加载器
│
├── agent/                           # 🧠 智能体核心（LangGraph）
│   ├── digital_smart_doctor_agent.py# Agent 主类：建连/建图/aprocess 入口
│   ├── core/
│   │   ├── constants.py             # 中断节点、日志配置
│   │   ├── state.py                 # 图状态定义 DigitalSmartDoctorState
│   │   └── graph_builder.py         # 状态图构建器
│   ├── nodes/                       # 图节点
│   │   ├── pre_process_node.py      # 预处理（提取病历号/患者信息）
│   │   ├── image_process_node.py    # 图片描述与可用技能识别
│   │   ├── skill_query_node.py      # 技能查询（被 UnifiedSkillDispatcher 取代）
│   │   ├── chat_node.py             # 大模型对话节点
│   │   ├── post_process_node.py     # 后处理
│   │   └── routing_node.py          # 环节路由
│   ├── tools/skill_tools.py         # 技能工具（LangChain Tools 封装）
│   ├── utils/
│   │   ├── llm_service.py           # DashScope ChatOpenAI 封装
│   │   ├── medical_record_extractor.py # 病历号提取
│   │   ├── restart_handler.py       # 断点续跑处理
│   │   ├── skill_executor.py        # 技能执行器
│   │   └── sub_agent_command.py     # 子智能体命令
│   └── multi_agent/                 # 多智能体编排（预留）
│
├── backend/                         # ⚡ FastAPI 后端
│   ├── chat_server.py               # 对话服务（8001，对话 + 管理 API 合一）
│   ├── main.py                      # 旧管理服务（8000，已并入 chat_server）
│   ├── database/session_factory.py  # SQLAlchemy session 工厂 / 建表
│   ├── models/                      # ORM 模型：agent.py / skill.py / agent_skill.py
│   ├── routes/                      # 管理路由：agents.py / skills.py / agent_skills.py
│   ├── services/                    # 业务层：agent_manager.py / skill_manager.py ...
│   └── utils/                       # file_manager.py / integration.py
│
├── skills/                          # 🔧 技能系统（注册表 + 实现）
│   ├── registry.py                  # SKILL_REGISTRY 技能注册表
│   ├── schemas.py                   # Pydantic 参数 Schema（各技能的入参约定）
│   ├── skills_optimize_srh/         # 技能执行框架
│   │   ├── base.py                  # SkillHandler 基类 / SkillResult
│   │   ├── skill_dispatcher.py      # UnifiedSkillDispatcher 统一调度器
│   │   └── manage_skills.py / dynamic_registry.py ...
│   ├── mysql_query_skill.py         # 患者医疗档案查询（MySQL CRM）
│   ├── milvus_query_skill.py        # 医学知识检索（两阶段召回+重排）
│   ├── web_search_skill.py          # 互联网搜索（Tavily）
│   └── derma_image_skill.py         # 皮肤病图片检测（YOLO）
│
├── prompt/                          # Prompt 模板
│   ├── chat_prompt.py               # 对话 Prompt
│   ├── skills_prompt.py             # 技能选择 Prompt
│   ├── external_skill_prompt.py     # 外部技能 Prompt
│   ├── image_prompt.py              # 图片理解 Prompt
│   └── query_result_prompt.py       # 检索结果整理 Prompt
│
├── vector/                          # 📚 向量库工具与初始化
│   ├── milvus_vector.py             # Milvus 向量存储封装（HNSW/COSINE）
│   ├── dialog2qa_prompt.py          # 对话转 QA 的 Prompt
│   └── init/                        # medicine / disease / knowledge / qa 向量化脚本
│
├── data/                            # ⚠️ 数据目录（含患者医疗对话等敏感数据）
│   ├── dialogs/                     # 患者对话存档（.xlsx）
│   ├── datasets/                    # 医学知识 / 对话 / ASR 数据集
│   ├── dialog2qa.py                 # 对话 Excel → QA 对
│   ├── milvus_to_excel.py           # 向量库导出 Excel
│   ├── 药品库.xlsx / qa_vector_data.xlsx
│   └── test_dialog/                 # 测试对话样例
│
├── frontend/                        # 🎨 Vue3 + Vite 前端
│   ├── index.html                   # 入口 HTML
│   ├── vite.config.js               # /api 代理 → http://localhost:8001
│   ├── package.json
│   └── src/
│       ├── main.js                  # Vue 应用入口
│       ├── App.vue                  # 对话界面（消息流式渲染 + 会话参数）
│       ├── style.css                # 全局主题变量
│       └── components/
│           ├── DoctorManager.vue    # 医生管理面板
│           └── SkillManager.vue     # 技能管理面板
│
├── scripts/                         # 运维 / 一次性脚本
│   └── enable_default_doctor_skills.py  # 启用默认医生全部技能
│
├── .claude/skills/                  # 技能实现明细（derma_image 权重、搜索脚本等）
├── derma_image/                     # 皮肤病检测结果输出目录
├── uploads/                         # 对话图片上传目录（24h 自动清理）
├── user_skills/                     # 用户技能关联配置
└── %USERPROFILE%/                   # 系统路径误创建的目录（可忽略）
```

---

## 环境配置

### 1. 配置文件说明

所有连接凭据集中在根目录 **`application.yaml`**，由 [config/app_config.py](config/app_config.py) 在启动时加载。请将以下字段**替换为你自己的真实凭据**：

```yaml
llm:
  default: "qwen-plus"                                        # 默认对话模型
  dashscope:
    api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "<YOUR_DASHSCOPE_API_KEY>"                       # ⚠️ 阿里云百炼 DashScope Key

db:
  mysql:                                                      # 主库（agent/checkpoint/技能元数据）
    host: "<YOUR_MYSQL_HOST>"
    username: "<YOUR_MYSQL_USER>"
    password: "<YOUR_MYSQL_PASSWORD>"
    port: 3306
    db: "vvm_digital_smart_doctor_agent"
  patient_mysql:                                              # 患者 CRM 库（PHI，最小权限访问）
    host: "<YOUR_MYSQL_HOST>"
    username: "<YOUR_MYSQL_USER>"
    password: "<YOUR_MYSQL_PASSWORD>"
    port: 3306
    db: "vvm_hn_skin_crm"

db_crm:
  hn: "mysql+pymysql://<USER>:<PASSWORD>@<HOST>:3306/vvm_hn_skin_crm?autocommit=true&charset=utf8mb4"

vector:                                                       # Milvus 向量库（RAG 医学知识）
  host: "<YOUR_MILVUS_HOST>"
  port: "19530"
  user: "root"
  password: "<YOUR_MILVUS_PASSWORD>"
  db_name: "digital_smart_doctor_agent"

asr:                                                          # 腾讯云语音识别（图片/语音联动）
  ten_secret_id: "<YOUR_TENCENT_SECRET_ID>"
  ten_secret_key: "<YOUR_TENCENT_SECRET_KEY>"

search:
  tavily:
    api_key: "<YOUR_TAVILY_API_KEY>"                          # ⚠️ 互联网搜索 Key
```

> 🔒 **凭据必须屏蔽**：上表中所有 `<YOUR_XXX>` 均为占位符。**真实 Key / 密码 / Secret 绝不能提交进仓库**。已有风险点：
> - `.claude/skills/milvus_query/scripts/milvus_vector.py` 中**硬编码**了 DashScope Key 与 Milvus 密码，建议改为从 `application.yaml` / 环境变量读取
> - `.claude/skills/web_search/scripts/search.py` 已支持 `TAVILY_API_KEY` 环境变量优先取用，也可直接读配置

### 2. 推荐：改用环境变量（可选）

web_search 技能支持 `TAVILY_API_KEY` 环境变量覆盖配置文件；其余模块如需环境变量化，可在 `config/app_config.py` 中扩展 `BaseSettings`（当前 `load_config_to_env()` 已内置展平到环境变量的能力，可自行开启）。

### 3. 前置服务

| 服务 | 用途 | 端口（默认） |
| --- | --- | --- |
| MySQL | 主库（对话记忆 Checkpoint、医生/技能管理）+ 患者 CRM 库 | 3306 |
| Milvus | 医学知识向量检索（drug / disease / gastroenterology 等集合需先建好） | 19530 |

---

## 安装与运行

> 默认配置为 **Windows**（脚本为 `.ps1`）。Linux/macOS 请按"手动分步"执行，命令等价。

### 方式一：一键启动（Windows）

```powershell
.\run_dev.ps1
```

- 自动启动后端 `backend.chat_server:app`（端口 **8001**，后台）
- 然后进入 `frontend/` 启动 Vite dev（端口 **5173**，前台，`Ctrl+C` 停止）
- 浏览器访问 http://localhost:5173

### 方式二：手动分步

#### 1）后端（Python 3.12 + uv）

```bash
# 安装依赖（使用项目 venv；没有 uv 也可用 pip install -e .）
uv sync

# 启动对话 + 管理服务
.venv/Scripts/python.exe -m uvicorn backend.chat_server:app --port 8001
```

验证服务：

```bash
curl http://localhost:8001/health
# => {"status": "healthy", "service": "VVM chat server", ...}
```

> 管理 API 已并入 8001：`/api/v1/agents`、`/api/v1/skills`。旧的 `backend/main.py`（8000）仅作兼容保留，无需再单独启动。

#### 2）前端（需 Node ≥ 20）

```bash
cd frontend
npm install      # 首次
npm run dev      # http://localhost:5173
```

开发模式下 `/api` 请求由 Vite 代理转发到 `http://localhost:8001`，浏览器无需处理 CORS。

#### 3）CLI 命令行对话（可选）

```bash
# 交互式 Shell（历史记录 / 彩色输出 / 斜杠命令）
.venv/Scripts/python.exe cli.py

# 或最简单入口
.venv/Scripts/python.exe main.py
```

CLI 内命令：`/reset` 重置会话、`/crm <name>` 切换 CRM 库、`/doctor <id>` 切换医生智能体、`exit` 退出。

#### 4）常用运维脚本

```bash
# 启用默认医生（agt_d75e25a434fa457f）下的全部技能
.venv/Scripts/python.exe scripts/enable_default_doctor_skills.py
```

---

## API 一览（8001）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | 普通对话，请求体 `{human_input, workflow_id, crm?, chat_name?, doctor_id?, medical_record_no?, image_path?, restart?}`，返回 `{reply, workflow_id}` |
| POST | `/api/chat/stream` | SSE 流式对话，同请求体；每帧 `{"type": "thought"\|"content", "content": "..."}`，结束帧 `[DONE]` |
| POST | `/api/upload` | 图片上传（jpg/png/webp/bmp/gif，≤10MB），返回服务器绝对路径供 `image_path` 使用 |
| GET | `/api/patients` | 患者搜索（姓名/病历号/手机号），仅返回 `medical_record_no/name/phone` |
| GET | `/api/v1/agents` | 医生智能体列表管理 |
| GET | `/api/v1/skills` | 技能列表管理 |
| GET | `/health` | 健康检查 |
| - | `/docs` | Swagger 交互文档 |

> 多轮记忆：同一 `workflow_id` 对应同一 `thread_id`；前端"新会话"即重新生成 `workflow_id`。
> 当前会话池上限 32 个（`MAX_AGENTS`），超出后自动清理最旧会话（见 [chat_server.py](backend/chat_server.py)）。

---

## 技能系统

技能注册在 [skills/registry.py](skills/registry.py) 的 `SKILL_REGISTRY`，由 `UnifiedSkillDispatcher` 统一调度：

| 技能 | 文件 | 输入 Schema | 数据来源 |
| --- | --- | --- | --- |
| `mysql_query` | [mysql_query_skill.py](skills/mysql_query_skill.py) | `MySQLQuerySchema` | MySQL 患者 CRM 库（患者身份/就诊/检查记录） |
| `milvus_query` | [milvus_query_skill.py](skills/milvus_query_skill.py) | `MilvusQuerySchema` | Milvus 向量库（药品/疾病/胃肠文献），两阶段检索（召回 K=10，LLM 重排 Top-3） |
| `web_search` | [web_search_skill.py](skills/web_search_skill.py) | `WebSearchSchema` | Tavily 互联网实时资讯 |
| `derma_image` | [derma_image_skill.py](skills/derma_image_skill.py) | `DermaImageSchema` | YOLO 本地模型（权重在 `.claude/skills/derma_image/references/weights/`），输出到 `derma_image/` |

---

## 安全与隐私

1. **API 凭据**：`application.yaml`、`.claude/skills/*/scripts/` 中出现的 Key/密码为敏感信息，README 与仓库中一律以占位符展示。**切勿把真实值 push 到 GitHub 或其他公开仓库**（建议用环境变量或在本地 git 忽略）。
2. **患者数据（PHI）**：`data/dialogs/`、`data/药品库.xlsx` 等含患者对话与个人信息的文件，均需**避免公开提交**；API 侧患者查询仅返回最小编号字段。
3. **建议的 `.gitignore`**（根目录当前缺失，强烈建议补充）：
   ```gitignore
   application.yaml
   .env
   data/dialogs/
   data/*.xlsx
   .claude/settings.local.json
   .venv/
   __pycache__/
   frontend/node_modules/
   frontend/dist/
   uploads/
   derma_image/
   ```

---

## 常见问题（FAQ）

- **8001 起不来**：确认 MySQL 可连、端口未被占用（`run_dev.ps1` 会在启动 5 秒后检查进程是否存活）。
- **前端 5173 无法对话**：确认 8001 已启动；Vite proxy 已将 `/api` 转发到 8001。
- **技能未被调用**：先执行 `scripts/enable_default_doctor_skills.py` 为默认医生启用技能，或在管理界面（SkillManager.vue）手动启用。
- **`derma_image` 无法检测**：检查 `.claude/skills/derma_image/references/weights/` 下是否存在 `YOLOv10.pt / YOLOv11.pt` 权重文件。

---

