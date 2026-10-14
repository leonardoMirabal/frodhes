/* Review 02 scratch: does the optimiser itself create a constant in 0xFFC00000..0xFFCFFFFF from innocent source?
 * clang -O2/-Os -S for 32-bit targets, then grep the immediates. */
#include <stdint.h>
int32_t idx_base(int32_t x) { return (x >> 22) << 22; }            /* 1024-entry LUT: the cell's base -> x & 0xFFC00000 */
int32_t clamp_q22(int32_t x) { return x < -(1 << 22) ? -(1 << 22) : x; }   /* -1.0 in Q22 = 0xFFC00000 */
int32_t q15_to_q22(int32_t s) { s = s < -32767 ? -32767 : s; return s << 7; } /* RULES.md's -32767 << 7 */
int32_t q15_to_q22_safe(int32_t s) { int32_t v = s << 7; return v < -4194176 + 0 ? v : v; }
int32_t frac_ok(int32_t x) { return x & 0x3FFFFF; }               /* the fraction: no ROM-range constant */
