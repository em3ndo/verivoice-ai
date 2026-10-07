// Regression: call startup must not wait on AudioContext.resume or slider input.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:id==='volume'?'30':'',checked:false,hidden:false,disabled:false,replaceChildren(){}});return elements.get(id);};
const order=[];
let resumeCount=0,microphoneCount=0,stopped=0,closed=0;
const node=()=>({connect(){},disconnect(){}});
class AudioContext {
 constructor(){this.sampleRate=16000;this.currentTime=0;this.state='running';this.destination={};this.audioWorklet={addModule:async()=>{}};}
 resume(){resumeCount++;order.push('resume');return new Promise(()=>{});}
 close(){closed++;return Promise.resolve();}
 createGain(){this.gain=node();this.gain.gain={value:0,setTargetAtTime:value=>this.gain.gain.value=value};return this.gain;}
 createDynamicsCompressor(){return {...node(),threshold:{},knee:{},ratio:{},attack:{},release:{}};}
 createMediaStreamSource(){return node();}
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
const child=vm.createContext({document,window:childWindow,AudioContext,AudioWorkletNode:class {constructor(){this.port={};}connect(){}disconnect(){}},WebSocket,
 navigator:{mediaDevices:{getUserMedia:async()=>{microphoneCount++;return {getTracks:()=>[{stop(){stopped++;}}]};}}},
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
 const before=resumeCount;
 element('volume').value='55';element('volume').oninput();await settle();
 assert.equal(resumeCount,before,'Volume adjustment never activates or resumes the call');
 assert.equal(microphoneCount,1,'Volume adjustment never requests another microphone');
 element('hangup').onclick();
 assert.equal(stopped,1);assert.equal(closed,1);
 console.log('Browser startup, slider isolation, and hang-up regression checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
