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

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.digital_smart_doctor_agent import DigitalSmartDoctorAgent
from sqlalchemy import create_engine, text

from config.app_config import configs
from backend.database.session_factory import init_db_session
from backend.routes import agents, agent_skills, skills

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

# 合并原 8000 管理服务路由到本服务：医生/技能/启停接口统一由 8001 提供（前端只连这一个后端）
app.include_router(agents.router)
app.include_router(agent_skills.router)
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


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """普通对话：一次性返回完整回答"""
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
async def chat_stream(req: ChatStreamRequest):
    """流式对话：以 SSE 形式逐 chunk 返回 text/event-stream"""
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
            _sse_stream(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("流式对话启动失败")
        raise HTTPException(status_code=500, detail=str(e))


async def _sse_stream(stream: AsyncGenerator) -> AsyncGenerator[str, None]:
    """将 aprocess 的异步生成器转换为 SSE 格式输出"""
    try:
        async for chunk in stream:
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
    except Exception as e:  # noqa: BLE001
        logger.exception("流式生成出错")
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    finally:
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
