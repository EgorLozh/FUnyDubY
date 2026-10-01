"""Показать структуру чекпоинта Bandit: гиперпараметры модели и ключи весов.

Запуск в контейнере воркера:
    python scripts/inspect_bandit_ckpt.py /data/models/bandit/checkpoint-multi.ckpt
"""
from __future__ import annotations

import sys

import torch


def _print_tree(data: dict, prefix: str = "", depth: int = 0) -> None:
    if depth > 3:
        return
    for key, value in data.items():
        if isinstance(value, dict):
            print(f"  {prefix}{key}:")
            _print_tree(value, prefix + "  ", depth + 1)
        else:
            text = repr(value)
            if len(text) > 160:
                text = text[:157] + "..."
            print(f"  {prefix}{key} = {text}")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/data/models/bandit/checkpoint-multi.ckpt"
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)

    print("=== верхний уровень чекпоинта ===")
    for key, value in checkpoint.items():
        if key == "state_dict":
            print(f"  {key}: {len(value)} тензоров")
        else:
            print(f"  {key}: {type(value).__name__}")

    hyper = checkpoint.get("hyper_parameters") or {}
    print("=== hyper_parameters ===")
    _print_tree(hyper)

    state = checkpoint.get("state_dict") or {}
    print("=== state_dict ===")
    print("  всего тензоров:", len(state))
    for key in list(state)[:6]:
        print("   ", key, tuple(state[key].shape))
    last = list(state)[-3:]
    for key in last:
        print("   ", key, tuple(state[key].shape))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
