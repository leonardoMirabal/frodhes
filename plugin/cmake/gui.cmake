# SPDX-License-Identifier: GPL-3.0-only
# The plugin window (B-M2b): frui (plugin/ui), the embedded fonts, the window's core (plugin/gui_core.c: the model,
# the input, the partial repaints) and one platform view behind plugin/gui_platform.h:
#   frodhes_ui        frui + the core + the fonts (any platform)
#   frodhes_gui_mac   macOS: plugin/gui_mac.m (Cocoa + QuartzCore)
#   frodhes_gui_win   Windows: plugin/gui_win.c (user32 + gdi32 only)
#   frodhes_gui_x11   Linux: plugin/gui_x11.c (libX11 only)
# FRP_GUI_VIEW names the one the plugin links. Tests: frp_test_gui_ctl (the core through its platform client,
# headless, on the build machine's own platform); Windows / Linux: frp_gui_demo (a window with the view over a
# stand-in host; --selftest drives it with native events, exits 0 / 1); macOS: frp_gui_shot (CMakeLists.txt).
set(_frp_fonts_c "${CMAKE_BINARY_DIR}/gen/frp_fonts.c")
set(_frp_font_text "${FRP_ROOT}/assets/fonts/InterTight[wght].ttf")
set(_frp_font_disp "${FRP_ROOT}/assets/fonts/Michroma-Regular.ttf")
add_custom_command(OUTPUT "${_frp_fonts_c}"
    COMMAND ${CMAKE_COMMAND} "-DIN=${_frp_font_text}" "-DOUT=${_frp_fonts_c}.text.c" -DNAME=frp_font_text
            -P "${FRP_ROOT}/plugin/cmake/embed.cmake"
    COMMAND ${CMAKE_COMMAND} "-DIN=${_frp_font_disp}" "-DOUT=${_frp_fonts_c}.disp.c" -DNAME=frp_font_display
            -P "${FRP_ROOT}/plugin/cmake/embed.cmake"
    COMMAND ${CMAKE_COMMAND} -E cat "${_frp_fonts_c}.text.c" "${_frp_fonts_c}.disp.c" > "${_frp_fonts_c}"
    DEPENDS "${_frp_font_text}" "${_frp_font_disp}" "${FRP_ROOT}/plugin/cmake/embed.cmake"
    COMMENT "plugin: embedding the UI fonts" VERBATIM)

add_library(frodhes_ui STATIC
    "${FRP_ROOT}/plugin/ui/frui.c" "${FRP_ROOT}/plugin/ui/frui_font.c" "${FRP_ROOT}/plugin/ui/frui_gfx.c"
    "${FRP_ROOT}/plugin/ui/frui_text.c" "${FRP_ROOT}/plugin/gui_core.c" "${_frp_fonts_c}")
target_include_directories(frodhes_ui PUBLIC "${FRP_ROOT}/plugin" "${FRP_ROOT}/plugin/ui")
target_compile_options(frodhes_ui PRIVATE -O2 ${FRP_C_STD} ${FRP_C_WARN} ${FRP_C_WIN})
set_target_properties(frodhes_ui PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(NOT MSVC)
    target_link_libraries(frodhes_ui PUBLIC m)
endif()

add_executable(frp_test_gui_ctl "${FRP_ROOT}/plugin/tests/test_gui_ctl.c")
target_link_libraries(frp_test_gui_ctl PRIVATE frodhes_ui)
target_compile_options(frp_test_gui_ctl PRIVATE -O1 ${FRP_C_STD} ${FRP_C_WARN} ${FRP_C_WIN})
set(FRP_GUI_TESTS frp_test_gui_ctl)

if(APPLE)
    add_library(frodhes_gui_mac STATIC "${FRP_ROOT}/plugin/gui_mac.m")
    target_link_libraries(frodhes_gui_mac PUBLIC frodhes_ui "-framework Cocoa" "-framework QuartzCore")
    target_compile_options(frodhes_gui_mac PRIVATE -O2 -ffp-contract=off ${FRP_C_WARN})
    set_target_properties(frodhes_gui_mac PROPERTIES POSITION_INDEPENDENT_CODE ON)
    set(FRP_GUI_VIEW frodhes_gui_mac)
elseif(WIN32)
    add_library(frodhes_gui_win STATIC "${FRP_ROOT}/plugin/gui_win.c")
    target_link_libraries(frodhes_gui_win PUBLIC frodhes_ui user32 gdi32)
    target_compile_options(frodhes_gui_win PRIVATE -O2 ${FRP_C_STD} ${FRP_C_WARN} ${FRP_C_WIN})
    set(FRP_GUI_VIEW frodhes_gui_win)
    add_executable(frp_gui_demo WIN32 "${FRP_ROOT}/plugin/tests/gui_win_demo.c")
    target_link_libraries(frp_gui_demo PRIVATE frodhes_gui_win)
    target_compile_options(frp_gui_demo PRIVATE -O1 ${FRP_C_STD} ${FRP_C_WARN} ${FRP_C_WIN})
    list(APPEND FRP_GUI_TESTS frp_gui_demo)
elseif(UNIX)
    find_package(X11 REQUIRED)
    add_library(frodhes_gui_x11 STATIC "${FRP_ROOT}/plugin/gui_x11.c")
    target_include_directories(frodhes_gui_x11 PRIVATE ${X11_INCLUDE_DIR})
    target_link_libraries(frodhes_gui_x11 PUBLIC frodhes_ui ${X11_X11_LIB})
    target_compile_options(frodhes_gui_x11 PRIVATE -O2 ${FRP_C_STD} ${FRP_C_WARN})
    set_target_properties(frodhes_gui_x11 PROPERTIES POSITION_INDEPENDENT_CODE ON)
    set(FRP_GUI_VIEW frodhes_gui_x11)
    add_executable(frp_gui_demo "${FRP_ROOT}/plugin/tests/gui_x11_demo.c")
    target_link_libraries(frp_gui_demo PRIVATE frodhes_gui_x11 m)
    target_compile_options(frp_gui_demo PRIVATE -O1 ${FRP_C_STD} ${FRP_C_WARN})
    list(APPEND FRP_GUI_TESTS frp_gui_demo)
endif()
add_custom_target(frodhes_gui_tests ALL DEPENDS ${FRP_GUI_TESTS})
