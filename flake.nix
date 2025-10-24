{
  description = "Longitudinal activity analysis workspace for Sinity";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        pythonEnv = pkgs.python312.withPackages (ps:
          with ps; [
            black
            ipykernel
            ipython
            jupyterlab
            matplotlib
            numpy
            pandas
            polars
            pyarrow
            pygwalker
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
            plotly
            beautifulsoup4
            lxml
            networkx
            openpyxl
          ]);

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
        formatter.default = pkgs.nixfmt-rfc-style;

        devShells.default = pkgs.mkShell {
          name = "sinity-analysis";
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
            midnight-commander
          ];

          shellHook = ''
            export PYTHONBREAKPOINT=ipdb.set_trace
            export PYTHONUSERBASE=$PWD/.pyuser
            export JUPYTER_PATH=$PWD/.jupyter
            export R_LIBS_USER=$PWD/.rlib
            echo "Loaded sinity-analysis devshell with Python ${pythonEnv.pythonVersion} and R support."
          '';
        };
      });
}
