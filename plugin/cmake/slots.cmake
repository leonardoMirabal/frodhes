# SPDX-License-Identifier: GPL-3.0-only
# The engine: plugin/src/engine_unit.c compiled FRP_SLOTS (16) times, -DFRP_SLOT=0..15, each into its own object
# (the app's statics are one set per object: 16 independent apps in one binary), plus the instance layer and the
# coroutine. Every slot is the HQ build: the Move's HQ switches (the float paths of every engine, the full-rate reverb
# TYPEs, the compressor kept through FX BYPASS) with 24 voices on every engine; an instance holds one slot
# (plugin/src/bridge.c). There is no CLASSIC (the FM-1's 10-voice integer build) in the plugin any more: Gate D
# compares the plugin with the host build at these same switches. Before the archive is written,
# plugin/cmake/nm_check.cmake fails the build if any slot object defines a global symbol other than its table
# frp_slot_<N> (two copies of the app must never share a symbol).
set(FRP_SLOTS 16)
# the app's build switches (tools/gate_d.py DEFS and tools/gen_plugin_params.py DEFS are the same)
set(FRP_ENGINE_DEFS FR_VOICES=24u FR_HQ=1 FR_BYP_KEEP_CMP=1 FR_REV_HQ=1 "APP_VER=\"${FRP_VERSION}\"")
if(MSVC)   # clang-cl (Windows): the same switches in its spelling; COFF exports nothing unless asked
    set(FRP_ENGINE_FLAGS -O2 ${FRP_C_STD} /W3 -Wno-unused-function -Wno-unused-variable ${FRP_C_WIN})
else()
    set(FRP_ENGINE_FLAGS -O2 -std=gnu11 -ffp-contract=off -fvisibility=hidden -Wall -Wno-unused-function
                         -Wno-unused-variable)
endif()
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
math(EXPR _last "${FRP_SLOTS} - 1")
foreach(n RANGE 0 ${_last})
    add_library(frp_slot_${n} OBJECT "${FRP_ROOT}/plugin/src/engine_unit.c")
    target_compile_definitions(frp_slot_${n} PRIVATE FRP_SLOT=${n} ${FRP_ENGINE_DEFS})
    target_include_directories(frp_slot_${n} PRIVATE "${FRP_ROOT}/plugin/compat" "${FRP_ROOT}/firmware/src"
                               "${FRP_ROOT}/firmware/gen" ${FRP_C_WIN_INC})
    target_compile_options(frp_slot_${n} PRIVATE ${FRP_ENGINE_FLAGS})
    set_target_properties(frp_slot_${n} PROPERTIES POSITION_INDEPENDENT_CODE ON)
    list(APPEND _frp_objs "$<TARGET_OBJECTS:frp_slot_${n}>")
    list(APPEND _frp_slot_targets frp_slot_${n})
endforeach()

if(WIN32)
    set(_frp_coro_src "")          # (Fibers: plugin/src/coro/coro.h)
else()
    set(_frp_coro_src "${FRP_ROOT}/plugin/src/coro/coro_arm64.S" "${FRP_ROOT}/plugin/src/coro/coro_x86_64.S")
endif()
add_library(frodhes_engine STATIC
    "${FRP_ROOT}/plugin/src/bridge.c"
    "${FRP_ROOT}/plugin/src/frp_paths.c"
    ${_frp_coro_src}
    ${_frp_objs})
target_include_directories(frodhes_engine PUBLIC "${FRP_ROOT}/plugin/src" "${FRP_ROOT}/plugin/gen"
                           PRIVATE "${FRP_ROOT}/firmware/src" ${FRP_C_WIN_INC})
target_compile_options(frodhes_engine PRIVATE $<$<COMPILE_LANGUAGE:C>:${FRP_ENGINE_FLAGS}>)
set_target_properties(frodhes_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
add_dependencies(frodhes_engine ${_frp_slot_targets})
string(REPLACE ";" "|" _frp_objs_arg "${_frp_objs}")
add_custom_command(TARGET frodhes_engine PRE_LINK
    COMMAND ${CMAKE_COMMAND} "-DOBJS=$<JOIN:${_frp_objs},|>" -DNM=${CMAKE_NM} -DWEAK_CRT_OK=$<BOOL:${WIN32}> -P "${FRP_ROOT}/plugin/cmake/nm_check.cmake"
    COMMENT "plugin: the slot objects export nothing but their tables (nm)"
    VERBATIM)
