const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
function element(s){if(!elements.has(s))elements.set(s,{innerHTML:'',value:s==='#filter'?'all':'',dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},addEventListener(){},querySelectorAll(){return []},querySelector(s){return element(s)},insertAdjacentHTML(where,html){this.adjacent=html}});return elements.get(s)}
const context=vm.createContext({console,Set,Map,Blob,document:{querySelector:element,addEventListener(){},body:element('body')},window:{addEventListener(){},scrollTo(){}},location:{hash:'#all'},fetch:async path=>({json:async()=>JSON.parse(fs.readFileSync('docs/'+path))})});
vm.runInContext(fs.readFileSync('docs/evaluation.js','utf8'),context);
vm.runInContext(fs.readFileSync('docs/passk-explorer.js','utf8'),context);
vm.runInContext(fs.readFileSync('docs/app.js','utf8').replace(/load\(\);\s*$/,''),context);
(async()=>{
 await vm.runInContext('load()',context);
 assert(element('#view').innerHTML.includes('Resource calibration'));
 assert(!element('#view').innerHTML.includes('NaN'));
 vm.runInContext("location.hash='#all-passk';route()",context);
 assert(element('#pk-matrix').innerHTML.includes('Exact success counts'));
 assert(element('#table').innerHTML.includes('Selected tasks'));
 vm.runInContext("location.hash='#all-calibration';route()",context);
 assert(element('#table').innerHTML.includes('64.7%'));
 assert(element('#table').innerHTML.includes('calibration-batches'));
 console.log('Overview, pass@4 and calibration routes render; real-data totals verified.');
})().catch(e=>{console.error(e);process.exit(1)});
