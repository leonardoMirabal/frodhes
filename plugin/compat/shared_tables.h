/* SPDX-License-Identifier: GPL-3.0-only */
/* The engine slots' shared tables (plugin/compat/<table header>.h): a generated table header's "static const" becomes
 * FRP_SHARED_TABLE const, a weak hidden definition, so the linker keeps one copy for all 32 slots (each slot object
 * defines it; the copies are the same bytes: the headers depend on no slot or pool switch, and Gate D renders both
 * pools byte for byte). Hidden: never exported from the bundle. A header shared this way must hold data only (no
 * functions) and must not depend on FR_HQ / FR_VOICES; plugin/cmake/nm_check.cmake allows these weak names only. */
#ifndef FRP_SHARED_TABLES_H
#define FRP_SHARED_TABLES_H
#include <stdint.h>
#define FRP_SHARED_TABLE __attribute__((weak, visibility("hidden")))
#endif
