# Sinevec Integration Migration

**Date**: 2026-01-01
**Status**: Complete
**Original Repository**: [Sinity/sinevec](https://github.com/Sinity/sinevec) (now archived)

## Summary

The entire Sinevec codebase has been migrated into the Lynchpin monorepo at `lynchpin/sinevec/`, eliminating the need to maintain a separate repository for vector embeddings and semantic search functionality.

## What Was Moved

All core Sinevec functionality from `/realm/project/sinevec/src/sinevec/` to `/realm/project/sinity-lynchpin/lynchpin/sinevec/`:

- **CLI entrypoint** (`cli.py`) - Typer-based command interface
- **Embedding utilities** (`embed_utils.py`) - Voyage AI integration, token counting, chunking
- **Ingest pipelines**:
  - `ingest/chats.py` - Polylogue conversation embeddings
  - `ingest/bookmarks.py` - Raindrop bookmark embeddings
  - `ingest/knowledge_code.py` - Code repository embeddings
- **Search core** (`search_core.py`) - Qdrant client, semantic search, filtering
- **FastAPI server** (`server.py`) - Web UI for exploration
- **Test suites** - All property tests and integration tests

## Import Changes

All imports were updated from absolute `from sinevec.X` to relative `from .X` or `from lynchpin.sinevec.X` to work within the Lynchpin package namespace.

## Nix Dependencies

Added custom Python package definitions in `flake.nix` for packages not in nixpkgs:

```nix
voyageai = prev.buildPythonPackage rec {
  pname = "voyageai";
  version = "0.2.3";
  # ... with poetry-core build system
};

qdrant-client = prev.buildPythonPackage rec {
  pname = "qdrant_client";
  version = "1.11.3";
  # ... with grpcio, httpx, pydantic dependencies
};
```

Plus standard packages: `tiktoken`, `python-dotenv`, `fastapi`, `uvicorn`

All dependencies are declaratively managed in `flake.nix` without pip/virtualenv.

## CLI Usage

The CLI is now invoked via the Python module path:

```bash
# Old (separate repo)
sinevec embed-chats /path/to/chats/
sinevec search "query" --n 10
sinevec serve

# New (integrated)
python -m lynchpin.sinevec.cli embed-chats /path/to/chats/
python -m lynchpin.sinevec.cli search "query" --n 10
python -m lynchpin.sinevec.cli serve
```

## API Access

The embedding state loader remains available for programmatic access:

```python
from lynchpin import sinevec

state = sinevec.load_embedding_state()
top_files = state.top_paths(10)
```

This maintains compatibility with calendar prompt generators and warehouse loaders that reference `lynchpin.sinevec`.

## Repository Archive

The original `Sinity/sinevec` repository was archived on GitHub using:

```bash
gh repo archive Sinity/sinevec --yes
```

It remains publicly visible for historical reference but is read-only. All future development happens in `sinity-lynchpin/lynchpin/sinevec/`.

## Validation

Final validation confirmed:

- ✓ All dependencies available in Nix devshell
- ✓ CLI commands execute successfully (`--help` works for all subcommands)
- ✓ Python modules compile without import errors
- ✓ Flake checks pass (`nix flake check`)
- ✓ README updated with usage examples
- ✓ Original repository archived on GitHub

## Benefits

1. **Single source of truth** - No need to sync changes between repos
2. **Unified devshell** - All Lynchpin tools + Sinevec in one environment
3. **Consistent packaging** - Same Nix approach as other Lynchpin modules
4. **Simplified imports** - Use `from lynchpin import sinevec` across all pipelines
5. **Reduced maintenance** - One flake to update, one CI to maintain

## Next Steps

- Consider adding `just` targets for common sinevec operations (embed-latest, serve, etc.)
- Integrate sinevec search results into calendar narrative generation
- Add sinevec metrics to the warehouse DuckDB (embedding coverage, search analytics)
- Wire up periodic embedding refresh as a systemd timer via Sinnix
