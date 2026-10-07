class MicrophoneCapture extends AudioWorkletProcessor {
 constructor(){super();this.frame=new Int16Array(1600);this.offset=0;}
 process(inputs,outputs){
  // Only microphone PCM is transmitted. The output stays silent to avoid feedback.
  for(const output of outputs)for(const channel of output)channel.fill(0);
  // Some devices expose a silent first channel despite a mono constraint.
  // Capture the strongest channel instead of silently losing the microphone.
  const channels=inputs[0] || [];
  let input,energy=-1;
  for(const channel of channels){let sum=0;for(const sample of channel)sum+=sample*sample;if(sum>energy){energy=sum;input=channel;}}
  if(input)for(const sample of input){
   const value=Math.max(-1,Math.min(1,sample));
   this.frame[this.offset++]=Math.round(value*(value<0?32768:32767));
   if(this.offset===this.frame.length){
    let sum=0;for(const sample of this.frame)sum+=(sample/32768)**2;
    this.port.postMessage({pcm:this.frame.buffer,rms:Math.sqrt(sum/this.frame.length)},[this.frame.buffer]);
    this.frame=new Int16Array(1600);this.offset=0;
   }
  }
  return true;
 }
}
registerProcessor("microphone-capture",MicrophoneCapture);
