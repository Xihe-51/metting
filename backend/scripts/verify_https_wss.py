"""
局域网 HTTPS / WSS 链路验证脚本（严重-2 的验证用例）

背景：浏览器只在安全上下文（HTTPS 或 localhost）下暴露 navigator.mediaDevices，
后端必须以 HTTPS 对外服务、WebSocket 同步升级为 wss，局域网设备才能开摄像头。

本脚本在后端已按 HTTPS 启动后运行，检查三件事：
  1) 明文 HTTP 打同一个端口会被拒（证明 TLS 真的开了，而不是“以为开了”）；
  2) GET https://<host>:<port>/health 返回 200（页面侧接口可用）；
  3) 连接 wss://<host>:<port>/api/v1/ws/<meeting_no> 后，首帧上报「伪造 token」
     会被鉴权拒绝（1008 关闭）—— 被拒即链路通：说明 TLS 终止、路由、WS 升级
     都正常，只是 token 不合法。token 自本轮起不再走 URL，改为连接后的首帧。

用法：
    python scripts/verify_https_wss.py                       # 默认 127.0.0.1:8000
    python scripts/verify_https_wss.py --host 192.168.1.5
    python scripts/verify_https_wss.py --meeting-no 123456
退出码：0 = 全部通过；1 = 存在失败项。
"""
import argparse
import asyncio
import json
import ssl
import sys
import urllib.error
import urllib.request

import websockets
from websockets import exceptions as ws_exceptions
from websockets.exceptions import ConnectionClosed

# websockets 各版本里「握手被 HTTP 拒绝」的异常类名不一致
# （12.x 是 InvalidStatusCode，13+ 是 InvalidStatus），这里一并兼容
_HANDSHAKE_REJECTED = tuple(
    cls for cls in (
        getattr(ws_exceptions, "InvalidStatus", None),
        getattr(ws_exceptions, "InvalidStatusCode", None),
    ) if cls is not None
) or (ws_exceptions.WebSocketException,)


def unverified_ctx() -> ssl.SSLContext:
    """自签证书不在系统信任链中，验证脚本必须显式放行（仅用于本脚本自测）

    这里直接构造 TLS 客户端上下文而不调用 create_default_context()：
    后者会去读系统证书库，在部分 Windows 环境上会因证书库里的脏数据直接抛错。
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_plain_http_rejected(host: str, port: int) -> bool:
    """明文 HTTP 打 TLS 端口应当失败，否则说明后端其实还在跑 HTTP"""
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            print(f"[FAIL] 明文 {url} 竟然返回 {resp.status} —— 后端并未启用 TLS")
            return False
    except urllib.error.HTTPError as e:
        # uvicorn 收到明文请求会回 400；能拿到 400 说明端口在跑 TLS
        if e.code == 400:
            print(f"[PASS] 明文 HTTP 被拒（{e.code}），端口已启用 TLS")
            return True
        print(f"[FAIL] 明文 HTTP 返回意外状态码 {e.code}")
        return False
    except Exception as e:
        # 连接层面直接失败也是预期结果
        print(f"[PASS] 明文 HTTP 连接失败（{type(e).__name__}），端口已启用 TLS")
        return True


def check_health(host: str, port: int, ctx: ssl.SSLContext) -> bool:
    url = f"https://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, context=ctx, timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
            if resp.status == 200:
                print(f"[PASS] GET {url} -> 200 {body[:120]}")
                return True
            print(f"[FAIL] GET {url} -> {resp.status}")
            return False
    except Exception as e:
        print(f"[FAIL] GET {url} 失败：{type(e).__name__}: {e}")
        print("       请确认 backend/.env 已配置 SSL_CERTFILE / SSL_KEYFILE 并重启后端。")
        return False


async def check_wss_auth_rejected(host: str, port: int, meeting_no: str, ctx: ssl.SSLContext) -> bool:
    """伪造 token 连接 wss：握手成功 + 首帧鉴权被拒，即说明链路通且鉴权有效

    token 不再走 URL，改为连接建立后的首帧 {"type":"auth"}：
    握手能完成（101）证明 TLS 终止 / 路由 / WS 升级都正常，
    随后服务端校验 token 失败并以 1008 关闭。
    """
    url = f"wss://{host}:{port}/api/v1/ws/{meeting_no}?participant_id=1"
    try:
        async with websockets.connect(url, ssl=ctx, open_timeout=5) as ws:
            await ws.send(json.dumps({"type": "auth", "token": "forged-token-for-verification"}))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                if code == 1008:
                    print(f"[PASS] WSS 握手完成，伪造 token 被拒（close code={code}）")
                    return True
                print(f"[FAIL] WSS 已连接，关闭码 {code}（期望 1008）")
                return False
            print("[FAIL] WSS 已连接且服务端未拒绝伪造 token —— 鉴权可能失效")
            return False
    except _HANDSHAKE_REJECTED as e:
        # 若服务端在 accept 之前就关闭（例如路径不匹配），uvicorn 会以 HTTP 状态码拒绝握手
        status = getattr(e, "status_code", None)
        if status is None:
            status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 403:
            print("[PASS] WSS 握手尝试被拒（HTTP 403），链路通且鉴权有效")
            return True
        print(f"[FAIL] WSS 握手被 HTTP 层拒绝：{status}")
        return False
    except Exception as e:
        print(f"[FAIL] WSS 连接失败：{type(e).__name__}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="验证后端 HTTPS / WSS 是否可用")
    parser.add_argument("--host", default="127.0.0.1", help="后端地址（局域网部署填本机 IP）")
    parser.add_argument("--port", type=int, default=8000, help="后端端口，默认 8000")
    parser.add_argument("--meeting-no", default="000000", help="用于 WS 探测的会议号，不存在即可")
    args = parser.parse_args()

    ctx = unverified_ctx()
    results = [
        check_plain_http_rejected(args.host, args.port),
        check_health(args.host, args.port, ctx),
        asyncio.run(check_wss_auth_rejected(args.host, args.port, args.meeting_no, ctx)),
    ]

    passed = sum(1 for r in results if r)
    print(f"\n结果：{passed}/{len(results)} 项通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())