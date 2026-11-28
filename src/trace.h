#ifndef TRACE_H
#define TRACE_H
#include <stdint.h>
#ifdef EA9_TRACE
#define TRACE_CAPACITY 256u
#define TRACE_SAMPLE_US 5000u
#define TRACE_WINDOW_US 1000000u
enum trace_kind { TRACE_BEGIN=1, TRACE_SAMPLE, TRACE_EVENT_QUEUED,
 TRACE_EVENT_SENT, TRACE_CANCELLED, TRACE_FINISHED };
struct trace_record {
 uint32_t t_us;
 int32_t pos;
 uint8_t kind;
 uint8_t detail;
 uint16_t reserved;
};
struct trace_buffer {
 struct trace_record records[TRACE_CAPACITY];
 uint32_t write_count, dropped, session, start_us, next_sample_us;
 uint16_t event_base;
 uint8_t active, event_queued, event_sent, reserved;
};
extern struct trace_buffer g_trace;
void trace_init(void);
void trace_begin(int32_t pos, int32_t target, uint8_t outcome, uint16_t events_sent);
void trace_new_command(void);
void trace_event_queued(void);
void trace_event_tx(uint16_t events_sent);
void trace_service(void);
#else
static inline void trace_init(void) {}
static inline void trace_begin(int32_t p,int32_t t,uint8_t o,uint16_t e)
{(void)p;(void)t;(void)o;(void)e;}
static inline void trace_new_command(void) {}
static inline void trace_event_queued(void) {}
static inline void trace_event_tx(uint16_t e) {(void)e;}
static inline void trace_service(void) {}
#endif
#endif
