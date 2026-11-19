package org.wmp.partyprobe;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.TextView;
import android.bluetooth.*;
import android.content.Context;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
public class PartyProbe extends Activity {
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);TextView view=new TextView(this);view.setText("WMP GATT verification. Audio focus and media controls are unchanged.");setContentView(view);
    if("inspect".equals(getIntent().getStringExtra("mode"))){
      String run=getIntent().getStringExtra("run");if(run==null || !run.matches("[a-f0-9]{8,32}"))return;
      File log=new File(getFilesDir(),"inspect-"+run+".log");
      BluetoothManager manager=(BluetoothManager)getSystemService(Context.BLUETOOTH_SERVICE);
      for(BluetoothDevice device:manager.getConnectedDevices(BluetoothProfile.GATT)){
        String address=device.getAddress();if(address.equals("08:B6:1F:B8:66:26") || address.equals("6C:C8:40:42:BF:66"))append(log,"GATT_CONNECTED "+address);
      }
      for(String address:new String[]{"08:B6:1F:B8:66:26","6C:C8:40:42:BF:66"}){
        BluetoothDevice device=manager.getAdapter().getRemoteDevice(address);append(log,"TARGET "+address+" type="+device.getType()+" bond="+device.getBondState());
      }
      return;
    }
    if("pair".equals(getIntent().getStringExtra("mode"))){
      String target=getIntent().getStringExtra("target"),run=getIntent().getStringExtra("run");
      if(!("6C:C8:40:42:BF:66".equals(target)||"08:B6:1F:B8:66:26".equals(target)) || run==null || !run.matches("[a-f0-9]{8,32}"))return;
      File log=new File(getFilesDir(),"pair-"+run+".log");
      new Thread(()->{
        try{
          BluetoothDevice device=((BluetoothManager)getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter().getRemoteDevice(target);
          append(log,"BEFORE type="+device.getType()+" bond="+device.getBondState());
          // A public RFCOMM SDP lookup selects the BR/EDR transport explicitly.
          // The speaker exposes A2DP, not an RFCOMM port; socket failure is
          // expected, but its real Classic SDP response updates device type.
          BluetoothSocket socket=device.createInsecureRfcommSocketToServiceRecord(UUID.fromString("0000110b-0000-1000-8000-00805f9b34fb"));
          try{socket.connect();append(log,"SOCKET unexpectedly connected");}catch(IOException expected){append(log,"SDP_PROBE "+expected.getMessage());}finally{socket.close();}
          append(log,"AFTER type="+device.getType()+" bond="+device.getBondState()+" bond_requested="+device.createBond());
        }catch(Exception error){append(log,"ERROR "+error);}
      },"WMPClassicProbe").start();return;
    }
    Intent service=new Intent(this,GattProbe.class);service.putExtras(getIntent());startForegroundService(service);
    String second=getIntent().getStringExtra("target2"),secondRun=getIntent().getStringExtra("run2");
    if(second!=null && secondRun!=null && !second.equals(getIntent().getStringExtra("target"))){
      Intent peer=new Intent(this,GattProbeSecond.class);
      peer.putExtra("target",second);peer.putExtra("run",secondRun);peer.putExtra("seconds",getIntent().getIntExtra("seconds",0));
      startForegroundService(peer);
    }
  }
  static synchronized void append(File file,String value){
    try(FileOutputStream out=new FileOutputStream(file,true)){out.write((value+"\n").getBytes(StandardCharsets.UTF_8));}catch(Exception ignored){}
  }
}
