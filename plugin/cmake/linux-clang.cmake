# SPDX-License-Identifier: GPL-3.0-only
# Linux with clang, lld and the LLVM binutils: the slot objects are LTO bitcode (plugin/cmake/slots.cmake), which
# GNU ar and nm cannot index or read. tools/plugin_pack.sh linux passes this file; FRP_LLVM_SUFFIX picks a versioned
# install (clang-18 ...). The C++ runtime is linked in (plugin/CMakeLists.txt), so a bundle needs only glibc.
set(_s "${FRP_LLVM_SUFFIX}")
set(CMAKE_C_COMPILER   "clang${_s}")
set(CMAKE_CXX_COMPILER "clang++${_s}")
set(CMAKE_ASM_COMPILER "clang${_s}")
set(CMAKE_AR     "llvm-ar${_s}" CACHE FILEPATH "")
set(CMAKE_RANLIB "llvm-ranlib${_s}" CACHE FILEPATH "")
set(CMAKE_NM     "llvm-nm${_s}" CACHE FILEPATH "")
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_TRY_COMPILE_PLATFORM_VARIABLES FRP_LLVM_SUFFIX)
