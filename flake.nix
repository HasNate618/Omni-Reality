{
  description = "omni Quest 3S Unity smoke test (FHS shell + adb + Unity CLI guidance)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

      # Unity CLI (experimental, per https://docs.unity.com/en-us/unity-cli/use-unity-cli).
      # Pinned standalone binary, shell-scoped: nothing lands in ~/.local/bin
      # or the system profile. Bump version+sha256 to update.
      unityCli = pkgs.stdenv.mkDerivation {
        pname = "unity-cli";
        version = "1.0.0-beta.10";
        src = pkgs.fetchurl {
          url = "https://public-cdn.cloud.unity3d.com/hub/prod/cli/1.0.0-beta.10/unity-linux-x64";
          sha256 = "sha256-EKUUZADAktoGeDJ+Fn7cf327NU8bP9dqVTWZDe4SXac=";
        };
        dontUnpack = true;
        installPhase = ''
          mkdir -p $out/bin
          cp $src $out/bin/unity
          chmod +x $out/bin/unity
        '';
      };

      # FHS env so Hub-installed Unity Editors (impure downloads under
      # ~/Unity/Hub/Editor) can find the shared libs they hardcode.
      # Unity Hub itself runs fine from nixpkgs; the *Editor* binary run
      # directly dies on e.g. libxml2.so.2 without this.
      unityFhs = pkgs.buildFHSEnv {
        name = "omni-unity-fhs";
        targetPkgs = pkgs_: with pkgs_; [
          unityhub
          unityCli
          android-tools
          usbutils

          # Unity Editor runtime libs (Hub + Editor + Android module installer)
          # NOTE: Unity 6000.6 wants legacy libxml2.so.2; nixpkgs' default
          # libxml2 is now 2.15 (SONAME .so.16), so pin libxml2_13 too.
          libxml2
          libxml2_13
          sqlite
          icu
          ncurses
          zlib
          glib
          gtk3
          gdk-pixbuf
          cairo
          pango
          atk
          at-spi2-atk
          nss
          nspr
          alsa-lib
          libpulseaudio
          mesa
          libglvnd
          fontconfig
          freetype
          expat
          dbus
          udev
          openssl
          curl
          stdenv.cc.cc.lib

          libx11
          libxcomposite
          libxdamage
          libxext
          libxfixes
          libxrandr
          libxcb
          libxscrnsaver
          libxtst
          libxi
          libxcursor
          libxrender
        ];
        multiPkgs = pkgs_: [ pkgs_.zlib ];
        runScript = "bash";
        profile = ''
          export SHELL=${pkgs.bash}/bin/bash
          export UNITY_EDITOR_6000="$HOME/Unity/Hub/Editor/6000.6.2f1/Editor/Unity"
          # The FHS ld.so.cache misses some libs (e.g. libtinfo.so.6, which the
          # UnityShaderCompiler needs). Belt-and-suspenders via LD_LIBRARY_PATH.
          export LD_LIBRARY_PATH="${pkgs.ncurses.out}/lib"''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
          echo "=== omni Unity FHS shell ==="
          echo "adb: $(adb --version 2>/dev/null | head -n 1)"
          adb devices -l 2>/dev/null || true
          echo ""
          if command -v unity >/dev/null 2>&1; then
            echo "unity CLI: $(unity --version 2>/dev/null | head -n 1) (from this flake, shell-scoped)"
          else
            echo "unity CLI: MISSING from shell (bump the pinned unityCli in flake.nix)."
          fi
          echo ""
          if [ -d "$HOME/Unity/Hub/Editor/6000.6.2f1/Editor/Data/PlaybackEngines/AndroidPlayer" ]; then
            echo "AndroidPlayer module: PRESENT for 6000.6.2f1"
          else
            echo "AndroidPlayer module: MISSING for 6000.6.2f1"
            echo "  Next (needs unity CLI + sign-in 'unity auth login'):"
            echo "    unity install-modules -e 6000.6.2f1 -m android --cm"
            echo "  (Hub-installed Editors only; yours qualifies.)"
          fi
          echo ""
          echo "Editor direct launch test: \$UNITY_EDITOR_6000 -version"
          echo "If that prints a version, CLI batchmode builds will work from this shell."
          echo "Quest deploy later: adb install <game>.apk, launch via Library > Unknown Sources."
        '';
      };
    in
    {
      devShells.${system}.default = unityFhs.env;
      # Same FHS env as a runnable package so batch commands work too:
      # nix run .# -- -c 'Unity -version; adb devices'
      # (nix develop --command does not forward into FHS shells.)
      packages.${system}.default = unityFhs;
    };
}
