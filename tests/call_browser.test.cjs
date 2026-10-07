// Regression: call startup must not wait on AudioContext.resume or slider input.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:id==='volume'?'30':'',checked:false,hidden:false,disabled:false,replaceChildren(){}});return elements.get(id);};
const order=[];
let resumeCount=0,microphoneCount=0,stopped=0,closed=0;
let defaultInput='airpods',deviceChange,activeContext;
const captures=[],tracks=[],constraintsUsed=[];
const node=()=>({connect(){},disconnect(){}});
class AudioContext {
 constructor(){activeContext=this;this.sampleRate=16000;this.currentTime=0;this.state='running';this.destination={};this.audioWorklet={addModule:async()=>{}};}
 resume(){resumeCount++;order.push('resume');return new Promise(()=>{});}
 close(){closed++;return Promise.resolve();}
 createGain(){this.gain=node();this.gain.gain={value:0,setTargetAtTime:value=>this.gain.gain.value=value};return this.gain;}
 createDynamicsCompressor(){return {...node(),threshold:{},knee:{},ratio:{},attack:{},release:{}};}
 createMediaStreamSource(){return node();}
 createBuffer(channels,frames,rate){return {duration:frames/rate,getChannelData:()=>new Float32Array(frames)};}
 createBufferSource(){return {...node(),start(){},stop(){}};}
}
const document={getElementById:element,querySelectorAll:()=>[]};
const opener={addEventListener(){},open(url,name){order.push('open');this.childName=name;return {};}};
const parent=vm.createContext({document,window:opener,AudioContext,crypto:{randomUUID:()=> 'test-call'},Intl,Option:function(){},setTimeout,clearTimeout,setInterval,clearInterval,fetch:async path=>({ok:path!=='/api/me',json:async()=>path==='/api/calling-codes'?[]:{ready:true}})});
vm.runInContext(fs.readFileSync('verivoice/web/app.js','utf8'),parent);
element('call').onclick({preventDefault(){}});
assert.deepEqual(order.slice(0,2),['resume','open']);
let socket;
class WebSocket {
 static OPEN=1;
 constructor(){socket=this;this.readyState=1;this.bufferedAmount=0;this.sent=[];queueMicrotask(()=>this.onopen?.());}
 send(value){this.sent.push(value);}
 close(){this.readyState=3;}
}
const childWindow={opener,name:opener.childName,addEventListener(){},close(){}};
const child=vm.createContext({document,window:childWindow,AudioContext,AudioWorkletNode:class {constructor(){this.port={};captures.push(this);}connect(){}disconnect(){}},WebSocket,
 navigator:{mediaDevices:{addEventListener(name,callback){if(name==='devicechange')deviceChange=callback;},enumerateDevices:async()=>[{kind:'audioinput',deviceId:'default',groupId:defaultInput,label:`Default - ${defaultInput}`},{kind:'audioinput',deviceId:'laptop',label:'Laptop microphone'},{kind:'audioinput',deviceId:'airpods',label:'AirPods'}],getUserMedia:async constraints=>{microphoneCount++;constraintsUsed.push(constraints);const id=constraints.audio.deviceId?.exact || defaultInput;const track={label:id==='laptop'?'Laptop microphone':'AirPods',getSettings:()=>({deviceId:id}),stop(){stopped++;}};tracks.push(track);return {getTracks:()=>[track],getAudioTracks:()=>[track]};}}},Option:function(label,value){this.label=label;this.value=value;},
 location:{protocol:'http:',host:'127.0.0.1:8000'},fetch:async()=>({ok:true,json:async()=>({phone:'+12025550123'})}),
 setTimeout,clearTimeout,setInterval,clearInterval,atob,Uint8Array,DataView});
vm.runInContext(fs.readFileSync('verivoice/web/call.js','utf8'),child);
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 await settle();
 assert.ok(socket,'Call connection starts without awaiting audio resume or moving slider');
 assert.deepEqual(JSON.parse(socket.sent[0]),{type:'start',phone:'+12025550123'});
 socket.onmessage({data:JSON.stringify({type:'ready'})});await settle();
 assert.equal(microphoneCount,1,'Microphone requested without touching slider');
 socket.onmessage({data:JSON.stringify({type:'audio',pcm:btoa('\0'.repeat(480)),rate:24000})});
 socket.onmessage({data:JSON.stringify({type:'intro_complete'})});
 socket.onmessage({data:JSON.stringify({type:'intro_complete'})});
 await new Promise(resolve=>setTimeout(resolve,150));await settle();
 assert.equal(socket.sent.filter(message=>typeof message==='string'&&JSON.parse(message).type==='intro_played').length,0,'Do not capture the opening prompt as caller speech');
 activeContext.currentTime=1; // Playback finished, but onended was never delivered.
 await new Promise(resolve=>setTimeout(resolve,250));await settle();
 socket.onmessage({data:JSON.stringify({type:'intro_complete'})});
 await new Promise(resolve=>setTimeout(resolve,150));await settle();
 assert.equal(socket.sent.filter(message=>typeof message==='string'&&JSON.parse(message).type==='intro_played').length,1,'Repeated opening completion events must acknowledge playback only once');
 socket.onmessage({data:JSON.stringify({type:'microphone_received'})});
 socket.onmessage({data:JSON.stringify({type:'verification_started'})});await settle();
 assert.equal(element('capture-status').textContent,'Microphone audio received by VeriVoice.');
 assert.match(element('status').textContent,/Hiya/);
 socket.onmessage({data:JSON.stringify({type:'verification_retry',message:'Hiya checked your voice, but confidence is below 0.80.'})});await settle();
 assert.match(element('status').textContent,/below 0.80/);
 const before=resumeCount;
 element('volume').value='55';element('volume').oninput();await settle();
 assert.equal(resumeCount,before,'Volume adjustment never activates or resumes the call');
 assert.equal(microphoneCount,1,'Volume adjustment never requests another microphone');
 assert.equal(element('microphone').value,'','Default input remains selected rather than pinning AirPods');
 defaultInput='laptop';await deviceChange();
 assert.equal(microphoneCount,2,'Changing the system input reconnects the microphone');
 assert.equal(element('mic-name').textContent,'Laptop microphone');
 assert.equal(element('microphone').value,'');
 const pcm=new ArrayBuffer(3200);
 captures.at(-1).port.onmessage({data:{pcm,rms:.005}});
 assert.equal(socket.sent.at(-1),pcm,'Switched laptop microphone audio is sent to verification');
 await deviceChange();
 assert.equal(microphoneCount,2,'Unchanged device enumeration must not repeatedly restart capture');
 element('microphone').value='laptop';await element('microphone').onchange();
 assert.equal(microphoneCount,3);assert.equal(stopped,2);
 assert.equal(constraintsUsed.at(-1).audio.deviceId.exact,'laptop');
 assert.equal(element('microphone').value,'laptop');
 assert.equal(element('mic-name').textContent,'Laptop microphone');
 defaultInput='airpods';await deviceChange();
 assert.equal(microphoneCount,3,'Explicit laptop selection stays selected when the default changes');
 tracks.at(-1).onended();await settle();
 assert.equal(microphoneCount,4,'An ended microphone reconnects to the available default');
 assert.equal(element('microphone').value,'');
 element('hangup').onclick();
 assert.equal(stopped,4);assert.equal(closed,1);
 defaultInput='laptop';await deviceChange();
 assert.equal(microphoneCount,4,'Device changes must not reopen a microphone after hang-up');
 console.log('Browser startup, slider isolation, and hang-up regression checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
