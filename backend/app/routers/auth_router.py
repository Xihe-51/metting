"""
用户认证路由
注册、登录、忘记密码、JWT验证
"""
from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi.responses import JSONResponse
import random
import os

from app.database import get_db
from app.models import User, VerificationCode
from app.schemas import success, error

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


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


def create_access_token(data):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """JWT鉴权依赖，返回当前登录用户"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise ValueError("无效的token")
    except (JWTError, ValueError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user


def require_auth(current_user: User = Depends(get_current_user)) -> User:
    """必须登录的鉴权依赖"""
    if current_user is None:
        return JSONResponse(status_code=401, content=error(401, "未登录或Token无效"))
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

    token = create_access_token({"user_id": user.id, "username": user.username})
    return success({
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name
    }, "注册成功")


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """用户登录"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        return JSONResponse(status_code=401, content=error(401, "用户名或密码错误"))

    token = create_access_token({"user_id": user.id, "username": user.username})
    return success({
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name
    }, "登录成功")


@router.post("/send-code")
def send_code(req: SendCodeRequest, db: Session = Depends(get_db)):
    """发送验证码（忘记密码）"""
    if not db.query(User).filter(User.email == req.email).first():
        return JSONResponse(status_code=404, content=error(404, "该邮箱未注册"))

    code = generate_code()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.query(VerificationCode).filter(VerificationCode.email == req.email).delete()
    db.add(VerificationCode(email=req.email, code=code, expires_at=expires_at))
    db.commit()

    print(f"验证码: {code} (发送至 {req.email})")
    return success({"code": code}, "验证码已发送")


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """重置密码"""
    vc = db.query(VerificationCode).filter(
        VerificationCode.email == req.email, VerificationCode.code == req.code
    ).first()
    if not vc:
        return JSONResponse(status_code=400, content=error(400, "验证码错误"))
    if vc.expires_at < datetime.utcnow():
        return JSONResponse(status_code=400, content=error(400, "验证码已过期"))

    user = db.query(User).filter(User.email == req.email).first()
    user.password_hash = get_password_hash(req.new_password)
    db.delete(vc)
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
        "created_at": str(current_user.created_at)
    })