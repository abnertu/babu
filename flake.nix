{
  description = "Babu durable agent harness";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    {
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      systems = [ "x86_64-linux" ];
      forAllSystems = lib.genAttrs systems;
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python313; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            overlay
          ]
        )
      );
    in
    {
      packages = forAllSystems (
        system:
        let
          environment = pythonSets.${system}.mkVirtualEnv "babu" workspace.deps.default;
        in
        {
          default = environment;
          babu = environment;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          environment = pythonSets.${system}.mkVirtualEnv "babu-check" workspace.deps.all;
          source = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./src
              ./tests
              ./config.example.toml
              ./pyproject.toml
            ];
          };
        in
        {
          application = pkgs.runCommand "babu-check" { nativeBuildInputs = [ environment ]; } ''
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            cp -R ${source} "$TMPDIR/source"
            chmod -R u+w "$TMPDIR/source"
            cd "$TMPDIR/source"
            pytest -q
            ruff check src tests
            mypy src
            babu check-config --config config.example.toml
            touch "$out"
          '';
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          editableOverlay = workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; };
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          environment = pythonSet.mkVirtualEnv "babu-dev" workspace.deps.all;
        in
        {
          default = pkgs.mkShell {
            packages = [
              environment
              pkgs.uv
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
        }
      );
    };
}
