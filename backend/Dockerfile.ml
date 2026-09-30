# ML-woker: CUDA + torch + модели. Тяжёлый образ, отдельно от api.
# Базовый образ с CUDA-рантаймом; torch ставится с индексом cu124 (проверено: доступен с хоста).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    STORAGE_ROOT=/data \
    HF_HOME=/root/.cache/huggingface \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip ffmpeg curl ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/local/bin/python \
    && ln -sf /usr/bin/python3.12 /usr/local/bin/python3

WORKDIR /srv/app
RUN python -m pip install --upgrade pip

# torch первым слоем — самый большой и редко меняющийся
COPY requirements-ml.txt requirements.txt ./
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && pip install -r requirements.txt \
    && pip install -r requirements-ml.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

CMD ["dramatiq", "app.workers.broker", "--queues", "gpu,system", "--processes", "1", "--threads", "1", "--prefetch", "1"]
