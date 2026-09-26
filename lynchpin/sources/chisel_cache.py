"""Copy immutable cache artifacts without sharing writable inodes."""

from pathlib import Path
import fcntl
import shutil


def copy_file(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as src, destination.open("wb") as dst:
            fcntl.ioctl(dst.fileno(), 0x40049409, src.fileno())  # Linux FICLONE
    except OSError:
        shutil.copyfile(source, destination)
    shutil.copystat(source, destination)
