"""
启动脚本
运行: python run.py
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # 局域网内所有设备可访问
        port=8000,
        reload=True  # 开发模式，代码修改自动重启
    )