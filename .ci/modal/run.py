# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
"""Run the full Linux premerge project set in Modal VM sandboxes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import modal

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
import compute_projects

# Pinned premerge Linux runner from llvm-zorg.
RUNNER = "ghcr.io/llvm/ci-ubuntu-26.04-agent:c91364399c7c"
# Generated libc++ tests include their absolute build path in xUnit identities.
CHECKOUT = "/home/gha/actions-runner/_work/llvm-project/llvm-project"
COMPILERS = 64


def test_shards(project_targets):
    # Keep small suites together, and distribute the longest suites by lit shard.
    counts = {"check-clang": 3, "check-llvm": 3, "check-lldb": 6}
    for target in project_targets:
        count = counts.get(target, 1)
        for i in range(1, count + 1):
            yield (f"{target[6:]}-{i}", "project", str(i), str(count), target)
    for family, count in (("cxx26", 10), ("cxxmod", 12)):
        for i in range(1, count + 1):
            yield (f"{family}-{i}", family, str(i), str(count), "check-cxx")
        yield (family + "-abi-unwind", family, "1", "1", "check-cxxabi", "check-unwind")


def upload_checkout(volume, run, commit, base):
    # Pack the commit object plus its tree, without traversing parent commits.
    trees = [commit + "^{tree}"]
    if (
        base
        and subprocess.run(
            ["git", "cat-file", "-e", base], stderr=subprocess.DEVNULL
        ).returncode
        == 0
    ):
        trees.append("^" + base + "^{tree}")
    objects = subprocess.check_output(["git", "rev-list", "--objects", *trees])
    with tempfile.TemporaryDirectory() as directory:
        pack = Path(directory, "checkout.pack")
        with pack.open("wb") as output:
            subprocess.run(
                ["git", "pack-objects", "--stdout"],
                input=commit.encode() + b"\n" + objects,
                check=True,
                stdout=output,
            )
        with volume.batch_upload() as batch:
            batch.put_file(pack, f"/{run}/checkout.pack")
            batch.put_file(CI / "modal/checkout.sh", f"/{run}/checkout.sh")
        print(f"Uploaded {pack.stat().st_size:,} bytes for {commit}", flush=True)


def require_files(volume, directory, names):
    # A new sandbox sees committed volume writes as of its creation.
    until = time.monotonic() + 90
    while True:
        found = {Path(e.path).name for e in volume.listdir(directory)}
        if not (missing := names - found):
            return
        if time.monotonic() >= until:
            raise RuntimeError(f"Missing from {directory}: {sorted(missing)}")
        time.sleep(1)


def pipeline(args):
    start = time.monotonic()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    app = modal.App.lookup(
        args.app, environment_name=args.environment, create_if_missing=True
    )
    volume = modal.Volume.from_name(
        args.volume, environment_name=args.environment, create_if_missing=True
    )
    run = uuid.uuid4().hex
    print(f"Run {run} ({args.mode})", flush=True)
    # Only a successful trunk publishes this pointer. A sandbox can mount
    # its own run's scratch and results, but cannot change the pointer.
    try:
        previous = json.loads(b"".join(volume.read_file("/trunk.json")))
    except FileNotFoundError:
        previous = None
    if args.mode == "pr" and previous is None:
        sys.exit("Run trunk once to publish a snapshot before running a pull request.")
    image = (
        modal.Image.from_id(previous["image"])
        if previous
        else modal.Image.from_registry(RUNNER)
    )
    bootstrap = ""
    if not previous:
        bootstrap = subprocess.check_output(
            ["git", "rev-parse", "--verify", args.bootstrap + "^{commit}"], text=True
        ).strip()
    upload_checkout(volume, run, commit, previous["commit"] if previous else bootstrap)
    mount = volume.with_mount_options(sub_path="/" + run)
    print(f"Checkout ready at {time.monotonic() - start:.1f}s", flush=True)

    def sandbox(stage, name, *arguments, snapshot=False):
        command = [f"{CHECKOUT}/.ci/modal/worker.sh", stage, name, *arguments]
        box = modal.Sandbox.create(
            *(["sleep", "infinity"] if snapshot else command),
            app=app,
            image=image,
            volumes={"/ci": mount},
            runtime="vm",
            cpu=(0.125, 64),
            memory=(1024, 262144),
            timeout=3600,
            experimental_options={"performance_sensitive": True},
            tags={"run": run, "stage": stage, "shard": name},
        )
        try:
            if stage == "seed":
                checkout = box.exec(
                    "runuser",
                    "-u",
                    "gha",
                    "--",
                    "bash",
                    "/ci/checkout.sh",
                    CHECKOUT,
                    commit,
                    bootstrap,
                )
                if checkout.wait() != 0:
                    raise RuntimeError(checkout.stderr.read())
            if snapshot:
                process = box.exec(*command, timeout=3600)
                code = process.wait()
            else:
                while (code := box.poll()) is None:
                    time.sleep(1)
            if code != 0:
                raise RuntimeError(f"{name} exited {code}. See {run}/logs/{name}.log")
            if snapshot:
                return box.snapshot_filesystem(timeout=600)
        finally:
            if box.returncode is None:
                box.terminate(wait=True)

    # A change to LLVM core selects the full project check set. C++ runtime
    # checks are the two extra configurations from monolithic-linux.sh.
    config = compute_projects.get_env_variables(["llvm/"], "Linux")
    targets = config["project_check_targets"].split()
    runtimes = config["runtimes_to_build"]
    image = sandbox(
        "seed", "seed", config["projects_to_build"], runtimes, *targets, snapshot=True
    )
    print(f"Seed snapshot ready at {time.monotonic() - start:.1f}s", flush=True)
    shards = list(test_shards(targets))
    with ThreadPoolExecutor(max_workers=COMPILERS) as pool:
        compiles = [
            pool.submit(sandbox, "compile", f"compile-{i}", str(i), str(COMPILERS))
            for i in range(COMPILERS)
        ]
        for job in compiles:
            job.result()
        require_files(
            volume, f"{run}/cache", {f"compile-{i}.tar" for i in range(COMPILERS)}
        )
        print(f"Compile complete at {time.monotonic() - start:.1f}s", flush=True)
        tests = [pool.submit(sandbox, "test", *shard) for shard in shards]
        for job in tests:
            job.result()
    require_files(volume, f"{run}/reports", {f"{s[0]}.tar.gz" for s in shards})
    print(
        f"{len(shards)} test shards complete at {time.monotonic() - start:.1f}s",
        flush=True,
    )
    if args.mode == "trunk":
        require_files(volume, f"{run}/times", {f"{s[0]}.tar.gz" for s in shards})
        packed = sandbox(
            "pack", "pack", *(f"{s[1]}/{s[0]}" for s in shards), snapshot=True
        )
        state = json.dumps({"commit": commit, "image": packed.object_id}).encode()
        with volume.batch_upload(force=True) as batch:
            batch.put_file(io.BytesIO(state), "/trunk.json")
    print(
        f"Finished in {time.monotonic() - start:.1f}s. Reports in {run}/reports.",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["trunk", "pr"])
    parser.add_argument(
        "--bootstrap",
        metavar="REV",
        default="HEAD",
        help="published LLVM revision to fetch directly on the first trunk run",
    )
    parser.add_argument("--environment", default="main")
    parser.add_argument("--app", default="llvm-premerge")
    parser.add_argument("--volume", default="llvm-premerge")
    pipeline(parser.parse_args())
