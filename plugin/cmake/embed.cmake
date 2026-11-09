# SPDX-License-Identifier: GPL-3.0-only
# A file's bytes as a C array (the plugin window's fonts, plugin/gui_core.c):
#   cmake -DIN=<file> -DOUT=<file.c> -DNAME=<symbol> -P plugin/cmake/embed.cmake
# writes const unsigned char NAME[] and const unsigned long NAME_len.
file(READ "${IN}" _hex HEX)
string(LENGTH "${_hex}" _n)
math(EXPR _len "${_n} / 2")
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _body "${_hex}")
string(REGEX REPLACE "((0x..,){24})" "\\1\n" _body "${_body}")
get_filename_component(_src "${IN}" NAME)
file(WRITE "${OUT}.tmp" "/* generated from assets/fonts/${_src} by plugin/cmake/embed.cmake: do not edit */\n"
     "const unsigned char ${NAME}[] = {\n${_body}};\nconst unsigned long ${NAME}_len = ${_len}ul;\n")
file(RENAME "${OUT}.tmp" "${OUT}")
