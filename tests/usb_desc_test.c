/* SPDX-License-Identifier: GPL-3.0-only */
/* The USB descriptors as the host reads them: usb.c compiled here unchanged, its get_desc() asked for the device,
 * the configuration and every string, the way a host enumerates.
 *
 * Checks: the device descriptor (VID 1209, the PID: the FM-1 app 0x0006, the SMK-37 Pro / Elite / MKE-P37 apps 0x0007 /
 * 0x0008 / 0x0009, the update loader 0x0002, its string indexes), the
 * configuration walked descriptor by descriptor (each bLength, the sum = wTotalLength = sizeof = CFG_LEN, the
 * interface count), the MIDI streaming jacks (four, the embedded pair tied to the bulk endpoints, iJack the product
 * string in the app and 0 in the loader), every string (bLength = its size, type 3, UTF-16LE, the language 0x0409) and
 * every index the descriptors name resolves. The app: "Hügelton Instruments" / "Frodhes FM-1" ("Frodhes SMK-37 Pro"
 * ...: the model in the name since 0.9.7, GitHub #21; "Frodhes" 0x0005 in 0.9.5-0.9.6); the loader: "Felucca
 * Update", its descriptors as before the PID change (iJack 0, PID 0x0002).
 *
 *   cc -o build/usb_desc_test tests/usb_desc_test.c -Ifirmware/src && build/usb_desc_test          (the app)
 *   cc -DFELUCCA_LOADER=1 -DFELUCCA_USB_PID=0x0002 -DFELUCCA_UAC=0 -o ... (the update loader) */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define RING_PUBLISH() ((void)0)
#define HALF_FRAMES 256u
#ifndef FELUCCA_UAC
#define FELUCCA_UAC 1                    /* the app (platform.c) */
#endif
static void fm1_delay_ms(uint32_t ms) { (void)ms; }
#include "usb.c"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL: " __VA_ARGS__); printf("\n"); } } while (0)

static int str_get(unsigned idx, char *out, size_t n)   /* the string as ASCII / Latin-1 (UTF-8 for 0xFC) */
{
    const uint8_t *d;
    uint16_t l;
    size_t o = 0;
    if (!get_desc(0x0300u | idx, &d, &l))
        return 0;
    CHECK(l >= 2 && d[0] == l && d[1] == 3, "string %u: bLength %u, size %u, type %u", idx, d[0], l, d[1]);
    CHECK((l & 1u) == 0, "string %u: odd length %u", idx, l);
    for (uint16_t i = 2; i + 1 < l && o + 3 < n; i += 2) {
        uint32_t c = d[i] | (uint32_t)d[i + 1] << 8;
        CHECK(c && c < 0x100, "string %u: char %u = U+%04X", idx, i / 2u, (unsigned)c);
        if (c < 0x80)
            out[o++] = (char)c;
        else {
            out[o++] = (char)(0xC0 | c >> 6);
            out[o++] = (char)(0x80 | (c & 0x3F));
        }
    }
    out[o] = 0;
    return 1;
}

int main(void)
{
    const uint8_t *dd, *cd, *s0;
    uint16_t dl, cl, s0l;
    char s[64];
#ifdef FELUCCA_LOADER
    const unsigned want_pid = 0x0002, want_ijack = 0;
    const char *want_prod = "Felucca Update";
#elif defined(WANT_PID)
    const unsigned want_pid = WANT_PID, want_ijack = 2;   /* (an SMK model: -DBOARD_SMK37=1 [-DSMK_MODEL=...]) */
    const char *want_prod = WANT_PROD;
#else
    const unsigned want_pid = 0x0006, want_ijack = 2;     /* the FM-1 */
    const char *want_prod = "Frodhes FM-1";
#endif

    CHECK(get_desc(0x0100, &dd, &dl), "no device descriptor");
    CHECK(dl == 18 && dd[0] == 18 && dd[1] == 1, "device descriptor: size %u, bLength %u, type %u", dl, dd[0], dd[1]);
    CHECK((dd[8] | dd[9] << 8) == 0x1209, "VID %04X", dd[8] | dd[9] << 8);
    CHECK((unsigned)(dd[10] | dd[11] << 8) == want_pid, "PID %04X, want %04X", dd[10] | dd[11] << 8, want_pid);
    CHECK(dd[14] == 1 && dd[15] == 2 && dd[16] == 0 && dd[17] == 1,
          "iManufacturer %u iProduct %u iSerialNumber %u, configurations %u", dd[14], dd[15], dd[16], dd[17]);
    printf("device: %04X:%04X bcdDevice %X.%02X, USB %X.%02X, class %u\n", dd[8] | dd[9] << 8, dd[10] | dd[11] << 8,
           dd[13], dd[12], dd[3], dd[2], dd[4]);

    CHECK(get_desc(0x0300, &s0, &s0l) && s0l == 4 && s0[0] == 4 && s0[1] == 3 && s0[2] == 0x09 && s0[3] == 0x04,
          "string 0: the language table (0x0409)");
    CHECK(str_get(1, s, sizeof s) && !strcmp(s, "H\xC3\xBCgelton Instruments"), "manufacturer \"%s\"", s);
    printf("manufacturer: \"%s\"\n", s);
    CHECK(str_get(2, s, sizeof s) && !strcmp(s, want_prod), "product \"%s\", want \"%s\"", s, want_prod);
    printf("product: \"%s\"\n", s);
    CHECK(!get_desc(0x0303, &s0, &s0l), "string 3 should not exist");
    CHECK(!get_desc(0x0300 | 0xEE, &s0, &s0l), "string 0xEE (the MS OS string) should not exist");

    CHECK(get_desc(0x0200, &cd, &cl), "no configuration descriptor");
    CHECK(cl == sizeof CFG_DESC && cl == CFG_LEN, "configuration size %u, CFG_LEN %u", cl, (unsigned)CFG_LEN);
    CHECK(cd[0] == 9 && cd[1] == 2 && (cd[2] | cd[3] << 8) == cl, "configuration header: wTotalLength %u, size %u",
          cd[2] | cd[3] << 8, cl);
    {
        unsigned off = 0, n = 0, nif = 0, jacks = 0, ms = 0, alt = 0;
        unsigned cur_if = 0xFF, cur_sub = 0, ep_jack[2] = {0, 0}, jack_type[8] = {0};
        while (off < cl) {
            const uint8_t *x = cd + off;
            CHECK(x[0] >= 2 && off + x[0] <= cl, "descriptor %u at %u: bLength %u runs past %u", n, off, x[0], cl);
            if (x[0] < 2 || off + x[0] > cl)
                break;
            if (x[1] == 4) {                       /* interface */
                CHECK(x[0] == 9, "interface at %u: bLength %u", off, x[0]);
                cur_if = x[2];
                cur_sub = x[6];
                if (x[3] == 0)
                    nif++;
                else
                    alt++;
                CHECK(x[8] == 0, "interface %u: iInterface %u (no string)", x[2], x[8]);
                if (x[5] == 1 && x[6] == 3)
                    ms++;
            } else if (x[1] == 0x24 && cur_sub == 3 && (x[2] == 2 || x[2] == 3)) {   /* MIDI IN / OUT jack */
                unsigned want_len = x[2] == 2 ? 6u : 9u, ij = x[x[0] - 1];
                CHECK(x[0] == want_len, "jack %u: bLength %u", x[4], x[0]);
                CHECK(x[4] && x[4] < 8, "jack id %u", x[4]);
                if (x[4] < 8)
                    jack_type[x[4]] = x[2] << 4 | x[3];
                CHECK(ij == want_ijack, "jack %u: iJack %u, want %u", x[4], ij, want_ijack);
                if (ij) {
                    CHECK(str_get(ij, s, sizeof s) && !strcmp(s, want_prod), "jack %u: name \"%s\"", x[4], s);
                }
                jacks++;
            } else if (x[1] == 0x25 && cur_sub == 3) {                              /* MS bulk endpoint */
                CHECK(x[0] == 5 && x[3] == 1, "MS endpoint at %u: bLength %u, jacks %u", off, x[0], x[3]);
                ep_jack[ep_jack[0] ? 1 : 0] = x[4];
            }
            off += x[0];
            n++;
        }
        CHECK(off == cl, "descriptors end at %u, wTotalLength %u", off, cl);
        CHECK(nif == cd[4] && nif == USB_NIF, "interfaces %u, bNumInterfaces %u, USB_NIF %u", nif, cd[4], USB_NIF);
        CHECK(ms == 1 && cur_if != 0xFF, "MIDI streaming interfaces %u", ms);
        CHECK(jacks == 4, "MIDI jacks %u", jacks);
        CHECK(jack_type[1] == 0x21 && jack_type[2] == 0x22 && jack_type[3] == 0x31 && jack_type[4] == 0x32,
              "jacks: in embedded 1, in external 2, out embedded 3, out external 4");
        CHECK(ep_jack[0] == 1 && ep_jack[1] == 3, "bulk OUT -> jack %u (want 1), bulk IN -> jack %u (want 3)",
              ep_jack[0], ep_jack[1]);
        printf("configuration: %u bytes, %u descriptors, %u interfaces (+%u alt), %u MIDI jacks, iJack %u\n", cl, n,
               nif, alt, jacks, want_ijack);
    }
    printf(fails ? "usb_desc_test: %d FAILED\n" : "usb_desc_test: ok\n", fails);
    return fails != 0;
}
