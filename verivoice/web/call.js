"use strict";
const $=id=>document.getElementById(id);
let socket, context, micStream, capture, micSource, gain, limiter;
let ended=false, mediaStopped=false, nextAudio=0, sources=new Set(), noticeTimer, closeTimer, goodbyeTimer, introTimer;
let introAcknowledged=false;
let selectedMicrophone="", defaultMicrophoneKey=null, microphoneRequest=0;
const setStatus=text=>$("status").textContent=text;
function clearPlayback(){for(const source of sources){try{source.stop();}catch{}}sources.clear();nextAudio=context?.currentTime || 0;}
function stopMedia(){
 mediaStopped=true;
 $("mic-level").value=0;$("microphone").disabled=true;
 microphoneRequest++;
 micStream?.getTracks().forEach(track=>{track.onended=null;track.stop();});micStream=null;
 if(capture){capture.port.onmessage=null;capture.disconnect();capture=null;}
 micSource?.disconnect();micSource=null;clearPlayback();
 if(context){context.close().catch(()=>{});context=null;}
}
function finish(message="Call ended.",close=false){
 if(ended)return;ended=true;
 clearTimeout(noticeTimer);clearInterval(goodbyeTimer);clearInterval(introTimer);stopMedia();
 if(socket?.readyState===WebSocket.OPEN){socket.send(JSON.stringify({type:"hangup"}));socket.close();}
 setStatus(message);$("hangup").disabled=true;
 if(close)window.close();
}
function bubble(message,removed=false){
 clearTimeout(noticeTimer);$("notice").hidden=false;$("notice").textContent=message;
 $("notice").className=removed?"removed":"";
 if(!removed)noticeTimer=setTimeout(()=>$("notice").hidden=true,6500);
}
async function prepareAudio(){
 context=window.opener?.takeCallAudioContext?.(window.name) || new AudioContext({sampleRate:16000});
 if(context.sampleRate!==16000)throw new Error("This browser cannot capture 16 kHz audio. Try Chrome or Edge.");
 gain=context.createGain();gain.gain.value=Number($("volume").value)/100;
 limiter=context.createDynamicsCompressor();limiter.threshold.value=-12;limiter.knee.value=12;limiter.ratio.value=12;limiter.attack.value=.003;limiter.release.value=.2;
 gain.connect(limiter);limiter.connect(context.destination);
 // Never wait for autoplay permission before connecting or requesting the mic.
 context.resume().catch(()=>{});
}
function playAudio(data){
 if(mediaStopped||!context)return;
 const raw=atob(data.pcm);if(raw.length%2)throw new Error("Invalid voice audio.");
 const bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
 const view=new DataView(bytes.buffer), buffer=context.createBuffer(1,raw.length/2,data.rate);
 const samples=buffer.getChannelData(0);for(let i=0;i<samples.length;i++)samples[i]=Math.max(-.9,Math.min(.9,view.getInt16(i*2,true)/32768));
 const source=context.createBufferSource();source.buffer=buffer;source.connect(gain);
 const start=Math.max(context.currentTime+.03,nextAudio);nextAudio=start+buffer.duration;
 sources.add(source);source.onended=()=>{sources.delete(source);source.disconnect();};source.start(start);
}
async function microphone(deviceId=""){
 if(ended||mediaStopped)return;
 const request=++microphoneRequest;
 const audio={channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true};
 if(deviceId)audio.deviceId={exact:deviceId};
 const fresh=await navigator.mediaDevices.getUserMedia({audio,video:false});
 if(ended||mediaStopped||request!==microphoneRequest){fresh.getTracks().forEach(track=>track.stop());return;}
 try{await context.audioWorklet.addModule("/static/microphone-worklet.js");}
 catch(error){fresh.getTracks().forEach(track=>track.stop());throw error;}
 if(ended||mediaStopped||request!==microphoneRequest){fresh.getTracks().forEach(track=>track.stop());return;}
 micStream?.getTracks().forEach(track=>{track.onended=null;track.stop();});
 micSource?.disconnect();
 if(capture){capture.port.onmessage=null;capture.disconnect();}
 micStream=fresh;
 selectedMicrophone=deviceId;
 const track=fresh.getAudioTracks()[0];
 track.onended=()=>{
  if(micStream!==fresh||ended||mediaStopped)return;
  bubble("Microphone disconnected. Switching to the system default microphone.");
  microphone().catch(()=>bubble("Microphone unavailable. Select a microphone or check your input settings."));
 };
 micSource=context.createMediaStreamSource(micStream);
 capture=new AudioWorkletNode(context,"microphone-capture");
 capture.port.onmessage=event=>{
  if(ended||mediaStopped||socket?.readyState!==WebSocket.OPEN)return;
  if(socket.bufferedAmount>256000){finish("Your connection cannot keep up with microphone audio. Call again.");return;}
  $("mic-level").value=Math.min(1,event.data.rms*12);
  socket.send(event.data.pcm);
 };
 micSource.connect(capture);capture.connect(context.destination);
 context.resume().catch(()=>{});
 await listMicrophones();
}
async function listMicrophones(){
 if(!navigator.mediaDevices.enumerateDevices)return;
 try{
  const devices=(await navigator.mediaDevices.enumerateDevices()).filter(device=>device.kind==="audioinput");
  const track=micStream?.getAudioTracks?.()[0];
  const defaultDevice=devices.find(device=>device.deviceId==="default") || devices[0];
  const key=defaultDevice?JSON.stringify([defaultDevice.deviceId,defaultDevice.groupId,defaultDevice.label]):"";
  const defaultChanged=defaultMicrophoneKey!==null&&defaultMicrophoneKey!==key;
  defaultMicrophoneKey=key;
  $("microphone").replaceChildren(new Option("System default microphone",""),...devices.filter(device=>device.deviceId!=="default").map((device,index)=>new Option(device.label || `Microphone ${index+1}`,device.deviceId)));
  $("microphone").value=selectedMicrophone;
  $("mic-name").textContent=track?.label || "Microphone connected";
  return {defaultChanged,devices};
 }catch{/* Device enumeration is optional; capture can still continue. */}
}
$("microphone").onchange=async()=>{
 const selector=$("microphone");selector.disabled=true;
 try{await microphone(selector.value);}catch{bubble("Could not switch microphones. Check microphone permissions and try again.");await listMicrophones();}
 finally{selector.disabled=false;}
};
navigator.mediaDevices?.addEventListener?.("devicechange",async()=>{
 const result=await listMicrophones();
 if(!result||!micStream||ended||mediaStopped)return;
 const missing=selectedMicrophone&&!result.devices.some(device=>device.deviceId===selectedMicrophone);
 if((!selectedMicrophone&&result.defaultChanged)||missing){
  try{await microphone();}
  catch{bubble("Could not follow the system microphone. Select a microphone or check your input settings.");}
 }
});
async function handle(data){
 switch(data.type){
 case "ready": await microphone();setStatus("Connected. Listen to the verification prompt.");break;
 case "audio": playAudio(data);break;
 case "intro_complete":
  // Completion can be delivered more than once. Multiple polling timers used
  // to keep sending intro_played and resetting the backend's speech buffer.
  if(introAcknowledged||introTimer)return;
  introTimer=setInterval(()=>{
   // Audio clock completion also works if a browser delays onended callbacks.
   if(!context||context.state!=="running"||context.currentTime<nextAudio)return;
   clearInterval(introTimer);
   introTimer=null;introAcknowledged=true;
   if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:"intro_played"}));
   setStatus("Repeat the phrase to verify your voice.");
  },100);break;
 case "listening":setStatus("Repeat the phrase to verify your voice.");break;
 case "microphone_received":$("capture-status").textContent="Microphone audio received by VeriVoice.";break;
 case "verification_waiting":setStatus(data.message);break;
 case "verification_started":setStatus("Sending your recording to Hiya and Soniox to check your voice and language…");break;
 case "verification_retry":setStatus(data.message);break;
 case "verified":setStatus("Voice verified. What would you like to talk about?");break;
 case "interrupted": clearPlayback();break;
 case "confidence": $("confidence").textContent=data.c===null?"Waiting for voice evidence":`Voice confidence: ${data.c.toFixed(2)} · Identity: ${data.ca.toFixed(2)} · Authenticity: ${data.ch.toFixed(2)} · Language: ${data.cl == null ? "—" : data.cl.toFixed(2)}`;break;
 case "warning": bubble(data.message);break;
 case "removed":
  clearInterval(introTimer);stopMedia();bubble(data.message,true);setStatus("Call ended.");
  closeTimer=setTimeout(()=>{finish("Call ended. Return to your account to call again.");window.close();},4000);break;
 case "declined":setStatus(data.message);$("confidence").textContent="";break;
 case "goodbye_complete":
  // Audio may be suspended by autoplay policy. Wait for actual playback to finish.
  goodbyeTimer=setInterval(()=>{
   if(!context||context.state!=="running"||sources.size)return;
   clearInterval(goodbyeTimer);
   if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:"played"}));
   finish("Call ended.",true);
  },100);break;
 case "error":bubble(data.message,true);finish(data.message);break;
 }
}
$("volume").oninput=()=>{
 if(gain&&context)gain.gain.setTargetAtTime(Number($("volume").value)/100,context.currentTime,.05);
};
$("hangup").onclick=()=>finish("Call ended.",true);
window.addEventListener("pagehide",()=>{clearTimeout(closeTimer);finish();});
(async()=>{
 try{
  const response=await fetch("/api/me");if(!response.ok)throw new Error("Sign in on the account page before calling.");
  const account=await response.json();if(ended)return;
  await prepareAudio();if(ended)return;
  socket=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/api/call`);
  socket.binaryType="arraybuffer";
  socket.onopen=()=>socket.send(JSON.stringify({type:"start",phone:account.phone}));
  // Serialize messages: microphone permission must settle before handling subsequent audio.
  let pending=Promise.resolve();
  socket.onmessage=event=>{pending=pending.then(()=>{if(!ended)return handle(JSON.parse(event.data));}).catch(()=>finish("Call audio could not start. Check microphone permissions and call again."));};
  socket.onerror=()=>finish("Unable to connect. Check that VeriVoice is running and call again.");
  socket.onclose=()=>{if(!closeTimer&&!ended)finish("Call ended. Return to your account to call again.");};
 }catch(error){finish(error.message);}
})();
