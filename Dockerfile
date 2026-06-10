FROM python:3.11-slim

# ffmpeg: PySceneDetect·ffmpeg 의존, libgl1/libglib2.0: OpenCV 의존
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ 는 볼륨으로 마운트하므로 빈 디렉토리만 생성
RUN mkdir -p data/videos data/frames data/exports

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app/Home.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.enableCORS=false"]
