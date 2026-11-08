# SPDX-License-Identifier: GPL-3.0-only
# Frodhes as a desktop plugin (milestone B-M0): CLAP, and VST3 + Audio Unit v2 through clap-wrapper (MIT).
#   tools/plugin_deps.sh                                       the pinned dependencies into .local/plugin-deps
#   cmake -S plugin -B build/plugin -G Ninja && cmake --build build/plugin      (or: make plugin)
# The bundles land in build/plugin/bundles: Frodhes.clap, Frodhes.vst3, Frodhes.component. Nothing is installed.
# Windows x64 (CLAP + VST3; clang-cl, not MSVC: the engine uses GNU C): on Windows, cmake -G Ninja with clang-cl;
# from macOS, tools/plugin_win.sh (clang-cl + lld-link over the xwin SDK: plugin/cmake/win-x64-clangcl.cmake).
# Linux x86_64 (CLAP + VST3): tools/plugin_linux.sh builds in Docker on Ubuntu 20.04 (glibc 2.31) with clang 12 + lld.
# The firmware build (tools/build.py) never sees this tree; this tree only reads firmware/src (plugin/src/engine_unit.c
# includes firmware/src/frodhes.c unchanged, over web/web_hal.c).
cmake_minimum_required(VERSION 3.21)
if(APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "the oldest macOS the plugin runs on")
    set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "universal by default (FRP: arm64 alone is faster to build)")
endif()
project(frodhes_plugin LANGUAGES C CXX)
if(NOT WIN32)
    enable_language(ASM)           # (the coroutine's switch routines; Windows uses Fibers: plugin/src/coro/coro.h)
endif()
if(APPLE)
    enable_language(OBJC OBJCXX)   # (clap-wrapper's Audio Unit and VST3 sources: at the top level, or Ninja lacks the rules)
endif()
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(FRP_VERSION "0.0.0-dev" CACHE STRING "the version text (the release's X.Y.Z; a number only in the GitHub release)")
get_filename_component(FRP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# the C flags of the plugin's own code: GNU-style, or clang-cl's spelling of them (Windows). FRP_C_WIN: what the
# Windows build adds (the MSVC CRT shims in plugin/compat/win, force-included / on the include path)
if(MSVC)
    set(FRP_C_STD /clang:-std=gnu11 /clang:-ffp-contract=off)
    set(FRP_C_WARN /W3)
    set(FRP_C_WIN "/FI${FRP_ROOT}/plugin/compat/win/frp_win.h")
    set(FRP_C_WIN_INC "${FRP_ROOT}/plugin/compat/win")
else()
    set(FRP_C_STD -std=gnu11 -ffp-contract=off)
    set(FRP_C_WARN -Wall -Wextra)
    set(FRP_C_WIN "")
    set(FRP_C_WIN_INC "")
endif()

# Linux: self-contained modules (the C++ runtime linked in: no libstdc++ version needed on the user's machine), no
# undefined symbols left, only the entry points exported, a non-executable stack (tools/plugin_linux.sh checks the result with readelf)
if(UNIX AND NOT APPLE)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON)   # (the VST3 SDK's and clap-wrapper's static libraries go into modules)
    foreach(_k SHARED MODULE)
        string(APPEND CMAKE_${_k}_LINKER_FLAGS " -static-libstdc++ -static-libgcc -Wl,--no-undefined -Wl,-z,noexecstack"
               " -Wl,--as-needed -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/cmake/linux_exports.map")
    endforeach()
    find_package(Threads REQUIRED)
endif()

include(cmake/deps.cmake)
include(cmake/slots.cmake)

if(APPLE)
    set(CLAP_WRAPPER_BUILD_AUV2 ON CACHE BOOL "" FORCE)
    set(FRP_FORMATS CLAP VST3 AUV2)
else()
    set(FRP_FORMATS CLAP VST3)
endif()
add_subdirectory("${CLAP_WRAPPER_ROOT}" clap-wrapper EXCLUDE_FROM_ALL)

# the plugin: a static library with the CLAP entry's three functions (clap-wrapper's "clap first" layout)
add_library(frodhes_clap_impl STATIC "${FRP_ROOT}/plugin/src/frodhes_clap.c")
target_link_libraries(frodhes_clap_impl PUBLIC frodhes_engine clap)
if(UNIX AND NOT APPLE)
    target_link_libraries(frodhes_clap_impl PUBLIC Threads::Threads m)
endif()
target_compile_definitions(frodhes_clap_impl PRIVATE "FRP_VERSION=\"${FRP_VERSION}\"")
target_compile_options(frodhes_clap_impl PRIVATE -O2 ${FRP_C_STD} ${FRP_C_WARN} ${FRP_C_WIN})
target_include_directories(frodhes_clap_impl PRIVATE ${FRP_C_WIN_INC})
if(NOT MSVC)   # (the UI core's frp_ui_* functions stay inside the bundle)
    target_compile_options(frodhes_clap_impl PRIVATE -fvisibility=hidden)
endif()
set_target_properties(frodhes_clap_impl PROPERTIES POSITION_INDEPENDENT_CODE ON)

set(FRP_BUNDLES "${CMAKE_BINARY_DIR}/bundles")
make_clapfirst_plugins(
    TARGET_NAME frodhes
    IMPL_TARGET frodhes_clap_impl
    OUTPUT_NAME "Frodhes"
    ENTRY_SOURCE "${FRP_ROOT}/plugin/src/clap_entry.cpp"
    BUNDLE_IDENTIFIER "io.github.leonardomirabal.frodhes"
    BUNDLE_VERSION "0.0.0"
    COPY_AFTER_BUILD FALSE
    PLUGIN_FORMATS ${FRP_FORMATS}
    WINDOWS_FOLDER_VST3 TRUE        # (Windows: the VST3 bundle folder, Frodhes.vst3/Contents/x86_64-win/Frodhes.vst3)
    ASSET_OUTPUT_DIRECTORY "${FRP_BUNDLES}"
    AUV2_MANUFACTURER_NAME "Frodhes"
    AUV2_MANUFACTURER_CODE "Frdh"
    AUV2_SUBTYPE_CODE "Frd1"
    AUV2_INSTRUMENT_TYPE "aumu"
)

# ---- tests (make test-plugin): the coroutine, the headless CLAP host of Gate D
if(WIN32)
    add_executable(frp_test_coro "${FRP_ROOT}/plugin/tests/test_coro.c")
else()
    add_executable(frp_test_coro "${FRP_ROOT}/plugin/tests/test_coro.c" "${FRP_ROOT}/plugin/src/coro/coro_arm64.S"
                   "${FRP_ROOT}/plugin/src/coro/coro_x86_64.S")
endif()
target_compile_options(frp_test_coro PRIVATE -O2 ${FRP_C_WARN} ${FRP_C_WIN})
add_executable(frp_clap_render "${FRP_ROOT}/plugin/tests/clap_render.c")
target_include_directories(frp_clap_render PRIVATE "${CLAP_SDK_ROOT}/include" ${FRP_C_WIN_INC})
target_compile_options(frp_clap_render PRIVATE -O1 ${FRP_C_WARN} ${FRP_C_WIN})
target_link_libraries(frp_clap_render PRIVATE ${CMAKE_DL_LIBS})
if(UNIX AND NOT APPLE)
    target_link_libraries(frp_clap_render PRIVATE m)
endif()
add_custom_target(frodhes_tests ALL DEPENDS frp_test_coro frp_clap_render)
include(cmake/gui.cmake)
add_custom_target(frodhes_plugins ALL DEPENDS frodhes_all)
