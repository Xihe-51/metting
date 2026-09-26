# 局域网视频会议系统

面向局域网的视频会议系统，支持会议管理、实时信令、WebRTC 音视频、会议录制与局域网 HTTPS 部署。

## 技术栈

- 后端：FastAPI、SQLite、WebSocket
- 前端：Vue 3、Vite、TypeScript
- 媒体：WebRTC Mesh

## 快速开始

后端：

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"
python run.py
```

把生成的随机字符串填入 `backend/.env` 的 `SECRET_KEY`。密钥未设置、仍是示例值或长度不足 32 字符时，后端会拒绝启动。

前端：

```powershell
cd front
npm install
npm run dev
```

- 后端 API：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`
- 前端开发服务：`http://localhost:5173`

## 局域网 HTTPS

浏览器只在 HTTPS 或 localhost 下暴露摄像头和麦克风接口。局域网内其他设备访问时需要生成开发证书：

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\gen_dev_cert.ps1
```

然后在 `backend/.env` 中配置脚本输出的证书路径：

```text
SSL_CERTFILE=D:/meeting/certs/dev-cert.pem
SSL_KEYFILE=D:/meeting/certs/dev-key.pem
```

重启后端后，前端开发服务会自动使用同一套证书。自签证书不会被浏览器默认信任，首次访问需选择继续访问；如需其他局域网设备免提示，将 `certs/dev-cert.pem` 安装为受信任根证书。证书和私钥只保留在本地，不会提交到仓库。

## 测试

```powershell
cd backend
pytest
```

## 更多说明

- 后端架构、容量限制与 TURN 部署：[backend/README.md](backend/README.md)
- 前端开发命令：[front/README.md](front/README.md)
