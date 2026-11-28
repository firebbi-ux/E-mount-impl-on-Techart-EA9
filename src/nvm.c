#include "board.h"
#include "nvm.h"

#define NVM_CMDEX    0xA500u
#define NVM_CMD_ER   0x02u
#define NVM_CMD_WP   0x04u
#define NVM_CMD_PBC  0x44u
#define NVM_ERROR_MASK (NVMCTRL_STATUS_PROGE | NVMCTRL_STATUS_LOCKE | NVMCTRL_STATUS_NVME)

static void nvm_wait(void)
{
    while (!(NVMCTRL->INTFLAG.reg & NVMCTRL_INTFLAG_READY)) { }
}

static void nvm_clear_errors(void)
{
    /* Both registers are write-one-to-clear. */
    NVMCTRL->INTFLAG.reg = NVMCTRL_INTFLAG_ERROR;
    NVMCTRL->STATUS.reg = NVM_ERROR_MASK;
}

static int nvm_has_error(void)
{
    return (NVMCTRL->INTFLAG.reg & NVMCTRL_INTFLAG_ERROR) != 0u
        || (NVMCTRL->STATUS.reg & NVM_ERROR_MASK) != 0u;
}

static int nvm_cmd(uint32_t byte_addr, uint32_t cmd)
{
    nvm_wait();
    nvm_clear_errors();
    NVMCTRL->ADDR.reg = byte_addr / 2u;
    NVMCTRL->CTRLA.reg = (uint16_t)(NVM_CMDEX | cmd);
    nvm_wait();
    return !nvm_has_error();
}

/* [addr, addr + len) inside [lo, hi), with overflow-safe bounds. */
static int nvm_inside(uint32_t addr, uint32_t len, uint32_t lo, uint32_t hi)
{
    return addr >= lo && addr < hi && len <= hi - addr;
}

static int nvm_erase_row(uint32_t addr, uint32_t lo, uint32_t hi)
{
    if ((addr % NVM_ROW_SIZE) != 0u
        || !nvm_inside(addr, NVM_ROW_SIZE, lo, hi)) return 0;
    return nvm_cmd(addr, NVM_CMD_ER);
}

/* Program words into one page, preserving untouched bytes. */
static int nvm_write(uint32_t addr, const uint32_t *w, uint32_t words,
                     uint32_t lo, uint32_t hi)
{
    uint32_t page, i, bytes;
    if (w == 0 || words == 0u || words > (NVM_PAGE_SIZE / 4u)) return 0;
    bytes = words * 4u;
    page = addr & ~(NVM_PAGE_SIZE - 1u);
    if ((addr & 3u) != 0u || addr < page
        || bytes > page + NVM_PAGE_SIZE - addr
        || !nvm_inside(addr, bytes, lo, hi)) return 0;
    if (!nvm_cmd(page, NVM_CMD_PBC)) return 0;
    for (i = 0; i < words; i++) ((volatile uint32_t *)addr)[i] = w[i];
    return nvm_cmd(page, NVM_CMD_WP);
}

#ifndef EA9_NO_LOG
void nvm_log_erase_row(uint32_t addr)
{
    (void)nvm_erase_row(addr, NVM_LOG_BASE, NVM_LOG_END);
}
void nvm_log_write(uint32_t addr, const uint32_t *w, uint32_t words)
{
    (void)nvm_write(addr, w, words, NVM_LOG_BASE, NVM_LOG_END);
}
#endif

#ifdef EA9_TRACE
__attribute__((noinline, used)) int nvm_trace_erase_row(uint32_t addr)
{
    return nvm_erase_row(addr, NVM_LOG_BASE, NVM_LOG_END);
}
int nvm_trace_write(uint32_t addr, const uint32_t *w, uint32_t words)
{
    return nvm_write(addr, w, words, NVM_LOG_BASE, NVM_LOG_END);
}
int nvm_trace_read(uint32_t addr, uint32_t *w, uint32_t words)
{
    uint32_t i, bytes;
    if (w == 0 || words == 0u || words > (NVM_LOG_END - NVM_LOG_BASE) / 4u)
        return 0;
    bytes = words * 4u;
    if ((addr & 3u) != 0u || !nvm_inside(addr, bytes, NVM_LOG_BASE, NVM_LOG_END))
        return 0;
    for (i = 0; i < words; i++) w[i] = ((volatile const uint32_t *)addr)[i];
    return 1;
}
#endif

void nvm_cfg_erase_row(uint32_t addr)
{
    (void)nvm_erase_row(addr, NVM_CFG_BASE, NVM_CFG_END);
}
void nvm_cfg_write(uint32_t addr, const uint32_t *w, uint32_t words)
{
    (void)nvm_write(addr, w, words, NVM_CFG_BASE, NVM_CFG_END);
}
