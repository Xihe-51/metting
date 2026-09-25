"""
客户端运行时配置下发

前端不再硬编码 ICE 服务器地址：不同部署环境（纯局域网 / 有 TURN 中继）配置不同，
写死在代码里要么必须改代码重新构建，要么在无 STUN/TURN 的环境里反复重试不可达地址。
"""
from fastapi import APIRouter, Depends

from app.ice import build_ice_config
from app.models import User
from app.routers.auth_router import require_auth
from app.schemas import success

router = APIRouter(prefix="/api/v1/config", tags=["配置"])


@router.get("/ice")
def get_ice_config(current_user: User = Depends(require_auth)):
    """下发当前用户的 ICE 配置（TURN 凭证绑定用户且带有效期）

    需要登录：TURN 时效凭证虽有 TTL，但不应向匿名访客签发。
    """
    return success(build_ice_config(current_user.id))