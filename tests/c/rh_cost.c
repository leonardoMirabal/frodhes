/* SPDX-License-Identifier: GPL-3.0-only */
/* The Rhodes engine's instruction count per audio block on the host (tests/py/test_rh_gate.py test_cost_model):
 * rh_render's voice loop, with each hot loop's instruction count per sample from the FM-1 build (tools/build.py's
 * hot-loop notes; JieLi clang -Os): the mode loops (MC3, with / without the hammer force), the hammer-force loop,
 * the pickup loops, the zeroing loops. Built with -DRH_NO_GATE -DRH_COST_OLD it counts d8861bf's loops (the
 * old force loop: 39 per sample while the pulse lives, ~6 after it); with -DRH_COST_RC 0.9.5 RC 8d913af's (the force
 * loop in two versions: 20 with no layer mix, 30 with it; 3 per sample for the zeros after the pulse and for rh_x;
 * the felt noise in the fade loop, 100), without them this tree's (O2: the layers pre-mixed at the strike, one force
 * loop of 18; O9: no rh_x zero pass, the first live mode writes rh_x, 24 / 19, the others 26 / 20, each mode out of
 * line, ~35 per call; O6: the felt noise in a pass of its own, 16 per sample; O3: the pickup 43, with the fade 56).
 *   rh_cost blocks note vel [note ...]       the notes struck at block 0 with the pedal down (hil_cpu's worst case)
 * prints per block: "block live_modes instructions", then "max N" */
#include <stdio.h>
#include <stdlib.h>
#include "rhodes.c"

#if defined RH_COST_OLD || defined RH_COST_RC
#define RH_COST_INLINE 1        /* the mode loops inlined, rh_x zeroed, the felt noise in the fade loop */
#endif
#ifdef RH_COST_INLINE
#define C_MODE_F 29u            /* the mode loop while the hammer pushes (the sub-block started with a pulse) */
#define C_MODE 22u              /* the mode loop without the force */
#define C_PICK 58u              /* the pickup loop (no fade, no felt noise) */
#else
#define C_MODE_F 26u            /* this tree (rh_mode_f / rh_mode_p: 32-bit multipliers, zero-extended remainders) */
#define C_MODE 20u
#define C_MODE1_F 24u           /* this tree: the first live mode (writes rh_x), with / without the force */
#define C_MODE1 19u
#define C_CALL 35u              /* this tree: a mode function's call, entry and exit (rh_mode_f1 .. rh_mode_p) */
#define C_S4 48u                /* this tree: S4's E for a first live mode with kap (rh_mode_f1 / rh_mode_p1 before the loop call) */
#define C_PICK 43u              /* this tree: rh_pickup (the interleaved table, no fade) */
#endif
#ifdef RH_COST_OLD
#define C_PICK_FADE 99u         /* the pickup loop with the steal fade / felt noise (d8861bf: 99, 1 stack store) */
#elif defined RH_COST_RC
#define C_PICK_FADE 100u        /* (the RC: 100, no stack store) */
#else
#define C_PICK_FADE 56u         /* (this tree: rh_pickup_fx, the fade only) */
#endif
#define C_FELT 16u              /* this tree: the felt-noise pass */
#define C_ZERO 3u               /* a zeroing loop */

int main(int argc, char **argv)
{
    int blocks = atoi(argv[1]), vel = atoi(argv[3]), b, i;
    unsigned long mx = 0;
    static int32_t out[256];
    rh_pedal(1);
    for (i = 2; i < argc; i++) if (i != 3) rh_note_on((uint32_t)atoi(argv[i]), (uint32_t)vel);
    for (b = 0; b < blocks; b++) {
        unsigned long ins = 0, live = 0;
        uint32_t vi, sb, m;
        rh_events();
        for (vi = 0; vi < RH_NV; vi++) {        /* rh_render's voice loop, counted sub-block by sub-block */
            rh_voice_t *v = &rv[vi];
            for (sb = 0; sb < 256u && v->alive; sb += SUB) {
                uint32_t n = 0, ppos = v->ppos, mix = v->ptab == v->pbuf, fnz = v->ptab != 0, first = 1;
                if (fnz) {                      /* the pulse's samples in this sub-block */
                    while (n < SUB) { n++; ppos += v->prate; if ((ppos >> 16) >= RH_PLEN) break; }
#ifdef RH_COST_OLD
                    (void)mix;
                    ins += n * 39u + (SUB - n) * 7u;
#elif defined RH_COST_RC
                    ins += n * (mix ? 30u : 20u) + (SUB - n) * C_ZERO + SUB * C_ZERO;
#else
                    (void)mix;
                    ins += n * 18u + (SUB - n) * C_ZERO;
#endif
                }
#ifdef RH_COST_INLINE
                else
                    ins += SUB * C_ZERO;
                (void)first;
                for (m = 0; m < RH_NM; m++) if (v->m[m].live) { ins += SUB * (fnz ? C_MODE_F : C_MODE); live++; }
                ins += SUB * (v->fade == 32768 && !v->felt_n ? C_PICK : C_PICK_FADE);
#else
                for (m = 0; m < RH_NM; m++)
                    if (v->m[m].live) {
                        ins += SUB * (first ? (fnz ? C_MODE1_F : C_MODE1) : (fnz ? C_MODE_F : C_MODE)) + C_CALL;
                        if (first && v->m[m].kap) ins += C_S4;
                        first = 0; live++;
                    }
                if (first) ins += SUB * C_ZERO;
                ins += SUB * (v->fade == 32768 ? C_PICK : C_PICK_FADE) + (v->felt_n < SUB ? v->felt_n : SUB) * C_FELT;
#endif
                rh_voice_sub(v, rh_mono + sb, rh_side + sb);
            }
        }
        rh_render(out, 0);                      /* the life count and the summaries (n = 0: no sample runs) */
        if (ins > mx) mx = ins;
        printf("%d %lu %lu\n", b, live, ins);
    }
    printf("max %lu\n", mx);
    return 0;
}
