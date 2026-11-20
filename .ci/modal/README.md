# Linux premerge on Modal

Run the full LLVM-core Linux premerge project set and both C++ runtime test
configurations from a local checkout. The first trunk run starts with LLVM's
Linux runner image. Subsequent runs start at the latest successful trunk
snapshot published to the volume.

Install the Modal Python client (`python -m pip install modal==1.6.1`) and
authenticate with `python -m modal setup`. The account must support VM
sandboxes, filesystem snapshots, volume subdirectory mounts, and the
`performance_sensitive` option. Each VM requests 0.125 CPU and 1 GiB of
memory and can grow to 64 CPUs and 262144 MiB. Run from the repository root
with the changes to test committed.

```sh
# With trunk checked out
python .ci/modal/run.py trunk

# With the pull request checked out (including the pipeline)
python .ci/modal/run.py pr
```

Pass `--environment`, `--app`, and `--volume` to either command to select
your resources. The first seed fetches the published trunk commit from GitHub.
For an unpublished trunk commit, pass `--bootstrap <published-base>` to fetch
that base and upload the local changes. Both commands use the same volume.
Run one trunk publisher at a time. On subsequent runs, the orchestrator sends
a Git pack containing the objects the snapshot lacks when its base is available
in the local checkout.

The seed configures with `.ci/monolithic-linux.sh`, builds generated inputs
and snapshots the tree. Compiler shards fill sccache in parallel. Each test
shard loads their cache entries, links its own prerequisites in a tmpfs
overlay, and runs its check target. The C++26 and Clang modules passes use
lit parameters in separate VMs. On trunk, a final VM packs the cache and
per-suite lit timings into the next snapshot.

The command prints a run directory on the volume. Its `logs` directory has a
log per worker. Its `reports` directory has a compressed tar of lit xUnit XML
per shard. The Clang Python bindings use unittest and report in their log.

## Differences from the GitHub job

The project list, check targets and CMake configuration come from
`compute_projects.py` and `monolithic-linux.sh`. The rest differs where
sharing one configured tree between VMs requires it:

- The seed builds only the generated sources that are cheap to produce.
  Compiler VMs run the outstanding Ninja compile commands directly, and each
  test VM builds what its check target still needs from the shared sccache.
- The `check-llvm` VMs build lld first. The GitHub job builds every project
  in one tree before any test runs.
- libc++ and libc++abi receive `std=c++26` and `enable_modules=clang` as lit
  parameters in separate VMs rather than through two CMake reconfigures.
  Those VMs configure only libc++, libc++abi and libunwind.
- The modules VMs set `partition_module_cache`, so tests with the same
  compiler command share a module cache.
- lit hands each worker one test at a time.
- lit's test times are merged across shards into the trunk snapshot.
- Reports stay on the volume. Nothing goes to GitHub or the premerge advisor.

## Measured run

Starting from the runner image and an empty volume, a trunk run takes about
six minutes, including publication of the packed snapshot. A pull-request run
from its parent's snapshot takes a little over two minutes. Both use 64
compiler VMs and 44 test VMs. Wall times include commit delivery and
publication of the shard reports.

A pull-request run covered all 188,285 xUnit test identities of the upstream
Linux run for the same change exactly once. Two LLDB unit tests exchanged pass
and skip statuses because they read the kernel ptrace setting. The separate
Clang Python target passed all 179 unittests.
