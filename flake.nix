{
  description = "Longitudinal activity analysis workspace for Sinity";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
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
          voyageai = prev.buildPythonPackage rec {
            pname = "voyageai";
            version = "0.2.3";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-KDIqp6ZM2qd0vm/PPk/WoIaU6iWs1frdHv8bjvjatoo=";
            };
            nativeBuildInputs = with prev; [ poetry-core ];
            propagatedBuildInputs = with prev; [
              requests
              aiohttp
              aiolimiter
              numpy
              tenacity
            ];
            doCheck = false;
          };

          qdrant-client = prev.buildPythonPackage rec {
            pname = "qdrant_client";
            version = "1.11.3";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-WhVdgoGiJKwYrO9RLq4vXpoJB5ddUqdifsZvplhtAoU=";
            };
            nativeBuildInputs = with prev; [ poetry-core ];
            propagatedBuildInputs = with prev; [
              httpx
              pydantic
              grpcio
              grpcio-tools
              numpy
              portalocker
              urllib3
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
            ipykernel
            ipython
            jupyterlab
            matplotlib
            numpy
            pandas
            polars
            pyarrow
            pyspark
            requests
            rich
            scikit-learn
            scipy
            seaborn
            statsmodels
            tqdm
            typer
            duckdb
            datasette
            cachetools
            plotly
            beautifulsoup4
            lxml
            networkx
            openpyxl
            dulwich
            # Sinevec dependencies
            voyageai
            qdrant-client
            tiktoken
            python-dotenv
            fastapi
            uvicorn
          ]
        );

        rEnv = pkgs.rWrapper.override {
          packages = with pkgs.rPackages; [
            tidyverse
            data_table
            arrow
            duckdb
            janitor
            lubridate
            remotes
          ];
        };
      in
      {
        formatter = pkgs.nixfmt-rfc-style;

        devShells.default = pkgs.mkShell {
          name = "sinity-lynchpin";
          packages = with pkgs; [
            pythonEnv
            rEnv
            duckdb
            sqlite
            git
            gh
            jq
            yq
            ripgrep
            fd
            just
            nodejs_22
            cargo
            go
            rustup
            gnumake
            cmake
            graphviz
            imagemagick
            ffmpeg
            deno
            pandoc
            uv
            pre-commit
            gnuplot
          ];

          shellHook = ''
            export PYTHONBREAKPOINT=ipdb.set_trace
            export PYTHONUSERBASE=$PWD/.pyuser
            export JUPYTER_PATH=$PWD/.jupyter
            export R_LIBS_USER=$PWD/.rlib
            echo "Loaded sinity-lynchpin devshell with Python ${pythonEnv.pythonVersion} and R support."
          '';
        };
      }
    );
}
