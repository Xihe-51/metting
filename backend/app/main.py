"""
FastAPI 应用入口
局域网视频会议系统后端
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import os

from app.database import engine, Base, auto_migrate
from app.routers import meeting_router
from app.routers.auth_router import router as auth_router
from app.websocket_manager import manager

# 创建数据库表 + 自动迁移缺失列
Base.metadata.create_all(bind=engine)
auto_migrate()

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


# 统一异常处理：HTTPException → 统一格式 {code, message, data}
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail, "data": None}
    )


# 统一异常处理：请求验证错误
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "请求参数错误", "data": None}
    )

# 注册路由
app.include_router(meeting_router.router, prefix="/api/v1")
app.include_router(auth_router)

# 创建录制文件存储目录
os.makedirs("recordings", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# 前端构建产物目录（dist）：单端口对外服务，隧道只需映射到本机 8000 一个端口
FRONTEND_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "front", "dist"))

# 静态文件服务（录制文件回放 + 用户上传）
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# 前端构建产物的静态资源（JS/CSS/媒体管线 wasm 等）
if os.path.isdir(os.path.join(FRONTEND_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")
if os.path.isdir(os.path.join(FRONTEND_DIST, "mediapipe")):
    app.mount("/mediapipe", StaticFiles(directory=os.path.join(FRONTEND_DIST, "mediapipe")), name="mediapipe")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """站点图标"""
    path = os.path.join(FRONTEND_DIST, "favicon.ico")
    if os.path.isfile(path):
        return FileResponse(path)
    raise StarletteHTTPException(status_code=404)


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}


@app.get("/")
async def root():
    """根路径：返回前端首页"""
    index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"message": "局域网视频会议系统 API", "version": "1.0.0"}


# SPA 路由回退：非 API / 非静态资源的 GET 请求，一律交给前端路由处理
# 注意：必须注册在所有 API 路由与静态挂载之后，才能不影响 /api/v1、/ws、/recordings 等
@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise StarletteHTTPException(status_code=404, detail="前端资源未构建，请先执行 npm run build")