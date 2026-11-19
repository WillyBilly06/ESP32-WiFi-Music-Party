"""Run the unchanged pinned IDF CLI with a local Windows socketpair workaround."""
import os,runpy,socket,sys
from pathlib import Path
if os.name=='nt':
    from local_ipc import socketpair
    socket.socketpair=socketpair
entry=Path(sys.argv[1]).resolve();sys.argv=sys.argv[1:];sys.path.insert(0,str(entry.parent))
runpy.run_path(str(entry),run_name='__main__')
