/* SPDX-License-Identifier: GPL-3.0-only */
/* Windows: the pthread calls the plugin makes (bridge.c's pthread_once for the resampler's table, over the system's
 * one-time initialisation, InitOnceExecuteOnce; its user-preset mutex, over an SRW lock). On the include path of the
 * Windows build only. */
#ifndef FRODHES_PLUGIN_WIN_PTHREAD_H
#define FRODHES_PLUGIN_WIN_PTHREAD_H
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
typedef INIT_ONCE pthread_once_t;
#define PTHREAD_ONCE_INIT INIT_ONCE_STATIC_INIT
static BOOL CALLBACK frp_once_cb(PINIT_ONCE once, PVOID fn, PVOID *ctx)
{
    (void)once; (void)ctx;
    ((void (*)(void))fn)();
    return TRUE;
}
static int pthread_once(pthread_once_t *once, void (*fn)(void))
{
    return InitOnceExecuteOnce(once, frp_once_cb, (PVOID)fn, 0) ? 0 : -1;
}
/* bridge.c's user-preset lock (main thread and the UI): a static mutex over a slim reader/writer lock */
typedef SRWLOCK pthread_mutex_t;
#define PTHREAD_MUTEX_INITIALIZER SRWLOCK_INIT
static int pthread_mutex_lock(pthread_mutex_t *m) { AcquireSRWLockExclusive(m); return 0; }
static int pthread_mutex_unlock(pthread_mutex_t *m) { ReleaseSRWLockExclusive(m); return 0; }
#endif
