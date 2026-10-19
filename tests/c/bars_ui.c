/* SPDX-License-Identifier: GPL-3.0-only */
/* The VIBES UI, MIDI and record compatibility, tested on the firmware's own functions (tests/py/test_bars_compat.py).
 * The whole app is included (one compilation unit, as on the FM-1) with tests/host.c's main renamed:
 *
 *   cc -DDINGHY_HOST -Dmain=host_main -Ifirmware/src -Ifirmware/gen tests/c/bars_ui.c -lm
 *   bars_ui labels | midi | conv          the new tree: card labels and values, CC / PC banding, preset index
 *   bars_ui dump FILE TYPE MODE PRESET    the settings record + the preset bank (U01 = this panel) to FILE
 *   bars_ui load FILE                     FILE's records through this tree's sanitize / bank load / preset load
 *
 * dump / load use only what every version since 0.4 has, so the same file also builds against an older tree (the
 * compatibility test feeds a VIBES record to the firmware before VIBES). Output: one "key value" line each. */
#include "frodhes.c"
#undef main

static void put_settings(void) { settings_save(&set, sizeof set); }

#ifndef BARS_UI_OLD
static void show_page(const char *tag, uint32_t pg, uint32_t vp)   /* (VOICE: its page vp; past the engine's last: none) */
{
    uint32_t k;
    if (pg == PG_VOICE && vp >= vp_count()) return;
    ui_vp = (uint8_t)vp;
    for (k = 0; k < 4u; k++) {
        char v[24];
        uint32_t s = slot_at_v(pg, vp, k);
        slot_text(s, v);
        printf("%s.%u %s=%s\n", tag, k, slot_label_v(pg, vp, k), v);
    }
    printf("%s.hint %s\n", tag, page_hint(pg));
    ui_vp = 0;
}
static void cmd_labels(void)
{
    static const uint8_t TONES[] = {0, 32, 64, 96, 127}, BARKS[] = {0, 3, 20, 42, 85, 127};
    uint32_t t, i;
    for (t = 0; t < N_TYPE_N; t++) {
        char tag[16];
        param_set(P_TYPE, (int32_t)t);
        printf("type.%u %s bars=%d\n", t, N_TYPE_LONG[t], ui_bars());
        snprintf(tag, sizeof tag, "voice%u", t); show_page(tag, PG_VOICE, 0);   /* VOICE 1 */
        snprintf(tag, sizeof tag, "decay%u", t); show_page(tag, PG_VOICE, 1);   /* VOICE 2 (was DECAY) */
        snprintf(tag, sizeof tag, "motor%u", t); show_page(tag, PG_VOICE, 2);   /* VOICE 3 (was MOTOR): bars only */
        printf("pages%u vpages=%u count=%u comp_pos=%u\n", t, vp_count(), pg_count(), pg_pos(PG_COMP, 0));
        snprintf(tag, sizeof tag, "perf%u", t);
        for (i = 0; i < PERF_N; i++) printf("%s.%u %s\n", tag, i, perf_name(i));
        for (i = 0; i < sizeof TONES; i++) { char v[16]; param_set(P_TONE, TONES[i]); slot_text(P_TONE, v); printf("tone%u.%u %s\n", t, TONES[i], v); }
        for (i = 0; i < sizeof BARKS; i++) { char v[16]; param_set(P_BARK, BARKS[i]); slot_text(P_BARK, v); printf("bark%u.%u %s\n", t, BARKS[i], v); }
        param_set(P_TONE, 64); param_set(P_BARK, 64);
        /* TREM/WAH: K1 from OFF all the way right, then all the way left */
        param_set(P_TW_SW, 0); param_set(P_TW_MODE, 0);
        for (i = 0; i < 6u; i++) {
            char v[16], r[16];
            slot_text(S_TWKIND, v); slot_text(P_TW_RATE, r);
            printf("kind%u.up%u %s sw=%u mode=%u fxsw=%d fxmode=%d rate=%s\n", t, i, v, PV(P_TW_SW), PV(P_TW_MODE),
                   (int)fx_p[FXP_TW_SW], (int)fx_p[FXP_TW_MODE], r);
            slot_turn(S_TWKIND, 1);
        }
        for (i = 0; i < 6u; i++) {
            char v[16];
            slot_text(S_TWKIND, v);
            printf("kind%u.down%u %s\n", t, i, v);
            slot_turn(S_TWKIND, -1);
        }
    }
    /* the motor on, then a Rhodes TYPE: TREM/WAH stays as it was (off): no tremolo comes on */
    param_set(P_TW_SW, 0); param_set(P_TYPE, 4); param_set(P_MOT_ON, 1); param_set(P_TYPE, 0);
    {
        char v[16];
        slot_text(S_TWKIND, v);
        printf("motor_on_rhodes %s fxsw=%d fxmode=%d\n", v, (int)fx_p[FXP_TW_SW], (int)fx_p[FXP_TW_MODE]);
    }
    for (i = 0; i <= 127u; i += 1u) {                   /* the MOTOR page's SPEED text */
        char v[16];
        param_set(P_TYPE, 4); param_set(P_MOT_SYNC, 0); param_set(P_MOT_RATE, (int32_t)i);
        slot_text(P_MOT_RATE, v);
        printf("motrate.%u %s\n", i, v);
    }
}
static void cmd_midi(void)
{
    uint32_t v, t;
    for (v = 0; v < 128u; v++) { midi_cc(80, (uint8_t)v); printf("cc80.%u %u\n", v, PV(P_TYPE)); }
    for (v = 0; v < 128u; v++) { midi_cc(79, (uint8_t)v); printf("cc79.%u %u\n", v, PV(P_TYPE)); }
    for (t = 0; t < N_TYPE_N; t++) {
        param_set(P_TYPE, (int32_t)t);
        for (v = 0; v < 128u; v++) { midi_cc(21, (uint8_t)v); printf("cc21.%u.%u %u\n", t, v, PV(P_TW_MODE)); }
    }
    for (v = 0; v < 40u; v++) {                          /* program change, as app_main does it */
        uint32_t d1 = v;
        set.preset = 200;
        preset_load(d1 < PR_NF ? d1 : d1 >= 16u && d1 < 32u ? PR_NF + d1 - 16u : 255u);
        printf("pc.%u %u type=%u\n", v, set.preset, PV(P_TYPE));
    }
    {                                                    /* the factory VIBES presets */
        uint32_t i;
        for (i = 12; i < PR_NF; i++) {
            char nm[20];
            preset_load(i); preset_name(i, nm);
            printf("factory.%u %s type=%u sw=%u mode=%u mot=%u depth=%u rate=%u shape=%u rev=%u\n", i, nm, PV(P_TYPE),
                   PV(P_TW_SW), PV(P_TW_MODE), PV(P_MOT_ON), PV(P_MOT_DEPTH), PV(P_MOT_RATE), PV(P_MOT_SHAPE), PV(P_RV_DEPTH));
        }
    }
}
static void cmd_conv(void)
{
    static const uint8_t PRE[] = {0, 5, 11, 12, 13, 14, 15, 20, 27, 28, 29, 30, 31, 200};
    static const char *const F[] = {"old", "v093", "new"};   /* before VIBES (12 factory), 0.9.3 (14), today (16) */
    uint32_t i, f;
    for (f = 0; f < 3u; f++)
        for (i = 0; i < sizeof PRE; i++) {
            params_defaults();
            set.flags = (uint8_t)(f == 2u ? 1u | 2u | 4u | 8u | 32u : f == 1u ? 1u | 2u | 4u | 8u : 1u | 2u | 8u);
            set.preset = PRE[i];
            set.undo_preset = (uint8_t)(PRE[i] + 1u);
            put_settings();
            memset(&set, 0, sizeof set);
            settings_load(&set, sizeof set);
            params_sanitize();
            printf("conv.%s.%u %u flags4=%u flags32=%u undo=%u\n", F[f], PRE[i], set.preset, (set.flags & 4u) != 0,
                   (set.flags & 32u) != 0, set.undo_preset);
            params_sanitize();                           /* a second boot: nothing moves again */
            printf("conv2.%s.%u %u\n", F[f], PRE[i], set.preset);
        }
    params_defaults();
    printf("fresh.flags4 %u\n", (set.flags & 4u) != 0);
    printf("fresh.flags32 %u\n", (set.flags & 32u) != 0);
}
#endif

static void cmd_dump(const char *path, int type, int mode, int preset)
{
    FILE *f = fopen(path, "wb");
    uint32_t i;
    params_defaults();
    params_sanitize();
    PV(P_TYPE) = (uint8_t)type; PV(P_TW_SW) = 1; PV(P_TW_MODE) = (uint8_t)mode; PV(P_RV_DEPTH) = 33;
    set.preset = (uint8_t)preset;
    put_settings();
    pr_user[0].ver = 2; pr_user[0].np = P_COUNT; pr_user[0].used = 1;
    memcpy(pr_user[0].name, "VIBE MOT    ", 12);
    for (i = 0; i < P_SLOTS; i++) pr_user[0].p[i] = i < P_COUNT ? PV(i) : 0;
    presets_save_bank();
    fwrite(host_store, 1, sizeof host_store, f);
    fwrite(host_bank, 1, sizeof host_bank, f);
    fclose(f);
}
static void cmd_load(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f || fread(host_store, 1, sizeof host_store, f) != sizeof host_store ||
        fread(host_bank, 1, sizeof host_bank, f) != sizeof host_bank) { printf("error read\n"); return; }
    fclose(f);
    if (settings_load(&set, sizeof set) != 0) params_defaults();
    params_sanitize();
    params_apply_all();
    printf("set.type %u\nset.tw_mode %u\nset.tw_sw %u\nset.rev %u\nset.preset %u\nset.flags %u\n", PV(P_TYPE),
           PV(P_TW_MODE), PV(P_TW_SW), PV(P_RV_DEPTH), set.preset, set.flags);
    printf("rh_type %u\nfx.tw_sw %d\nfx.tw_mode %d\n", rh_type, (int)fx_p[FXP_TW_SW], (int)fx_p[FXP_TW_MODE]);
    presets_load_bank();
    printf("u01.used %u\n", pr_user[0].used);
    preset_load(PR_NF);
    printf("u01.type %u\nu01.tw_mode %u\nu01.tw_sw %u\nu01.rev %u\nu01.preset %u\n", PV(P_TYPE), PV(P_TW_MODE),
           PV(P_TW_SW), PV(P_RV_DEPTH), set.preset);
}

int main(int argc, char **argv)
{
    params_defaults();
    params_sanitize();
    params_apply_all();
    if (argc > 1 && !strcmp(argv[1], "dump") && argc > 5) cmd_dump(argv[2], atoi(argv[3]), atoi(argv[4]), atoi(argv[5]));
    else if (argc > 2 && !strcmp(argv[1], "load")) cmd_load(argv[2]);
#ifndef BARS_UI_OLD
    else if (argc > 1 && !strcmp(argv[1], "labels")) cmd_labels();
    else if (argc > 1 && !strcmp(argv[1], "midi")) cmd_midi();
    else if (argc > 1 && !strcmp(argv[1], "conv")) cmd_conv();
#endif
    else { fprintf(stderr, "usage: bars_ui labels|midi|conv|dump FILE TYPE MODE PRESET|load FILE\n"); return 2; }
    return 0;
}
