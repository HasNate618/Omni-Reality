#!/usr/bin/env bash
#
# Start the loopback worker services the Python coordinator drives.
#
#   inspect worker  :8771  (POST /inspect)  -> provider/workers/sam2_server.py
#   generation work :8772  (POST /jobs)     -> provider/workers/gen_server.py
#
# Neither model runs in this repo's own venv: that venv has no torch. The only
# interpreter that works is the omni-3d-gen one, and it needs a specific
# LD_LIBRARY_PATH or it fails in three different confusing ways:
#
#   * without /run/opengl-driver/lib   -> torch imports but reports NO cuda
#   * without the gcc-14 lib dir       -> torch.so itself fails to load
#   * without the steam-run usr/lib64  -> opencv (via rembg inside TripoSR)
#                                         dies on libxcb.so.1
#
# The nix store hashes below are pinned to the system generation they were
# verified on. If a nixos-rebuild moves them, the resolver below falls back to a
# glob and warns loudly rather than starting a worker that cannot use the GPU.
#
# Override anything with env vars: OMNI_PY, OMNI_3D_GEN, GEN_HOST/PORT,
# SAM2_HOST/PORT, SAM2_CHECKPOINT, SAM2_CONFIG, GEN_ARTIFACT_ROOT.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VENDOR="$HERE/vendor"
OMNI_3D_GEN="${OMNI_3D_GEN:-/home/nate/Projects/omni-3d-gen}"
# shellcheck disable=SC2016 # literal `$LD_LIBRARY_PATH` is the env var, not a var here
OMNI_PY="${OMNI_PY:-$OMNI_3D_GEN/provider/.venv/bin/python}"

die() { echo "run_workers: $*" >&2; exit 1; }

[ -x "$OMNI_PY" ] || die "python not executable: $OMNI_PY
  This worker needs the omni-3d-gen venv (the one in this repo has no torch)."

[ -d "$VENDOR/tsr" ] || die "vendored tsr package missing: $VENDOR/tsr
  It was previously fetched into /tmp, which a reboot destroys. See vendor/README.md."

[ -f "$VENDOR/torchmcubes.py" ] || die "vendored torchmcubes shim missing: $VENDOR/torchmcubes.py"
[ -f "$VENDOR/tripo_remap.py" ] || die "vendored tripo_remap missing: $VENDOR/tripo_remap.py"

resolve_lib() {
  # $1 pinned path, $2 fallback glob, $3 label
  if [ -e "$1" ]; then printf '%s' "$1"; return 0; fi
  local found
  found="$(ls -d $2 2>/dev/null | head -1 || true)"
  if [ -n "$found" ]; then
    echo "run_workers: WARN $3 pinned path gone after a system update; using $found" >&2
    printf '%s' "$found"
    return 0
  fi
  echo "run_workers: WARN $3 not found at all (looked for $1 and $2)" >&2
  printf '%s' ""
}

DRIVER_LIB="$(resolve_lib '/run/opengl-driver/lib' '/run/opengl-driver/lib' 'cuda driver lib')"
GCC_LIB="$(resolve_lib \
  '/nix/store/3w4ccijixck0wdchh5ab5ykyxqwkxdp5-gcc-14.3.0-lib/lib' \
  '/nix/store/*-gcc-14*-lib/lib' 'gcc-14 lib')"
ZLIB_DIR="$(resolve_lib \
  '/nix/store/2kdz3m7ic8w226pcvkz1dlg169v91p6a-zlib-1.3.2/lib' \
  '/nix/store/*-zlib-1*/lib' 'zlib')"
STEAM_LIB="$(resolve_lib \
  '/nix/store/1mjhblsalgyq8yl2wxx06rcz74y3hk5i-steam-run-1.0.0.87-fhsenv-rootfs/usr/lib64' \
  '/nix/store/*-steam-run-*/usr/lib64' 'steam-run lib64 (X libs)')"

[ -n "$DRIVER_LIB" ] || die "no CUDA driver lib; torch will report cuda unavailable"

export LD_LIBRARY_PATH="$DRIVER_LIB:$GCC_LIB:$ZLIB_DIR:$STEAM_LIB:${LD_LIBRARY_PATH:-}"
# Vendored dirs FIRST so the durable copy wins over any stale /tmp checkout.
export PYTHONPATH="$VENDOR:$OMNI_3D_GEN"

# Fail before spawning anything if the GPU is not actually usable from here.
if ! "$OMNI_PY" - <<'PY'
import sys
try:
    import torch
except Exception as exc:  # noqa: BLE001
    sys.exit(f"torch import failed: {type(exc).__name__}: {exc}")
if not torch.cuda.is_available():
    sys.exit("torch reports cuda unavailable")
PY
then
  die "preflight failed: the interpreter cannot see the GPU (see the message above)"
fi

echo "run_workers: preflight OK (GPU visible), starting both workers"

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$OMNI_PY" "$HERE/sam2_server.py" &
pids+=($!)
"$OMNI_PY" "$HERE/gen_server.py" &
pids+=($!)

echo "run_workers: inspect   -> http://${SAM2_HOST:-127.0.0.1}:${SAM2_PORT:-8771}/inspect  (pid ${pids[0]})"
echo "run_workers: generation-> http://${GEN_HOST:-127.0.0.1}:${GEN_PORT:-8772}/jobs      (pid ${pids[1]})"
echo "run_workers: Ctrl-C stops both"

# If either worker dies, tear the other one down rather than leaving a half-up pair.
wait -n
echo "run_workers: a worker exited; shutting down" >&2
exit 1
