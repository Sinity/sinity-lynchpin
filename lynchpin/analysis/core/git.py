import concurrent.futures
import subprocess
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from collections.abc import Iterator, Sequence
from pathlib import Path


def run_git(
    repo: str | Path,
    *args: str,
    timeout: float | None = None,
) -> str | None:
    """Run `git <args>` in repo, returning stdout.strip() or None on any failure.

    Consolidates near-identical private wrappers across the analysis
    layer (sinex/temporal default-branch probe, ecosystem/polylogue_metrics._run_git,
    graph/current_state._git).
    Treats all failures (FileNotFoundError, CalledProcessError, TimeoutExpired,
    non-zero exit) as None — callers decide whether None or empty-string is
    semantically meaningful.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_log(
    repo_dir: str,
    branch: str = "HEAD",
    after: str | None = None,
    params: Sequence[str] | None = None,
) -> Iterator[str]:
    """
    Returns git log generator line by line.
    """
    cmd = ["git", "log", branch]
    if after:
        cmd.extend(["--after", after])
    if params:
        cmd.extend(params)

    try:
        proc = subprocess.Popen(cmd, cwd=repo_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if proc.stdout is None:
            return
        for line in proc.stdout:
            yield line
    except Exception as e:
        print(f"Error executing git log in {repo_dir}: {e}")
        return


def _run_checked(cmd: Sequence[str], cwd: str) -> None:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "unknown git error"
        raise RuntimeError(f'git command failed in {cwd}: {" ".join(cmd)}: {detail}')


@contextmanager
def branch_clone(repo_dir: str, branch: str) -> Iterator[str]:
    """Yield a temporary clean local clone checked out at the requested branch."""
    with tempfile.TemporaryDirectory(prefix="analysis-git-") as tmp:
        _run_checked(["git", "clone", "--quiet", "--local", "--shared", repo_dir, tmp], cwd=repo_dir)
        _run_checked(["git", "checkout", "--quiet", branch], cwd=tmp)
        yield tmp


def blame_file(repo_dir: str, filepath: str, after_epoch: int | None = None) -> dict[str, int]:
    """
    Returns a dict of author -> lines mapped via active lines in the file.
    If after_epoch is given, only counts lines whose commit timestamp >= that epoch.
    """
    counts: dict[str, int] = defaultdict(int)
    try:
        out = subprocess.check_output(
            ["git", "blame", "--line-porcelain", filepath],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
        )
        out_text = out.decode("utf-8", errors="ignore")

        cur_author: str | None = None
        cur_time: int | None = None

        for line in out_text.split("\n"):
            if line.startswith("author "):
                cur_author = line[7:].strip()
            elif line.startswith("committer-time "):
                try:
                    cur_time = int(line.split(" ", 1)[1])
                except ValueError:
                    cur_time = 0
            elif line.startswith("\t"):
                # This is the actual source line — flush the current blame entry
                if cur_author:
                    if after_epoch is None or (cur_time and cur_time >= after_epoch):
                        counts[cur_author] += 1
                cur_author = None
                cur_time = None

        return counts
    except subprocess.CalledProcessError:
        return {}


def bulk_blame(
    repo_dir: str,
    filepaths: Sequence[str],
    max_workers: int = 8,
    after_date: str | None = None,
) -> dict[str, int]:
    """
    Batches git blame operations in thread pool.
    If after_date is given (YYYY-MM-DD string), only counts lines committed on or after that date.
    """
    from datetime import datetime

    after_epoch: int | None = None
    if after_date:
        after_epoch = int(datetime.strptime(after_date, "%Y-%m-%d").timestamp())

    aggregated: dict[str, int] = defaultdict(int)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(blame_file, repo_dir, fp, after_epoch): fp for fp in filepaths}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            for auth, count in res.items():
                aggregated[auth] += count
    return dict(aggregated)
