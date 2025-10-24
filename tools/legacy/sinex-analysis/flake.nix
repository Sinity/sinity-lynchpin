{
  description = "Sinex Analysis Toolkit";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        
        pythonEnv = pkgs.python312.withPackages (ps: with ps; [
          pandas
          matplotlib
          seaborn
          jupyter
          livereload
          flask
          sqlite-utils
          click
          rich
          plotly
          httpx
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Data gathering
            tokei
            git
            jq
            ripgrep
            fd
            
            # Database
            sqlite
            sqlite-utils
            datasette  # Instant web UI for SQLite
            
            # Analysis
            pythonEnv
            rPackages.tidyverse
            gnuplot
            
            # Rust analysis tools
            rust-analyzer
            cargo
            rustc
            
            # Development
            just
            watchexec
            nodePackages.live-server
            
            # Visualization
            vega-lite
            graphviz
            
            # Process management
            tmux
          ];
          
          shellHook = ''
            echo "🔬 Sinex Analysis Environment"
            echo ""
            
            # Initialize database if it doesn't exist
            if [ ! -f metrics.db ]; then
              echo "📊 Initializing database..."
              just init-db
            fi
            
            # Start the web server in background using tmux
            if ! tmux has-session -t sinex-analysis 2>/dev/null; then
              echo "🚀 Starting analysis server in background..."
              tmux new-session -d -s sinex-analysis -c "$PWD" "python3 serve.py"
              echo "✅ Server running at http://localhost:8080"
              echo ""
              echo "Server management:"
              echo "  tmux attach -t sinex-analysis  # View server logs"
              echo "  tmux kill-session -t sinex-analysis  # Stop server"
            else
              echo "✅ Server already running at http://localhost:8080"
            fi
            
            echo ""
            echo "Key commands:"
            echo "  just gather-loc  - Collect LOC metrics from sinex"
            echo "  just explore     - Launch datasette UI"
            echo "  just plot-loc    - Generate growth chart"
            echo "  just query 'SQL' - Run SQL queries"
            echo ""
            echo "Open http://localhost:8080 in your browser"
          '';
        };
      });
}
