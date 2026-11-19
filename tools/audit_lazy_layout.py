"""Bind the codec's flash-resident tables and lazy assembler to a built ELF."""
import argparse,hashlib,json,time
from pathlib import Path
from elftools.elf.elffile import ELFFile
from board_io import ROOT,save

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--app',required=True,type=Path);a=p.parse_args()
    manifest=json.loads((a.app/'app.json').read_text());elf_path=Path(manifest['elf']['path'])
    if not manifest['pass_all'] or hashlib.sha256(elf_path.read_bytes()).hexdigest()!=manifest['elf']['sha256']:raise RuntimeError('Unverified ELF identity')
    required={'btstack_sbc_decoder_bluedroid','sbc_sampling_frequency_table','SynthFrame4SB','SynthFrame8SB','rcos','crc_table'}
    rows=[];errors=[]
    with elf_path.open('rb') as f:
        elf=ELFFile(f)
        for symbol in elf.get_section_by_name('.symtab').iter_symbols():
            if symbol.name=='group' and symbol['st_size']:errors.append('Source assembler still reserves static storage')
            if symbol.name not in required:continue
            index=symbol['st_shndx'];section=elf.get_section(index) if isinstance(index,int) else None
            address=symbol['st_value'];size=symbol['st_size']
            rows.append(dict(name=symbol.name,bytes=size,section=section.name if section else str(index),address=address,section_flags=section['sh_flags'] if section else None))
            # IDF's aggregate output section carries SHF_WRITE even for these
            # C const objects. Placement in the ESP32 DROM address range,
            # rather than that aggregate flag, establishes mapped flash.
            if not section or section.name!='.flash.rodata' or not (0x3f400000<=address and address+size<=0x3f800000):errors.append(symbol.name+' is not in mapped flash DROM')
    if required-{r['name'] for r in rows}:errors.append('Expected linked constants absent')
    out=ROOT/'evidence'/('lazy-layout-'+str(time.time_ns()));out.mkdir()
    report=dict(build_id=manifest['build_id'],elf=manifest['elf'],constants=rows,errors=errors,pass_all=not errors,
                runtime_lifetime_qualified=False,audio_qualified=False,note='ELF placement evidence only; does not prove runtime unload or audio stability.')
    save(out/'receipt.json',report);print(json.dumps(dict(pass_all=report['pass_all'],evidence=str(out),constants=rows)))
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
