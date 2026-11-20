#!/usr/bin/env bash
# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

set -euo pipefail
cd "$(dirname "$0")/../.."
stage=$1
name=$2
shift 2

if [[ $(id -u) == 0 ]]; then
  install -d -o gha -g gha /ci/{logs,cache,reports,times}
  if [[ $stage == test || $stage == compile ]]; then
    # Build output on tmpfs lets the VM grow with the working set. Buffered
    # disk writes alone leave a small guest throttled on dirty pages.
    mkdir -p /ram
    mount -t tmpfs -o size=100G tmpfs /ram
    mkdir /ram/{upper,work}
    chown --reference=build /ram/upper
    mount -t overlay overlay -o "lowerdir=$PWD/build,upperdir=/ram/upper,workdir=/ram/work" build
  fi
  if [[ $stage == compile ]]; then
    # Keep the seed cache available for comparison. sccache touches a cache
    # hit and can update a preprocessor entry without changing its name.
    mkdir /ram/{cache,cache-upper,cache-work}
    chown gha:gha /ram/cache-upper
    mount -t overlay overlay -o "lowerdir=/home/gha/sccache,upperdir=/ram/cache-upper,workdir=/ram/cache-work" /ram/cache
  fi
  # Give the runner user the image's tools, without the VM's injected Python
  # search path and thread limits.
  exec python3 -I .ci/modal/session.py runuser -u gha -- env -i \
    HOME=/home/gha USER=gha PATH="$PATH" bash "$0" "$stage" "$name" "$@" \
    > "/ci/logs/$name.log" 2>&1
fi

export CC=/opt/llvm/bin/clang CXX=/opt/llvm/bin/clang++
export SCCACHE_DIR="$HOME/sccache" SCCACHE_CACHE_SIZE=300G SCCACHE_IDLE_TIMEOUT=0
if [[ $stage == compile ]]; then export SCCACHE_DIR=/ram/cache; fi
mkdir -p "$SCCACHE_DIR"
load_cache() {
  printf '%s\n' /ci/cache/*.tar | xargs -P 32 -I '{}' tar xf '{}' -C "$SCCACHE_DIR"
}

if [[ $stage != pack ]]; then sccache --start-server; fi

case "$stage" in
seed)
  projects=$1 runtimes=$2
  shift 2
  if [[ -f build/build.ninja ]]; then
    ninja -C build build.ninja
  else
    .ci/monolithic-linux.sh "$projects" "" "$runtimes" "" ""
  fi
  (cd build && python3 ../.ci/modal/plan.py "$@")
  sccache --stop-server
  ;;
compile)
  index=$1 count=$2
  cd build
  awk -v i="$index" -v n="$count" '(NR-1) % n == i' compile-commands |
    xargs -r -d '\n' -n 1 -P "$(nproc)" sh -c
  sccache --stop-server
  python3 ../.ci/modal/pack_cache.py "/ci/cache/$name.tar"
  ;;
test)
  family=$1 index=$2 count=$3
  shift 3
  load_cache
  # Each worker can pick up the next long test as soon as it finishes one.
  export LIT_BATCH_SIZE=1
  opts="--num-shards $count --run-shard $index"
  if [[ $family != project ]]; then
    export CMAKE_TOOLCHAIN_FILE="$PWD/.ci/modal/cxx-runtimes.cmake"
    if [[ $family == cxx26 ]]; then
      opts+=" --param std=c++26"
    else
      opts+=" --param enable_modules=clang"
      if [[ $1 == check-cxx ]]; then opts+=" --param partition_module_cache=true"; fi
    fi
  fi
  if [[ $1 == check-llvm ]]; then
    # The monolithic build supplies lld to LLVM's linker-dependent tests.
    ninja -C build lld
  fi
  if [[ -d "lit-times/$family" ]]; then cp -a "lit-times/$family/." build/; fi
  mkdir /tmp/reports
  status=0
  shopt -s nullglob
  for target in "$@"; do
    # Upstream sets the lit parameters for libc++ and libc++abi only.
    if [[ $target == check-unwind ]]; then opts=""; fi
    LIT_OPTS="$opts" ninja -C build -k 0 "$target" || status=$?
    # The Python bindings use unittest. Every lit target must produce XML.
    results=(build/test-results*.xml)
    if (( ${#results[@]} )); then
      mv "${results[@]}" /tmp/reports/
    elif [[ $target != check-clang-python ]]; then
      echo "Missing xUnit results for $target" >&2
      status=1
    fi
  done
  tar czf "/ci/reports/$name.tar.gz" -C /tmp/reports .
  (cd build && find . -name .lit_test_times.txt -print0 |
    tar czf "/ci/times/$name.tar.gz" --null -T -)
  if (( status )); then
    python3 .ci/generate_test_report_github.py "$status" /tmp/reports/*.xml "/ci/logs/$name.log" > "/ci/reports/$name.md"
    exit "$status"
  fi
  ;;
pack)
  load_cache
  python3 .ci/modal/merge_times.py "$@"
  ;;
esac
