# ML-воркер: CUDA + torch + модели. Тяжёлый образ, отдельно от api.
#
# Базовый образ — Ubuntu 24.04: в нём уже есть python3.12, тогда как в ubuntu22.04
# пакет python3.12 отсутствует и сборка падает на apt (проверено).
# Тег 12.4.1 для ubuntu24.04 не существует (24.04 начинается с 12.6) — берём 12.6.3:
# torch ставится из индекса cu124 и несёт свои CUDA-библиотеки, драйвер 595.x их принимает.
# pip ставится в venv: в 24.04 системный pip заблокирован PEP 668.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    STORAGE_ROOT=/data \
    HF_HOME=/root/.cache/huggingface \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev \
        ffmpeg curl ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel

WORKDIR /srv/app

# torch первым слоем — самый большой и редко меняющийся
COPY requirements-ml.txt requirements.txt ./
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && pip install -r requirements.txt \
    && pip install -r requirements-ml.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

# Entrypoint выставляет LD_LIBRARY_PATH так, чтобы библиотеки из pip-пакетов nvidia-*
# (cuDNN/cuBLAS, с которыми собран torch) были в приоритете над системными из базового образа.
COPY docker/entrypoint-ml.sh /usr/local/bin/entrypoint-ml.sh
RUN chmod +x /usr/local/bin/entrypoint-ml.sh
ENTRYPOINT ["/usr/local/bin/entrypoint-ml.sh"]

CMD ["dramatiq", "--queues", "gpu", "--processes", "1", "--threads", "1", "app.workers.broker", "app.workers.tasks"]
