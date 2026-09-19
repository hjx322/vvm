"""数智医生智能体 - 对话服务（独立 FastAPI，端口 8001）

提供对话 API 供前端（frontend/）与 CLI 调用，内部复用 DigitalSmartDoctorAgent。
与 backend/main.py（技能管理系统，8000）相互独立，职责分离。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator, Dict, Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine, text

from config.app_config import configs
from agent.utils.emergency import detect_emergency, emergency_reply
from backend.database.session_factory import init_db_session
from backend.routes import skills, worker_skills

# 默认对话参数（沿用 main.py 的现状，可被请求体覆盖）
DEFAULT_CRM = "hn"
DEFAULT_DOCTOR_ID = "agt_d75e25a434fa457f"
DEFAULT_CHAT_NAME = ""

# agent 实例缓存上限，超出后按插入顺序清理最旧的
MAX_AGENTS = 32

# 图片上传配置：保存到项目根目录下的 uploads/
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
UPLOAD_TTL_SECONDS = 24 * 3600  # 上传文件保留 24 小时后清理

logger = logging.getLogger("chat_server")

# 患者搜索：复用 CRM 连接串建只读 SQLAlchemy 引擎（惰性建连，同步查询用 asyncio.to_thread 包装）
_patient_engine = create_engine(configs.db_crm["hn"], pool_pre_ping=True)


def _query_patients_sync(keyword: str, limit: int) -> list:
    """同步查询患者（仅返回 medical_record_no/name/phone 三字段，避免暴露多余 PHI）

    keyword 为空时不加 WHERE，返回全量患者列表（供前端原生下拉一次拉全）。
    """
    with _patient_engine.connect() as conn:
        if keyword:
            kw = f"%{keyword}%"
            rows = conn.execute(
                text(
                    "SELECT medical_record_no, name, phone "
                    "FROM cst_patient_info "
                    "WHERE name LIKE :kw OR medical_record_no LIKE :kw OR phone LIKE :kw "
                    "ORDER BY file_date DESC LIMIT :lim"
                ),
                {"kw": kw, "lim": limit},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    "SELECT medical_record_no, name, phone "
                    "FROM cst_patient_info "
                    "ORDER BY file_date DESC LIMIT :lim"
                ),
                {"lim": limit},
            ).mappings().all()
    return [dict(r) for r in rows]

# 全局 agent 缓存：workflow_id -> agent 实例
_agents: Dict[str, DigitalSmartDoctorAgent] = {}
_agents_lock = asyncio.Lock()


class ChatRequest(BaseModel):
    """普通对话请求体（response_type="normal"）"""

    human_input: str = Field(..., min_length=1, description="用户输入")
    workflow_id: str = Field(..., min_length=1, description="会话标识，决定多轮记忆")
    crm: str = DEFAULT_CRM
    chat_name: str = DEFAULT_CHAT_NAME
    doctor_id: str = DEFAULT_DOCTOR_ID
    medical_record_no: Optional[str] = Field(
        default=None, description="显式病历号（前端下拉选定患者时传完整编号，绕过正则提取）"
    )
    image_path: Optional[str] = Field(
        default=None, description="已上传图片的服务器本地路径（来自 /api/upload）"
    )
    restart: bool = False


class ChatStreamRequest(ChatRequest):
    """流式对话请求体（response_type="stream"），字段同 ChatRequest"""


app = FastAPI(
    title="VVM 数智医生智能体 - 对话服务",
    description="提供对话 API，供 Vue3 前端与 CLI 调用",
    version="1.0.0",
)

# 放开 CORS，供前端 dev 与不同来源直接访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 合并原 8000 管理服务路由到本服务：worker技能/技能/启停接口统一由 8001 提供（前端只连这一个后端）
app.include_router(worker_skills.router)
app.include_router(skills.router)


async def _get_agent(workflow_id: str) -> DigitalSmartDoctorAgent:
    """按 workflow_id 获取（或创建）agent 实例，并做并发保护与上限清理。

    同一 workflow_id 对应同一 thread_id → 多轮记忆由 MySQL checkpoint 自动保留。
    """
    global _agents
    async with _agents_lock:
        agent = _agents.get(workflow_id)
        if agent is not None:
            return agent

        # 懒加载：首次创建会话时才 import 整个 agent/技能栈，避开启动期的 ~4s 强依赖加载
        from agent.digital_smart_doctor_agent import DigitalSmartDoctorAgent
        agent = await DigitalSmartDoctorAgent.create(workflow_id=workflow_id)
        # 超过上限时，优先清理最早加入的实例
        if len(_agents) >= MAX_AGENTS:
            oldest_id = next(iter(_agents))
            old = _agents.pop(oldest_id)
            await old.close()
            logger.info("清理超龄会话 %s", oldest_id)

        _agents[workflow_id] = agent
        logger.info("创建/缓存会话 %s（当前共 %d 个）", workflow_id, len(_agents))
        return agent


@app.on_event("shutdown")
async def shutdown_event():
    """关闭所有缓存的 agent 实例，释放数据库连接"""
    global _agents
    async with _agents_lock:
        for wid, agent in _agents.items():
            await agent.close()
        _agents.clear()


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "service": "VVM chat server", "agents": len(_agents)}


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    """上传图片文件，保存到服务器本地 uploads/ 目录，返回绝对路径

    校验：扩展名白名单 + 大小 ≤10MB + 内容非空；用 uuid 强制重命名防止路径穿越。
    """
    # 1. 校验扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}，仅支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # 2. 读取内容并校验大小
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 上限")
    if len(content) < 4:
        raise HTTPException(status_code=400, detail="文件内容过小，无法识别为有效图片")

    # 3. 用 uuid 重命名保存（完全忽略客户端文件名，杜绝路径穿越）
    safe_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(save_path, "wb") as f:
        f.write(content)

    logger.info("图片上传成功: %s (%d bytes)", safe_name, len(content))
    return {
        "path": os.path.abspath(save_path),
        "filename": file.filename,
        "size": len(content),
    }


@app.get("/api/patients")
async def search_patients(
    keyword: str = Query("", max_length=50),
    limit: int = Query(1000, ge=1, le=5000),
):
    """按关键词模糊搜索患者（姓名/病历号/手机号）；keyword 为空时返回全量列表。

    limit 默认 1000、上限 5000（仅作防呆防内存）；keyword 为空即全量返回，
    供前端原生下拉一次拉全（有几个患者显示几个）。
    仅返回 medical_record_no/name/phone 三个必要字段（患者数据为 PHI）。
    """
    keyword = keyword.strip()
    try:
        rows = await asyncio.to_thread(_query_patients_sync, keyword, limit)
        return {"data": rows}
    except Exception as e:  # noqa: BLE001
        logger.exception("患者搜索失败")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history(workflow_id: str = Query(...)):
    """从 MySQL checkpoint 读取该会话的历史消息（state.messages），供前端重开页面回显。

    同一 workflow_id → 同一 thread_id：历史由 LangGraph AIOMySQLSaver 持久化在 checkpoint 表；
    前端关闭重开后只要带着同一个 workflow_id 打开，即可拉回过往对话继续续聊。
    """
    try:
        agent = await _get_agent(workflow_id)  # 复用同一 agent 实例（同一 MySQL 连接 + 同一 thread_id）
        state = await agent.graph.aget_state(agent.config)
        msgs = []
        if state and state.values:
            for m in state.values.get("messages") or []:
                msgs.append({
                    "role": "user" if isinstance(m, HumanMessage) else "assistant",
                    "content": getattr(m, "content", ""),
                })
        return {"messages": msgs, "workflow_id": workflow_id}
    except Exception as e:  # noqa: BLE001
        logger.exception("读取会话历史失败")
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("startup")
async def startup_init_db():
    """初始化数据库 session 工厂（agents/skills 管理路由的 get_session_context 依赖），幂等建表。"""
    try:
        init_db_session(configs)
        logger.info("Database session initialized（医生/技能管理路由可用）")
    except Exception as e:
        logger.warning("Database initialization failed: %s", e)


@app.on_event("startup")
async def startup_upload_cleanup():
    """启动后周期清理超过 24 小时未使用的上传文件"""

    async def _cleanup_loop():
        while True:
            try:
                now = time.time()
                for fname in os.listdir(UPLOAD_DIR):
                    fpath = os.path.join(UPLOAD_DIR, fname)
                    if (
                        os.path.isfile(fpath)
                        and now - os.path.getmtime(fpath) > UPLOAD_TTL_SECONDS
                    ):
                        os.remove(fpath)
                        logger.info("清理过期上传文件: %s", fname)
            except Exception as e:  # noqa: BLE001
                logger.warning("清理上传文件出错: %s", e)
            await asyncio.sleep(3600)  # 每小时检查一次

    asyncio.create_task(_cleanup_loop())


async def _content_once(text: str):
    """把一段完整文本包成单个 content 事件（复用 _sse_stream 的 SSE 分帧）"""
    yield {"type": "content", "content": text}


def _edge_emergency_reply(human_input: str):
    """边缘急症快速通道：命中返回指引文本，未命中/已关闭返回 None

    为什么放在 _get_agent 之前：新建 agent 要连 MySQL 并构建整张 LangGraph 图，
    实测冷启动约 6s；急症场景下这几秒没有意义。命中时直接在边缘返回，
    不连库、不建图、不调用任何 LLM。

    与 digital_smart_doctor_agent.aprocess 内的同名检查是**防御性重复**（分层兜底）：
    这里服务 Web 入口的最快路径，那里保证 CLI / 其它调用方也覆盖到。
    """
    if not getattr(configs.emergency, "enabled", True):
        return None
    category = detect_emergency(human_input)
    if not category:
        return None
    logger.warning(f"[Emergency] 边缘命中急症规则: {category}")
    return emergency_reply(category)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """普通对话：一次性返回完整回答"""
    emergency = _edge_emergency_reply(req.human_input)
    if emergency is not None:
        return {"reply": emergency, "workflow_id": req.workflow_id}
    try:
        agent = await _get_agent(req.workflow_id)
        text = await agent.aprocess(
            req.human_input,
            req.crm,
            req.chat_name,
            req.doctor_id,
            response_type="normal",
            restart=req.restart,
            image_path=req.image_path,
            medical_record_no=req.medical_record_no,
        )
        return {"reply": text, "workflow_id": req.workflow_id}
    except Exception as e:  # noqa: BLE001
        logger.exception("对话处理失败")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    """流式对话：以 SSE 形式逐 chunk 返回 text/event-stream

    `request` 用于检测客户端断开（用户点"停止"/关页面），见 _sse_stream。
    """
    emergency = _edge_emergency_reply(req.human_input)
    if emergency is not None:
        return StreamingResponse(
            _sse_stream(_content_once(emergency), None),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        agent = await _get_agent(req.workflow_id)
        # aprocess 是 async def，需要 await 获取 async generator 后逐块流式返回
        stream = await agent.aprocess(
            req.human_input,
            req.crm,
            req.chat_name,
            req.doctor_id,
            response_type="stream",
            restart=req.restart,
            image_path=req.image_path,
            medical_record_no=req.medical_record_no,
        )
        return StreamingResponse(
            _sse_stream(stream, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("流式对话启动失败")
        raise HTTPException(status_code=500, detail=str(e))


async def _sse_stream(
    stream: AsyncGenerator, request: Optional[Request] = None
) -> AsyncGenerator[str, None]:
    """将 aprocess 的异步生成器转换为 SSE 格式输出

    Fix: 原先前端点"停止"（AbortController.abort）只断开 HTTP 连接，后端 astream
    仍在继续跑完整张图（多 Agent 一轮 9 次 LLM 调用）并写 checkpoint —— 用户以为停了，
    token 照烧。

    现在的机制（2026-09-12 端到端实测）：
      * 客户端断开后，本生成器会被关闭（finally 里的 aclose 触发上游 LangGraph
        astream 的清理）→ 图停在当时的节点，后续 LLM 调用不再发生。
        实测：中止后 checkpoint 的 next=('node_multi_agent',)（图停在中间节点），
        说明后半个流程确实没跑。
      * is_disconnected() 探测只在"恰好在两个 chunk 之间断开"时才命中；实测的
        abort 走的是生成器关闭路径，收不到该信号，故两种机制都保留。
      * 图被停在中途会留下 next 非收尾节点的 checkpoint —— 由
        digital_smart_doctor_agent.aprocess 每轮的 handle_restart 自愈（已实测验证）。
    """
    finished = False  # 是否把本轮流完整读完（用于区分"正常结束"与"被中止"）
    try:
        async for chunk in stream:
            # 客户端已断开（用户点"停止"或关闭页面）→ 提前终止，避免无谓的 LLM 开销
            if request is not None and await request.is_disconnected():
                logger.info("客户端已断开，提前终止流式生成")
                break
            # 透传事件类型：type="thought" 为思考过程块，type="content"（默认）为正式回复。
            # 旧 chunk（无 type）自动归为 "content"，向前兼容。
            if isinstance(chunk, dict):
                content = chunk.get("content", "")
                ctype = chunk.get("type", "content")
            else:
                content = getattr(chunk, "content", "")
                ctype = getattr(chunk, "type", "content")
            if content:
                payload = {"type": ctype, "content": content}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        else:
            finished = True  # 迭代自然耗尽 = 本轮生成完整跑完
    except Exception as e:  # noqa: BLE001
        logger.exception("流式生成出错")
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    finally:
        # 关闭上游生成器：触发 LangGraph astream 的清理，停止未完成的 LLM 调用。
        #
        # 实测（2026-09-12 端到端）：客户端 abort 时 Starlette 是直接 aclose 掉本异步
        # 生成器（抛 GeneratorExit）—— 既不是 is_disconnected() 探测为 True，也不是
        # CancelledError，两条分支都收不到。所以"是否被中止"必须靠 finished 标志判断，
        # 不能靠异常类型。
        #
        # 注：在"跑到一半被中止"的场景下，LangGraph 内部（pregel/runner.py 的
        # FuturesDict.on_done）会向事件循环打一条 "'NoneType' object is not callable"
        # 的回调异常日志 —— 它不经过我们的调用栈、无法在此捕获，也不影响用户（连接已断）。
        # 保留 aclose 是有意的：不关就会让图继续跑完后半个流程（多 Agent 一轮 9 次
        # LLM 调用），用户以为停了、token 照烧。
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except (Exception, asyncio.CancelledError, GeneratorExit):  # noqa: BLE001
                pass
        if not finished:
            # 尽力而为的日志：Starlette 在某些断开时序下会直接丢弃本生成器（不 resume），
            # 此时本 finally 只会等到 GC 才执行——所以"没看到这行"不代表中止没生效，
            # 判定中止是否生效请看 checkpoint 的 next（见 digital_smart_doctor_agent）。
            logger.info("本轮生成被提前中止（客户端断开），已关闭上游生成器")
    # 只有正常跑完才写终止帧；被中止时写不出去、也可能抛错
    if finished:
        yield "data: [DONE]\n\n"


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常兜底"""
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.chat_server:app", host="0.0.0.0", port=8001)
