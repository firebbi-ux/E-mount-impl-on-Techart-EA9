#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "trace.h"
#include "nvm.h"
#include "emount.h"

static uint32_t now_us;
static int32_t position;
static uint8_t flash_image[NVM_LOG_END - NVM_LOG_BASE];

uint32_t micros(void) { return now_us; }
int32_t abs_encoder_track(void) { return position; }

void nvm_trace_erase_row(uint32_t address)
{
    uint32_t offset = address - NVM_LOG_BASE;
    if (address % NVM_ROW_SIZE || offset + NVM_ROW_SIZE > sizeof(flash_image)) abort();
    memset(flash_image + offset, 0xFF, NVM_ROW_SIZE);
}

void nvm_trace_write(uint32_t address, const uint32_t *words, uint32_t count)
{
    uint32_t offset = address - NVM_LOG_BASE, i;
    const uint8_t *src = (const uint8_t *)words;
    if (address % NVM_PAGE_SIZE || count != 16u
        || offset + NVM_PAGE_SIZE > sizeof(flash_image)) abort();
    for (i = 0; i < NVM_PAGE_SIZE; i++) flash_image[offset + i] &= src[i];
}

static void check(int ok, const char *message)
{
    if (!ok) { fprintf(stderr, "FAIL: %s\n", message); exit(1); }
}

static uint32_t crc32(const uint8_t *p, uint32_t n)
{
    uint32_t crc = 0xFFFFFFFFu, i;
    unsigned b;
    for (i = 0; i < n; i++) {
        crc ^= p[i];
        for (b = 0; b < 8; b++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return crc ^ 0xFFFFFFFFu;
}

static const uint32_t *header(unsigned slot)
{
    return (const uint32_t *)(flash_image + slot * 0x1800u);
}

int main(void)
{
    unsigned i;
    uint32_t count, bytes, expected_first;
    const uint32_t *h;
    const uint8_t *data;

    memset(flash_image, 0xFF, sizeof(flash_image));
    trace_init();
    for (i = 0; i < TRACE_CAPACITY + 20u; i++) {
        now_us += 1000u;
        position = (int32_t)(1000u + i);
        trace_begin(position, position + 7, 0);
    }
    check(g_trace.retained_count == TRACE_CAPACITY, "ring fills before snapshot");
    check(trace_save(), "first snapshot committed");
    h = header(0);
    count = h[3];
    bytes = count * sizeof(struct trace_record);
    check(h[0] == 0x45545243u && h[1] == 1u, "header magic/version");
    check(h[2] == 1u && count == TRACE_CAPACITY, "first slot sequence and count");
    check(h[12] == crc32((const uint8_t *)h, 12u * sizeof(uint32_t)), "header CRC");
    data = flash_image + 64u;
    check(h[11] == crc32(data, bytes), "record stream CRC");
    expected_first = g_trace.write_count - g_trace.retained_count;
    check(memcmp(data, &g_trace.records[expected_first % TRACE_CAPACITY],
                 sizeof(struct trace_record)) == 0, "snapshot begins at oldest retained record");

    now_us += 1000u;
    trace_begin(44, 55, 0);
    check(trace_save(), "second snapshot committed");
    check(header(1)[2] == 2u, "second save rotates to next slot");
    check(header(0)[2] == 1u, "previous valid slot survives rotation");

    for (i = 0; i < 3u; i++) {
        now_us += 1000u;
        trace_begin((int32_t)i, (int32_t)i + 1, 0);
        check(trace_save(), "subsequent snapshot committed");
    }
    check(header(0)[2] == 5u, "fifth save reuses oldest slot");
    check(header(1)[2] == 2u, "other recent slot remains intact");
    puts("PASS: TRACE flash slots, ring ordering, CRCs, and rotation");
    return 0;
}
