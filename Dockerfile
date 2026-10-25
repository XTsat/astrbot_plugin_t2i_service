FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt requirements-standalone.txt ./
RUN pip install --no-cache-dir -r requirements-standalone.txt \
    && playwright install --with-deps chromium

COPY . .

# 独立服务模式（FastAPI + uvicorn）。若以 AstrBot 插件方式使用，
# 不要把本镜像装进 AstrBot，插件由仓库根目录的 main.py 直接提供渲染服务。
CMD ["python", "service/main.py"]
