"""Attribute each lost broadcast copy to what held the follower's radio.

Inputs: a directory with COM6.txt / COM7.txt RADIOTRACE dumps (WMP_RT lines).
COM7 (source) records kind 3 = TX done (a: copy|0x80 replay|0x40 failed, c: sequence).
COM6 (follower) records kind 6 = RX (a&0x0f copy, a&0x80 replay, c: sequence),
kind 8 = coex phase, 11/12 = Wi-Fi/BT coex request (a: event, b: duration us, c: latency us),
13/14 = Wi-Fi/BT release (a: event).
Clock offset COM6-COM7 is the median of (rx - txdone) over received originals.
"""
import re,sys,statistics as st,collections
d=sys.argv[1]
def load(p):
    rows=[dict((k,int(v)) for k,v in re.findall(r'(\w+)=(\d+)',l) if k!='query') for l in open(f'{d}/{p}.txt',encoding='ascii',errors='replace') if l.startswith('WMP_RT ')]
    return [r for r in rows if all(x in r for x in ('k','at','a','b','c'))]
tx=load('COM7');rx=load('COM6')
done={(r['c'],r['a']&0x0f):r['at'] for r in tx if r['k']==3 and not(r['a']&0xc0)}
got={}
for r in rx:
    if r['k']==6 and not(r['a']&0x80) and not(r['a']&0x20):got.setdefault((r['c'],r['a']&0x0f),r['at'])
pairs=[got[k]-done[k] for k in got if k in done]
if not pairs:sys.exit('no matched originals')
off=st.median(pairs);spread=sorted(x-off for x in pairs)
print(f'matched {len(pairs)} originals; offset {off}; arrival spread p05 {spread[len(spread)//20]} p95 {spread[19*len(spread)//20]} us')
lo=min(r['at'] for r in rx)-off+20000;hi=max(r['at'] for r in rx)-off-20000
sent=[(t,k) for k,t in done.items() if lo<t<hi]
lost=[(t,k) for t,k in sent if k not in got];rcv=[(t,k) for t,k in sent if k in got]
print(f'copies sent in overlap {len(sent)}, lost {len(lost)} ({100*len(lost)/max(1,len(sent)):.1f}%)')
# BT/Wi-Fi request windows on COM6 (local clock)
wins=[]
for r in rx:
    if r['k'] in (11,12):
        start=r['at']+r['c'];end=start+max(r['b'],1);wins.append((start,end,'bt' if r['k']==12 else 'wifi',r['a']))
wins.sort()
ph=sorted((r['at'],r['a']) for r in rx if r['k']==8)
def phase_at(t):
    cur=None
    for at,p in ph:
        if at<=t:cur=p
        else:break
    return cur
def cover(t):
    c=[f'{s}{e}' for (a,b,s,e) in wins if a<=t<=b]
    return c
def tally(items):
    by=collections.Counter();ph_c=collections.Counter()
    for t,k in items:
        tt=t+off;c=cover(tt);by.update(set(c) or {'none'});ph_c[phase_at(tt)]+=1
    return by,ph_c
lb,lp=tally(lost);rb,rp=tally(rcv)
print('cause      lost(share)    received-also-covered(share)')
for key in sorted(set(lb)|set(rb),key=lambda x:-lb.get(x,0)):
    print(f'{key:10} {lb.get(key,0):5} ({100*lb.get(key,0)/max(1,len(lost)):5.1f}%)   {rb.get(key,0):5} ({100*rb.get(key,0)/max(1,len(rcv)):5.1f}%)')
print('COM6 phase at lost',dict(lp),'received',dict(rp))
