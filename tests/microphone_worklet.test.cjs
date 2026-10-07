const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
let Processor;
class AudioWorkletProcessor{constructor(){this.messages=[];this.port={postMessage:message=>this.messages.push(message)};}}
vm.runInNewContext(fs.readFileSync('verivoice/web/microphone-worklet.js','utf8'),{
 AudioWorkletProcessor,Int16Array,registerProcessor:(name,implementation)=>Processor=implementation
});
const capture=new Processor();
const silent=new Float32Array(128),voice=new Float32Array(128).fill(.004);
for(let i=0;i<13;i++){
 const output=new Float32Array(128).fill(1);
 capture.process([[silent,voice]],[[output]]);
 assert.ok(output.every(value=>value===0),'Microphone must not play back through speakers');
}
assert.equal(capture.messages.length,1);
const frame=new Int16Array(capture.messages[0].pcm);
assert.ok(frame.every(value=>value>0),'A silent first channel must not hide the active second channel');
assert.ok(capture.messages[0].rms>.003,'Quiet microphone signal is captured without amplification');
console.log('Multichannel and quiet-microphone capture checks passed.');
