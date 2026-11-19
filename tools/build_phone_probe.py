"""Build the owned, exact-target GATT test APK using the installed Android SDK."""
import hashlib,json,os,subprocess,time,zipfile
from pathlib import Path
from board_io import ROOT,save
def main():
    sdk=Path(os.environ['LOCALAPPDATA'])/'Android/Sdk';bt=sdk/'build-tools/36.0.0';jar=sdk/'platforms/android-36/android.jar'
    out=ROOT/'build'/('gatt-probe-'+str(time.time_ns()));out.mkdir();classes=out/'classes';classes.mkdir();dex=out/'dex';dex.mkdir()
    source=ROOT/'tools/android';log=out/'build.log'
    def call(args):
        r=subprocess.run([str(a) for a in args],capture_output=True,text=True)
        with log.open('a') as f:f.write(r.stdout+r.stderr)
        if r.returncode:raise RuntimeError('Build failed; see '+str(log))
    call(['javac','-source','8','-target','8','-cp',jar,'-d',classes,*source.glob('*.java')])
    call([bt/'d8.bat','--min-api','26','--lib',jar,'--output',dex,*classes.rglob('*.class')])
    unsigned=out/'unsigned.apk';call([bt/'aapt2.exe','link','--manifest',source/'AndroidManifest.xml','-I',jar,'-o',unsigned])
    with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as z:z.write(dex/'classes.dex','classes.dex')
    aligned=out/'aligned.apk';call([bt/'zipalign.exe','-p','4',unsigned,aligned])
    key=ROOT/'build/phone-probe-signing/debug.keystore';key.parent.mkdir(exist_ok=True)
    if not key.exists():call(['keytool','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','wmpdebug','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=WMP Development'])
    apk=out/'wmp-gatt-probe.apk';call([bt/'apksigner.bat','sign','--ks',key,'--ks-pass','pass:android','--out',apk,aligned]);call([bt/'apksigner.bat','verify',apk])
    receipt=dict(apk=str(apk),sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),package='org.wmp.partyprobe',sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file()})
    save(out/'receipt.json',receipt);print(json.dumps(receipt))
if __name__=='__main__':main()
