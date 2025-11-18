#!/usr/bin/env bash
# combine-files-batch.sh — Non-interactive bundler that groups files into
# sources, tests, and docs and emits combined markdown reports.
set -euo pipefail
IFS=$'\n\t'

ROOT=${1:-$(pwd)}
OUTPUT_DIR=${2:-combined-bundles}
OUTPUT_FORMAT="markdown"

# Directories to exclude entirely
EXCLUDES=(
  '.git/*'
  'target/*'
  'node_modules/*'
  'result/*'
  'dist/*'
  'build/*'
  '.venv/*'
  'venv/*'
  '.sqlx/*'
  '*.lock'
  'nixos/grafana-dashboards/*'
  'docs/test-suite-report/*'
  'docs/historical/*'
  "$OUTPUT_DIR/*"
)

SKIP_PATHS=(
  'docs/misc-including-high-level-overviews-and-plans/_new_ideas_discussion.md'
  'docs/testing-gap-analysis.md'
  'docs/TEST_PATTERNS.md'
  'docs/TODO.md'
  'nixos/README.md'
  'docs/vision/emergent-insights-and-extensions.md'
  'docs/vision/project-target-state.md'
  'docs/misc-including-high-level-overviews-and-plans/EMERGENT_INSIGHTS_AND_SPECULATIVE_EXTENSIONS.md'
)

mkdir -p "$OUTPUT_DIR"

ensure_git_available() {
  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

is_text_file() {
  local mime
  mime=$(file --mime-type -b "$1")
  if [[ $mime == text/* ]]; then
    return 0
  fi
  case $mime in
    application/json|application/xml|application/x-yaml|application/yaml|application/x-sh|application/javascript|application/x-toml)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_doc_file() {
  local path=$1
  case $path in
    */docs/*|docs/*|*/doc/*|doc/*|schemas/*|*/schemas/*)
      return 0
      ;;
  esac
  local ext=${path##*.}
  case $ext in
    md|mdx|rst|txt|adoc|org|markdown|rtf)
      return 0
      ;;
  esac
  return 1
}

is_test_file() {
  local path=$1
  case $path in
    */tests/*|tests/*|*/test/*|test/*)
      return 0
      ;;
  esac
  local base=$(basename "$path")
  case $base in
    *_test.*|*_tests.*|test_*.rs|test-*.rs)
      return 0
      ;;
  esac
  return 1
}

sort_array() {
  local -n arr=$1
  if ((${#arr[@]} == 0)); then
    return
  fi
  local temp=()
  for f in "${arr[@]}"; do
    local rel=${f#"$ROOT"/}
    local priority
    priority=$(path_priority "$rel")
    temp+=("$(printf '%04d' "$priority")|$rel")
  done
  local sorted=()
  while IFS= read -r entry; do
    sorted+=("$entry")
  done < <(printf '%s\n' "${temp[@]}" | sort)
  arr=()
  for entry in "${sorted[@]}"; do
    local rel_only=${entry#*|}
    if [[ "$ROOT" == "." ]]; then
      arr+=("$rel_only")
    else
      arr+=("$ROOT/$rel_only")
    fi
  done
}

should_skip_file() {
  local file=$1
  local rel=$file
  if [[ $file == "$ROOT"* ]]; then
    rel=${file#"$ROOT"/}
  fi
  for skip in "${SKIP_PATHS[@]}"; do
    [[ -z $skip ]] && continue
    if [[ $rel == "$skip" ]]; then
      return 0
    fi
  done
  return 1
}

path_priority() {
  local rel=$1
  case $rel in
    README.md|README.MD|README|AGENTS.md|CLAUDE.md|TESTING.md)
      echo 10
      return
      ;;
  esac
  case $rel in
    docs/README.md|docs/architecture/*)
      echo 12
      return
      ;;
  esac
  case $rel in
    Cargo.toml|justfile|flake.nix|flake.lock|deny.toml|clippy.toml|.pre-commit-config.yaml|.editorconfig|.gitignore|.cargo/config.toml|.cargo-machete.toml)
      echo 15
      return
      ;;
  esac
  case $rel in
    scripts/*)
      echo 20
      return
      ;;
    cli/*)
      echo 30
      return
      ;;
    nixos/*)
      echo 40
      return
      ;;
    schemas/*)
      echo 45
      return
      ;;
    crate/lib/*)
      echo 50
      return
      ;;
    crate/core/*)
      echo 60
      return
      ;;
    crate/satellites/*)
      echo 70
      return
      ;;
    src/*)
      echo 80
      return
      ;;
    tests/*)
      echo 85
      return
      ;;
    docs/*)
      echo 25
      return
      ;;
  esac
  echo 100
}

collect_files() {
  local dir=$1
  local -a args=(rg --files "$dir" --hidden)
  if [[ ${FOLLOW_SYMLINKS:-0} -ne 0 ]]; then
    args+=(--follow)
  fi
  for ex in "${EXCLUDES[@]}"; do
    args+=( -g "!$ex" )
  done
  "${args[@]}"
}

build_bundle() {
  local category=$1
  shift
  local files=("$@")
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "Skipping $category bundle (no files)."
    return
  fi
  local outfile="$OUTPUT_DIR/combined-$category.md"
  : >"$outfile"
  local current_date total_tokens total_files
  current_date=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  total_files=${#files[@]}
  total_tokens=0
  declare -A size_map token_map
  for f in "${files[@]}"; do
    local sz tk
    sz=$(stat -c%s "$f")
    tk=$((sz / 4))
    size_map["$f"]=$sz
    token_map["$f"]=$tk
    total_tokens=$((total_tokens + tk))
  done
  {
    echo '---'
    echo "generated: $current_date"
    echo "category: $category"
    echo "base_directory: $ROOT"
    echo "file_count: $total_files"
    echo "token_estimate: $total_tokens"
    echo '---'
    echo
    echo "## Table of Contents"
    echo
    local i=1
    for f in "${files[@]}"; do
      local rel
      rel=${f#"$ROOT"/}
      echo "$i. [$rel](#${category}-file-$i)"
      ((i++))
    done
    echo
  } >>"$outfile"

  local idx=1
  for f in "${files[@]}"; do
    local rel=${f#"$ROOT"/}
    local sz=${size_map["$f"]}
    local tk=${token_map["$f"]}
    local typ=$(file -b "$f" | cut -d, -f1)
    local ext=${f##*.}
    local lang=""
    case $ext in
      rs) lang=rust ;;
      ts) lang=typescript ;;
      js) lang=javascript ;;
      py) lang=python ;;
      sh) lang=bash ;;
      nix) lang=nix ;;
      toml) lang=toml ;;
      json) lang=json ;;
      yml|yaml) lang=yaml ;;
      md|mdx) lang=markdown ;;
    esac
    {
      echo "<a id=\"${category}-file-$idx\"></a>"
      echo "## File: $rel"
      echo
      echo "- Size: $sz bytes"
      echo "- Tokens: $tk"
      echo "- Type: $typ"
      echo
      echo '```'"$lang"
      cat "$f"
      echo '```'
      echo
    } >>"$outfile"
    ((idx++))
  done
  echo "Wrote $outfile (${total_files} files)."
}

generate_tokei_plus_gitlog() {
  local outfile="$OUTPUT_DIR/tokei_plus_gitlog.md"
  : >"$outfile"
  echo "# Tokei Report" >>"$outfile"
  echo >>"$outfile"
  if command -v tokei >/dev/null 2>&1; then
    echo '```text' >>"$outfile"
    tokei "$ROOT" >>"$outfile"
    echo '```' >>"$outfile"
  else
    echo "_tokei not installed; skipping code statistics._" >>"$outfile"
  fi

  echo >>"$outfile"
  echo "# Git Log (reverse, summary + stat)" >>"$outfile"
  echo >>"$outfile"
  if ensure_git_available; then
    echo '```text' >>"$outfile"
    git -C "$ROOT" log --reverse --summary --stat >>"$outfile"
    echo '```' >>"$outfile"
  else
    echo "_Not a git repository; skipping log output._" >>"$outfile"
  fi

  echo "Wrote $outfile"
}

generate_git_diff_parts() {
  if ! ensure_git_available; then
    echo "Skipping git diff parts (not a git repository)."
    return
  fi

  local parts=8
  local total_commits
  total_commits=$(git -C "$ROOT" rev-list --count HEAD)
  if ((total_commits == 0)); then
    echo "Skipping git diff parts (no commits)."
    return
  fi

  local chunk=$(((total_commits + parts - 1) / parts))
  local part=1
  while ((part <= parts)); do
    local skip=$(((part - 1) * chunk))
    local outfile="$OUTPUT_DIR/all_diffs_part_${part}.md"
    if ((skip >= total_commits)); then
      : >"$outfile"
      ((part++))
      continue
    fi
    git -C "$ROOT" log --reverse --summary --stat -p --max-count=$chunk --skip=$skip >"$outfile"
    echo "Wrote $outfile"
    ((part++))
  done
}

main() {
  mapfile -t files < <(collect_files "$ROOT")
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No files found in $ROOT"
    exit 1
  fi
  declare -a sources_files=()
  declare -a tests_files=()
  declare -a docs_files=()
  for f in "${files[@]}"; do
    [[ -f $f ]] || continue
    # Skip generated SQLx cache artifacts regardless of location
    if [[ $f == */.sqlx/* ]]; then
      continue
    fi
    if should_skip_file "$f"; then
      continue
    fi
    if ! is_text_file "$f"; then
      continue
    fi
    if is_doc_file "$f"; then
      docs_files+=("$f")
    elif is_test_file "$f"; then
      tests_files+=("$f")
    else
      sources_files+=("$f")
    fi
  done
  sort_array sources_files
  sort_array tests_files
  sort_array docs_files
  build_bundle sources "${sources_files[@]}"
  build_bundle tests "${tests_files[@]}"
  build_bundle docs "${docs_files[@]}"
  generate_tokei_plus_gitlog
  generate_git_diff_parts
}

main "$@"
