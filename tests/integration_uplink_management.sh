#!/bin/sh
set -eu
[ "${1:-}" = "--isolated-vm" ] || { echo "Run only in a disposable VM with --isolated-vm" >&2; exit 2; }
[ ! -e /sys/class/net/eth0 ] || { echo "Refusing a host with eth0" >&2; exit 2; }
cd "$(dirname "$0")/.."
set -eu
sudo python3 - <<'PY'
import sys,json,subprocess,time
sys.path.insert(0,'scripts');import pcs_uplink_management as m
names=['pcsut-mgmt-server','pcsut-mgmt-client'];s,c=names;processes=[]
def run(*a,input=None,check=True):
 r=subprocess.run(a,input=input,text=True,capture_output=True,timeout=15)
 if check and r.returncode:print(r.stderr,flush=True);r.check_returncode()
 return r
def ns(n,*a,**kw):return run('ip','netns','exec',n,*a,**kw)
assert not any(n in run('ip','netns','list').stdout for n in names)
try:
 for n in names:run('ip','netns','add',n)
 run('ip','link','add','pcsut-mg0','type','veth','peer','name','pcsut-mg1')
 for n,dev,addr in [(s,'pcsut-mg0','192.168.50.1/24'),(c,'pcsut-mg1','192.168.50.2/24')]:
  run('ip','link','set',dev,'netns',n);ns(n,'ip','address','add',addr,'dev',dev);ns(n,'ip','link','set',dev,'up');ns(n,'ip','link','set','lo','up')
 ns(c,'ip','address','add','192.168.51.2/24','dev','pcsut-mg1');ns(s,'ip','address','add','192.168.51.1/24','dev','pcsut-mg0')
 script='table inet pcs_wireguard { chain input { type filter hook input priority -20; policy accept; tcp dport { 80, 22 } drop;\n}\n}\ntable inet pcs_stats_api { chain input { type filter hook input priority -15; policy accept; tcp dport 9443 drop;\n}\n}\n'
 ns(s,'nft','-f','-',input=script)
 for port in (80,9443):processes.append(subprocess.Popen(['ip','netns','exec',s,'python3','-m','http.server',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
 time.sleep(.5)
 def reaches(source,port):
  code=f'import socket;s=socket.socket();s.settimeout(1);s.bind(({source!r},0));s.connect(("192.168.50.1",{port}));s.close()'
  return ns(c,'python3','-c',code,check=False).returncode==0
 assert not reaches('192.168.50.2',80) and not reaches('192.168.50.2',9443)
 index=json.loads(ns(s,'ip','-j','link','show','pcsut-mg0').stdout)[0]['ifindex']
 def refresh(rows):
  chains={t:[x['rule'] for x in json.loads(ns(s,'nft','-j','list','chain','inet',t,'input').stdout)['nftables'] if 'rule' in x] for t in m.TABLE_PORTS}
  script=m.transaction(chains,rows);ns(s,'nft','-c','-f','-',input=script);ns(s,'nft','-f','-',input=script)
 refresh([('starlink',index,'192.168.50.0/24')]);refresh([('starlink',index,'192.168.50.0/24')])
 for port in (80,9443):assert reaches('192.168.50.2',port) and not reaches('192.168.51.2',port)
 ns(s,'ip','link','set','pcsut-mg0','down');ns(s,'ip','link','set','pcsut-mg0','name','pcsut-mg-ren');ns(s,'ip','link','set','pcsut-mg-ren','up')
 assert reaches('192.168.50.2',80)
 refresh([]);assert not reaches('192.168.50.2',80) and not reaches('192.168.50.2',9443)
 print('PASS: real nftables default deny, trusted web/API access, untrusted rejection, repeat refresh, rename and grant withdrawal')
finally:
 for p in processes:p.terminate();p.wait(timeout=5)
 for n in names:run('ip','netns','delete',n,check=False)
PY
