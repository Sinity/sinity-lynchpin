import concurrent.futures
import subprocess
import tempfile
from collections import defaultdict
from contextlib import contextmanager

def get_log(repo_dir, branch="HEAD", after=None, params=None):
    """
    Returns git log generator line by line.
    """
    cmd = ['git', 'log', branch]
    if after:
        cmd.extend(['--after', after])
    if params:
        cmd.extend(params)
    
    try:
        proc = subprocess.Popen(cmd, cwd=repo_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            yield line
    except Exception as e:
        print(f"Error executing git log in {repo_dir}: {e}")
        return

def _run_checked(cmd, cwd):
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or 'unknown git error'
        raise RuntimeError(f'git command failed in {cwd}: {" ".join(cmd)}: {detail}')


@contextmanager
def branch_clone(repo_dir, branch):
    """Yield a temporary clean local clone checked out at the requested branch."""
    with tempfile.TemporaryDirectory(prefix='analysis-git-') as tmp:
        _run_checked(['git', 'clone', '--quiet', '--local', '--shared', repo_dir, tmp], cwd=repo_dir)
        _run_checked(['git', 'checkout', '--quiet', branch], cwd=tmp)
        yield tmp

def blame_file(repo_dir, filepath, after_epoch=None):
    """
    Returns a dict of author -> lines mapped via active lines in the file.
    If after_epoch is given, only counts lines whose commit timestamp >= that epoch.
    """
    counts = defaultdict(int)
    try:
        out = subprocess.check_output(
            ['git', 'blame', '--line-porcelain', filepath],
            cwd=repo_dir, stderr=subprocess.DEVNULL
        )
        out_text = out.decode('utf-8', errors='ignore')

        cur_author = None
        cur_time = None

        for line in out_text.split('\n'):
            if line.startswith('author '):
                cur_author = line[7:].strip()
            elif line.startswith('committer-time '):
                try:
                    cur_time = int(line.split(' ', 1)[1])
                except Exception:
                    cur_time = 0
            elif line.startswith('\t'):
                # This is the actual source line — flush the current blame entry
                if cur_author:
                    if after_epoch is None or (cur_time and cur_time >= after_epoch):
                        counts[cur_author] += 1
                cur_author = None
                cur_time = None

        return counts
    except subprocess.CalledProcessError:
        return {}


def bulk_blame(repo_dir, filepaths, max_workers=8, after_date=None):
    """
    Batches git blame operations in thread pool.
    If after_date is given (YYYY-MM-DD string), only counts lines committed on or after that date.
    """
    from datetime import datetime
    after_epoch = None
    if after_date:
        after_epoch = int(datetime.strptime(after_date, '%Y-%m-%d').timestamp())

    aggregated = defaultdict(int)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(blame_file, repo_dir, fp, after_epoch): fp for fp in filepaths}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            for auth, count in res.items():
                aggregated[auth] += count
    return dict(aggregated)
