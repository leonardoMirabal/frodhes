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
# clap-wrapper with the plugin's one change, in a copy in the build folder (the pinned checkout stays as fetched):
# its VST3 getLatencySamples() answers 0 while the component is inactive, as CLAP lets a plugin's latency.get() be
# called only while it is activating or active. Frodhes's answers at any time (bridge.c: the last activated rate's
# latency, 48 kHz's before the first activation), and changes it only inside activate(), where it tells the host
# (frodhes_clap.c pl_activate: host latency.changed(), which the wrapper turns into kLatencyChanged). So a VST3 host
# that asks before setActive(true) gets the latency, not 0.
set(_frp_cw "${CMAKE_BINARY_DIR}/clap-wrapper-frp")
set(_frp_cw_mark "${_frp_cw}/.frp-806bba0a-latency-1")
if(NOT EXISTS "${_frp_cw_mark}")
    file(REMOVE_RECURSE "${_frp_cw}")
    file(COPY "${FRP_DEPS_DIR}/clap-wrapper/" DESTINATION "${_frp_cw}" PATTERN ".git" EXCLUDE)
    file(READ "${_frp_cw}/src/wrapasvst3.cpp" _frp_s)
    set(_frp_old "  if (!_active)\n  {\n    _missedLatencyRequest = true;\n    return 0;\n  }\n\n  _missedLatencyRequest = false;\n  return _plugin->_ext._latency->get(_plugin->_plugin);")
    set(_frp_new "  // (Frodhes, plugin/cmake/deps.cmake: its latency.get() answers at any time; it changes only in activate)\n  _missedLatencyRequest = false;\n  return _plugin->_ext._latency->get(_plugin->_plugin);")
    string(REPLACE "${_frp_old}" "${_frp_new}" _frp_t "${_frp_s}")
    if(_frp_t STREQUAL _frp_s)
        message(FATAL_ERROR "plugin: clap-wrapper's getLatencySamples() is not as plugin/cmake/deps.cmake expects")
    endif()
    file(WRITE "${_frp_cw}/src/wrapasvst3.cpp" "${_frp_t}")
    file(WRITE "${_frp_cw_mark}" "")
endif()
set(CLAP_WRAPPER_ROOT "${_frp_cw}")
# (the wrapper keeps its source folder in the cache: a build folder configured before this change names the checkout)
set(CLAP_WRAPPER_CMAKE_CURRENT_SOURCE_DIR "${_frp_cw}" CACHE STRING "Clap Wrapper Source Location" FORCE)
