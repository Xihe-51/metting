"""
严重-2 局域网 HTTPS / WSS —— 配置解析验证 + 前后端契约校验

【修复前的问题】
浏览器只在安全上下文（HTTPS 或 localhost）下暴露 navigator.mediaDevices。
局域网里访问 http://192.168.x.x 时 getUserMedia 是 undefined，
摄像头/麦克风全部不可用 —— 视频会议在真实局域网里其实开不了摄像头。

【修复要点】
- 后端：app.config.load_tls_config() 解析 SSL_CERTFILE / SSL_KEYFILE，
  两个都配且文件存在才给 uvicorn 传 ssl_* 参数（对外即 https/wss）；
  只配一个或文件不存在直接报错，避免「以为开了 https 其实没开」。
- 前端：vite dev server 读取 certs/ 下的自签证书启用 HTTPS，并给 /api 代理开 ws: true；
  页面为 https 时前端自动把 WS 地址改成 wss（MeetingView 已有该判断）。
- 交付物：证书生成脚本 gen_dev_cert.ps1、链路验证脚本 verify_https_wss.py、
  .env.example 与 certs/.gitignore（私钥不入库）。

运行：
    cd D:\\meeting\\backend
    python -m pytest tests/test_s17_lan_tls_config.py -v
"""
import os

import pytest

from conftest import BACKEND_ROOT  # noqa: E402
from app import config  # noqa: E402
from app.config import InsecureConfigError, load_tls_config  # noqa: E402

FRONT_ROOT = os.path.abspath(os.path.join(BACKEND_ROOT, "..", "front"))
REPO_ROOT = os.path.abspath(os.path.join(BACKEND_ROOT, ".."))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture()
def no_env_file(monkeypatch):
    """屏蔽 backend/.env，避免开发机本地配置影响断言（环境变量由用例自己控制）"""
    monkeypatch.setattr(config, "load_env_file", lambda *a, **k: None)


# ---------------- 后端 TLS 配置解析 ----------------

def test_tls_disabled_when_nothing_configured(monkeypatch, no_env_file):
    """两个变量都没配 → 返回空字典，后端保持 http（仅本机调试可用）"""
    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    assert load_tls_config() == {}


def test_tls_requires_both_cert_and_key(monkeypatch, no_env_file, tmp_path):
    """只配一个 → 报错，不允许“半开”的 TLS 配置"""
    cert = tmp_path / "dev-cert.pem"
    cert.write_text("dummy")

    monkeypatch.setenv("SSL_CERTFILE", str(cert))
    monkeypatch.delenv("SSL_KEYFILE", raising=False)
    with pytest.raises(InsecureConfigError):
        load_tls_config()

    monkeypatch.delenv("SSL_CERTFILE", raising=False)
    monkeypatch.setenv("SSL_KEYFILE", str(cert))
    with pytest.raises(InsecureConfigError):
        load_tls_config()


def test_tls_rejects_nonexistent_files(monkeypatch, no_env_file, tmp_path):
    """路径存在但文件不在 → 报错（防止证书路径写错却静默降级为 http）"""
    monkeypatch.setenv("SSL_CERTFILE", str(tmp_path / "nope-cert.pem"))
    monkeypatch.setenv("SSL_KEYFILE", str(tmp_path / "nope-key.pem"))
    with pytest.raises(InsecureConfigError):
        load_tls_config()


def test_tls_returns_uvicorn_kwargs(monkeypatch, no_env_file, tmp_path):
    """两个都配且文件存在 → 返回可直接展开给 uvicorn.run 的参数"""
    cert = tmp_path / "dev-cert.pem"
    key = tmp_path / "dev-key.pem"
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")

    monkeypatch.setenv("SSL_CERTFILE", str(cert))
    monkeypatch.setenv("SSL_KEYFILE", str(key))
    assert load_tls_config() == {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


# ---------------- 前端：vite HTTPS + WS 代理 ----------------

def test_vite_dev_server_enables_https_and_ws_proxy():
    """vite 必须能启用自签 HTTPS，并让 /api 代理支持 WebSocket 升级"""
    src = _read(os.path.join(FRONT_ROOT, "vite.config.ts"))

    assert "../certs/dev-key.pem" in src and "../certs/dev-cert.pem" in src
    assert "https:" in src                      # server.https 自签证书配置
    assert "fs.existsSync" in src               # 无证书时退回 HTTP，不打断旧流程
    assert "ws: true" in src                    # /api 代理支持 WebSocket 升级
    assert "secure: false" in src               # 上游是自签证书，代理不做校验


def test_frontend_derives_wss_from_page_protocol():
    """页面为 https 时，前端必须把 WebSocket 地址换成 wss://（否则混合内容被浏览器阻断）"""
    src = _read(os.path.join(FRONT_ROOT, "src", "views", "MeetingView.vue"))
    assert "window.location.protocol === 'https:'" in src
    assert "wss:" in src


# ---------------- 交付物：示例配置与私钥保护 ----------------

def test_env_example_documents_secret_and_tls():
    """.env.example 必须写明 SECRET_KEY 与 TLS 两个变量，供部署时照抄"""
    text = _read(os.path.join(BACKEND_ROOT, ".env.example"))
    assert "SECRET_KEY=" in text
    assert "SSL_CERTFILE" in text and "SSL_KEYFILE" in text


def test_secrets_are_gitignored():
    """私钥与 .env 绝不能进版本库"""
    certs_ignore = _read(os.path.join(REPO_ROOT, "certs", ".gitignore"))
    assert "*.pem" in certs_ignore

    backend_ignore = _read(os.path.join(BACKEND_ROOT, ".gitignore"))
    assert ".env" in backend_ignore


def test_cert_script_and_verifier_exist():
    """证书生成脚本要覆盖局域网 IP，验证脚本要能自证 https/wss 链路"""
    gen = _read(os.path.join(BACKEND_ROOT, "scripts", "gen_dev_cert.ps1"))
    assert "subjectAltName" in gen
    assert "Get-NetIPAddress" in gen            # SAN 覆盖本机全部局域网 IPv4
    assert "-days 825" in gen

    verifier = os.path.join(BACKEND_ROOT, "scripts", "verify_https_wss.py")
    assert os.path.isfile(verifier)
    v_src = _read(verifier)
    assert "wss://" in v_src and "/health" in v_src