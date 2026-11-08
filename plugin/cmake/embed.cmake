# SPDX-License-Identifier: GPL-3.0-only
# cmake -DIN=<file> -DOUT=<file.c> -DNAME=<symbol> -P embed.cmake: the file's bytes as a C array (the plugin embeds
# its fonts: nothing is read from disk at run time). Defines  const unsigned char NAME[]  and  const unsigned long
# NAME_len.
file(READ "${IN}" _hex HEX)
string(LENGTH "${_hex}" _n)
math(EXPR _bytes "${_n} / 2")
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," _arr "${_hex}")
string(REGEX REPLACE "((0x[0-9a-f][0-9a-f],){32})" "\\1\n" _arr "${_arr}")
file(WRITE "${OUT}.tmp" "/* generated from ${IN} by plugin/cmake/embed.cmake */\n"
     "const unsigned char ${NAME}[${_bytes}] = {\n${_arr}\n};\nconst unsigned long ${NAME}_len = ${_bytes};\n")
file(RENAME "${OUT}.tmp" "${OUT}")
