/* SPDX-License-Identifier: GPL-3.0-only */
/* Frodhes plugin UI: the value texts and the parameters' defaults (B-M2a, shared by the render tool and the plugin
 * window from B-M2b). params.c's param_text arithmetic over the same tables (firmware/gen/fx_tables.h), with ui.c's
 * cases that depend on another value (a synced rate shows its note value, the bar engines' named detents). */
#ifndef FRUI_TEXT_H
#define FRUI_TEXT_H
#include <stdint.h>
#include "frui.h"
extern const uint8_t frui_param_def[FRP_COUNT];       /* params.c PARAMS[].def */
/* a frui_text_fn: the value text and its unit for widget id (a parameter, FRP_TUNE) */
void frui_text_default(void *ctx, const frui_model *m, int id, char *val, char *unit);
#endif
