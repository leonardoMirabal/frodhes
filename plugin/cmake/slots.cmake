# SPDX-License-Identifier: GPL-3.0-only
# The engine: plugin/src/engine_unit.c compiled 2 x FRP_SLOTS (32) times, -DFRP_SLOT=0..31, each into its own object
# (the app's statics are one set per object: 32 independent apps in one binary), plus the instance layer and the
# coroutine. Two pools of 16: slots 0..15 are HQ (the Move's build: 16 voices, the float paths), slots 16..31 CLASSIC
# (the FM-1's build, unchanged: 10 voices, the integer paths; Gate R's renders). An instance holds one of each and plays
# the one its ENGINE names (plugin/src/bridge.c). Before the archive is written, plugin/cmake/nm_check.cmake fails the build if any slot object defines a
# global symbol other than its table frp_slot_<N> (two copies of the app must never share a symbol).
set(FRP_SLOTS 16)
# the app's build switches: HQ the Move's (16 voices, the HQ paths), CLASSIC the FM-1's (none: 10 voices)
set(FRP_ENGINE_DEFS_HQ FR_VOICES=16u FR_HQ=1 FR_BYP_KEEP_CMP=1 FR_REV_HQ=1 "APP_VER=\"${FRP_VERSION}\"")
set(FRP_ENGINE_DEFS_CLASSIC "APP_VER=\"${FRP_VERSION}\"")
set(FRP_ENGINE_FLAGS -O2 -std=gnu11 -ffp-contract=off -fvisibility=hidden -Wall -Wno-unused-function
                     -Wno-unused-variable)
# Full LTO on the slot objects: the 16 copies of the firmware's constant tables (bars, rhodes, pulse, HQ: ~3 MB per
# slot) are identical, and the link-time constant merge keeps one copy (a 4-slot arm64 test: 3.6 MB of constants, not
# 11.9 MB). Without it a universal bundle is ~108 MB. The audio is unchanged (Gate D: tools/gate_d.py). The tables
# whose address the code keeps (RH_KEYS, BAR_KEYS, RH_PHIM_*, the drive and font tables) are not merged that way: the
# slots read them through plugin/compat/<header>.h (first on the include path), weak hidden definitions the linker
# keeps once (shared_tables.h; a 32-slot arm64 slice: 3.0 MB of constants, not 8.1 MB).
option(FRP_LTO "full LTO on the slot objects (shares their constant tables)" ON)
if(FRP_LTO)
    list(APPEND FRP_ENGINE_FLAGS -flto=full)
endif()
set(_frp_objs "")
set(_frp_slot_targets "")
math(EXPR _last "2 * ${FRP_SLOTS} - 1")
foreach(n RANGE 0 ${_last})
    add_library(frp_slot_${n} OBJECT "${FRP_ROOT}/plugin/src/engine_unit.c")
    if(n LESS ${FRP_SLOTS})
        target_compile_definitions(frp_slot_${n} PRIVATE FRP_SLOT=${n} ${FRP_ENGINE_DEFS_HQ})
    else()
        target_compile_definitions(frp_slot_${n} PRIVATE FRP_SLOT=${n} ${FRP_ENGINE_DEFS_CLASSIC})
    endif()
    target_include_directories(frp_slot_${n} PRIVATE "${FRP_ROOT}/plugin/compat" "${FRP_ROOT}/firmware/src"
                               "${FRP_ROOT}/firmware/gen")
    target_compile_options(frp_slot_${n} PRIVATE ${FRP_ENGINE_FLAGS})
    set_target_properties(frp_slot_${n} PROPERTIES POSITION_INDEPENDENT_CODE ON)
    list(APPEND _frp_objs "$<TARGET_OBJECTS:frp_slot_${n}>")
    list(APPEND _frp_slot_targets frp_slot_${n})
endforeach()

add_library(frodhes_engine STATIC
    "${FRP_ROOT}/plugin/src/bridge.c"
    "${FRP_ROOT}/plugin/src/coro/coro_arm64.S"
    "${FRP_ROOT}/plugin/src/coro/coro_x86_64.S"
    ${_frp_objs})
target_include_directories(frodhes_engine PUBLIC "${FRP_ROOT}/plugin/src" "${FRP_ROOT}/plugin/gen"
                           PRIVATE "${FRP_ROOT}/firmware/src")
target_compile_options(frodhes_engine PRIVATE $<$<COMPILE_LANGUAGE:C>:${FRP_ENGINE_FLAGS}>)
set_target_properties(frodhes_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
add_dependencies(frodhes_engine ${_frp_slot_targets})
string(REPLACE ";" "|" _frp_objs_arg "${_frp_objs}")
add_custom_command(TARGET frodhes_engine PRE_LINK
    COMMAND ${CMAKE_COMMAND} "-DOBJS=$<JOIN:${_frp_objs},|>" -DNM=${CMAKE_NM} -P "${FRP_ROOT}/plugin/cmake/nm_check.cmake"
    COMMENT "plugin: the slot objects export nothing but their tables (nm)"
    VERBATIM)
