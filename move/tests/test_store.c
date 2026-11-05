/* SPDX-License-Identifier: GPL-3.0-only */
/* move/move_store.h (M1-08): the save is atomic. A child saves records A and B in turn, without end; the parent
 * kills it (SIGKILL) at a random moment, N times. After every kill the file loads, and holds A or B, never a mix;
 * a torn temporary file left behind does not count. Then: a missing file, a torn file, a file of another size and
 * a flipped byte all load as "none" (-1), and the data are left for the defaults.
 *   test_store DIR [N]   (DIR is made; N kills, default 40) */
#include <signal.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <time.h>
#include "../move_store.h"

#define RN 1796u
static uint8_t A[RN], B[RN], got[RN];
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "build/move/t/store";
    int n = argc > 2 ? atoi(argv[2]) : 40, a = 0, b = 0, tmp_left = 0;
    char path[600];
    for (unsigned i = 0; i < RN; i++) { A[i] = (uint8_t)(i * 7u + 1u); B[i] = (uint8_t)(255u - i * 13u); }
    CHECK(ms_mkdirs(dir) == 0, "mkdir %s", dir);
    CHECK(ms_save(dir, "store.bin", A, RN) == 0, "first save");
    srand((unsigned)time(0));
    for (int k = 0; k < n; k++) {
        pid_t c = fork();
        if (c == 0) { for (unsigned j = 0;; j++) (void)ms_save(dir, "store.bin", j & 1u ? A : B, RN); }
        { struct timespec t = { 0, (long)(rand() % 20000) * 1000L }; nanosleep(&t, 0); }   /* 0 .. 20 ms */
        kill(c, SIGKILL);
        waitpid(c, 0, 0);
        snprintf(path, sizeof path, "%s/.store.bin.tmp", dir);
        if (access(path, F_OK) == 0) tmp_left++;
        memset(got, 0, RN);
        int r = ms_load(dir, "store.bin", got, RN);
        CHECK(r == (int)RN, "kill %d: the file loads (%d)", k, r);
        if (!memcmp(got, A, RN)) a++; else if (!memcmp(got, B, RN)) b++; else CHECK(0, "kill %d: neither A nor B", k);
    }
    /* the refusals */
    {
        FILE *f;
        CHECK(ms_load(dir, "none.bin", got, RN) == -1, "a missing file");
        CHECK(ms_save(dir, "x.bin", A, RN) == 0, "save x");
        CHECK(ms_load(dir, "x.bin", got, RN - 1) == -1, "another size");
        snprintf(path, sizeof path, "%s/x.bin", dir);
        f = fopen(path, "r+b"); fseek(f, 100, SEEK_SET); fputc(A[84] ^ 0x10, f); fclose(f);
        CHECK(ms_load(dir, "x.bin", got, RN) == -1, "a flipped byte");
        CHECK(truncate(path, 16 + RN / 2) == 0, "truncate");
        CHECK(ms_load(dir, "x.bin", got, RN) == -1, "a torn file");
        unlink(path);
    }
    printf("%s    store: %d kills mid-save, the file always whole (%d A, %d B, %d torn temporaries ignored); "
           "missing, short, torn and flipped files refused\n", fails ? "FAIL" : "ok", n, a, b, tmp_left);
    return fails != 0;
}
