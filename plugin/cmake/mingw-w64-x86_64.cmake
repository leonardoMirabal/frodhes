# SPDX-License-Identifier: GPL-3.0-only
# Windows x64 with llvm-mingw (clang, lld, the UCRT; pinned in plugin/deps.lock, fetched by tools/plugin_deps.sh
# --mingw; tools/plugin_pack.sh build windows passes FRP_MINGW). The same compiler on every host: natively on Windows
# (CI), cross on macOS or Linux, so a local build is the release's build. The C and C++ runtimes are linked in
# (plugin/CMakeLists.txt -static): the binaries import only Windows' own DLLs (tools/plugin_depcheck.py).
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
if(NOT FRP_MINGW)
    set(FRP_MINGW "$ENV{FRP_MINGW}")
endif()
set(_t "${FRP_MINGW}/bin/x86_64-w64-mingw32")
set(_x "")
if(CMAKE_HOST_WIN32)
    set(_x ".exe")
endif()
set(CMAKE_C_COMPILER   "${_t}-clang${_x}")
set(CMAKE_CXX_COMPILER "${_t}-clang++${_x}")
set(CMAKE_ASM_COMPILER "${_t}-clang${_x}")
set(CMAKE_RC_COMPILER  "${_t}-windres${_x}")
set(CMAKE_AR           "${FRP_MINGW}/bin/llvm-ar${_x}" CACHE FILEPATH "")
set(CMAKE_RANLIB       "${FRP_MINGW}/bin/llvm-ranlib${_x}" CACHE FILEPATH "")
set(CMAKE_NM           "${FRP_MINGW}/bin/llvm-nm${_x}" CACHE FILEPATH "")
set(CMAKE_FIND_ROOT_PATH "${FRP_MINGW}/x86_64-w64-mingw32")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_TRY_COMPILE_PLATFORM_VARIABLES FRP_MINGW)
