"""
启动脚本
运行: python run.py
"""
import os

import uvicorn

from app.config import InsecureConfigError, load_secret_key, load_tls_config

# 是否开启 uvicorn 热重载（开发用）。默认关闭：
# reload 会在每次代码改动后重启进程，而 ConnectionManager 的连接表只存在内存里，
# 重启即全部丢失 —— 数据库却还留着未离会的参会记录与 participant_count，
# 于是出现「幽灵在线成员、会议永不自动结束、名单错乱」。
# 需要边改代码边调试时再显式设置 DEV_RELOAD=1（应用启动时会对账清理上述残留状态）。
RELOAD_ENV_VAR = "DEV_RELOAD"


def _reload_enabled() -> bool:
    return (os.getenv(RELOAD_ENV_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    # 启动前显式校验：配置不合格时给出可操作的提示并退出，
    # 而不是让 uvicorn 在 reload 子进程里抛一段难以定位的异常栈
    try:
        load_secret_key()
        tls = load_tls_config()
    except InsecureConfigError as e:
        raise SystemExit(f"[配置错误] {e}")

    reload_enabled = _reload_enabled()

    scheme = "https" if tls else "http"
    print(f"访问地址: {scheme}://<本机IP>:8000   WebSocket: {'wss' if tls else 'ws'}://<本机IP>:8000")
    if not tls:
        print(
            "提示: 当前为 HTTP。浏览器只在安全上下文（HTTPS 或 localhost）下暴露摄像头接口，\n"
            "      局域网内其他设备将无法开启音视频。请执行 backend/scripts/gen_dev_cert.ps1\n"
            "      生成证书，并在 .env 中配置 SSL_CERTFILE / SSL_KEYFILE 后重启。"
        )
    if reload_enabled:
        print(
            f"提示: 已开启热重载（{RELOAD_ENV_VAR}=1）。每次代码改动都会重启进程并清空\n"
            "      内存中的 WS 连接，正在进行中的会议会因此结束。仅建议本地调试时使用。"
        )

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # 局域网内所有设备可访问
        port=8000,
        reload=reload_enabled,
        **tls,        # 配置了证书即启用 TLS（对外即 https / wss）
    )