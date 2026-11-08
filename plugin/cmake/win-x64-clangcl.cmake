# SPDX-License-Identifier: GPL-3.0-only
# Windows x64 from macOS (or Linux): clang-cl + lld-link (LLVM 20, Homebrew llvm@20 + lld@20) over the Windows SDK and
# the MSVC CRT laid out by xwin (tools/plugin_win_toolchain.sh puts them in .local/win-toolchain/sdk; nothing of it is
# committed). On a Windows machine none of this is needed: cmake -G Ninja -T ClangCL (or a clang-cl in PATH).
#   cmake -S plugin -B build/plugin-win -G Ninja -DCMAKE_TOOLCHAIN_FILE=plugin/cmake/win-x64-clangcl.cmake
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_VERSION 10.0)
set(CMAKE_SYSTEM_PROCESSOR AMD64)
get_filename_component(_frp_root "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
if(EXISTS "${_frp_root}/.local/win-toolchain/sdk")
    set(_frp_xwin_default "${_frp_root}/.local/win-toolchain/sdk")
else()   # (a git worktree under .claude/worktrees/<name>: the main checkout's .local)
    get_filename_component(_frp_xwin_default "${_frp_root}/../../../.local/win-toolchain/sdk" ABSOLUTE)
endif()
set(FRP_XWIN "${_frp_xwin_default}" CACHE PATH "the xwin splat (crt/, sdk/)")
set(FRP_LLVM_BIN "/opt/homebrew/opt/llvm@20/bin" CACHE PATH "clang-cl, llvm-lib, llvm-rc, llvm-mt, llvm-nm")
set(FRP_LLD_BIN "/opt/homebrew/opt/lld@20/bin" CACHE PATH "lld-link (the same LLVM version as clang-cl: LTO)")
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES FRP_XWIN FRP_LLVM_BIN FRP_LLD_BIN)

set(CMAKE_C_COMPILER "${FRP_LLVM_BIN}/clang-cl")
set(CMAKE_CXX_COMPILER "${FRP_LLVM_BIN}/clang-cl")
set(CMAKE_C_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_LINKER "${FRP_LLD_BIN}/lld-link" CACHE FILEPATH "")
set(CMAKE_AR "${FRP_LLVM_BIN}/llvm-lib" CACHE FILEPATH "")
set(CMAKE_NM "${FRP_LLVM_BIN}/llvm-nm" CACHE FILEPATH "")
set(CMAKE_RC_COMPILER "${FRP_LLVM_BIN}/llvm-rc" CACHE FILEPATH "")
set(CMAKE_MT "${FRP_LLVM_BIN}/llvm-mt" CACHE FILEPATH "")

set(_frp_inc "/imsvc \"${FRP_XWIN}/crt/include\" /imsvc \"${FRP_XWIN}/sdk/include/ucrt\" /imsvc \"${FRP_XWIN}/sdk/include/um\" /imsvc \"${FRP_XWIN}/sdk/include/shared\"")
set(CMAKE_C_FLAGS_INIT "${_frp_inc}")
set(CMAKE_CXX_FLAGS_INIT "${_frp_inc} /EHsc")
set(CMAKE_RC_FLAGS_INIT "-I \"${FRP_XWIN}/sdk/include/um\" -I \"${FRP_XWIN}/sdk/include/shared\"")
set(_frp_lib "/libpath:\"${FRP_XWIN}/crt/lib/x86_64\" /libpath:\"${FRP_XWIN}/sdk/lib/um/x86_64\" /libpath:\"${FRP_XWIN}/sdk/lib/ucrt/x86_64\"")
foreach(_k EXE SHARED MODULE)
    set(CMAKE_${_k}_LINKER_FLAGS_INIT "${_frp_lib}")
endforeach()
# the static CRT: the DLL then needs no Visual C++ redistributable on the user's machine
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
