/* SPDX-License-Identifier: GPL-3.0-only (from genkigenki/sloop-fm1 smk37; OberVave: SMK_PW_ST 2) */
/* SMK-37 Pro pitch wheel (firmware/src/smk_wheel.h): the bend is back to 0 once the wheel rests again, however
 * it was moved. v0.1 moved the centre by one count every 200 calls; the main loop calls it some 10 000 to
 * 50 000 times a second, so a slow touch dragged the centre along, the spring brought the wheel back outside
 * the dragged dead band and a bend of several semitones stayed on, on whichever synth track was selected:
 * switching tracks took it off one track and put it on the next ("track 1 transposes when I change track"). */
#include <stdio.h>
#include "../firmware/src/smk_wheel.h"
static int fails;
static void check(int ok, const char *what) { printf("smk_wheel: %-66s %s\n", what, ok ? "ok" : "FAIL"); fails += !ok; }

/* v0.1 (main.c smk_wheels before the fix): the centre's drift counted in calls */
typedef struct { int32_t pc, plo, phi, rest; } old_t;
static int32_t old_bend(old_t *w, int32_t p)
{
    int32_t d, b;
    if (!w->pc) {
        w->pc = p;
        w->plo = p - SMK_PW_SPAN0;
        w->phi = p + SMK_PW_SPAN0;
    }
    if (p < w->plo) w->plo = p;
    if (p > w->phi) w->phi = p;
    d = p - w->pc;
    if (d > -SMK_PW_DEAD && d < SMK_PW_DEAD) {
        b = 0;
        if (++w->rest > 200) {
            w->pc += d > 0 ? 1 : d < 0 ? -1 : 0;
            w->rest = 0;
        }
    } else {
        w->rest = 0;
        b = d > 0 ? (d - SMK_PW_DEAD) * (SMK_PW_ST * 4096) / (w->phi - w->pc - SMK_PW_DEAD > 1 ? w->phi - w->pc - SMK_PW_DEAD : 1)
                  : (d + SMK_PW_DEAD) * (SMK_PW_ST * 4096) / (w->pc - w->plo - SMK_PW_DEAD > 1 ? w->pc - w->plo - SMK_PW_DEAD : 1);
        b = b < -SMK_PW_ST * 4096 ? -SMK_PW_ST * 4096 : b > SMK_PW_ST * 4096 ? SMK_PW_ST * 4096 : b;
    }
    return b;
}

#define CALLS_PER_MS 20           /* main loop: ui_input() + smk_wheels() some 20 000 times a second */
#define REST 540
static uint32_t now;
static smk_pw_t nw;
static old_t ow;
static int32_t nb, ob;
/* move the wheel linearly from a to b over ms milliseconds (the ADC's /4 IIR: a reading every 2.4 ms) */
static void move(int32_t a, int32_t b, uint32_t ms)
{
    uint32_t t, c;
    int32_t f = a;
    for (t = 0; t < ms; t++, now++) {
        int32_t raw = ms ? a + (b - a) * (int32_t)t / (int32_t)ms : b;
        if (t % 2u == 0u)
            f += (raw - f) / 4;
        for (c = 0; c < CALLS_PER_MS; c++) {
            nb = smk_pw_bend(&nw, f, now);
            ob = old_bend(&ow, f);
        }
    }
}
static void reset(void)
{
    smk_pw_t z = {0};
    old_t zo = {0};
    nw = z;
    ow = zo;
    now = 1000;
    move(REST, REST, 500);                      /* calibrated at rest */
}

int main(void)
{
    char b[96];
    reset();
    check(nb == 0 && ob == 0, "at rest: no bend");
    move(REST, REST + 100, 60);                 /* a quick bend to the top */
    move(REST + 100, REST + 100, 200);
    check(nb == SMK_PW_ST * 4096, "a quick bend to the top: +SMK_PW_ST semitones (OberVave: 2)");
    move(REST + 100, REST, 40);                 /* the spring */
    move(REST, REST, 1000);
    check(nb == 0, "let go: back to 0");

    reset();                                    /* the report: a slow touch of the wheel, then the spring */
    move(REST, REST + 60, 1500);
    move(REST + 60, REST + 60, 300);
    move(REST + 60, REST, 40);
    move(REST, REST, 3000);
    snprintf(b, sizeof b, "v0.1: a slow touch leaves a bend at rest (%d/4096 st)", (int)ob);
    check(ob != 0, b);                          /* (shows the fault: the old algorithm sticks) */
    check(nb == 0, "fixed: a slow touch, then let go: back to 0");

    reset();                                    /* very slow, in both directions */
    move(REST, REST - 70, 4000);
    move(REST - 70, REST, 30);
    move(REST, REST, 2000);
    check(nb == 0, "fixed: a very slow bend down, then let go: back to 0");

    reset();                                    /* a held bend stays */
    move(REST, REST + 50, 100);
    move(REST + 50, REST + 50, 5000);
    check(nb > 0, "fixed: a bend held for 5 s stays");
    move(REST + 50, REST, 40);
    move(REST, REST, 1000);
    check(nb == 0, "fixed: ... and goes when let go");

    reset();                                    /* the rest drifts (temperature, supply): followed */
    move(REST, REST + 8, 20000);
    move(REST + 8, REST + 8, 5000);
    move(REST + 8, REST + 20, 60000);
    move(REST + 20, REST + 20, 5000);
    check(nb == 0 && nw.pc >= REST + 15, "fixed: a slow drift of the rest is followed (no bend)");

    /* 0.9.6 (#29, "the pitch wheel stops after the pedal"): spikes on the analog inputs (a jack going in) */
    reset();                                    /* the full travel learnt: REST +- 400 */
    move(REST, REST + 400, 60); move(REST + 400, REST + 400, 200); move(REST + 400, REST, 40);
    move(REST, REST - 400, 60); move(REST - 400, REST - 400, 200); move(REST - 400, REST, 40);
    move(REST, REST, 500);
    check(nw.phi >= REST + 390 && nw.plo <= REST - 390, "a held full travel widens the range");
    move(REST, 1023, 1); move(1023, 1023, 10); move(1023, 0, 1); move(0, 0, 10);   /* a 20 ms spike, both ends */
    move(0, REST, 1); move(REST, REST, 500);
    check(nb == 0 && nw.pc >= REST - 2 && nw.pc <= REST + 2, "a 20 ms spike: the centre stays, no bend at rest");
    check(nw.phi < 1000 && nw.plo > 50, "a 20 ms spike does not widen the range");
    move(REST, REST + 400, 60); move(REST + 400, REST + 400, 200);
    check(nb == SMK_PW_ST * 4096, "after the spike: the full travel up still bends fully");
    snprintf(b, sizeof b, "before 0.9.6 (the range only grew): after the spike, up bends %d/4096 st", (int)ob);
    check(ob < SMK_PW_ST * 4096, b);            /* (shows the fault: less than the full bend) */
    move(REST + 400, REST, 40); move(REST, REST - 400, 60); move(REST - 400, REST - 400, 200);
    check(nb == -SMK_PW_ST * 4096, "after the spike: the full travel down still bends fully");
    move(REST - 400, REST, 40); move(REST, REST, 1000);
    check(nb == 0, "... and back to 0");
    move(REST, 0, 60); move(0, 0, 200);         /* the true end of the travel (reading 0) held: taken */
    check(nw.plo <= 2, "a held reading of 0 widens the range to it");

    {                                           /* power-on during a spike: the centre waits for a still reading */
        smk_pw_t z = {0};
        old_t zo = {0};
        int32_t i;
        nw = z, ow = zo, now = 1000;
        for (i = 0; i < 6; i++) { move(i & 1 ? 0 : 1023, i & 1 ? 0 : 1023, 5); }   /* 30 ms of bounce */
        move(REST, REST, 500);
        check(nb == 0 && nw.pc >= REST - 2 && nw.pc <= REST + 2, "power-on in a spike: the centre is the rest");
        snprintf(b, sizeof b, "before 0.9.6: the centre from the spike (pc %d): a bend at rest (%d/4096 st)", (int)ow.pc, (int)ob);
        check(ob != 0, b);                      /* (shows the fault) */
        move(REST, REST + 400, 60); move(REST + 400, REST + 400, 200);
        check(nb == SMK_PW_ST * 4096, "power-on in a spike: then a full bend up");
    }

    {                                           /* a noisy input that never holds still: calibrated after 1 s */
        smk_pw_t z = {0};
        int32_t i;
        nw = z, now = 1000;
        for (i = 0; i < 300; i++) { move(REST + (i & 1 ? 6 : -6), REST + (i & 1 ? 6 : -6), 5); }
        check(nw.pc != 0 && nw.pc >= REST - 6 && nw.pc <= REST + 6, "a noisy input: still calibrated (after 1 s)");
    }

    printf("smk_wheel: %s\n", fails ? "FAILED" : "all ok");
    return fails != 0;
}
