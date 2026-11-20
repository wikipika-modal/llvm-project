# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
"""Build the generated inputs shared by compiler shards, then divide the compiles."""

import functools
import json
from pathlib import Path
import re
import subprocess
import sys


def ninja(*args):
    return subprocess.check_output(["ninja", *args], text=True)


def generate(targets):
    # CMake collects a target's generated inputs under an order-only phony node.
    # Some of those inputs are linked programs (e.g. lldb-server) which no
    # compiler reads. Limit the part of the build that must run in the seed.
    objects = sum(
        p.endswith(".o") for p in ninja("-t", "inputs", *targets).splitlines()
    )
    waits = re.findall(
        r"^(cmake_object_order_depends_target_[^:]+):",
        ninja("-t", "targets", "all"),
        re.M,
    )
    labels, rules, inputs = {}, set(), {}
    for line in ninja("-t", "graph", *waits).splitlines():
        if edge := re.match(r'"(0x\w+)" -> "(0x\w+)"', line):
            inputs.setdefault(edge[2], []).append(edge[1])
        elif node := re.match(r'"(0x\w+)" \[label="(.*?)"(, shape=ellipse)?', line):
            labels[node[1]] = node[2]
            if node[3]:
                rules.add(node[1])

    # A bit per object keeps counting transitive prerequisites cheap even
    # when many rules depend on the same libraries.
    bits = {}

    @functools.cache
    def prerequisites(node):
        mask = 0
        if labels[node].endswith(".o"):
            mask = 1 << bits.setdefault(node, len(bits))
        for dep in inputs.get(node, ()):
            mask |= prerequisites(dep)
        return mask

    generated = set()
    for node, label in labels.items():
        if label.startswith("cmake_object_order_depends_target_"):
            for dep in inputs.get(node, ()):
                generated.update(inputs[dep] if dep in rules else (dep,))
    cheap = []
    sources = []
    for node in generated:
        path = labels[node]
        if node not in inputs or path.startswith("cmake_object_order_depends_target_"):
            continue
        if prerequisites(node).bit_count() <= objects / 15:
            cheap.append(path)
        elif Path(path).suffix in (".cpp", ".h", ".inc"):
            sources.append(path)
    subprocess.run(["ninja", *sorted(cheap)], check=True)
    # CMake also attaches order-only link dependencies to some Python and
    # CMake source generators. Run their own Ninja commands after the tools
    # above have been built. Compiler shards can then treat errors as fatal.
    if sources:
        for command in dict.fromkeys(
            ninja("-t", "commands", "-s", *sorted(sources)).splitlines()
        ):
            subprocess.run(command, shell=True, check=True)


def main(targets):
    generate(targets)
    stale = set(
        re.findall(
            r"^\[\d+/\d+\] Building \w+ object (.+)$", ninja("-n", *targets), re.M
        )
    )
    commands = {}
    for entry in json.loads(ninja("-t", "compdb")):
        output = entry["output"]
        if output in stale:
            commands[output] = entry["command"]
            Path(output).parent.mkdir(parents=True, exist_ok=True)
    if missing := stale - commands.keys():
        sys.exit(f"No Ninja compile command for {sorted(missing)}")
    with open("compile-commands", "w") as f:
        for output in sorted(commands):
            print(commands[output], file=f)
    print(f"Planned {len(commands)} compiles")


if __name__ == "__main__":
    main(sys.argv[1:])
