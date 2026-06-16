"""
FastAPI 应用入口
局域网视频会议系统后端
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.database import engine, Base
from app.routers import meeting_router
from app.routers.auth_router import router as auth_router
from app.websocket_manager import manager

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 创建 FastAPI 应用
app = FastAPI(
    title="局域网视频会议系统",
    description="纯局域网视频会议后端API",
    version="1.0.0"
)

# CORS 配置（允许前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 局域网环境，允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(meeting_router.router, prefix="/api/v1")
app.include_router(auth_router)

# 创建录制文件存储目录
os.makedirs("recordings", exist_ok=True)
os.makedirs("static", exist_ok=True)

# 静态文件服务（录制文件回放）
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """根路径"""
    return {"message": "局域网视频会议系统 API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}