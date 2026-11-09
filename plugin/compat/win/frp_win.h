/* SPDX-License-Identifier: GPL-3.0-only */
/* Windows: force-included (clang-cl /FI) into every C file of the plugin's Windows build (plugin/CMakeLists.txt), so
 * the shared sources build unchanged against the MSVC CRT. */
#ifndef FRODHES_PLUGIN_FRP_WIN_H
#define FRODHES_PLUGIN_FRP_WIN_H
#define _USE_MATH_DEFINES 1              /* M_PI */
#define _CRT_SECURE_NO_WARNINGS 1        /* (snprintf, strcpy: the CRT's "use the _s forms" notes) */
#define NOMINMAX 1
#define WIN32_LEAN_AND_MEAN 1
#include <malloc.h>
#include <stdlib.h>
/* C11 aligned_alloc: the MSVC CRT has none (its free() cannot release an aligned block; the plugin's only
 * aligned_alloc, the coroutine stack in slot_bridge.h, is never freed and unused on Windows: Fibers have their own) */
#define aligned_alloc(al, n) _aligned_malloc((n), (al))
/* <sys/stat.h>'s POSIX test (the test hosts) */
#define S_ISDIR(m) (((m) & _S_IFMT) == _S_IFDIR)
#endif
