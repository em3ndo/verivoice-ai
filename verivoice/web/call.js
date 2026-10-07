"use strict";
const $=id=>document.getElementById(id);
let socket, context, micStream, capture, micSource, gain, limiter;
let ended=false, mediaStopped=false, nextAudio=0, sources=new Set(), noticeTimer, closeTimer, goodbyeTimer, introTimer;
const setStatus=text=>$("status").textContent=text;
function clearPlayback(){for(const source of sources){try{source.stop();}catch{}}sources.clear();nextAudio=context?.currentTime || 0;}
function stopMedia(){
 mediaStopped=true;
 micStream?.getTracks().forEach(track=>track.stop());micStream=null;
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
async function microphone(){
 micStream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
 if(ended||mediaStopped){micStream.getTracks().forEach(track=>track.stop());micStream=null;return;}
 await context.audioWorklet.addModule("/static/microphone-worklet.js");
 if(ended||mediaStopped)return;
 micSource=context.createMediaStreamSource(micStream);
 capture=new AudioWorkletNode(context,"microphone-capture");
 capture.port.onmessage=event=>{
  if(ended||mediaStopped||socket?.readyState!==WebSocket.OPEN)return;
  if(socket.bufferedAmount>256000){finish("Your connection cannot keep up with microphone audio. Call again.");return;}
  socket.send(event.data);
 };
 micSource.connect(capture);capture.connect(context.destination);
 context.resume().catch(()=>{});
 setStatus("Connected. Listen to the verification prompt.");
}
async function handle(data){
 switch(data.type){
 case "ready": await microphone();break;
 case "audio": playAudio(data);break;
 case "intro_complete":
  introTimer=setInterval(()=>{
   if(!context||context.state!=="running"||sources.size)return;
   clearInterval(introTimer);
   if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:"intro_played"}));
   setStatus("Repeat the phrase to verify your voice.");
  },100);break;
 case "verified":setStatus("Voice verified. What would you like to talk about?");break;
 case "interrupted": clearPlayback();break;
 case "confidence": $("confidence").textContent=data.c===null?"Waiting for voice evidence":`Voice confidence: ${data.c.toFixed(2)}`;break;
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
