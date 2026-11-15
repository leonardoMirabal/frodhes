/* SPDX-License-Identifier: GPL-3.0-only */
/* CHECK FOR UPDATES on macOS (plugin/src/frp_update.h): the request through NSURLSession (Foundation: TLS verified
 * by the system, an ephemeral session: no cookie, no cache, no credential), the browser and the package through
 * NSWorkspace.
 * Manual reference counting, as plugin/gui_mac.m. */
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#include <pthread.h>
#include <string.h>
#include "frp_update.h"

static pthread_mutex_t mk = PTHREAD_MUTEX_INITIALIZER;
static NSURLSessionDataTask *cur;                /* the request under way (retained), for frp_update_fetch_cancel */

int frp_update_fetch(const char *url, char *buf, size_t cap, size_t *len, int timeout_ms, int follow)
{
    __block int status = -1;
    __block size_t got = 0;
    *len = 0;
    if (!cap) return -1;
    buf[0] = 0;
    @autoreleasepool {
        NSURLSessionConfiguration *cfg = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        NSURL *u = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
        NSMutableURLRequest *rq;
        NSURLSession *s;
        NSURLSessionDataTask *t;
        dispatch_semaphore_t sem;
        if (!u || ![[u scheme] isEqualToString:@"https"]) return -1;
        cfg.timeoutIntervalForRequest = timeout_ms / 1000.0;
        cfg.timeoutIntervalForResource = timeout_ms / 1000.0;
        cfg.HTTPCookieStorage = nil;
        cfg.HTTPShouldSetCookies = NO;
        cfg.URLCache = nil;
        cfg.URLCredentialStorage = nil;
        cfg.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        rq = [NSMutableURLRequest requestWithURL:u];
        [rq setValue:@"Frodhes" forHTTPHeaderField:@"User-Agent"];   /* (GitHub's API wants one; no version, no id) */
        [rq setValue:follow ? @"application/octet-stream" : @"application/vnd.github+json" forHTTPHeaderField:@"Accept"];
        s = [NSURLSession sessionWithConfiguration:cfg];
        sem = dispatch_semaphore_create(0);
        t = [s dataTaskWithRequest:rq completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
            /* (the session follows redirects: the API's answer must come from its own URL, a package's from https) */
            if (!e && [r isKindOfClass:[NSHTTPURLResponse class]] && [[r.URL scheme] isEqualToString:@"https"] &&
                (follow || [r.URL isEqual:u])) {
                size_t n = d.length < cap - 1 ? d.length : cap - 1;
                status = (int)((NSHTTPURLResponse *)r).statusCode;
                memcpy(buf, d.bytes, n);
                buf[n] = 0;
                got = n;
            }
            dispatch_semaphore_signal(sem);
        }];
        pthread_mutex_lock(&mk);
        cur = [t retain];
        pthread_mutex_unlock(&mk);
        [t resume];
        /* the session's own timeout ends it; if it ever does not, cancel: the handler always runs once, and buf must
         * not be written after this returns, so wait for it */
        if (dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(timeout_ms + 2000) * NSEC_PER_MSEC))) {
            [t cancel];
            dispatch_semaphore_wait(sem, DISPATCH_TIME_FOREVER);
        }
        pthread_mutex_lock(&mk);
        [cur release];
        cur = nil;
        pthread_mutex_unlock(&mk);
        [s finishTasksAndInvalidate];
        dispatch_release(sem);
    }
    *len = got;
    return status;
}
void frp_update_fetch_cancel(void)
{
    pthread_mutex_lock(&mk);
    [cur cancel];
    pthread_mutex_unlock(&mk);
}
int frp_update_open_url(const char *url)
{
    int ok = 0;
    @autoreleasepool {
        NSURL *u = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
        if (u && [[u scheme] isEqualToString:@"https"]) ok = [[NSWorkspace sharedWorkspace] openURL:u];
    }
    return ok ? 0 : -1;
}
int frp_update_reveal(const char *path)          /* the .pkg in Installer (its default application) */
{
    int ok = 0;
    @autoreleasepool {
        NSString *p = [NSString stringWithUTF8String:path];
        if (p.length) ok = [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:p]];
    }
    return ok ? 0 : -1;
}
