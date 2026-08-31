"""
用户认证路由
注册、登录、忘记密码、JWT验证
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi.responses import JSONResponse
from typing import Optional
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
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """JWT鉴权依赖，从 Header 手动解析 Token，无效时返回 None 而非抛异常"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            return None
    except JWTError:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user


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
    db.commit()
    return success(None, "密码修改成功")


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