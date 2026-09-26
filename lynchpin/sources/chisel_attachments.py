"""Size-bounded archives with inspectable manifests and verified reconstruction."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _archive(root: Path, target: Path, paths: list[Path], companions: list[str]) -> dict[str, Any]:
    manifest = {"schema_version": 1, "companions": companions,
                "contents": [{"path": p.relative_to(root).as_posix(),
                              "bytes": p.stat().st_size, "sha256": digest(p)} for p in paths]}
    portfolio = root / "portfolio.json"
    manifest["identities"] = json.loads(portfolio.read_text()).get("projects", []) if portfolio.exists() else []
    target.unlink(missing_ok=True)
    with tarfile.open(target, "w:gz", compresslevel=3) as archive:
        data = (json.dumps(manifest, indent=2) + "\n").encode()
        header = tarfile.TarInfo(f"attachments/{target.name}.json")
        header.size = len(data)
        archive.addfile(header, io.BytesIO(data))
        guide = (f"# {target.name}\n\n" + "Companions: " + (", ".join(companions) or "none") +
                 f"\n\nFile hashes and snapshot identities: `{target.name}.json`.\n\n" +
                 "\n".join(f"- `{row['path']}` ({row['bytes']} bytes)" for row in manifest["contents"]) + "\n").encode()
        header = tarfile.TarInfo(f"attachments/{target.name}.md")
        header.size = len(guide)
        archive.addfile(header, io.BytesIO(guide))
        for path in paths:
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return {"path": target.name, "bytes": target.stat().st_size,
            "sha256": digest(target), **manifest}


def build_attachments(root: Path, projects: list[str], *, limit: int, layout: str = "auto") -> dict[str, Any]:
    if limit < 4096:
        raise ValueError("attachment limit must be at least 4096 bytes for manifest overhead")
    (root / "reconstruct.py").write_text(RECONSTRUCT)
    project_paths = {name: sorted(p for p in (root / name).rglob("*") if p.is_file()
                        and not (p.parent == root / name and (p.name == "index.sqlite3" or p.suffix == ".xml"
                                 or p.name.endswith(("-working-tree.tar.gz", "-beads.html"))))) for name in projects}
    metadata = [root / name for name in ("portfolio.json", "START_HERE.md", "ATTACHMENT_START_HERE.md", "reconstruct.py", "cross-project-links.jsonl",
                "cross-project-links.coverage.json") if (root / name).is_file()]
    metadata += sorted(p for p in (root / "growth").rglob("*") if p.is_file())
    metadata += [root / "logs" / f"{name}.log" for name in projects if (root / "logs" / f"{name}.log").is_file()]
    attachments = []
    reconstruction = []

    def emit(name: str, paths: list[Path], *, can_partition: bool = True) -> None:
        target = root / f"{name}.tar.gz"
        row = _archive(root, target, paths, [])
        if row["bytes"] <= limit:
            attachments.append(row)
            return
        target.unlink()
        if len(paths) > 1 and can_partition:
            middle = len(paths) // 2
            emit(name + "-1", paths[:middle])
            emit(name + "-2", paths[middle:])
            return
        if len(paths) != 1:
            raise ValueError("cannot partition empty attachment")
        original = paths[0]
        chunk_size = limit // 2
        count = (original.stat().st_size + chunk_size - 1) // chunk_size
        part_names = [f"{name}.part-{i + 1:04d}.tar.gz" for i in range(count)]
        original_record = {"path": original.relative_to(root).as_posix(),
                           "sha256": digest(original), "bytes": original.stat().st_size,
                           "mode": original.stat().st_mode & 0o777, "parts": part_names}
        reconstruction.append(original_record)
        with original.open("rb") as stream:
            for index, part in enumerate(part_names):
                data = stream.read(chunk_size)
                part_manifest = {**original_record, "part": index + 1,
                                 "payload_sha256": hashlib.sha256(data).hexdigest()}
                target = root / part
                target.unlink(missing_ok=True)
                with tarfile.open(target, "w:gz", compresslevel=3) as archive:
                    for filename, content in ((f"parts/{part}.bin", data),
                        (f"parts/{part}.json", json.dumps(part_manifest).encode()),
                        ("reconstruct.py", RECONSTRUCT.encode())):
                        header = tarfile.TarInfo(filename)
                        header.size = len(content)
                        archive.addfile(header, io.BytesIO(content))
                if target.stat().st_size > limit:
                    raise ValueError("part manifest overhead exceeds attachment limit")
                attachments.append({"path": part, "bytes": target.stat().st_size,
                                    "sha256": digest(target), "contents": [part_manifest],
                                    "companions": part_names, "reconstruction": "python3 reconstruct.py"})

    all_paths = metadata + [p for name in projects for p in project_paths[name]]
    if layout == "auto":
        trial = _archive(root, root / "portfolio-all.tar.gz", all_paths, [])
        if trial["bytes"] <= limit:
            attachments.append(trial)
        else:
            (root / "portfolio-all.tar.gz").unlink()
    if not attachments:
        emit("portfolio-metadata", metadata)
        for name in projects:
            paths = project_paths[name]
            if layout != "dataset":
                target = root / f"{name}-all.tar.gz"
                row = _archive(root, target, paths, ["portfolio-metadata.tar.gz"])
                if row["bytes"] <= limit:
                    attachments.append(row)
                    continue
                target.unlink()
            groups: dict[str, list[Path]] = {}
            for path in paths:
                rel = path.relative_to(root / name)
                groups.setdefault(rel.parts[0] if len(rel.parts) > 1 else "metadata", []).append(path)
            for dataset, files in groups.items():
                emit(f"{name}-{dataset}", files)
    # Rebind companion requirements after the actual partition is known.
    # Extra metadata may require another split near a tight byte boundary.
    for _ in range(10):
        names = [row["path"] for row in attachments]
        pending = attachments[:]
        attachments.clear()
        split = False
        for row in pending:
            if row.get("reconstruction"):
                attachments.append(row)
                continue
            companions = [name for name in names if name != row["path"]]
            if row.get("companions") == companions:
                attachments.append(row)
                continue
            paths = [root / item["path"] for item in row["contents"]]
            rebuilt = _archive(root, root / row["path"], paths, companions)
            if rebuilt["bytes"] <= limit:
                attachments.append(rebuilt)
            else:
                (root / row["path"]).unlink()
                split = True
                if len(paths) > 1:
                    middle = len(paths) // 2
                    emit(row["path"].removesuffix(".tar.gz") + "-a", paths[:middle])
                    emit(row["path"].removesuffix(".tar.gz") + "-b", paths[middle:])
                else:
                    raise ValueError("companion manifest exceeds attachment cap for one artifact")
        if not split:
            break
    else:
        raise ValueError("attachment partition did not converge")
    manifest = {"schema_version": 1, "limit_bytes": limit, "projects": projects,
                "attachments": attachments, "reconstruction": reconstruction}
    (root / "attachments.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "reconstruct.py").write_text(RECONSTRUCT)
    (root / "ATTACHMENT_START_HERE.md").write_text(
        "# Attachments\n\nExtract every listed archive into one directory. Each archive contains its own file manifest. "
        "For numbered parts, run `python3 reconstruct.py` from that directory. "
        "The command verifies every part and reconstructed artifact.\n\n" +
        "\n".join(f"- `{r['path']}`: {r['bytes']} bytes; SHA-256 `{r['sha256']}`" for r in attachments) + "\n")
    return manifest


RECONSTRUCT = '''#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

root = Path.cwd()
groups = {}
for path in (root / "parts").glob("*.json"):
    row = json.loads(path.read_text())
    groups.setdefault(row["path"], []).append((row, path.with_suffix(".bin")))
for relative, records in groups.items():
    target = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("unsafe reconstruction path")
    records.sort(key=lambda pair: pair[0]["part"])
    first = records[0][0]
    if [r["part"] for r, _ in records] != list(range(1, len(first["parts"]) + 1)):
        raise ValueError("missing or duplicate parts: " + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".reconstructing")
    h = hashlib.sha256()
    with temporary.open("xb") as output:
        for row, payload in records:
            data = payload.read_bytes()
            if hashlib.sha256(data).hexdigest() != row["payload_sha256"]:
                raise ValueError("corrupt part")
            if row["sha256"] != first["sha256"] or row["parts"] != first["parts"]:
                raise ValueError("inconsistent part identity")
            h.update(data)
            output.write(data)
    if h.hexdigest() != first["sha256"] or temporary.stat().st_size != first["bytes"]:
        raise ValueError("reconstruction hash/size mismatch")
    temporary.chmod(first.get("mode", 0o644))
    temporary.replace(target)
    print(relative)
'''
