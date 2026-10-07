const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',hidden:false,disabled:false,checked:false,replaceChildren(...children){this.children=children;}});return elements.get(id);}
const requests=[];
const en={email:'test@example.com',phone:'+12025550123',language:'en',state:'complete',accepted:5,total:5,phrase:null,voices:[{language:'en',state:'complete'}],available_languages:['es','hi','ru']};
const es={...en,language:'es',state:'pending',accepted:0,phrase:'Read a Spanish sentence',voices:[...en.voices,{language:'es',state:'pending'}],available_languages:['hi','ru']};
const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[]},window:{addEventListener(){}},Intl,Option:function(label,value,defaultSelected,selected){Object.assign(this,{label,value,selected});},setInterval,clearInterval,setTimeout,clearTimeout,fetch:async(path,options)=>{
 requests.push({path,body:options?.body?JSON.parse(options.body):null});
 return {ok:path!=='/api/me',json:async()=>path==='/api/setup'?{ready:true}:path==='/api/calling-codes'?[]:path==='/api/voices'?es:en};
}});
vm.runInContext(fs.readFileSync('verivoice/web/app.js','utf8'),context);
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 await settle();
 element('show-create').onclick({preventDefault(){}});
 assert.equal(element('signin').hidden,true);assert.equal(element('account').hidden,false);
 element('show-signin').onclick({preventDefault(){}});
 assert.equal(element('signin').hidden,false);assert.equal(element('account').hidden,true);
 element('signin-identifier').value='+12025550123';element('signin-password').value='password';
 element('signin-form').onsubmit({preventDefault(){}});await settle();
 assert.deepEqual(requests.find(r=>r.path==='/api/login').body,{identifier:'+12025550123',password:'password'});
 assert.equal(element('call-link').hidden,false);
 assert.deepEqual(element('new-voice-language').children.map(c=>c.value),['es','hi','ru']);
 assert.equal(elements.has('reference'),false);
 element('show-new-voice').onclick({preventDefault(){}});assert.equal(element('new-voice-form').hidden,false);
 element('new-voice-language').value='es';element('new-voice-form').onsubmit({preventDefault(){}});await settle();
 assert.equal(element('call-link').hidden,true);assert.equal(element('record-panel').hidden,false);
 assert.deepEqual(element('new-voice-language').children.map(c=>c.value),['hi','ru']);
 assert.equal(element('phrase').lang,'es');
 console.log('Sign-in, language enrollment choices, and provider-detail hiding checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
