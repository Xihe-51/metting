"""
用户认证路由
注册、登录、忘记密码、JWT验证
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi.responses import JSONResponse
from typing import Optional
from uuid import uuid4
import logging
import random
import os

from app.database import get_db, SessionLocal
from app.config import ALGORITHM, ACCESS_TOKEN_EXPIRE_HOURS, SECRET_KEY
from app.models import User, VerificationCode
from app.schemas import success, error

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

logger = logging.getLogger("uvicorn.error")

# 单个验证码允许的最大校验失败次数，超过即作废（防止 6 位码被暴力枚举）
MAX_CODE_ATTEMPTS = 5
# 验证码有效期（分钟）
CODE_TTL_MINUTES = 10


def _dev_mode_enabled() -> bool:
    """仅当显式设置 AUTH_DEV_MODE=1 时才允许在响应中回显验证码（本地联调/自动化测试用）。

    生产环境必须保持关闭，否则等同于任意账号可被接管。
    """
    return os.getenv("AUTH_DEV_MODE", "0") == "1"


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str
    email: str
    phone: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class SendCodeRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    """签发 JWT

    额外写入三个标准声明：
    - iat：签发时间，便于判断 token 新旧与排查问题；
    - jti：token 唯一标识，用于追踪单次签发（也为后续做黑名单留钩子）；
    - ver 需由调用方通过 token_claims 传入，改密码后旧 token 立即失效。
    """
    to_encode = data.copy()
    now = datetime.utcnow()
    to_encode.update({
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
        "jti": uuid4().hex,
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def token_claims(user: User) -> dict:
    """签发 token 所需的声明（含 token 版本，供吊销校验使用）"""
    return {"user_id": user.id, "username": user.username, "ver": user.token_version or 0}


def resolve_user_from_token(token: str, db: Session) -> Optional[User]:
    """解析 token 并返回对应用户，任一环节不合法都返回 None

    返回 None 的情况：签名/格式无效、缺少 user_id、用户不存在、
    token 的 ver 与用户当前 token_version 不一致
    （不一致说明用户改过密码，此前签发的所有 token 必须立即作废）。
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    user_id = payload.get("user_id")
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return None
    if payload.get("ver", 0) != (user.token_version or 0):
        return None
    return user


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """JWT鉴权依赖，从 Header 手动解析 Token，无效时返回 None 而非抛异常"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return resolve_user_from_token(authorization[7:], db)


def require_auth(current_user: Optional[User] = Depends(get_current_user)) -> User:
    """必须登录的鉴权依赖，未登录时抛出 HTTPException 终止请求"""
    if current_user is None:
        raise HTTPException(status_code=401, detail="未登录或Token无效")
    return current_user


def generate_code():
    return str(random.randint(100000, 999999))


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """用户注册"""
    if db.query(User).filter(User.username == req.username).first():
        return JSONResponse(status_code=400, content=error(400, "用户名已存在"))
    if db.query(User).filter(User.email == req.email).first():
        return JSONResponse(status_code=400, content=error(400, "邮箱已被注册"))

    user = User(
        username=req.username,
        password_hash=get_password_hash(req.password),
        display_name=req.display_name,
        email=req.email,
        phone=req.phone or None
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(token_claims(user))
    return success({
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name
    }, "注册成功")


def _authenticate(username: str, password: str):
    """阻塞的认证逻辑（DB查询 + bcrypt校验），在线程池中执行"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return None
        return user
    finally:
        db.close()


@router.post("/login")
async def login(req: LoginRequest):
    """用户登录（async + 线程池，避免 bcrypt 阻塞事件循环）"""
    user = await run_in_threadpool(_authenticate, req.username, req.password)
    if not user:
        return JSONResponse(status_code=401, content=error(401, "用户名或密码错误"))

    token = create_access_token(token_claims(user))
    return success({
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name
    }, "登录成功")


@router.post("/send-code")
def send_code(req: SendCodeRequest, db: Session = Depends(get_db)):
    """发送验证码（忘记密码）

    安全约束：
    1. 响应体默认不回显验证码，验证码只能经邮件等服务端通道下发；
       仅 AUTH_DEV_MODE=1（本地联调/自动化测试）时才在 data.code 中返回。
    2. 无论邮箱是否已注册，都返回完全相同的成功文案，避免账号枚举。
    """
    user = db.query(User).filter(User.email == req.email).first()

    code = None
    if user:
        code = generate_code()
        expires_at = datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES)
        db.query(VerificationCode).filter(VerificationCode.email == req.email).delete()
        db.add(VerificationCode(email=req.email, code=code, expires_at=expires_at, attempts=0))
        db.commit()
        # TODO: 接入邮件服务后改为发送邮件；生产环境不打印验证码明文
        logger.info("已为邮箱 %s 生成密码重置验证码", req.email)

    if _dev_mode_enabled() and code:
        return success({"code": code}, "验证码已发送")
    return success(None, "验证码已发送")


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """重置密码

    安全约束：同一验证码连续校验失败达到 MAX_CODE_ATTEMPTS 次即作废，
    避免 6 位数字验证码（90 万组合）在 10 分钟有效期内被暴力枚举。
    """
    vc = db.query(VerificationCode).filter(
        VerificationCode.email == req.email, VerificationCode.code == req.code
    ).first()

    if not vc:
        # 校验失败：对该邮箱当前有效的验证码累加失败次数，超限即删除
        latest = db.query(VerificationCode).filter(
            VerificationCode.email == req.email
        ).order_by(VerificationCode.created_at.desc(), VerificationCode.id.desc()).first()
        if latest:
            # 并发猜测场景必须用原子 SQL 自增：多个请求同时读改写同一行会导致
            # StaleDataError（UPDATE ... 0 were matched）而返回 500
            db.query(VerificationCode).filter(
                VerificationCode.id == latest.id
            ).update(
                {VerificationCode.attempts: func.coalesce(VerificationCode.attempts, 0) + 1},
                synchronize_session=False
            )
            db.query(VerificationCode).filter(
                VerificationCode.id == latest.id,
                VerificationCode.attempts >= MAX_CODE_ATTEMPTS
            ).delete(synchronize_session=False)
            db.commit()
        return JSONResponse(status_code=400, content=error(400, "验证码错误"))

    if (vc.attempts or 0) >= MAX_CODE_ATTEMPTS:
        db.query(VerificationCode).filter(
            VerificationCode.id == vc.id
        ).delete(synchronize_session=False)
        db.commit()
        return JSONResponse(status_code=400, content=error(400, "验证码错误次数过多，请重新获取"))

    if vc.expires_at < datetime.utcnow():
        return JSONResponse(status_code=400, content=error(400, "验证码已过期"))

    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        db.query(VerificationCode).filter(
            VerificationCode.id == vc.id
        ).delete(synchronize_session=False)
        db.commit()
        return JSONResponse(status_code=400, content=error(400, "验证码错误"))

    # 先原子占用验证码（保证单次使用），再改密码；并发下未抢到的请求直接失败
    claimed = db.query(VerificationCode).filter(
        VerificationCode.id == vc.id
    ).delete(synchronize_session=False)
    if claimed == 0:
        db.rollback()
        return JSONResponse(status_code=400, content=error(400, "验证码已失效"))

    # 改密码必须同时让此前签发的所有 token 立即失效：
    # password_hash 与 token_version 用同一条原子 SQL 更新（并发改密不会互相覆盖），
    # 旧 token 的 ver 声明随即与库中不一致，resolve_user_from_token 会拒绝它
    db.query(User).filter(User.id == user.id).update(
        {
            User.password_hash: get_password_hash(req.new_password),
            User.token_version: func.coalesce(User.token_version, 0) + 1,
        },
        synchronize_session=False,
    )
    db.commit()
    return success(None, "密码重置成功")


@router.get("/me")
def get_me(current_user: User = Depends(require_auth)):
    """获取当前用户信息"""
    return success({
        "id": current_user.id,
        "username": current_user.username,
        "display_name": current_user.display_name,
        "email": current_user.email,
        "phone": current_user.phone or "",
        "avatar_url": current_user.avatar_url or "",
        "cover_url": current_user.cover_url or "",
        "bio": current_user.bio or "",
        "verified": current_user.verified,
        "created_at": str(current_user.created_at)
    })


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    cover_url: Optional[str] = None
    verified: Optional[bool] = None


@router.put("/profile")
async def update_profile(
    req: UpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """更新个人资料"""
    if req.display_name is not None and req.display_name.strip():
        current_user.display_name = req.display_name.strip()
    if req.bio is not None:
        current_user.bio = req.bio.strip() if req.bio.strip() else None
    if req.avatar_url is not None:
        current_user.avatar_url = req.avatar_url.strip() if req.avatar_url.strip() else None
    if req.cover_url is not None:
        current_user.cover_url = req.cover_url.strip() if req.cover_url.strip() else None
    if req.verified is not None:
        current_user.verified = req.verified
    db.commit()
    db.refresh(current_user)
    return success({
        "id": current_user.id,
        "display_name": current_user.display_name,
        "bio": current_user.bio or "",
        "avatar_url": current_user.avatar_url or "",
        "cover_url": current_user.cover_url or "",
        "verified": current_user.verified
    }, "资料已更新")


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.put("/password")
def change_password(
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """修改密码"""
    if not verify_password(req.old_password, current_user.password_hash):
        return JSONResponse(status_code=400, content=error(400, "原密码错误"))
    if len(req.new_password) < 6:
        return JSONResponse(status_code=400, content=error(400, "新密码至少6位"))
    current_user.password_hash = get_password_hash(req.new_password)
    # 改密码 → 此前签发的 token 全部作废（token_version 自增）；
    # 同时给当前会话换发新 token，避免用户刚改完密码就被自己踢下线
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    db.refresh(current_user)
    new_token = create_access_token(token_claims(current_user))
    return success({"access_token": new_token, "token_type": "bearer"}, "密码修改成功")


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_file(request: Request, current_user: User = Depends(require_auth)):
    """上传文件（头像/封面）"""
    from fastapi import UploadFile, File, Form
    # 这里用原生 Request 解析 multipart
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return JSONResponse(status_code=400, content=error(400, "需要 multipart/form-data"))

    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "filename"):
        return JSONResponse(status_code=400, content=error(400, "请选择文件"))

    filename = file.filename
    # 只允许图片
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return JSONResponse(status_code=400, content=error(400, "仅支持 jpg/png/gif/webp 格式"))

    # 保存文件
    save_name = f"user_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    save_path = os.path.join(UPLOAD_DIR, save_name)
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # 返回可访问的 URL（通过静态文件挂载）
    file_url = f"/uploads/{save_name}"
    return success({"url": file_url}, "上传成功")