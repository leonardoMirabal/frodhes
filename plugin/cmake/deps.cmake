# SPDX-License-Identifier: GPL-3.0-only
# The pinned dependencies (plugin/deps.lock), fetched by tools/plugin_deps.sh into .local/plugin-deps (or
# FRP_DEPS_DIR). This file never downloads: it checks that each checkout is at its pinned commit and points
# clap-wrapper at them (CLAP_SDK_ROOT, VST3_SDK_ROOT, AUDIOUNIT_SDK_ROOT), so the build is the same offline.
set(FRP_DEPS_DIR "${FRP_ROOT}/.local/plugin-deps" CACHE PATH "where tools/plugin_deps.sh put the dependencies")
file(STRINGS "${FRP_ROOT}/plugin/deps.lock" _frp_lock REGEX "^git ")
foreach(_l IN LISTS _frp_lock)
    string(REGEX REPLACE "[ \t]+" ";" _f "${_l}")
    list(GET _f 1 _name)
    list(GET _f 4 _pin)
    set(_dir "${FRP_DEPS_DIR}/${_name}")
    execute_process(COMMAND git -C "${_dir}" rev-parse HEAD OUTPUT_VARIABLE _head
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET RESULT_VARIABLE _rc)
    if(NOT _rc EQUAL 0 OR NOT _head STREQUAL _pin)
        message(FATAL_ERROR "plugin: ${_name} is not at its pinned commit ${_pin} in ${_dir} "
                            "(got '${_head}'): run tools/plugin_deps.sh")
    endif()
    message(STATUS "plugin: ${_name} ${_pin}")
endforeach()
set(CLAP_SDK_ROOT "${FRP_DEPS_DIR}/clap" CACHE PATH "" FORCE)
set(VST3_SDK_ROOT "${FRP_DEPS_DIR}/vst3sdk" CACHE PATH "" FORCE)
set(AUDIOUNIT_SDK_ROOT "${FRP_DEPS_DIR}/audiounitsdk" CACHE PATH "" FORCE)
set(CLAP_WRAPPER_DOWNLOAD_DEPENDENCIES OFF CACHE BOOL "" FORCE)
set(CLAP_WRAPPER_ROOT "${FRP_DEPS_DIR}/clap-wrapper")
