/* SPDX-License-Identifier: GPL-3.0-only */
/* firmware/gen/rhodes_phim.h as every engine slot sees it (plugin/cmake/slots.cmake): this directory comes first on the include
 * path, so the app's #include "rhodes_phim.h" lands here. Its tables are defined weak (hidden) instead of static: the 32 slots'
 * identical copies become one in the bundle (the slots never write them; nothing compares their addresses). The data
 * is firmware/gen's own, unchanged; plugin/compat/shared_tables.h holds the rule. */
#include "shared_tables.h"
#define static FRP_SHARED_TABLE
#include "../../firmware/gen/rhodes_phim.h"
#undef static
