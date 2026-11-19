"""Single owned Linux SSH session. Credentials arrive on stdin, never argv/files.

After authentication, each stdin JSON line supplies one script. Results contain
only that script's exit code/stdout/stderr. No command replay occurs on failure.
"""
import argparse,base64,getpass,hashlib,json,sys,time
from pathlib import Path
import paramiko
from local_ipc import connection as task_connection

ROOT=Path(__file__).resolve().parents[1]
def report(value):print(json.dumps(value),flush=True)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--host',required=True);a=p.parse_args()
    credential=json.loads(getpass.getpass('SSH credentials (not echoed): ') if sys.stdin.isatty() else sys.stdin.readline());client=None;user=None
    known=ROOT/'hardware/linux-known-hosts';known.touch(exist_ok=True)
    for candidate in credential['users']:
        c=paramiko.SSHClient();c.load_host_keys(str(known));c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:c.connect(a.host,username=candidate,password=credential['password'],sock=task_connection((a.host,22),15),look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15);client=c;user=candidate;break
        except paramiko.AuthenticationException:c.close()
    credential.clear()
    if client is None:raise RuntimeError('Authentication failed for the two user-supplied username spellings')
    key=client.get_transport().get_remote_server_key();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    report(dict(connected=True,host=a.host,user=user,host_key=fingerprint))
    try:
        while True:
            line=getpass.getpass('') if sys.stdin.isatty() else sys.stdin.readline()
            if not line:break
            request=json.loads(line)
            if request.get('exit'):break
            if 'upload' in request:
                item=request['upload'];path=item['path'];root='/home/'+user+'/wmp-linux-source/'
                if not path.startswith(root) or '..' in Path(path).parts:raise ValueError('Upload outside owned Linux test folder')
                raw=base64.b64decode(item['base64'],validate=True)
                if len(raw)>1048576:raise ValueError('Upload too large')
                with client.open_sftp() as sftp:
                    with sftp.file(path,'wb') as destination:destination.write(raw)
                report(dict(id=request.get('id'),status=0,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()));continue
            script=request['script'];timeout=request.get('timeout',60)
            stdin,stdout,stderr=client.exec_command(script,timeout=timeout)
            if request.get('stdin') is not None:stdin.write(request['stdin']);stdin.flush()
            stdin.close()
            channel=stdout.channel;out=bytearray();err=bytearray();deadline=time.monotonic()+timeout;unknown=False
            while True:
                while channel.recv_ready():
                    block=channel.recv(8192);out.extend(block[:max(0,1048576-len(out))])
                while channel.recv_stderr_ready():
                    block=channel.recv_stderr(8192);err.extend(block[:max(0,262144-len(err))])
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
                if time.monotonic()>=deadline:unknown=True;channel.close();break
                time.sleep(.02)
            report(dict(id=request.get('id'),status=None if unknown else channel.recv_exit_status(),outcome_unknown=unknown,stdout=out.decode('utf-8','replace'),stderr=err.decode('utf-8','replace')))

    finally:client.close()
if __name__=='__main__':main()
