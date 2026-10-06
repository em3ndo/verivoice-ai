"use strict";
const $ = id => document.getElementById(id);
let current, audioContext, stream, processor, source, chunks = [], timer, autoStop, startTime, busy = false, setupState = null;
function status(text, error=false) { $("status").textContent=text; $("status").className=error?"error":""; }
async function api(path, body, raw=false) {
 const options = body===undefined ? {} : {method:"POST",headers:{"X-Verivoice":"enrollment","Content-Type":raw?"audio/wav":"application/json"},body:raw?body:JSON.stringify(body)};
 const r=await fetch(path,options); const data=await r.json();
 if(!r.ok) throw new Error(data.error || "The request could not be completed. Check your input and try again.");
 return data;
}
function render(data) {
 current=data; $("account").hidden=true; $("enrollment").hidden=false;
 $("account-phone").textContent=data.phone ? `Account phone: ${data.phone}` : "";
 $("progress").textContent=`${data.accepted} of ${data.total} sentences accepted`;
 $("meter").value=data.accepted; $("phrase").textContent=data.phrase || "";
 $("phrase").lang=data.language; $("record-panel").hidden=!data.phrase;
 $("finish").hidden=data.accepted!==5 || data.state==="complete";
 $("success").hidden=data.state!=="complete";
 $("reference").textContent=data.identity?`Hiya identity: ${data.identity} · Voiceprint: ${data.voiceprint}`:"";
}
async function run(task) {
 if(busy) return; busy=true;
 const buttons=[...document.querySelectorAll("button")]; const states=buttons.map(b=>b.disabled); buttons.forEach(b=>b.disabled=true);
 try { await task(); } catch(e) {status(e.message,true);}
 finally {buttons.forEach((b,i)=>b.disabled=states[i]);busy=false;}
}
function credentials(){return {email:$("email").value,password:$("password").value,language:$("language").value,consent:$("consent").checked,phone_region:$("phone-region").value,phone_number:$("phone-number").value};}
$("account-form").onsubmit=e=>{e.preventDefault();if(setupState && !setupState.ready){status("Account setup is unavailable until the backend configuration listed above is completed.",true);return;}run(async()=>{status("Creating your phrases…");render(await api("/api/register",credentials()));$("password").value="";status("Ready for your first recording.");});};
$("login").onclick=()=>run(async()=>{render(await api("/api/login",credentials()));$("password").value="";status("Your saved progress is ready.");});
$("logout").onclick=()=>run(async()=>{cleanup();await api("/api/logout",{});location.reload();});
function cleanup(){clearInterval(timer);clearTimeout(autoStop);if(processor){processor.onaudioprocess=null;processor.disconnect();processor=null;}if(source){source.disconnect();source=null;}if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}if(audioContext){audioContext.close();audioContext=null;}$("record").disabled=false;$("stop").disabled=true;}
$("record").onclick=async()=>{
 if(busy||stream) return; $("record").disabled=true;
 status("Waiting for microphone access. Allow microphone use in your browser.");
 try {
  if(!navigator.mediaDevices) throw new Error("Microphone capture requires localhost or HTTPS.");
  stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
  audioContext=new AudioContext({sampleRate:16000});await audioContext.resume();
  if(audioContext.sampleRate!==16000) throw new Error("This browser cannot record at 16 kHz. Try Chrome or Edge.");
  chunks=[];source=audioContext.createMediaStreamSource(stream);
  // Simple local demo capture. A production client should use AudioWorklet.
  processor=audioContext.createScriptProcessor(4096,1,1);
  processor.onaudioprocess=e=>chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(processor);processor.connect(audioContext.destination);
  startTime=Date.now();timer=setInterval(()=>$("timer").textContent=`${Math.floor((Date.now()-startTime)/1000)}s`,250);
  autoStop=setTimeout(stopRecording,19500);$("stop").disabled=false;$("logout").disabled=true;
  status("Recording. Read the sentence, then press Stop & check.");
 } catch(e){cleanup();status(e.message,true);}
};
function wavBlob(){
 const length=chunks.reduce((sum,c)=>sum+c.length,0), buffer=new ArrayBuffer(44+length*2),v=new DataView(buffer);
 const str=(offset,s)=>{for(let i=0;i<s.length;i++)v.setUint8(offset+i,s.charCodeAt(i));};
 str(0,"RIFF");v.setUint32(4,36+length*2,true);str(8,"WAVE");str(12,"fmt ");v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,"data");v.setUint32(40,length*2,true);
 let offset=44;for(const chunk of chunks)for(const sample of chunk){const x=Math.max(-1,Math.min(1,sample));v.setInt16(offset,x<0?x*32768:x*32767,true);offset+=2;}
 return new Blob([buffer],{type:"audio/wav"});
}
function stopRecording(){
 if(!stream) return; const blob=wavBlob();cleanup();chunks=[];$("logout").disabled=false;
 run(async()=>{status("Checking language, phrase and voice… This can take a minute.");render(await api(`/api/record/${current.accepted}`,blob,true));status(current.accepted===5?"All recordings accepted. Finish to compute your Hiya voiceprint.":"Recording accepted. Continue with the next sentence.");});
}
$("stop").onclick=stopRecording;
$("finish").onclick=()=>run(async()=>{status("Computing and confirming your Hiya voiceprint…");render(await api("/api/finish",{}));status("Enrollment complete.");});
window.addEventListener("pagehide",cleanup);
async function loadCallingCodes() {
 try {
  const regions=await api("/api/calling-codes");
  const names=typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames(["en"],{type:"region"}) : null;
  const options=regions.map(item=>({...item,name:names ? names.of(item.region) : item.region}));
  options.sort((a,b)=>a.name.localeCompare(b.name));
  $("phone-region").replaceChildren(...options.map(item=>new Option(`${item.name} (${item.code})`,item.region)));
  $("phone-region").value="US";
 } catch(e) {status("Country calling codes could not be loaded. Reload the page to try again.",true);}
}
loadCallingCodes();
(async()=>{try {const s=await api("/api/setup");setupState=s;if(!s.ready){$("configuration").hidden=false;$("configuration").textContent=`Backend setup needed: set ${s.missing.join(", ")} in .env and restart the server.`;}try{render(await api("/api/me"));}catch{}}catch(e){status(e.message,true);}})();
