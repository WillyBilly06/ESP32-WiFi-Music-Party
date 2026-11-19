package org.wmp.partyprobe;
import android.app.*;
import android.bluetooth.*;
import android.content.*;
import android.os.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class GattProbe extends Service {
  final Handler handler=new Handler(Looper.getMainLooper());
  final UUID party=UUID.fromString("aefb0001-2c51-4b0d-b100-ec5b00000001");
  final UUID controlId=UUID.fromString("aefb0002-2c51-4b0d-b100-ec5b00000001");
  final UUID statusId=UUID.fromString("aefb0003-2c51-4b0d-b100-ec5b00000001");
  final UUID volumeId=UUID.fromString("aefb0004-2c51-4b0d-b100-ec5b00000001");
  final UUID cccd=UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
  BluetoothGatt gatt;BluetoothGattCharacteristic statusValue,volumeValue,controlValue;
  final ArrayDeque<BluetoothGattCharacteristic> reads=new ArrayDeque<>();
  File file;String run;boolean ended,discovered;int readCount,notifyCount,volume=-1;boolean writePassed,subscribed,stress,pendingWrite;int stressWrites,writeSerial;
  @Override public IBinder onBind(Intent intent){return null;}
  synchronized void log(String value) {
    try(FileOutputStream stream=new FileOutputStream(file,true)){
      stream.write(("run="+run+" phone_us="+(SystemClock.elapsedRealtimeNanos()/1000)+" "+value+"\n").getBytes(StandardCharsets.UTF_8));
    }catch(Exception error){android.util.Log.e("WMPProbe","log",error);}
  }
  static String hex(byte[] value){StringBuilder out=new StringBuilder();if(value!=null)for(byte b:value)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
  protected int foregroundId(){return 1;}
  void resetSession(){
    if(file!=null && !ended)log("END reason=superseded pending_write="+pendingWrite);
    ended=true;handler.removeCallbacksAndMessages(null);BluetoothGatt previous=gatt;gatt=null;
    if(previous!=null){previous.disconnect();previous.close();}
    reads.clear();statusValue=volumeValue=controlValue=null;
    discovered=writePassed=subscribed=stress=pendingWrite=false;
    readCount=notifyCount=stressWrites=0;volume=-1;++writeSerial;ended=false;
  }
  void awaitWrite(){
    pendingWrite=true;final int serial=++writeSerial;
    handler.postDelayed(()->{if(!ended && pendingWrite && serial==writeSerial){log("WRITE_TIMEOUT sequence="+stressWrites+" replayed=0");finish("write_ack_timeout");}},8000);
  }
  @Override public int onStartCommand(Intent intent,int flags,int id){
    if(intent==null){stopSelf();return START_NOT_STICKY;}
    resetSession();
    run=intent.getStringExtra("run");String target=intent.getStringExtra("target");
    if(run==null || !run.matches("[a-f0-9]{8,32}") || !("08:B6:1F:B8:66:26".equals(target)||"6C:C8:40:42:BF:66".equals(target))){stopSelf();return START_NOT_STICKY;}
    file=new File(getFilesDir(),"gatt-"+run+".log");
    NotificationManager manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
    manager.createNotificationChannel(new NotificationChannel("test","WMP test",NotificationManager.IMPORTANCE_LOW));
    startForeground(foregroundId(),new Notification.Builder(this,"test").setContentTitle("WMP GATT verification").setSmallIcon(android.R.drawable.stat_sys_data_bluetooth).build());
    log("START target="+target);
    try{
      BluetoothAdapter adapter=((BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();
      gatt=adapter.getRemoteDevice(target).connectGatt(this,false,callback,BluetoothDevice.TRANSPORT_LE,BluetoothDevice.PHY_LE_1M_MASK,handler);
      int seconds=intent.getIntExtra("seconds",0);stress=seconds>0;
      if(seconds<0 || seconds>7200)throw new IllegalArgumentException("duration");
      handler.postDelayed(()->finish(stress && writePassed && subscribed && notifyCount>=3 && stressWrites>0?"passed":(stress?"coverage_failed":"timeout")),stress?seconds*1000L:45000L);
    }catch(Exception error){log("ERROR "+error);finish("error");}
    return START_NOT_STICKY;
  }
  final BluetoothGattCallback callback=new BluetoothGattCallback(){
    @Override public void onConnectionStateChange(BluetoothGatt link,int status,int state){
      if(ended || link!=gatt)return;
      log("STATE status="+status+" state="+state);
      if(status==0 && state==BluetoothProfile.STATE_CONNECTED){
        // This exact test device changed its ATT database. Request fresh discovery.
        try{Object result=BluetoothGatt.class.getMethod("refresh").invoke(link);log("CACHE_REFRESH result="+result);}catch(Exception error){log("CACHE_REFRESH unavailable="+error.getClass().getSimpleName());}
        handler.postDelayed(()->link.discoverServices(),600);
      }else if(!ended && (status!=0 || state==BluetoothProfile.STATE_DISCONNECTED))finish("disconnected");
    }
    @Override public void onServicesDiscovered(BluetoothGatt link,int status){
      if(ended || link!=gatt)return;
      if(discovered)return;discovered=true;Set<UUID> actual=new HashSet<>();
      for(BluetoothGattService service:link.getServices()){
        actual.add(service.getUuid());log("SERVICE uuid="+service.getUuid());
        for(BluetoothGattCharacteristic value:service.getCharacteristics()){
          log("CHAR service="+service.getUuid()+" uuid="+value.getUuid()+" properties="+value.getProperties());
          if((value.getProperties()&BluetoothGattCharacteristic.PROPERTY_READ)!=0)reads.add(value);
          if(value.getUuid().equals(controlId))controlValue=value;
          if(value.getUuid().equals(statusId))statusValue=value;if(value.getUuid().equals(volumeId))volumeValue=value;
        }
      }
      boolean complete=status==0 && actual.contains(party);
      for(String id:new String[]{"1800","1801","180a"})complete&=actual.contains(UUID.fromString("0000"+id+"-0000-1000-8000-00805f9b34fb"));
      log("DISCOVERY status="+status+" services="+actual.size()+" complete="+complete);
      if(!complete || statusValue==null || volumeValue==null || controlValue==null){finish("incomplete_services");return;}nextRead();
    }
    @Override public void onCharacteristicRead(BluetoothGatt link,BluetoothGattCharacteristic value,int status){
      if(ended || link!=gatt)return;
      byte[] data=value.getValue();log("READ uuid="+value.getUuid()+" status="+status+" value="+hex(data));
      if(status!=0){finish("read_failed");return;}++readCount;
      if(value.getUuid().equals(volumeId) && data!=null && data.length==1)volume=data[0]&255;nextRead();
    }
    @Override public void onDescriptorWrite(BluetoothGatt link,BluetoothGattDescriptor value,int status){
      if(ended || link!=gatt)return;
      log("SUBSCRIBE status="+status);if(status!=0){finish("subscribe_failed");return;}subscribed=true;
      if(volume<0 || volume>100){finish("invalid_volume");return;}
      volumeValue.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);volumeValue.setValue(new byte[]{(byte)volume});
      awaitWrite();if(!link.writeCharacteristic(volumeValue))finish("write_refused");
    }
    @Override public void onCharacteristicWrite(BluetoothGatt link,BluetoothGattCharacteristic value,int status){
      if(ended || link!=gatt)return;
      pendingWrite=false;++writeSerial;
      if(value.getUuid().equals(controlId)){
        log("LOAD_ACK sequence="+stressWrites+" status="+status);if(status!=0){finish("load_failed");return;}
        handler.postDelayed(loadWrite,100);return;
      }
      log("WRITE uuid="+value.getUuid()+" status="+status+" value="+hex(value.getValue()));writePassed=status==0;
      if(!writePassed)finish("write_failed");else if(stress)handler.post(loadWrite);else maybeDone();
    }
    @Override public void onCharacteristicChanged(BluetoothGatt link,BluetoothGattCharacteristic value){
      if(ended || link!=gatt)return;
      if(!value.getUuid().equals(statusId))return;++notifyCount;log("NOTIFY value="+hex(value.getValue()));maybeDone();
    }
  };
  void nextRead(){
    if(ended)return;
    BluetoothGattCharacteristic value=reads.poll();
    if(value!=null){if(!gatt.readCharacteristic(value))finish("read_refused");return;}
    BluetoothGattDescriptor descriptor=statusValue.getDescriptor(cccd);
    if(descriptor==null || !gatt.setCharacteristicNotification(statusValue,true)){finish("no_cccd");return;}
    descriptor.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);if(!gatt.writeDescriptor(descriptor))finish("subscribe_refused");
  }
  final Runnable loadWrite=new Runnable(){public void run(){
    if(ended || pendingWrite || controlValue==null)return;byte[] data=new byte[9];data[0]=0x10;++stressWrites;
    for(int i=0;i<4;i++)data[i+1]=(byte)(stressWrites>>(8*i));controlValue.setValue(data);controlValue.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
    awaitWrite();if(!gatt.writeCharacteristic(controlValue)){pendingWrite=false;finish("load_refused");}
  }};
  void maybeDone(){if(!stress && writePassed && subscribed && notifyCount>=3)finish("passed");}
  synchronized void finish(String reason){
    if(ended)return;ended=true;handler.removeCallbacksAndMessages(null);
    log("END reason="+reason+" reads="+readCount+" notifications="+notifyCount+" write_pass="+writePassed+" load_writes="+stressWrites);
    if(gatt!=null){gatt.disconnect();gatt.close();gatt=null;}stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();
  }
  @Override public void onDestroy(){finish("destroyed");super.onDestroy();}
}
