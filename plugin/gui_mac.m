/* SPDX-License-Identifier: GPL-3.0-only */
/* The plugin window on macOS (gui_platform.h): an NSView whose sublayer shows the canvas as a CGImage at the window's
 * backing scale (2x on Retina: the canvas is drawn at the pixel size, never scaled), sRGB as the canvas's colours are
 * (the documentation pictures and the screen show the same values). The canvas is the largest 16:10 rectangle in the
 * view (hosts keep the aspect: the gui extension's hints), its corners rounded as the plate's (look A3: 22 logical px)
 * so the host's window shows behind them. Sizes are points (CLAP's cocoa API); the timer runs at 60 Hz.
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
#include "gui_platform.h"
#include "ui/frui.h"

const char *const FRG_VIEW_API = "cocoa";

struct frg_view {
    frg_client cl;
    NSView *nsv;
    CALayer *img;                    /* the canvas's layer */
    NSTimer *timer;
    NSTrackingArea *track;
    uint32_t *px;                    /* the canvas, pw x ph pixels */
    int pw, ph;
    uint32_t *grab;
    int dirty;
    CGColorSpaceRef cs;
};

static Class s_cls;
static ptrdiff_t s_ivar;             /* the view's frg_view pointer */
static frg_view *V(id self) { return *(frg_view **)((char *)self + s_ivar); }

/* ---- painting */
static double backing(const frg_view *v)
{
    NSWindow *w = [v->nsv window];
    double s = w ? [w backingScaleFactor] : [[NSScreen mainScreen] backingScaleFactor];
    return s > 0.0 ? s : 1.0;
}
static void blit(frg_view *v)
{
    CFDataRef data = CFDataCreate(NULL, (const UInt8 *)v->px, (CFIndex)v->pw * v->ph * 4);
    CGDataProviderRef dp = CGDataProviderCreateWithCFData(data);
    CGImageRef im = CGImageCreate((size_t)v->pw, (size_t)v->ph, 8, 32, (size_t)v->pw * 4, v->cs,
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
static void paint(frg_view *v)
{
    frui_canvas c;
    if (!v->px || !v->cl.paint) return;
    frui_canvas_init(&c, v->px, v->pw, v->ph);
    v->cl.paint(v->cl.ctx, &c, (float)backing(v));
    v->dirty = 0;
    blit(v);
}
static void layout(frg_view *v)
{
    NSRect b = [v->nsv bounds];
    double s = backing(v), lw = b.size.width, lh = b.size.height;
    int pw, ph;
    if (lw < 1.0 || lh < 1.0) return;
    if (lh * FRUI_W > lw * FRUI_H) lh = lw * FRUI_H / FRUI_W; else lw = lh * FRUI_W / FRUI_H;   /* 16:10 */
    pw = (int)lround(lw * s); ph = (int)lround(lh * s);
    if (pw != v->pw || ph != v->ph || !v->px) {
        uint32_t *px = calloc((size_t)pw * (size_t)ph, 4);
        if (!px) return;
        free(v->px);
        v->px = px; v->pw = pw; v->ph = ph;
    }
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    v->img.contentsScale = s;
    v->img.frame = CGRectMake(0, b.size.height - ph / s, pw / s, ph / s);   /* the top left (the layer tree's y runs up) */
    if ([v->nsv.layer isGeometryFlipped]) v->img.frame = CGRectMake(0, 0, pw / s, ph / s);
    v->img.cornerRadius = 22.0 * (pw / s) / FRUI_W;
    v->img.masksToBounds = YES;
    [CATransaction commit];
    paint(v);
}

/* ---- input */
static int mods_of(NSEvent *e)
{
    NSEventModifierFlags f = [e modifierFlags];
    return (f & NSEventModifierFlagShift ? FRG_SHIFT : 0) | (f & NSEventModifierFlagOption ? FRG_ALT : 0) |
           (f & NSEventModifierFlagCommand ? FRG_CMD : 0) | (f & NSEventModifierFlagControl ? FRG_CTRL : 0);
}
static int view_send(frg_view *v, frg_input *e)
{
    int r = v->cl.input ? v->cl.input(v->cl.ctx, e) : 0;
    if (r & 1) paint(v);
    return r;
}
static void mouse(id self, NSEvent *e, int kind, int button)
{
    frg_view *v = V(self);
    frg_input in;
    NSPoint p;
    float k;
    if (!v || v->pw < 1) return;
    memset(&in, 0, sizeof in);
    p = [self convertPoint:[e locationInWindow] fromView:nil];
    k = (float)(FRUI_W / (v->pw / backing(v)));      /* logical px per point */
    in.kind = kind; in.button = button; in.mods = mods_of(e);
    in.x = (float)p.x * k; in.y = (float)p.y * k;    /* (flipped: y from the top) */
    if (kind == FRG_DOWN && button == FRG_LEFT && (in.mods & FRG_CTRL)) in.button = FRG_RIGHT;   /* control-click */
    if (kind == FRG_DOWN && [e clickCount] == 2) in.kind = FRG_DOUBLE;
    view_send(v, &in);
}
static BOOL m_yes(id self, SEL s) { (void)self; (void)s; return YES; }
static BOOL m_yes1(id self, SEL s, id a) { (void)self; (void)s; (void)a; return YES; }
static void m_down(id self, SEL s, NSEvent *e)
{
    (void)s;
    [[self window] makeFirstResponder:self];
    mouse(self, e, FRG_DOWN, FRG_LEFT);
}
static void m_drag(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_MOVE, 0); }
static void m_up(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_UP, FRG_LEFT); }
static void m_rdown(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_DOWN, FRG_RIGHT); }
static void m_rup(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_UP, FRG_RIGHT); }
static void m_move(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_MOVE, 0); }
static void m_exit(id self, SEL s, NSEvent *e) { (void)s; mouse(self, e, FRG_LEAVE, 0); }
static void m_scroll(id self, SEL s, NSEvent *e)
{
    frg_view *v = V(self);
    frg_input in;
    NSPoint p;
    float k, n;
    (void)s;
    if (!v || v->pw < 1) return;
    memset(&in, 0, sizeof in);
    p = [self convertPoint:[e locationInWindow] fromView:nil];
    k = (float)(FRUI_W / (v->pw / backing(v)));
    n = (float)[e scrollingDeltaY];
    if ([e hasPreciseScrollingDeltas]) n /= 16.f;   /* a trackpad's pixels: about one notch each 16 */
    if ([e isDirectionInvertedFromDevice]) n = -n;  /* (natural scrolling: up on the knob is still up) */
    in.kind = FRG_WHEEL; in.mods = mods_of(e); in.wheel = n;
    in.x = (float)p.x * k; in.y = (float)p.y * k;
    view_send(v, &in);
}
static void m_key(id self, SEL s, NSEvent *e)
{
    frg_view *v = V(self);
    frg_input in;
    struct objc_super sup = {self, [NSView class]};
    memset(&in, 0, sizeof in);
    in.kind = FRG_KEY; in.mods = mods_of(e);
    switch ([e keyCode]) {
    case 48: in.key = FRK_TAB; break;
    case 53: in.key = FRK_ESCAPE; break;
    case 36: case 76: in.key = FRK_RETURN; break;
    case 123: in.key = FRK_LEFT; break;
    case 124: in.key = FRK_RIGHT; break;
    case 125: in.key = FRK_DOWN; break;
    case 126: in.key = FRK_UP; break;
    case 115: in.key = FRK_HOME; break;
    case 119: in.key = FRK_END; break;
    case 116: in.key = FRK_PGUP; break;
    case 121: in.key = FRK_PGDN; break;
    case 51: in.key = FRK_BACKSPACE; break;
    case 117: in.key = FRK_DELETE; break;
    default: {
        NSString *ch = [e charactersIgnoringModifiers];
        unichar c = [ch length] == 1 ? [ch characterAtIndex:0] : 0;
        if (c >= 0x20 && c < 0x7f && !(in.mods & (FRG_CMD | FRG_CTRL))) in.key = (int)c;
    }
    }
    if (v && in.key && (view_send(v, &in) & 2)) return;
    ((void (*)(struct objc_super *, SEL, NSEvent *))objc_msgSendSuper)(&sup, s, e);   /* the host's key */
}
static void m_resign(id self, SEL s)
{
    frg_view *v = V(self);
    frg_input in;
    (void)s;
    if (v) { memset(&in, 0, sizeof in); in.kind = FRG_FOCUS_LOST; view_send(v, &in); }
}
static BOOL m_resign_first(id self, SEL s)
{
    m_resign(self, s);
    return YES;
}
static void m_backing(id self, SEL s)
{
    frg_view *v = V(self);
    (void)s;
    if (v) layout(v);
}
static void m_setsize(id self, SEL s, NSSize sz)
{
    struct objc_super sup = {self, [NSView class]};
    frg_view *v = V(self);
    ((void (*)(struct objc_super *, SEL, NSSize))objc_msgSendSuper)(&sup, s, sz);
    if (v) layout(v);
}
static void m_tracking(id self, SEL s)
{
    struct objc_super sup = {self, [NSView class]};
    frg_view *v = V(self);
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
    frg_view *v = V(self);
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
    class_addMethod(c, @selector(resignFirstResponder), (IMP)m_resign_first, "c@:");
    class_addMethod(c, @selector(acceptsFirstMouse:), (IMP)m_yes1, "c@:@");
    class_addMethod(c, @selector(mouseDown:), (IMP)m_down, "v@:@");
    class_addMethod(c, @selector(mouseDragged:), (IMP)m_drag, "v@:@");
    class_addMethod(c, @selector(mouseUp:), (IMP)m_up, "v@:@");
    class_addMethod(c, @selector(rightMouseDown:), (IMP)m_rdown, "v@:@");
    class_addMethod(c, @selector(rightMouseUp:), (IMP)m_rup, "v@:@");
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

/* ---- gui_platform.h */
float frg_view_scale(const frg_view *v) { return v ? (float)backing(v) : (float)[[NSScreen mainScreen] backingScaleFactor]; }
void frg_view_set_scale(frg_view *v, float scale) { (void)v; (void)scale; }   /* (the window's backing scale) */
void frg_view_default_size(const frg_view *v, int *w, int *h)
{
    (void)v;
    *w = FRUI_W; *h = FRUI_H;
}
frg_view *frg_view_create(const frg_client *cl, int w, int h)
{
    frg_view *v = calloc(1, sizeof *v);
    Class c = view_class();
    if (!v || !c) { free(v); return 0; }
    v->cl = *cl;
    v->cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    v->nsv = [[c alloc] initWithFrame:NSMakeRect(0, 0, w, h)];
    *(frg_view **)((char *)v->nsv + s_ivar) = v;
    [v->nsv setWantsLayer:YES];
    v->nsv.layer.backgroundColor = CGColorGetConstantColor(kCGColorClear);
    v->img = [[CALayer alloc] init];
    v->img.contentsGravity = kCAGravityResize;
    v->img.magnificationFilter = kCAFilterNearest;  /* (only ever shown 1:1) */
    [v->nsv.layer addSublayer:v->img];
    layout(v);
    v->timer = [[NSTimer timerWithTimeInterval:1.0 / 60.0
                                      repeats:YES
                                        block:^(NSTimer *t) {
                                            (void)t;
                                            if ((v->cl.tick && v->cl.tick(v->cl.ctx)) || v->dirty) paint(v);
                                        }] retain];
    [[NSRunLoop mainRunLoop] addTimer:v->timer forMode:NSRunLoopCommonModes];
    return v;
}
void frg_view_destroy(frg_view *v)
{
    if (!v) return;
    [v->timer invalidate];
    [v->timer release];
    *(frg_view **)((char *)v->nsv + s_ivar) = 0;   /* (a late event finds no window) */
    if (v->track) { [v->nsv removeTrackingArea:v->track]; [v->track release]; }
    [v->nsv removeFromSuperview];
    [v->img release];
    [v->nsv release];
    CGColorSpaceRelease(v->cs);
    free(v->px);
    free(v->grab);
    free(v);
}
int frg_view_set_parent(frg_view *v, void *native_parent)
{
    NSView *p = (NSView *)native_parent;
    if (!p) return -1;
    [p addSubview:v->nsv];
    return 0;
}
void frg_view_show(frg_view *v, int visible)
{
    [v->nsv setHidden:visible ? NO : YES];
    if (visible) paint(v);
}
void frg_view_resize(frg_view *v, int w, int h) { [v->nsv setFrameSize:NSMakeSize(w, h)]; }
void frg_view_repaint(frg_view *v) { paint(v); }   /* (macOS: at once; the main thread) */
int frg_view_poll_fd(const frg_view *v) { (void)v; return -1; }   /* (the run loop) */
void frg_view_poll(frg_view *v) { (void)v; }
const uint32_t *frg_view_grab(frg_view *v, int *w, int *h)   /* what the layer shows, read back */
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
