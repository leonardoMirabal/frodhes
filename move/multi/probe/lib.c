/* SPDX-License-Identifier: GPL-3.0-only */
/* the glibc probe's library: one global, its address and its value tell whether two loads share it */
static int g_val;
__attribute__((visibility("default"))) int *probe_global(void) { return &g_val; }
