#!/usr/bin/env bash
# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

set -eu
mkdir -p "$1"
cd "$1"
commit=$2
git init -q
# Snapshot restore changes inodes and ctimes while preserving file contents.
git config core.trustctime false
git config core.checkStat minimal
git config remote.origin.url https://github.com/llvm/llvm-project
if [[ -n $3 ]]; then
  git fetch -q --depth=1 --no-tags origin "$3"
fi
git index-pack --stdin < /ci/checkout.pack
# The pack contains the commit and its tree. It omits history.
echo "$commit" > .git/shallow
git checkout -q --detach "$commit"
