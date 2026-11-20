# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import ctypes
import os
from pathlib import Path
import sys

# Reap orphaned test processes in the VM. LLDB's host tests also expect
# their runner to lead a session, and BOLT's sleep loop needs normal timer slack.
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER)")
Path("/proc/self/timerslack_ns").write_text("50000")
child = os.fork()
if child == 0:
    os.setsid()
    os.execvp(sys.argv[1], sys.argv[1:])
while True:
    pid, status = os.wait()
    if pid == child:
        sys.exit(os.waitstatus_to_exitcode(status) % 256)
