# Works on Railway, Fly.io, a VPS, or anything that runs containers.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 TRENCH_HOSTED=1 TRENCH_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "run.py"]
