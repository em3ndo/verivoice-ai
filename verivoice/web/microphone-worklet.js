class MicrophoneCapture extends AudioWorkletProcessor {
 constructor(){super();this.frame=new Int16Array(1600);this.offset=0;}
 process(inputs,outputs){
  // Only microphone PCM is transmitted. The output stays silent to avoid feedback.
  for(const output of outputs)for(const channel of output)channel.fill(0);
  const input=inputs[0]?.[0];
  if(input)for(const sample of input){
   const value=Math.max(-1,Math.min(1,sample));
   this.frame[this.offset++]=Math.round(value*(value<0?32768:32767));
   if(this.offset===this.frame.length){this.port.postMessage(this.frame.buffer,[this.frame.buffer]);this.frame=new Int16Array(1600);this.offset=0;}
  }
  return true;
 }
}
registerProcessor("microphone-capture",MicrophoneCapture);
