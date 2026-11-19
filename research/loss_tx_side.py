"""Lost copies vs the SOURCE's own Bluetooth request windows at transmit time.

COM7 trace: kind 3 TX done (a copy|0x40 failed|0x80 replay, c seq), kind 2 TX submit, kind 12 BT request
(a event, b duration, c latency). COM6 trace: kind 6 RX (a&0x0f copy).
"""
import re,sys,statistics as st,collections
d=sys.argv[1]
def load(p):
    rows=[dict((k,int(v)) for k,v in re.findall(r'(\w+)=(\d+)',l) if k!='query') for l in open(f'{d}/{p}.txt',encoding='ascii',errors='replace') if l.startswith('WMP_RT ')]
    return [r for r in rows if all(x in r for x in ('k','at','a','b','c'))]
tx=load('COM7');rx=load('COM6')
done={(r['c'],r['a']&0x0f):r['at'] for r in tx if r['k']==3 and not(r['a']&0xc0)}
sub={(r['c'],r['a']&0x0f):r['at'] for r in tx if r['k']==2 and not(r['a']&0x80)}
got={}
for r in rx:
    if r['k']==6 and not(r['a']&0xa0):got.setdefault((r['c'],r['a']&0x0f),r['at'])
pairs=[got[k]-done[k] for k in got if k in done];off=st.median(pairs)
lo=max(min(r['at'] for r in rx)-off,min(r['at'] for r in tx))+20000;hi=min(max(r['at'] for r in rx)-off,max(r['at'] for r in tx))-20000
sent=[(t,k) for k,t in done.items() if lo<t<hi]
lost=[x for x in sent if x[1] not in got];rcv=[x for x in sent if x[1] in got]
print(f'copies in overlap {len(sent)}, lost {len(lost)} ({100*len(lost)/max(1,len(sent)):.1f}%)')
wins=sorted((r['at']+r['c'],r['at']+r['c']+max(r['b'],1),r['a']) for r in tx if r['k']==12)
def cov(t):return {f'bt{e}' for a,b,e in wins if a-300<=t<=b+300}
def tally(items):
    c=collections.Counter()
    for t,k in items:c.update(cov(t) or {'none'})
    return c
L=tally(lost);R=tally(rcv)
print('source BT window at TX-done   lost(share)   received(share)')
for key in sorted(set(L)|set(R),key=lambda x:-L.get(x,0)):
    print(f'{key:8} {L.get(key,0):5} ({100*L.get(key,0)/max(1,len(lost)):5.1f}%)  {R.get(key,0):5} ({100*R.get(key,0)/max(1,len(rcv)):5.1f}%)')
dur=[done[k]-sub[k] for t,k in sent if k in sub]
if dur:
    ls=[done[k]-sub[k] for t,k in lost if k in sub];rs=[done[k]-sub[k] for t,k in rcv if k in sub]
    print('submit->done us median lost',st.median(ls) if ls else None,'received',st.median(rs) if rs else None)
