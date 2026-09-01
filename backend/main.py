#FastAPI 后端入口文件，连接LLM API，mysql等
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

# Import routes
from backend.routes import agents, agent_skills, skills
from backend.database.session_factory import init_db_session
from config.app_config import configs

# Initialize FastAPI app
app = FastAPI(
    title="VVM Multi-Doctor Skill Management System",
    description="API for managing skills and agents for multiple doctors",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    try:
        init_db_session(configs)
        print("Database initialized successfully")
    except Exception as e:
        print(f"Warning: Database initialization failed - {str(e)}")
        print("   Continuing without database (test mode)")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "VVM Multi-Doctor Skill Management"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to VVM Multi-Doctor Skill Management System",
        "version": "1.0.0",
        "endpoints": {
            "agents": "/api/v1/agents",
            "skills": "/api/v1/skills",
            "docs": "/docs",
            "health": "/health",
        },
    }


# Include routes
app.include_router(agents.router)
app.include_router(agent_skills.router)
app.include_router(skills.router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=os.getenv("ENVIRONMENT") != "production",
    )
