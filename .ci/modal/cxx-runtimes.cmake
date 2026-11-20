# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

# ExternalProject passes the outer build's runtime list on the nested CMake
# command line. Its toolchain file can narrow that list without regenerating LLVM.
if(CMAKE_PROJECT_NAME STREQUAL "Runtimes")
  set(LLVM_ENABLE_RUNTIMES "libcxx;libcxxabi;libunwind" CACHE STRING "" FORCE)
  message(STATUS "Configuring C++ test runtimes: ${LLVM_ENABLE_RUNTIMES}")
endif()
