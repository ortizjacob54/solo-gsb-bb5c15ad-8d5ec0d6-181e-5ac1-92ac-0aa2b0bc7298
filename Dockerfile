FROM python:3.12-slim

WORKDIR /app

# 先装依赖，充分利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据库文件位置（可用环境变量覆盖）；/data 挂载卷以持久化
ENV DATABASE_PATH=/data/tide.db
VOLUME ["/data"]

EXPOSE 8000

# 启动时自动完成数据库初始化（CREATE TABLE IF NOT EXISTS，幂等）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
