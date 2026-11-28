#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "trace.h"
static uint32_t now_us;
static int32_t position;
uint32_t micros(void){return now_us;}
int32_t abs_encoder_track(void){return position;}
static void check(int ok,const char *s){if(!ok){fprintf(stderr,"FAIL: %s\n",s);exit(1);}}
int main(void)
{
 unsigned i,samples=0;
 trace_init(); trace_begin(100,200,0,7);
 check(g_trace.active,"begin activates trace");
 check(g_trace.records[0].kind==TRACE_BEGIN,"begin marker");
 trace_event_queued();
 check(g_trace.records[1].kind==TRACE_EVENT_QUEUED,"queue marker");
 now_us=3000; trace_service(); check(g_trace.write_count==2,"no early sample");
 for(i=1;i<=200;i++){now_us=i*TRACE_SAMPLE_US;position=100+(int32_t)i;trace_service();}
 check(!g_trace.active,"window ends at one second");
 for(i=0;i<g_trace.write_count && i<TRACE_CAPACITY;i++)
  if(g_trace.records[i].kind==TRACE_SAMPLE)samples++;
 check(samples>=190 && samples<=200,"approximately 5 ms sample cadence");
 check(g_trace.records[(g_trace.write_count-1)%TRACE_CAPACITY].kind==TRACE_FINISHED,"finished marker");
 trace_begin(10,20,0,9); trace_new_command();
 check(!g_trace.active,"new command cancels observation");
 check(g_trace.records[(g_trace.write_count-1)%TRACE_CAPACITY].kind==TRACE_CANCELLED,"cancel marker");
 puts("PASS: trace ring, sampling window, markers, cancellation");
 return 0;
}
