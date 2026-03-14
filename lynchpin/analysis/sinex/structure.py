"""Sinex (Rust) codebase structural analysis.

Uses tokei for authoritative line counts, with custom analysis for
structural metrics (structs, traits, unsafe blocks) and test detection.
"""
import os
import re
import json
import subprocess
from collections import defaultdict, Counter
from datetime import datetime
from ..core import git


SINEX_DIR_DEFAULT = '/realm/project/sinex'
SKIP_DIRS = {'.git', 'target', 'node_modules', '.direnv', '.sinex'}


def discover_crates(sinex_dir):
    """Find all Rust crates by locating Cargo.toml files."""
    crates = []
    for root, dirs, files in os.walk(sinex_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if 'Cargo.toml' in files:
            rel = os.path.relpath(root, sinex_dir)
            if rel == '.':
                continue  # Skip workspace root
            crates.append(rel)
    return sorted(crates)


def run_tokei(path):
    """Run tokei on a path and return the Rust stats dict."""
    try:
        out = subprocess.check_output(
            ['tokei', path, '--output', 'json'],
            stderr=subprocess.DEVNULL, text=True
        )
        data = json.loads(out)
        rust = data.get('Rust', {})
        return {
            'code': rust.get('code', 0),
            'comments': rust.get('comments', 0),
            'blanks': rust.get('blanks', 0),
        }
    except:
        return {'code': 0, 'comments': 0, 'blanks': 0}


def run_tokei_multi(paths):
    """Run tokei on multiple paths combined."""
    if not paths:
        return {'code': 0, 'comments': 0, 'blanks': 0}
    try:
        cmd = ['tokei'] + list(paths) + ['--output', 'json']
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        data = json.loads(out)
        rust = data.get('Rust', {})
        return {
            'code': rust.get('code', 0),
            'comments': rust.get('comments', 0),
            'blanks': rust.get('blanks', 0),
        }
    except:
        return {'code': 0, 'comments': 0, 'blanks': 0}


def analyze_crate(sinex_dir, crate_path):
    """Analyze a single crate using tokei + custom structural metrics."""
    full_path = os.path.join(sinex_dir, crate_path)

    # === Use tokei for authoritative line counts ===
    tokei_all = run_tokei(full_path)

    # Find tests/ dir for this crate
    tests_dir = os.path.join(full_path, 'tests')
    tokei_tests = run_tokei(tests_dir) if os.path.isdir(tests_dir) else {'code': 0, 'comments': 0, 'blanks': 0}

    # === Custom: count inline #[cfg(test)] blocks + structural metrics ===
    inline_test_code = 0
    unsafe_count = 0
    struct_count = 0
    enum_count = 0
    trait_count = 0
    impl_count = 0
    fn_count = 0
    file_count = 0
    file_sizes = []
    largest_file = ('', 0)

    for root, dirs, files in os.walk(full_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith('.rs'):
                continue
            fp = os.path.join(root, f)
            try:
                with open(fp, 'r', errors='ignore') as fh:
                    lines = fh.readlines()
            except:
                continue

            file_count += 1
            n = len(lines)
            file_sizes.append(n)

            if n > largest_file[1]:
                largest_file = (os.path.relpath(fp, sinex_dir), n)

            is_test_file = '/tests/' in fp

            # Track inline test blocks (only in non-test-dir files)
            in_test_block = False
            brace_depth = 0

            for line in lines:
                s = line.strip()

                # Skip blanks and comments for code-level counting
                is_code = bool(s) and not s.startswith('//')

                if not is_test_file:
                    if '#[cfg(test)]' in s.replace(' ', ''):
                        in_test_block = True
                        brace_depth = 0

                    if in_test_block:
                        if is_code:
                            inline_test_code += 1
                        brace_depth += line.count('{') - line.count('}')
                        if brace_depth <= 0 and ('}' in s or ';' in s):
                            in_test_block = False

                # Structural metrics on ALL files
                if 'unsafe ' in s or 'unsafe{' in s:
                    unsafe_count += 1
                if re.match(r'\s*pub\s+struct\s+', line) or re.match(r'\s*struct\s+', line):
                    struct_count += 1
                if re.match(r'\s*pub\s+enum\s+', line) or re.match(r'\s*enum\s+', line):
                    enum_count += 1
                if re.match(r'\s*pub\s+trait\s+', line) or re.match(r'\s*trait\s+', line):
                    trait_count += 1
                if re.match(r'\s*impl\s+', line):
                    impl_count += 1
                if re.match(r'\s*(?:pub\s+)?(?:async\s+)?fn\s+', line):
                    fn_count += 1

    # Total test code = test dir code (from tokei) + inline test code
    total_test_code = tokei_tests['code'] + inline_test_code
    total_code = tokei_all['code']
    app_code = total_code - total_test_code

    file_sizes.sort()
    n_files = len(file_sizes)

    # Extract crate name from Cargo.toml
    cargo_path = os.path.join(full_path, 'Cargo.toml')
    crate_name = crate_path
    try:
        with open(cargo_path, 'r') as f:
            for line in f:
                m = re.match(r'name\s*=\s*"([^"]+)"', line.strip())
                if m:
                    crate_name = m.group(1)
                    break
    except:
        pass

    total_lines = tokei_all['code'] + tokei_all['comments'] + tokei_all['blanks']
    comment_pct = round((tokei_all['comments']) / max(1, total_code) * 100, 1)

    return {
        'path': crate_path,
        'name': crate_name,
        'files': file_count,
        'total_lines': total_lines,
        'code_lines': total_code,
        'app_code_lines': app_code,
        'test_code_lines': total_test_code,
        'test_dir_code': tokei_tests['code'],
        'inline_test_code': inline_test_code,
        'comment_lines': tokei_all['comments'],
        'blank_lines': tokei_all['blanks'],
        'test_to_app_ratio': round(total_test_code / max(1, app_code), 2),
        'comment_ratio': comment_pct,
        'unsafe_blocks': unsafe_count,
        'structs': struct_count,
        'enums': enum_count,
        'traits': trait_count,
        'impls': impl_count,
        'functions': fn_count,
        'median_file_size': file_sizes[n_files // 2] if n_files else 0,
        'max_file_size': file_sizes[-1] if n_files else 0,
        'largest_file': largest_file[0],
    }


def compute_crate_timeline(sinex_dir):
    """Determine when each crate was first created (first Cargo.toml commit)."""
    print("Computing crate creation timeline...")
    crates = discover_crates(sinex_dir)
    timeline = {}

    for crate in crates:
        cargo_rel = os.path.join(crate, 'Cargo.toml')
        try:
            out = subprocess.check_output(
                ['git', 'log', '--all', '--follow', '--diff-filter=A',
                 '--pretty=format:%aI', '--', cargo_rel],
                cwd=sinex_dir, text=True, stderr=subprocess.DEVNULL
            )
            dates = out.strip().split('\n')
            if dates and dates[-1]:
                timeline[crate] = dates[-1][:10]
        except:
            pass

    return dict(sorted(timeline.items(), key=lambda x: x[1]))


def run_sinex_analysis(sinex_dir, out_file):
    """Full sinex structural analysis."""
    print(f"Discovering crates in {sinex_dir}...")
    crates = discover_crates(sinex_dir)
    print(f"  Found {len(crates)} crates")

    print("Analyzing crate structure (using tokei)...")
    crate_data = {}
    totals = {
        'files': 0, 'total_lines': 0, 'code_lines': 0,
        'app_code_lines': 0, 'test_code_lines': 0,
        'structs': 0, 'enums': 0,
        'traits': 0, 'functions': 0, 'unsafe_blocks': 0,
    }

    for crate in crates:
        info = analyze_crate(sinex_dir, crate)
        crate_data[crate] = info
        for k in totals:
            totals[k] += info.get(k, 0)

    timeline = compute_crate_timeline(sinex_dir)

    # Categorize crates
    categories = {}
    for crate in crates:
        if 'lib/' in crate:
            categories[crate] = 'library'
        elif 'core/' in crate:
            categories[crate] = 'core'
        elif 'nodes/' in crate:
            categories[crate] = 'node'
        elif 'cli/' in crate:
            categories[crate] = 'cli'
        elif 'xtask' in crate:
            categories[crate] = 'tooling'
        else:
            categories[crate] = 'other'

    output = {
        'totals': totals,
        'crates': crate_data,
        'timeline': timeline,
        'categories': categories,
    }

    # Console summary
    print(f"\n{'Crate':45s} {'Code':>6s} {'App':>6s} {'Test':>6s} {'T/A':>5s} "
          f"{'Fn':>4s} {'St':>4s} {'Tr':>3s} {'Unsafe':>6s}")
    for crate in sorted(crate_data.keys(), key=lambda x: -crate_data[x]['code_lines']):
        d = crate_data[crate]
        if d['code_lines'] < 10:
            continue
        print(f"  {d['name']:45s} {d['code_lines']:>6} {d['app_code_lines']:>6} "
              f"{d['test_code_lines']:>6} {d['test_to_app_ratio']:>5.2f} "
              f"{d['functions']:>4} {d['structs']:>4} {d['traits']:>3} {d['unsafe_blocks']:>6}")

    ratio = round(totals['test_code_lines'] / max(1, totals['app_code_lines']), 2)
    print(f"\n  TOTALS: {totals['code_lines']:,} code lines "
          f"({totals['app_code_lines']:,} app + {totals['test_code_lines']:,} test, "
          f"ratio={ratio}x)")
    print(f"  {totals['functions']} fns, {totals['structs']} structs, "
          f"{totals['traits']} traits, {totals['unsafe_blocks']} unsafe blocks")

    with open(out_file, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {out_file}")
