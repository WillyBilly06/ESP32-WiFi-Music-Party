"""Process-local workaround for this host's denied automatic TCP port binds.

Use available explicit ports outside Windows' dynamic range. No system port,
firewall or unrelated application settings are changed.
"""
import os,secrets,socket

def bind_task_port(sock,host):
    start=18000+secrets.randbelow(2000)
    for offset in range(2000):
        port=18000+(start-18000+offset)%2000
        try:sock.bind((host,port));return
        except OSError as error:
            if getattr(error,'winerror',None) not in [10013,10048] and error.errno not in [13,98]:raise
    raise OSError('No free task-local TCP source port')

def connection(address,timeout=30,source_address=None):
    if os.name!='nt':return socket.create_connection(address,timeout,source_address)
    last=None
    for _ in range(64):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.settimeout(timeout)
        try:
            bind_task_port(sock,'127.0.0.1' if address[0]=='127.0.0.1' else '0.0.0.0')
            sock.connect(address);return sock
        except OSError as error:
            sock.close()
            # A TIME_WAIT tuple can pass bind and fail connect on Windows.
            # No request bytes exist yet; this cannot replay a device action.
            if getattr(error,'winerror',None) not in (10013,10048) and error.errno not in (10013,10048):raise
            last=error
        except BaseException:sock.close();raise
    raise OSError('No usable task-local TCP connection after64 source-port attempts') from last

def socketpair(family=None,type=socket.SOCK_STREAM,proto=0):
    listener=socket.socket(socket.AF_INET,type,proto);client=None;server=None
    try:
        bind_task_port(listener,'127.0.0.1');listener.listen(1)
        client=connection(listener.getsockname(),5)
        server,_=listener.accept();server.settimeout(None);client.settimeout(None)
        return server,client
    except BaseException:
        if client:client.close()
        if server:server.close()
        raise
    finally:listener.close()
