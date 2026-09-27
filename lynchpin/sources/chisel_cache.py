"""Copy immutable cache artifacts without sharing writable inodes."""

from pathlib import Path
import fcntl
import os
import stat
import shutil
import tempfile


def atomic_write_text(destination: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Publish a complete cache text file using a unique sibling temporary."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        mode = None
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as stream:
            stream.write(text)
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def copy_file(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as src, destination.open("wb") as dst:
            fcntl.ioctl(dst.fileno(), 0x40049409, src.fileno())  # Linux FICLONE
    except OSError:
        shutil.copyfile(source, destination)
    shutil.copystat(source, destination)
