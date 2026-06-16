# 局域网视频会议系统 - 后端

FastAPI + SQLite + WebSocket

## 项目结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口
│   ├── database.py         # SQLAlchemy 配置
│   ├── models.py           # 数据库模型
│   ├── websocket_manager.py # WebSocket 连接管理
│   └── routers/
│       ├── __init__.py
│       └── meeting_router.py # 会议路由
├── recordings/             # 录制文件存储目录
├── static/                 # 静态文件目录
├── meeting.db              # SQLite 数据库（运行后自动创建）
├── requirements.txt        # Python 依赖
└── run.py                  # 启动脚本
```

## 安装依赖

```bash
# 创建虚拟环境（可选）
python -m venv venv
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

## 运行

```bash
python run.py
```

服务启动后：
- API 地址: http://localhost:8000
- API 文档: http://localhost:8000/docs （Swagger UI）
- WebSocket: ws://localhost:8000/ws/{meeting_no}

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/meetings` | POST | 创建会议 |
| `/api/v1/meetings/{no}/join` | POST | 加入会议 |
| `/api/v1/meetings/{no}` | GET | 获取会议信息 |
| `/api/v1/meetings/{no}/end` | POST | 结束会议 |
| `/api/v1/meetings` | GET | 获取会议列表 |
| `/ws/{meeting_no}` | WebSocket | 实时通信 |

## 测试

打开浏览器访问 http://localhost:8000/docs 可以看到自动生成的 API 文档，可以直接测试接口。