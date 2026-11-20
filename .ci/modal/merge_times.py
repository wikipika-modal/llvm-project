# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
"""Merge each shard's observations into the timing files used to order the next run."""

from pathlib import Path
import sys
import tarfile


def read_times(text):
    return dict(line.split(" ", 1)[::-1] for line in text.splitlines())


def merge(shards):
    baseline, updated = {}, {}
    for shard in shards:
        family, name = shard.split("/")
        with tarfile.open(f"/ci/times/{name}.tar.gz") as archive:
            for member in archive:
                path = Path("lit-times", family, member.name)
                if path not in baseline:
                    baseline[path] = (
                        read_times(path.read_text()) if path.exists() else {}
                    )
                    updated[path] = baseline[path].copy()
                times = read_times(archive.extractfile(member).read().decode())
                # lit rewrites the whole file, including tests run by other
                # shards. Only a time changed from the common seed is new.
                updated[path].update(
                    (test, t)
                    for test, t in times.items()
                    if baseline[path].get(test) != t
                )
    for path, times in updated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{t} {test}\n" for test, t in times.items()))


if __name__ == "__main__":
    merge(sys.argv[1:])
