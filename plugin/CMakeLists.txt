# SPDX-License-Identifier: GPL-3.0-only
# Frodhes as a desktop plugin (milestone B-M0): CLAP, and VST3 + Audio Unit v2 through clap-wrapper (MIT).
#   tools/plugin_deps.sh                                       the pinned dependencies into .local/plugin-deps
#   cmake -S plugin -B build/plugin -G Ninja && cmake --build build/plugin      (or: make plugin)
# The bundles land in build/plugin/bundles: Frodhes.clap, Frodhes.vst3, Frodhes.component. Nothing is installed.
# The firmware build (tools/build.py) never sees this tree; this tree only reads firmware/src (plugin/src/engine_unit.c
# includes firmware/src/frodhes.c unchanged, over web/web_hal.c).
cmake_minimum_required(VERSION 3.21)
if(APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "the oldest macOS the plugin runs on")
    set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "universal by default (FRP: arm64 alone is faster to build)")
endif()
project(frodhes_plugin LANGUAGES C CXX ASM)
if(APPLE)
    enable_language(OBJC OBJCXX)   # (clap-wrapper's Audio Unit and VST3 sources: at the top level, or Ninja lacks the rules)
endif()
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)   # (Linux: the wrapper's static libraries go into shared modules)
set(FRP_VERSION "0.0.0-dev" CACHE STRING "the version text (the release's X.Y.Z; a number only in the GitHub release)")
get_filename_component(FRP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

include(cmake/deps.cmake)
include(cmake/slots.cmake)

# Windows (MinGW-w64: tools/plugin_pack.sh windows, plugin/cmake/mingw-w64-x86_64.cmake): the C and C++ runtimes and
# winpthreads linked in, so each binary needs only the system's DLLs
if(MINGW)
    add_link_options(-static)
elseif(UNIX AND NOT APPLE)   # (Linux: libstdc++ and libgcc linked in; a bundle needs only glibc)
    add_link_options(-static-libstdc++ -static-libgcc)
endif()
set(FRP_FORMATS CLAP VST3)
if(APPLE)
    list(APPEND FRP_FORMATS AUV2)
endif()
set(CLAP_WRAPPER_BUILD_AUV2 ${APPLE} CACHE BOOL "" FORCE)
add_subdirectory("${CLAP_WRAPPER_ROOT}" clap-wrapper EXCLUDE_FROM_ALL)

# the plugin: a static library with the CLAP entry's three functions (clap-wrapper's "clap first" layout)
add_library(frodhes_clap_impl STATIC "${FRP_ROOT}/plugin/src/frodhes_clap.c")
target_link_libraries(frodhes_clap_impl PUBLIC frodhes_engine clap)
if(UNIX AND NOT APPLE)   # (glibc before 2.34 keeps pthread and dlopen in their own libraries; the wrapper uses both)
    set(THREADS_PREFER_PTHREAD_FLAG ON)
    find_package(Threads REQUIRED)
    target_link_libraries(frodhes_clap_impl PUBLIC Threads::Threads ${CMAKE_DL_LIBS})
endif()
target_compile_definitions(frodhes_clap_impl PRIVATE "FRP_VERSION=\"${FRP_VERSION}\"")
target_compile_options(frodhes_clap_impl PRIVATE -O2 -std=gnu11 -ffp-contract=off -Wall -Wextra)
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
    WINDOWS_FOLDER_VST3 TRUE
    ASSET_OUTPUT_DIRECTORY "${FRP_BUNDLES}"
    AUV2_MANUFACTURER_NAME "Frodhes"
    AUV2_MANUFACTURER_CODE "Frdh"
    AUV2_SUBTYPE_CODE "Frd1"
    AUV2_INSTRUMENT_TYPE "aumu"
)

if(MINGW)   # (the VST3 SDK's threadchecker_win32.cpp calls std::terminate without <exception>: libc++ needs it)
    target_compile_options(base-sdk-vst3 PRIVATE "SHELL:-include exception")
    set_target_properties(frodhes_vst3 PROPERTIES PREFIX "")   # (MinGW names a module libX: the VST3 binary is Frodhes.vst3)
endif()

# ---- tests (make test-plugin): the coroutine, the headless CLAP host of Gate D
add_executable(frp_test_coro "${FRP_ROOT}/plugin/tests/test_coro.c" ${FRP_CORO_SRC})
target_compile_options(frp_test_coro PRIVATE -O2 -Wall -Wextra)
add_executable(frp_clap_render "${FRP_ROOT}/plugin/tests/clap_render.c")
target_include_directories(frp_clap_render PRIVATE "${CLAP_SDK_ROOT}/include")
target_compile_options(frp_clap_render PRIVATE -O1 -Wall -Wextra)
target_link_libraries(frp_clap_render PRIVATE ${CMAKE_DL_LIBS})
if(UNIX AND NOT APPLE)
    target_link_libraries(frp_clap_render PRIVATE m)
endif()
add_custom_target(frodhes_tests ALL DEPENDS frp_test_coro frp_clap_render)
add_custom_target(frodhes_plugins ALL DEPENDS frodhes_all)
