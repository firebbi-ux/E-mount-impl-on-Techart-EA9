#include "trace.h"
#ifdef EA9_TRACE
#include "abs_encoder.h"
#include "delay.h"
#include "emount.h"
struct trace_buffer g_trace;
static void trace_put_at(uint8_t kind,uint8_t detail,int32_t pos,uint32_t t_us)
{
 uint32_t n=g_trace.write_count;
 struct trace_record *r=&g_trace.records[n % TRACE_CAPACITY];
 if(g_trace.retained_count<TRACE_CAPACITY) g_trace.retained_count++;
 else {
  g_trace.overwritten_count++;
  if(g_trace.active) g_trace.dropped++;
 }
 r->t_us=t_us; r->pos=pos; r->kind=kind; r->detail=detail; r->reserved=0;
 g_trace.write_count=n+1u;
}
static void trace_put(uint8_t kind,uint8_t detail,int32_t pos)
{
 trace_put_at(kind,detail,pos,micros());
}
void trace_init(void)
{
 uint32_t i;
 g_trace.write_count=0; g_trace.retained_count=0; g_trace.overwritten_count=0;
 g_trace.dropped=0; g_trace.session=0; g_trace.session_first=0;
 g_trace.start_us=0; g_trace.next_sample_us=0; g_trace.target=0; g_trace.events_ahead=0;
 g_trace.active=0; g_trace.event_queued=0; g_trace.event_sent=0;
 for(i=0;i<TRACE_CAPACITY;i++) {
  g_trace.records[i].t_us=0; g_trace.records[i].pos=0;
  g_trace.records[i].kind=0; g_trace.records[i].detail=0;
  g_trace.records[i].reserved=0;
 }
}
void trace_begin(int32_t pos,int32_t target,uint8_t outcome)
{
 g_trace.active=0; g_trace.session++; g_trace.session_first=g_trace.write_count;
 g_trace.start_us=micros(); g_trace.target=target;
 g_trace.next_sample_us=g_trace.start_us+TRACE_SAMPLE_US;
 g_trace.events_ahead=0; g_trace.event_queued=0; g_trace.event_sent=0;
 trace_put_at(TRACE_BEGIN,outcome,pos,g_trace.start_us);
 trace_put_at(TRACE_TARGET,0,target,g_trace.start_us);
 g_trace.active=1;
}
void trace_new_command(void)
{
 if(g_trace.active) { trace_put(TRACE_CANCELLED,0,abs_encoder_track()); g_trace.active=0; }
}
void trace_event_queued(uint8_t events_ahead)
{
 if(g_trace.active && !g_trace.event_queued) {
  g_trace.events_ahead=events_ahead;
  trace_put(TRACE_EVENT_QUEUED,events_ahead,abs_encoder_track());
  g_trace.event_queued=1;
 }
}
void trace_event_tx(uint8_t code,uint8_t param)
{
 if(!g_trace.active || !g_trace.event_queued || g_trace.event_sent) return;
 if(g_trace.events_ahead) { g_trace.events_ahead--; return; }
 if(code==EM_REC_MOVE && param==0x00u) {
  trace_put(TRACE_EVENT_SENT,0,abs_encoder_track());
  g_trace.event_sent=1;
 }
}
void trace_service(void)
{
 uint32_t now;
 if(!g_trace.active) return;
 now=micros();
 if((int32_t)(now-g_trace.next_sample_us)<0) return;
 trace_put(TRACE_SAMPLE,0,abs_encoder_track());
 g_trace.next_sample_us=now+TRACE_SAMPLE_US;
 if((uint32_t)(now-g_trace.start_us)>=TRACE_WINDOW_US) {
  trace_put(TRACE_FINISHED,0,abs_encoder_track()); g_trace.active=0;
 }
}
#endif
