"""Wrong/stale firmware cannot qualify a different candidate; no device I/O."""
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import firmware_evidence as evidence

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
def sha(raw):return hashlib.sha256(raw).hexdigest()

class Binding(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='firmware-binding-',dir=ROOT/'build');self.root=Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to((ROOT/'build').resolve()));self.addCleanup(self.temp.cleanup)
        self.app=self.root/'build/app-fixture';self.app.mkdir(parents=True);self.build='a'*16
        deps={'esp_idf':{'revision':'idf-pin'},'btstack':{'revision':'bt-pin'}};write(self.root/'dependencies.lock.json',deps)
        sources={}
        for name in ['firmware/app/main/main.c','protocol/src/wmp_wire.c']:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode());sources[name]=sha(p.read_bytes())
        artifacts=[]
        for offset,name in [(0x1000,'bootloader.bin'),(0x8000,'partition-table.bin'),(0x10000,'wmp_audio.bin')]:
            p=self.app/name;p.write_bytes(name.encode());artifacts.append(dict(offset=offset,path=str(p),bytes=p.stat().st_size,sha256=sha(p.read_bytes())))
        self.manifest=dict(pass_all=True,profile='btstack-custom-wifi-audio-candidate',build_id=self.build,dependencies=deps,sources=sources,artifacts=artifacts);write(self.app/'app.json',self.manifest)
        admitted=[]
        for port,mac in [('COM6','00:01:02:03:04:06'),('COM7','00:01:02:03:04:07')]:
            admitted.append(dict(port=port,base_mac=mac));images=[]
            for a in artifacts:
                raw=Path(a['path']).read_bytes();images.append(dict(a,readback_sha256=sha(raw+b'\xff'*((-len(raw))%4096))))
            path=self.root/'evidence'/('install-'+port+'.json');outside=[dict(offset=0,bytes=4096,md5='b'*32)]
            row=dict(port=port,build_id=self.build,actual_mac=mac,manifest=str(self.app/'app.json'),pass_all=True,errors=[],flash_verified=True,stage='terminal',boots=[dict(identity_verified=True),dict(identity_verified=True)],images=images,outside_before=outside,outside_after=outside,outside_runtime=outside)
            write(path,row);write(self.root/'hardware'/('deployed-'+port+'.json'),dict(port=port,build_id=self.build,base_mac=mac,boot_verified=True,state='installed',receipt=str(path)))
        write(self.root/'hardware/admitted-boards-reference.json',dict(boards=admitted))

    def change(self,path,mutate):
        r=json.loads(path.read_text());mutate(r);write(path,r)

    def test_exact_pair_and_live_id_bind_to_candidate(self):
        p=evidence.installed_pair(self.app,self.root)
        self.assertEqual(p['candidate']['build_id'],self.build)
        for port in ['COM6','COM7']:
            mac=p['boards'][port]['base_mac'];r=evidence.live_info(p,port,'WMP_INFO build='+self.build+' base_mac='+mac+' radio=1 audio_qualified=0\n');self.assertEqual(r['port'],port)
        self.assertFalse(p['audio_qualified'])

    def test_mixed_installed_versions_fail_before_live_access(self):
        self.change(self.root/'hardware/deployed-COM6.json',lambda r:r.update(build_id='c'*16))
        with self.assertRaises(ValueError):evidence.installed_pair(self.app,self.root)

    def test_failed_install_cannot_be_promoted_by_deployed_json(self):
        self.change(self.root/'evidence/install-COM7.json',lambda r:r.update(pass_all=False,errors=['readback mismatch']))
        with self.assertRaises(ValueError):evidence.installed_pair(self.app,self.root)

    def test_padded_readback_identity_is_required(self):
        self.change(self.root/'evidence/install-COM6.json',lambda r:r['images'][2].update(readback_sha256='0'*64))
        with self.assertRaises(ValueError):evidence.installed_pair(self.app,self.root)

    def test_changed_binary_or_current_source_rejects_candidate(self):
        p=self.app/'wmp_audio.bin';p.write_bytes(b'changed')
        with self.assertRaises(ValueError):evidence.candidate(self.app,self.root)
        p.write_bytes(b'wmp_audio.bin');(self.root/'protocol/src/wmp_wire.c').write_bytes(b'edited source')
        with self.assertRaises(ValueError):evidence.candidate(self.app,self.root)

    def test_omitted_source_or_image_coverage_rejects_candidate(self):
        self.change(self.app/'app.json',lambda r:r['sources'].pop('protocol/src/wmp_wire.c'))
        with self.assertRaises(ValueError):evidence.candidate(self.app,self.root)

    def test_erase_extents_protect_nvs_and_otadata(self):
        self.assertEqual(evidence.image_erase_end(0x8000,3072),0x9000)
        self.assertEqual(evidence.image_erase_end(0x1000,0x7000),0x8000)
        for offset,size in [(0x8000,4097),(0x1000,0x7001),(0x10000,0x180001),(0x9000,1),(0xf000,1),(0x10000,0)]:
            with self.assertRaises(ValueError):evidence.image_erase_end(offset,size)
        p=self.app/'partition-table.bin';p.write_bytes(b'x'*4097)
        self.change(self.app/'app.json',lambda r:next(a for a in r['artifacts'] if a['offset']==0x8000).update(bytes=4097,sha256=sha(p.read_bytes())))
        with self.assertRaises(ValueError):evidence.candidate(self.app,self.root)

    def test_missing_boot_or_outside_extent_audit_is_not_complete(self):
        p=self.root/'evidence/install-COM6.json';original=json.loads(p.read_text());self.change(p,lambda r:r.update(boots=[dict(identity_verified=True)]))
        with self.assertRaises(ValueError):evidence.installed_pair(self.app,self.root)
        write(p,original);self.change(p,lambda r:r.update(outside_runtime=[dict(md5='different')]))
        with self.assertRaises(ValueError):evidence.installed_pair(self.app,self.root)

    def test_live_old_image_wrong_mac_and_ambiguous_rows_fail(self):
        p=evidence.installed_pair(self.app,self.root);mac=p['boards']['COM6']['base_mac'];good='WMP_INFO build='+self.build+' base_mac='+mac+'\n'
        for bad in ['WMP_INFO build='+('c'*16)+' base_mac='+mac,good.replace(mac,'00:00:00:00:00:00'),good+good,'WMP_READY build='+self.build]:
            with self.assertRaises(ValueError):evidence.live_info(p,'COM6',bad)

    def test_both_fresh_live_ids_are_required_before_live_binding_is_complete(self):
        p=evidence.installed_pair(self.app,self.root);calls=[]
        def reply(port,line,marker):
            calls.append(port);return dict(reply='WMP_INFO build='+self.build+' base_mac='+p['boards'][port]['base_mac']+'\n',evidence='mock UART only')
        rows=evidence.live_pair(p,reply);self.assertEqual(calls,['COM6','COM7']);self.assertEqual(set(rows),{'COM6','COM7'});self.assertTrue(p['live_info_checked'])
        p=evidence.installed_pair(self.app,self.root)
        def wrong_second(port,line,marker):
            v=reply(port,line,marker)
            if port=='COM7':v['reply']=v['reply'].replace(self.build,'c'*16)
            return v
        with self.assertRaises(ValueError):evidence.live_pair(p,wrong_second)
        self.assertFalse(p['live_info_checked'])

if __name__=='__main__':unittest.main()
