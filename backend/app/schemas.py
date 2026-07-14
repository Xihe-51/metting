"""
统一响应模型
所有接口返回 {code: int, message: str, data: object|null}
"""
from typing import Any, Optional, TypeVar, Generic
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应格式"""
    code: int = 200
    message: str = "success"
    data: Optional[T] = None


def success(data: Any = None, message: str = "success") -> dict:
    """成功响应"""
    return {"code": 200, "message": message, "data": data}


def error(code: int, message: str) -> dict:
    """错误响应"""
    return {"code": code, "message": message, "data": None}