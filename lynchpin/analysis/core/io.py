"""Shared filesystem and JSON helpers for analysis modules."""

import json
import os

from ...core.config import get_config


def project_root():
    return str(get_config().repo_root)


def resolve_project_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(project_root(), path)


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_json_if_exists(path):
    if not os.path.exists(path):
        return None
    return load_json(path)


def save_json(path, payload, sort_keys=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=sort_keys)


def save_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

