import os

def count_lines(filepath):
    """Safely count lines in a file."""
    try:
        with open(filepath, 'r', errors='ignore') as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def walk_files(base_dir, skip_dirs=None, target_exts=None, exclude_exts=None):
    """
    Yields (root, dir, filename, filepath, relative_path)
    """
    if skip_dirs is None:
        skip_dirs = {
            '.git', 'venv', '.venv', 'node_modules', '__pycache__', 'target',
            '.lynchpin', 'artefacts', '.direnv', '.ruff_cache', '.pytest_cache', '.mypy_cache',
            'dist', 'build', '.eggs', 'result', '.claude', '.playwright-mcp',
            '.sinex', '.cargo', '.nats', '.cache', '.local',
        }
    
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if target_exts and ext not in target_exts:
                continue
            if exclude_exts and ext in exclude_exts:
                continue
            
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, base_dir)
            yield root, dirs, f, fp, rel
