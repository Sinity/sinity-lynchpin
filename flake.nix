{
  description = "Local-first evidence and analysis platform for personal data exports and activity captures";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
    polylogueSrc = {
      url = "github:Sinity/polylogue/master";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      polylogueSrc,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
        # Package Python deps not in nixpkgs
        pythonPackagesOverlay = final: prev: {
          cachew = prev.buildPythonPackage rec {
            pname = "cachew";
            version = "0.22.20251013";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-1ddzhN7j5QHhZ5XEQY21LD89Zt0wxFse18Wh4UcWK2w=";
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with prev; [
              platformdirs
              sqlalchemy
              orjson
              typing-extensions
            ];
            doCheck = false;
          };

          mcp = prev.buildPythonPackage rec {
            pname = "mcp";
            version = "1.7.1";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/ae/79/fe0e20c3358997a80911af51bad927b5ea2f343ef95ab092b19c9cc48b59/mcp-1.7.1-py3-none-any.whl";
              sha256 = "0yh563yrqvzr4y3py6bm3csa4nq8rrx6qhlmhi0h6vfvfy4i1rpp";
            };
            propagatedBuildInputs = with prev; [
              anyio
              httpx
              httpx-sse
              pydantic
              starlette
              sse-starlette
              pydantic-settings
              uvicorn
              python-multipart
            ];
            doCheck = false;
          };

          sqlite-vec = prev.buildPythonPackage rec {
            pname = "sqlite_vec";
            version = "0.1.9";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/6f/ad/6afd073b0f817b3e03f9e37ad626ae341805891f23c74b5292818f49ac63/sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux1_x86_64.whl";
              sha256 = "11kpis9wiwai28ii6l4hcs5iyikxzhpyxzbmmyy7k7mlj1wp458m";
            };
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          polylogue = prev.buildPythonPackage {
            pname = "polylogue";
            version = "0.1.0";
            pyproject = true;
            src = polylogueSrc;

            postPatch = ''
              cat > polylogue/_build_info.py << 'BUILDEOF'
              from __future__ import annotations
              BUILD_COMMIT = "${polylogueSrc.rev or polylogueSrc.shortRev or "unknown"}"
              BUILD_DIRTY = False
              BUILDEOF
            '';

            build-system = with prev; [
              hatchling
            ];

            dependencies = with final; [
              google-auth-oauthlib
              google-api-python-client
              google-auth-httplib2
              httpx
              h2
              rich
              textual
              jinja2
              markdown-it-py
              pygments
              ijson
              sqlite-vec
              questionary
              click
              tenacity
              dateparser
              orjson
              structlog
              pydantic
              aiosqlite
              mcp
              pyyaml
              watchfiles
              nh3
              cryptography
              python-multipart
              typing-extensions
            ];

            doCheck = false;
            pythonImportsCheck = [
              "polylogue"
            ];
            dontCheckRuntimeDeps = true;
          };

          claude-agent-sdk = prev.buildPythonPackage rec {
            pname = "claude_agent_sdk";
            version = "0.1.48";
            pyproject = true;
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-7ilNPwKTbAuCYRn/vvz4jGdzHPjC0stxEczJf3Y0QnI=";
            };
            nativeBuildInputs = with prev; [ hatchling ];
            # mcp defined in the same overlay — must reference via final, not prev
            propagatedBuildInputs = [
              prev.anyio
              final.mcp
            ];
            doCheck = false;
          };
        };

        python = pkgs.python312.override {
          packageOverrides = pythonPackagesOverlay;
        };

        pythonEnv = python.withPackages (
          ps: with ps; [
            black
            ipython
            mypy
            types-pyyaml
            numpy
            rich
            scipy
            hmmlearn
            tqdm
            duckdb
            cachew
            lz4
            tiktoken
            typer
            claude-agent-sdk
            mcp
            polars
            polylogue
            tree-sitter
            tree-sitter-python
            tree-sitter-rust
            pytest
          ]
        );

        defaultDevPackages =
          (with pkgs; [
            pythonEnv
            duckdb
            sqlite
            git
            gh
            jq
            yq
            ripgrep
            fd
            just
            py-spy
            ruff
            tokei
            haskellPackages.arbtt
          ])
          ++ (with pkgs; [
            gnumake
          ]);
        heavyAnalysisPackages = with pkgs; [
          cargo
          semgrep
          pip-audit
          cargo-machete
          cargo-geiger
          cargo-audit
        ];
        baseShellHook = profileName: ''
          export LYNCHPIN_DEV_PROFILE="${profileName}"
          export NIX_CONFIG="max-jobs = ''${LYNCHPIN_NIX_MAX_JOBS:-2}
          cores = ''${LYNCHPIN_NIX_CORES:-4}
          fallback = true"
          export PYTHONUSERBASE=$PWD/.pyuser
          if [ -d /realm/project/polylogue/polylogue ]; then
            export PYTHONPATH=/realm/project/polylogue:$PWD''${PYTHONPATH:+:$PYTHONPATH}
          else
            export PYTHONPATH=$PWD''${PYTHONPATH:+:$PYTHONPATH}
          fi
        '';
        mkLynchpinShell =
          profileName: packages:
          pkgs.mkShell {
            name = "sinity-lynchpin-${profileName}";
            inherit packages;

            shellHook =
              (baseShellHook profileName)
              + ''
                export LYNCHPIN_DEV_PYTHON="${pythonEnv.pythonVersion}"
                if [ "''${LYNCHPIN_MOTD_PRINTED:-0}" != "1" ] && [ -x "$PWD/tool/devshell-motd" ] && { [ -n "''${DIRENV_DIR:-}" ] || [ -t 1 ]; }; then
                  "$PWD/tool/devshell-motd" >&2
                fi
                export LYNCHPIN_MOTD_PRINTED=1
              '';
          };
        lynchpinPackage = python.pkgs.buildPythonPackage {
          pname = "lynchpin";
          version = "0.1.0";
          pyproject = true;
          src = self;

          build-system = with python.pkgs; [
            setuptools
          ];

          dependencies = with python.pkgs; [
            cachew
            claude-agent-sdk
            duckdb
            hmmlearn
            mcp
            numpy
            polars
            polylogue
            pyyaml
            rich
            scipy
            tiktoken
            typer
            lz4
            tree-sitter
            tree-sitter-python
            tree-sitter-rust
          ];

          pythonImportsCheck = [
            "lynchpin"
            "lynchpin.cli.current_state"
          ];
          doCheck = false;
          dontCheckRuntimeDeps = true;

          meta = {
            description = "Lynchpin analysis/control-plane Python package";
            mainProgram = "python";
          };
        };

        lynchpinApiPython = python.withPackages (_: [ lynchpinPackage ]);
      in
      {
        formatter = pkgs.nixfmt-rfc-style;

        packages = {
          default = lynchpinPackage;
          lynchpin = lynchpinPackage;
          api-python = lynchpinApiPython;
        };

        devShells = {
          default = mkLynchpinShell "default" defaultDevPackages;
          heavy = mkLynchpinShell "heavy" (defaultDevPackages ++ heavyAnalysisPackages);
        };
      }
    );
}
