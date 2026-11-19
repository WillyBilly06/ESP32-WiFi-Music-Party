import sys,unittest,struct,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from capture_copies import join_traces,parse,presentation_slack

def trace(kind,copies=3,first=37,highest=100):
    mask=(1<<copies)-1
    rows={n:dict(raw=mask,accepted=mask,done=mask,failed=0,repaired=0,a=[100,200,300,400],b=[120,220,320,420]) for n in range(first,highest+1)}
    return dict(meta=dict(kind=str(kind),generation='7',highest=str(highest)),rows=rows)

class CopyEvidence(unittest.TestCase):
    def test_route_flags_are_crc_protected_without_growing_the_record(self):
        nonce='12345678';packed=struct.pack('<QQ8I8B',10,65000,100,200,300,400,120,220,320,420,15,15,15,0,0,1,0,9)
        crc=zlib.crc32(packed)
        text=f'WMP_TRACE_META query={nonce} kind=2 generation=7 highest=10 row_crc=1 route_bits=1\nWMP_TRACE query={nonce} seq=10 pts=65000 raw=15 accepted=15 done=15 failed=0 repaired=0 injected=0 route=9 crc={crc} a=100,200,300,400 b=120,220,320,420\nWMP_TRACE_END query={nonce} rows=1\n'
        self.assertEqual(parse(dict(reply=text,evidence='host-test'),nonce)['rows'][10]['route'],9)
        with self.assertRaisesRegex(RuntimeError,'CRC'):parse(dict(reply=text.replace('route=9','route=1'),evidence='host-test'),nonce)
    def test_row_crc_rejects_silent_decimal_value_corruption(self):
        nonce='12345678';packed=struct.pack('<QQ8I7B',10,65000,100,200,300,400,120,220,320,420,15,15,15,0,0,1,0)
        crc=zlib.crc32(packed)
        text=f'WMP_TRACE_META query={nonce} kind=1 generation=7 highest=10 row_crc=1\nWMP_TRACE query={nonce} seq=10 pts=65000 raw=15 accepted=15 done=15 failed=0 repaired=0 injected=0 crc={crc} a=100,200,300,400 b=120,220,320,420\nWMP_TRACE_END query={nonce} rows=1\n'
        self.assertEqual(parse(dict(reply=text,evidence='host-test'),nonce)['rows'][10]['done'],15)
        with self.assertRaisesRegex(RuntimeError,'CRC'):parse(dict(reply=text.replace('done=15','done=5'),evidence='host-test'),nonce)

    def test_three_copy_run_has_no_phantom_fourth_loss(self):
        rows,_=join_traces(trace(1),trace(2),3)
        self.assertTrue(rows);self.assertTrue(all(r['missing_copies']==[] for r in rows))
    def test_unobserved_sequence_inside_coverage_is_retained(self):
        tx,rx=trace(1),trace(2);del rx['rows'][60]
        rows,_=join_traces(tx,rx,3);row=next(r for r in rows if r['sequence']==60)
        self.assertEqual(row['rx_callback'],0);self.assertEqual(row['missing_copies'],[0,1,2]);self.assertEqual(row['tx_completed'],7)
    def test_startup_and_retired_rows_are_not_called_losses(self):
        rows,coverage=join_traces(trace(1),trace(2,first=80),3)
        self.assertEqual(coverage,dict(first=80,last=88));self.assertTrue(all(r['sequence']>=80 for r in rows))
    def test_four_copy_run_keeps_fourth_copy(self):
        tx,rx=trace(1,4),trace(2,4);rx['rows'][60]['raw']=7
        rows,_=join_traces(tx,rx,4);self.assertEqual(next(r for r in rows if r['sequence']==60)['missing_copies'],[3])
    def test_long_copy_train_requires_larger_settled_margin(self):
        rows,coverage=join_traces(trace(1,4),trace(2,4),4,44)
        self.assertEqual(coverage['last'],56);self.assertTrue(all(r['sequence']<=56 for r in rows))
    def test_empty_or_wrong_generation_coverage_fails(self):
        with self.assertRaises(RuntimeError):join_traces(trace(1),trace(2,first=137,highest=200),3)
        rx=trace(2);rx['meta']['generation']='8'
        with self.assertRaises(RuntimeError):join_traces(trace(1),rx,3)
    def test_parser_ignores_console_noise_but_rejects_stale_trace(self):
        row=dict(reply='\r\nWMP_A2DP_COEX status=0\nWMP_TRACE_META query=abc kind=1 generation=7 highest=100\nWMP_TRACE query=abc seq=100 raw=7 accepted=7 done=7 failed=0 repaired=0 a=1,2,3,0 b=2,3,4,0\nWMP_TRACE_END query=abc rows=1\n',evidence='fixture')
        self.assertIn(100,parse(row,'abc')['rows'])
        with self.assertRaises(RuntimeError):parse(row,'def')
        with self.assertRaises(RuntimeError):parse(dict(row,reply=row['reply'].replace('rows=1','rows=2')),'abc')
    def test_changed_copy_count_is_not_reinterpreted(self):
        with self.assertRaises(RuntimeError):join_traces(trace(1,4),trace(2,4),3)
    def test_presentation_slack_unwraps_device_timestamp(self):
        tx=trace(1);tx['rows'][60].update(pts=1010,a=[0xfffffff0,0,10,0],b=[0xfffffff5,5,15,0])
        rows=[dict(sequence=60)]
        presentation_slack(rows,tx,dict(lines={'WMP_PHY':dict(source_tsf=1000,clock_local_us=0x100000000)}))
        self.assertEqual(rows[0]['tx_submit_headroom_us'],[26,10,0,None])
        self.assertEqual(rows[0]['tx_callback_headroom_us'],[21,5,-5,None])

if __name__=='__main__':unittest.main()
