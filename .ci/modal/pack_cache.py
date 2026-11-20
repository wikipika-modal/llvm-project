# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
"""Archive the cache entries a compiler added or changed relative to the seed."""

import filecmp
import os
from pathlib import Path
import sys
import tarfile

upper = Path("/ram/cache-upper")
with tarfile.open(sys.argv[1], "w") as archive:
    for path in upper.rglob("*"):
        if path.is_file():
            name = path.relative_to(upper)
            entry = Path(os.environ["SCCACHE_DIR"]) / name
            original = Path.home() / "sccache" / name
            if not original.exists() or not filecmp.cmp(entry, original, shallow=False):
                archive.add(entry, arcname=name)
