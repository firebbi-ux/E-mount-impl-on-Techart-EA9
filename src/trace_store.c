/* trace_store.c -- TRACE-only persistent snapshot in the otherwise-unused LOG area.
 *
 * Four independently erasable 6 KiB slots.  A slot's 64-byte header is written
 * LAST, so an interrupted erase/program never makes a partial snapshot valid.
 * This module is called only from the post-acknowledgement power-down path.
 */
#include "trace.h"
#include "nvm.h"
#include <stddef.h>
#include <stdint.h>

#define TRACE_STORE_MAGIC   0x45545243u /* "CRTE" */
#define TRACE_STORE_VERSION 1u
#define TRACE_STORE_SLOTS   4u
#define TRACE_STORE_SLOT_SIZE ((NVM_LOG_END - NVM_LOG_BASE) / TRACE_STORE_SLOTS)
#define TRACE_STORE_HEADER_SIZE NVM_PAGE_SIZE
#define TRACE_STORE_DATA_SIZE (TRACE_CAPACITY * sizeof(struct trace_record))
#define TRACE_STORE_SLOT_ROWS (TRACE_STORE_SLOT_SIZE / NVM_ROW_SIZE)
#define TRACE_STORE_DATA_PAGES ((TRACE_STORE_DATA_SIZE + NVM_PAGE_SIZE - 1u) / NVM_PAGE_SIZE)

typedef char trace_record_must_be_12_bytes[(sizeof(struct trace_record) == 12u) ? 1 : -1];
typedef char trace_slot_must_be_row_aligned[(TRACE_STORE_SLOT_SIZE % NVM_ROW_SIZE == 0u) ? 1 : -1];
typedef char trace_data_must_fit_slot[(TRACE_STORE_HEADER_SIZE + TRACE_STORE_DATA_SIZE <= TRACE_STORE_SLOT_SIZE) ? 1 : -1];

static uint32_t crc32_update(uint32_t crc, uint8_t byte)
{
    unsigned bit;
    crc ^= byte;
    for (bit = 0; bit < 8; bit++) {
        crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return crc;
}

static uint32_t crc32_bytes(const uint8_t *data, uint32_t length)
{
    uint32_t crc = 0xFFFFFFFFu;
    uint32_t i;
    for (i = 0; i < length; i++) crc = crc32_update(crc, data[i]);
    return crc ^ 0xFFFFFFFFu;
}

static uint32_t slot_base(unsigned slot)
{
    return NVM_LOG_BASE + (uint32_t)slot * TRACE_STORE_SLOT_SIZE;
}

static int read_header(unsigned slot, uint32_t words[16])
{
    volatile const uint32_t *flash =
        (volatile const uint32_t *)slot_base(slot);
    uint32_t i;
    for (i = 0; i < 16; i++) words[i] = flash[i];
    if (words[0] != TRACE_STORE_MAGIC || words[1] != TRACE_STORE_VERSION
        || words[3] > TRACE_CAPACITY) return 0;
    return words[12] == crc32_bytes((const uint8_t *)words, 12u * sizeof(uint32_t));
}

static void find_next_slot(unsigned *slot, uint32_t *sequence)
{
    uint32_t h[16], newest = 0;
    unsigned i, newest_slot = TRACE_STORE_SLOTS - 1u;
    int found = 0;

    for (i = 0; i < TRACE_STORE_SLOTS; i++) {
        if (read_header(i, h)) {
            if (!found || (int32_t)(h[2] - newest) > 0) {
                newest = h[2];
                newest_slot = i;
                found = 1;
            }
        }
    }
    *slot = found ? (newest_slot + 1u) % TRACE_STORE_SLOTS : 0u;
    *sequence = found ? newest + 1u : 1u;
}

static uint32_t record_byte(uint32_t logical_byte, uint32_t first)
{
    uint32_t record_index = logical_byte / sizeof(struct trace_record);
    uint32_t byte_index = logical_byte % sizeof(struct trace_record);
    uint32_t physical = (first + record_index) % TRACE_CAPACITY;
    const uint8_t *p = (const uint8_t *)&g_trace.records[physical];
    return p[byte_index];
}

/* Returns 1 only after the final, CRC-protected header page is committed. */
int trace_save(void)
{
    uint32_t header[16], page[16], crc = 0xFFFFFFFFu;
    uint32_t count = g_trace.retained_count;
    uint32_t first, i, j, page_no, total_bytes;
    unsigned slot, row;
    uint32_t sequence;

    if (count == 0u || count > TRACE_CAPACITY) return 0;

    first = g_trace.write_count - count;
    total_bytes = count * (uint32_t)sizeof(struct trace_record);

    /* Compute the chronological stream CRC before touching flash. */
    for (i = 0; i < total_bytes; i++)
        crc = crc32_update(crc, (uint8_t)record_byte(i, first));
    crc ^= 0xFFFFFFFFu;

    find_next_slot(&slot, &sequence);

    /* Erase exactly one 6 KiB slot (24 rows); never touch CFG or bootloader. */
    for (row = 0; row < TRACE_STORE_SLOT_ROWS; row++)
        nvm_trace_erase_row(slot_base(slot) + row * NVM_ROW_SIZE);

    /* Program the chronological ring contents, one complete 64-byte page
     * at a time. Unused tail bytes remain erased (0xFF). */
    for (page_no = 0; page_no < TRACE_STORE_DATA_PAGES; page_no++) {
        for (j = 0; j < 16; j++) page[j] = 0xFFFFFFFFu;
        for (j = 0; j < NVM_PAGE_SIZE; j++) {
            uint32_t offset = page_no * NVM_PAGE_SIZE + j;
            if (offset < total_bytes) {
                ((uint8_t *)page)[j] = (uint8_t)record_byte(offset, first);
            }
        }
        nvm_trace_write(slot_base(slot) + TRACE_STORE_HEADER_SIZE
                        + page_no * NVM_PAGE_SIZE, page, 16u);
    }

    /* Commit marker/header is last. Header CRC excludes word 12 itself. */
    for (i = 0; i < 16; i++) header[i] = 0xFFFFFFFFu;
    header[0]  = TRACE_STORE_MAGIC;
    header[1]  = TRACE_STORE_VERSION;
    header[2]  = sequence;
    header[3]  = count;
    header[4]  = g_trace.write_count;
    header[5]  = g_trace.retained_count;
    header[6]  = g_trace.overwritten_count;
    header[7]  = g_trace.dropped;
    header[8]  = g_trace.session;
    header[9]  = g_trace.session_first;
    header[10] = g_trace.start_us;
    header[11] = crc;
    header[12] = crc32_bytes((const uint8_t *)header, 12u * sizeof(uint32_t));
    nvm_trace_write(slot_base(slot), header, 16u);
    return 1;
}
