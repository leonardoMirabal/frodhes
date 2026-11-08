/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on macOS (gui_view.h): an NSView whose sublayer shows gui_core's canvas as a CGImage at the
 * window's backing scale (2x on Retina: the canvas is drawn at the pixel size, never scaled), sRGB as the canvas's
 * colours are (the documentation pictures and the screen show the same values).
 *
 * The view's class is registered at run time under a name unique to this binary image: AppKit's classes are global to
 * the process, and the same plugin can be loaded twice in one host (its CLAP and its VST3, or two versions), each with
 * its own code. Manual reference counting (no ARC). */
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <clap/clap.h>
#include "gui_view.h"

const char *const FRP_VIEW_API = CLAP_WINDOW_API_COCOA;

struct frp_view {
    frp_gui_t *core;
    NSView *nsv;
    CALayer *img;                    /* the canvas's layer */
    NSTimer *timer;
    NSTrackingArea *track;
    uint32_t *grab;
    int gw, gh;
    CGColorSpaceRef cs;
};

static Class s_cls;
static ptrdiff_t s_ivar;             /* the view's frp_view pointer */
static frp_view_t *V(id self) { return *(frp_view_t **)((char *)self + s_ivar); }

static double now_s(void) { return CACurrentMediaTime(); }

/* ---- painting */
static void blit(frp_view_t *v)
{
    int w, h;
    const uint32_t *px = frp_gui_pixels(v->core, &w, &h);
    CFDataRef data;
    CGDataProviderRef dp;
    CGImageRef im;
    if (!px) return;
    data = CFDataCreate(NULL, (const UInt8 *)px, (CFIndex)w * h * 4);
    dp = CGDataProviderCreateWithCFData(data);
    im = CGImageCreate((size_t)w, (size_t)h, 8, 32, (size_t)w * 4, v->cs,
                       kCGBitmapByteOrder32Little | (CGBitmapInfo)kCGImageAlphaNoneSkipFirst, dp, NULL, false,
                       kCGRenderingIntentDefault);
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    v->img.contents = (id)im;
    [CATransaction commit];
    CGImageRelease(im);
    CGDataProviderRelease(dp);
    CFRelease(data);
}
static void frame(frp_view_t *v, int force)
{
    int d[4];
    if (frp_gui_tick(v->core, now_s()) || force)
        if (frp_gui_paint(v->core, d) || force) blit(v);
}
static double backing(frp_view_t *v)
{
    NSWindow *w = [v->nsv window];
    double s = w ? [w backingScaleFactor] : [[NSScreen mainScreen] backingScaleFactor];
    return s > 0.0 ? s : 1.0;
}
static void layout(frp_view_t *v)
{
    NSRect b = [v->nsv bounds];
    double s = backing(v);
    int pw = (int)lround(b.size.width * s), ph = (int)lround(b.size.height * s), cw, ch;
    if (pw < 1 || ph < 1) return;
    frp_gui_resize(v->core, pw, ph);
    (void)frp_gui_pixels(v->core, &cw, &ch);
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    v->img.contentsScale = s;
    v->img.frame = CGRectMake(0, b.size.height - ch / s, cw / s, ch / s);   /* (the layer tree's y runs up) */
    if ([v->nsv.layer isGeometryFlipped]) v->img.frame = CGRectMake(0, 0, cw / s, ch / s);
    /* the plate's rounded corners (look A3: 22 logical px) show the host's window behind them */
    v->img.cornerRadius = 22.0 * (cw / s) / FRP_GUI_W;
    v->img.masksToBounds = YES;
    [CATransaction commit];
    frame(v, 1);
}

/* ---- input */
static void logical(id self, NSEvent *e, float *x, float *y)
{
    frp_view_t *v = V(self);
    NSPoint p = [self convertPoint:[e locationInWindow] fromView:nil];
    float k = frp_gui_px_to_logical(v->core) * (float)backing(v);
    *x = (float)p.x * k;
    *y = (float)p.y * k;             /* (the view is flipped: y from the top) */
}
static int mods_of(NSEvent *e)
{
    NSEventModifierFlags f = [e modifierFlags];
    return (f & NSEventModifierFlagShift ? FRP_MOD_SHIFT : 0) | (f & NSEventModifierFlagOption ? FRP_MOD_ALT : 0) |
           (f & NSEventModifierFlagCommand ? FRP_MOD_CMD : 0) | (f & NSEventModifierFlagControl ? FRP_MOD_CTRL : 0);
}
static void mouse(id self, NSEvent *e, int ev)
{
    frp_view_t *v = V(self);
    float x, y;
    if (!v) return;
    logical(self, e, &x, &y);
    if (ev == FRP_MOUSE_DOWN && (mods_of(e) & FRP_MOD_CTRL)) ev = FRP_MOUSE_RDOWN;   /* control-click: the menu */
    frp_gui_mouse(v->core, ev, x, y, mods_of(e), ev == FRP_MOUSE_DOWN ? (int)[e clickCount] : 1);
    frame(v, 0);
}
static BOOL m_yes(id self, SEL s) { (void)self; (void)s; return YES; }
static BOOL m_yes1(id self, SEL s, id a) { (void)self; (void)s; (void)a; return YES; }
static void m_down(id self, SEL s, NSEvent *e)
{
    (void)s;
    [[self window] makeFirstResponder:self];
    mouse(self, e, FRP_MOUSE_DOWN);
}
static void m_drag(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRP_MOUSE_DRAG); }
static void m_up(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRP_MOUSE_UP); }
static void m_rdown(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRP_MOUSE_RDOWN); }
static void m_move(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRP_MOUSE_MOVE); }
static void m_exit(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRP_MOUSE_LEAVE); }
static void m_scroll(id self, SEL s, NSEvent *e)
{
    frp_view_t *v = V(self);
    float x, y, n;
    (void)s;
    if (!v) return;
    logical(self, e, &x, &y);
    n = (float)[e scrollingDeltaY];
    if ([e hasPreciseScrollingDeltas]) n /= 16.f;   /* a trackpad's pixels: about one notch each 16 */
    if ([e isDirectionInvertedFromDevice]) n = -n;  /* (natural scrolling: up on the knob is still up) */
    frp_gui_wheel(v->core, x, y, n, mods_of(e));
    frame(v, 0);
}
static void m_key(id self, SEL s, NSEvent *e)
{
    frp_view_t *v = V(self);
    int k = 0;
    struct objc_super sup = {self, [NSView class]};
    switch ([e keyCode]) {
    case 48: k = FRP_KEY_TAB; break;
    case 53: k = FRP_KEY_ESC; break;
    case 36: case 76: k = FRP_KEY_ENTER; break;
    case 123: k = FRP_KEY_LEFT; break;
    case 124: k = FRP_KEY_RIGHT; break;
    case 125: k = FRP_KEY_DOWN; break;
    case 126: k = FRP_KEY_UP; break;
    case 115: k = FRP_KEY_HOME; break;
    case 119: k = FRP_KEY_END; break;
    }
    if (v && k && frp_gui_key(v->core, k, mods_of(e))) { frame(v, 0); return; }
    ((void (*)(struct objc_super *, SEL, NSEvent *))objc_msgSendSuper)(&sup, s, e);   /* the host's key */
}
static void m_backing(id self, SEL s)
{
    frp_view_t *v = V(self);
    (void)s;
    if (v) layout(v);
}
static void m_setsize(id self, SEL s, NSSize sz)
{
    struct objc_super sup = {self, [NSView class]};
    frp_view_t *v = V(self);
    ((void (*)(struct objc_super *, SEL, NSSize))objc_msgSendSuper)(&sup, s, sz);
    if (v) layout(v);
}
static void m_tracking(id self, SEL s)
{
    struct objc_super sup = {self, [NSView class]};
    frp_view_t *v = V(self);
    if (v) {
        if (v->track) { [self removeTrackingArea:v->track]; [v->track release]; }
        v->track = [[NSTrackingArea alloc]
            initWithRect:NSZeroRect
                 options:NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways |
                         NSTrackingInVisibleRect
                   owner:self
                userInfo:nil];
        [self addTrackingArea:v->track];
    }
    ((void (*)(struct objc_super *, SEL))objc_msgSendSuper)(&sup, s);
}
static void m_window(id self, SEL s)
{
    struct objc_super sup = {self, [NSView class]};
    frp_view_t *v = V(self);
    ((void (*)(struct objc_super *, SEL))objc_msgSendSuper)(&sup, s);
    if (v && [self window]) layout(v);
}

static Class view_class(void)
{
    char name[64];
    Class c;
    if (s_cls) return s_cls;
    snprintf(name, sizeof name, "FrodhesView_%lx", (unsigned long)(uintptr_t)&s_cls);   /* this image's own */
    c = objc_allocateClassPair([NSView class], name, 0);
    if (!c) { s_cls = objc_getClass(name); return s_cls; }
    class_addIvar(c, "frp", sizeof(void *), sizeof(void *) == 8 ? 3 : 2, "^v");
    class_addMethod(c, @selector(isFlipped), (IMP)m_yes, "c@:");
    class_addMethod(c, @selector(acceptsFirstResponder), (IMP)m_yes, "c@:");
    class_addMethod(c, @selector(acceptsFirstMouse:), (IMP)m_yes1, "c@:@");
    class_addMethod(c, @selector(mouseDown:), (IMP)m_down, "v@:@");
    class_addMethod(c, @selector(mouseDragged:), (IMP)m_drag, "v@:@");
    class_addMethod(c, @selector(mouseUp:), (IMP)m_up, "v@:@");
    class_addMethod(c, @selector(rightMouseDown:), (IMP)m_rdown, "v@:@");
    class_addMethod(c, @selector(mouseMoved:), (IMP)m_move, "v@:@");
    class_addMethod(c, @selector(mouseExited:), (IMP)m_exit, "v@:@");
    class_addMethod(c, @selector(scrollWheel:), (IMP)m_scroll, "v@:@");
    class_addMethod(c, @selector(keyDown:), (IMP)m_key, "v@:@");
    class_addMethod(c, @selector(viewDidChangeBackingProperties), (IMP)m_backing, "v@:");
    class_addMethod(c, @selector(setFrameSize:), (IMP)m_setsize, "v@:{CGSize=dd}");
    class_addMethod(c, @selector(updateTrackingAreas), (IMP)m_tracking, "v@:");
    class_addMethod(c, @selector(viewDidMoveToWindow), (IMP)m_window, "v@:");
    objc_registerClassPair(c);
    s_cls = c;
    s_ivar = ivar_getOffset(class_getInstanceVariable(c, "frp"));
    return c;
}

/* ---- gui_view.h */
frp_view_t *frp_view_new(frp_gui_t *core, uint32_t w, uint32_t h)
{
    frp_view_t *v = calloc(1, sizeof *v);
    Class c = view_class();
    if (!v || !c) { free(v); return 0; }
    v->core = core;
    v->cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    v->nsv = [[c alloc] initWithFrame:NSMakeRect(0, 0, w, h)];
    *(frp_view_t **)((char *)v->nsv + s_ivar) = v;
    [v->nsv setWantsLayer:YES];
    v->nsv.layer.backgroundColor = CGColorGetConstantColor(kCGColorClear);
    v->img = [[CALayer alloc] init];
    v->img.contentsGravity = kCAGravityResize;
    v->img.magnificationFilter = kCAFilterNearest;  /* (only ever shown 1:1) */
    [v->nsv.layer addSublayer:v->img];
    layout(v);
    v->timer = [[NSTimer timerWithTimeInterval:1.0 / 60.0
                                      repeats:YES
                                        block:^(NSTimer *t) { (void)t; frame(v, 0); }] retain];
    [[NSRunLoop mainRunLoop] addTimer:v->timer forMode:NSRunLoopCommonModes];
    return v;
}
void frp_view_free(frp_view_t *v)
{
    if (!v) return;
    [v->timer invalidate];
    [v->timer release];
    *(frp_view_t **)((char *)v->nsv + s_ivar) = 0;   /* (a late event finds no window) */
    if (v->track) { [v->nsv removeTrackingArea:v->track]; [v->track release]; }
    [v->nsv removeFromSuperview];
    [v->img release];
    [v->nsv release];
    CGColorSpaceRelease(v->cs);
    free(v->grab);
    free(v);
}
int frp_view_attach(frp_view_t *v, void *parent)
{
    NSView *p = (NSView *)parent;
    if (!p) return -1;
    [p addSubview:v->nsv];
    return 0;
}
void frp_view_size(frp_view_t *v, uint32_t w, uint32_t h) { [v->nsv setFrameSize:NSMakeSize(w, h)]; }
void frp_view_show(frp_view_t *v, int show)
{
    [v->nsv setHidden:show ? NO : YES];
    if (show) frame(v, 1);
}
void frp_view_scale(frp_view_t *v, double scale) { (void)v; (void)scale; }
const uint32_t *frp_view_grab(frp_view_t *v, int *w, int *h)
{
    CGImageRef im = (CGImageRef)v->img.contents;
    CFDataRef d;
    size_t iw, ih, bpr, y;
    if (!im) return 0;
    iw = CGImageGetWidth(im); ih = CGImageGetHeight(im); bpr = CGImageGetBytesPerRow(im);
    d = CGDataProviderCopyData(CGImageGetDataProvider(im));
    if (!d) return 0;
    free(v->grab);
    v->grab = malloc(iw * ih * 4);
    for (y = 0; y < ih && v->grab; y++) memcpy(v->grab + y * iw, CFDataGetBytePtr(d) + y * bpr, iw * 4);
    CFRelease(d);
    *w = (int)iw; *h = (int)ih;
    return v->grab;
}
