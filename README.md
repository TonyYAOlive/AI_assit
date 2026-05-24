# 个人日程 AI 助手

一个用自然语言管理日程的最小后端。说一句话自动解析成结构化日程入库，也能用一句话查未完成的日程。

## 技术栈

- Python 3.10+ / FastAPI / SQLAlchemy / SQLite
- Claude API（通过 LLM 抽象层封装，未来可零成本替换为 Deepseek）

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 ANTHROPIC_API_KEY

# 3. 启动
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/docs 可以看到自动生成的 Swagger 文档。

## 接口

### 1. 解析并存储日程
```bash
curl -X POST http://localhost:8000/api/parse \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午3点和老王开会，讨论Q2预算，大概1小时，很重要"}'
```

### 2. 自然语言查询
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"text":"这周还有什么没做完的？"}'

curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"text":"有哪些逾期的高优先级日程？"}'
```

### 3. 列表查询（不走 LLM，给小程序首页用）
```bash
curl http://localhost:8000/api/schedules?status=pending
```

### 4. 标记完成
```bash
curl -X PATCH http://localhost:8000/api/schedules/1/status \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}'
```

### 5. 删除
```bash
curl -X DELETE http://localhost:8000/api/schedules/1
```

## 部署到 Linux 服务器

### Nginx 反代 + SSL

```nginx
# /etc/nginx/sites-available/schedule-ai
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

SSL 证书用 Let's Encrypt 免费申请：`certbot --nginx -d your-domain.com`

### systemd 守护进程

```ini
# /etc/systemd/system/schedule-ai.service
[Unit]
Description=Schedule AI Backend
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/schedule-ai
Environment="PATH=/path/to/schedule-ai/venv/bin"
ExecStart=/path/to/schedule-ai/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now schedule-ai
sudo systemctl status schedule-ai
```

## 微信小程序对接要点

- 在小程序后台「服务器域名」白名单中加上你的域名
- 必须 HTTPS
- 请求示例（小程序端）：

```javascript
wx.request({
  url: 'https://your-domain.com/api/parse',
  method: 'POST',
  data: { text: '明天下午3点和老王开会' },
  success(res) { console.log(res.data) }
})
```

## 未来切换到 Deepseek

1. `pip install openai`
2. `.env` 加 `DEEPSEEK_API_KEY=xxx`
3. 取消 `app/llm/deepseek.py` 中的注释
4. 修改 `app/llm/claude.py` 末尾：
   ```python
   from app.llm.deepseek import DeepseekClient
   llm_client: LLMClient = DeepseekClient(api_key=os.getenv("DEEPSEEK_API_KEY"))
   ```
5. 重启服务，业务代码无需任何改动

## 数据库迁移到 PostgreSQL（数据量大了再考虑）

1. `pip install psycopg2-binary`
2. `.env` 改 `DATABASE_URL=postgresql://user:pwd@localhost:5432/schedule`
3. 重启，SQLAlchemy 自动适配

## 项目结构

```
schedule-ai/
├── app/
│   ├── main.py              FastAPI 入口
│   ├── config.py            环境变量
│   ├── models.py            SQLAlchemy 模型
│   ├── schemas.py           Pydantic 请求模型
│   ├── llm/
│   │   ├── base.py          LLM 抽象接口
│   │   ├── claude.py        Claude 实现
│   │   └── deepseek.py      Deepseek 实现（预留）
│   ├── services/
│   │   ├── parser.py        自然语言 → 日程
│   │   └── query.py         自然语言 → 查询
│   └── api/
│       └── routes.py        HTTP 路由
├── .env.example
├── requirements.txt
└── README.md
```
