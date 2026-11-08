/* SPDX-License-Identifier: GPL-3.0-only */
/* Where the plugin keeps the user's files, per OS, and the few file calls that differ on Windows (plugin/src/frp_paths.c).
 *
 *   macOS    ~/Library/Application Support/Frodhes/            Presets/  settings.json
 *            in the App Sandbox (a sandboxed Audio Unit host: APP_SANDBOX_CONTAINER_ID is set, HOME is the
 *            container's ~/Library/Containers/<id>/Data): the account's own folder above when it exists and can be
 *            written (the same presets as every other host), else the container's (the host's own presets only)
 *   Linux    $XDG_DATA_HOME/frodhes/ (an absolute path), else ~/.local/share/frodhes/     presets/  settings.json
 *   Windows  %APPDATA%\Frodhes\ (else %USERPROFILE%\AppData\Roaming\Frodhes\)          Presets\  settings.json
 *   FRODHES_PRESET_DIR, when set, is the preset folder on every OS (the tests).
 *
 * The path logic is pure (frp_path_* take the OS and an frp_env_t): every OS's rules are tested on any OS
 * (plugin/tests/test_paths.c). Paths are UTF-8 everywhere; on Windows the file calls convert to UTF-16 (the "W"
 * calls), so a user name outside the ANSI code page works. */
#ifndef FRP_PATHS_H
#define FRP_PATHS_H
#include <stddef.h>
#include <stdio.h>

enum { FRP_OS_MACOS, FRP_OS_LINUX, FRP_OS_WINDOWS };
#if defined(_WIN32)
#define FRP_OS_HERE FRP_OS_WINDOWS
#elif defined(__APPLE__)
#define FRP_OS_HERE FRP_OS_MACOS
#else
#define FRP_OS_HERE FRP_OS_LINUX
#endif

typedef struct {
    const char *home;                    /* HOME (Windows: USERPROFILE); 0 or "" none */
    const char *appdata;                 /* APPDATA (Windows) */
    const char *xdg_data;                /* XDG_DATA_HOME (Linux) */
    const char *sandbox;                 /* APP_SANDBOX_CONTAINER_ID (macOS: set inside the App Sandbox) */
    const char *real_home;               /* the account's home from the user database (macOS: outside a container) */
    const char *preset_dir;              /* FRODHES_PRESET_DIR */
    int (*usable)(const char *dir);      /* the folder exists and can be written (the sandbox's choice); 0: never */
} frp_env_t;

/* the folder of the plugin's files, its preset folder, its settings file: 0 ok, -1 none (no home known, or too long:
 * out is "") */
int frp_path_data(int os, const frp_env_t *e, char *out, size_t n);
int frp_path_presets(int os, const frp_env_t *e, char *out, size_t n);
int frp_path_settings(int os, const frp_env_t *e, char *out, size_t n);
/* the length of the path's root, which is never created: "/" 1; Windows "C:\" 3, "\\server\share\" up to the share's
 * separator; 0 a relative path */
size_t frp_path_root(int os, const char *path);
char frp_path_sep(int os);

void frp_env_here(frp_env_t *e);         /* this process's environment (the strings live until the process ends) */
int frp_dir_usable(const char *dir);     /* a folder that exists and can be written */

/* mkdir -p: every missing folder of path, 0 ok (they exist), -1 not */
int frp_mkdirs(const char *path);
/* fopen of a UTF-8 path */
FILE *frp_fopen(const char *path, const char *mode);
/* tmp over path at once (the atomic save): POSIX rename; Windows MoveFileExW with replace (rename() refuses an
 * existing file there). 0 ok */
int frp_replace(const char *tmp, const char *path);
int frp_remove(const char *path);
/* each entry's name of a folder (not "." or ".."): fn returns nonzero to stop. 0 ok, -1 the folder cannot be read */
int frp_list_dir(const char *dir, int (*fn)(const char *name, void *u), void *u);
#endif
