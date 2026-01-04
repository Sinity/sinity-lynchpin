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
        fetchFromGitHub =
          if pkgs ? fetchFromGitHub then
            pkgs.fetchFromGitHub
          else
            (args:
              let
                hash = args.hash or args.sha256;
              in
              pkgs.fetchgit {
                url = "https://github.com/${args.owner}/${args.repo}.git";
                rev = args.rev;
                inherit hash;
              });

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

          kompress = prev.buildPythonPackage rec {
            pname = "kompress";
            version = "0.2.20240918";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-FVMd5PY9VOUnZ9Lz8Ubh8WOPJIGQWg9ZeiUnGOURgXU=";
            };
            nativeBuildInputs = with prev; [
              setuptools
              setuptools-scm
            ];
            propagatedBuildInputs = with prev; [
              typing-extensions
            ];
            doCheck = false;
          };

          lunardate = prev.buildPythonPackage rec {
            pname = "lunardate";
            version = "0.2.2";
            format = "setuptools";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-j/jAFyG+93EPBxKjArHLfQuLP/xypjqaBWzQHljlSFo=";
            };
            doCheck = false;
          };

          pyluach = prev.buildPythonPackage rec {
            pname = "pyluach";
            version = "2.3.0";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-7G4wZp0d9QycoWBIbaRKgZW7THpdPVM5kNDFsDrM0oE=";
            };
            nativeBuildInputs = with prev; [
              flit-core
            ];
            doCheck = false;
          };

          workalendar = prev.buildPythonPackage rec {
            pname = "workalendar";
            version = "17.0.0";
            format = "setuptools";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-uC1gJK7UUlBbAbrwbb6NYwmjE1/x053uB8MbIezoU7Q=";
            };
            propagatedBuildInputs = with final; [
              python-dateutil
              convertdate
              lunardate
              pyluach
            ];
            doCheck = false;
          };

          sqlite-backup = prev.buildPythonPackage rec {
            pname = "sqlite_backup";
            version = "0.1.7";
            format = "setuptools";
            src = prev.fetchPypi {
              pname = "sqlite_backup";
              inherit version;
              sha256 = "sha256-gSY5eP5a5dDzie7yl21wmOz3K/uAcC3ErSxZ1TJl7Ww=";
            };
            postPatch = ''
              if [ ! -f requirements.txt ]; then
                cat > requirements.txt <<'EOF'
click>=8.0
logzero
EOF
              fi
            '';
            propagatedBuildInputs = with final; [
              click
              logzero
            ];
            doCheck = false;
          };

          browserexport = prev.buildPythonPackage rec {
            pname = "browserexport";
            version = "0.4.3";
            format = "setuptools";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-WfzHbsMMBPB9ui5eby7zc1T7H9wdSOwSBiAXKt9yTWo=";
            };
            propagatedBuildInputs = [
              final.click
              final.kompress
              final.logzero
              final."sqlite-backup"
            ];
            doCheck = false;
          };

          python-tcxparser = prev.buildPythonPackage rec {
            pname = "python-tcxparser";
            version = "2.4.0";
            format = "setuptools";
            src = prev.fetchPypi {
              pname = "python_tcxparser";
              inherit version;
              sha256 = "sha256-9heAhnnFQa8wT2RMUMyR9xEvrcj6jatQfcBuy5/wUPk=";
            };
            propagatedBuildInputs = with final; [
              lxml
              python-dateutil
            ];
            doCheck = false;
          };

          google-takeout-parser = prev.buildPythonPackage rec {
            pname = "google-takeout-parser";
            version = "0.1.13";
            format = "setuptools";
            src = prev.fetchPypi {
              pname = "google_takeout_parser";
              inherit version;
              sha256 = "sha256-BI/e+xEy9oIqui09xoCghPOe/7oSDjUoR85a8YhSkvk=";
            };
            propagatedBuildInputs = with final; [
              ipython
              beautifulsoup4
              cachew
              click
              logzero
              lxml
              platformdirs
              pytz
            ];
            doCheck = false;
          };

          pushshift-comment-export = prev.buildPythonPackage rec {
            pname = "pushshift_comment_export";
            version = "0.1.4";
            format = "setuptools";
            src = prev.fetchPypi {
              pname = "pushshift_comment_export";
              inherit version;
              sha256 = "sha256-OIVSC1ddO4T6AcvTxZ8zTFOTOQTUZnAlUfK+YaL1IqY=";
            };
            postPatch = ''
              if [ ! -f requirements.txt ]; then
                cat > requirements.txt <<'EOF'
logzero
backoff
requests
click
EOF
              fi
            '';
            propagatedBuildInputs = with final; [
              logzero
              backoff
              requests
              click
            ];
            doCheck = false;
          };

          python-xlib = prev.buildPythonPackage rec {
            pname = "python-xlib";
            version = "0.33";
            format = "setuptools";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-Va95BqLHXObLKApYR3YIBgJET3WBWnr/TSh7stcBizI=";
            };
            nativeBuildInputs = with prev; [
              setuptools-scm
            ];
            propagatedBuildInputs = with final; [
              six
            ];
            doCheck = false;
          };

          fbchat = prev.buildPythonPackage rec {
            pname = "fbchat";
            version = "1.9.7";
            format = "pyproject";
            src = prev.fetchPypi {
              inherit pname version;
              sha256 = "sha256-pcgfIYI80YXetgfWeu9Aw1RJ8UB95W32bRAYYoUYQwc=";
            };
            nativeBuildInputs = with prev; [
              flit
              pythonRelaxDepsHook
            ];
            pythonRelaxDeps = [
              "aenum"
            ];
            propagatedBuildInputs = with final; [
              aenum
              attrs
              requests
              beautifulsoup4
              paho-mqtt
            ];
            doCheck = false;
          };

          endoapi = prev.buildPythonPackage rec {
            pname = "endoapi";
            version = "1.0.0";
            format = "setuptools";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "endoapi";
              rev = "master";
              hash = "sha256-DfzprNv5Glrs6Xz3TGhZWw2k/0NN+zXkvyRBMvPv3Qg=";
            };
            propagatedBuildInputs = with final; [
              requests
              pytz
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          fbmessengerexport = prev.buildPythonPackage rec {
            pname = "fbmessengerexport";
            version = "0.0.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "fbmessengerexport";
              rev = "7a9b4828994a75e4b84da0e7a6533136e00807bf";
              hash = "sha256-ko0N6dhxI9b4Yd7JIjbItwEsRHd8dQHibn8gnffSmo4=";
              fetchSubmodules = true;
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with final; [
              fbchat
              backoff
              orjson
              colorlog
              ijson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          ghexport = prev.buildPythonPackage rec {
            pname = "ghexport";
            version = "0.0.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "ghexport";
              rev = "d61e3af7d69e61669753125735a51224acb1955d";
              hash = "sha256-rmX0bO2sQUGsfzuTSNOZdhlqb/QarVEScVnPgcW51SU=";
              fetchSubmodules = true;
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with final; [
              PyGithub
              orjson
              colorlog
              ijson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          rexport = prev.buildPythonPackage rec {
            pname = "rexport";
            version = "0.0.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "rexport";
              rev = "6c74362783df48d5482f0de0499efe2b39144b52";
              hash = "sha256-j3L7NnMlboxnudazcpl3axBTf1Bbn6wxwTqQh46YFDk=";
              fetchSubmodules = true;
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with final; [
              praw
              orjson
              colorlog
              ijson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          goodrexport = prev.buildPythonPackage rec {
            pname = "goodrexport";
            version = "0.0.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "goodrexport";
              rev = "da9dca6f4ef4878a434b6d41bed3e4a262ac5b11";
              hash = "sha256-JziAHrehpuVRO2d1cwHzBYyEpFi0hW3uAkrfCda5J+8=";
              fetchSubmodules = true;
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with final; [
              lxml
              orjson
              colorlog
              ijson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          endoexport = prev.buildPythonPackage rec {
            pname = "endoexport";
            version = "0.0.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "karlicoss";
              repo = "endoexport";
              rev = "bfb7bc43edc0c7e7d6913c8b57879f098d15fab7";
              hash = "sha256-6wF/BdcaCwOArCVPijdtbfZq7uFNfDjwY5/96w1L318=";
              fetchSubmodules = true;
            };
            nativeBuildInputs = with prev; [
              hatchling
              hatch-vcs
            ];
            propagatedBuildInputs = with final; [
              endoapi
              orjson
              colorlog
              ijson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          activitywatch = prev.buildPythonPackage rec {
            pname = "activitywatch";
            version = "0.0.0";
            format = "setuptools";
            src = fetchFromGitHub {
              owner = "hpi";
              repo = "activitywatch";
              rev = "main";
              hash = "sha256-KFBHmwkGgYJT7KKq8nwRvZFW/jgkm2ofNIykAO5++U8=";
            };
            SETUPTOOLS_SCM_PRETEND_VERSION = version;
            nativeBuildInputs = with prev; [
              setuptools-scm
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          taskwarrior = prev.buildPythonPackage rec {
            pname = "taskwarrior";
            version = "0.0.0";
            format = "setuptools";
            src = fetchFromGitHub {
              owner = "hpi";
              repo = "taskwarrior";
              rev = "master";
              hash = "sha256-MDlXp5CxKV4IJIyVGJ+rj+MxBKXe+9xF5yeOU6HiXqc=";
            };
            SETUPTOOLS_SCM_PRETEND_VERSION = version;
            nativeBuildInputs = with prev; [
              setuptools-scm
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          aw-watcher-window = prev.buildPythonPackage rec {
            pname = "window_watcher";
            version = "0.2.0";
            format = "setuptools";
            src = fetchFromGitHub {
              owner = "purarue";
              repo = "aw-watcher-window";
              rev = "master";
              hash = "sha256-XOOy3HdH/M9bl66dz0SkYN+uXpwYBgufYdrCHZRVTMo=";
            };
            propagatedBuildInputs = [
              final.logzero
              final."python-xlib"
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
          };

          active_window = prev.buildPythonPackage rec {
            pname = "active_window";
            version = "0.1.0";
            format = "pyproject";
            src = fetchFromGitHub {
              owner = "purarue";
              repo = "active_window";
              rev = "main";
              hash = "sha256-wT7vz7ONhGETPuT31pe3jI2MmU6Vnz+2be/Tdv5oTbg=";
            };
            nativeBuildInputs = with prev; [
              setuptools
            ];
            postPatch = ''
              substituteInPlace pyproject.toml \
                --replace 'license = "MIT"' 'license = { text = "MIT" }'
              sed -i '/license-files/d' pyproject.toml
            '';
            propagatedBuildInputs = with final; [
              click
              more-itertools
              simplejson
            ];
            doCheck = false;
            dontCheckRuntimeDeps = true;
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
            click
            cachew
            plotly
            beautifulsoup4
            decorator
            lxml
            networkx
            openpyxl
            pillow
            dulwich
            more-itertools
            kompress
            platformdirs
            typing-extensions
            gitpython
            workalendar
            orgparse
            geopy
            ijson
            python-magic
            dateparser
            timezonefinder
            pytz
            python-dateutil
            convertdate
            lunardate
            pyluach
            logzero
            backoff
            colorlog
            orjson
            simplejson
            six
            aenum
            attrs
            paho-mqtt
            python-xlib
            fbchat
            browserexport
            browser-cookie3
            sqlite-backup
            python-tcxparser
            google-takeout-parser
            pushshift-comment-export
            endoapi
            endoexport
            ghexport
            rexport
            goodrexport
            fbmessengerexport
            activitywatch
            taskwarrior
            aw-watcher-window
            active_window
            PyGithub
            praw
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
            gnome-keyring
          ];

          shellHook = ''
            export PYTHONBREAKPOINT=ipdb.set_trace
            export PYTHONUSERBASE=$PWD/.pyuser
            export JUPYTER_PATH=$PWD/.jupyter
            export R_LIBS_USER=$PWD/.rlib
            export MY_CONFIG=$PWD/config
            export PYTHONPATH=$PWD:$PWD/external/hpi:$PWD/external/hpi-madelinecameron:$PWD/external/hpi-purarue:$PWD/external/hpi-sinity''${PYTHONPATH:+:$PYTHONPATH}
            export PATH=$PWD/.bin:$PATH
            if command -v fd >/dev/null && ! command -v fdfind >/dev/null; then
              mkdir -p "$PWD/.bin"
              ln -sf "$(command -v fd)" "$PWD/.bin/fdfind"
            fi
            echo "Loaded sinity-lynchpin devshell with Python ${pythonEnv.pythonVersion} and R support."
          '';
        };
      }
    );
}
