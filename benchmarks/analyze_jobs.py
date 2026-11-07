#!/usr/bin/env python3
"""Uses timings already recorded by finished jobs. No GPU."""
import json, glob, csv, os, re, statistics as st
import numpy as np
REF=11.893
rows=[]
for m in glob.glob('/home/pavan/voice/jobs/*/work/chunks/*/meta.json'):
    job=m.split('/')[5]
    for s in json.load(open(m,encoding='utf-8'))['segments']:
        if s['type']=='speech' and not s.get('retried'):
            rows.append((job,s['seconds'],s['gen_seconds'],len(s['text'].encode()),s['est_seconds'],s['text']))
print('chunks',len(rows),'audio_s',round(sum(r[1] for r in rows)),'gen_s',round(sum(r[2] for r in rows)))
sec=np.array([r[1] for r in rows]);gen=np.array([r[2] for r in rows]);bytes_=np.array([r[3] for r in rows])
# requested mel length follows text bytes: ref_len/ref_bytes*gen_bytes
ref_bytes=len(open('/home/pavan/voice/voices/pavan/ref.txt',encoding='utf-8').read().encode())
req=REF/ref_bytes*bytes_
L=REF+req
print('ref_bytes',ref_bytes,'requested_gen_s mean',round(req.mean(),1),'actual kept mean',round(sec.mean(),1))
print('kept/requested ratio mean',round((sec/req).mean(),2))
A=np.vstack([L**2,L,np.ones_like(L)]).T
c,res,_,_=np.linalg.lstsq(A,gen,rcond=None); pred=A@c
print('fit gen = %.4f L^2 + %.3f L + %.2f, R2=%.3f'%(c[0],c[1],c[2],1-((gen-pred)**2).sum()/((gen-gen.mean())**2).sum()))
print('\nbucket(by kept s)  n  gen/audio')
for lo,hi in [(0,5),(5,10),(10,15),(15,20),(20,25),(25,40)]:
    k=(sec>=lo)&(sec<hi)
    if k.sum(): print(f'{lo:>3}-{hi:<3} n={k.sum():<3} audio={sec[k].sum():6.0f}s rtf={gen[k].sum()/sec[k].sum():.2f} mean_gen={gen[k].mean():.1f}s')
print('\njobs (wall vs synthesis)')
for sp in sorted(glob.glob('/home/pavan/voice/jobs/*/status.json')):
    s=json.load(open(sp));mm=s.get('metrics')
    if mm: print(f"{sp.split('/')[5][:44]:44} files={mm['files']} audio={mm['audio_minutes']:.2f}m gen={mm['generation_seconds']:.0f}s wall={mm['wall_seconds']:.0f}s outside={mm['wall_seconds']-mm['generation_seconds']:.0f}s rtf_syn={mm['rtf_synthesis']} rtf_wall={mm['rtf_wall']} vram={mm['peak_vram_mib']}")
