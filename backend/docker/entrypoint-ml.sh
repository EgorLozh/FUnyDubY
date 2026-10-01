#!/bin/sh
# Точка входа ML-воркера.
#
# Зачем нужна: базовый образ nvidia/cuda кладёт в LD_LIBRARY_PATH системные cuDNN/cuBLAS
# версии 9.5.x, а torch из pip несёт свои (например 9.24) и падает с
#   RuntimeError: cuDNN version incompatibility: PyTorch was compiled against (9, 24, 0)
#   but found runtime version (9, 5, 1)
# Порядок в LD_LIBRARY_PATH решает: сначала библиотеки из pip-пакетов nvidia-*, потом всe остальное.
set -e

PIP_NVIDIA_LIBS=$(python - <<'PY'
import glob
import os
import site

paths = []
for base in site.getsitepackages() + [site.getusersitepackages()]:
    paths.extend(glob.glob(os.path.join(base, "nvidia", "*", "lib")))
print(":".join(sorted(set(paths))))
PY
)

if [ -n "$PIP_NVIDIA_LIBS" ]; then
    export LD_LIBRARY_PATH="$PIP_NVIDIA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec "$@"
