/* SPDX-License-Identifier: GPL-3.0-only */
/* The update module's runtime calls (frp_update.h) with nothing behind them: a build with -DFRP_UPDATE_CHECK=OFF (the
 * CMake option, ON by default) links this file instead of frp_update.c, frp_update_rt.c and the per-OS network file.
 * No request is made, no file is written, no thread starts; the window's UPDATES setting reads OFF and a newer
 * release is never offered. */
#include <stddef.h>
#include "frp_update.h"

void frp_update_init(const char *own_version) { (void)own_version; }
void frp_update_shutdown(void) {}
void frp_update_poke(void) {}
int frp_update_available(char ver[16])
{
    if (ver) ver[0] = 0;
    return FRP_UPD_NONE;
}
int frp_update_enabled(void) { return 0; }
void frp_update_set_enabled(int on) { (void)on; }
int frp_update_open_download(void) { return -1; }
int frp_update_busy(void) { return 0; }
int frp_update_downloads_dir(char *out, size_t n)
{
    if (out && n) out[0] = 0;
    return -1;
}
