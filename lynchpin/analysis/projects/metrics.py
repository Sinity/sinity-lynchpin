"""Cross-Project Productivity and Structural Metrics Suite"""
import os
from collections import Counter
from datetime import datetime

from ..core.io import save_json
from ..core.fs import walk_files
from ..core.git import get_log
from ...core.projects import ALL_PROJECTS

PROJECTS_META = {
    name: {"era": p.era, "ext": p.extensions, "path": p.path}
    for name, p in ALL_PROJECTS.items()
}


def _project_dir(base_dir, project_name, meta):
    return os.path.join(base_dir, meta.get("path", project_name))

def analyze_structural(base_dir):
    """Assess source code distributions for all configured projects."""
    results = {}
    for proj, meta in PROJECTS_META.items():
        proj_dir = _project_dir(base_dir, proj, meta)
        if not os.path.isdir(proj_dir):
            continue
        
        total_files = 0
        total_lines = 0
        comments = 0
        blank = 0
        sizes = []
        ext_counter = Counter()
        
        for root, dirs, f, fp, rel in walk_files(proj_dir):
            ext = os.path.splitext(f)[1].lower()
            if not ext or ext in {'.png', '.jpg', '.ico', '.dll', '.exe', '.so', '.pdf', '.a'}: 
                continue
                
            try:
                with open(fp, 'r', errors='ignore') as f_obj:
                    lines = f_obj.readlines()
            except Exception:
                continue
            
            n = len(lines)
            if n == 0:
                continue
            
            total_files += 1
            total_lines += n
            ext_counter[ext] += 1
            sizes.append(n)
            
            cmt_char = '//' if ext in ('.rs','.c','.cpp','.h','.hpp','.cs','.js') else '#' if ext in ('.py','.nix') else None
            for line in lines:
                s = line.strip()
                if not s:
                    blank += 1
                elif cmt_char and s.startswith(cmt_char):
                    comments += 1

        if not sizes:
            continue
        sizes.sort()
        results[proj] = {
            "era": meta["era"],
            "files_counted": total_files,
            "total_lines": total_lines,
            "blank_lines": blank,
            "comment_lines": comments,
            "comment_ratio": round(comments / max(1, total_lines) * 100, 1),
            "median_file_size": sizes[len(sizes)//2],
            "max_file_size": sizes[-1],
            "extensions": dict(ext_counter.most_common(5))
        }
    return results

def analyze_productivity(base_dir, results):
    """Append Git usage stats, commits per day, etc. to existing structural results."""
    for proj, meta in PROJECTS_META.items():
        proj_dir = _project_dir(base_dir, proj, meta)
        if not os.path.isdir(os.path.join(proj_dir, '.git')):
            continue
        
        # log format config for generator parsing (this simplifies things)
        dates = set()
        total_added = 0
        total_deleted = 0
        commits = 0
        cur_added = 0
        commit_sizes = []
        
        for line in get_log(proj_dir, branch="--all", params=['--pretty=format:COMMIT|%aI', '--numstat']):
            line = line.strip()
            if not line:
                continue
            if line.startswith('COMMIT|'):
                if cur_added > 0:
                    commit_sizes.append(cur_added)
                cur_added = 0
                dates.add(line.split('|')[1][:10])
                commits += 1
            elif '\t' in line:
                parts = line.split('\t')
                if len(parts) >= 3 and parts[0] != '-':
                    a = int(parts[0])
                    d = int(parts[1]) if parts[1] != '-' else 0
                    ext = os.path.splitext(parts[2])[1].lower()
                    if ext in meta["ext"] or not meta["ext"]:
                        total_added += a
                        total_deleted += d
                        cur_added += a

        if cur_added > 0:
            commit_sizes.append(cur_added)
        
        dates = sorted(dates)
        active_days = len(dates)
        if active_days > 1:
            first = datetime.strptime(dates[0], "%Y-%m-%d")
            last = datetime.strptime(dates[-1], "%Y-%m-%d")
            span_days = max(1, (last - first).days)
        else:
            span_days = 1
            
        commit_sizes.sort()

        if proj not in results:
            results[proj] = {}
        p = results[proj]
        
        p["commits"] = commits
        p["code_added"] = total_added
        p["code_deleted"] = total_deleted
        p["net_code"] = total_added - total_deleted
        p["churn_ratio"] = round(total_deleted / max(1, total_added) * 100, 1) if total_added else 0
        p["active_days"] = active_days
        p["span_days"] = span_days
        
        if active_days > 0 and total_added > 100:
            p["added_per_active_day"] = round(total_added / active_days)
            p["commits_per_active_day"] = round(commits / active_days, 1)
            p["median_commit_size"] = commit_sizes[len(commit_sizes)//2] if commit_sizes else 0
            p["mean_commit_size"] = round(sum(commit_sizes)/max(1, len(commit_sizes)))

    return results

def run_cross_project(base_dir, out_file):
    print("Gathering structural footprint...")
    res = analyze_structural(base_dir)
    print("Gathering productivity traces...")
    res = analyze_productivity(base_dir, res)
    
    save_json(out_file, {"projects": res})
    print(f"Results saved to {out_file}.")
