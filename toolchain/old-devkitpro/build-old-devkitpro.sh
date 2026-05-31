#!/usr/bin/env bash
set -euo pipefail

target="${1:-.}"
image="${NETSLUG_OLD_DEVKITPRO_IMAGE:-netslug-old-devkitpro:r27-libogc-1.8.12}"
make_target="${NETSLUG_MAKE_TARGET:-release}"

tool_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${tool_root}/../.." && pwd)"
target_path="$(cd "${repo_root}/${target}" && pwd)"

docker build -f "${tool_root}/Dockerfile" -t "${image}" "${tool_root}"
docker run --rm -v "${target_path}:/work" -w /work "${image}" make "${make_target}"
