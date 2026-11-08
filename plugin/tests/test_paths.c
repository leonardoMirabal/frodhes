/* SPDX-License-Identifier: GPL-3.0-only */
/* The user folders per OS (plugin/src/frp_paths.c): every OS's rules, tested on any OS (the logic takes the OS and
 * the environment as arguments), then this OS's mkdir -p, atomic replace, listing and removal in a scratch folder.
 *   cc -I plugin/src plugin/tests/test_paths.c plugin/src/frp_paths.c -o test_paths && ./test_paths SCRATCH_DIR
 * (tests/py/test_plugin_paths.py builds and runs it) */
#include <stdio.h>
#include <string.h>
#include "frp_paths.h"

static int fails;
#define EXPECT(c, ...) do { if (!(c)) { fails++; printf("FAIL "); printf(__VA_ARGS__); printf("\n"); } } while (0)
static int yes(const char *d) { (void)d; return 1; }
static int no(const char *d) { (void)d; return 0; }
static void path_is(int os, const frp_env_t *e, int which, const char *want)
{
    char out[1024];
    int rc = which == 0 ? frp_path_data(os, e, out, sizeof out) : which == 1 ? frp_path_presets(os, e, out, sizeof out)
                                                                  : frp_path_settings(os, e, out, sizeof out);
    if (!want) EXPECT(rc == -1 && out[0] == 0, "os %d kind %d: none expected, got '%s'", os, which, out);
    else EXPECT(rc == 0 && !strcmp(out, want), "os %d kind %d: '%s', want '%s'", os, which, out, want);
}
static int count_cb(const char *name, void *u) { if (strstr(name, ".frpreset")) (*(int *)u)++; return 0; }

int main(int argc, char **argv)
{
    frp_env_t e;
    char small[8];
    /* macOS */
    memset(&e, 0, sizeof e);
    e.home = "/Users/ana";
    path_is(FRP_OS_MACOS, &e, 0, "/Users/ana/Library/Application Support/Frodhes");
    path_is(FRP_OS_MACOS, &e, 1, "/Users/ana/Library/Application Support/Frodhes/Presets");
    path_is(FRP_OS_MACOS, &e, 2, "/Users/ana/Library/Application Support/Frodhes/settings.json");
    e.home = "/Users/ana/";                                 /* (no doubled separator) */
    path_is(FRP_OS_MACOS, &e, 1, "/Users/ana/Library/Application Support/Frodhes/Presets");
    /* the App Sandbox: HOME is the container's; the account's folder when it is usable, else the container's */
    e.home = "/Users/ana/Library/Containers/com.example.host/Data";
    e.sandbox = "com.example.host"; e.real_home = "/Users/ana"; e.usable = yes;
    path_is(FRP_OS_MACOS, &e, 1, "/Users/ana/Library/Application Support/Frodhes/Presets");
    e.usable = no;
    path_is(FRP_OS_MACOS, &e, 1, "/Users/ana/Library/Containers/com.example.host/Data/Library/Application Support/Frodhes/Presets");
    e.usable = 0;
    path_is(FRP_OS_MACOS, &e, 0, "/Users/ana/Library/Containers/com.example.host/Data/Library/Application Support/Frodhes");
    e.sandbox = 0; e.usable = yes;                          /* (no sandbox: HOME, even if real_home differs) */
    path_is(FRP_OS_MACOS, &e, 0, "/Users/ana/Library/Containers/com.example.host/Data/Library/Application Support/Frodhes");
    memset(&e, 0, sizeof e);
    path_is(FRP_OS_MACOS, &e, 1, 0);                        /* no home at all */
    e.preset_dir = "/tmp/p";
    path_is(FRP_OS_MACOS, &e, 1, "/tmp/p");                 /* FRODHES_PRESET_DIR wins */
    path_is(FRP_OS_MACOS, &e, 2, 0);                        /* (and is not the settings' folder) */
    /* Linux */
    memset(&e, 0, sizeof e);
    e.home = "/home/ana";
    path_is(FRP_OS_LINUX, &e, 0, "/home/ana/.local/share/frodhes");
    path_is(FRP_OS_LINUX, &e, 1, "/home/ana/.local/share/frodhes/presets");
    path_is(FRP_OS_LINUX, &e, 2, "/home/ana/.local/share/frodhes/settings.json");
    e.xdg_data = "/data/ana";
    path_is(FRP_OS_LINUX, &e, 1, "/data/ana/frodhes/presets");
    e.xdg_data = "relative/dir";                            /* (the spec: ignored) */
    path_is(FRP_OS_LINUX, &e, 1, "/home/ana/.local/share/frodhes/presets");
    e.xdg_data = "";
    path_is(FRP_OS_LINUX, &e, 1, "/home/ana/.local/share/frodhes/presets");
    e.home = 0;
    path_is(FRP_OS_LINUX, &e, 1, 0);
    /* Windows */
    memset(&e, 0, sizeof e);
    e.appdata = "C:\\Users\\Ana\\AppData\\Roaming"; e.home = "C:\\Users\\Ana";
    path_is(FRP_OS_WINDOWS, &e, 0, "C:\\Users\\Ana\\AppData\\Roaming\\Frodhes");
    path_is(FRP_OS_WINDOWS, &e, 1, "C:\\Users\\Ana\\AppData\\Roaming\\Frodhes\\Presets");
    path_is(FRP_OS_WINDOWS, &e, 2, "C:\\Users\\Ana\\AppData\\Roaming\\Frodhes\\settings.json");
    e.appdata = "C:\\Users\\J\xc3\xbcrgen\\AppData\\Roaming\\";   /* (UTF-8 kept; the trailing separator dropped) */
    path_is(FRP_OS_WINDOWS, &e, 1, "C:\\Users\\J\xc3\xbcrgen\\AppData\\Roaming\\Frodhes\\Presets");
    e.appdata = 0;
    path_is(FRP_OS_WINDOWS, &e, 1, "C:\\Users\\Ana\\AppData\\Roaming\\Frodhes\\Presets");
    e.home = 0;
    path_is(FRP_OS_WINDOWS, &e, 1, 0);
    /* too long: none, never cut */
    memset(&e, 0, sizeof e);
    e.home = "/Users/ana";
    EXPECT(frp_path_presets(FRP_OS_MACOS, &e, small, sizeof small) == -1 && small[0] == 0, "a path too long is refused");
    /* the roots mkdir -p never creates */
    EXPECT(frp_path_root(FRP_OS_LINUX, "/a/b") == 1 && frp_path_root(FRP_OS_LINUX, "a/b") == 0, "POSIX roots");
    EXPECT(frp_path_root(FRP_OS_WINDOWS, "C:\\a\\b") == 3 && frp_path_root(FRP_OS_WINDOWS, "c:/a") == 3 &&
           frp_path_root(FRP_OS_WINDOWS, "C:a") == 2, "drive roots");
    EXPECT(frp_path_root(FRP_OS_WINDOWS, "\\\\server\\share\\a\\b") == 15, "UNC root (%zu)",
           frp_path_root(FRP_OS_WINDOWS, "\\\\server\\share\\a\\b"));
    EXPECT(frp_path_root(FRP_OS_WINDOWS, "\\a") == 1 && frp_path_root(FRP_OS_WINDOWS, "a\\b") == 0, "Windows others");
    /* this OS: mkdir -p, the atomic replace (over an existing file too), the listing, the removal */
    if (argc > 1) {
        char d[1100], f[1200], t[1300], s = frp_path_sep(FRP_OS_HERE);
        FILE *x;
        int n = 0;
        snprintf(d, sizeof d, "%s%cdeep%cer%cPresets", argv[1], s, s, s);
        EXPECT(frp_mkdirs(d) == 0 && frp_dir_usable(d), "mkdir -p %s", d);
        EXPECT(frp_mkdirs(d) == 0, "mkdir -p again (it exists)");
        snprintf(f, sizeof f, "%s%cA.frpreset", d, s);
        snprintf(t, sizeof t, "%s.tmp", f);
        for (int round = 0; round < 2; round++) {           /* (the second: over the first) */
            EXPECT((x = frp_fopen(t, "wb")) != 0, "write %s", t);
            if (x) { fprintf(x, "round %d", round); fclose(x); }
            EXPECT(frp_replace(t, f) == 0, "replace round %d", round);
        }
        if ((x = frp_fopen(f, "rb")) != 0) { char b[16] = {0}; fread(b, 1, sizeof b - 1, x); fclose(x); EXPECT(!strcmp(b, "round 1"), "the replaced file holds round 1 ('%s')", b); }
        else EXPECT(0, "read back %s", f);
        EXPECT(frp_list_dir(d, count_cb, &n) == 0 && n == 1, "one preset listed (%d)", n);
        EXPECT(frp_remove(f) == 0, "remove");
        n = 0;
        EXPECT(frp_list_dir(d, count_cb, &n) == 0 && n == 0, "none listed after the removal");
        snprintf(f, sizeof f, "%s%cnone", d, s);
        EXPECT(frp_list_dir(f, count_cb, &n) == 0 && n == 0, "a folder not made yet: no files, no error");
        snprintf(f, sizeof f, "%s%cA.frpreset", d, s);
        EXPECT((x = frp_fopen(f, "wb")) != 0, "a file where a folder should go");
        if (x) fclose(x);
        snprintf(t, sizeof t, "%s%cx", f, s);
        EXPECT(frp_mkdirs(t) == -1, "mkdir -p through a file fails");
        frp_remove(f);
    }
    if (!fails) printf("ok    paths: macOS (and its App Sandbox), Linux (XDG), Windows (APPDATA, UNC, drive roots)%s\n",
                       argc > 1 ? "; mkdir -p, the atomic replace, the listing on this OS" : "");
    return fails ? 1 : 0;
}
