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
set(FRP_VERSION "0.0.0-dev" CACHE STRING "the version text (the release's X.Y.Z; a number only in the GitHub release)")
get_filename_component(FRP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

include(cmake/deps.cmake)
include(cmake/slots.cmake)

set(CLAP_WRAPPER_BUILD_AUV2 ON CACHE BOOL "" FORCE)
add_subdirectory("${CLAP_WRAPPER_ROOT}" clap-wrapper EXCLUDE_FROM_ALL)

# the plugin window (B-M2b): plugin/gui_*.c over the renderer plugin/ui/, the fonts embedded in the binary
set(FRP_FONT_TEXT "${FRP_ROOT}/assets/fonts/InterTight[wght].ttf")
set(FRP_FONT_DISPLAY "${FRP_ROOT}/assets/fonts/Michroma-Regular.ttf")
foreach(_f text display)
    string(TOUPPER "${_f}" _F)
    add_custom_command(OUTPUT "${CMAKE_BINARY_DIR}/gen/font_${_f}.c"
        COMMAND ${CMAKE_COMMAND} -DIN=${FRP_FONT_${_F}} -DOUT=${CMAKE_BINARY_DIR}/gen/font_${_f}.c -DNAME=frp_font_${_f}
                -P "${FRP_ROOT}/plugin/cmake/embed.cmake"
        DEPENDS "${FRP_FONT_${_F}}" "${FRP_ROOT}/plugin/cmake/embed.cmake" VERBATIM)
endforeach()
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/gen")
set(FRP_GUI_SRC "${FRP_ROOT}/plugin/gui_core.c" "${FRP_ROOT}/plugin/gui_clap.c"
    "${FRP_ROOT}/plugin/ui/frui.c" "${FRP_ROOT}/plugin/ui/frui_font.c" "${FRP_ROOT}/plugin/ui/frui_gfx.c"
    "${FRP_ROOT}/plugin/ui/frui_text.c" "${CMAKE_BINARY_DIR}/gen/font_text.c" "${CMAKE_BINARY_DIR}/gen/font_display.c")
if(APPLE)
    list(APPEND FRP_GUI_SRC "${FRP_ROOT}/plugin/gui_mac.m")
elseif(WIN32)
    list(APPEND FRP_GUI_SRC "${FRP_ROOT}/plugin/gui_win.c")
else()
    list(APPEND FRP_GUI_SRC "${FRP_ROOT}/plugin/gui_none.c")    # (an X11 view: a later milestone)
endif()

# the plugin: a static library with the CLAP entry's three functions (clap-wrapper's "clap first" layout)
add_library(frodhes_clap_impl STATIC "${FRP_ROOT}/plugin/src/frodhes_clap.c" ${FRP_GUI_SRC})
target_link_libraries(frodhes_clap_impl PUBLIC frodhes_engine clap)
target_compile_definitions(frodhes_clap_impl PRIVATE "FRP_VERSION=\"${FRP_VERSION}\"")
target_compile_options(frodhes_clap_impl PRIVATE -O2 -ffp-contract=off -Wall -Wextra
                       $<$<COMPILE_LANGUAGE:C>:-std=gnu11>)
set_target_properties(frodhes_clap_impl PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(APPLE)
    target_link_libraries(frodhes_clap_impl PUBLIC "-framework Cocoa" "-framework QuartzCore")
elseif(WIN32)
    target_link_libraries(frodhes_clap_impl PUBLIC user32 gdi32)
    target_compile_definitions(frodhes_clap_impl PRIVATE _USE_MATH_DEFINES _CRT_SECURE_NO_WARNINGS)
endif()

set(FRP_BUNDLES "${CMAKE_BINARY_DIR}/bundles")
make_clapfirst_plugins(
    TARGET_NAME frodhes
    IMPL_TARGET frodhes_clap_impl
    OUTPUT_NAME "Frodhes"
    ENTRY_SOURCE "${FRP_ROOT}/plugin/src/clap_entry.cpp"
    BUNDLE_IDENTIFIER "io.github.leonardomirabal.frodhes"
    BUNDLE_VERSION "0.0.0"
    COPY_AFTER_BUILD FALSE
    PLUGIN_FORMATS CLAP VST3 AUV2
    ASSET_OUTPUT_DIRECTORY "${FRP_BUNDLES}"
    AUV2_MANUFACTURER_NAME "Frodhes"
    AUV2_MANUFACTURER_CODE "Frdh"
    AUV2_SUBTYPE_CODE "Frd1"
    AUV2_INSTRUMENT_TYPE "aumu"
)

# ---- tests (make test-plugin): the coroutine, the headless CLAP host of Gate D
add_executable(frp_test_coro "${FRP_ROOT}/plugin/tests/test_coro.c" "${FRP_ROOT}/plugin/src/coro/coro_arm64.S"
               "${FRP_ROOT}/plugin/src/coro/coro_x86_64.S")
target_compile_options(frp_test_coro PRIVATE -O2 -Wall -Wextra)
add_executable(frp_clap_render "${FRP_ROOT}/plugin/tests/clap_render.c")
target_include_directories(frp_clap_render PRIVATE "${CLAP_SDK_ROOT}/include")
target_compile_options(frp_clap_render PRIVATE -O1 -Wall -Wextra)
target_link_libraries(frp_clap_render PRIVATE ${CMAKE_DL_LIBS})
set(FRP_TEST_TARGETS frp_test_coro frp_clap_render)
if(APPLE)   # the window's test host (tests/py/test_plugin_gui.py)
    add_executable(frp_gui_shot "${FRP_ROOT}/plugin/tests/gui_shot.m")
    target_include_directories(frp_gui_shot PRIVATE "${CLAP_SDK_ROOT}/include")
    target_compile_options(frp_gui_shot PRIVATE -O1 -Wall -Wextra)
    target_link_libraries(frp_gui_shot PRIVATE "-framework Cocoa" "-framework QuartzCore" z)
    list(APPEND FRP_TEST_TARGETS frp_gui_shot)
endif()
add_custom_target(frodhes_tests ALL DEPENDS ${FRP_TEST_TARGETS})
add_custom_target(frodhes_plugins ALL DEPENDS frodhes_all)
