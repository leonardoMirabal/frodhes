/* Review 02 scratch: which 64-bit forms become libcalls on a 32-bit target (proxies for pi32v2) */
#include <stdint.h>
int32_t mulq30(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 30); }         /* widening: 1 mul-high */
int64_t mac2(int32_t a1, int32_t y1, int32_t a2, int32_t y2) { return (int64_t)a1 * y1 + (int64_t)a2 * y2; }
int64_t mul6464(int64_t a, int64_t b) { return a * b; }                                     /* true 64 x 64 */
int64_t div6432(int64_t a, int32_t b) { return a / b; }                                     /* banned */
