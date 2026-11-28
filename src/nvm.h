/* nvm.h -- region-confined SAMD21 NVM access. */
#ifndef NVM_H
#define NVM_H
#include <stdint.h>

#define NVM_PAGE_SIZE   64u
#define NVM_ROW_SIZE    256u
#define NVM_LOG_BASE    0x00016000u
#define NVM_LOG_END     0x0001C000u
#define NVM_CFG_BASE    0x0001C000u
#define NVM_CFG_END     0x0001D000u

#ifndef EA9_NO_LOG
void nvm_log_erase_row(uint32_t addr);
void nvm_log_write(uint32_t addr, const uint32_t *w, uint32_t words);
#endif
#ifdef EA9_TRACE
int nvm_trace_erase_row(uint32_t addr);
int nvm_trace_write(uint32_t addr, const uint32_t *w, uint32_t words);
int nvm_trace_read(uint32_t addr, uint32_t *w, uint32_t words);
#endif
void nvm_cfg_erase_row(uint32_t addr);
void nvm_cfg_write(uint32_t addr, const uint32_t *w, uint32_t words);
#endif
