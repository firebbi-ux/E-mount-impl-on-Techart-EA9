#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "trace.h"
#include "emount.h"
static uint32_t now_us;
static int32_t position;
uint32_t micros(void){return now_us;}
int32_t abs_encoder_track(void){return position;}
static void check(int ok,const char *s){if(!ok){fprintf(stderr,"FAIL: %s\n",s);exit(1);}}
int main(void)
{
 unsigned i,samples=0;
 trace_init(); trace_begin(100,200,0);
 check(g_trace.active,"begin activates trace");
 check(g_trace.retained_count==2,"begin and target records are retained");
 check(g_trace.session_first==0,"session start sequence retained");
 check(g_trace.records[0].kind==TRACE_BEGIN,"begin marker");
 check(g_trace.records[0].pos==100,"begin position retained");
 check(g_trace.records[1].kind==TRACE_TARGET,"target marker");
 check(g_trace.records[1].pos==200,"target position retained per session");
 check(g_trace.records[0].t_us==g_trace.records[1].t_us,"begin and target share one timestamp");
 trace_event_queued(0);
 check(g_trace.records[2].kind==TRACE_EVENT_QUEUED,"queue marker");
 trace_event_tx(EM_REC_MOVE,0x00u);
 check(g_trace.records[3].kind==TRACE_EVENT_SENT,"transmit marker");
 check(g_trace.target==200,"commanded target retained");
 now_us=3000; trace_service(); check(g_trace.write_count==4,"no early sample");
 for(i=1;i<=200;i++){now_us=i*TRACE_SAMPLE_US;position=100+(int32_t)i;trace_service();}
 check(!g_trace.active,"window ends at one second");
 for(i=0;i<g_trace.write_count && i<TRACE_CAPACITY;i++)
  if(g_trace.records[i].kind==TRACE_SAMPLE)samples++;
 check(samples>=190 && samples<=200,"approximately 5 ms sample cadence");
 check(g_trace.records[(g_trace.write_count-1)%TRACE_CAPACITY].kind==TRACE_FINISHED,"finished marker");
 {
  unsigned at=g_trace.write_count;
  trace_begin(10,20,0);
  check(g_trace.records[at % TRACE_CAPACITY].kind==TRACE_BEGIN,"second session begin retained");
  check(g_trace.records[(at+1u) % TRACE_CAPACITY].kind==TRACE_TARGET,"second session target marker retained");
  check(g_trace.records[(at+1u) % TRACE_CAPACITY].pos==20,"second session target is not overwritten by later session");
 }
 trace_event_queued(1);
 trace_event_tx(EM_REC_STOP,0x01u);
 check(!g_trace.event_sent,"older queued event is not mistaken for Move");
 trace_event_tx(EM_REC_MOVE,0x00u);
 check(g_trace.event_sent,"Move event recognized after earlier queue item");
 trace_new_command();
 check(!g_trace.active,"new command cancels observation");
 check(g_trace.records[(g_trace.write_count-1)%TRACE_CAPACITY].kind==TRACE_CANCELLED,"cancel marker");
 check(g_trace.retained_count==g_trace.write_count,"retained count before wrap");
 for(i=0;i<TRACE_CAPACITY+10u;i++) trace_begin((int32_t)i,(int32_t)i+1,0);
 check(g_trace.retained_count==TRACE_CAPACITY,"retained count saturates at ring capacity");
 check(g_trace.overwritten_count>0,"ring overwrite count is explicit");
 puts("PASS: trace ring, sampling window, markers, cancellation");
 return 0;
}
