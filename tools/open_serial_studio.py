"""Open the user's existing Serial Studio GUI as a live capture follower.

The persistent Music Party capture retains both UARTs; no CONNECT/reset command
is sent by this launcher. The original dashboard source is used unchanged.
"""
import json,sys,time
from pathlib import Path
from board_io import ROOT

def main():
    location=Path(r'D:\Decompiled IDF\tools\serial_dashboard')
    sys.path.insert(0,str(location))
    import dashboard as gui
    state=json.loads((ROOT/'hardware/active-session.json').read_text())
    if state.get('state')!='running':raise RuntimeError('Start the admitted persistent capture first')
    out=ROOT/'evidence'/('serial-studio-'+str(time.time_ns()))
    root=gui.tk.Tk();app=gui.Dashboard(root,['COM6','COM7'],out,False,115200)
    for panel in app.panels:
        port=panel.port.get().upper()
        path=(Path(state['evidence'])/(port+'.raw')).resolve(strict=True)
        path.relative_to(ROOT/'evidence')
        panel.worker=gui.PortWorker(port,panel.emit,out,
            serial_factory=lambda _port=port,**options:gui.dashboard_serial(_port,**options))
        panel.worker.start()
        if panel.worker.request('PAUSE')!='OK released':raise RuntimeError('GUI did not remain released')
        panel.worker.request('FOLLOW',str(path))
        panel.port_box.configure(state='disabled')
    root.mainloop()

if __name__=='__main__':main()
