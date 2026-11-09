/* SPDX-License-Identifier: GPL-3.0-only */
/* Windows (the MSVC CRT has no <strings.h>): the one call the plugin makes from it. On the include path of the
 * Windows build only (plugin/CMakeLists.txt). */
#ifndef FRODHES_PLUGIN_WIN_STRINGS_H
#define FRODHES_PLUGIN_WIN_STRINGS_H
#include <string.h>
#define strcasecmp _stricmp
#define strncasecmp _strnicmp
#endif
