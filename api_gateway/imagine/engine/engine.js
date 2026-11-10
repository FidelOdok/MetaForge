(function(){
'use strict';
try{const th=new URLSearchParams(location.search).get('theme');if(th==='light'||th==='dark')document.documentElement.setAttribute('data-theme',th);}catch(e){}
const $=s=>document.querySelector(s);
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const DEG=Math.PI/180;
const ENGINE='formgraph@2.0.0';
const CFG=(()=>{try{const el=document.getElementById('fg-config');const c=el?JSON.parse(el.textContent||'{}'):{};return c&&typeof c==='object'?c:{};}catch(e){return{};}})();
const CP=CFG.product||{},SKEY='formgraph.v1.'+(CP.id||'default');
/* Hosted in the MetaForge dashboard (a sandboxed iframe) the page fits its
   frame: one compact bar, the 3D view at a bounded size, and no chrome that
   Create already gives (the product name, the stage tabs, the theme). */
const HOSTED=(()=>{try{return window.parent!==window;}catch(e){return true;}})();
if(HOSTED)document.documentElement.classList.add('fg-hosted');
/* The shell's name comes from the config, never from the template's own
   words: an airframe calls it the fuselage, anything else the shell. */
const SHELL_NAME=(()=>{const f=CFG.form||{},n=typeof f.label==='string'?f.label.trim():'';
  if(n)return n.slice(0,60);return CFG.airframe&&CFG.airframe.type==='multirotor'?'Fuselage shell':'Shell';})();
/* A config that sets no form parameters has nothing of its own to draw yet.
   Hosted, the 3D view then shows an empty state, never the template's
   default part, until the person shapes it here. */
const FORM_SET=!!(CFG.form&&CFG.form.params&&typeof CFG.form.params==='object'&&Object.keys(CFG.form.params).length);
let SHOW_PART=!HOSTED||FORM_SET;
const SHELL_MESH=SHELL_NAME.toLowerCase();
const FEATS=CFG.form&&Array.isArray(CFG.form.features)?CFG.form.features:['slots','bosses','ribs'];
let PENDING_WIRE=false;
const fmt=(v,d=1)=>(v==null||!isFinite(v))?'n/a':Number(v).toFixed(d);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const decOf=step=>step>=1?0:step>=0.1?1:step>=0.01?2:3;
const gbp=v=>!isFinite(v)?'n/a':v<100?'£'+v.toFixed(2):'£'+Math.round(v).toLocaleString('en-GB');
const store={get(k){try{return JSON.parse(localStorage.getItem(k))}catch(e){return null}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};

/* ---------------- Processes: the DFM rule book ---------------- */
const PROC={
  im:{id:'im',name:'Injection moulding',short:'Moulding',mat:'ABS',density:1.05,mech:{E:2300,str:40,k:0.17,flow:180},
    wall:[1.2,3.5],draft:1.0,innerR:t=>0.5*t,topInnerR:t=>0.5*t,slotMin:1.0,gapMin:t=>Math.max(1.0,t),
    rib:t=>[0.4*t,0.6*t],ribHMax:(t)=>3*t,holeMin:1.6,holeMax:t=>1.2*t,env:[350,350,200],
    finishes:[{id:'gloss',name:'Gloss, SPI A-2',draft:0,rough:.16,cc:.7},{id:'matte',name:'Matte, SPI C-1',draft:.5,rough:.55,cc:0},{id:'tex',name:'Texture, MT-11020',draft:1.5,rough:.85,cc:0}]},
  cnc:{id:'cnc',name:'CNC machining',short:'CNC',mat:'Al 6061-T6',density:2.70,metal:true,mech:{E:69000,str:276,k:167,metal:true},
    wall:[0.8,40],draft:0,innerR:()=>1.5,topInnerR:()=>0,slotMin:1.5,gapMin:()=>1.0,
    rib:()=>[1.0,20],ribHMax:(t,rt)=>8*rt,holeMin:1.6,holeMax:()=>10,env:[500,400,150],
    finishes:[{id:'asm',name:'As machined',draft:0,rough:.32},{id:'anod',name:'Bead blast and anodise',draft:0,rough:.5}]},
  sls:{id:'sls',name:'SLS printing',short:'SLS',mat:'PA12',density:1.01,mech:{E:1700,str:48,k:0.25},
    wall:[0.8,20],draft:0,innerR:()=>0,topInnerR:()=>0,slotMin:0.6,gapMin:()=>0.8,
    rib:()=>[0.8,20],ribHMax:(t,rt)=>15*rt,holeMin:2.0,holeMax:()=>10,env:[330,330,550],
    finishes:[{id:'dyed',name:'Dyed',draft:0,rough:.92},{id:'vapour',name:'Vapour smoothed',draft:0,rough:.42}]},
  /* Desktop FDM (FORGE-827): a Bambu Lab P1S-class machine, 0.4 mm nozzle, 0.2 mm layers,
     256 x 256 x 256 mm, enclosed chamber. Walls are whole perimeters of a 0.42 mm line;
     no draft; a closed top bridges about 20 mm before it sags; holes print undersize. */
  fdm:{id:'fdm',name:'FDM printing',short:'FDM',mat:'PETG',density:1.27,mech:{E:1700,str:38,k:0.2},
    wall:[1.2,10],draft:0,innerR:()=>0,topInnerR:()=>0,slotMin:1.0,gapMin:()=>1.2,
    rib:()=>[0.8,20],ribHMax:(t,rt)=>10*rt,holeMin:2.0,holeMax:()=>10,env:[256,256,256],
    line:0.42,layer:0.2,bridge:20,
    finishes:[{id:'asp',name:'As printed, 0.2 mm layers',draft:0,rough:.9},{id:'fine',name:'Fine, 0.12 mm layers',draft:0,rough:.6},{id:'sanded',name:'Sanded and primed',draft:0,rough:.35}]}
};
const CMF=[{id:'graphite',name:'Graphite',hex:'#30343B'},{id:'arctic',name:'Arctic white',hex:'#E4E6E8'},{id:'signal',name:'Signal orange',hex:'#DD5F2A'},{id:'cobalt',name:'Cobalt',hex:'#33529F'}];

/* ---------------- Node types ---------------- */
const NUM='num',SHAPE='shape';
const TYPES={
  slider:{cat:'param',title:'Slider',inputs:[],outputs:[{k:'v',label:'value',t:NUM}],compute:n=>({v:+n.props.value})},
  math:{cat:'math',title:'Math',inputs:[{k:'a',label:'a',t:NUM,def:1},{k:'b',label:'b',t:NUM,def:1}],outputs:[{k:'r',label:'result',t:NUM}],
    compute:(n,i)=>{const a=i.a,b=i.b;let r;switch(n.props.op){case'+':r=a+b;break;case'-':r=a-b;break;case'/':r=b?a/b:0;break;case'min':r=Math.min(a,b);break;case'max':r=Math.max(a,b);break;case'pow':r=Math.pow(a,b);break;default:r=a*b;}return{r:isFinite(r)?r:0}}},
  softbox:{cat:'form',title:'Soft box',inputs:[
      {k:'W',label:'Width',t:NUM,def:100,role:'W'},{k:'D',label:'Depth',t:NUM,def:60,role:'D'},{k:'H',label:'Height',t:NUM,def:30,role:'H'},
      {k:'R',label:'Corner radius',t:NUM,def:12,role:'R'},{k:'F',label:'Top fillet',t:NUM,def:6,role:'F'},{k:'A',label:'Draft °',t:NUM,def:1.5,role:'draft'}],
    outputs:[{k:'s',label:'form',t:SHAPE}],
    compute:(n,i)=>{const W=Math.max(10,i.W),D=Math.max(10,i.D),H=Math.max(4,i.H);const R=clamp(i.R,0.5,Math.min(W,D)/2-0.01);const F=clamp(i.F,0,Math.min(H-0.5,R));const A=clamp(i.A,0,15);
      return{s:{W,D,H,R,F,A,t:null,slots:null,bosses:null,ribs:null}}}},
  shell:{cat:'form',title:'Shell',inputs:[{k:'s',label:'form',t:SHAPE},{k:'t',label:'Wall',t:NUM,def:2,role:'wall'}],outputs:[{k:'s',label:'part',t:SHAPE}],
    compute:(n,i)=>i.s?{s:Object.assign({},i.s,{t:clamp(i.t,0.3,Math.min(i.s.W,i.s.D,2*i.s.H)/2-1)})}:{s:null}},
  slots:{cat:'form',title:'Vent slots',inputs:[{k:'s',label:'part',t:SHAPE},{k:'n',label:'Count',t:NUM,def:7,role:'slotN'},{k:'w',label:'Width',t:NUM,def:1.6,role:'slotW'}],outputs:[{k:'s',label:'part',t:SHAPE}],
    compute:(n,i)=>i.s?{s:Object.assign({},i.s,{slots:{n:Math.max(0,Math.round(i.n)),w:Math.max(0.2,i.w)}})}:{s:null}},
  bosses:{cat:'form',title:'Screw bosses',inputs:[{k:'s',label:'part',t:SHAPE},{k:'n',label:'Count',t:NUM,def:4,role:'bossN'},{k:'d',label:'Hole Ø',t:NUM,def:2.2,role:'hole'}],outputs:[{k:'s',label:'part',t:SHAPE}],
    compute:(n,i)=>{if(!i.s)return{s:null};const opts=[0,2,4,6];const c=opts.reduce((a,b)=>Math.abs(b-i.n)<Math.abs(a-i.n)?b:a,0);return{s:Object.assign({},i.s,{bosses:{n:c,d:Math.max(0.5,i.d)}})}}},
  ribs:{cat:'form',title:'Ribs',inputs:[{k:'s',label:'part',t:SHAPE},{k:'t',label:'Thickness',t:NUM,def:1.2,role:'ribT'},{k:'h',label:'Height',t:NUM,def:5,role:'ribH'}],outputs:[{k:'s',label:'part',t:SHAPE}],
    compute:(n,i)=>i.s?{s:Object.assign({},i.s,{ribs:{t:Math.max(0.2,i.t),h:Math.max(0.5,i.h)}})}:{s:null}},
  output:{cat:'out',title:'Manufacture',inputs:[{k:'s',label:'part',t:SHAPE}],outputs:[],compute:()=>({})}
};
/* ---------------- Electronic blocks, registered with the node editor ---------------- */
// Typical catalogue values. Each is confirmed against the nominated part's datasheet in the BOM.
const EB={
  e_board:{name:'Main board',cat:'compute',ins:[['vin','pwr'],['usb','usb']],outs:[['i2c','i2c'],['uart','uart'],['spi','spi'],['gpio','gpio']],fixed:true},
  e_batt:{name:'Battery',cat:'power',ins:[],outs:[['bat','pwr']],fixed:true},
  e_usbc:{name:'USB-C receptacle',cat:'conn',ins:[],outs:[['vbus','pwr'],['usb','usb']],out:{vbus:{v:[4.75,5.25],imax:1500}},mass:0.8,cost:0.35,func:'Charges over USB-C'},
  e_chg:{name:'Charger and 3.3 V regulator',cat:'power',ins:[['vbus','pwr'],['bat','pwr']],outs:[['v33','pwr'],['vsys','pwr']],inV:{vbus:[4.5,5.5],bat:[3.0,4.25]},out:{v33:{v:[3.3,3.3],imax:800},vsys:{v:'bat',imax:3000}},conv:'chg',iq:0.03,eff:0.9,chg:500,dims:[22,3,18],mass:2,cost:1.8,place:'floor'},
  e_boost:{name:'5 V boost converter',cat:'power',ins:[['vin','pwr']],outs:[['v5','pwr']],inV:{vin:[2.8,4.3]},out:{v5:{v:[5,5],imax:2000}},conv:'boost',iq:0.1,eff:0.88,dims:[20,4,12],mass:2,cost:1.5,place:'floor'},
  e_th:{name:'Temperature and humidity sensor',cat:'sense',ins:[['vcc','pwr'],['i2c','i2c']],inV:{vcc:[1.7,3.6]},addr:'0x44',cur:{sleep:0.001,measure:0.6},acc:0.2,dims:[12,2,10],mass:0.5,cost:2.2,place:'ceil',pref:'center',func:'Measures temperature and humidity'},
  e_imu:{name:'Motion sensor',cat:'sense',ins:[['vcc','pwr'],['i2c','i2c']],inV:{vcc:[1.7,3.6]},addr:'0x6A',cur:{sleep:0.003,measure:0.65},dims:[10,2,10],mass:0.3,cost:1.5,place:'ceil',pref:'plusx',func:'Senses motion'},
  e_gnss:{name:'GNSS receiver',cat:'sense',ins:[['vcc','pwr'],['uart','uart']],inV:{vcc:[3.0,3.6]},cur:{sleep:0.015,locate:25},fix:30,dims:[18,6,18],mass:3,cost:9,place:'ceil',only:'ceil',pref:'minusx',func:'Knows its location'},
  e_lte:{name:'LTE-M / NB-IoT module',cat:'comm',ins:[['vbat','pwr'],['uart','uart']],outs:[['rf','rf']],inV:{vbat:[3.3,4.3]},cur:{sleep:0.005,transmit:220},peak:2000,tx:20,margin:15,mhz:1900,dims:[25,3,22],mass:3,cost:9.5,place:'floor',radio:true,func:'Reports over LTE'},
  e_lora:{name:'LoRa module',cat:'comm',ins:[['vcc','pwr'],['spi','spi']],outs:[['rf','rf']],inV:{vcc:[1.8,3.7]},cur:{sleep:0.002,transmit:120},peak:130,tx:1.5,margin:25,mhz:868,dims:[16,3,16],mass:1.5,cost:6,place:'floor',radio:true,func:'Reports over LoRa'},
  e_ant:{name:'Flexible PCB antenna',cat:'comm',ins:[['rf','rf']],keep:10,dims:[8,0.6,40],mass:0.3,cost:1.2,place:'ceil',only:'ceil',pref:'minusx'},
  e_btn:{name:'Push button',cat:'ui',ins:[['io','gpio']],dims:[6,4,6],mass:0.4,cost:0.15,open:'button',place:'ceil',only:'ceil',pref:'plusx',func:'Has a button'},
  e_led:{name:'Status LED and light pipe',cat:'ui',ins:[['io','gpio']],cur:{transmit:5},dims:[4,5,4],mass:0.1,cost:0.12,open:'led',place:'ceil',only:'ceil',pref:'plusx',func:'Shows a status light'},
  e_buzz:{name:'Buzzer',cat:'ui',ins:[['io','gpio']],cur:{alert:20},dims:[9,4,9],mass:1,cost:0.6,open:'sound',place:'ceil',only:'ceil',pref:'plusx',func:'Makes a sound'}
};
/* FORGE-827: a constant-current buck LED driver for a high-power LED (a lamp's light
   engine), dimmed by PWM from the board. Typical catalogue values; the nominated part's
   datasheet sets the real ones in the BOM. */
EB.e_ldrv={name:'Constant-current LED driver (buck)',cat:'power',ins:[['vin','pwr'],['io','gpio']],inV:{vin:[4.5,36]},cur:{sleep:0.05,transmit:300},dims:[12,3,10],mass:0.6,cost:0.9,place:'floor',func:'Drives a high-power LED'};
EB.e_ext={name:'External power input',cat:'power',ins:[],outs:[['vext','pwr']],out:{vext:{v:[6.0,8.4],imax:3000}},dims:[8,4,6],mass:1,cost:0.5,place:'floor',func:'Powered from outside'};
EB.e_ldo={name:'3.3 V regulator',cat:'power',ins:[['vin','pwr']],outs:[['v33','pwr']],inV:{vin:[3.6,16]},out:{v33:{v:[3.3,3.3],imax:500}},conv:'ldo',iq:0.05,dims:[5,2,5],mass:0.2,cost:0.35,place:'floor'};
const PL={vext:'Power in',vbus:'USB 5 V',usb:'USB data',bat:'Battery',v33:'3.3 V out',vsys:'Battery out',vin:'Power in',v5:'5 V out',vcc:'Power',vbat:'Battery power',i2c:'I2C',uart:'UART',spi:'SPI',gpio:'GPIO',io:'GPIO',rf:'RF'};
const EB_PRE=JSON.parse(JSON.stringify(EB));
if(CFG.catalogue&&CFG.catalogue.blocks)Object.entries(CFG.catalogue.blocks).forEach(([k,b])=>{if(/^e_/.test(k))EB[k]=Object.assign({},EB[k]||{},b);});
Object.entries(EB).forEach(([k,b])=>{TYPES[k]={cat:'elec',dom:'e',title:b.name,
  inputs:b.ins.map(([kk,t])=>({k:kk,label:PL[kk]||kk,t})),outputs:(b.outs||[]).map(([kk,t])=>({k:kk,label:PL[kk]||kk,t})),compute:()=>({})};});
const domOf=n=>(TYPES[n.type]&&TYPES[n.type].dom)||'f';
const inTab=n=>((S.gtab||'form')==='elec')===(domOf(n)==='e');
const nodeHidden=id=>{const n=G.nodes.find(m=>m.id===id);return !n||!inTab(n);};
const hasUSB=()=>G.nodes.some(n=>n.type==='e_usbc');
function starterElec(nodes,links){
  if(Array.isArray(CFG.functions)){
    const N=(id,type,x,y)=>nodes.push({id,type,x,y,props:{},inVals:{}}),want=CFG.functions.filter(t=>EB[t]&&!EB[t].fixed);
    const hasBat=((CFG.power&&CFG.power.battery)||'c18650')!=='none';
    if(want.some(t=>EB[t].radio)&&!want.includes('e_ant'))want.push('e_ant');
    if(((want.includes('e_usbc')&&!want.includes('e_ext'))||hasBat)&&!want.includes('e_chg'))want.unshift('e_chg');
    if(want.includes('e_ext')&&!hasBat&&!want.includes('e_ldo'))want.push('e_ldo');
    N('eMCU','e_board',500,40);N('eBAT','e_batt',20,210);
    const col={conn:20,power:260,compute:500,sense:760,comm:1000,ui:760},cnt={};
    want.forEach(t=>{const x=col[EB[t].cat]||760;cnt[x]=cnt[x]||0;N('e'+t.slice(2).toUpperCase(),t,x,(x===260?60:20)+cnt[x]*130);cnt[x]++;});
    PENDING_WIRE=true;return;
  }
  const N=(id,type,x,y)=>nodes.push({id,type,x,y,props:{},inVals:{}}),Lk=(a,ak,b,bk)=>links.push({from:[a,ak],to:[b,bk]});
  N('eUSB','e_usbc',20,20);N('eBAT','e_batt',20,210);N('eCHG','e_chg',260,60);N('eMCU','e_board',500,40);
  N('eTH','e_th',760,20);N('eLTE','e_lte',760,170);N('eANT','e_ant',1000,190);N('eBTN','e_btn',760,330);N('eLED','e_led',760,440);
  Lk('eUSB','vbus','eCHG','vbus');Lk('eBAT','bat','eCHG','bat');Lk('eCHG','v33','eMCU','vin');Lk('eCHG','v33','eTH','vcc');Lk('eCHG','vsys','eLTE','vbat');
  Lk('eUSB','usb','eMCU','usb');Lk('eMCU','i2c','eTH','i2c');Lk('eMCU','uart','eLTE','uart');Lk('eLTE','rf','eANT','rf');Lk('eMCU','gpio','eBTN','io');Lk('eMCU','gpio','eLED','io');
}


/* ---------------- Default graph ---------------- */
function applyFormConfig(nodes,links){
  const f=CFG.form||{},P=f.params||{},Rg=f.ranges||{};
  nodes.forEach(n=>{if(n.type!=='slider')return;const k=n.props.key;
    if(Array.isArray(Rg[k])){n.props.min=+Rg[k][0];n.props.max=+Rg[k][1];}
    if(P[k]!=null){const v=+P[k];if(v<n.props.min)n.props.min=v;if(v>n.props.max)n.props.max=v;n.props.value=v;}});
  const prune=(op,sl)=>{const up=links.find(l=>l.to[0]===op&&l.to[1]==='s');
    links.filter(l=>l.from[0]===op&&l.from[1]==='s').forEach(l=>{if(up)l.from=up.from.slice();});
    for(let i=links.length-1;i>=0;i--){const l=links[i];if(l.to[0]===op||l.from[0]===op||sl.includes(l.from[0]))links.splice(i,1);}
    for(let i=nodes.length-1;i>=0;i--)if(nodes[i].id===op||sl.includes(nodes[i].id))nodes.splice(i,1);};
  if(!FEATS.includes('slots'))prune('slots',['sSN','sSW']);
  if(!FEATS.includes('bosses'))prune('boss',['sBN','sBD']);
  if(!FEATS.includes('ribs'))prune('ribs',['sRT','sRH']);
}
function defaultGraph(){
  const nodes=[],links=[];
  const sl=(id,key,name,value,min,max,step,unit,x,y)=>nodes.push({id,type:'slider',x,y,props:{key,name,value,min,max,step,unit},inVals:{}});
  const op=(id,type,x,y,props)=>nodes.push({id,type,x,y,props:props||{},inVals:{}});
  const ln=(a,ak,b,bk)=>links.push({from:[a,ak],to:[b,bk]});
  const X1=20,X2=262,X3=470,h=58;let y=20;
  sl('sW','W','Width',110,40,300,1,'mm',X1,y);
  sl('sP','P','Proportion',0.618,0.2,1.2,0.001,'',X1,y+=h);
  sl('sH','H','Height',32,8,120,0.5,'mm',X1,y+=h);
  sl('sR','R','Corner radius',18,1,100,0.5,'mm',X1,y+=h);
  sl('sF','F','Top fillet',8,0,40,0.5,'mm',X1,y+=h);
  sl('sA','A','Draft',1.5,0,8,0.1,'°',X1,y+=h);
  op('mD','math',X2,64,{op:'*',name:'Depth = W × P'});
  op('box','softbox',X3,40);
  sl('sT','t','Wall',2.2,0.4,6,0.05,'mm',X1,y=400);
  op('shell','shell',X3,290);
  sl('sSN','sn','Slot count',9,0,30,1,'',X1,y=500);
  sl('sSW','sw','Slot width',1.8,0.3,6,0.1,'mm',X1,y+=h);
  op('slots','slots',X3,430);
  sl('sBN','bn','Bosses',4,0,6,2,'',X1,y=660);
  sl('sBD','bd','Screw hole',2.2,0.6,8,0.1,'mm',X1,y+=h);
  op('boss','bosses',X3,600);
  sl('sRT','rt','Rib thickness',1.2,0.3,6,0.05,'mm',X1,y=820);
  sl('sRH','rh','Rib height',5,1,30,0.5,'mm',X1,y+=h);
  op('ribs','ribs',X3,770);
  op('out','output',X3,940);
  ln('sW','v','box','W');ln('sW','v','mD','a');ln('sP','v','mD','b');ln('mD','r','box','D');
  ln('sH','v','box','H');ln('sR','v','box','R');ln('sF','v','box','F');ln('sA','v','box','A');
  ln('box','s','shell','s');ln('sT','v','shell','t');
  ln('shell','s','slots','s');ln('sSN','v','slots','n');ln('sSW','v','slots','w');
  ln('slots','s','boss','s');ln('sBN','v','boss','n');ln('sBD','v','boss','d');
  ln('boss','s','ribs','s');ln('sRT','v','ribs','t');ln('sRH','v','ribs','h');
  ln('ribs','s','out','s');
  applyFormConfig(nodes,links);
  starterElec(nodes,links);
  return{nodes,links};
}
/* ---------------- State ---------------- */
let G=defaultGraph();
let S={proc:'im',finish:'matte',qty:5000,constrain:true,view:'studio',color:'graphite',flip:false,sim:'drop',simIn:{dropH:1.2,payload:120,heatW:3,ambient:25,force:50},mat:'absfr',board:'mcu',batt:'c18650',fast:'pt25',port:true,bomDrive:true,nom:{},revs:[],custom:{L:70,W:45,H:8,P:1.5,M:25},level:'simple',gtab:'form',req:{interval:30,life:14,resp:0.5,cov:'fair',acc:0.5,drop:1.2,touch:48,chgh:10,ip:'none'},reqPri:{},decisions:{}};
(function(){const c=CFG,pr=c.process||{},pw=c.power||{};
  if(pr.id)S.proc=pr.id;if(pr.material)S.mat=pr.material;if(pr.finish)S.finish=pr.finish;if(pr.qty)S.qty=+pr.qty;if(pr.fixings)S.fast=pr.fixings;if(pr.machine)S.machine=pr.machine;
  if(pw.board)S.board=pw.board;if(pw.battery)S.batt=pw.battery;if(pw.custom)S.custom=Object.assign({},S.custom,pw.custom);
  if(c.level)S.level=c.level;if(c.color)S.color=c.color;if(c.ambient!=null)S.simIn.ambient=+c.ambient;
  if(Array.isArray(c.requirements)){S.reqPri={};const ids=new Set();
    c.requirements.forEach(r=>{ids.add(r.id);if(r.pri)S.reqPri[r.id]=r.pri;Object.assign(S.req,r.values||{});});
    ['interval','life','resp','cov','acc','drop','touch','chgt','chgh','ip'].forEach(id=>{if(!ids.has(id))S.reqPri[id]='Off';});}
  if(c.decisions)S.decisions=Object.assign({},c.decisions);if(c.nominations)S.nom=Object.assign({},c.nominations);})();
const CFGH=fnv(JSON.stringify(Object.assign({},CFG,{_registry:undefined})));let RESETTING=false;
const saved0=store.get(SKEY),saved=saved0&&saved0.cfg===CFGH?saved0:null;
if(saved&&saved.G&&Array.isArray(saved.G.nodes)&&saved.G.nodes.every(n=>TYPES[n.type])){G=saved.G;Object.assign(S,saved.S||{});}
if(saved&&G===saved.G)PENDING_WIRE=false;
if(!G.nodes.some(n=>EB[n.type]))starterElec(G.nodes,G.links);
S.req=Object.assign({interval:30,life:14,resp:0.5,cov:'fair',acc:0.5,drop:1.2,touch:48,chgh:10,ip:'none'},S.req||{});S.decisions=S.decisions||{};S.reqPri=S.reqPri||{};
let selId=null;
let last=null;               // last solve result
let held=new Set();          // slider ids moved by the solver this pass
let simAnim=null,simRes=null,simT=1,EL=null,REQ=[],dev=null;

const byId=id=>G.nodes.find(n=>n.id===id);
const finishOf=()=>{const P=PROC[S.proc];return P.finishes.find(f=>f.id===S.finish)||P.finishes[0];};

/* ---------------- Evaluation ---------------- */
function topo(){
  const indeg={},adj={};G.nodes.forEach(n=>{indeg[n.id]=0;adj[n.id]=[];});
  G.links.forEach(l=>{if(adj[l.from[0]]&&indeg[l.to[0]]!==undefined){adj[l.from[0]].push(l.to[0]);indeg[l.to[0]]++;}});
  const q=G.nodes.filter(n=>!indeg[n.id]).map(n=>n.id),out=[];
  while(q.length){const id=q.shift();out.push(id);adj[id].forEach(m=>{if(--indeg[m]===0)q.push(m);});}
  return out;
}
function ancestors(id){const set=new Set([id]),st=[id];while(st.length){const c=st.pop();G.links.forEach(l=>{if(l.to[0]===c&&!set.has(l.from[0])){set.add(l.from[0]);st.push(l.from[0]);}});}return set;}
function reaches(a,b){const seen=new Set([a]),st=[a];while(st.length){const c=st.pop();if(c===b)return true;G.links.forEach(l=>{if(l.from[0]===c&&!seen.has(l.to[0])){seen.add(l.to[0]);st.push(l.to[0]);}});}return false;}
function evaluate(){
  const vals={},roles={},inputs={};let shape=null;
  const out=G.nodes.find(n=>n.type==='output');const up=out?ancestors(out.id):new Set();
  for(const id of topo()){
    const n=byId(id),T=TYPES[n.type],inp={};
    for(const d of T.inputs){
      const l=G.links.find(l=>l.to[0]===id&&l.to[1]===d.k);
      let v=l?(vals[l.from[0]]||{})[l.from[1]]:undefined;
      if(v===undefined)v=d.t===NUM?((n.inVals&&n.inVals[d.k]!=null)?n.inVals[d.k]:d.def):null;
      if(d.t===NUM)v=+v||0;
      inp[d.k]=v;
      if(d.role&&up.has(id)){const src=l?byId(l.from[0]):null;roles[d.role]={node:id,k:d.k,linked:!!l,slider:src&&src.type==='slider'?src.id:null,value:v};}
    }
    inputs[id]=inp;vals[id]=T.compute(n,inp)||{};
    if(n.type==='output')shape=inp.s;
  }
  return{vals,roles,inputs,shape};
}

/* ---------------- Geometry facts used by rules ---------------- */
function filletZ(y,H,F){if(F<=0||y<=H-F)return 0;const u=y-(H-F);return F-Math.sqrt(Math.max(0,F*F-u*u));}
function derive(s){
  const tan=Math.tan(s.A*DEG);const topInset=s.H*tan+s.F;
  const aTop=s.W/2-topInset,bTop=s.D/2-topInset;
  const span=Math.max(0,2*aTop*0.62);const slotL=Math.max(0,Math.min(2*bTop*0.6,s.D*0.5));
  let gap=null;if(s.slots&&s.slots.n>1)gap=(span-s.slots.n*s.slots.w)/(s.slots.n-1);
  const ri=s.t?s.R-s.t:null,fi=s.t?s.F-s.t:null;
  return{tan,topInset,aTop,bTop,span,slotL,gap,ri,fi};
}
const maxSlots=(span,w,g)=>w>span?0:Math.floor((span+g)/(w+g));
function areaRR(a,b,r){if(a<=0||b<=0)return 0;r=clamp(r,0,Math.min(a,b));return 4*a*b-(4-Math.PI)*r*r;}
function solidVol(W,D,H,R,F,tan,base){let v=0;const n=80;for(let i=0;i<n;i++){const y=(i+.5)*H/n;const d=base+y*tan+filletZ(y,H,F);v+=areaRR(W/2-d,D/2-d,Math.max(R-d,0))*H/n;}return v;}
function partVolume(s){
  const d=derive(s);let V=solidVol(s.W,s.D,s.H,s.R,s.F,d.tan,0);
  if(s.t){const t=s.t;V-=solidVol(s.W,s.D,s.H-t,s.R,Math.max(s.F-t,0),d.tan,t);
    if(s.bosses&&s.bosses.n){const h=s.bosses.d,od=2*h,len=Math.max(1,s.H-t-1.5);V+=s.bosses.n*Math.PI/4*(od*od-h*h)*len;}
    if(s.ribs){V+=(s.W+s.D-4*t)*s.ribs.t*s.ribs.h*0.92;}
    if(s.slots&&s.slots.n){const w=s.slots.w,L=d.slotL;V-=s.slots.n*(w*Math.max(L-w,0)+Math.PI*w*w/4)*t;}
  }
  return Math.max(V,0);
}

/* ---------------- Cost model ---------------- */
function costs(s){const c=costs0(s);Object.keys(PROC).forEach(k=>{const P=PROC[k],b=P.base&&c[P.base];if(!c[k]&&b)c[k]={tool:b.tool,at:q=>b.at(q)*(P.costFactor||1)};});
  const m=machineOf();if(m&&m.costFactor&&c[S.proc]&&!PROC[S.proc].base){const b=c[S.proc];c[S.proc]={tool:b.tool,at:q=>b.at(q)*m.costFactor};}return c;}
function costs0(s){
  const Vc=partVolume(s)/1000,A=areaRR(s.W/2,s.D/2,s.R)/100,t=s.t||Math.min(s.W,s.D)/2;
  const bossN=s.bosses?s.bosses.n:0,slotN=s.slots?s.slots.n:0;
  const tool=6000+28*A+bossN*120+(slotN?400:0)+(s.ribs?300:0);
  const cycle=12+2.2*t*t;const unitIM=Vc*matFor('im').density/1000*matFor('im').costKg*1.08+cycle*(42/3600)+0.15;
  const stock=(s.W+6)*(s.D+6)*(s.H+3)/1000,removed=Math.max(0,stock-Vc);
  const minutes=12+removed/7+slotN*1.0+bossN*0.8+(s.ribs?4:0);const unitCNC=stock*matFor('cnc').density/1000*matFor('cnc').costKg+minutes*1.25;
  const bbox=s.W*s.D*s.H/1000;const unitSLS=Vc*0.62*(matFor('sls').costKg||1)+bbox*0.035+3.5;
  const mf=matFor('fdm'),gFDM=Vc*(mf.density||1.25)*1.1,hFDM=Vc/(8*3600/1000)+0.25;const unitFDM=gFDM/1000*(mf.costKg||22)+hFDM*1.5+2;
  return{
    im:{tool,at:q=>unitIM+tool/q},
    cnc:{tool:180,at:q=>unitCNC*(q>=500?0.82:q>=50?0.92:1)+180/q},
    sls:{tool:0,at:q=>unitSLS*(q>=1000?0.8:q>=100?0.9:1)},
    fdm:{tool:0,at:q=>unitFDM*(q>=50?0.92:1)}
  };
}

/* ---------------- DFM limits used for clamping ---------------- */
function roleRange0(role,s,P,fin){
  const d=derive(s),t=s.t;
  switch(role){
    case'wall':return[P.wall[0],Math.min(P.wall[1],Math.min(s.W,s.D)/2-2)];
    case'draft':return[P.draft+fin.draft,10];
    case'R':return[t?t+P.innerR(t):0.5,Math.min(s.W,s.D)/2];
    case'F':{const lo=t&&P.topInnerR(t)>0?t+P.topInnerR(t):0;return[lo,Math.max(lo,Math.min(s.R,s.H-(t||0)-2))];}
    case'H':return[s.F+(t||0)+2,P.env[2]];
    case'W':return[2*s.R,P.env[0]];
    case'slotW':return s.slots&&s.slots.n?[P.slotMin,Math.max(P.slotMin,Math.min(8,d.span))]:null;
    case'slotN':return s.slots?[0,maxSlots(d.span,s.slots.w,P.gapMin(t||2))]:null;
    case'hole':return s.bosses&&s.bosses.n&&t?[P.holeMin,Math.max(P.holeMin,P.holeMax(t))]:null;
    case'ribT':return s.ribs&&t?P.rib(t):null;
    case'ribH':return s.ribs&&t?[1,Math.max(1,Math.min(P.ribHMax(t,s.ribs.t),s.H-t-1))]:null;
  }
  return null;
}
const fixable=src=>src&&(src.slider||!src.linked);
function setRole(src,v){
  if(!src)return false;
  if(src.slider){const n=byId(src.slider);const p=n.props;if(v<p.min)p.min=+fmt(v,3);if(v>p.max)p.max=+fmt(v,3);p.value=v;return true;}
  if(!src.linked){const n=byId(src.node);n.inVals=n.inVals||{};n.inVals[src.k]=v;return true;}
  return false;
}

/* ---------------- Solve: evaluate, then pull parameters inside limits ---------------- */
function solve(){
  const P=PROC[S.proc],fin=finishOf();held=new Set();
  let ev=evaluate();curRoles=ev.roles;ev=enforceBOM(ev);curRoles=ev.roles;
  if(S.constrain){
    for(let pass=0;pass<5&&ev.shape;pass++){
      let changed=false;
      for(const role in ev.roles){
        const src=ev.roles[role];const rg=roleRange(role,ev.shape,P,fin);if(!rg||!fixable(src))continue;
        const lo=rg[0],hi=Math.max(rg[0],rg[1]);let nv=clamp(src.value,lo,hi);
        if(role==='slotN')nv=Math.floor(nv+1e-9);
        if(Math.abs(nv-src.value)>1e-6){setRole(src,nv);changed=true;if(src.slider)held.add(src.slider);}
      }
      if(!changed)break;ev=evaluate();curRoles=ev.roles;
    }
  }
  // ranges per slider for the red bands
  const bands={};
  if(ev.shape)for(const role in ev.roles){const src=ev.roles[role];if(!src.slider)continue;const rg=roleRange(role,ev.shape,P,fin);if(!rg)continue;
    const b=bands[src.slider]||[-Infinity,Infinity];bands[src.slider]=[Math.max(b[0],rg[0]),Math.min(b[1],Math.max(rg[0],rg[1]))];}
  ev.bands=bands;
  return ev;
}

/* ---------------- DFM report ---------------- */
const isFDM=P=>P.id==='fdm'||P.base==='fdm';
/* FDM screening rules (FORGE-827): perimeters, the closed top as a bridge, warping, and
   screws in printed plastic. Preliminary guidance for a P1S-class printer, V2 at most. */
function fdmRules(s,t,d,P,add){
  const line=P.line||0.42,lines=t/line,frac=lines-Math.floor(lines);
  add({label:'Wall in whole perimeters',val:`${fmt(lines,1)} lines of ${fmt(line,2)} mm`,lim:'a whole number of lines',st:frac<0.25||frac>0.75?'pass':'warn',role:'wall',target:Math.max(3,Math.round(lines))*line,
    why:'A wall between whole perimeters is filled with thin gap-fill lines, which are weak and slow.'});
  const span=Math.max(0,Math.min(s.W,s.D)-2*t),br=P.bridge||20;
  add({label:'Print orientation',val:`closed top spans ${fmt(span,0)} mm`,lim:`top face on the bed; bridges about ${br} mm`,st:span<=br?'pass':'warn',
    why:'Printed opening up, the closed top would need support inside the shell. Print it top face down, opening up, so every wall rises from the bed.'});
  const M=matOf(),big=Math.max(s.W,s.D),warp=/asa|abs|cf/i.test(M.id)?150:220;
  add({label:'Warp risk',val:`${fmt(big,0)} mm footprint in ${M.name}`,lim:`up to ${warp} mm without a brim`,st:big<=warp?'pass':'warn',
    why:'Long flat bases lift at the corners as they cool. Use a brim and the closed chamber, and round the base corners.'});
  const f=fastOf();
  if(s.bosses&&s.bosses.n)add({label:'Screws in printed plastic',val:f.name.split(',')[0],lim:'heat-set inserts for repeat assembly',st:f.insert?'pass':'warn',
    why:'A thread-forming screw cuts printed layers; after a few assemblies it strips. Heat-set inserts hold.'});
  if(s.slots&&s.slots.n)add({label:'Slot width in lines',val:fmt(s.slots.w/line,1)+' lines',lim:'at least 2 lines',st:s.slots.w>=2*line?'pass':'fail',role:'slotW',target:2*line});
  add({label:'Hole size as printed',val:'holes print about 0.1 to 0.2 mm small',lim:'size holes up or ream them',st:'pass'});
}
function report(s,roles){
  const P=PROC[S.proc],fin=finishOf(),R=[],d=derive(s),t=s.t;
  const add=o=>R.push(o);
  const fitEnv=s.W<=P.env[0]&&s.D<=P.env[1]&&s.H<=P.env[2];
  add({label:'Fits the '+P.short+' envelope',val:`${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm`,lim:`max ${P.env.join(' × ')}`,st:fitEnv?'pass':'fail',role:'W',target:Math.min(s.W,P.env[0])});
  if(!t){
    add({label:'Hollow part',val:'solid block',lim:'add a Shell node',st:P.id==='cnc'?'warn':'fail',why:'Solid sections sink, void and take minutes to cool.'});
  }else{
    add({label:'Nominal wall',val:fmt(t,2)+' mm',lim:`${P.wall[0]} to ${P.wall[1]} mm`,st:t>=P.wall[0]-1e-6&&t<=P.wall[1]+1e-6?'pass':'fail',role:'wall',target:clamp(t,P.wall[0],P.wall[1])});
    const rmin=P.innerR(t);
    if(rmin>0)add({label:'Inside corner radius',val:fmt(Math.max(d.ri,0),2)+' mm',lim:`min ${fmt(rmin,2)} mm`,st:d.ri>=rmin-1e-6?'pass':(d.ri>0?'warn':'fail'),role:'R',target:t+rmin});
    else add({label:'Inside corner radius',val:fmt(Math.max(d.ri,0),2)+' mm',lim:'any',st:'pass'});
    const fmin=P.topInnerR(t);
    if(fmin>0)add({label:'Inside top fillet',val:fmt(Math.max(d.fi,0),2)+' mm',lim:`min ${fmt(fmin,2)} mm`,st:d.fi>=fmin-1e-6?'pass':'warn',role:'F',target:Math.min(t+fmin,s.R)});
    if(P.id==='cnc'&&d.ri>0){const tool=2*Math.min(d.ri,6),depth=s.H-t,r=depth/tool;
      add({label:'Pocket depth to tool Ø',val:fmt(r,1)+' : 1',lim:'max 4 : 1',st:r<=4?'pass':r<=6?'warn':'fail',role:'R',target:t+Math.min(6,depth/8)});}
    if(P.id==='sls'&&Math.max(s.W,s.D)>200&&t<1.5)add({label:'Warp risk on large panels',val:fmt(t,2)+' mm wall',lim:'min 1.5 mm over 200 mm',st:'warn',role:'wall',target:1.5});
    if(isFDM(P))fdmRules(s,t,d,P,add);
  }
  const dmin=P.draft+fin.draft;
  if(dmin>0)add({label:'Draft angle'+(fin.draft?' for '+fin.name:''),val:fmt(s.A,1)+'°',lim:`min ${fmt(dmin,1)}°`,st:s.A>=dmin-1e-6?'pass':(s.A>=dmin*0.5?'warn':'fail'),role:'draft',target:dmin});
  else add({label:'Draft angle',val:fmt(s.A,1)+'°',lim:'not needed',st:'pass'});
  if(s.slots&&s.slots.n){
    const w=s.slots.w;add({label:'Slot width',val:fmt(w,2)+' mm',lim:`min ${P.slotMin} mm`,st:w>=P.slotMin-1e-6?'pass':'fail',role:'slotW',target:P.slotMin});
    const g=P.gapMin(t||2),mx=maxSlots(d.span,w,g);
    add({label:'Slot spacing',val:s.slots.n>1?fmt(d.gap,2)+' mm':'single slot',lim:`min ${fmt(g,2)} mm, fits ${mx}`,st:s.slots.n<=mx?'pass':'fail',role:'slotN',target:mx});
    if(P.id==='cnc'&&t)add({label:'Slot depth to width',val:fmt(t/w,1)+' : 1',lim:'max 3 : 1',st:t/w<=3?'pass':'warn',role:'slotW',target:t/3});
  }
  if(s.bosses&&s.bosses.n&&t){
    const h=s.bosses.d;add({label:'Screw hole Ø',val:fmt(h,2)+' mm',lim:`min ${P.holeMin} mm`,st:h>=P.holeMin-1e-6?'pass':'fail',role:'hole',target:P.holeMin});
    if(P.id==='im')add({label:'Boss wall vs nominal wall',val:fmt(h/2,2)+' mm',lim:`max ${fmt(0.6*t,2)} mm (60%)`,st:h/2<=0.6*t+1e-6?'pass':'warn',role:'wall',target:Math.min(P.wall[1],h/1.2),why:'Thicker bosses leave sink marks on the show face.'});
  }
  if(s.ribs&&t){
    const [a,b]=P.rib(t),rt=s.ribs.t,rh=s.ribs.h,hm=P.ribHMax(t,rt);
    add({label:'Rib thickness',val:fmt(rt,2)+' mm',lim:P.id==='im'?`${fmt(a,2)} to ${fmt(b,2)} mm`:`min ${a} mm`,st:rt>=a-1e-6&&rt<=b+1e-6?'pass':(rt>b?'warn':'fail'),role:'ribT',target:clamp(rt,a,b)});
    add({label:'Rib height',val:fmt(rh,1)+' mm',lim:`max ${fmt(hm,1)} mm`,st:rh<=hm+1e-6?'pass':'warn',role:'ribH',target:hm});
  }
  const C=costs(s);const q=S.qty;let best='im';for(const k in C)if(C[k].at(q)<C[best].at(q))best=k;
  const cur=C[P.id].at(q),bv=C[best].at(q);
  if(best!==P.id&&cur>1.25*bv)add({label:`Process suits ${q.toLocaleString('en-GB')} units`,val:gbp(cur)+' per unit',lim:`${PROC[best].short} ${gbp(bv)}`,st:'warn',action:{proc:best}});
  else add({label:`Process suits ${q.toLocaleString('en-GB')} units`,val:gbp(cur)+' per unit',lim:'lowest cost route',st:'pass'});
  R.forEach(r=>{r.canFix=r.st!=='pass'&&(r.action||(r.role&&fixable(roles[r.role])));});
  return{rules:R,costs:C,best};
}

/* ================= Node editor ================= */
const canvas=$('#canvas'),world=$('#world'),nodesEl=$('#nodes'),wires=$('#wires'),tempWire=$('#tempWire');
let view={x:20,y:20,k:0.8};const nodeEls={};
function applyView(){world.style.transform=`translate(${view.x}px,${view.y}px) scale(${view.k})`;canvas.style.backgroundPosition=`${view.x}px ${view.y}px`;canvas.style.backgroundSize=`${20*view.k}px ${20*view.k}px`;}

function nodeHTML(n){
  const T=TYPES[n.type];
  if(n.type==='slider'){const p=n.props,dc=decOf(p.step);
    return `<div class="nh drag"><span class="nname"><input class="name" value="${esc(p.name)}" aria-label="Slider name"></span>
      <span class="nval"><input class="num" type="number" step="${p.step}" value="${fmt(p.value,dc)}" aria-label="${esc(p.name)} value"><span class="unit">${esc(p.unit||'')}</span></span>
      <span class="held" hidden title="Held at a DFM limit">DFM</span><span class="port out" data-dir="out" data-k="v" data-t="num"></span></div>
      <div class="srow"><input class="rng" type="range" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.value}" aria-label="${esc(p.name)}"></div>
      <div class="mm"><span>Range</span><input class="mn" type="number" value="${p.min}" aria-label="Minimum"><span>to</span><input class="mx" type="number" value="${p.max}" aria-label="Maximum"></div>`;}
  let h=`<div class="nh drag"><span class="dot"></span><span class="title">${esc(n.type==='math'&&n.props.name?n.props.name:T.title)}</span>${(n.type==='output'||(EB[n.type]&&EB[n.type].fixed))?'':'<button class="x" type="button" aria-label="Delete node">×</button>'}</div><div class="body">`;
  if(n.type==='math')h+=`<div class="row"><select class="op" aria-label="Operation">${['+','-','*','/','min','max','pow'].map(o=>`<option value="${o}"${n.props.op===o?' selected':''}>${{'+':'a + b','-':'a − b','*':'a × b','/':'a ÷ b',min:'min(a, b)',max:'max(a, b)',pow:'a ^ b'}[o]}</option>`).join('')}</select></div>`;
  for(const d of T.inputs){const iv=(n.inVals&&n.inVals[d.k]!=null)?n.inVals[d.k]:d.def;
    h+=`<div class="row in" data-k="${d.k}"><span class="port in" data-dir="in" data-k="${d.k}" data-t="${d.t}"></span><span class="lbl">${esc(d.label)}</span>${d.t===NUM?`<input class="iv" type="number" step="any" value="${iv}" aria-label="${esc(d.label)}"><span class="rv"></span>`:''}</div>`;}
  for(const d of T.outputs)h+=`<div class="row out"><span class="lbl">${esc(d.label)}</span><span class="rv o" data-o="${d.k}"></span><span class="port out" data-dir="out" data-k="${d.k}" data-t="${d.t}"></span></div>`;
  h+='</div>';if(n.type==='output')h+='<div class="osum" id="osum"></div>';if(T.dom==='e')h+='<div class="osum esum"></div>';
  return h;
}
function renderNodes(){
  nodesEl.innerHTML='';for(const k in nodeEls)delete nodeEls[k];
  for(const n of G.nodes){const el=document.createElement('div');const T=TYPES[n.type];
    el.className='node t-'+T.cat+(n.type==='slider'?'':' op')+(n.id===selId?' sel':'');el.dataset.id=n.id;el.style.left=n.x+'px';el.style.top=n.y+'px';
    el.innerHTML=nodeHTML(n);el.hidden=!inTab(n);nodesEl.appendChild(el);nodeEls[n.id]=el;}
  refreshPorts();
}
function portXY(id,dir,k){const el=nodeEls[id]&&nodeEls[id].querySelector(`.port.${dir}[data-k="${k}"]`);if(!el)return null;const r=el.getBoundingClientRect(),c=canvas.getBoundingClientRect();
  return[(r.left+r.width/2-c.left-view.x)/view.k,(r.top+r.height/2-c.top-view.y)/view.k];}
const curve=(a,b)=>{const dx=Math.max(40,Math.abs(b[0]-a[0])*0.5);return`M${a[0]},${a[1]} C${a[0]+dx},${a[1]} ${b[0]-dx},${b[1]} ${b[0]},${b[1]}`;};
function drawWires(){
  wires.querySelectorAll('path.link').forEach(p=>p.remove());
  for(const l of G.links){if(nodeHidden(l.from[0])||nodeHidden(l.to[0]))continue;const a=portXY(l.from[0],'out',l.from[1]),b=portXY(l.to[0],'in',l.to[1]);if(!a||!b)continue;
    const T=TYPES[byId(l.from[0]).type].outputs.find(o=>o.k===l.from[1]);
    const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('class','wire link'+(T&&T.t===SHAPE?' shape':'')+(T?' t-'+T.t:''));p.setAttribute('d',curve(a,b));wires.insertBefore(p,tempWire);}
}
function refreshPorts(){
  nodesEl.querySelectorAll('.port').forEach(p=>p.classList.remove('on'));nodesEl.querySelectorAll('.row.in').forEach(r=>r.classList.remove('linked'));
  for(const l of G.links){const a=nodeEls[l.from[0]],b=nodeEls[l.to[0]];if(!a||!b)continue;
    const pa=a.querySelector(`.port.out[data-k="${l.from[1]}"]`);pa&&pa.classList.add('on');
    const row=b.querySelector(`.row.in[data-k="${l.to[1]}"]`);if(row){row.classList.add('linked');row.querySelector('.port').classList.add('on');}}
  drawWires();
}
function select(id){selId=id;for(const k in nodeEls)nodeEls[k].classList.toggle('sel',k===id);requestAnimationFrame(drawWires);}
function fitGraph(){
  if(!G.nodes.length)return;let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const n of G.nodes.filter(inTab)){const el=nodeEls[n.id];const w=el?el.offsetWidth:200,h=el?el.offsetHeight:80;x0=Math.min(x0,n.x);y0=Math.min(y0,n.y);x1=Math.max(x1,n.x+w);y1=Math.max(y1,n.y+h);}
  const cw=canvas.clientWidth,ch=canvas.clientHeight-30;const k=clamp(Math.min((cw-32)/Math.max(1,x1-x0),(ch-24)/Math.max(1,y1-y0)),0.3,1.1);
  view={k,x:(cw-(x1-x0)*k)/2-x0*k,y:Math.max(12,(ch-(y1-y0)*k)/2)-y0*k};applyView();drawWires();
}
let nid=0;const newId=t=>t[0]+Date.now().toString(36)+(nid++);
function addNode(type){
  const c=canvas.getBoundingClientRect();const x=(c.width/2-view.x)/view.k-95,y=(c.height/2-view.y)/view.k-40;
  const n={id:newId(type),type,x:Math.round(x),y:Math.round(y),props:{},inVals:{}};
  if(type==='slider')n.props={key:'',name:'Slider '+(G.nodes.filter(m=>m.type==='slider').length+1),value:10,min:0,max:100,step:0.1,unit:''};
  if(type==='math')n.props={op:'*',name:''};
  G.nodes.push(n);if(EB[type])autoWire(n);selId=n.id;renderNodes();schedule();
}
function deleteNode(id){const n=byId(id);if(!n||n.type==='output'||(EB[n.type]&&EB[n.type].fixed))return;G.nodes=G.nodes.filter(m=>m.id!==id);G.links=G.links.filter(l=>l.from[0]!==id&&l.to[0]!==id);if(selId===id)selId=null;renderNodes();schedule();}

let drag=null;
const toWorld=e=>{const c=canvas.getBoundingClientRect();return[(e.clientX-c.left-view.x)/view.k,(e.clientY-c.top-view.y)/view.k];};
const pointers=new Map();
canvas.addEventListener('pointerdown',e=>{
  pointers.set(e.pointerId,[e.clientX,e.clientY]);
  if(pointers.size===2){const [a,b]=[...pointers.values()];drag={mode:'pinch',d0:Math.hypot(a[0]-b[0],a[1]-b[1]),k0:view.k,x0:view.x,y0:view.y,cx:(a[0]+b[0])/2,cy:(a[1]+b[1])/2};return;}
  const port=e.target.closest('.port');const nodeEl=e.target.closest('.node');
  if(port){e.preventDefault();const id=port.closest('.node').dataset.id,k=port.dataset.k,t=port.dataset.t;
    if(port.dataset.dir==='out')drag={mode:'wire',from:[id,k],t};
    else{const l=G.links.find(l=>l.to[0]===id&&l.to[1]===k);if(!l)return;G.links=G.links.filter(x=>x!==l);drag={mode:'wire',from:l.from,t};refreshPorts();schedule();}
    tempWire.setAttribute('class','wire temp'+(t===SHAPE?' shape':'')+' t-'+t);tempWire.style.display='';canvas.setPointerCapture(e.pointerId);moveTemp(e);return;}
  if(nodeEl){if(selId!==nodeEl.dataset.id)select(nodeEl.dataset.id);
    if(e.target.closest('.drag')&&!e.target.closest('input,select,button,textarea')){const n=byId(nodeEl.dataset.id);drag={mode:'node',n,el:nodeEl,sx:e.clientX,sy:e.clientY,ox:n.x,oy:n.y};canvas.setPointerCapture(e.pointerId);e.preventDefault();}
    return;}
  select(null);drag={mode:'pan',sx:e.clientX,sy:e.clientY,ox:view.x,oy:view.y};canvas.classList.add('panning');canvas.setPointerCapture(e.pointerId);
});
function moveTemp(e){const a=portXY(drag.from[0],'out',drag.from[1]);if(a)tempWire.setAttribute('d',curve(a,toWorld(e)));}
canvas.addEventListener('pointermove',e=>{
  if(pointers.has(e.pointerId))pointers.set(e.pointerId,[e.clientX,e.clientY]);
  if(!drag)return;
  if(drag.mode==='pinch'&&pointers.size===2){const [a,b]=[...pointers.values()];const k=clamp(drag.k0*Math.hypot(a[0]-b[0],a[1]-b[1])/drag.d0,0.3,2);const c=canvas.getBoundingClientRect();const px=drag.cx-c.left,py=drag.cy-c.top;
    view.x=px-(px-drag.x0)*k/drag.k0;view.y=py-(py-drag.y0)*k/drag.k0;view.k=k;applyView();drawWires();}
  else if(drag.mode==='pan'){view.x=drag.ox+e.clientX-drag.sx;view.y=drag.oy+e.clientY-drag.sy;applyView();}
  else if(drag.mode==='node'){drag.n.x=Math.round(drag.ox+(e.clientX-drag.sx)/view.k);drag.n.y=Math.round(drag.oy+(e.clientY-drag.sy)/view.k);drag.el.style.left=drag.n.x+'px';drag.el.style.top=drag.n.y+'px';drawWires();}
  else if(drag.mode==='wire')moveTemp(e);
});
function endPointer(e){
  pointers.delete(e.pointerId);if(!drag)return;
  if(drag.mode==='wire'){const el=document.elementFromPoint(e.clientX,e.clientY);const p=el&&el.closest('.port.in');
    if(p&&p.dataset.t===drag.t){const to=p.closest('.node').dataset.id,k=p.dataset.k;
      if(to!==drag.from[0]&&!reaches(to,drag.from[0])){G.links=G.links.filter(l=>!(l.to[0]===to&&l.to[1]===k));G.links.push({from:drag.from,to:[to,k]});}
      else toast('That wire would make a loop.');}
    tempWire.style.display='none';refreshPorts();schedule();}
  if(drag.mode==='node')save();
  canvas.classList.remove('panning');drag=null;
}
canvas.addEventListener('pointerup',endPointer);canvas.addEventListener('pointercancel',endPointer);
canvas.addEventListener('wheel',e=>{e.preventDefault();const c=canvas.getBoundingClientRect();const px=e.clientX-c.left,py=e.clientY-c.top;const k=clamp(view.k*Math.exp(-e.deltaY*0.0015),0.3,2);
  view.x=px-(px-view.x)*k/view.k;view.y=py-(py-view.y)*k/view.k;view.k=k;applyView();drawWires();},{passive:false});

nodesEl.addEventListener('input',e=>{
  const el=e.target,nodeEl=el.closest('.node');if(!nodeEl)return;const n=byId(nodeEl.dataset.id);
  if(el.classList.contains('rng')){n.props.value=+el.value;const num=nodeEl.querySelector('.num');num.value=fmt(n.props.value,decOf(n.props.step));schedule();}
  else if(el.classList.contains('num')){const v=parseFloat(el.value);if(isFinite(v)){n.props.value=v;if(v<n.props.min)n.props.min=v;if(v>n.props.max)n.props.max=v;nodeEl.querySelector('.rng').value=v;schedule();}}
  else if(el.classList.contains('iv')){const v=parseFloat(el.value);if(isFinite(v)){n.inVals=n.inVals||{};n.inVals[el.closest('.row').dataset.k]=v;schedule();}}
});
nodesEl.addEventListener('change',e=>{
  const el=e.target,nodeEl=el.closest('.node');if(!nodeEl)return;const n=byId(nodeEl.dataset.id);
  if(el.classList.contains('name')){n.props.name=el.value.slice(0,40);save();}
  else if(el.classList.contains('mn')||el.classList.contains('mx')){const v=parseFloat(el.value);if(!isFinite(v))return;if(el.classList.contains('mn'))n.props.min=Math.min(v,n.props.max-n.props.step);else n.props.max=Math.max(v,n.props.min+n.props.step);
    const r=nodeEl.querySelector('.rng');r.min=n.props.min;r.max=n.props.max;schedule();}
  else if(el.classList.contains('op')){n.props.op=el.value;schedule();}
  else if(el.classList.contains('num')){el.value=fmt(n.props.value,decOf(n.props.step));}
});
nodesEl.addEventListener('click',e=>{const x=e.target.closest('.x');if(x)deleteNode(x.closest('.node').dataset.id);});
document.addEventListener('keydown',e=>{if((e.key==='Delete'||e.key==='Backspace')&&selId&&!e.target.closest('input,textarea,select')){e.preventDefault();deleteNode(selId);}});
document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>addNode(b.dataset.add)));
$('#fit').addEventListener('click',fitGraph);

/* ================= 3D studio ================= */
const host=$('#view3d');
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));renderer.setClearColor(0x000000,0);
renderer.outputEncoding=THREE.sRGBEncoding;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.localClippingEnabled=true;
host.appendChild(renderer.domElement);
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(32,1,1,6000);camera.position.set(170,140,230);
const controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=0.09;controls.maxPolarAngle=Math.PI*0.495;controls.target.set(0,16,0);

(function env(){const pm=new THREE.PMREMGenerator(renderer);const es=new THREE.Scene();
  const g=new THREE.SphereGeometry(100,32,16);const p=g.attributes.position,c=[];for(let i=0;i<p.count;i++){const y=p.getY(i)/100;const v=y>0?0.32+0.5*y:0.32-0.22*(-y);c.push(v,v,v*1.03);}
  g.setAttribute('color',new THREE.Float32BufferAttribute(c,3));es.add(new THREE.Mesh(g,new THREE.MeshBasicMaterial({side:THREE.BackSide,vertexColors:true})));
  const lm=new THREE.MeshBasicMaterial({color:new THREE.Color(5,5,5),side:THREE.DoubleSide});
  const top=new THREE.Mesh(new THREE.PlaneGeometry(90,40),lm);top.position.set(0,90,0);top.rotation.x=Math.PI/2;es.add(top);
  const s1=new THREE.Mesh(new THREE.PlaneGeometry(30,80),lm);s1.position.set(-90,20,30);s1.rotation.y=Math.PI/2;es.add(s1);
  const s2=new THREE.Mesh(new THREE.PlaneGeometry(20,60),new THREE.MeshBasicMaterial({color:new THREE.Color(2,2.1,2.3),side:THREE.DoubleSide}));s2.position.set(80,30,-60);s2.rotation.y=-Math.PI/3;es.add(s2);
  scene.environment=pm.fromScene(es,0.04).texture;})();

scene.add(new THREE.HemisphereLight(0xffffff,0x3a4250,0.35));
const key=new THREE.DirectionalLight(0xffffff,1.25);key.position.set(140,260,160);key.castShadow=true;key.shadow.mapSize.set(2048,2048);
Object.assign(key.shadow.camera,{left:-260,right:260,top:260,bottom:-260,near:20,far:900});key.shadow.bias=-0.0004;key.shadow.radius=4;scene.add(key);
const rimL=new THREE.DirectionalLight(0xdfe6ff,0.45);rimL.position.set(-180,90,-220);scene.add(rimL);
const ground=new THREE.Mesh(new THREE.PlaneGeometry(3000,3000),new THREE.ShadowMaterial({opacity:0.22}));ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);
let grid=null;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function themeScene(){if(grid){scene.remove(grid);grid.geometry.dispose();}
  grid=new THREE.GridHelper(600,60,new THREE.Color(css('--grid-a')),new THREE.Color(css('--grid-b')));grid.material.transparent=true;grid.material.opacity=0.55;grid.position.y=0.01;grid.visible=SHOW_PART;scene.add(grid);
  matCut.color.set(css('--cut')).convertSRGBToLinear();}

const plane=new THREE.Plane(new THREE.Vector3(0,0,-1),0);
const matBody=new THREE.MeshPhysicalMaterial({color:0x30343b,roughness:.55,metalness:0,clearcoat:0,clearcoatRoughness:.2,envMapIntensity:0.9});
const matDraft=new THREE.MeshStandardMaterial({vertexColors:true,roughness:.6,metalness:0,envMapIntensity:0.6});
const matSlot=new THREE.MeshStandardMaterial({color:0x0b0c0f,roughness:.95,metalness:0});
const matCut=new THREE.MeshBasicMaterial({color:0xe5584f,side:THREE.BackSide});
const part=new THREE.Group();scene.add(part);
/* MetaForge: an agent-authored form (spec 23.6) is drawn by authored.js, in a sandbox, instead of the enclosure. */
const AUTHORED=!!(CFG.form&&CFG.form.template==='authored'&&CFG.form.authored&&window.FGAuthored);let AUTHORED_MOUNT=null;
if(AUTHORED)document.documentElement.setAttribute('data-authored','1');

const C_GREEN=new THREE.Color('#3FAE76').convertSRGBToLinear(),C_AMBER=new THREE.Color('#E3A43A').convertSRGBToLinear(),C_RED=new THREE.Color('#E04A42').convertSRGBToLinear(),C_NEU=new THREE.Color('#9AA3B2').convertSRGBToLinear();
function ringPts(a,b,r,seg){a=Math.max(a,0.05);b=Math.max(b,0.05);r=clamp(r,0.001,Math.min(a,b));const cx=a-r,cz=b-r,P=[];
  [[cx,cz],[-cx,cz],[-cx,-cz],[cx,-cz]].forEach(([x0,z0],q)=>{for(let i=0;i<=seg;i++){const th=(q+i/seg)*Math.PI/2;P.push([x0+r*Math.cos(th),z0+r*Math.sin(th)]);}});return P;}
function levels(H,F,tan,base){const L=[],hs=H-F,nS=4;for(let i=0;i<=nS;i++){const y=hs*i/nS;L.push({y,d:base+y*tan});}
  if(F>0.05){const nF=12;for(let i=1;i<=nF;i++){const ph=i/nF*Math.PI/2;const y=hs+F*Math.sin(ph);L.push({y,d:base+y*tan+F*(1-Math.cos(ph))});}}return L;}
function geom(pos,idx){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));g.setIndex(idx);g.computeVertexNormals();return g;}
function surface(W,D,R,L,seg,inward){const pos=[],idx=[],n=4*(seg+1);
  L.forEach(l=>ringPts(W/2-l.d,D/2-l.d,R-l.d,seg).forEach(q=>pos.push(q[0],l.y,q[1])));
  for(let k=0;k<L.length-1;k++)for(let j=0;j<n;j++){const a=k*n+j,b=k*n+(j+1)%n,c=(k+1)*n+j,d=(k+1)*n+(j+1)%n;if(!inward)idx.push(a,c,b,b,c,d);else idx.push(a,b,c,b,d,c);}
  return geom(pos,idx);}
function cap(W,D,R,l,seg,up){const P=ringPts(W/2-l.d,D/2-l.d,R-l.d,seg),pos=[0,l.y,0],idx=[];P.forEach(q=>pos.push(q[0],l.y,q[1]));const n=P.length;
  for(let j=0;j<n;j++){const a=1+j,b=1+(j+1)%n;if(up)idx.push(0,b,a);else idx.push(0,a,b);}return geom(pos,idx);}
function rim(W,D,R,lo,li,seg){const O=ringPts(W/2-lo.d,D/2-lo.d,R-lo.d,seg),I=ringPts(W/2-li.d,D/2-li.d,R-li.d,seg),pos=[],idx=[],n=O.length;
  O.forEach(q=>pos.push(q[0],0,q[1]));I.forEach(q=>pos.push(q[0],0,q[1]));
  for(let j=0;j<n;j++){const o=j,o1=(j+1)%n,i=n+j,i1=n+(j+1)%n;idx.push(o,o1,i,o1,i1,i);}return geom(pos,idx);}
function lathe(prof,seg){const pts=[];prof.forEach(p=>{pts.push(new THREE.Vector2(p[0],p[1]));pts.push(new THREE.Vector2(p[0],p[1]));});
  const g=new THREE.LatheGeometry(pts.slice(1,-1).concat([pts[0]]),seg);return g;}
function prism(shapePts,depth){const sh=new THREE.Shape(shapePts.map(p=>new THREE.Vector2(p[0],p[1])));return new THREE.ExtrudeGeometry(sh,{depth,bevelEnabled:false,curveSegments:10});}
function colorize(g,pull,minD){const n=g.attributes.normal,col=new Float32Array(n.count*3);
  for(let i=0;i<n.count;i++){const deg=Math.asin(clamp(n.getY(i)*pull,-1,1))/DEG;const c=deg<-60?C_NEU:deg<-0.05?C_RED:deg<minD-0.05?C_AMBER:C_GREEN;col[i*3]=c.r;col[i*3+1]=c.g;col[i*3+2]=c.b;}
  g.setAttribute('color',new THREE.BufferAttribute(col,3));}

function clearPart(){while(part.children.length){const m=part.children.pop();if(m.geometry)m.geometry.dispose();}}
function buildPart(s){
  clearPart();
  if(AUTHORED){if(!AUTHORED_MOUNT)AUTHORED_MOUNT=FGAuthored.mount({THREE,scene,cfg:CFG,quick:document.getElementById('quick'),host:document.getElementById('graphPane')||document.body,summaryHost:document.getElementById('dfm'),material:()=>{const m=matOf();return{name:m.name,density:m.density};},onSummary:d=>{$('#dims').textContent=d.dims||d.headline;},onDraw:()=>{if(!camMoved)frame({W:100,D:100,H:100});}});return;}
  if(!s)return;
  const P=PROC[S.proc],fin=finishOf(),minD=P.draft+fin.draft,tan=Math.tan(s.A*DEG),seg=12;const items=[];
  const add=(g,pull,kind,mat,name)=>items.push({g,pull,kind,mat,name});
  const Lo=levels(s.H,s.F,tan,0);
  add(surface(s.W,s.D,s.R,Lo,seg,false),1,'body');add(cap(s.W,s.D,s.R,Lo[Lo.length-1],seg,true),1,'body');
  const t=s.t;
  if(t){
    const Hi=s.H-t,Fi=Math.max(s.F-t,0);const Li=levels(Hi,Fi,tan,t);
    add(surface(s.W,s.D,s.R,Li,seg,true),-1,'body');add(cap(s.W,s.D,s.R,Li[Li.length-1],seg,false),-1,'body');add(rim(s.W,s.D,s.R,Lo[0],Li[0],seg),1,'body');
    const capD=Li[Li.length-1].d,aC=s.W/2-capD,bC=s.D/2-capD,rC=Math.max(s.R-capD,0);
    if(s.bosses&&s.bosses.n){const h=s.bosses.d,od=2*h,y0=1.5,y1=Hi+0.15,len=y1-y0,m=od/2+0.6;
      const cx=aC-Math.max(rC,m),cz=bC-Math.max(rC,m),k=Math.max(0,rC-m)/Math.SQRT2;
      const pos=[[cx+k,cz+k],[-cx-k,-cz-k],[-cx-k,cz+k],[cx+k,-cz-k],[0,bC-m],[0,-bC+m]];
      const use=s.bosses.n===2?pos.slice(0,2):s.bosses.n===4?pos.slice(0,4):pos;
      const ro0=od/2,ro1=od/2+len*tan,rh0=h/2,rh1=Math.max(0.3,h/2-len*tan*0.5);
      use.forEach(([x,z])=>{if(aC<m||bC<m)return;const g=lathe([[rh0,y0],[ro0,y0],[ro1,y1],[rh1,y1],[rh0,y0]],28);g.translate(x,0,z);add(g,-1,'body');});}
    if(s.ribs){const rt=s.ribs.t,rh=Math.min(s.ribs.h,Hi-1),yb=Hi-rh,tip=Math.max(rt*0.5,rt-2*rh*tan);
      const inset=t+yb*tan+filletZ(yb,Hi,Fi);const hx=s.W/2-inset-0.1,hz=s.D/2-inset-0.1;
      const sec=[[-rt/2,Hi+0.1],[-tip/2,yb],[tip/2,yb],[rt/2,Hi+0.1]];
      if(hx>1&&rh>0.5){const gx=prism(sec,2*hx);gx.rotateY(Math.PI/2);gx.translate(-hx,0,0);add(gx,-1,'body');}
      if(hz>1&&rh>0.5){const gz=prism(sec,2*hz);gz.translate(0,0,-hz);add(gz,-1,'body');}}
    addComponents(s,add);
  }else add(cap(s.W,s.D,s.R,Lo[0],seg,false),1,'body');
  if(s.slots&&s.slots.n){const d=derive(s),w=s.slots.w,L=Math.max(d.slotL,w),hl=(L-w)/2,r=w/2,n=s.slots.n,gap=n>1?d.gap:0;
    const pts=[];for(let i=0;i<=10;i++){const a=Math.PI*i/10;pts.push([r*Math.cos(a),hl+r*Math.sin(a)]);}for(let i=0;i<=10;i++){const a=Math.PI+Math.PI*i/10;pts.push([r*Math.cos(a),-hl+r*Math.sin(a)]);}
    const dep=(t||3)+0.12,x0=-(n*w+(n-1)*gap)/2+w/2;
    for(let i=0;i<n;i++){const g=prism(pts,dep);g.rotateX(-Math.PI/2);g.translate(x0+i*(w+gap),s.H-dep+0.05,0);add(g,1,'slot');}}
  if(AF)addAirframe(s,add);
  if(CTX)addOthers(s,add);
  const mode=S.view;matLed.clippingPlanes=matMod.clippingPlanes=matWireP.clippingPlanes=matWireD.clippingPlanes=matWireR.clippingPlanes=matPCB.clippingPlanes=matChip.clippingPlanes=matBatt.clippingPlanes=matBody.clippingPlanes=matDraft.clippingPlanes=matSlot.clippingPlanes=matCut.clippingPlanes=mode==='section'?[plane]:[];
  for(const it of items){
    if(mode==='draft'&&it.kind==='body')colorize(it.g,it.pull,minD);
    if(mode==='sim'&&it.kind==='body')it.g.setAttribute('color',new THREE.BufferAttribute(new Float32Array(it.g.attributes.position.count*3),3));
    const m=new THREE.Mesh(it.g,it.mat?it.mat:it.kind==='slot'?matSlot:(mode==='draft'||mode==='sim'?matDraft:matBody));m.castShadow=true;m.receiveShadow=true;m.name=it.name||(it.kind==='body'?SHELL_MESH:it.kind==='slot'?SHELL_MESH+' cut':'internal parts');m.userData.kind=it.kind;part.add(m);
    if(mode==='sim'&&it.kind!=='comp')m.userData={base:Float32Array.from(it.g.attributes.position.array),pull:it.pull,slot:it.kind==='slot'};
    if(mode==='section'&&it.kind==='body'){const c=new THREE.Mesh(it.g,matCut);part.add(c);}
  }
  part.rotation.x=S.flip?Math.PI:0;part.position.y=(S.flip?s.H:0)+(AF&&!S.flip&&S.view!=='sim'?AFL:0);
}
function styleMaterial(){
  const P=PROC[S.proc],fin=finishOf(),c=CMF.find(x=>x.id===S.color)||CMF[0];
  matBody.color.set(c.hex).convertSRGBToLinear();matBody.metalness=P.metal?0.9:0;matBody.roughness=P.metal?Math.max(.22,fin.rough):fin.rough;
  matBody.clearcoat=fin.cc||0;matBody.needsUpdate=true;
}
let framedFor=null,camMoved=false;
const FIT_DIR=new THREE.Vector3(0.55,0.5,0.75).normalize();
/* Frame the part: the camera backs off along a fixed three-quarter view
   until the part's bounding sphere fits both the height and the width of
   the view, whatever the view's aspect. */
function fitDistance(r,fovDeg,aspect,margin){const v=fovDeg*DEG/2,h=Math.atan(Math.tan(v)*Math.max(aspect,0.05));return r*(margin||1.15)/Math.sin(Math.min(v,h));}
function frame(s){if(!s)return;part.updateMatrixWorld(true);const box=new THREE.Box3().setFromObject(part);if(AUTHORED_MOUNT){AUTHORED_MOUNT.group.updateMatrixWorld(true);box.union(new THREE.Box3().setFromObject(AUTHORED_MOUNT.group));}
  let c,r;if(box.isEmpty()){c=new THREE.Vector3(0,s.H/2,0);r=Math.max(s.W,s.D,s.H,AF?AFSPAN:0)/2;}
  else{const sp=box.getBoundingSphere(new THREE.Sphere());c=sp.center;r=sp.radius;}
  r=Math.max(r,1);const d=fitDistance(r,camera.fov,camera.aspect);
  camera.position.copy(c).addScaledVector(FIT_DIR,d);camera.near=Math.max(0.1,d/100);camera.far=Math.max(6000,d*20);camera.updateProjectionMatrix();
  controls.target.copy(c);controls.update();camMoved=false;}
controls.addEventListener('start',()=>{camMoved=true;});
function resize(){const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();
  // Until the person moves the camera, a new view size re-frames the part.
  if(!camMoved&&last&&last.shape)frame(last.shape);}
new ResizeObserver(resize).observe(host);
window.__fgView=()=>({w:host.clientWidth,h:host.clientHeight,aspect:camera.aspect,shown:part.visible&&part.children.length>0,
  fits:(()=>{if(!part.children.length)return null;part.updateMatrixWorld(true);camera.updateMatrixWorld(true);const b=new THREE.Box3().setFromObject(part),P=[];
    for(let i=0;i<8;i++)P.push(new THREE.Vector3(i&1?b.max.x:b.min.x,i&2?b.max.y:b.min.y,i&4?b.max.z:b.min.z).project(camera));
    return P.every(p=>Math.abs(p.x)<=1.001&&Math.abs(p.y)<=1.001);})()});
(function loop(){requestAnimationFrame(loop);if(simAnim){const t=(performance.now()-simAnim.t0)/simAnim.dur;simT=Math.min(1,t);applySim(simT);if(t>=1)simAnim=null;}devTick();controls.update();renderer.render(scene,camera);})();

/* ================= Panels ================= */
function renderProcess(){
  $('#procSeg').innerHTML=Object.values(PROC).map(p=>`<button type="button" data-p="${p.id}" aria-pressed="${p.id===S.proc}"><b>${p.short}</b><small>${p.mat}</small></button>`).join('');
  const P=PROC[S.proc];if(!P.finishes.some(f=>f.id===S.finish))S.finish=P.finishes[0].id;
  $('#finish').innerHTML=P.finishes.map(f=>`<option value="${f.id}"${f.id===S.finish?' selected':''}>${f.name}${f.draft?` (+${f.draft}° draft)`:''}</option>`).join('');
  $('#qty').value=String(S.qty);$('#constrain').checked=S.constrain;
  $('#cmf').innerHTML='<span>Colour</span>'+CMF.map(c=>`<button type="button" class="sw" data-c="${c.id}" style="background:${c.hex}" aria-pressed="${c.id===S.color}" aria-label="${c.name}" title="${c.name}"></button>`).join('');
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===S.view)));
  $('#flip').setAttribute('aria-pressed',String(S.flip));
  renderComp();applyLevel();
}
$('#procSeg').addEventListener('click',e=>{const b=e.target.closest('[data-p]');if(!b)return;S.proc=b.dataset.p;renderProcess();schedule();});
$('#finish').addEventListener('change',e=>{S.finish=e.target.value;schedule();});
$('#qty').addEventListener('change',e=>{S.qty=+e.target.value;schedule();});
$('#constrain').addEventListener('change',e=>{S.constrain=e.target.checked;schedule();});
$('#cmf').addEventListener('click',e=>{const b=e.target.closest('[data-c]');if(!b)return;S.color=b.dataset.c;renderProcess();schedule();});
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{S.view=b.dataset.view;renderProcess();if(S.view==='sim'){renderSimInputs();startSim();}schedule();}));
$('#flip').addEventListener('click',()=>{S.flip=!S.flip;renderProcess();schedule();});
$('#frame').addEventListener('click',()=>last&&frame(last.shape));

function updateNodeReadouts(ev){
  for(const n of G.nodes){const el=nodeEls[n.id];if(!el)continue;
    if(n.type==='slider'){const p=n.props,r=el.querySelector('.rng'),num=el.querySelector('.num'),dc=decOf(p.step);
      if(+r.min!==p.min)r.min=p.min;if(+r.max!==p.max)r.max=p.max;if(Math.abs(+r.value-p.value)>p.step/2)r.value=p.value;
      if(document.activeElement!==num)num.value=fmt(p.value,dc);
      const b=ev.bands[n.id],span=p.max-p.min;
      if(b&&span>0){r.style.setProperty('--lo',clamp((b[0]-p.min)/span*100,0,100)+'%');r.style.setProperty('--hi',clamp((b[1]-p.min)/span*100,0,100)+'%');}
      else{r.style.setProperty('--lo','0%');r.style.setProperty('--hi','100%');}
      const bl=bomLocked.has(n.id),hd=el.querySelector('.held');hd.textContent=bl?'BOM':'DFM';hd.title=bl?'Set by the selected fixings':'Held at a DFM limit';r.disabled=bl;num.disabled=bl;
      hd.hidden=!(bl||S.constrain&&b&&(Math.abs(p.value-b[0])<1e-6&&b[0]>p.min+1e-9||Math.abs(p.value-b[1])<1e-6&&b[1]<p.max-1e-9));
      continue;}
    const inp=ev.inputs[n.id]||{};
    el.querySelectorAll('.row.in').forEach(row=>{const rv=row.querySelector('.rv');if(rv){const v=inp[row.dataset.k];rv.textContent=typeof v==='number'?fmt(v,Math.abs(v)<10?2:1):'';}});
    if(n.type==='math'){const o=el.querySelector('.rv.o');if(o)o.textContent=fmt(ev.vals[n.id].r,2);}
  }
}

function renderPanel(ev){
  if(AUTHORED)return null; /* the enclosure's checks say nothing about an authored form; authored.js shows what was measured */
  const s=ev.shape,P=PROC[S.proc];
  const osum=$('#osum');
  if(!s){
    $('#verdict').innerHTML='<div class="vhead"><span class="big">No part to check</span></div><div class="vsub">Wire a Soft box into the Manufacture node to see the form and its checks.</div>';
    $('#rules').innerHTML='';$('#metrics').innerHTML='';$('#costT').innerHTML='';$('#dims').textContent='No form connected';if(osum)osum.innerHTML='<span class="vd">Waiting for a form</span>';return null;}
  const rep=report(s,ev.roles);const nf=rep.rules.filter(r=>r.st==='fail').length,nw=rep.rules.filter(r=>r.st==='warn').length,np=rep.rules.length-nf-nw;
  const head=nf?`${nf} blocking issue${nf>1?'s':''}`:nw?'Makeable with notes':`Ready for ${P.short.toLowerCase()}`;
  const col=nf?'var(--fail)':nw?'var(--warn)':'var(--pass)';
  const unit=rep.costs[P.id].at(S.qty);
  $('#verdict').innerHTML=`<div class="lbl">Verdict</div><div class="vhead"><span class="big" style="color:${col}">${head}</span></div>
    <div class="vbar" aria-hidden="true"><i style="flex:${np};background:var(--pass)"></i><i style="flex:${nw};background:var(--warn)"></i><i style="flex:${nf};background:var(--fail)"></i></div>
    <div class="vsub">${np} of ${rep.rules.length} checks pass for ${P.name.toLowerCase()} in ${matOf().name}. About ${gbp(unit)} per unit at ${S.qty.toLocaleString('en-GB')} units${P.id==='im'?`, with tooling near ${gbp(rep.costs.im.tool)}`:''}.</div>`;
  const order={fail:0,warn:1,pass:2};
  $('#rules').innerHTML=rep.rules.map((r,i)=>({r,i})).sort((a,b)=>order[a.r.st]-order[b.r.st]||a.i-b.i).map(({r,i})=>{
    const fixLbl=r.action?`Use ${PROC[r.action.proc].short}`:'Fix';
    const btn=r.st==='pass'?'<span></span>':`<button class="fix" type="button" data-fix="${i}"${r.canFix?'':' disabled title="Driven by a Math node. Change its inputs instead."'}>${fixLbl}</button>`;
    return `<li class="rule st-${r.st}"><span class="chip">${r.st}</span><div class="rt"><div class="rl">${esc(r.label)}</div><div class="rv"><b>${esc(r.val)}</b> · ${esc(r.lim)}</div>${r.why&&r.st!=='pass'?`<div class="rv">${esc(r.why)}</div>`:''}</div>${btn}</li>`;}).join('');
  const V=partVolume(s),mass=V/1000*matOf().density;
  $('#metrics').innerHTML=[
    ['Envelope',`${fmt(s.W,1)} × ${fmt(s.D,1)} × ${fmt(s.H,1)} mm`],['Material volume',`${fmt(V/1000,1)} cm³`],['Mass in '+matOf().name,`${fmt(mass,0)} g`],
    ['Nominal wall',s.t?`${fmt(s.t,2)} mm`:'solid'],['Projected area',`${fmt(areaRR(s.W/2,s.D/2,s.R)/100,1)} cm²`],['Draft',`${fmt(s.A,1)}°`],
    ['Vent open area',s.slots&&s.slots.n?`${fmt(s.slots.n*(s.slots.w*Math.max(derive(s).slotL-s.slots.w,0)+Math.PI*s.slots.w**2/4),0)} mm²`:'none']
  ].map(([a,b])=>`<dt>${a}</dt><dd>${b}</dd>`).join('');
  const Q=[100,1000,10000];const best=Q.map(q=>Object.keys(rep.costs).reduce((a,b)=>rep.costs[b].at(q)<rep.costs[a].at(q)?b:a));
  $('#costT').innerHTML=`<thead><tr><th>Route</th>${Q.map(q=>`<th>${q.toLocaleString('en-GB')}</th>`).join('')}<th>Setup</th></tr></thead><tbody>${Object.values(PROC).map(p=>`<tr class="${p.id===S.proc?'cur':''}"><td>${p.short}</td>${Q.map((q,i)=>`<td class="${best[i]===p.id?'best':''}">${gbp(rep.costs[p.id].at(q))}</td>`).join('')}<td>${rep.costs[p.id].tool?gbp(rep.costs[p.id].tool):'none'}</td></tr>`).join('')}</tbody>`;
  $('#dims').textContent=`${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm · ${fmt(V/1000,1)} cm³ · ${fmt(mass,0)} g`;
  if(osum)osum.innerHTML=`<span class="vd" style="color:${col}">${head}</span><span style="color:var(--muted)">${np}/${rep.rules.length} checks · ${gbp(unit)} per unit</span>`;
  const lg=$('#legend');lg.hidden=S.view!=='draft';
  if(S.view==='draft'){const dm=P.draft+finishOf().draft;lg.innerHTML=`<b style="font-size:12px">Draft vs ${dm?fmt(dm,1)+'° minimum':'no minimum'}</b><span><i style="background:#3FAE76"></i>Releases cleanly</span><span><i style="background:#E3A43A"></i>Under minimum draft</span><span><i style="background:#E04A42"></i>Undercut, needs a side action</span><span><i style="background:#9AA3B2"></i>Parting face</span>`;}
  return rep;
}
$('#rules').addEventListener('click',e=>{const b=e.target.closest('[data-fix]');if(!b||!last||!last.rep)return;const r=last.rep.rules[+b.dataset.fix];if(!r)return;
  if(r.action){S.proc=r.action.proc;renderProcess();}
  else if(r.role){let v=r.target;if(r.role==='slotN')v=Math.floor(v);if(!setRole(last.roles[r.role],v))return;}
  schedule();});

/* ================= Product simulator ================= */
const SIMS={fill:'Mould fill',drop:'Drop test',thermal:'Thermal',press:'Press test'};
if(Array.isArray(CFG.simulations)&&CFG.simulations.length){Object.keys(SIMS).forEach(k=>{if(!CFG.simulations.includes(k))delete SIMS[k];});if(!SIMS[S.sim])S.sim=Object.keys(SIMS)[0];}
const CMAP=['#2B5BD7','#1FB5C9','#4CC36B','#F2C12E','#E8483F'].map(h=>new THREE.Color(h).convertSRGBToLinear());
const C_SHORT=new THREE.Color('#5A1F2A').convertSRGBToLinear(),C_EMPTY=new THREE.Color('#C9CED6').convertSRGBToLinear();
const _c=new THREE.Color();
function cmap(v){v=clamp(v,0,1)*(CMAP.length-1);const i=Math.min(CMAP.length-2,Math.floor(v));return _c.copy(CMAP[i]).lerp(CMAP[i+1],v-i);}
const perim=s=>2*(s.W-2*s.R)+2*(s.D-2*s.R)+2*Math.PI*s.R;
const grade=(v,ok,bad,hiBad=true)=>hiBad?(v<=ok?'pass':v<=bad?'warn':'fail'):(v>=ok?'pass':v>=bad?'warn':'fail');
function worst(rows){const o={fail:0,warn:1,pass:2};return rows.reduce((a,r)=>r[3]&&o[r[3]]<o[a]?r[3]:a,'pass');}

function simulate(s){
  const P=PROC[S.proc],M=matOf(),I=simInputs(s),d=derive(s),t=s.t||s.H,V=partVolume(s),out={};
  const slotArea=s.slots&&s.slots.n?s.slots.n*(s.slots.w*Math.max(d.slotL-s.slots.w,0)+Math.PI*s.slots.w**2/4):0;

  // Mould fill: centre gate on the top face, flow front spreads across the top then down the walls
  if(P.id!=='im'){
    out.fill={na:true,st:'na',head:'moulding only',rows:[],msg:'Mould fill applies to injection moulding. Switch the process to Moulding to run it.'};
  }else{
    const Lmax=Math.hypot(s.W/2,s.D/2)-(Math.SQRT2-1)*s.R+s.H,ratio=Lmax/t,reach=Math.min(1,M.flow*t/Lmax);
    const welds=(s.slots?s.slots.n:0)+(s.bosses?s.bosses.n:0),time=V/1000/30+0.3;
    const rows=[
      ['Flow length to wall',fmt(ratio,0)+' : 1','max '+M.flow+' : 1 for '+M.name,grade(ratio,0.85*M.flow,M.flow)],
      ['Short shot',reach<1?fmt((1-reach)*100,0)+'% of flow path unfilled':'fills fully','',reach<1?'fail':'pass'],
      ['Weld lines',welds?String(welds):'none','behind slots and boss holes',welds>14?'warn':'pass'],
      ['Fill time',fmt(time,2)+' s','at 30 cm³/s','']];
    out.fill={st:worst(rows),head:fmt(ratio,0)+' : 1 flow ratio',rows,Lmax,reach,
      field:(x,y,z)=>Math.hypot(x,z)+(s.H-y),lo:'Gate',hi:'Last to fill',
      msg:'Gate at the centre of the top face. Thin walls on long parts freeze before the far corners fill.'};
  }

  // Drop: corner impact, energy absorbed by the shell near the corner
  {
    const mass=V/1000*M.density/1000+I.payload/1000,E=mass*9.81*I.dropH*1000,v=Math.sqrt(2*9.81*I.dropH);
    const Veff=(s.t?V:V*0.3)*0.25*(s.R/(s.R+8))+t*(Math.PI/2*s.R+20)*s.H*0.6+t*s.R*s.R*0.5;
    const sig=Math.sqrt(2*E*M.E/Veff),sf=M.str/sig,c=(1-1/Math.SQRT2)*s.R,cx=s.W/2-c,cz=s.D/2-c,len=s.R+0.25*s.H+10;
    const rows=[
      ['Safety factor',fmt(sf,2),'min 1.5 against '+fmt(M.str,0)+' MPa',grade(sf,1.5,1,false)],
      ['Peak corner stress',fmt(sig,0)+' MPa','',''],
      ['Impact energy',fmt(E/1000,2)+' J',fmt(mass*1000,0)+' g with contents',''],
      ['Impact speed',fmt(v,1)+' m/s','','']];
    out.drop={st:rows[0][3],head:'SF '+fmt(sf,2)+' at '+fmt(I.dropH,1)+' m',rows,peak:sig,
      field:(x,y,z)=>Math.exp(-Math.hypot(x-cx,y,z-cz)/len),lo:'0 MPa',hi:fmt(sig,0)+' MPa',
      msg:M.metal?'Metal parts dent rather than crack below a safety factor of 1. Drop height is not drawn to scale.':'Larger corner radii and thicker walls spread the impact. Drop height is not drawn to scale.'};
  }

  // Thermal: internal air node, wall conduction, outside convection, chimney flow through the vents
  {
    const Q=I.heatW,Ta=I.ambient,As=(areaRR(s.W/2,s.D/2,s.R)+perim(s)*s.H)/1e6,tw=t/1000;
    const Rpath=1/(6*As)+tw/(M.k*As)+1/(8*As);let Ti=Ta+10;
    for(let i=0;i<80;i++){const Gv=0.00012*slotArea*Math.sqrt(Math.max(Ti-Ta,0.01));Ti=0.5*Ti+0.5*(Ta+Q/(1/Rpath+Gv));}
    const Qw=(Ti-Ta)/Rpath,Ts=Ta+Qw/(8*As),hot=Ts+(Ts-Ta)*(M.metal?0.05:0.4),touch=M.metal?43:48,ventShare=Q>0?1-Qw/Q:0;
    const rows=[
      ['Internal air',fmt(Ti,1)+' °C','max 60 °C for electronics',grade(Ti,55,65)],
      ['Hottest surface',fmt(hot,1)+' °C','touch limit '+touch+' °C',grade(hot,touch-3,touch)],
      ['Heat out through vents',fmt(100*ventShare,0)+'%',slotArea?fmt(slotArea,0)+' mm² open':'no vents',''],
      ['Wall conduction',M.metal?'spreads heat':'insulating','k = '+M.k+' W/m·K','']];
    const top=Math.max(Ti,hot);
    out.thermal={st:worst(rows),head:fmt(Ti,1)+' °C inside',rows,lo:fmt(Ta,0)+' °C',hi:fmt(top,0)+' °C',
      field:(x,y,z,pull)=>{let T;if(pull<0)T=Ti;else if(M.metal)T=Ts+(hot-Ts)*(y/s.H);
        else{const r=Math.min(1,(x/(s.W/2))**2+(z/(s.D/2))**2);T=Ta+(hot-Ta)*(0.45+0.55*(y/s.H)*(1-0.5*r));}
        return(T-Ta)/Math.max(0.1,top-Ta);},
      msg:'Steady state with the open face closed by a lid. Vents set the chimney airflow, the wall material sets how evenly the skin warms.'};
  }

  // Press: point load on the top centre, plate working in parallel with the cross ribs
  {
    const F=I.force,E=M.E,ha=Math.max(3,s.W/2-t),hb=Math.max(3,s.D/2-t),a=2*ha,b=2*hb,sh=Math.min(a,b),ratio=Math.max(a,b)/sh;
    const beta=0.0611+0.012*Math.min(1,(ratio-1)/0.6),kP=E*t**3/(beta*sh*sh);let kR=0;
    if(s.ribs&&s.t){const rt=s.ribs.t,rh=s.ribs.h,bf=Math.min(16*t,sh/2),A1=bf*t,y1=t/2,A2=rt*rh,y2=t+rh/2,yc=(A1*y1+A2*y2)/(A1+A2);
      const Ir=bf*t**3/12+A1*(yc-y1)**2+rt*rh**3/12+A2*(y2-yc)**2;kR=192*E*Ir/a**3+192*E*Ir/b**3;}
    const k=kP+kR,w0=F/k,sig=(3*F/(2*Math.PI*t*t))*(1+Math.log(Math.max(1.2,sh/2/5)))*(kP/k),sf=M.str/sig,ribY=s.H-t-(s.ribs?s.ribs.h:0)-0.3;
    const rows=[
      ['Deflection',fmt(w0,2)+' mm','max 0.5 mm to feel solid',grade(w0,0.5,1)],
      ['Panel safety factor',fmt(sf,1),'min 2',grade(sf,2,1,false)],
      ['Stiffness',fmt(k,0)+' N/mm','',''],
      ['Ribs carry',kR?fmt(100*kR/k,0)+'% of the load':'no ribs','','']];
    out.press={st:worst(rows),head:fmt(w0,2)+' mm at '+fmt(F,0)+' N',rows,w0,scale:Math.min(4000,0.07*sh/w0),lo:'0 mm',hi:fmt(w0,2)+' mm',
      field:(x,y,z)=>{if(y<ribY)return 0;const fx=Math.max(0,1-(x/ha)**2),fz=Math.max(0,1-(z/hb)**2);return(fx*fz)**2;},
      msg:'Finger load on the top centre. The bend is drawn exaggerated by '+fmt(Math.min(4000,0.07*sh/w0),0)+' times so its shape is visible.'};
  }
  return out;
}

function startSim(){simAnim={t0:performance.now(),dur:S.sim==='thermal'?1500:1900};simT=0;
  if(S.sim==='drop'&&last&&last.shape){const s=last.shape,hV=Math.min(Math.max(s.W,s.D)*1.1,S.simIn.dropH*70),m=Math.max(s.W,s.D,hV+s.H)*2.1;
    camera.position.set(m*0.62,m*0.32+hV/2,m*0.78);controls.target.set(0,(hV+s.H)/2,0);controls.update();}}
function applySim(t){refVis();
  if(S.view!=='sim'||!simRes||!last||!last.shape)return;
  const s=last.shape,r=simRes[S.sim],ease=1-Math.pow(1-t,3);
  if(S.sim==='drop'){
    part.rotation.set(0.28,0,-0.38);part.position.set(0,0,0);part.updateMatrixWorld(true);
    const bb=new THREE.Box3().setFromObject(part),hV=Math.min(Math.max(s.W,s.D)*1.1,S.simIn.dropH*70);let y;
    if(t<0.55){const u=t/0.55;y=hV*(1-u*u);}else{const u=(t-0.55)/0.45;y=hV*0.1*Math.sin(Math.PI*Math.min(1,u*1.7));}
    part.position.y=-bb.min.y+Math.max(0,y);
  }
  const hit=S.sim==='drop'?clamp((t-0.55)/0.15,0,1):1;
  for(const m of part.children){
    const u=m.userData;if(!u||!u.base)continue;
    const g=m.geometry,pos=g.attributes.position,col=g.attributes.color,B=u.base;let moved=false;
    for(let i=0;i<pos.count;i++){
      const x=B[i*3],y=B[i*3+1],z=B[i*3+2];let c=null;
      if(S.sim==='fill'){if(r.na)c=C_NEU;else{const f=r.field(x,y,z)/r.Lmax;c=f<=Math.min(t,r.reach)?cmap(f):(t>=0.999&&f>r.reach?C_SHORT:C_EMPTY);}}
      else if(S.sim==='drop')c=_c.copy(C_NEU).lerp(cmap(r.field(x,y,z)),hit);
      else if(S.sim==='thermal')c=cmap(r.field(x,y,z,u.pull)*(1-Math.exp(-4*t))/(1-Math.exp(-4)));
      else{const w=r.field(x,y,z);pos.setY(i,y-w*r.w0*r.scale*ease);moved=true;c=cmap(w*ease);}
      if(S.sim!=='press'&&pos.getY(i)!==y){pos.setY(i,y);moved=true;}
      if(col&&c)col.setXYZ(i,c.r,c.g,c.b);
    }
    if(col)col.needsUpdate=true;
    if(moved){pos.needsUpdate=true;g.computeVertexNormals();}
  }
}
function renderSimInputs(){
  $('#simTabs').innerHTML=Object.keys(SIMS).map(k=>`<button type="button" data-sim="${k}" aria-pressed="${k===S.sim}">${SIMS[k].replace(' test','')}</button>`).join('');
  const I=Object.assign({},S.simIn,{dropH:S.req.drop});
  const num=(k,l,step,min,max)=>`<div class="frow"><label for="si-${k}">${l}</label><input id="si-${k}" data-si="${k}" type="number" step="${step}" min="${min}" max="${max}" value="${I[k]}"></div>`;
  const sel=(k,l,opts)=>`<div class="frow"><label for="si-${k}">${l}</label><select id="si-${k}" data-si="${k}">${opts.map(o=>`<option value="${o}"${+o===+I[k]?' selected':''}>${o}</option>`).join('')}</select></div>`;
  $('#simIn').innerHTML=S.sim==='drop'?sel('dropH','Drop height (m)',[0.5,0.8,1.0,1.2,1.5,2.0])+num('payload','Contents (g)',10,0,5000)
    :S.sim==='thermal'?num('heatW','Heat load (W)',0.5,0,100)+num('ambient','Ambient (°C)',1,-20,50)
    :S.sim==='press'?sel('force','Load (N)',[20,50,100,200]):'';
  $('#simTitle').textContent=SIMS[S.sim];
}
function renderSim(){
  const list=$('#testList');
  if(!simRes){list.innerHTML='<li class="note">Connect a form to run the tests.</li>';$('#simp').hidden=true;return;}
  list.innerHTML=Object.keys(SIMS).map(k=>{const r=simRes[k];
    return `<li class="rule st-${r.st} test" data-test="${k}" role="button" tabindex="0"><span class="chip">${r.na?'n/a':r.st}</span><div class="rt"><div class="rl">${SIMS[k]}</div><div class="rv">${esc(r.head)}</div></div><span class="go">Run</span></li>`;}).join('');
  const box=$('#simp');box.hidden=S.view!=='sim';if(box.hidden)return;
  const r=simRes[S.sim];
  $('#simOut').innerHTML=r.rows.map(([l,v,lim,st])=>`<div><i class="${st||''}"></i><span>${esc(l)}</span><b>${esc(v)}</b>${lim?`<small>${esc(lim)}</small>`:''}</div>`).join('');
  $('#cbar').hidden=!!r.na;$('#cbMin').textContent=r.lo||'';$('#cbMax').textContent=r.hi||'';$('#simNote').textContent=r.msg||'';
  $('#simRun').disabled=!!r.na;
}
function openTest(k){S.sim=k;S.view='sim';renderProcess();renderSimInputs();startSim();schedule();}
$('#simTabs').addEventListener('click',e=>{const b=e.target.closest('[data-sim]');if(b)openTest(b.dataset.sim);});
$('#testList').addEventListener('click',e=>{const li=e.target.closest('[data-test]');if(li)openTest(li.dataset.test);});
$('#testList').addEventListener('keydown',e=>{const li=e.target.closest('[data-test]');if(li&&(e.key==='Enter'||e.key===' ')){e.preventDefault();openTest(li.dataset.test);}});
$('#simRun').addEventListener('click',()=>startSim());
$('#simIn').addEventListener('change',e=>{const el=e.target.closest('[data-si]');if(!el)return;const v=parseFloat(el.value);if(!isFinite(v))return;S.simIn[el.dataset.si]=v;if(el.dataset.si==='dropH')S.req.drop=v;schedule();startSim();});

/* ================= Components, materials and BOM ================= */
// Catalogue snapshot. Values are typical and must be confirmed against the nominated part's datasheet.
const MATS={
  im:[{id:'abs',name:'ABS, general purpose',E:2300,str:40,k:0.17,flow:180,density:1.05,costKg:2.4,ul94:'HB'},
      {id:'absfr',name:'ABS, flame retardant',E:2400,str:40,k:0.17,flow:160,density:1.18,costKg:3.6,ul94:'V-0'},
      {id:'pcabsfr',name:'PC/ABS, flame retardant',E:2600,str:55,k:0.2,flow:150,density:1.18,costKg:4.2,ul94:'V-0'},
      {id:'pc',name:'Polycarbonate',E:2350,str:60,k:0.2,flow:120,density:1.2,costKg:3.8,ul94:'V-2'},
      {id:'pp',name:'Polypropylene',E:1500,str:30,k:0.22,flow:250,density:0.905,costKg:1.6,ul94:'HB'}],
  cnc:[{id:'al6061',name:'Aluminium 6061-T6',E:69000,str:276,k:167,density:2.70,costKg:7.5,ul94:'metal',metal:true},
       {id:'al6082',name:'Aluminium 6082-T6',E:70000,str:260,k:180,density:2.70,costKg:7.0,ul94:'metal',metal:true},
       {id:'pom',name:'Acetal, POM-C',E:2800,str:60,k:0.31,density:1.41,costKg:9,ul94:'HB'}],
  sls:[{id:'pa12',name:'PA12',E:1700,str:48,k:0.25,density:1.01,costKg:1.0,ul94:'HB'},
       {id:'pa12gb',name:'PA12, glass bead filled',E:3200,str:30,k:0.3,density:1.3,costKg:1.15,ul94:'HB'}],
  /* Typical printed values in the layer plane (XY); across layers is weaker. */
  fdm:[{id:'petg',name:'PETG',E:1700,str:38,k:0.2,density:1.27,costKg:22,ul94:'HB'},
       {id:'pla',name:'PLA',E:2700,str:45,k:0.13,density:1.24,costKg:20,ul94:'HB'},
       {id:'petgcf',name:'PETG-CF, carbon fibre filled',E:3100,str:42,k:0.25,density:1.3,costKg:32,ul94:'HB'},
       {id:'asa',name:'ASA',E:1900,str:36,k:0.17,density:1.07,costKg:25,ul94:'HB'}]
};
const UL={'metal':4,'V-0':3,'V-1':2,'V-2':1,'HB':0};
const BOARDS={
  none:{name:'No board'},
  rpiz2w:{name:'Raspberry Pi Zero 2 W',mfr:'Raspberry Pi Ltd',L:65,Wd:30,h:5,holes:[58,23],holeOff:0,power:2.0,mass:11,cost:15,elec:{wireless:true,vin:[4.75,5.25],sleep:100,active:300,wake:0.05,clock:1000,gpio:26,uart:1,spi:2},src:'Raspberry Pi product brief, typical values'},
  rpi4:{name:'Raspberry Pi 4 Model B',mfr:'Raspberry Pi Ltd',L:85,Wd:56,h:16,holes:[58,49],holeOff:-10,power:4.0,mass:46,cost:55,elec:{wireless:true,vin:[4.75,5.25],sleep:540,active:1200,wake:0.05,clock:1500,gpio:26,uart:1,spi:2},src:'Raspberry Pi product brief, typical values'},
  mcu:{name:'Low-power MCU board',mfr:'Your choice',L:40,Wd:25,h:4,holes:[34,19],holeOff:0,power:0.13,mass:4,cost:6,src:'Catalogue, typical values',elec:{vin:[3.0,3.6],sleep:0.01,active:40,wake:0.005,clock:240,gpio:20,uart:2,spi:1}},
  custom:{name:'Custom PCB',mfr:'Your design',custom:true,src:'Your PCB drawing'}
};
const BATTS={
  none:{name:'No battery'},
  c18650:{name:'Li-ion cell, 18650, 3.5 Ah',shape:'cyl',d:18.6,L:65.2,mass:48,heat:0.4,cost:3.5,cap:3500,imax:8000,rint:0.035,eg:'INR18650-35E'},
  p103450:{name:'Li-ion pouch, 103450, 2.0 Ah',shape:'box',t:10,w:34,L:50,mass:38,heat:0.3,cost:4.0,cap:2000,imax:2000,rint:0.12}
};
const FASTS={
  pt25:{name:'Thread-forming screw for plastics, Ø2.5 × 8',procs:['im','sls','fdm'],hole:2.0,pcbHole:2.0,cost:0.04,mass:0.4},
  pt30:{name:'Thread-forming screw for plastics, Ø3.0 × 10',procs:['im','sls','fdm'],hole:2.4,pcbHole:2.0,cost:0.05,mass:0.6},
  ins25:{name:'M2.5 heat-set insert + M2.5 × 6 screw',procs:['im','sls','fdm'],hole:3.6,pcbHole:3.6,insert:true,cost:0.15,mass:0.7},
  ins30:{name:'M3 heat-set insert + M3 × 8 screw',procs:['im','sls','fdm'],hole:4.0,pcbHole:3.6,insert:true,cost:0.17,mass:0.9},
  tap25:{name:'M2.5 tapped hole + M2.5 × 6 screw',procs:['cnc'],hole:2.05,pcbHole:2.05,cost:0.03,mass:0.3},
  tap30:{name:'M3 tapped hole + M3 × 8 screw',procs:['cnc'],hole:2.5,pcbHole:2.05,cost:0.04,mass:0.5}
};
/* ================= Product context (formgraph@2) ================= */
const CTX=CFG._context||null;
const PROC0=Object.assign({},PROC);
if(CTX)Object.entries(CTX.processes||{}).forEach(([k,v])=>{const b=PROC[v.ruleBase];if(!b||PROC[k]||v.shell===false)return;
  PROC[k]=Object.assign({},b,{id:k,name:v.name||k,short:v.short||k.toUpperCase(),base:v.ruleBase,mat:(v.materials&&v.materials[0]||'').toUpperCase()});PROC0[k]=PROC[k];
  MATS[k]=(MATS[v.ruleBase]||[]).filter(m=>(v.materials||[]).includes(m.id)).map(m=>Object.assign({},m));
  Object.values(FASTS).forEach(f=>{if(f.procs.includes(v.ruleBase)&&!f.procs.includes(k))f.procs.push(k);});});
const MACH=CTX?(CTX.machines||[]).filter(m=>m.process&&PROC0[m.process]):[];
const machinesFor=p=>MACH.filter(m=>m.process===p);
function machineOf(){const m=MACH.find(x=>x.id===S.machine);return m&&m.process===S.proc?m:null;}
function applyMachine(){Object.keys(PROC0).forEach(k=>PROC[k]=PROC0[k]);
  if(MACH.length&&!machineOf()){const f=machinesFor(S.proc)[0];S.machine=f?f.id:null;}
  const m=machineOf();if(!m)return;const L=m.limits||{},b=PROC0[m.process],num=(v,d)=>typeof v==='number'?v:d;
  PROC[m.process]=Object.assign({},b,{wall:[num(L.wallMin,b.wall[0]),num(L.wallMax,b.wall[1])],draft:num(L.draft,b.draft),slotMin:num(L.slotMin,b.slotMin),holeMin:num(L.holeMin,b.holeMin),
    env:m.buildVolume||b.env,machine:m.id,machineName:m.name,tol:L.tolerance,leadDays:m.leadDays,costFactor:m.costFactor||1});}
(function(){const k=CFG.catalogue||{};
  if(k.materials)Object.entries(k.materials).forEach(([p,list])=>{if(MATS[p])list.forEach(m=>{const i=MATS[p].findIndex(x=>x.id===m.id);if(i>=0)MATS[p][i]=Object.assign({},MATS[p][i],m);else MATS[p].push(m);});});
  if(k.boards)Object.assign(BOARDS,k.boards);if(k.batteries)Object.assign(BATTS,k.batteries);if(k.fixings)Object.assign(FASTS,k.fixings);})();
const matPCB=new THREE.MeshStandardMaterial({color:new THREE.Color('#1F6B45').convertSRGBToLinear(),roughness:.6});
const matChip=new THREE.MeshStandardMaterial({color:new THREE.Color('#23272E').convertSRGBToLinear(),roughness:.4,metalness:.2});
const matBatt=new THREE.MeshStandardMaterial({color:new THREE.Color('#2C4E8C').convertSRGBToLinear(),roughness:.45});
const bomLocked=new Set();let curRoles={};let bomToken=0;

function normSel(){
  if(!MATS[S.proc].some(m=>m.id===S.mat))S.mat=MATS[S.proc][0].id;
  const f=FASTS[S.fast];if(!f||!f.procs.includes(S.proc))S.fast=Object.keys(FASTS).find(k=>FASTS[k].procs.includes(S.proc));
  if(!BOARDS[S.board])S.board='none';if(!BATTS[S.batt])S.batt='none';
  S.nom=S.nom||{};S.revs=S.revs||[];S.custom=S.custom||{L:70,W:45,H:8,P:1.5,M:25};
}
const matOf=()=>MATS[S.proc].find(m=>m.id===S.mat)||MATS[S.proc][0];
const matFor=p=>p===S.proc?matOf():MATS[p][0];
const fastOf=()=>FASTS[S.fast]||FASTS.pt25;
const battOf=()=>BATTS[S.batt]||BATTS.none;
function boardOf(){const b=BOARDS[S.board]||BOARDS.none;if(!b.custom)return b;const c=S.custom;
  return Object.assign({},b,{L:+c.L,Wd:+c.W,h:+c.H,power:+c.P,mass:+c.M,cost:0,holes:[Math.max(5,c.L-7),Math.max(5,c.W-7)],holeOff:0,elec:{vin:[3.0,5.5],sleep:0.05,active:Math.max(1,+c.P/3.3*1000),wake:0.01,clock:100,gpio:16,uart:2,spi:1}});}
function fnv(str){let h=0x811c9dc5;for(let i=0;i<str.length;i++){h^=str.charCodeAt(i);h=Math.imul(h,0x01000193)>>>0;}return h.toString(16).toUpperCase().padStart(8,'0').slice(0,6);}
async function sha256(txt){try{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(txt));return[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('');}catch(e){return'fnv-'+fnv(txt);}}
const canonShape=s=>JSON.stringify(s,(k,v)=>typeof v==='number'?+v.toFixed(3):v);

// Where the contents sit: board hangs from moulded standoffs under the top, battery below it, lid closes the open face.
function layout(s){
  if(!s||!s.t)return null;const b=boardOf(),bt=battOf();if(!b.L&&!bt.mass)return null;
  const t=s.t,tan=Math.tan(s.A*DEG),Hi=s.H-t,Fi=Math.max(s.F-t,0);
  const so=Math.max(3,(s.ribs?s.ribs.h:0)+0.5),clr=0.75,insetIn=y=>t+Math.max(0,y)*tan+filletZ(y,Hi,Fi);
  const L={so,clr,insetIn,board:null,batt:null,port:null};let y=Hi-so;
  if(b.L){const half=s.W/2-insetIn(y),halfZ=s.D/2-insetIn(y);
    L.board=Object.assign({},b,{yTop:y,marginX:half-b.L/2-clr,marginZ:halfZ-b.Wd/2-clr});
    L.board.cx=hasUSB()?Math.max(0,L.board.marginX):0;
    if(hasUSB())L.port={y:y-0.8};
    y=y-b.h-1;}
  if(bt.mass){const cyl=bt.shape==='cyl',hgt=cyl?bt.d:bt.t*1.1,len=bt.L,wid=cyl?bt.d:bt.w;
    L.batt=Object.assign({},bt,{yTop:y,hgt,len,wid,marginX:s.W/2-insetIn(y)-len/2-clr,marginZ:s.D/2-insetIn(y)-wid/2-clr});y=y-hgt;}
  L.stackBottom=y+(L.batt?0:1);
  return L;
}
function bossPositions(s){
  const t=s.t,tan=Math.tan(s.A*DEG),Hi=s.H-t,Fi=Math.max(s.F-t,0),capD=t+Hi*tan+Fi;
  const aC=s.W/2-capD,bC=s.D/2-capD,rC=Math.max(s.R-capD,0),od=2*s.bosses.d,m=od/2+0.6;
  const cx=aC-Math.max(rC,m),cz=bC-Math.max(rC,m),k=Math.max(0,rC-m)/Math.SQRT2;
  const pos=[[cx+k,cz+k],[-cx-k,-cz-k],[-cx-k,cz+k],[cx+k,-cz-k],[0,bC-m],[0,-bC+m]];
  return s.bosses.n===2?pos.slice(0,2):s.bosses.n===4?pos.slice(0,4):pos;
}
// DFM limits plus the limits the selected components impose
function roleRange(role,s,P,fin){
  if(role==='hole')return null; // set by the selected fixings, never clamped
  let r=roleRange0(role,s,P,fin);const L=layout(s);
  if(!L||!['W','D','H','F'].includes(role))return r;
  r=r?r.slice():[-Infinity,Infinity];
  const needW=[],needD=[];
  if(L.board){needW.push(2*(L.insetIn(L.board.yTop)+L.board.L/2+L.clr));needD.push(2*(L.insetIn(L.board.yTop)+L.board.Wd/2+L.clr));}
  if(L.batt){needW.push(2*(L.insetIn(L.batt.yTop)+L.batt.len/2+L.clr));needD.push(2*(L.insetIn(L.batt.yTop)+L.batt.wid/2+L.clr));}
  const dDriven=curRoles.D&&!fixable(curRoles.D),ratio=s.D/s.W;
  if(role==='W'){r[0]=Math.max(r[0],...needW,...(dDriven?needD.map(d=>d/ratio):[]));}
  if(role==='D'){r[0]=Math.max(r[0],...needD);}
  if(role==='H'){const b=boardOf(),bt=L.batt;r[0]=Math.max(r[0],s.t+L.so+(L.board?L.board.h+1:0)+(bt?bt.hgt:0)+1.5+0.5);}
  if(role==='F'&&L.port){r[1]=Math.min(r[1],s.t+L.so-1.5);}
  return r;
}
function enforceBOM(ev){
  bomLocked.clear();if(!ev.shape)return ev;const src=ev.roles.hole,f=fastOf();
  if(src&&ev.shape.bosses&&ev.shape.bosses.n){if(src.slider)bomLocked.add(src.slider);
    if(fixable(src)&&Math.abs(src.value-f.hole)>1e-6){setRole(src,f.hole);return evaluate();}}
  return ev;
}
function bomDerived(s){const b=boardOf(),bt=battOf(),f=fastOf(),n=s&&s.bosses?s.bosses.n:0;
  return{heatW:+(EL?EL.heatW:((b.power||0)+(bt.heat||0))).toFixed(2),payload:Math.round((b.mass||0)+(bt.mass||0)+n*f.mass+(b.L?4*0.4:0)+(EL?EL.mass:0))};}
function simInputs(s){return Object.assign({},S.simIn,S.bomDrive?bomDerived(s):{},{dropH:S.req.drop});}

function addComponents(s,add){
  const L=layout(s);if(!L){addElecParts(s,add);return;}const f=fastOf(),tan=Math.tan(s.A*DEG);
  if(L.board){const B=L.board,pcb=1.6;
    if(B.geom&&B.geom.body&&B.geom.body.length)addBody(B.geom,B.cx,B.yTop,0,add,true);
    else{const gb=new THREE.BoxGeometry(B.L,pcb,B.Wd);gb.translate(B.cx,B.yTop-pcb/2,0);add(gb,1,'comp',matPCB);}
    if(!(B.geom&&B.geom.body&&B.geom.body.length)&&B.h>pcb+0.5){const ch=B.h-pcb,gc=new THREE.BoxGeometry(B.L*0.62,ch,B.Wd*0.6);gc.translate(B.cx-B.L*0.12,B.yTop-pcb-ch/2,0);add(gc,1,'comp',matChip);}
    const hx=(B.holes||[0,0])[0]/2,hz=(B.holes||[0,0])[1]/2,ox=B.cx+(B.holeOff||0),h=f.pcbHole,od=Math.max(2*h,h+2.4),y0=B.yTop,y1=s.H-s.t+0.15;
    (B.holes?[[1,1],[1,-1],[-1,1],[-1,-1]]:[]).forEach(([sx,sz])=>{const g=lathe([[h/2,y0],[od/2,y0],[od/2+(y1-y0)*tan,y1],[Math.max(0.3,h/2-0.1),y1],[h/2,y0]],24);g.translate(ox+sx*hx,0,sz*hz);add(g,-1,'body');});
    if(L.port){const r=1.8,hl=(9.2-3.6)/2,pts=[];for(let i=0;i<=10;i++){const a=-Math.PI/2+Math.PI*i/10;pts.push([hl+r*Math.cos(a),r*Math.sin(a)]);}
      for(let i=0;i<=10;i++){const a=Math.PI/2+Math.PI*i/10;pts.push([-hl+r*Math.cos(a),r*Math.sin(a)]);}
      const yp=L.port.y,xo=s.W/2-(yp*tan+filletZ(yp,s.H,s.F)),dep=s.t+0.6,g=prism(pts,dep);g.rotateY(Math.PI/2);g.translate(xo-s.t-0.3,yp,0);add(g,1,'slot');}
  }
  if(L.batt){const T=L.batt;let g;
    if(T.shape==='cyl'){g=new THREE.CylinderGeometry(T.d/2,T.d/2,T.L,28);g.rotateZ(Math.PI/2);g.translate(0,T.yTop-T.d/2,0);}
    else{g=new THREE.BoxGeometry(T.L,T.t,T.w);g.translate(0,T.yTop-T.t/2,0);}
    add(g,1,'comp',matBatt);}
  addElecParts(s,add);
}

function bomLines(s){
  const P=PROC[S.proc],M=matOf(),f=fastOf(),b=boardOf(),bt=battOf(),C=costs(s),V=partVolume(s),nom=S.nom;
  const pn=fnv(canonShape(s)+S.proc+S.mat+S.finish),shellMass=V/1000*M.density,lidMass=areaRR(s.W/2-0.3,s.D/2-0.3,s.R)*(s.t||2)/1000*M.density,shellUnit=C[P.id].at(S.qty);
  const out=[],line=o=>out.push(Object.assign({qty:1,mass:0,unit:0,mpn:null,auto:false,src:'Formgraph catalogue, typical values'},o));
  line({id:'shell',item:'Top shell',spec:`${P.name}, ${finishOf().name}`,mass:shellMass,unit:shellUnit,mpn:'IDI-ENC-'+pn,auto:true,src:'Made to this graph'});
  line({id:'lid',item:'Base lid',spec:`Flat plate, ${fmt(s.t||2,1)} mm, same grade`,mass:lidMass,unit:shellUnit*0.35,mpn:'IDI-LID-'+pn,auto:true,src:'Made to this graph'});
  line({id:'mat',item:'Material grade',spec:`${M.name}, UL94 ${M.ul94==='metal'?'non-combustible':M.ul94}`,qty:+((shellMass+lidMass)/1000).toFixed(3),qtyUnit:'kg',unitNote:'in part price',mpn:nom.mat||null,ph:'Supplier and grade name'});
  if(s.bosses&&s.bosses.n)line({id:'lidfix',item:'Lid fixings',spec:f.name,qty:s.bosses.n,unit:f.cost,mass:f.mass,mpn:nom.lidfix||null,ph:'Manufacturer part number'});
  if(b.L){
    line({id:'board',item:'Main board',spec:`${b.name}, ${fmt(b.L,0)} × ${fmt(b.Wd,0)} × ${fmt(b.h,0)} mm, ${fmt(b.power,1)} W`,mfr:b.mfr,unit:b.cost,mass:b.mass,mpn:nom.board||null,src:b.src,ph:b.custom?'Your PCB part number':'Order code'});
    const fs=f.insert?'M2.5 heat-set insert + M2.5 × 5 screw':f.procs.includes('cnc')?'M2.5 tapped hole + M2.5 × 5 screw':'Thread-forming screw for plastics, Ø2.5 × 6';
    if(b.holes)line({id:'pcbfix',item:'Board fixings',spec:fs,qty:4,unit:0.05,mass:0.4,mpn:nom.pcbfix||null,ph:'Manufacturer part number'});
  }
  if(bt.mass)line({id:'batt',item:'Battery',spec:bt.name,unit:bt.cost,mass:bt.mass,mpn:nom.batt||null,ph:bt.eg?'e.g. '+bt.eg:'Manufacturer part number'});
  if(EL)EL.bomLines.forEach(l=>line(Object.assign({},l,{mpn:l.auto?l.mpn:(nom[l.id]||l.mpn||null)})));
  if(CTX&&MFGP.length)mfgEval(s).forEach(r=>{const c=r.pick;if(!c)return;
    line({id:'mfg_'+r.p.id,item:r.p.name,spec:`${c.m.name}, ${c.mat.name||c.mat.id}`,qty:r.p.qty||1,unit:c.cost,mass:c.mass,mpn:'IDI-MFG-'+fnv(r.p.id+c.m.id+r.d.map(v=>v.toFixed(1)).join('x')),auto:true,src:`Made to this design by ${c.m.name}; cost estimated for ${c.m.process}`});
    const j=r.p.join||{};if(['screw','bolt','rivet'].includes(j.method)&&j.count)line({id:'mfg_'+r.p.id+'_fix',item:`Fixings, ${r.p.name.toLowerCase()}`,spec:j.fastener||j.method,qty:j.count*(r.p.qty||1),unit:0.05,mass:0.6,mpn:null,ph:'Manufacturer part number'});});
  if(CTX)pickedParts().filter(p=>p.role==='other').forEach(p=>line({id:'oth_'+p.id,item:p.name,spec:`${p.manufacturer||''} ${p.mpn||''}`.trim(),qty:p.qty||1,unit:(p.cost&&p.cost.unit)||0,mass:p.mass||0,mpn:p.excluded?null:(p.mpn||null),src:p.datasheet||'context'}));
  if(ENV.ventMembrane)line({id:'membrane',item:'Vent membrane',spec:'ePTFE adhesive vent over the vents, IP67 rated',unit:0.35,mass:0.1,mpn:nom.membrane||null,ph:'Manufacturer part number'});
  return out;
}
function bomChecks(s,lines,L){
  const P=PROC[S.proc],M=matOf(),f=fastOf(),b=boardOf(),bt=battOf(),C=[],ratio=s.D/s.W,add=o=>C.push(Object.assign({lines:[]},o));
  if(L&&L.board){const B=L.board,mx=B.marginX,mz=B.marginZ;
    add({id:'fitBoard',label:'Board fits inside',val:`${fmt(Math.min(mx,mz),1)} mm spare`,lim:`${L.clr} mm clearance`,st:mx>=0&&mz>=0?'pass':'fail',lines:['board','shell'],role:'W',target:s.W+Math.max(0,-2*mx,-2*mz/ratio)+0.5});
    if(L.port){const top=L.port.y+1.8,lim=s.H-s.F-0.5;
      add({id:'port',label:'USB-C opening on a flat wall',val:`top at ${fmt(top,1)} mm`,lim:`fillet starts ${fmt(lim+0.5,1)} mm`,st:top<=lim+1e-6?'pass':'fail',lines:[b.custom?'port':'board','shell'],role:'F',target:s.t+L.so-1.5});}
    if(s.bosses&&s.bosses.n){const od=2*s.bosses.d;let worst=Infinity,fixW=0;
      bossPositions(s).forEach(([x,z])=>{const dx=Math.abs(x-B.cx)-(B.L/2+od/2+0.5),dz=Math.abs(z)-(B.Wd/2+od/2+0.5),c=Math.max(dx,dz);worst=Math.min(worst,c);if(c<0)fixW=Math.max(fixW,Math.min(-2*dx,-2*dz/ratio));});
      add({id:'bossClr',label:'Lid bosses clear the board',val:worst<0?`${fmt(-worst,1)} mm overlap`:`${fmt(worst,1)} mm gap`,lim:'no overlap',st:worst>=0?'pass':'fail',lines:['board','lidfix'],role:'W',target:s.W+fixW+1});}
    add({id:'ribClr',label:'Ribs clear the board',val:`standoffs ${fmt(L.so,1)} mm`,lim:s.ribs?`ribs ${fmt(s.ribs.h,1)} mm`:'no ribs',st:'pass',lines:['board']});
  }
  if(L&&L.batt){const T=L.batt;
    add({id:'fitBatt',label:'Battery fits inside',val:`${fmt(Math.min(T.marginX,T.marginZ),1)} mm spare`,lim:`${L.clr} mm clearance`,st:T.marginX>=0&&T.marginZ>=0?'pass':'fail',lines:['batt','shell'],role:'W',target:s.W+Math.max(0,-2*T.marginX,-2*T.marginZ/ratio)+0.5});
    if(T.shape==='box')add({id:'swell',label:'Pouch swelling allowance',val:'10% of thickness',lim:'held in the stack',st:'pass',lines:['batt']});}
  if(L){const m=L.stackBottom-1.5;add({id:'stack',label:'Contents stack above the lid',val:`${fmt(m,1)} mm spare`,lim:'1.5 mm lid recess',st:m>=0?'pass':'fail',lines:['board','batt','shell'],role:'H',target:s.H-m+0.5});}
  if(s.bosses&&s.bosses.n){const hv=curRoles.hole?curRoles.hole.value:null,ok=hv!=null&&Math.abs(hv-f.hole)<0.01;
    add({id:'hole',label:'Boss holes match the fixings',val:hv!=null?`Ø${fmt(hv,2)} mm`:'n/a',lim:`Ø${f.hole} mm required`,st:ok?'pass':'fail',lines:['lidfix']});
    const bw=last&&last.rep&&last.rep.rules.find(r=>r.label.startsWith('Boss wall'));
    if(bw)add({id:'bossWall',label:'Fixing boss wall vs nominal wall',val:bw.val,lim:bw.lim,st:bw.st,lines:['lidfix','shell'],role:'wall',target:Math.min(P.wall[1],f.hole/1.2)});}
  add({id:'fastProc',label:'Fixings suit '+P.short.toLowerCase(),val:f.name.split(' + ')[0].split(',')[0],lim:'compatible',st:f.procs.includes(S.proc)?'pass':'fail',lines:['lidfix','pcbfix']});
  if(bt.mass||(b.power||0)>15){const ok=(UL[M.ul94]||0)>=2,alt=MATS[S.proc].find(m=>(UL[m.ul94]||0)>=2);
    add({id:'fire',label:'Fire enclosure rating',val:M.ul94==='metal'?'non-combustible':'UL94 '+M.ul94,lim:'V-1 or better with a Li-ion cell inside',st:ok?'pass':'fail',lines:['mat','shell'],action:!ok&&alt?{mat:alt.id,label:'Use '+alt.name.split(',')[0]+(alt.ul94!=='metal'?' FR':'')}:null});}
  if(simRes){const tag=S.bomDrive?'from the BOM':'manual inputs',d=bomDerived(s);
    add({id:'heat',label:'Thermal test, '+tag,val:simRes.thermal.head,lim:S.bomDrive?fmt(d.heatW,1)+' W heat load':'set in Simulate',st:simRes.thermal.st,lines:['board','batt','shell'],open:'thermal'});
    add({id:'drop',label:'Drop test, '+tag,val:simRes.drop.head,lim:S.bomDrive?d.payload+' g of contents':'set in Simulate',st:simRes.drop.st,lines:['shell'],open:'drop'});}
  if(EL){const ef=EL.checks.filter(c=>c.st==='fail');add({id:'elec',label:'Electronics and wiring checks',val:ef.length?ef.length+' failing':'all pass',lim:'no failures',st:ef.length?'fail':EL.checks.some(c=>c.st==='warn')?'warn':'pass',lines:[...new Set(ef.flatMap(c=>c.lines))]});}
  if(ENVM){const vf=ENVM.C.filter(c=>c.st==='fail');add({id:'env',label:'Environment checks',val:vf.length?vf.length+' failing':'all pass',lim:'no failures',st:vf.length?'fail':ENVM.C.some(c=>c.st==='warn')?'warn':'pass',lines:[...new Set(vf.flatMap(c=>c.lines))]});}
  if(REQ&&REQ.length){const mf=REQ.filter(r=>r.pri==='Must'&&r.st==='fail');add({id:'reqs',label:'Must requirements met',val:mf.length?mf.length+' not met':'all met',lim:'every Must',st:mf.length?'fail':'pass'});}
  const miss=lines.filter(l=>!l.auto&&!l.mpn).length;
  add({id:'nom',label:'Part numbers nominated',val:`${lines.length-miss} of ${lines.length}`,lim:'every line',st:miss?'warn':'pass'});
  return C;
}
function renderComp(){
  normSel();
  $('#matSel').innerHTML=MATS[S.proc].map(m=>`<option value="${m.id}"${m.id===S.mat?' selected':''}>${m.name} · ${m.ul94==='metal'?'non-combustible':'UL94 '+m.ul94}</option>`).join('');
  $('#boardSel').innerHTML=Object.entries(BOARDS).map(([k,v])=>`<option value="${k}"${k===S.board?' selected':''}>${v.name}</option>`).join('');
  $('#battSel').innerHTML=Object.entries(BATTS).map(([k,v])=>`<option value="${k}"${k===S.batt?' selected':''}>${v.name}</option>`).join('');
  $('#fastSel').innerHTML=Object.entries(FASTS).filter(([k,v])=>v.procs.includes(S.proc)).map(([k,v])=>`<option value="${k}"${k===S.fast?' selected':''}>${v.name} (Ø${v.hole} hole)</option>`).join('');
  $('#bomDrive').checked=!!S.bomDrive;
  renderFuncs();
  const cb=$('#customBox');cb.hidden=S.board!=='custom';cb.querySelectorAll('[data-cf]').forEach(i=>{if(document.activeElement!==i)i.value=S.custom[i.dataset.cf];});
}
function syncSimInputs(){const d=bomDerived(last&&last.shape);
  [['heatW','si-heatW'],['payload','si-payload']].forEach(([k,id])=>{const el=document.getElementById(id);if(!el)return;el.disabled=!!S.bomDrive;el.title=S.bomDrive?'Set by the BOM':'';if(S.bomDrive)el.value=d[k];});}
async function renderBOM(){
  const s=last&&last.shape,box=$('#bomBody'),tok=++bomToken;syncSimInputs();
  if(!s){box.innerHTML='<p class="note">Connect a form to build the BOM.</p>';return;}
  const L=layout(s),lines=bomLines(s),checks=bomChecks(s,lines,L);
  lines.forEach(l=>{const f=checks.filter(c=>c.st==='fail'&&c.lines.includes(l.id));l.status=f.length?'conflict':(l.auto||l.mpn)?'verified':'nominate';l.why=f.map(c=>c.label.toLowerCase()).join(', ');});
  const ver=lines.filter(l=>l.status==='verified').length,nf=checks.filter(c=>c.st==='fail').length,nom=lines.filter(l=>l.status==='nominate').length;
  const cost=lines.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0),mass=lines.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0);
  const can=ver===lines.length&&!nf,nextRev=String.fromCharCode(65+S.revs.length);
  const head=nf?`${nf} conflict${nf>1?'s':''} to resolve`:nom?`${nom} line${nom>1?'s':''} need a part number`:'Verified, ready to release';
  const col=nf?'var(--fail)':nom?'var(--warn)':'var(--pass)';
  last.bom={lines,checks,fp:null};
  const lab={verified:'Verified',nominate:'Needs part no.',conflict:'Conflict'};
  box.innerHTML=`<div class="bomsum"><span class="big" style="color:${col}">${head}</span>
    <span class="vsub">${ver} of ${lines.length} lines verified · ${gbp(cost)} per set at ${S.qty.toLocaleString('en-GB')} units · ${fmt(mass,0)} g assembled</span>
    <span class="fp">Fingerprint <b id="bomFp">computing</b></span><span class="rev" id="bomRev"></span></div>
    <ul class="bl">${lines.map(l=>`<li class="ln-${l.status}" data-cmp="${esc(l.id)}"><div class="top"><b>${esc(l.item)}</b><span class="num">${l.qtyUnit?fmt(l.qty,3)+' '+l.qtyUnit:l.qty+' × '+gbp(l.unit)}</span><span class="ls ${l.status}">${lab[l.status]}</span></div>
      <div class="spec">${esc(l.spec)}${l.mfr?' · '+esc(l.mfr):''}</div>
      ${l.auto?`<div class="num">${esc(l.mpn)}</div>`:`<input class="mpn" data-line="${l.id}" value="${esc(l.mpn||'')}" placeholder="${esc(l.ph||'Part number')}" aria-label="Part number for ${esc(l.item)}" spellcheck="false">`}
      <div class="src">${esc(l.src)}${l.why?` · <span class="bad">conflicts: ${esc(l.why)}</span>`:''}</div></li>`).join('')}</ul>
    <div class="lbl">BOM checks</div>
    <ul class="rlist">${checks.map((c,i)=>{const fx=c.action?`<button class="fix" type="button" data-bfix="${i}">${esc(c.action.label)}</button>`:c.open&&c.st!=='pass'?`<button class="fix" type="button" data-open="${c.open}">Open test</button>`:c.st!=='pass'&&c.role?`<button class="fix" type="button" data-bfix="${i}"${fixable(curRoles[c.role])?'':' disabled title="Driven by a Math node"'}>Fix</button>`:'<span></span>';
      return `<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div></div>${fx}</li>`;}).join('')}</ul>
    <div class="xrow"><button class="btn pri" type="button" data-rel${can?'':' disabled'}>Release Rev ${nextRev}</button><button class="btn" type="button" data-cpbom>Copy BOM CSV</button></div>
    <p class="note">${can?'Releasing records this fingerprint. Any later change to geometry, process, material, parts or checks changes it.':'Release unlocks when every line is verified: a part number or grade is nominated and no linked check fails.'} Catalogue values are typical; confirm each against the nominated part's datasheet.</p>`;
  const canon=JSON.stringify({proc:S.proc,mat:S.mat,finish:S.finish,req:S.req,dec:S.decisions,shape:JSON.parse(canonShape(s)),lines:lines.map(l=>[l.id,l.spec,l.qty,l.mpn||'']),checks:checks.map(c=>[c.id,c.st])});
  const fp=await sha256(canon);if(tok!==bomToken)return;last.bom.fp=fp;last.bom.can=can;
  $('#bomFp').textContent='sha256:'+fp.slice(0,16);
  const rv=S.revs[S.revs.length-1],re=$('#bomRev');
  if(!rv){re.textContent='Not released yet';re.style.color='var(--muted)';}
  else if(rv.fp===fp){re.textContent=`Matches Rev ${rv.rev}, released ${rv.date}`;re.style.color='var(--pass)';}
  else{re.textContent=`Changed since Rev ${rv.rev} (${rv.date})`;re.style.color='var(--warn)';}
}
$('#bomBody').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b||!last||!last.bom)return;
  if(b.dataset.open){openTest(b.dataset.open);return;}
  if(b.dataset.bfix!=null){const c=last.bom.checks[+b.dataset.bfix];if(!c)return;
    if(c.action){S.mat=c.action.mat;renderComp();}else if(c.role&&!setRole(curRoles[c.role],c.target))return;schedule();return;}
  if(b.hasAttribute('data-rel')){if(!last.bom.can||!last.bom.fp)return;const rev=String.fromCharCode(65+S.revs.length);
    S.revs.push({rev,fp:last.bom.fp,date:new Date().toISOString().slice(0,10),snap:snapNow()});toast(`Released Rev ${rev}.`);schedule();return;}
  if(b.hasAttribute('data-cpbom')){const q=v=>`"${String(v).replace(/"/g,'""')}"`;
    const rows=[['Line','Item','Specification','Manufacturer','Part number','Qty','Unit','Unit GBP','Mass g','Source','Status']];
    last.bom.lines.forEach((l,i)=>rows.push([i+1,l.item,l.spec,l.mfr||'',l.mpn||'',l.qty,l.qtyUnit||'pcs',l.qtyUnit?'':l.unit.toFixed(3),l.qtyUnit?'':(l.mass*l.qty).toFixed(1),l.src,l.status]));
    rows.push([]);rows.push(['Fingerprint','sha256:'+(last.bom.fp||'')]);rows.push(['Revision',(S.revs[S.revs.length-1]||{}).rev||'unreleased']);
    copy(rows.map(r=>r.map(q).join(',')).join('\n'),'BOM CSV');}
});
$('#bomBody').addEventListener('change',e=>{const i=e.target.closest('input.mpn');if(!i)return;i.blur();S.nom[i.dataset.line]=i.value.trim()||undefined;if(!S.nom[i.dataset.line])delete S.nom[i.dataset.line];schedule();});
[['matSel','mat'],['boardSel','board'],['battSel','batt'],['fastSel','fast']].forEach(([id,k])=>$('#'+id).addEventListener('change',e=>{S[k]=e.target.value;renderComp();schedule();}));
$('#bomDrive').addEventListener('change',e=>{S.bomDrive=e.target.checked;schedule();});
$('#customBox').addEventListener('change',e=>{const i=e.target.closest('[data-cf]');if(!i)return;const v=parseFloat(i.value);if(isFinite(v)&&v>0){S.custom[i.dataset.cf]=v;schedule();}});

/* ================= Simple level: plain controls instead of the graph ================= */
const QGROUPS=[['Size and shape',['W','P','H','R','F']],['Build',['t','A']],['Details',['sn','sw','bn','rt','rh']]];
const QHELP={W:'Overall length',P:'Depth as a share of the length',H:'Overall height',R:'How rounded the corners are',F:'How soft the top edge is',t:'Wall thickness',A:'Taper that lets the part leave the mould',sn:'Number of vent slots',sw:'Width of each slot',bn:'Screws holding the lid',rt:'Stiffening rib thickness',rh:'Stiffening rib depth'};
let quickSig='';
function applyLevel(){document.documentElement.setAttribute('data-level',S.level==='eng'?'eng':'simple');
  document.querySelectorAll('[data-level]').forEach(b=>{if(b.tagName==='BUTTON')b.setAttribute('aria-pressed',String((b.dataset.level==='eng')===(S.level==='eng')));});
  if(S.level==='eng')requestAnimationFrame(()=>{drawWires();});}
function renderQuick(ev){
  if(AUTHORED)return; /* an authored form renders its own controls into #quick (authored.js) */
  const box=$('#quick');const sl=G.nodes.filter(n=>n.type==='slider');
  const sig=sl.map(n=>n.id+n.props.key+n.props.min+n.props.max).join('|');
  if(sig!==quickSig){quickSig=sig;const used=new Set();
    const row=n=>{used.add(n.id);const p=n.props;return `<div class="qs" data-q="${n.id}" title="${esc(QHELP[p.key]||'')}"><div class="qt"><span>${esc(p.name)}</span><span class="held" hidden>DFM</span><b></b></div><input class="rng" type="range" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.value}" aria-label="${esc(p.name)}"></div>`;};
    let h='<p class="qintro">Every part has its own section. Drag to shape it; red marks what its maker cannot make.</p><details class="pp" open data-pps="shell"><summary><span>'+esc(SHELL_NAME)+'</span><small id="ppShellSum"></small></summary><div class="ppb">';
    QGROUPS.forEach(([title,keys])=>{const ns=keys.map(k=>sl.find(n=>n.props.key===k)).filter(Boolean);if(ns.length)h+=`<div class="qgrp"><h3>${title}</h3>${ns.map(row).join('')}</div>`;});
    const rest=sl.filter(n=>!used.has(n.id)&&n.props.key!=='bd');if(rest.length)h+=`<div class="qgrp"><h3>Other</h3>${rest.map(row).join('')}</div>`;
    h+='</div></details><div id="partq"></div>';box.innerHTML=h;}
  box.querySelectorAll('[data-q]').forEach(q=>{const n=byId(q.dataset.q);if(!n)return;const p=n.props,r=q.querySelector('.rng'),b=ev&&ev.bands[n.id],span=p.max-p.min;
    if(Math.abs(+r.value-p.value)>p.step/2)r.value=p.value;q.querySelector('b').textContent=fmt(p.value,decOf(p.step))+(p.unit?' '+p.unit:'');
    if(b&&span>0){r.style.setProperty('--lo',clamp((b[0]-p.min)/span*100,0,100)+'%');r.style.setProperty('--hi',clamp((b[1]-p.min)/span*100,0,100)+'%');}else{r.style.setProperty('--lo','0%');r.style.setProperty('--hi','100%');}
    const bl=bomLocked.has(n.id),hd=q.querySelector('.held');hd.textContent=bl?'Set by parts':'At limit';r.disabled=bl;
    hd.hidden=!(bl||S.constrain&&b&&(Math.abs(p.value-b[0])<1e-6&&b[0]>p.min+1e-9||Math.abs(p.value-b[1])<1e-6&&b[1]<p.max-1e-9));});
}
$('#quick').addEventListener('input',e=>{const r=e.target.closest('.rng');if(!r)return;const qq=r.closest('[data-q]');if(!qq)return;const n=byId(qq.dataset.q);if(!n)return;n.props.value=+r.value;
  const el=nodeEls[n.id];if(el){el.querySelector('.rng').value=n.props.value;el.querySelector('.num').value=fmt(n.props.value,decOf(n.props.step));}schedule();});
document.querySelectorAll('button[data-level]').forEach(b=>b.addEventListener('click',()=>{S.level=b.dataset.level;applyLevel();save();if(S.level==='eng')requestAnimationFrame(()=>requestAnimationFrame(fitGraph));}));


/* ================= Electronics: power, placement, wiring, behaviour ================= */
const matLed=new THREE.MeshStandardMaterial({color:new THREE.Color('#2A3A2E').convertSRGBToLinear(),emissive:new THREE.Color('#3BFF7A'),emissiveIntensity:0,roughness:.3});
const matMod=new THREE.MeshStandardMaterial({color:new THREE.Color('#3A4A5E').convertSRGBToLinear(),roughness:.5});
const matWireP=new THREE.MeshStandardMaterial({color:new THREE.Color('#D2402F').convertSRGBToLinear(),roughness:.5});
const matWireD=new THREE.MeshStandardMaterial({color:new THREE.Color('#8E6CE8').convertSRGBToLinear(),roughness:.5});
const matWireR=new THREE.MeshStandardMaterial({color:new THREE.Color('#C9A23A').convertSRGBToLinear(),roughness:.35,metalness:.5});
const STATES=['sleep','measure','locate','transmit','alert'];
const AWG=[[28,1.4,0.213,0.8],[26,2.2,0.134,0.9],[24,3.5,0.084,1.1],[22,7,0.053,1.3]]; // gauge, amps, ohm per m, outer diameter mm
const eNodes=()=>G.nodes.filter(n=>EB[n.type]);
const outType=(n,k)=>{const o=TYPES[n.type].outputs.find(o=>o.k===k);return o?o.t:null;};
const lineOf=n=>n.type==='e_board'?'board':n.type==='e_batt'?'batt':'e_'+n.id;

function blockSpec(n){
  const b=EB[n.type];
  if(n.type==='e_board'){const B=boardOf(),e=B.elec;if(!e)return Object.assign({},b,{missing:'No main board selected'});
    return Object.assign({},b,{name:B.name,inV:Object.fromEntries((b.ins||[['vin','pwr']]).filter(([k,t])=>t==='pwr').map(([k])=>[k,e.vin])),cur:{sleep:e.sleep,measure:e.active,locate:e.active,transmit:e.active,alert:e.active},vnom:e.vnom||(e.vin[0]+e.vin[1])/2,e});}
  if(n.type==='e_batt'){const T=battOf();if(!T.mass)return Object.assign({},b,{missing:'No battery selected'});
    return Object.assign({},b,{name:T.name,out:{bat:{v:[3.4,4.2],imax:T.imax}},T});}
  return b;
}
function innerFn(s){const t=s.t||0,tan=Math.tan(s.A*DEG),Hi=s.H-t,Fi=Math.max(s.F-t,0),ins=y=>t+Math.max(0,y)*tan+filletZ(y,Hi,Fi);
  return{Hi,ins,a:y=>s.W/2-ins(y),b:y=>s.D/2-ins(y),r:y=>Math.max(0,s.R-ins(y))};}
function inRR(x,z,a,b,r){x=Math.abs(x);z=Math.abs(z);if(x>a||z>b)return false;if(x>a-r&&z>b-r)return Math.hypot(x-(a-r),z-(b-r))<=r;return true;}
const boxHit=(p,q,g=0)=>p.x0<q.x1+g&&p.x1>q.x0-g&&p.y0<q.y1+g&&p.y1>q.y0-g&&p.z0<q.z1+g&&p.z1>q.z0-g;
const mkBox=(cx,cy,cz,dx,dy,dz)=>({x0:cx-dx/2,x1:cx+dx/2,y0:cy-dy/2,y1:cy+dy/2,z0:cz-dz/2,z1:cz+dz/2});
function obstacles(s,L){
  const O=[];if(!s.t)return O;const I=innerFn(s),f=fastOf();
  if(L&&L.board){const B=L.board;O.push(Object.assign(mkBox(B.cx,B.yTop-B.h/2,0,B.L,B.h,B.Wd),{id:'board'}));
    const hx=(B.holes||[0,0])[0]/2,hz=(B.holes||[0,0])[1]/2,ox=B.cx+(B.holeOff||0),od=Math.max(2*f.pcbHole,f.pcbHole+2.4);
    (B.holes?[[1,1],[1,-1],[-1,1],[-1,-1]]:[]).forEach(([a,c])=>O.push(Object.assign(mkBox(ox+a*hx,(B.yTop+I.Hi)/2,c*hz,od,Math.max(0.5,I.Hi-B.yTop),od),{id:'standoff'})));}
  if(L&&L.batt){const T=L.batt;O.push(Object.assign(mkBox(0,T.yTop-T.hgt/2,0,T.len,T.hgt,T.wid),{id:'batt'}));}
  if(s.bosses&&s.bosses.n){const od=2*s.bosses.d;bossPositions(s).forEach(([x,z])=>O.push(Object.assign(mkBox(x,(1.5+I.Hi)/2,z,od+1,I.Hi-1.5,od+1),{id:'boss'})));}
  if(s.ribs){const rh=Math.min(s.ribs.h,I.Hi-1),rt=s.ribs.t;O.push(Object.assign(mkBox(0,I.Hi-rh/2,0,s.W,rh,rt),{id:'rib'}),Object.assign(mkBox(0,I.Hi-rh/2,0,rt,rh,s.D),{id:'rib'}));}
  return O;
}
// Greedy placement on the underside of the top or just above the lid, clear of everything already inside
function placeParts(s,L){
  const parts=[],fails=[];const want=eNodes().filter(n=>EB[n.type].place);
  if(!s.t)return{parts,fails:want.map(n=>n.id),O:[]};
  const I=innerFn(s),O=obstacles(s,L),d=derive(s),slotBox=s.slots&&s.slots.n?mkBox(0,I.Hi-1,0,d.span+2,4,d.slotL+2):null;
  const order=want.slice().sort((a,b)=>(EB[b.type].only?1:0)-(EB[a.type].only?1:0));
  for(const n of order){
    const b=EB[n.type],[dx0,dy,dz0]=b.dims,levels=b.only?['ceil']:b.place==='floor'?['floor','ceil']:['ceil','floor'];let best=null,bs=Infinity;
    for(const lv of levels)for(const [dx,dz] of (dx0===dz0?[[dx0,dz0]]:[[dx0,dz0],[dz0,dx0]])){
      const y0=lv==='floor'?2:I.Hi-dy,y1=lv==='floor'?2+dy:I.Hi,aY=Math.min(I.a(y0),I.a(y1))-0.3,bY=Math.min(I.b(y0),I.b(y1))-0.3,rY=Math.min(I.r(y0),I.r(y1));
      for(let x=-aY+dx/2;x<=aY-dx/2;x+=2)for(let z=-bY+dz/2;z<=bY-dz/2;z+=2){
        const bx=mkBox(x,(y0+y1)/2,z,dx,dy,dz);
        if(![[bx.x0,bx.z0],[bx.x0,bx.z1],[bx.x1,bx.z0],[bx.x1,bx.z1]].every(([px,pz])=>inRR(px,pz,aY,bY,rY)))continue;
        if(O.some(o=>boxHit(bx,o,0.5))||parts.some(p=>boxHit(bx,p.box,0.8)))continue;
        if(b.open&&slotBox&&boxHit(bx,slotBox,1))continue;
        const kp=EB.e_ant.keep;let pen=0;
        if(n.type==='e_ant'){if(O.some(o=>o.id!=='rib'&&boxHit(bx,o,kp))||parts.some(p=>EB[p.type].cat!=='ui'&&boxHit(bx,p.box,kp)))pen=500;}
        else if(b.cat!=='ui'&&parts.some(p=>p.type==='e_ant'&&boxHit(bx,p.box,kp)))pen=500;
        const sc=pen+(b.pref==='center'?Math.hypot(x,z):b.pref==='minusx'?x+Math.abs(z)*0.2:b.pref==='plusx'?-x+Math.abs(z+bY*0.5)*0.3:x)+(lv===levels[0]?0:1000);
        if(sc<bs){bs=sc;best=bx;}
      }
    }
    if(best)parts.push({id:n.id,type:n.type,box:best,cx:(best.x0+best.x1)/2,cy:(best.y0+best.y1)/2,cz:(best.z0+best.z1)/2});else fails.push(n.id);
  }
  return{parts,fails,O};
}
function thermalAt(s,Q,Ta){
  const M=matOf(),t=s.t||s.H,As=(areaRR(s.W/2,s.D/2,s.R)+perim(s)*s.H)/1e6,Rpath=1/(6*As)+(t/1000)/(M.k*As)+1/(8*As),d=derive(s);
  const slotArea=s.slots&&s.slots.n?s.slots.n*(s.slots.w*Math.max(d.slotL-s.slots.w,0)+Math.PI*s.slots.w**2/4):0;let Ti=Ta+5;
  for(let i=0;i<80;i++){const Gv=0.00012*slotArea*Math.sqrt(Math.max(Ti-Ta,0.01));Ti=0.5*Ti+0.5*(Ta+Q/(1/Rpath+Gv));}
  const Qw=(Ti-Ta)/Rpath,Ts=Ta+Qw/(8*As);return{Ti,Ts,hot:Ts+(Ts-Ta)*(M.metal?0.05:0.4)};
}
function routeSig(s,cables){return fnv(JSON.stringify([+s.W.toFixed(1),+s.D.toFixed(1),+s.H.toFixed(1),+s.R.toFixed(1),+s.F.toFixed(1),+(s.t||0).toFixed(2),+s.A.toFixed(1),cables.map(c=>[c.key,c.A.map(v=>+v.toFixed(1)),c.B.map(v=>+v.toFixed(1))])]));}

function elecAnalyse(s){
  const ns=eNodes();if(!ns.length||!s)return null;
  const sp={};ns.forEach(n=>sp[n.id]=blockSpec(n));
  const lin=(id,k)=>G.links.find(l=>l.to[0]===id&&l.to[1]===k);
  const lout=(id,k)=>G.links.filter(l=>l.from[0]===id&&l.from[1]===k&&sp[l.to[0]]);
  const find=t=>ns.find(n=>n.type===t),nm=n=>sp[n.id].name;
  const board=find('e_board'),bat=find('e_batt'),chg=find('e_chg'),usb=find('e_usbc'),T=battOf(),hasBat=!!(bat&&T.mass);
  function loadAt(id,k,st,pk,dp=0){if(dp>14)return 0;return lout(id,k).reduce((a,l)=>a+inCur(l.to[0],l.to[1],st,pk,dp+1),0);}
  function inCur(id,k,st,pk,dp){const q=sp[id];if(!q||q.missing)return 0;
    if(q.conv==='chg'){if(k!=='bat')return 0;return 3.3*loadAt(id,'v33',st,pk,dp)/q.eff/3.7+loadAt(id,'vsys',st,pk,dp)+q.iq;}
    if(q.conv==='boost')return 5*loadAt(id,'v5',st,pk,dp)/q.eff/3.7+q.iq;
    if(q.conv==='ldo'){const o=loadAt(id,'v33',st,pk,dp);return(q.eff&&q.vinNom?o*3.3/(q.eff*q.vinNom):o)+q.iq;}
    const c=q.cur||{};let v=c[st];if(v==null)v=c.sleep||0;if(pk&&st==='transmit'&&q.peak)v=q.peak;return v;}
  const C=[],ck=o=>C.push(Object.assign({st:'pass',lines:[]},o));
  // connections
  ns.forEach(n=>{const b=EB[n.type];if(n.type==='e_batt'||n.type==='e_usbc')return;
    if(sp[n.id].missing){ck({id:'miss'+n.id,label:sp[n.id].missing,val:'not selected',lim:'choose one under What goes inside',st:n.type==='e_board'?'fail':'warn'});return;}
    b.ins.forEach(([k,t])=>{if(n.type==='e_chg'&&(k==='vbus'||(k==='bat'&&!hasBat)))return;if(n.type==='e_board'&&k==='usb')return;
      if(!lin(n.id,k))ck({id:'nc'+n.id+k,label:`${nm(n)}: ${PL[k]||k}`,val:'not wired',lim:t==='pwr'?'needs a power source':'needs a link',st:'fail',fix:{act:'wire',label:'Wire it'},lines:[lineOf(n)]});});});
  // supply voltages
  G.links.forEach(l=>{const a=byId(l.from[0]),b=byId(l.to[0]);if(!a||!b||!sp[a.id]||!sp[b.id]||outType(a,l.from[1])!=='pwr')return;
    const sa=sp[a.id],sb=sp[b.id];if(sa.missing||sb.missing)return;const src=sa.out&&sa.out[l.from[1]];if(!src)return;
    let v=src.v;if(v==='bat')v=hasBat?[3.4,4.2]:null;const need=sb.inV&&sb.inV[l.to[1]];if(!need||!v)return;
    if(!(v[0]>=need[0]-0.05&&v[1]<=need[1]+0.05))ck({id:'v'+b.id+l.to[1],label:`${nm(b)} supply voltage`,val:`${fmt(v[0],1)} to ${fmt(v[1],1)} V`,lim:`needs ${fmt(need[0],2)} to ${fmt(need[1],2)} V`,st:'fail',fix:b.type==='e_board'&&need[0]>4?{act:'boost',label:'Add 5 V boost'}:null,lines:[lineOf(b)]});});
  // rail currents
  ns.forEach(n=>{const q=sp[n.id];if(!q.out||q.missing)return;Object.entries(q.out).forEach(([k,o])=>{if(!o.imax)return;const pk=Math.max(...STATES.map(st=>loadAt(n.id,k,st,true)));if(pk<=0)return;
    ck({id:'i'+n.id+k,label:`${nm(n)}, ${PL[k]||k} current`,val:fmt(pk,0)+' mA peak',lim:'rated '+o.imax+' mA',st:pk<=o.imax?'pass':'fail',lines:[lineOf(n)]});});});
  // pins and addresses
  if(board&&sp[board.id].e){const e=sp[board.id].e;
    [['gpio','GPIO pins',e.gpio],['uart','UART ports',e.uart],['spi','SPI devices',e.spi]].forEach(([k,l,cap])=>{const used=lout(board.id,k).length;if(used)ck({id:'pin'+k,label:l,val:used+' used',lim:cap+' on the '+sp[board.id].name,st:used<=cap?'pass':'fail',lines:['board']});});
    const adr={};lout(board.id,'i2c').forEach(l=>{const a=sp[l.to[0]].addr;if(a)(adr[a]=adr[a]||[]).push(l.to[0]);});const dup=Object.keys(adr).filter(a=>adr[a].length>1);
    if(Object.keys(adr).length)ck({id:'i2c',label:'I2C addresses unique',val:dup.length?dup.join(', ')+' repeated':Object.keys(adr).length+' on the bus',lim:'one device per address',st:dup.length?'fail':'pass',lines:['board']});}
  if(hasBat){const o=lout(bat.id,'bat');if(!o.length)ck({id:'batnc',label:'Battery connected',val:'not wired',lim:'to the charger',st:'fail',fix:{act:'wire',label:'Wire it'},lines:['batt']});
    else if(o.some(l=>byId(l.to[0]).type!=='e_chg'))ck({id:'prot',label:'Battery goes through the charger',val:'direct load on the cell',lim:'protection in the path',st:'warn',lines:['batt']});}
  // functions
  const radio=ns.find(n=>EB[n.type].radio),ant=find('e_ant'),th=find('e_th'),btn=find('e_btn'),led=find('e_led'),gn=find('e_gnss');
  const linked=(a,b)=>G.links.some(l=>(l.from[0]===a&&l.to[0]===b)||(l.from[0]===b&&l.to[0]===a));
  const rfAnt=radio&&G.links.some(l=>l.from[0]===radio.id&&l.from[1]==='rf'&&byId(l.to[0])&&byId(l.to[0]).type==='e_ant');
  const radioOK=!!(radio&&board&&linked(board.id,radio.id)&&rfAnt&&lin(radio.id,EB[radio.type].ins[0][0]));
  if(radio&&!rfAnt)ck({id:'ant',label:'Radio has an antenna',val:'no antenna',lim:'RF link to an antenna',st:'fail',fix:ant?{act:'wire',label:'Wire it'}:{act:'func',type:'e_ant',on:true,label:'Add antenna'},lines:[lineOf(radio)]});
  const thOK=!!(th&&board&&linked(board.id,th.id)&&lin(th.id,'vcc')),btnOK=!!(btn&&board&&linked(board.id,btn.id)),ledOK=!!(led&&board&&linked(board.id,led.id));
  const chgPath=!!(usb&&chg&&hasBat&&lin(chg.id,'vbus')&&lin(chg.id,'bat'));
  // duty cycle and battery life
  const R=S.req,covF={good:1,fair:1.5,poor:3}[R.cov]||1.5,forever=S.decisions.nosignal==='forever';
  const interval=Math.max(60,R.interval*60),cycles=86400/interval+4,tMeas=2,tLoc=gn&&lin(gn.id,'vcc')?EB.e_gnss.fix:0;
  const txBase=radio?EB[radio.type].tx:0,tTx=radioOK?txBase*covF*(forever&&R.cov==='poor'?1.7:1):0;
  const Ib={};STATES.forEach(st=>Ib[st]=hasBat?loadAt(bat.id,'bat',st,false):0);
  const busy=cycles*(tMeas+tLoc+tTx),avg=(Ib.sleep*Math.max(0,86400-busy)+cycles*(Ib.measure*tMeas+Ib.locate*tLoc+Ib.transmit*tTx))/86400;
  const life=hasBat&&avg>0?T.cap*0.8/avg/24:null,Ipk=hasBat?loadAt(bat.id,'bat','transmit',true):0;
  const bE=board&&sp[board.id].e;const avgW=hasBat?avg*3.7/1000:(bE?bE.active*sp[board.id].vnom/1000:0.2);
  const chgI=chg?EB.e_chg.chg:0,chgW=chgPath?(5-3.7)*chgI/1000+0.1+avgW:0,Ta=S.simIn.ambient;
  const thOp=thermalAt(s,avgW,Ta),thCh=chgPath?thermalAt(s,chgW,Ta):null;
  // placement and cables
  const L=layout(s),PP=placeParts(s,L),pos={};
  if(L&&L.board)pos.board=[L.board.cx,L.board.yTop-1,0];if(L&&L.batt)pos.batt=[0,L.batt.yTop-L.batt.hgt/2,0];
  PP.parts.forEach(p=>pos[p.id]=[p.cx,p.cy,p.cz]);
  if(usb&&!pos.board)pos[usb.id]=[s.W/2-(s.t||2)-3,Math.max(4,s.H/2),0];
  const partKey=n=>n.type==='e_board'?'board':n.type==='e_batt'?'batt':(n.type==='e_usbc'&&pos.board)?'board':n.id;
  const cabs={},cond={pwr:1,i2c:2,uart:2,spi:4,gpio:1,usb:2,rf:1};
  G.links.forEach(l=>{const a=byId(l.from[0]),b=byId(l.to[0]);if(!a||!b||!EB[a.type]||!EB[b.type])return;const ka=partKey(a),kb=partKey(b);if(ka===kb||!pos[ka]||!pos[kb])return;
    if(a.type==='e_batt'&&!hasBat)return;const t=outType(a,l.from[1]),key=[ka,kb].sort().join('|')+(t==='rf'?'|rf':'');
    const c=cabs[key]||(cabs[key]={key,a:ka,b:kb,cond:0,rf:t==='rf',imax:0,pwr:false});c.cond+=cond[t]||1;
    if(t==='pwr'){c.pwr=true;const pk=Math.max(...STATES.map(st=>a.type==='e_batt'?loadAt(a.id,'bat',st,true):inCur(b.id,l.to[1],st,true,0)));c.imax=Math.max(c.imax,pk);}});
  let cables=Object.values(cabs).map(c=>Object.assign(c,{cond:c.rf?1:c.cond+1,A:pos[c.a],B:pos[c.b]}));
  const sig=routeSig(s,cables),J=JOBS.route,routes=J.status==='done'&&J.res&&J.res.sig===sig?J.res.cables:{};
  const label=k=>k==='board'?'Board':k==='batt'?'Battery':nm(byId(k));
  cables=cables.map(c=>{const rr=routes[c.key],est=Math.abs(c.A[0]-c.B[0])+Math.abs(c.A[1]-c.B[1])+Math.abs(c.A[2]-c.B[2]);
    const len=(rr&&rr.ok?rr.len:est*1.15)+15,amps=c.imax/1000,g=c.rf?null:(c.pwr?AWG.find(w=>w[1]>=amps*1.25)||AWG[3]:AWG[0]);
    const ce=k=>{const nd=k==='board'?{type:'e_board'}:k==='batt'?null:byId(k),bb=nd&&EB[nd.type];return bb&&bb.conn?Object.values(bb.conn)[0]:null;},cn=ce(c.b)||ce(c.a);
    const fam=c.rf?'U.FL coax':cn?`${cn.family}${cn.pitch?' '+cn.pitch+' mm':''}`:c.imax>1500?'JST XH 2.5 mm':c.imax>800?'JST PH 2.0 mm':'JST SH 1.0 mm';c.mate=cn&&cn.mate||'';
    return Object.assign(c,{rr,len,routed:!!(rr&&rr.ok),awg:g?g[0]:null,od:c.rf?1.13:(g?g[3]:0.8),ohm:g?g[2]:0,fam,vd:c.pwr&&g?amps*g[2]*len/1000*2:0,label:`${label(c.a)} to ${label(c.b)}`});});
  cables.forEach(c=>{
    if(c.pwr){const g=AWG.find(w=>w[0]===c.awg),amps=c.imax/1000,rail=(c.a==='batt'||c.b==='batt')?3.7:3.3,pct=c.vd/rail*100;
      ck({id:'amp'+c.key,label:`${c.label}, wire size`,val:`${c.awg} AWG at ${fmt(amps,2)} A peak`,lim:`rated ${g[1]} A`,st:amps<=g[1]?'pass':'fail',lines:['harness']});
      ck({id:'vd'+c.key,label:`${c.label}, voltage drop`,val:`${fmt(c.vd*1000,0)} mV, ${fmt(pct,1)}%`,lim:'max 3% of the rail',st:pct<=3?'pass':pct<=6?'warn':'fail',lines:['harness']});}
    if(c.rr){if(!c.rr.ok)ck({id:'rt'+c.key,label:`${c.label}, route`,val:'no path found',lim:'clear path inside',st:'fail',fix:{act:'role',role:'W',target:s.W+10,label:'Make it larger'},lines:['harness']});
      else{const need=(c.rf?5:6)*c.od;ck({id:'bend'+c.key,label:`${c.label}, bend radius`,val:c.rr.minR>=999?'straight':fmt(c.rr.minR,1)+' mm',lim:`min ${fmt(need,1)} mm`,st:c.rr.minR>=need*0.5?'pass':'warn',lines:['harness']});
        if(c.rf)ck({id:'rfn'+c.key,label:'Antenna cable clear of the board',val:fmt(Math.min(c.rr.boardDist,99),1)+' mm',lim:'min 3 mm',st:c.rr.boardDist>=3?'pass':'warn',lines:['harness']});}}});
  PP.fails.forEach(id=>{const n=byId(id);ck({id:'room'+id,label:`Room inside for the ${nm(n)}`,val:'no space found',lim:'clear of other parts',st:'fail',fix:{act:'role',role:'W',target:s.W+12,label:'Make it larger'},lines:[lineOf(n)]});});
  // battery under load
  let brown=null;
  if(hasBat&&radioOK&&Ipk>0){const bc=cables.find(c=>c.a==='batt'||c.b==='batt'),Rw=bc&&bc.pwr?bc.ohm*bc.len/1000*2:0.02,rk=EB[radio.type].ins[0][0],vmin=EB[radio.type].inV[rk][0],v=3.6-Ipk/1000*(T.rint+Rw);
    brown={v,vmin,Rw};
    ck({id:'brown',label:'Battery holds up while sending',val:`${fmt(v,2)} V at ${fmt(Ipk/1000,2)} A`,lim:`radio needs ${vmin} V (20% charge, 20 °C)`,st:v>=vmin+0.05?'pass':v>=vmin?'warn':'fail',lines:['batt',lineOf(radio)]});
    ck({id:'bpk',label:'Battery peak current',val:fmt(Ipk,0)+' mA',lim:'cell rated '+T.imax+' mA',st:Ipk<=T.imax?'pass':'fail',fix:Ipk>T.imax&&S.batt!=='c18650'?{act:'batt',id:'c18650',label:'Use an 18650 cell'}:null,lines:['batt']});
    if(JOBS.tol.status==='done'&&JOBS.tol.res){const pp=JOBS.tol.res.p*100;ck({id:'brownwc',label:'Brownout risk, worst case',val:fmt(pp,1)+'% of conditions',lim:'cold, low charge, cell spread',st:pp<=1?'pass':pp<=10?'warn':'fail',lines:['batt']});}}
  // radio, sky, sensor, EMC
  const M=matOf();let margin=null;
  if(radio){const antP=ant&&PP.parts.find(p=>p.id===ant.id);let pen=M.metal?25:1.5;const notes=[];
    if(antP){const kp=EB.e_ant.keep,near=bx=>{const dx=Math.max(bx.x0-antP.box.x1,antP.box.x0-bx.x1,0),dy=Math.max(bx.y0-antP.box.y1,antP.box.y0-bx.y1,0),dz=Math.max(bx.z0-antP.box.z1,antP.box.z0-bx.z1,0);return Math.hypot(dx,dy,dz)<kp;};
      if(PP.O.some(o=>o.id==='board'&&near(o))||PP.parts.some(p=>p.id!==antP.id&&EB[p.type].cat!=='ui'&&near(p.box))){pen+=6;notes.push('parts in the clear zone');}
      if(PP.O.some(o=>(o.id==='boss'||o.id==='standoff')&&near(o))){pen+=3;notes.push('metal fixings nearby');}}
    margin=EB[radio.type].margin-({good:0,fair:6,poor:14}[R.cov]||6)-pen;
    ck({id:'rf',label:'Radio link margin',val:fmt(margin,0)+' dB',lim:'min 6 dB'+(M.metal?', metal enclosure blocks it':'')+(notes.length?', '+notes.join(', '):''),st:margin>=6?'pass':margin>=0?'warn':'fail',fix:M.metal?{act:'mat',mat:'pom',label:'Use POM-C'}:null,lines:['mat',lineOf(radio)]});}
  if(gn&&M.metal)ck({id:'sky',label:'GNSS can see the sky',val:'metal enclosure',lim:'non-metal above the receiver',st:'fail',fix:{act:'mat',mat:'pom',label:'Use POM-C'},lines:['mat',lineOf(gn)]});
  if(th&&!(s.slots&&s.slots.n))ck({id:'vent',label:'Sensor reaches outside air',val:'no vents',lim:'vent slots near the sensor',st:'warn',fix:{act:'role',role:'slotN',target:5,label:'Add vents'},lines:[lineOf(th)]});
  if(M.metal&&s.slots&&s.slots.n){const f=300000/(20*Math.max(1,derive(s).slotL)),fx=Math.max(bE?bE.clock:0,radio?EB[radio.type].mhz:0);
    ck({id:'emc',label:'Vent slots and EMC shielding',val:`slots leak above ${fmt(f,0)} MHz`,lim:`fastest signal ${fx} MHz`,st:f>fx?'pass':'warn',lines:['shell']});}
  // behaviour decisions
  const DEC=[];
  if(usb&&radio)DEC.push({id:'usbbusy',q:'USB-C is plugged in while a reading is being sent',opts:[['finish','Finish sending, then charge'],['now','Start charging straight away']]});
  if(hasBat&&radio)DEC.push({id:'lowbusy',q:'The battery runs low while sending',opts:[['finish','Finish, then sleep'],['abort','Stop and keep the reading']]});
  if(btn&&radio)DEC.push({id:'btnbusy',q:'The button is pressed while sending',opts:[['queue','Take another reading after'],['ignore','Ignore the press']]});
  if(radio)DEC.push({id:'nosignal',q:'There is no signal',opts:[['store','Retry 3 times, then keep the reading'],['forever','Keep retrying until sent']]});
  const openD=DEC.filter(x=>!S.decisions[x.id]);
  if(DEC.length)ck({id:'dec',label:'Behaviour decided for every situation',val:openD.length?`${openD.length} of ${DEC.length} open`:`${DEC.length} decided`,lim:'answer them under Try it',st:openD.length?'warn':'pass',fix:openD.length?{act:'device',label:'Decide'}:null});
  // BOM lines for electronics and harness
  const bomLines=[];
  ns.forEach(n=>{if(n.type==='e_board'||n.type==='e_batt')return;if(n.type==='e_usbc'&&pos.board&&!boardOf().custom)return;const b=EB[n.type];
    const parts=[];b.ins.forEach(([k,t])=>{if(t!=='pwr')parts.push(t.toUpperCase());});if(b.addr)parts.push('address '+b.addr);if(b.peak)parts.push(fmt(b.peak/1000,1)+' A peak');if(b.mhz)parts.push(b.mhz+' MHz');if(b.open)parts.push('needs an opening');
    bomLines.push({id:'e_'+n.id,item:b.name,spec:parts.join(', ')||'electronics',unit:b.cost,mass:b.mass,ph:'Manufacturer part number',mpn:b.standIn?null:(b.mpn||null),mfr:b.mfr,src:b.src||'Formgraph catalogue, typical values',standIn:!!b.standIn});});
  if(cables.length){const conn={},wire={};
    cables.forEach(c=>{const k=c.fam+'|'+c.cond+'|'+(c.mate||'');conn[k]=(conn[k]||0)+2;const w=c.rf?'coax':c.awg;wire[w]=(wire[w]||0)+c.len*(c.rf?1:c.cond)/1000;});
    bomLines.push({id:'harness',item:'Wiring harness',spec:`${cables.length} cable${cables.length>1?'s':''}${cables.every(c=>c.routed)?', routed':', lengths estimated until routed'}`,unit:0.6+cables.length*0.35,mass:cables.reduce((a,c)=>a+c.len/1000*c.cond*1.5,0),auto:true,mpn:'IDI-HAR-'+fnv(JSON.stringify(cables.map(c=>[c.key,Math.round(c.len),c.awg]))),src:'Made to this graph, see the wiring table'});
    Object.entries(conn).forEach(([k,q])=>{const [f,p,mt]=k.split('|'),rf=f.startsWith('U.FL');bomLines.push({id:'cn_'+f.replace(/\W+/g,'')+p,item:rf?'U.FL plug':`${f} housing, ${p}-pin`,spec:rf?'For 1.13 mm coax':`With ${p} crimp terminals each`,qty:q,unit:rf?0.25:0.08+p*0.02,mass:0.2,ph:'Manufacturer part number',src:mt?'Mating part from the component datasheet':'Connector family from current and pin count',mpn:mt||null});});
    Object.entries(wire).forEach(([w,m])=>bomLines.push({id:'w_'+w,item:w==='coax'?'Coax cable, 1.13 mm':`Wire, ${w} AWG`,spec:'Cut lengths from the wiring table',qty:+m.toFixed(3),qtyUnit:'m',unit:0,mass:0,ph:'Manufacturer part number',src:'Size from peak current'}));}
  const mass=ns.reduce((a,n)=>a+(n.type==='e_board'||n.type==='e_batt'?0:(EB[n.type].mass||0)),0)+cables.reduce((a,c)=>a+c.len/1000*c.cond*1.5,0);
  const nodeInfo={};ns.forEach(n=>{const q=sp[n.id];let txt='';
    if(n.type==='e_board')txt=q.e?`${q.name}, needs ${q.e.vin[0]} to ${q.e.vin[1]} V`:'none selected';
    else if(n.type==='e_batt')txt=hasBat?`${T.name}, ${T.cap} mAh`:'none selected';
    else if(q.conv)txt=n.type==='e_chg'?`charges at ${q.chg} mA`:'5 V for 5 V boards';
    else if(q.cur){const pk=q.peak||Math.max(...STATES.map(st=>q.cur[st]||0));txt=`${fmt(q.cur.sleep||0,3)} mA asleep, ${fmt(pk,0)} mA peak`;}
    else txt=n.type==='e_usbc'?'5 V and USB 2.0':n.type==='e_ant'?'keep 10 mm clear':'';
    const f=C.filter(c=>c.st==='fail'&&c.lines.includes(lineOf(n))).length;nodeInfo[n.id]={txt:(f?`${f} problem${f>1?'s':''}. `:'')+txt,st:f?'fail':'pass'};});
  return{nodes:ns,sp,checks:C,Ib,avg,life,Ipk,cap:hasBat?T.cap:0,hasBat,radio,radioOK,thOK,btnOK,ledOK,tLoc,txBase:radioOK?txBase:0,chgPath,chgI,avgW,chgW,thOp,thCh,
    heatW:Math.max(avgW,chgW),latency:bE?bE.wake+0.03:null,margin,brown,parts:PP.parts,O:PP.O,pos,cables,routeSig:sig,DEC,mass,bomLines,nodeInfo,
    sensErr:th?EB.e_th.acc+(thOp.Ti-Ta)*((s.slots&&s.slots.n)?0.15:0.6):null,chgH:chgPath?T.cap/chgI*1.15:null};
}

/* ---------- editing helpers ---------- */
function autoWire(n){
  const f=t=>G.nodes.find(m=>m.type===t),has=(id,k)=>G.links.some(l=>l.to[0]===id&&l.to[1]===k);
  const link=(a,ak,b,bk)=>{if(!a||!b||a===b||has(b.id,bk)||reaches(b.id,a.id))return;G.links.push({from:[a.id,ak],to:[b.id,bk]});};
  const board=f('e_board'),chg=f('e_chg'),bat=f('e_batt'),usb=f('e_usbc'),boost=f('e_boost'),needs5=()=>{const e=boardOf().elec;return!!(e&&e.vin[0]>4);};
  const ext=f('e_ext'),ldo=f('e_ldo');
  const PRI={e_ldo:0,e_chg:0,e_boost:0,e_board:1,e_ext:2,e_batt:3,e_usbc:4};
  const feedByWindow=(m,k)=>{const w=m.type==='e_board'?((boardOf().elec||{}).vin):((EB[m.type].inV||{})[k]);if(!Array.isArray(w))return false;const c=[];
    G.nodes.forEach(n=>{if(n===m||!EB[n.type]||!TYPES[n.type])return;const b=EB[n.type];(TYPES[n.type].outputs||[]).filter(o=>o.t==='pwr').forEach(o=>{let v=((b.out||{})[o.k]||{}).v;if(v==='bat')v=[3.4,4.2];
      if(Array.isArray(v)&&v[0]>=w[0]-0.05&&v[1]<=w[1]+0.05&&!reaches(n.id,m.id))c.push([PRI[n.type]!=null?PRI[n.type]:1,-(((b.out||{})[o.k]||{}).imax||0),n,o.k]);});});
    if(!c.length)return false;c.sort((a,b)=>a[0]-b[0]||a[1]-b[1]);link(c[0][2],c[0][3],m,k);return true;};
  const feed=(m,k)=>{if(feedByWindow(m,k))return;if(m.type==='e_ldo'){const bo=board&&TYPES[board.type]&&TYPES[board.type].outputs.find(o=>o.t==='pwr');if(bo)link(board,bo.k,m,k);else if(ext)link(ext,'vext',m,k);else link(chg||usb,chg?'vsys':'vbus',m,k);return;}
    if(m.type==='e_board'&&ext){link(ext,'vext',m,k);return;}
    if(m.type==='e_chg'){if(k==='vbus')link(usb,'vbus',m,k);else link(bat,'bat',m,k);return;}
    if(m.type==='e_board'){if(needs5())link(boost||usb,boost?'v5':'vbus',m,k);else link(chg,'v33',m,k);return;}
    if(k==='vbat'||m.type==='e_boost'){link(chg||bat||ext,chg?'vsys':bat?'bat':'vext',m,k);return;}link(chg||ldo,'v33',m,k);};
  const all=G.nodes.filter(m=>EB[m.type]);
  const wireNode=m=>{TYPES[m.type].inputs.forEach(d=>{if(has(m.id,d.k))return;
      if(d.t==='pwr')feed(m,d.k);
      else if(d.t==='rf'){const r=all.find(q=>EB[q.type].radio&&!G.links.some(l=>l.from[0]===q.id&&l.from[1]==='rf'));link(r,'rf',m,d.k);}
      else if(d.t==='usb')link(usb,'usb',m,d.k);else if(m.type!=='e_board')link(board,d.t,m,d.k);});
    if(EB[m.type].radio){const a=all.find(q=>q.type==='e_ant'&&!has(q.id,'rf'));link(m,'rf',a,'rf');}};
  if(n)wireNode(n);all.forEach(m=>{if(m!==n)wireNode(m);});
}
function newENode(type){const col={conn:20,power:260,compute:500,sense:760,comm:1000,ui:760}[EB[type].cat]||760;
  const ys=G.nodes.filter(n=>EB[n.type]&&Math.abs(n.x-col)<5).map(n=>n.y);const n={id:newId(type.slice(2)),type,x:col,y:ys.length?Math.max(...ys)+130:20,props:{},inVals:{}};G.nodes.push(n);return n;}
function removeNode(id){G.nodes=G.nodes.filter(m=>m.id!==id);G.links=G.links.filter(l=>l.from[0]!==id&&l.to[0]!==id);}
function setFunction(type,on){
  if(on){if(EB[type].radio)G.nodes.filter(n=>EB[n.type].radio&&n.type!==type).forEach(n=>removeNode(n.id));
    if(!G.nodes.some(n=>n.type===type)){newENode(type);if(type==='e_usbc'&&!G.nodes.some(m=>m.type==='e_chg'))newENode('e_chg');if(EB[type].radio&&!G.nodes.some(m=>m.type==='e_ant'))newENode('e_ant');}
    autoWire(null);}
  else{G.nodes.filter(n=>n.type===type).forEach(n=>removeNode(n.id));if(EB[type].radio)G.nodes.filter(n=>n.type==='e_ant').forEach(n=>removeNode(n.id));}
  renderNodes();renderFuncs();schedule();
}
function addBoost(){const b=G.nodes.find(n=>n.type==='e_board');if(!b)return;const bo=G.nodes.find(n=>n.type==='e_boost')||newENode('e_boost');
  G.links=G.links.filter(l=>!(l.to[0]===b.id&&l.to[1]==='vin'));autoWire(bo);if(!G.links.some(l=>l.to[0]===b.id&&l.to[1]==='vin'))G.links.push({from:[bo.id,'v5'],to:[b.id,'vin']});}
function applyFix(f){if(!f)return;
  switch(f.act){case'wire':autoWire(null);renderNodes();break;case'boost':addBoost();renderNodes();break;case'func':setFunction(f.type,f.on);return;
    case'role':if(!setRole(curRoles[f.role],f.target))return;break;case'mat':if(MATS[S.proc].some(m=>m.id===f.mat))S.mat=f.mat;renderComp();break;
    case'batt':S.batt=f.id;renderComp();break;case'color':S.color=f.color;renderProcess();break;case'finish':S.finish=f.finish;renderProcess();break;case'device':openDevice();return;}
  schedule();}
function renderFuncs(){const box=$('#funcs');if(!box)return;
  box.innerHTML=Object.entries(EB).filter(([k,b])=>b.func).map(([k,b])=>`<label><input type="checkbox" data-func="${k}"${G.nodes.some(n=>n.type===k)?' checked':''}><span>${esc(b.func)}</span></label>`).join('');}
$('#funcs').addEventListener('change',e=>{const i=e.target.closest('[data-func]');if(i)setFunction(i.dataset.func,i.checked);});
$('#boardSel').addEventListener('change',()=>{const b=G.nodes.find(n=>n.type==='e_board');if(b){G.links=G.links.filter(l=>!(l.to[0]===b.id&&l.to[1]==='vin'));autoWire(b);renderNodes();}});
function applyGtab(){document.querySelectorAll('[data-gtab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.gtab===(S.gtab||'form'))));
  document.querySelectorAll('[data-add]').forEach(b=>b.hidden=S.gtab==='elec');$('#eAdd').hidden=S.gtab!=='elec';}
document.querySelectorAll('[data-gtab]').forEach(b=>b.addEventListener('click',()=>{S.gtab=b.dataset.gtab;selId=null;applyGtab();renderNodes();requestAnimationFrame(()=>requestAnimationFrame(fitGraph));save();}));
$('#eAdd').innerHTML='<option value="">Add a part</option>'+Object.entries(EB).filter(([k,b])=>!b.fixed).map(([k,b])=>`<option value="${k}">${esc(b.name)}</option>`).join('');
$('#eAdd').addEventListener('change',e=>{if(!e.target.value)return;addNode(e.target.value);e.target.value='';renderFuncs();});
applyGtab();

/* ---------- 3D: parts, openings, harness ---------- */
const bodyMats={};
function bodyMat(c){c=c||'#3a3f47';return bodyMats[c]||(bodyMats[c]=new THREE.MeshStandardMaterial({color:new THREE.Color(c).convertSRGBToLinear(),roughness:.55,metalness:.1}));}
// Draws a part from its datasheet geometry. Context axes: x, y on the part, z up from its base. Engine axes: x, y up, z.
function addBody(geom,cx,y0,cz,add,down){(geom.body||[]).forEach(b=>{const [x,y,z]=b.at,[a,c,h]=b.size;let g;
    if(b.shape==='cyl'){g=new THREE.CylinderGeometry(a/2,a/2,h,24);}else g=new THREE.BoxGeometry(a,h,c);
    g.translate(cx+x,down?y0-z:y0+z,cz+y);add(g,1,'comp',bodyMat(b.color));});}
function addElecParts(s,add){
  if(!EL||!s.t)return;
  EL.parts.forEach(p=>{const b=EB[p.type],bx=p.box;
    if(b.geom&&b.geom.body&&b.geom.body.length)addBody(b.geom,p.cx,p.cy-(bx.y1-bx.y0)/2,p.cz,add,false);
    else{const g=new THREE.BoxGeometry(bx.x1-bx.x0,bx.y1-bx.y0,bx.z1-bx.z0);g.translate(p.cx,p.cy,p.cz);add(g,1,'comp',p.type==='e_ant'?matWireR:b.cat==='ui'?matChip:matMod);}
    if(b.open){const r=b.open==='button'?3.5:b.open==='led'?1.7:1.1,holes=b.open==='sound'?[[-2.5,0],[0,0],[2.5,0]]:[[0,0]];
      holes.forEach(([ox,oz])=>{const pts=[];for(let i=0;i<20;i++){const a=i/20*Math.PI*2;pts.push([r*Math.cos(a),r*Math.sin(a)]);}const gh=prism(pts,s.t+0.14);gh.rotateX(-Math.PI/2);gh.translate(p.cx+ox,s.H-s.t-0.07,p.cz+oz);add(gh,1,'slot');});
      if(b.open==='button'){const gc=new THREE.CylinderGeometry(3,3,s.t+1,24);gc.translate(p.cx,s.H-s.t/2+0.5,p.cz);add(gc,1,'comp',matBtnCap);}
      if(b.open==='led'){const gl=new THREE.CylinderGeometry(1.5,1.5,s.t+0.4,16);gl.translate(p.cx,s.H-s.t/2+0.2,p.cz);add(gl,1,'comp',matLed);}}});
  EL.cables.forEach(c=>{const raw=c.rr&&c.rr.ok?c.rr.pts:[c.A,[(c.A[0]+c.B[0])/2,Math.min(c.A[1],c.B[1])-3,(c.A[2]+c.B[2])/2],c.B];
    const v=raw.map(q=>new THREE.Vector3(q[0],q[1],q[2]));if(v.length<2)return;if(v.length===2)v.splice(1,0,v[0].clone().lerp(v[1],0.5));
    const g=new THREE.TubeGeometry(new THREE.CatmullRomCurve3(v,false,'centripetal'),Math.min(240,v.length*14),Math.max(0.45,c.rf?0.6:c.od/2*Math.sqrt(c.cond)*0.6),6,false);
    add(g,1,'comp',c.rf?matWireR:c.pwr?matWireP:matWireD);});
}

/* ---------- Requirements ---------- */
const REQS=[
  {id:'interval',pri:'Must',t:'The device shall send a reading every [interval] minutes.',in:{type:'number',step:1,min:1}},
  {id:'life',pri:'Must',t:'The device shall run at least [life] days between charges.',in:{type:'number',step:1,min:1}},
  {id:'resp',pri:'Must',t:'When the button is pressed, the device shall respond within [resp] s.',in:{type:'number',step:0.1,min:0.05}},
  {id:'cov',pri:'Must',t:'While in [cov] mobile coverage, the device shall deliver its readings.',in:{type:'select',opts:[['good','good'],['fair','fair'],['poor','poor']]}},
  {id:'acc',pri:'Should',t:'The device shall measure air temperature to ±[acc] °C.',in:{type:'number',step:0.1,min:0.1}},
  {id:'drop',pri:'Must',t:'If dropped from [drop] m, the enclosure shall not crack.',in:{type:'number',step:0.1,min:0.2}},
  {id:'touch',pri:'Must',t:'While charging, the outer surface shall stay below [touch] °C.',in:{type:'number',step:1,min:30}},
  {id:'chgt',pri:'Must',t:'While charging, the battery shall stay inside its 45 °C charging limit.'},
  {id:'chgh',pri:'Should',t:'The device shall charge fully within [chgh] h.',in:{type:'number',step:0.5,min:0.5}},
  {id:'ip',pri:'Should',t:'The enclosure shall resist [ip].',in:{type:'select',opts:[['none','light dust only'],['ip54','splashing water (IP54)'],['ip67','immersion (IP67)']]}}
];
function reqEval(s){
  if(!s)return[];const E=EL,R=S.req,out=[],has=t=>G.nodes.some(n=>n.type===t);
  const wk=JOBS.week.status==='done'?JOBS.week.res:null,ht=JOBS.heat3d.status==='done'?JOBS.heat3d.res:null;
  const add=(id,st,val,conf,fix,how)=>out.push({id,st,val,conf,fix,how});
  if(!E||!E.radio)add('interval','fail','no radio fitted','rule',{act:'func',type:'e_lte',on:true,label:'Add LTE'},'wiring path');
  else if(!E.radioOK)add('interval','fail','radio not fully wired','rule',{act:'wire',label:'Wire it'},'wiring path');
  else if(!E.thOK&&!has('e_imu')&&!has('e_gnss'))add('interval','warn','nothing to measure','rule',{act:'func',type:'e_th',on:true,label:'Add sensor'},'wiring path');
  else add('interval','pass','sensor, board, radio and antenna wired','rule',null,'wiring path and behaviour model');
  if(!E||!E.hasBat)add('life','na','no battery, runs from USB','rule',null,'');
  else{const v=wk?wk.p10:E.life,st=v>=R.life?'pass':v>=0.8*R.life?'warn':'fail';add('life',st,(wk?'P10 ':'about ')+fmt(v,1)+' days',wk?'estimated':'provisional',st!=='pass'&&S.batt!=='c18650'?{act:'batt',id:'c18650',label:'Bigger battery'}:null,wk?'week of use, 2,000 runs':'power budget');}
  if(!has('e_btn'))add('resp','fail','no button fitted','rule',{act:'func',type:'e_btn',on:true,label:'Add button'},'');
  else if(!E||!E.btnOK)add('resp','fail','button not wired','rule',{act:'wire',label:'Wire it'},'');
  else{const v=E.latency;add('resp',v<=R.resp?(E.ledOK?'pass':'warn'):'fail',fmt(v,2)+' s'+(E.ledOK?'':', nothing shows it'),'provisional',E.ledOK?null:{act:'func',type:'e_led',on:true,label:'Add light'},'behaviour model');}
  if(!E||!E.radio)add('cov','na','no radio','rule',null,'');else add('cov',E.margin>=6?'pass':E.margin>=0?'warn':'fail',fmt(E.margin,0)+' dB margin','provisional',null,'link budget');
  if(!has('e_th'))add('acc','fail','no temperature sensor','rule',{act:'func',type:'e_th',on:true,label:'Add sensor'},'');
  else{const v=ht?EB.e_th.acc+Math.max(0,ht.op.sens-S.simIn.ambient):E.sensErr;add('acc',v<=R.acc?'pass':v<=R.acc*1.5?'warn':'fail','±'+fmt(v,2)+' °C',ht?'estimated':'provisional',null,ht?'3D heat model':'self-heating estimate');}
  const dr=simRes&&simRes.drop;add('drop',dr?dr.st:'na',dr?dr.head:'n/a','provisional',null,'drop model');
  if(!E||!E.chgPath)['touch','chgt','chgh'].forEach(id=>add(id,'na','not charged in the product','rule',null,''));
  else{const hc=ht&&ht.ch,hot=hc?hc.skin:E.thCh.hot,bt=hc?hc.batt:E.thCh.Ti;
    add('touch',hot<=R.touch-2?'pass':hot<=R.touch?'warn':'fail',fmt(hot,1)+' °C',hc?'estimated':'provisional',null,hc?'3D heat model':'thermal model');
    add('chgt',bt<=42?'pass':bt<=45?'warn':'fail',fmt(bt,1)+' °C',hc?'estimated':'provisional',null,hc?'3D heat model':'thermal model');
    add('chgh',E.chgH<=R.chgh?'pass':'fail',fmt(E.chgH,1)+' h','provisional',null,'charge model');}
  if(R.ip==='none')add('ip','pass','no sealing needed','rule',null,'');
  else{const bad=s.slots&&s.slots.n&&!(ENV.ventMembrane&&R.ip==='ip54')?'open vent slots':(R.ip==='ip67'&&hasUSB()?'open USB-C port':null);add('ip',bad?'fail':'pass',bad||'no open apertures','rule',bad==='open vent slots'?{act:'role',role:'slotN',target:0,label:'Remove vents'}:null,'aperture rules');}
  return out.map(r=>{const pri=(S.reqPri&&S.reqPri[r.id])||REQS.find(q=>q.id===r.id).pri;return Object.assign(r,{pri,st:pri==='Off'?'na':r.st});});
}
const RSRC={};(CFG.requirements||[]).forEach(r=>{if(r.source)RSRC[r.id]=r.source;});
function renderScenarios(){const box=$('#scen');if(!box)return;const sc=Array.isArray(CFG.scenarios)?CFG.scenarios:[];box.hidden=!sc.length;
  const RN={interval:'reporting',life:'battery life',resp:'button response',cov:'coverage',acc:'accuracy',drop:'drop',touch:'touch temperature',chgt:'charging temperature',chgh:'charge time',ip:'sealing'};
  box.innerHTML=sc.length?`<div class="lbl">Situations it must handle</div><ul class="scen">${sc.map(x=>`<li><b>${esc(x.text)}</b>${Array.isArray(x.requires)&&x.requires.length?`<span>proved by ${x.requires.map(id=>esc(RN[id]||id)).join(', ')}</span>`:''}</li>`).join('')}</ul>`:'';}
function renderReqs(){
  const box=$('#reqList');if(box.contains(document.activeElement)&&document.activeElement.matches('input,select'))return;
  const lab=AUTHORED?{pass:'Not assessed',warn:'Not assessed',fail:'Not assessed',na:'n/a'}:{pass:'Met',warn:'At risk',fail:'Not met',na:'n/a'},cl={rule:'Rule',provisional:'Provisional',estimated:'Estimated'};
  box.innerHTML=REQ.map((r,i)=>{const q=REQS.find(x=>x.id===r.id),off=r.pri==='Off';
    const txt=esc(q.t).replace(/\[(\w+)\]/g,(m,k)=>q.in.type==='select'?`<select class="rqv" data-r="${k}" aria-label="${k}">${q.in.opts.map(([v,l])=>`<option value="${v}"${S.req[k]===v?' selected':''}>${esc(l)}</option>`).join('')}</select>`:`<input class="rqv" data-r="${k}" type="number" step="${q.in.step}" min="${q.in.min}" value="${S.req[k]}" aria-label="${k}">`);
    return `<li class="rq st-${r.st}${off?' off':''}"><div class="rqh"><span class="chip">${off?'Off':lab[r.st]}</span><select class="rpri" data-p="${r.id}" aria-label="Priority">${['Must','Should','Off'].map(p=>`<option${p===r.pri?' selected':''}>${p}</option>`).join('')}</select><span class="conf c-${r.conf} eng">${cl[r.conf]||''}</span>${r.fix&&!off&&(r.st==='fail'||r.st==='warn')?`<button class="fix" type="button" data-rfix="${i}">${esc(r.fix.label)}</button>`:''}</div><div class="ears">${txt}</div><div class="rv"><b>${esc(r.val)}</b>${r.how?` <span class="eng">· ${esc(r.how)}</span>`:''}${RSRC[r.id]?` <span class="eng">· source: ${esc(RSRC[r.id])}</span>`:''}</div></li>`;}).join('');
}
$('#reqList').addEventListener('change',e=>{const v=e.target.closest('.rqv'),p=e.target.closest('.rpri');
  if(v){const k=v.dataset.r,raw=v.value;S.req[k]=v.tagName==='SELECT'?raw:(isFinite(parseFloat(raw))?parseFloat(raw):S.req[k]);v.blur();schedule();}
  if(p){S.reqPri=S.reqPri||{};S.reqPri[p.dataset.p]=p.value;p.blur();schedule();}});
$('#reqList').addEventListener('click',e=>{const b=e.target.closest('[data-rfix]');if(b)applyFix(REQ[+b.dataset.rfix].fix);});

function renderElec(){
  const sum=$('#elecSum'),box=$('#elecList');
  if(!EL){sum.innerHTML='<p class="note">No electronics in this design. Tick a function above to add some.</p>';box.innerHTML='';$('#wireT').innerHTML='';return;}
  const f=EL.checks.filter(c=>c.st==='fail').length,w=EL.checks.filter(c=>c.st==='warn').length;
  sum.innerHTML=`<div class="bomsum"><span class="big" style="color:${f?'var(--fail)':w?'var(--warn)':'var(--pass)'}">${f?`${f} wiring or power problem${f>1?'s':''}`:w?'Works, with notes':'Everything connected'}</span><span class="vsub">${EL.hasBat?`Battery about ${fmt(EL.life,1)} days at ${fmt(EL.avg,2)} mA average. `:''}${EL.cables.length} cable${EL.cables.length===1?'':'s'}, ${EL.parts.length} parts placed inside.</span></div>`;
  box.innerHTML=EL.checks.map((c,i)=>`<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div></div>${c.fix&&c.st!=='pass'?`<button class="fix" type="button" data-efix="${i}">${esc(c.fix.label||'Fix')}</button>`:'<span></span>'}</li>`).join('');
  $('#wireT').innerHTML=EL.cables.length?`<thead><tr><th>Cable</th><th>Cores</th><th>Wire</th><th>Length</th><th>Connector</th></tr></thead><tbody>${EL.cables.map(c=>`<tr><td>${esc(c.label)}</td><td>${c.rf?'coax':c.cond}</td><td>${c.rf?'1.13 mm':c.awg+' AWG'}</td><td>${fmt(c.len,0)} mm${c.routed?'':' est.'}</td><td>${esc(c.fam)}</td></tr>`).join('')}</tbody>`:'';
  EL.nodes.forEach(n=>{const el=nodeEls[n.id]&&nodeEls[n.id].querySelector('.esum');if(el){const ni=EL.nodeInfo[n.id];el.innerHTML=ni?`<span style="color:${ni.st==='fail'?'var(--fail)':'var(--muted)'}">${esc(ni.txt)}</span>`:'';}});
}
$('#elecList').addEventListener('click',e=>{const b=e.target.closest('[data-efix]');if(b&&EL)applyFix(EL.checks[+b.dataset.efix].fix);});

/* ---------- Background validation ---------- */
const JOBS={route:{name:'Harness routing'},week:{name:'Week of use, 2,000 runs'},tol:{name:'Brownout, worst case'},heat3d:{name:'3D heat, use and charging'}};
Object.values(JOBS).forEach(j=>Object.assign(j,{status:'skip',prog:0,eta:0,hash:null,res:null}));
const RATE={route:0.0004,week:0.00012,tol:0.0006,heat3d:0.0000045},CACHE=new Map();let worker=null,curJob=null,jobTimer=0,lastJobRender=0;
const etaTxt=s=>s<1?'under a second':s<60?Math.round(s)+' s':s<3600?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h';
function jobParams(s){
  const E=EL,P={route:null,week:null,tol:null,heat3d:null};if(!s||!E||!s.t)return P;
  const tan=Math.tan(s.A*DEG),Hi=s.H-s.t,Fi=Math.max(s.F-s.t,0),bd=E.O.find(o=>o.id==='board'),plain=b=>({x0:b.x0,x1:b.x1,y0:b.y0,y1:b.y1,z0:b.z0,z1:b.z1});
  if(E.cables.length){const cell=Math.max(1.5,Math.cbrt(s.W*s.D*s.H/45000)),ap=E.parts.find(p=>p.type==='e_ant'),k=EB.e_ant.keep;
    P.route={sig:E.routeSig,W:s.W,D:s.D,H:s.H,R:s.R,t:s.t,tan,Hi,Fi,cell,obs:E.O.map(plain).concat(E.parts.map(p=>plain(p.box))),cables:E.cables.map(c=>({key:c.key,a:c.A,b:c.B,rf:c.rf})),
      keep:ap?{x0:ap.box.x0-k,x1:ap.box.x1+k,y0:ap.box.y0-k,y1:ap.box.y1+k,z0:ap.box.z0-k,z1:ap.box.z1+k}:null,board:bd?plain(bd):null};
    P.route.units=(s.W/cell)*(s.D/cell)*(s.H/cell)*E.cables.length;}
  if(E.hasBat&&E.avg>0){const iv=Math.max(60,S.req.interval*60);P.week={Ib:E.Ib,cap:E.cap,interval:iv,tMeas:2,tLoc:E.tLoc,txBase:E.txBase,cov:S.req.cov,forever:S.decisions.nosignal==='forever',runs:2000};P.week.units=2000*(7*86400/iv+28);}
  if(E.brown){P.tol={Ipk:E.Ipk,rint:battOf().rint,Rw:E.brown.Rw,vmin:E.brown.vmin,n:20000};P.tol.units=20000;}
  {const cell=Math.max(1.6,Math.min(3,s.t)),d=derive(s),pb=t=>{const p=E.parts.find(q=>q.type===t);return p?plain(p.box):null;};
   const main=bd?plain(bd):pb('e_lte')||pb('e_lora')||plain(mkBox(0,s.H/2,0,10,4,10)),scen=[{src:[Object.assign({},main,{P:E.avgW})]}];
   if(E.chgPath){const bb=E.O.find(o=>o.id==='batt');scen.push({src:[Object.assign({},pb('e_chg')||main,{P:(5-3.7)*E.chgI/1000}),Object.assign({},bb?plain(bb):main,{P:0.1}),Object.assign({},main,{P:E.avgW})]});}
   const thp=E.parts.find(p=>p.type==='e_th'),bb=E.O.find(o=>o.id==='batt');
   P.heat3d={W:s.W,D:s.D,H:s.H,R:s.R,F:s.F,t:s.t,tan,Hi,Fi,cell,kw:matOf().k,Ta:S.simIn.ambient,vent:{on:!!(s.slots&&s.slots.n),span:d.span,slotL:d.slotL},scen,sensor:thp?[thp.cx,thp.cy,thp.cz]:null,batt:bb?plain(bb):null,iters:1100};
   P.heat3d.units=(s.W/cell+2)*(s.D/cell+2)*(s.H/cell+1)*1100*scen.length;}
  return P;
}
function summ(k,r){if(!r)return'';if(k==='route'){const v=Object.values(r.cables),ok=v.filter(c=>c.ok);return`${ok.length} of ${v.length} cables routed, ${fmt(ok.reduce((a,c)=>a+c.len,0),0)} mm`;}
  if(k==='week')return`P10 ${fmt(r.p10,1)} days, median ${fmt(r.p50,1)} days`;if(k==='tol')return`${fmt(r.p*100,1)}% of conditions brown out`;
  if(k==='heat3d')return r.ch?`skin ${fmt(r.ch.skin,1)} °C and battery ${fmt(r.ch.batt,1)} °C while charging`:`skin ${fmt(r.op.skin,1)} °C in use`;return'';}
function killWorker(){if(worker){worker.terminate();worker=null;}curJob=null;}
function scheduleJobs(){
  clearTimeout(jobTimer);const P=jobParams(last&&last.shape);let any=false;
  for(const k in JOBS){const j=JOBS[k],p=P[k];
    if(!p){if(curJob===k)killWorker();Object.assign(j,{status:'skip',res:null,hash:null,prog:0});continue;}
    const h=fnv(JSON.stringify(p));if(h===j.hash&&j.status!=='error')continue;
    if(curJob===k)killWorker();j.hash=h;j.params=p;j.eta=Math.max(0.05,p.units*RATE[k]/1000);
    if(CACHE.has(k+h)){Object.assign(j,{res:CACHE.get(k+h),status:'done',prog:1});j.sum=summ(k,j.res);continue;}
    Object.assign(j,{status:'queued',prog:0,res:null});any=true;}
  if(any||Object.values(JOBS).some(j=>j.status==='queued'))jobTimer=setTimeout(runNext,1200);
}
function runNext(){if(curJob)return;const k=['route','week','tol','heat3d'].find(k=>JOBS[k].status==='queued');if(!k){renderJobs();return;}
  if(!worker){worker=new Worker(URL.createObjectURL(new Blob(['('+workerMain.toString()+')()'],{type:'text/javascript'})));worker.onmessage=onJobMsg;
    worker.onerror=e=>{if(curJob){JOBS[curJob].status='error';JOBS[curJob].err=e.message;}killWorker();renderJobs();setTimeout(runNext,0);};}
  curJob=k;const j=JOBS[k];j.status='running';j.t0=performance.now();worker.postMessage({k,hash:j.hash,p:j.params});renderJobs();}
function onJobMsg(e){const m=e.data,j=JOBS[m.k];if(!j||m.hash!==j.hash)return;
  if(m.type==='prog'){j.prog=m.prog;const now=performance.now();if(now-lastJobRender>150){lastJobRender=now;renderJobs();}return;}
  curJob=null;
  if(m.type==='err'){j.status='error';j.err=m.msg;renderJobs();setTimeout(runNext,0);return;}
  if(m.k==='route')m.res.sig=j.params.sig;j.res=m.res;j.status='done';j.prog=1;j.ms=m.ms;j.sum=summ(m.k,m.res);CACHE.set(m.k+j.hash,m.res);
  RATE[m.k]=RATE[m.k]*0.5+0.5*(m.ms/Math.max(1,j.params.units));schedule();setTimeout(runNext,0);}
function l3Jobs(){const s=last&&last.shape;if(!s)return[];const t=s.t||2,V=partVolume(s),out=[],el=V/Math.pow(t/2,3);
  out.push({name:'Drop impact, explicit FEA',what:`${Math.round(el/1000)}k elements, 4 corner drops`,solver:'explicit dynamics solver',min:2+el/50000*24});
  const cfd=(s.W+40)*(s.D+40)*(s.H+40)/3.375;out.push({name:'Airflow and heat, CFD',what:`${Math.round(cfd/1000)}k cells, use and charging`,solver:'OpenFOAM',min:3+cfd/20000});
  if(EL&&EL.radio){const mhz=EB[EL.radio.type].mhz,lam=300000/mhz,c=lam/20,cells=(s.W+lam/2)*(s.D+lam/2)*(s.H+lam/2)/Math.pow(c,3);out.push({name:'Antenna in the enclosure, full-wave EM',what:`${Math.round(cells/1000)}k cells at ${mhz} MHz`,solver:'openEMS',min:5+cells/2500});}
  if(EL){const rails=EL.nodes.filter(n=>EB[n.type].conv).length+1;out.push({name:'Power path transient, SPICE',what:`${rails} rails, transmit burst`,solver:'ngspice',min:2+rails*1.5});}
  if(S.proc==='im'){const me=(areaRR(s.W/2,s.D/2,s.R)*2+perim(s)*s.H*2)/(t*t);out.push({name:'Mould filling, 3D flow',what:`${Math.round(me/1000)}k elements`,solver:'mould-flow solver',min:5+me/8000});}
  return out;}
function physTests(){const T=[['Drop test, 5 samples','1 day'],['Thermal chamber, use and charging','2 days']];
  if(S.req.ip!=='none')T.push(['Ingress test to IEC 60529','2 days']);if(EL&&EL.radio)T.push(['Radio range and certification pre-scan','2 to 4 weeks']);
  if(EL&&EL.hasBat)T.push(['Battery safety to IEC 62133','3 to 4 weeks at a lab']);if(EL&&(EL.radio||hasUSB()))T.push(['EMC pre-scan','1 day']);return T;}
function renderJobs(){
  const box=$('#valBody');if(!box)return;const L2=['route','week','tol','heat3d'].map(k=>JOBS[k]),act=L2.filter(j=>j.status==='running'||j.status==='queued');
  const left=act.reduce((a,j)=>a+j.eta*(1-(j.prog||0)),0);
  const msg=j=>j.status==='queued'?'Queued, about '+etaTxt(j.eta):j.status==='running'?`${Math.round((j.prog||0)*100)}%, about ${etaTxt(j.eta*(1-(j.prog||0)))} left`:j.status==='done'?`${j.sum}${j.ms!=null?` · took ${etaTxt(j.ms/1000)}`:''}`:j.status==='error'?'Failed: '+(j.err||'unknown'):'Not needed for this design';
  box.innerHTML=`<div class="vsub">${act.length?`${act.length} check${act.length>1?'s':''} running, about ${etaTxt(left)} left. Results replace the quick estimates as they finish.`:'Background checks are up to date with this design.'}</div>
    <ul class="jobs">${L2.map(j=>`<li class="job js-${j.status}"><div class="jt"><b>${esc(j.name)}</b><span class="lv">in browser</span></div><div class="jbar"><i style="width:${Math.round((j.status==='done'?1:j.prog||0)*100)}%"></i></div><div class="jm">${esc(msg(j))}</div></li>`).join('')}</ul>
    <div class="eng"><div class="lbl" style="margin:10px 0 6px">Cloud solvers, estimated</div><ul class="jobs">${l3Jobs().map(j=>`<li class="job"><div class="jt"><b>${esc(j.name)}</b><span class="lv">about ${etaTxt(j.min*60)}</span></div><div class="jm">${esc(j.what)} · ${esc(j.solver)} · needs a cloud backend</div></li>`).join('')}</ul>
    <div class="lbl" style="margin:10px 0 6px">Physical tests</div><ul class="jobs">${physTests().map(t=>`<li class="job"><div class="jt"><b>${esc(t[0])}</b><span class="lv">${esc(t[1])}</span></div></li>`).join('')}</ul></div>`;
}

/* ---------- Gates and the validation pack ---------- */
function gateState0(){
  const must=REQ.filter(r=>r.pri==='Must'),dfmF=last&&last.rep?last.rep.rules.filter(r=>r.st==='fail').length:0,elF=EL?EL.checks.filter(c=>c.st==='fail').length:0;
  const bomF=last&&last.bom?last.bom.checks.filter(c=>c.st==='fail'&&!['reqs','elec'].includes(c.id)).length:0;
  const PRR=partReadiness(),prDef=PRR.filter(p=>p.st.def!=='pass').length,prNot=PRR.filter(p=>!p.ready).length;
  const g1=REQS.every(q=>!q.in||(S.req[q.id]!=null&&S.req[q.id]!==''))&&!prDef;const dfaF=(DFA?DFA.C.filter(c=>c.st==='fail').length:0)+REFC.filter(c=>c.st==='fail').length+AFC.filter(c=>c.st==='fail').length+mfgFails();const g2=g1&&!dfmF&&!elF&&!bomF&&!dfaF&&!prNot;
  const open=must.filter(r=>r.st==='fail'||r.st==='warn'),mOpen=ctxMustOpen(),g3=g2&&!open.length&&!mOpen;
  const running=Object.values(JOBS).some(j=>j.status==='queued'||j.status==='running')||MCR.status==='running',prov=must.filter(r=>r.st!=='na'&&r.conf==='provisional'&&['life','touch','chgt','acc'].includes(r.id));
  const g4=g3&&!running&&!prov.length;
  return[{n:1,name:'Intent',ok:g1,msg:g1?'Every requirement and part is defined':prDef?`${prDef} part${prDef===1?'':'s'} not defined`:'Fill in every requirement'},
    {n:2,name:'Concept',ok:g2,msg:g2?'Every part ready, no blocking problems':prNot?`${prNot} part${prNot===1?'':'s'} not ready${dfmF+elF+bomF+dfaF?`, ${dfmF+elF+bomF+dfaF} blocking problem${dfmF+elF+bomF+dfaF===1?'':'s'}`:''}`:`${dfmF+elF+bomF+dfaF} blocking problem${dfmF+elF+bomF+dfaF===1?'':'s'} to fix`},
    {n:3,name:'Simulation',ok:g3,msg:g3?'Every Must requirement is met':!g2?'Waiting on earlier gates':`${open.length+mOpen} Must requirement${open.length+mOpen===1?'':'s'} not met yet`},
    {n:4,name:'Validation',ok:g4,msg:g4?'Ready for engineering validation':running?'Background checks still running':'Waiting on earlier gates'}];
}
function renderGates(){
  renderPartReady();const G4=gateState(),cur=G4.find(g=>!g.ok);
  $('#gates').innerHTML=G4.map(g=>`<div class="gate${g.ok?' ok':''}${cur&&cur.n===g.n?' cur':''}"><span class="gnum">Gate ${g.n}</span><b>${g.name}</b><small>${esc(g.msg)}</small></div>`).join('');
  $('#gateMsg').textContent=cur?`Next: ${cur.msg.charAt(0).toLowerCase()+cur.msg.slice(1)}.`:'All four gates pass. Copy the validation pack to hand over to engineering.';
  $('#cpPack').disabled=!(G4[2].ok);
}
$('#cpPack').addEventListener('click',()=>{if(!last||!last.shape)return;const s=last.shape,L=[];
  L.push('FORMGRAPH VALIDATION PACK',`Product: ${CP.name||'Unnamed product'} (${CP.id||'default'})`,`Engine: ${ENGINE}`,'MetaForge, metaforge.uk','',`Fingerprint: sha256:${(last.bom&&last.bom.fp)||''}`,`Revision: ${(S.revs[S.revs.length-1]||{}).rev||'unreleased'}`,`Process: ${PROC[S.proc].name}, ${matOf().name}, ${finishOf().name}`,`Envelope: ${fmt(s.W,1)} x ${fmt(s.D,1)} x ${fmt(s.H,1)} mm, wall ${fmt(s.t||0,2)} mm`,'');
  L.push('GATES');gateState().forEach(g=>L.push(`  ${g.n}. ${g.name}: ${g.ok?'pass':'open'} (${g.msg})`));L.push('','REQUIREMENTS');
  REQ.forEach(r=>{const q=REQS.find(x=>x.id===r.id);L.push(`  [${r.pri}] ${q.t.replace(/\[(\w+)\]/g,(m,k)=>{const o=q.in.opts&&q.in.opts.find(o=>o[0]===S.req[k]);return o?o[1]:S.req[k];})}`,`      ${r.st.toUpperCase()}: ${r.val} (${r.conf}${r.how?', '+r.how:''})`);});
  if(EL){L.push('','ELECTRONICS AND WIRING CHECKS');EL.checks.forEach(c=>L.push(`  [${c.st.toUpperCase()}] ${c.label}: ${c.val} (${c.lim})`));
    L.push('','WIRING TABLE');EL.cables.forEach(c=>L.push(`  ${c.label}: ${c.rf?'coax 1.13 mm':c.cond+' cores, '+c.awg+' AWG'}, ${fmt(c.len,0)} mm${c.routed?'':' (estimated)'}, ${c.fam}`));}
  {const ps=progSummary();if(ps){L.push('','PROGRAMME',`  Phase: ${ps.phase}`);ps.exit.forEach(c=>L.push(`  [${c.status.toUpperCase()}] ${c.criterion}${c.detail?': '+c.detail:''}`));
    L.push(`  Domains: ${ps.domains.filter(d=>d.status==='pass').length} pass, ${ps.domains.filter(d=>d.status==='warn').length} at risk, ${ps.domains.filter(d=>d.status==='fail').length} fail, ${ps.domains.filter(d=>['open','plan'].includes(d.status)).length} open of ${ps.domains.length}`);
    ps.risks.slice(0,5).forEach(r=>L.push(`  Risk RPN ${r.rpn}: ${r.mode}${r.mitigation?' (mitigation: '+r.mitigation+')':''}`));}}
  if(ENVM){L.push('','ENVIRONMENT',`  Operates ${ENV.opMin} to ${ENV.opMax} °C, stored ${ENV.storeMin} to ${ENV.storeMax} °C, sun ${ENV.sun}, ${ENV.altitude_m} m, ${ENV.vibration}`);ENVM.C.forEach(c=>L.push(`  [${c.st.toUpperCase()}] ${c.label}: ${c.val} (${c.lim})`));}
  L.push('','BACKGROUND CHECKS (IN BROWSER)');Object.values(JOBS).forEach(j=>L.push(`  ${j.name}: ${j.status}${j.sum?', '+j.sum:''}`));
  L.push('','CLOUD SOLVER RUNS TO SCHEDULE');l3Jobs().forEach(j=>L.push(`  ${j.name}: ${j.what}, ${j.solver}, about ${etaTxt(j.min*60)}`));
  L.push('','PHYSICAL TESTS');physTests().forEach(t=>L.push(`  ${t[0]}: ${t[1]}`));
  if(last.bom){L.push('','BILL OF MATERIALS');last.bom.lines.forEach((l,i)=>L.push(`  ${i+1}. ${l.item}, ${l.spec}, qty ${l.qty}${l.qtyUnit?' '+l.qtyUnit:''}, ${l.mpn||'part number to nominate'} [${l.status}]`));}
  L.push('','Models up to gate 3 are early screening: rules of thumb and simplified physics. Cloud solvers and physical tests above are still required.');
  const st={product:CP.id||'default',engine:ENGINE,fingerprint:(last.bom&&last.bom.fp)||null,revision:(S.revs[S.revs.length-1]||{}).rev||null,
    process:{id:S.proc,material:S.mat,finish:S.finish,qty:S.qty,fixings:S.fast},power:{board:S.board,battery:S.batt},
    form:{W:+fmt(s.W,2),D:+fmt(s.D,2),H:+fmt(s.H,2),R:+fmt(s.R,2),F:+fmt(s.F,2),A:+fmt(s.A,2),t:s.t?+fmt(s.t,2):null,features:FEATS},
    functions:G.nodes.filter(n=>EB[n.type]&&!EB[n.type].fixed).map(n=>n.type),requirements:REQ.map(r=>({id:r.id,pri:r.pri,status:r.st,value:r.val,confidence:r.conf})),
    gates:gateState().map(g=>({n:g.n,name:g.name,ok:g.ok})),dfm:last.rep?last.rep.rules.filter(r=>r.st!=='pass').map(r=>({check:r.label,value:r.val,limit:r.lim,status:r.st})):[],
    electronics:EL?EL.checks.filter(c=>c.st!=='pass').map(c=>({check:c.label,value:c.val,limit:c.lim,status:c.st})):[],
    bom:last.bom?last.bom.lines.map(l=>({item:l.item,qty:l.qty,unit:l.qtyUnit||'pcs',part:l.mpn||null,status:l.status})):[],
    jobs:Object.fromEntries(Object.entries(JOBS).map(([k,j])=>[k,{status:j.status,summary:j.sum||null}])),decisions:S.decisions,requirementValues:S.req,programme:progSummary()};
  L.push('','STATE JSON',JSON.stringify(st));
  copy(L.join('\n'),'Validation pack');});

/* ---------- Virtual device ---------- */
dev={open:false,play:false,speed:3600,t:0,state:'sleep',soc:1,usb:false,until:0,next:0,log:[],hist:[],cov:'fair',tries:0,flash:0,queued:false,low:false,full:false,lastTick:0};
const dLog=m=>{const d=Math.floor(dev.t/86400)+1,h=Math.floor(dev.t%86400/3600),mi=Math.floor(dev.t%3600/60);dev.log.unshift(`D${d} ${String(h).padStart(2,'0')}:${String(mi).padStart(2,'0')}  ${m}`);dev.log.length=Math.min(dev.log.length,6);};
function devReset(){Object.assign(dev,{t:0,state:'sleep',soc:1,usb:false,until:0,next:Math.max(60,S.req.interval*60),log:[],hist:[[0,1]],cov:S.req.cov,tries:0,flash:0,queued:false,low:false,full:false});dLog('Switched on, sleeping');}
function devStartMeasure(src){dev.state='measure';dev.until=dev.t+2;dev.tries=0;dLog(src+': measuring');}
function devStartTx(){if(!EL||!EL.radioOK){dLog('No radio wired, reading kept');dev.state='sleep';dev.next=dev.t+Math.max(60,S.req.interval*60);return;}
  dev.state='transmit';dev.until=dev.t+EL.txBase*({good:1,fair:1.5,poor:3}[dev.cov]||1.5);}
function devTransition(){
  const iv=Math.max(60,S.req.interval*60);
  if(dev.state==='sleep'){if(dev.low){dev.next=dev.t+iv;return;}devStartMeasure('Timer');return;}
  if(dev.state==='measure'){if(EL&&EL.tLoc){dev.state='locate';dev.until=dev.t+EL.tLoc;}else devStartTx();return;}
  if(dev.state==='locate'){devStartTx();return;}
  if(dev.state==='transmit'){const pf={good:.02,fair:.08,poor:.25}[dev.cov]||.08;
    if(Math.random()<pf){dev.tries++;const lim=S.decisions.nosignal==='forever'?20:3;if(dev.tries<lim){dLog('No signal, retrying');dev.until=dev.t+EL.txBase;return;}dLog('No signal, reading kept for later');}
    else dLog('Reading sent');
    dev.state='sleep';dev.next=dev.t+iv;if(dev.queued){dev.queued=false;devStartMeasure('Queued press');}}
}
function devStep(dt){
  let rem=dt,guard=0;
  while(rem>1e-6&&guard++<20000){const end=dev.state==='sleep'?dev.next:dev.until,step=Math.max(0,Math.min(rem,end-dev.t));
    const I=EL&&EL.hasBat?(EL.Ib[dev.state]||0):0;
    if(EL&&EL.cap){dev.soc-=I*step/3600/EL.cap;if(dev.usb&&EL.chgPath)dev.soc+=EL.chgI*step/3600/EL.cap;dev.soc=clamp(dev.soc,0,1);}
    dev.t+=step;rem-=step;
    if(dev.soc>=1&&dev.usb&&!dev.full){dev.full=true;dLog('Fully charged');}
    if(dev.soc<0.1&&!dev.low&&EL&&EL.hasBat){dev.low=true;dLog('Battery low, readings paused');}
    if(dev.low&&dev.soc>=0.2){dev.low=false;dLog('Battery recovered, readings resume');}
    if(dev.t>=end-1e-9)devTransition();
    if(!dev.hist.length||dev.t-dev.hist[dev.hist.length-1][0]>=600){dev.hist.push([dev.t,dev.soc]);if(dev.hist.length>1100)dev.hist.shift();}}
}
function devTick(){
  if(!dev||!dev.open)return;const now=performance.now(),dt=dev.lastTick?Math.min(0.1,(now-dev.lastTick)/1000):0;dev.lastTick=now;
  if(dev.play&&EL)devStep(dt*dev.speed);dev.flash=Math.max(0,dev.flash-dt);
  devFx(now);
  if(now-(dev.lastRender||0)>120){dev.lastRender=now;renderDev();}
}
function renderDev(){
  if(!dev.open)return;const names={sleep:'Asleep',measure:'Measuring',locate:'Finding location',transmit:'Sending'};
  $('#dState').textContent=(dev.low?'Battery low, ':'')+(names[dev.state]||dev.state)+(dev.usb?(EL&&EL.chgPath?', charging':', USB connected'):'');
  const d=Math.floor(dev.t/86400)+1,h=Math.floor(dev.t%86400/3600),mi=Math.floor(dev.t%3600/60);$('#dState').dataset.state=dev.low?'low':(dev.usb&&EL&&EL.chgPath&&dev.state==='sleep'&&dev.soc<1?'charge':dev.state);$('#dClock').textContent=`Day ${d}, ${String(h).padStart(2,'0')}:${String(mi).padStart(2,'0')}`;
  const pc=Math.round(dev.soc*100);$('#dSoc').style.width=pc+'%';$('#dSoc').style.background=dev.soc<0.1?'var(--fail)':dev.soc<0.3?'var(--warn)':'var(--pass)';
  $('#dSocT').textContent=EL&&EL.hasBat?`Battery ${pc}%`:'No battery, USB powered';$('#dLife').textContent=EL&&EL.life?`about ${fmt(EL.life,1)} days per charge`:'';
  $('#dPlay').textContent=dev.play?'❚❚':'▶';$('#dPlay').setAttribute('aria-label',dev.play?'Pause':'Play');$('#dUsb').setAttribute('aria-pressed',String(dev.usb));$('#dUsb').querySelector('span').textContent=dev.usb?'Unplug':'Plug in';$('#dUsb').disabled=!hasUSB();$('#dBtn').disabled=!G.nodes.some(n=>n.type==='e_btn');
  $('#dLog').innerHTML=dev.log.map(l=>`<li>${esc(l)}</li>`).join('');
  const dec=EL?EL.DEC:[];$('#dDec').innerHTML=dec.length?`${dec.map(x=>`<label class="dec"><span>${esc(x.q)}</span><select data-dec="${x.id}"><option value="">Choose one</option>${x.opts.map(([v,l])=>`<option value="${v}"${S.decisions[x.id]===v?' selected':''}>${esc(l)}</option>`).join('')}</select></label>`).join('')}`:'';
  {const od=dec.filter(x=>!S.decisions[x.id]).length;$('#dDecBox').hidden=!dec.length;$('#dDecSum').textContent=od?`${od} decision${od>1?'s':''} to make`:'Behaviour decided';$('#dMiniSoc').textContent=EL&&EL.hasBat?Math.round(dev.soc*100)+'%':'';$('#dLast').textContent=dev.log[0]?dev.log[0].replace(/^D\d+ \d\d:\d\d\s+/,''):'';}
  const cv=$('#dChart'),w=cv.clientWidth||260,hh=56,dpr=Math.min(2,window.devicePixelRatio||1);if(cv.width!==Math.round(w*dpr)){cv.width=Math.round(w*dpr);cv.height=hh*dpr;}
  const g=cv.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,w,hh);g.strokeStyle=css('--line');g.lineWidth=1;g.beginPath();g.moveTo(0,hh-0.5);g.lineTo(w,hh-0.5);g.stroke();
  const span=7*86400,t0=Math.max(0,dev.t-span),pts=dev.hist.filter(p=>p[0]>=t0);
  if(pts.length>1){g.beginPath();pts.forEach(([t,s],i)=>{const x=(t-t0)/span*w,y=4+(1-s)*(hh-8);i?g.lineTo(x,y):g.moveTo(x,y);});g.strokeStyle=css('--accent');g.lineWidth=1.6;g.stroke();
    g.lineTo((pts[pts.length-1][0]-t0)/span*w,hh);g.lineTo((pts[0][0]-t0)/span*w,hh);g.closePath();g.globalAlpha=0.12;g.fillStyle=css('--accent');g.fill();g.globalAlpha=1;}
  g.fillStyle=css('--muted');g.font='10px '+css('--f-mono');g.fillText('last 7 days',4,11);
}
function openDevice(){if(!dev.hist.length)devReset();dev.open=true;dev.lastTick=0;$('#devp').hidden=false;$('#devBtn').setAttribute('aria-pressed','true');devSegs();renderDev();}
function closeDevice(){dev.open=false;dev.play=false;$('#devp').hidden=true;$('#devBtn').setAttribute('aria-pressed','false');matLed.emissiveIntensity=0;devFxOff();}
$('#devBtn').addEventListener('click',()=>dev.open?closeDevice():openDevice());
$('#devClose').addEventListener('click',closeDevice);
$('#dPlay').addEventListener('click',()=>{dev.play=!dev.play;dev.lastTick=0;renderDev();});
$('#dReset').addEventListener('click',()=>{devReset();renderDev();});
$('#dSpeedSeg').addEventListener('click',e=>{const b=e.target.closest('[data-speed]');if(b){dev.speed=+b.dataset.speed;devSegs();}});
$('#dCovSeg').addEventListener('click',e=>{const b=e.target.closest('[data-cov]');if(b){dev.cov=b.dataset.cov;dLog('Signal now '+dev.cov);devSegs();renderDev();}});
$('#dBtn').addEventListener('click',()=>{dev.flash=0.35;dev.pressAt=performance.now();if(!EL||!EL.btnOK){dLog('Button pressed, not wired to the board');renderDev();return;}
  if(dev.state==='sleep'){devStartMeasure('Button');}else if(dev.state==='transmit'&&S.decisions.btnbusy==='ignore')dLog('Busy, press ignored');else{dev.queued=true;dLog('Busy, press queued');}renderDev();});
$('#dUsb').addEventListener('click',()=>{dev.usb=!dev.usb;dev.full=false;if(dev.usb){dLog(EL&&EL.chgPath?(dev.state==='transmit'&&S.decisions.usbbusy!=='now'?'USB-C in, charging after this send':'USB-C in, charging'):'USB-C in, no charger wired');}else dLog('USB-C out');renderDev();});
$('#dDec').addEventListener('change',e=>{const s2=e.target.closest('[data-dec]');if(!s2)return;S.decisions[s2.dataset.dec]=s2.value||undefined;if(!s2.value)delete S.decisions[s2.dataset.dec];schedule();});

/* ---------- Worker for level 2 checks ---------- */
function workerMain(){
  const fz=(y,H,F)=>{if(F<=0||y<=H-F)return 0;const u=y-(H-F);return F-Math.sqrt(Math.max(0,F*F-u*u));};
  const inRR=(x,z,a,b,r)=>{x=Math.abs(x);z=Math.abs(z);if(x>a||z>b)return false;if(x>a-r&&z>b-r)return Math.hypot(x-(a-r),z-(b-r))<=r;return true;};
  let send=()=>{};
  function route(p){
    const{W,D,H,R,t,tan,Hi,Fi,cell,obs,cables,keep,board}=p,nx=Math.ceil(W/cell)+1,ny=Math.ceil(H/cell)+1,nz=Math.ceil(D/cell)+1,N=nx*ny*nz;
    const X=i=>-W/2+i*cell,Y=j=>j*cell,Z=k=>-D/2+k*cell,id=(i,j,k)=>(j*nz+k)*nx+i,ins=y=>t+Math.max(0,y)*tan+fz(y,Hi,Fi);
    const free=new Uint8Array(N),pen=new Uint8Array(N);
    for(let j=0;j<ny;j++){const y=Y(j),ok=y>=2&&y<=Hi-0.6,a=W/2-ins(y)-0.8,b=D/2-ins(y)-0.8,r=Math.max(0,R-ins(y)-0.8);
      for(let k=0;k<nz;k++)for(let i=0;i<nx;i++){const x=X(i),z=Z(k);let f=ok&&inRR(x,z,a,b,r);
        if(f)for(const o of obs){if(x>o.x0-0.6&&x<o.x1+0.6&&y>o.y0-0.6&&y<o.y1+0.6&&z>o.z0-0.6&&z<o.z1+0.6){f=false;break;}}
        const c=id(i,j,k);free[c]=f?1:0;if(keep&&x>keep.x0&&x<keep.x1&&y>keep.y0&&y<keep.y1&&z>keep.z0&&z<keep.z1)pen[c]=1;}}
    const cellOf=q=>[Math.round((q[0]+W/2)/cell),Math.round(q[1]/cell),Math.round((q[2]+D/2)/cell)];
    const near=q=>{const[ci,cj,ck]=cellOf(q),R2=Math.ceil(30/cell);let best=-1,bd=1e9;
      for(let dj=-R2;dj<=R2;dj++)for(let dk=-R2;dk<=R2;dk++)for(let di=-R2;di<=R2;di++){const i=ci+di,j=cj+dj,k=ck+dk;if(i<0||j<0||k<0||i>=nx||j>=ny||k>=nz)continue;const c=id(i,j,k);if(!free[c])continue;const d=di*di+dj*dj+dk*dk;if(d<bd){bd=d;best=c;}}return best;};
    const nb=[1,-1,nx,-nx,nx*nz,-nx*nz],out={};let done=0;
    for(const cb of cables){
      const s0=near(cb.a),g0=near(cb.b);if(s0<0||g0<0){out[cb.key]={ok:false};send({type:'prog',prog:++done/cables.length});continue;}
      const gs=new Float32Array(N).fill(Infinity),from=new Int32Array(N).fill(-1),closed=new Uint8Array(N),gi=g0%nx,gk=Math.floor(g0/nx)%nz,gj=Math.floor(g0/(nx*nz));
      const hf=c=>Math.abs(c%nx-gi)+Math.abs(Math.floor(c/(nx*nz))-gj)+Math.abs(Math.floor(c/nx)%nz-gk);
      const hp=[],hv=[];
      const push=(c,f)=>{hp.push(c);hv.push(f);let n=hp.length-1;while(n>0){const pa=(n-1)>>1;if(hv[pa]<=hv[n])break;[hp[pa],hp[n]]=[hp[n],hp[pa]];[hv[pa],hv[n]]=[hv[n],hv[pa]];n=pa;}};
      const pop=()=>{const c=hp[0],lc=hp.pop(),lv=hv.pop();if(hp.length){hp[0]=lc;hv[0]=lv;let n=0;for(;;){const l=2*n+1,r=l+1;let m=n;if(l<hp.length&&hv[l]<hv[m])m=l;if(r<hp.length&&hv[r]<hv[m])m=r;if(m===n)break;[hp[m],hp[n]]=[hp[n],hp[m]];[hv[m],hv[n]]=[hv[n],hv[m]];n=m;}}return c;};
      gs[s0]=0;push(s0,hf(s0));let found=false,steps=0;
      while(hp.length){const c=pop();if(closed[c])continue;closed[c]=1;if(c===g0){found=true;break;}if(++steps>600000)break;
        const i=c%nx,k=Math.floor(c/nx)%nz,j=Math.floor(c/(nx*nz));
        for(let d=0;d<6;d++){if((d===0&&i===nx-1)||(d===1&&i===0)||(d===2&&k===nz-1)||(d===3&&k===0)||(d===4&&j===ny-1)||(d===5&&j===0))continue;
          const n=c+nb[d];if(!free[n]||closed[n])continue;let cost=1+(pen[n]&&!cb.rf?4:0);const pf=from[c];if(pf>=0&&c-pf!==nb[d])cost+=0.4;
          const ng=gs[c]+cost;if(ng<gs[n]){gs[n]=ng;from[n]=c;push(n,ng+hf(n));}}}
      if(!found){out[cb.key]={ok:false};send({type:'prog',prog:++done/cables.length});continue;}
      const path=[];for(let c=g0;c>=0;c=from[c]){path.push(c);if(c===s0)break;}path.reverse();
      const P=path.map(c=>[X(c%nx),Y(Math.floor(c/(nx*nz))),Z(Math.floor(c/nx)%nz)]);
      const clear=(A,B)=>{const Ln=Math.hypot(B[0]-A[0],B[1]-A[1],B[2]-A[2]),n=Math.max(1,Math.ceil(Ln/(cell*0.5)));for(let s=1;s<n;s++){const u=s/n,[i,j,k]=cellOf([A[0]+(B[0]-A[0])*u,A[1]+(B[1]-A[1])*u,A[2]+(B[2]-A[2])*u]);if(i<0||j<0||k<0||i>=nx||j>=ny||k>=nz||!free[id(i,j,k)])return false;}return true;};
      const sm=[P[0]];let a=0;while(a<P.length-1){let b=P.length-1;while(b>a+1&&!clear(P[a],P[b]))b--;sm.push(P[b]);a=b;}
      const pts=[cb.a].concat(sm,[cb.b]);let len=0,minR=999;
      for(let n=1;n<pts.length;n++)len+=Math.hypot(pts[n][0]-pts[n-1][0],pts[n][1]-pts[n-1][1],pts[n][2]-pts[n-1][2]);
      for(let n=1;n<pts.length-1;n++){const u=[0,1,2].map(q=>pts[n][q]-pts[n-1][q]),w=[0,1,2].map(q=>pts[n+1][q]-pts[n][q]),lu=Math.hypot(...u),lw=Math.hypot(...w);if(lu<1e-6||lw<1e-6)continue;
        const th=Math.acos(Math.max(-1,Math.min(1,(u[0]*w[0]+u[1]*w[1]+u[2]*w[2])/(lu*lw))));if(th<0.05)continue;minR=Math.min(minR,Math.min(lu,lw)/Math.tan(th/2));}
      let bdist=999;if(board&&cb.rf)for(const q of sm){const dx=Math.max(board.x0-q[0],0,q[0]-board.x1),dy=Math.max(board.y0-q[1],0,q[1]-board.y1),dz=Math.max(board.z0-q[2],0,q[2]-board.z1);bdist=Math.min(bdist,Math.hypot(dx,dy,dz));}
      out[cb.key]={ok:true,pts,len,minR,boardDist:bdist};send({type:'prog',prog:++done/cables.length});}
    return{cables:out};
  }
  function week(p){
    const{Ib,cap,interval,tMeas,tLoc,txBase,cov,forever,runs}=p,pf={good:.02,fair:.08,poor:.25}[cov]||.08,sg={good:.2,fair:.35,poor:.5}[cov]||.35,cf={good:1,fair:1.5,poor:3}[cov]||1.5;
    const T=7*86400,cyc=Math.floor(T/interval)+28,lives=[],ga=()=>Math.sqrt(-2*Math.log(Math.random()+1e-12))*Math.cos(2*Math.PI*Math.random());
    for(let r=0;r<runs;r++){let q=0,busy=0;
      for(let c=0;c<cyc;c++){let tx=0,tries=0;do{tx+=txBase*cf*Math.exp(sg*ga()-sg*sg/2);tries++;}while(Math.random()<pf&&tries<(forever?20:3));busy+=tMeas+tLoc+tx;q+=Ib.measure*tMeas+Ib.locate*tLoc+Ib.transmit*tx;}
      q+=Ib.sleep*Math.max(0,T-busy);lives.push(cap*0.8/(q/T)/24);if(r%100===0)send({type:'prog',prog:r/runs});}
    lives.sort((a,b)=>a-b);return{p10:lives[Math.floor(runs*0.1)],p50:lives[Math.floor(runs*0.5)],min:lives[0]};
  }
  function tol(p){const{Ipk,rint,Rw,vmin,n}=p;let bad=0;
    for(let i=0;i<n;i++){const T=-10+Math.random()*50,cold=1+Math.max(0,20-T)/15,v=3.45+Math.random()*0.75,I=Ipk/1000*(0.9+Math.random()*0.25),r=rint*(0.8+Math.random()*0.4)*cold+Rw*(0.9+Math.random()*0.2);if(v-I*r<vmin)bad++;if(i%4000===0)send({type:'prog',prog:i/n});}
    return{p:bad/n};}
  function heat3d(p){
    const{W,D,H,R,F,t,tan,Hi,Fi,cell,kw,Ta,vent,scen,sensor,batt,iters}=p,nx=Math.ceil(W/cell)+2,ny=Math.ceil(H/cell)+1,nz=Math.ceil(D/cell)+2,N=nx*ny*nz;
    const X=i=>-W/2-cell+(i+0.5)*cell,Y=j=>(j+0.5)*cell,Z=k=>-D/2-cell+(k+0.5)*cell,id=(i,j,k)=>(j*nz+k)*nx+i,typ=new Uint8Array(N),K=new Float32Array(N),hm=cell/1000;
    for(let j=0;j<ny;j++){const y=Y(j),oi=y*tan+fz(y,H,F),ii=t+y*tan+fz(y,Hi,Fi);
      for(let k=0;k<nz;k++)for(let i=0;i<nx;i++){const x=X(i),z=Z(k),c=id(i,j,k);if(!(y<=H&&inRR(x,z,W/2-oi,D/2-oi,Math.max(0,R-oi))))continue;
        if(y<Hi&&y>1.5&&inRR(x,z,W/2-ii,D/2-ii,Math.max(0,R-ii))){typ[c]=2;K[c]=0.9;}else if(vent.on&&y>Hi&&Math.abs(x)<vent.span/2&&Math.abs(z)<vent.slotL/2){typ[c]=3;K[c]=0.9;}else{typ[c]=1;K[c]=kw;}}}
    const nbr=[[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]],res=[];
    for(let sc=0;sc<scen.length;sc++){
      const Q=new Float32Array(N);
      for(const s of scen[sc].src){const cs=[];for(let j=0;j<ny;j++)for(let k=0;k<nz;k++)for(let i=0;i<nx;i++){const c=id(i,j,k);if(typ[c]!==2)continue;const x=X(i),y=Y(j),z=Z(k);if(x>=s.x0-2*cell&&x<=s.x1+2*cell&&y>=s.y0-2*cell&&y<=s.y1+2*cell&&z>=s.z0-2*cell&&z<=s.z1+2*cell)cs.push(c);}cs.forEach(c=>Q[c]+=s.P/cs.length);}
      const T=new Float32Array(N).fill(Ta);
      for(let it=0;it<iters;it++){
        for(let j=0;j<ny;j++)for(let k=0;k<nz;k++)for(let i=0;i<nx;i++){const c=id(i,j,k),tc=typ[c];if(!tc)continue;let sg=0,st=0;
          for(let q=0;q<6;q++){const a=i+nbr[q][0],b=j+nbr[q][1],e=k+nbr[q][2];let g,tn;
            if(a<0||b<0||e<0||a>=nx||b>=ny||e>=nz||!typ[id(a,b,e)]){g=(tc===3?40:(b<0?6:12))*hm*hm;tn=Ta;}else{const n=id(a,b,e);g=2*K[c]*K[n]/(K[c]+K[n])*hm;tn=T[n];}sg+=g;st+=g*tn;}
          T[c]+=1.6*((st+Q[c])/sg-T[c]);}
        if(it%50===0)send({type:'prog',prog:(sc+it/iters)/scen.length});}
      let skin=Ta,bm=Ta,sens=Ta,air=0,na=0;
      for(let j=0;j<ny;j++)for(let k=0;k<nz;k++)for(let i=0;i<nx;i++){const c=id(i,j,k);if(!typ[c])continue;
        if(typ[c]===1){let s=false;for(let q=0;q<6;q++){const a=i+nbr[q][0],b=j+nbr[q][1],e=k+nbr[q][2];if(a<0||b<0||e<0||a>=nx||b>=ny||e>=nz||!typ[id(a,b,e)]){s=true;break;}}if(s&&T[c]>skin)skin=T[c];}
        if(typ[c]===2){air+=T[c];na++;const x=X(i),y=Y(j),z=Z(k);if(batt&&x>=batt.x0-cell&&x<=batt.x1+cell&&y>=batt.y0-cell&&y<=batt.y1+cell&&z>=batt.z0-cell&&z<=batt.z1+cell&&T[c]>bm)bm=T[c];}}
      if(sensor){const i=Math.max(0,Math.min(nx-1,Math.round((sensor[0]+W/2+cell)/cell-0.5))),j=Math.max(0,Math.min(ny-1,Math.round(sensor[1]/cell-0.5))),k=Math.max(0,Math.min(nz-1,Math.round((sensor[2]+D/2+cell)/cell-0.5)));sens=T[id(i,j,k)];}
      res.push({skin,batt:Math.max(bm,na?air/na:Ta),sens,air:na?air/na:Ta});}
    return{op:res[0],ch:res[1]||null};
  }
  self.onmessage=e=>{const{k,hash,p}=e.data;send=m=>self.postMessage(Object.assign({k,hash},m));
    try{const t0=performance.now(),res={route,week,tol,heat3d}[k](p);send({type:'done',res,ms:performance.now()-t0});}catch(err){send({type:'err',msg:String(err&&err.message||err)});}};
}


/* ================= Programme: every domain, phase and record from the design guide ================= */
const REG=CFG._registry||null,PCFG=CFG.programme||{};
const PHASES=REG?REG.phases:[];
function progState(){S.prog=S.prog||{};const p=S.prog;
  ['confirmed','tests','risks','dom','std','ifz','ledger','creq','docs','miles','flows','sec'].forEach(k=>{if(!p[k]||typeof p[k]!=='object')p[k]={};});
  if(!Array.isArray(p.loops))p.loops=[];if(!p.phase)p.phase=PCFG.phase||'define';return p;}
const PST={pass:'Pass',warn:'At risk',fail:'Fail',open:'Open',plan:'Planned',na:'n/a'};
const worstOf=a=>{const o={fail:0,warn:1,open:2,plan:3,pass:4,na:5};return a.reduce((w,s)=>o[s]<o[w]?s:w,'na');};
const num=s=>{const m=String(s).match(/-?\d+(\.\d+)?/g);return m?m.map(Number):[];};
const today=()=>new Date();const addW=(d,w)=>new Date(d.getTime()+w*7*86400000);const dstr=d=>d.toISOString().slice(0,10);

function pflags(){
  const P=PCFG,m=(P.markets||['UK','EU']).map(x=>String(x).toUpperCase()),has=t=>G.nodes.some(n=>n.type===t),radio=has('e_lte')||has('e_lora')||!!(boardOf().elec&&boardOf().elec.wireless);
  const sw=P.software||{},fw=P.firmware||{},sec=P.sector||'ict',ipOn=S.req.ip!=='none'&&(S.reqPri.ip||'Should')!=='Off';
  const f={always:true,battery:battOf().mass>0,radio,lte:has('e_lte'),lora:has('e_lora'),gnss:has('e_gnss'),button:has('e_btn'),light:has('e_led'),sound:has('e_buzz'),
    sensor:has('e_th')||has('e_imu')||has('e_gnss'),usb:hasUSB(),charging:!!(EL&&EL.chgPath),connected:radio||!!sw.connected,sealed:ipOn,
    outdoor:ENV.outdoor||!!(P.environment&&P.environment.outdoor)||ipOn,salt:!!ENV.salt,sun:ENV.sun!=='none',uv:(ENV.uvHours||0)>0||(ENV.outdoor&&ENV.sun!=='none'),highalt:(ENV.altitude_m||0)>2000,cold:ENV.opMin<0,condensing:!!ENV.condensing,uk:m.includes('UK'),eu:m.includes('EU'),us:m.includes('US'),ukeu:m.includes('UK')||m.includes('EU'),
    ict:!['luminaire','household','medical'].includes(sec),luminaire:sec==='luminaire',household:sec==='household',medical:sec==='medical',automotive:sec==='automotive',aircraft:sec==='aircraft',
    industrial:P.segment==='industrial'||sec==='industrial',consumer:P.segment==='consumer',sector:['medical','automotive','aircraft'].includes(sec),safety:!!P.safetyFunction,
    mechanism:!!P.mechanism,control:!!P.control,screen:!!(sw.display||sw.app),app:!!sw.app,mains:!!P.mains,handheld:!!(P.human&&P.human.handheld)};
  f.ota=f.connected&&fw.ota!==false;return f;}
const applies=(when,F)=>!when||!when.length||when.every(k=>F[k]);

/* ---------- calculations ---------- */
function budgets(s,F){
  const B=PCFG.budgets||{},out=[];const bomMass=last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0):0;
  const bomCost=last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0):0;
  const add=(id,name,val,unit,target,lowerIsBetter=true,dp=0)=>{let st='open',lim='no target set';
    if(target!=null){const r=val/target;st=lowerIsBetter?(r<=0.9?'pass':r<=1?'warn':'fail'):(r>=1.1?'pass':r>=1?'warn':'fail');lim=(lowerIsBetter?'max ':'min ')+fmt(target,dp)+' '+unit;}
    out.push({id,name,val:fmt(val,dp)+' '+unit,raw:val,st,lim});};
  add('mass','Mass',bomMass,'g',B.mass_g);
  if(EL&&EL.hasBat)add('power','Average power',EL.avg*3.7,'mW',B.power_mW,true,1);else if(EL)add('power','Average power',EL.avgW*1000,'mW',B.power_mW,true,0);
  add('cost','BOM cost per set',bomCost,'GBP',B.cost_gbp,true,2);
  if(EL&&s.t){let lo=0,hi=40;for(let i=0;i<30;i++){const m=(lo+hi)/2;if(thermalAt(s,m,S.simIn.ambient).hot<=S.req.touch)lo=m;else hi=m;}
    out.push({id:'heat',name:'Heat load',val:fmt(EL.heatW,2)+' W',raw:EL.heatW,st:EL.heatW<=lo*0.8?'pass':EL.heatW<=lo?'warn':'fail',lim:`${fmt(lo,2)} W keeps the skin under ${S.req.touch} °C`});}
  if(F.sensor&&G.nodes.some(n=>n.type==='e_th')){const self=Math.max(0,(EL&&EL.sensErr!=null?EL.sensErr:0.2)-0.2),parts=[['sensor',0.2],['self-heating',self],['ADC and quantisation',0.05],['calibration residual',0.1]];
    const rss=Math.sqrt(parts.reduce((a,p)=>a+p[1]*p[1],0));out.push({id:'error',name:'Temperature error (RSS)',val:'±'+fmt(rss,2)+' °C',raw:rss,st:rss<=S.req.acc?'pass':rss<=S.req.acc*1.3?'warn':'fail',lim:'±'+S.req.acc+' °C required',parts});}
  return out;}
function tolerance(s){
  const W=s.W,p=S.proc,tol=p==='im'?0.08+0.0015*W:p==='cnc'?0.05:0.15+0.002*W,sh=p==='im'?0.0015*W/3:0,c0=0.25,LSL=0.05,USL=0.6;
  const sig=Math.sqrt(2*(tol/3)**2+sh*sh)/2;let lo=0,hiN=0,sum=0,sq=0;const n=20000;
  for(let i=0;i<n;i++){const g=c0+sig*Math.sqrt(-2*Math.log(Math.random()+1e-12))*Math.cos(2*Math.PI*Math.random());sum+=g;sq+=g*g;if(g<LSL)lo++;else if(g>USL)hiN++;}
  const mu=sum/n,sd=Math.sqrt(sq/n-mu*mu),cpk=Math.min(USL-mu,mu-LSL)/(3*sd);
  return{tol,sig:sd,mu,cpk,tight:lo/n,loose:hiN/n,st:cpk>=1.33?'pass':cpk>=1?'warn':'fail',c0,LSL,USL};}
function modal(s){
  if(!s.t)return null;const M=matOf(),t=s.t/1000,a=(s.W-2*s.t)/1000,b=(s.D-2*s.t)/1000,nu=M.metal?0.33:0.35,E=M.E*1e6,rho=M.density*1000;
  const Dp=E*t**3/(12*(1-nu*nu)),fss=Math.PI/2*(1/(a*a)+1/(b*b))*Math.sqrt(Dp/(rho*t));let k=1;
  if(s.ribs){const tm=s.t,ha=s.W/2-tm,hb=s.D/2-tm,A=2*ha,B=2*hb,sh=Math.min(A,B),ratio=Math.max(A,B)/sh,beta=0.0611+0.012*Math.min(1,(ratio-1)/0.6),kP=M.E*tm**3/(beta*sh*sh);
    const rt=s.ribs.t,rh=s.ribs.h,bf=Math.min(16*tm,sh/2),A1=bf*tm,y1=tm/2,A2=rt*rh,y2=tm+rh/2,yc=(A1*y1+A2*y2)/(A1+A2),Ir=bf*tm**3/12+A1*(yc-y1)**2+rt*rh**3/12+A2*(y2-yc)**2;
    const kR=192*M.E*Ir/A**3+192*M.E*Ir/B**3;k=Math.sqrt((kP+kR)/kP);}
  const f=1.8*fss*k;return{f,st:f>=150?'pass':f>=80?'warn':'fail',ribs:k};}
function mtbf(F){
  const fit=REG?REG.fit:{},rows=[];if(EL)EL.nodes.forEach(n=>{if(n.type==='e_batt'){if(F.battery)rows.push([battOf().name,fit.battery]);return;}if(n.type==='e_board'&&!boardOf().elec)return;
    const b=EB[n.type];rows.push([n.type==='e_board'?boardOf().name:b.name,fit[b.cat]||20]);});
  if(EL)rows.push([`${EL.cables.length} cables and connectors`,(fit.cable||8)*EL.cables.length]);rows.push(['Enclosure and fixings',fit.enclosure||2]);
  const Ti=EL&&EL.thOp?EL.thOp.Ti:25,piT=Math.exp(0.7/8.617e-5*(1/313-1/(Ti+273))),lam=rows.reduce((a,r)=>a+r[1],0)*piT,mt=1e9/Math.max(lam,1e-6);
  const L=(PCFG.lifecycle&&PCFG.lifecycle.targetLifeYears)||5,R=Math.exp(-L*8760/mt);return{rows,piT,lam,mtbf:mt,years:mt/8760,L,R,st:R>=0.9?'pass':R>=0.8?'warn':'fail'};}
function derating(){
  const rows=[];if(!EL)return{rows,st:'na'};
  EL.checks.forEach(c=>{if(/^i/.test(c.id)||c.id==='bpk'||/^amp/.test(c.id)){const a=num(c.val),b=num(c.lim);if(!a.length||!b.length)return;let used=a[0],rated=b[b.length-1];if(/^amp/.test(c.id)){used=a[a.length-1];}
    const u=used/rated;rows.push({name:c.label,u,st:u<=0.8?'pass':u<=1?'warn':'fail'});}});
  return{rows,st:rows.length?worstOf(rows.map(r=>r.st)):'na'};}
function junctions(){
  const rows=[];if(!EL)return{rows,st:'na'};const Ta=EL.thCh?EL.thCh.Ti:EL.thOp.Ti,th={compute:60,comm:30,power:50};
  EL.nodes.forEach(n=>{const b=EB[n.type],q=EL.sp[n.id];if(!q||q.missing||!th[b.cat])return;let P=0;
    if(n.type==='e_board'&&q.e)P=q.e.active*q.vnom/1000;else if(b.conv==='chg')P=EL.chgPath?(5-3.7)*b.chg/1000:0.02;else if(b.conv==='boost')P=0.12;else if(q.cur)P=Math.max(...Object.values(q.cur))*3.7/1000;
    const Tj=Ta+P*th[b.cat];rows.push({name:n.type==='e_board'?boardOf().name:b.name,P,theta:th[b.cat],Tj,st:Tj<=75?'pass':Tj<=85?'warn':'fail'});});
  return{rows,Ta,st:rows.length?worstOf(rows.map(r=>r.st)):'na'};}
function traceWidths(){
  const rows=[];if(!EL)return rows;
  EL.checks.forEach(c=>{if(!/^i/.test(c.id)&&c.id!=='bpk')return;const a=num(c.val);if(!a.length)return;const I=a[0]/1000,A=Math.pow(I/(0.048*Math.pow(10,0.44)),1/0.725),w=A/1.378*0.0254;
    rows.push({name:c.label.replace(' current',''),I,w,st:w<=3?'pass':'warn'});});return rows;}
function creepage(F){return F.mains?{v:'230 V mains',cl:3.0,cr:6.4,note:'Reinforced insulation, pollution degree 2, material group III. Hipot and insulation tests required, Class II construction.',st:'open'}
  :{v:'up to 5.25 V (SELV, ES1)',cl:0.15,cr:0.15,note:'No safety insulation needed below ES1 limits; spacing is functional only.',st:'pass'};}
function dfa(){
  const L=last&&last.bom?last.bom.lines:[];let parts=0,fast=0,conn=0,cab=0;
  L.forEach(l=>{if(l.qtyUnit)return;if(/fixings|screw/i.test(l.item+l.spec))fast+=l.qty;else if(/housing|U\.FL/i.test(l.item))conn+=l.qty;else if(l.id==='harness')cab+=EL?EL.cables.length:0;else parts+=l.qty;});
  const tot=parts+fast;const time=parts*4+fast*8+conn*3+cab*10;const nmin=Math.max(3,L.filter(l=>!l.qtyUnit&&!/fixings|housing|U\.FL|harness|Wire/i.test(l.item)).length);
  const eff=3*nmin/Math.max(1,time);return{parts:tot,fast,conn,cab,time,nmin,eff,st:eff>=0.25?'pass':eff>=0.12?'warn':'fail',
    tips:[fast>4?`${fast} screws: consider snap fits or fewer, larger fixings`:null,cab>3?`${cab} cables: consider board-to-board connectors`:null].filter(Boolean)};}
function packaging(s){
  const pd=(PCFG.packaging&&PCFG.packaging.padding_mm)||15,bw=s.W+2*pd+6,bd=s.D+2*pd+6,bh=s.H+2*pd+6,area=2*(bw*bd+bw*bh+bd*bh)/1e6;
  const mass=(last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0):0)+area*450+20,vol=bw*bd*bh/1e3/5000;
  return{pd,dims:[bw,bd,bh],mass,vol,charge:Math.max(mass/1000,vol),st:'pass'};}
function carbon(s,F){
  if(!REG)return null;const M=matOf(),f=REG.carbon_kg_per_kg,c=REG.carbon_fixed,rows=[];const V=partVolume(s)/1000,shell=V*M.density/1000,lid=areaRR(s.W/2-0.3,s.D/2-0.3,s.R)*(s.t||2)/1e6*M.density;
  rows.push([M.name+' shell and lid',(shell+lid)*(f[S.mat]||4)]);if(EL){if(boardOf().elec)rows.push(['Main board',c.board]);rows.push(['Modules',EL.nodes.filter(n=>!['e_board','e_batt'].includes(n.type)).length*c.module]);
    if(F.battery)rows.push(['Battery',battOf().cap*3.7/1000*c.battery_per_wh*10]);rows.push(['Harness',EL.cables.reduce((a,x)=>a+x.len*x.cond,0)/1000*c.harness_per_m]);}
  rows.push(['Assembly and moulding energy',c.assembly]);const tot=rows.reduce((a,r)=>a+r[1],0),tgt=PCFG.sustainability&&PCFG.sustainability.carbon_kg;
  return{rows,tot,st:tgt?(tot<=tgt?'pass':tot<=tgt*1.15?'warn':'fail'):'plan',tgt};}
function repair(F){
  const f=fastOf(),pts=[];let sc=0;const add=(ok,txt,w)=>{if(ok)sc+=w;pts.push([ok,txt,w]);};
  add(!!(last&&last.shape&&last.shape.bosses&&last.shape.bosses.n),'Lid held by screws, opens without damage',3);
  if(F.battery)add(!!(EL&&EL.cables.some(c=>c.a==='batt'||c.b==='batt')),'Battery on a connector, replaceable',3);add(!!(EL&&EL.cables.length),'Standard connectors inside',2);
  add(!!(PCFG.lifecycle&&PCFG.lifecycle.spares),'Spare parts listed',1);add(F.connected,'Remote diagnostics',1);
  const max=pts.reduce((a,p)=>a+p[2],0);const score=max?sc/max*10:0;return{score,pts,insert:!!f.insert,st:score>=7?'pass':score>=4?'warn':'fail'};}
function recycle(){const f=fastOf(),M=matOf(),code=M.metal?(M.name.startsWith('Alu')?'Aluminium, recyclable':'Metal'):'>'+M.name.split(',')[0].replace('PC/ABS','PC+ABS').replace(/\s/g,'')+(M.ul94&&M.ul94.startsWith('V')?'-FR':'')+'<';
  const notes=[];if(f.insert)notes.push('Brass heat-set inserts must be removed before plastic recycling');if(G.nodes.some(n=>n.type==='e_ant'))notes.push('Adhesive antenna: specify a peelable adhesive');
  return{code,mono:true,notes,st:notes.length?'warn':'pass'};}
function economics(){
  const B=PCFG.business||{},bom=last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0):0,d=dfa(),labour=d.time/3600*30,pack=0.8,cost=bom+labour+pack;
  if(!B.price)return{bom,labour,pack,cost,st:'open'};const margin=(B.price-cost)/B.price,tg=B.margin||0.4;return{bom,labour,pack,cost,price:B.price,margin,target:tg,st:margin>=tg?'pass':margin>=tg*0.75?'warn':'fail'};}
function progSchedule(){
  const pw=(REG&&REG.phase_weeks)||{},start=PCFG.start?new Date(PCFG.start):today(),rows=[];let d=start;
  ['define','concept','evt','dvt','pvt'].forEach(k=>{const e=addW(d,pw[k]||4);rows.push({k,start:d,end:e});d=e;});
  const dvt=rows.find(r=>r.k==='dvt'),lw=(REG&&REG.lead_weeks)||{},items=[];
  if(S.proc==='im')items.push({name:'Order injection-mould tooling',by:addW(dvt.start,-(lw.im||8)),why:`tooling takes about ${lw.im||8} weeks and DVT needs production tooling`});
  items.push({name:'Book certification test slots',by:addW(dvt.start,-(lw.cert_slot||12)),why:'test houses book months ahead'});
  if(EL&&EL.hasBat)items.push({name:'Order battery cells for DVT',by:addW(dvt.start,-(lw.battery||8)),why:`cells take about ${lw.battery||8} weeks`});
  items.push({name:'Order PCBs and assembly for EVT',by:addW(rows[2].start,-((lw.pcb||3)+(lw.pcba||4))),why:'fabrication then assembly'});
  const now=today();items.forEach(i=>{const late=i.by<now;i.st=late?'warn':'plan';});
  return{rows,items,st:items.some(i=>i.st==='warn')?'warn':'plan'};}
function supply(){
  const lw=(REG&&REG.lead_weeks)||{},L=last&&last.bom?last.bom.lines:[],rows=L.map(l=>{let w=lw.fixings||2;if(l.id==='shell'||l.id==='lid')w=lw[S.proc]||4;else if(l.id==='board')w=lw.pcba||4;else if(l.id==='batt')w=lw.battery||8;
    else if(/^e_/.test(l.id))w=lw.module||6;else if(/^cn_/.test(l.id))w=lw.connector||4;else if(l.id==='mat')w=2;const ss=!!(S.prog.std['ss_'+l.id]||(PCFG.supply&&PCFG.supply.secondSources||[]).includes(l.id));return{id:l.id,item:l.item,w,ss};});
  const longest=rows.reduce((a,r)=>Math.max(a,r.w),0),single=rows.filter(r=>!r.ss&&r.w>=6);return{rows,longest,single,st:single.length?'warn':'pass'};}
function production(){
  const s=last&&last.shape;if(!s)return null;const t=s.t||2,cyc=S.proc==='im'?12+2.2*t*t:S.proc==='cnc'?60*(12+3):900;const yieldT=(PCFG.production&&PCFG.production.yield)||0.97;
  const eol=30+(EL?EL.nodes.length*5:0),prog=20,rate=3600/Math.max(cyc,eol+prog);return{cyc,eol,prog,yieldT,rate,steps:['PCB fabrication','SMT assembly and reflow','AOI and X-ray where needed','Firmware programming',`End-of-line test, about ${Math.round(eol)} s`,'Final assembly into the enclosure','Pack and label'],st:'plan'};}
function cpkPlan(s,tol){const p=S.proc,t=s.t||2;return[
  {dim:'Lid-to-shell gap',nom:fmt(tol.c0,2)+' mm',tolv:`${tol.LSL} to ${tol.USL} mm`,cpk:tol.cpk,st:tol.st},
  {dim:'Nominal wall',nom:fmt(t,2)+' mm',tolv:'±'+fmt(p==='im'?0.1:p==='cnc'?0.05:0.2,2)+' mm',cpk:null,st:'plan'},
  {dim:'Boss hole Ø',nom:s.bosses?fmt(s.bosses.d,2)+' mm':'n/a',tolv:'±0.05 mm',cpk:null,st:s.bosses?'plan':'na'},
  {dim:'Overall width',nom:fmt(s.W,1)+' mm',tolv:'±'+fmt(tol.tol,2)+' mm',cpk:null,st:'plan'}];}
function form(s){const p=s.D/s.W,gold=Math.abs(p-0.618)<=0.08,sq=Math.abs(p-1)<=0.05,stance=s.H/Math.min(s.W,s.D),edge=s.F/Math.max(s.R,0.1);
  const items=[{name:'Proportion',val:fmt(p,3),note:gold?'close to the golden ratio':sq?'square plan, reads as a puck':'free proportion',st:gold||sq?'pass':'open'},
    {name:'Stance (height to plan)',val:fmt(stance,2),note:stance<0.2?'very flat, reads as a plate':stance>0.6?'tall, reads as a tower':'balanced',st:stance>=0.2&&stance<=0.6?'pass':'warn'},
    {name:'Edge language (top fillet to corner radius)',val:fmt(edge,2),note:edge<0.25?'sharp top against soft corners':edge>0.8?'very soft, pebble-like':'consistent soft edges',st:edge>=0.25&&edge<=0.8?'pass':'warn'}];
  return{items,st:worstOf(items.map(i=>i.st))};}
function ergo(s,F){const A=(REG&&REG.anthropometrics)||{},H=PCFG.human||{},items=[],mass=last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0):0;
  if(F.handheld){const g=Math.min(s.D,s.H*2);items.push({name:'Grip span',val:fmt(Math.min(s.D,s.W),0)+' mm',note:`comfortable ${A.grip_span_mm[0]} to ${A.grip_span_mm[1]} mm`,st:Math.min(s.D,s.W)<=A.grip_span_mm[1]?'pass':Math.min(s.D,s.W)<=A.grip_span_mm[1]*1.15?'warn':'fail'});
    items.push({name:'One-hand mass',val:fmt(mass,0)+' g',note:`under ${A.one_hand_mass_g} g`,st:mass<=A.one_hand_mass_g?'pass':mass<=A.one_hand_mass_g*2?'warn':'fail'});}
  if(F.button){const need=H.gloves?A.button_gloved_mm:A.button_min_mm;items.push({name:'Button size',val:'6 mm cap',note:`min ${need} mm${H.gloves?' with gloves':''}`,st:6>=need?'pass':'warn'});
    items.push({name:'Button force',val:'to specify',note:`${A.button_force_N[0]} to ${A.button_force_N[1]} N`,st:'plan'});}
  if(F.light){const wall=H.mount==='wall';items.push({name:'Sightline to the status light',val:'light on the top face',note:wall?'wall mounted: the top face is hard to see; move the light to the front':'visible from above and the side',st:wall?'warn':'pass'});}
  items.push({name:'Edges and pinch points',val:`corner radius ${fmt(s.R,1)} mm`,note:'no sharp edges under 0.5 mm radius; no pinch points',st:s.R>=0.5?'pass':'fail'});
  return{items,st:worstOf(items.map(i=>i.st))};}
function feedback(F){const ch=[F.light?'light':null,F.sound?'sound':null].filter(Boolean),ev=[];
  if(F.button)ev.push('button press');if(F.radio)ev.push('sending');if(F.charging)ev.push('charging');if(F.battery)ev.push('low battery');ev.push('fault');
  const rows=ev.map(e=>({e,ch:ch.length?ch.join(' and '):'none',st:ch.length?'pass':'warn'}));return{rows,st:worstOf(rows.map(r=>r.st))};}
function fwPlan(F){const fw=PCFG.firmware||{},b=boardOf(),os=fw.os||(b.elec&&b.elec.vin[0]>4?'embedded Linux':'bare metal with a scheduler');
  const tasks=[],ints=[],timers=[],queues=[];if(F.sensor){tasks.push('Sensor task: wake, measure, filter');ints.push('Sensor data-ready (if used)');}
  if(F.radio){tasks.push('Radio task: attach, send, retry, sleep');queues.push('Sensor to radio queue');}if(F.button||F.light||F.sound){tasks.push('User interface task: button, light, sound');}
  if(F.button)ints.push('Button press (debounced)');if(F.battery)tasks.push('Power task: battery level, charging state, low-battery shutdown');
  timers.push(`Real-time clock wake every ${S.req.interval} min`);if(F.radio)timers.push('Retry back-off timer');
  const i2c=EL?EL.nodes.filter(n=>EB[n.type]&&EB[n.type].addr).length:0;const mutex=i2c>1?['I2C bus mutex shared by '+i2c+' devices']:[];
  const modes=['Idle (sleep)','Active (measure and send)','Fault (safe state, error shown)'];const fails=['Watchdog reset after a lockup','Low-battery shutdown to protect the cell'];if(F.charging)fails.push('Charge inhibit outside 0 to 45 °C cell temperature'+((PCFG.firmware||{}).chargeInhibit?'':' (not set yet)'));
  if(S.decisions.nosignal)fails.push(S.decisions.nosignal==='store'?'No signal: keep readings and send later':'No signal: keep retrying');
  const boot=['Boot ROM','Bootloader verifies the image signature','Application','Fallback to the previous image if verification fails'];
  const upd=F.ota?['Download in the background','Verify signature and integrity','Swap on reboot','Roll back if the new image does not check in']:['No update path'];
  const proto=F.lte?'MQTT over TLS on LTE-M or NB-IoT':F.lora?'LoRaWAN class A':'none';
  const fields=[['timestamp',4],F.sensor?['temperature',2]:null,F.sensor?['humidity',2]:null,F.gnss?['latitude and longitude',8]:null,F.battery?['battery level',1]:null,['status flags',1]].filter(Boolean);
  return{os,tasks,ints,timers,queues,mutex,watchdog:fw.watchdog!==false,modes,fails,boot,upd,proto,fields,bytes:fields.reduce((a,f)=>a+f[1],0),config:['reporting interval','alert thresholds','network credentials (unique per device)']};}
function security(F){const fw=PCFG.firmware||{},items=[
  {id:'unique',name:'No universal default passwords; unique credentials per device',ok:!!fw.uniqueCredentials,psti:true},
  {id:'vuln',name:'Published vulnerability disclosure policy',ok:!!fw.vulnPolicy,psti:true},
  {id:'period',name:'Stated minimum security update period',ok:!!fw.supportYears,psti:true},
  {id:'secureboot',name:'Secure boot with signed images',ok:!!fw.secureBoot},
  {id:'signedota',name:'Signed and verified updates',ok:F.ota&&fw.signedUpdates!==false},
  {id:'tls',name:'Encryption in transit',ok:!!(fw.encryption||F.lte)},
  {id:'keys',name:'Keys in secure storage',ok:!!fw.secureStorage}];
  const pstiApplies=F.connected&&F.consumer&&F.uk,miss=items.filter(i=>!i.ok),pstiMiss=items.filter(i=>i.psti&&!i.ok);
  return{items,pstiApplies,st:!F.connected?'na':(pstiApplies&&pstiMiss.length)?'fail':miss.length?'warn':'pass'};}
function interfacesReg(s,F){
  const R=[],add=(id,kind,a,b,what)=>R.push({id,kind,a,b,what,frozen:!!S.prog.ifz[id]});const L=EL&&last?layout(s):null;
  if(L&&L.board)add('m_board','Mechanical','Main board','Shell standoffs',`4 holes on ${L.board.holes[0]} x ${L.board.holes[1]} mm, ${fastOf().pcbHole} mm pilot`);
  if(s.bosses&&s.bosses.n)add('m_lid','Mechanical','Lid','Shell',`${s.bosses.n} x ${fastOf().name}, ${fmt(0.25,2)} mm nominal gap per side`);
  if(L&&L.batt)add('m_batt','Mechanical','Battery','Holder',`${L.batt.name}, clip or bracket under the board`);
  if(F.button)add('m_btn','Mechanical','Button cap','Top opening','7 mm opening, 6 mm cap');if(F.usb)add('m_usb','Mechanical','USB-C receptacle','Side wall','9.2 x 3.6 mm opening on a flat wall');
  if(EL)EL.cables.forEach(c=>add('e_'+c.key,'Electrical',c.label.split(' to ')[0],c.label.split(' to ')[1]||'',`${c.rf?'coax':c.cond+' cores, '+c.awg+' AWG'}, ${c.fam}`));
  if(EL)EL.nodes.forEach(n=>{const b=EB[n.type];if(!b.addr&&!(b.ins||[]).some(([k,t])=>['uart','spi'].includes(t)))return;const t=(b.ins.find(([k,t])=>['i2c','uart','spi'].includes(t))||[])[1];if(t)add('d_'+n.id,'Data','Main board',b.name,`${t.toUpperCase()}${b.addr?' at '+b.addr:''}`);});
  if(EL)add('t_main','Thermal','Electronics','Enclosure wall',`${fmt(EL.heatW,2)} W worst case into the inside air`);if(F.charging)add('t_chg','Thermal','Charger','Battery','charging heat next to the cell');if(ENVM&&ENVM.dSun>0)add('t_sun','Thermal','Sun','Enclosure skin',`about ${fmt(ENVM.dSun,0)} °C extra on a ${(CMF.find(c=>c.id===S.color)||CMF[0]).name.toLowerCase()} body`);
  if(F.radio)add('r_ant','RF','Antenna','Enclosure',`${matOf().name}, ${EB.e_ant.keep} mm clear zone`);
  add('u_fault','User','Firmware fault state',F.light?'Status light':F.sound?'Buzzer':'None',F.light||F.sound?'fault shown to the user':'no way to show a fault');
  (PCFG.interfaces||[]).forEach((x,i)=>add('c_'+i,x.kind||'Mechanical',x.a,x.b,x.what||''));return R;}
function tradeoffs(){const RT=PCFG.rationale||{},out=[],C=last&&last.shape?costs(last.shape):null;
  if(C)out.push({id:'process',q:'Which production process?',opts:Object.keys(C).map(k=>`${PROC[k].short} ${gbp(C[k].at(S.qty))}`),chosen:PROC[S.proc].short,reason:RT.process||(Object.keys(C).every(k=>C[k].at(S.qty)>=C[S.proc].at(S.qty))?`lowest unit cost at ${S.qty.toLocaleString('en-GB')} units`:'')});
  out.push({id:'material',q:'Which material grade?',opts:MATS[S.proc].map(m=>m.name),chosen:matOf().name,reason:RT.material||''});
  out.push({id:'board',q:'Which main board?',opts:Object.values(BOARDS).filter(b=>b.elec).map(b=>b.name),chosen:boardOf().name,reason:RT.board||''});
  out.push({id:'battery',q:'Which battery?',opts:Object.values(BATTS).map(b=>b.name),chosen:battOf().name,reason:RT.battery||''});
  if(EL&&EL.radio)out.push({id:'radio',q:'Which network?',opts:['LTE-M / NB-IoT','LoRa'],chosen:EB[EL.radio.type].name,reason:RT.radio||''});
  out.push({id:'size',q:'What size?',opts:['driven by contents and DFM limits'],chosen:last&&last.shape?`${fmt(last.shape.W,0)} x ${fmt(last.shape.D,0)} x ${fmt(last.shape.H,0)} mm`:'',reason:RT.size||''});
  (PCFG.tradeoffs||[]).forEach((t,i)=>out.push({id:'c'+i,q:t.question,opts:t.options||[],chosen:t.chosen,reason:t.reason||'',criteria:t.criteria}));
  out.forEach(t=>t.st=t.reason?'pass':'open');return out;}
function testPlan(F){
  const out=[];if(!REG)return out;const active=REQ.filter(r=>r.st!=='na'&&r.pri!=='Off').map(r=>r.id);
  REG.tests.forEach(t=>{if(!applies(t.when,F))return;const reqs=t.reqs.includes('*')?active.slice():t.reqs.filter(r=>active.includes(r));const p=S.prog.tests[t.id]||{};
    out.push(Object.assign({},t,{reqs,st:p.st||'planned',ev:p.ev||''}));});
  (PCFG.customRequirements||[]).forEach(c=>{const p=S.prog.tests['tc_'+c.id]||{};out.push({id:'tc_'+c.id,name:`Verify: ${c.text}`,type:c.verify||'test',phase:c.phase||'dvt',level:c.level||'system',reqs:['c:'+c.id],st:p.st||'planned',ev:p.ev||''});});
  return out;}
function standards(F){if(!REG)return[];return REG.standards.filter(x=>applies(x.when,F)).map(x=>{const p=S.prog.std[x.id]||{};return Object.assign({},x,{st:p.st||'planned',house:p.house||''});});}
function fmea(F,ctx){
  const R=[],get=id=>REQ.find(r=>r.id===id),Ofrom=st=>({pass:2,warn:5,fail:8,na:1}[st]||4),add=(id,mode,effect,cause,S0,O0,D0,mit,links)=>{const o=S.prog.risks[id]||{};
    const Sv=+o.S||S0,Ov=+o.O||O0,Dv=+o.D||D0;R.push({id,mode,effect,cause,S:Sv,O:Ov,D:Dv,rpn:Sv*Ov*Dv,mit:o.mit!=null?o.mit:mit,links});};
  if(F.battery){add('r_therm','Cell overheats while charging','fire risk, injury','charger heat, high ambient',10,Ofrom(get('chgt')?get('chgt').st:'na'),4,'charger thermal cut-off, V-0 or V-1 enclosure, charging test',['chgt','touch']);
    add('r_brown','Brownout during transmit','lost readings, reboot loop','cold cell, high peak current',5,EL&&JOBS.tol.res?Math.max(1,Math.min(9,Math.round(JOBS.tol.res.p*40)+1)):4,5,'bulk capacitor, low-battery cut-off, cold test',['life']);}
  if(F.radio)add('r_link','Readings not delivered','missed weather hold or alarm','weak coverage, antenna detuned',6,Ofrom(get('cov')?get('cov').st:'na'),4,'store and forward, link margin test',['cov','interval']);
  add('r_drop','Enclosure cracks on a drop','exposed electronics, ingress','corner impact',6,Ofrom(get('drop')?get('drop').st:'na'),3,'corner radius and wall from the drop model, drop test',['drop']);
  if(F.sealed)add('r_ingress','Water gets in','corrosion, short circuit','seal gap, open apertures',7,Ofrom(get('ip')?get('ip').st:'na'),4,'gasket in the lid, IP test',['ip']);
  add('r_fw','Firmware locks up','device stops working','unhandled state, memory fault',7,(PCFG.firmware||{}).watchdog===false?5:2,6,'hardware watchdog, fault state, soak test',[]);
  if(F.sensor)add('r_bias','Reading biased by self-heating','wrong decisions from data','heat from electronics near the sensor',4,Ofrom(get('acc')?get('acc').st:'na'),5,'sensor next to the vents, calibration',['acc']);
  if(F.button)add('r_button','Button wears out','cannot check the device','cycle wear',3,3,4,'rated switch, cycle test',['resp']);
  if(F.connected){const sc=ctx.sec;add('r_sec','Device compromised','data tampering, botnet','default credentials, unsigned updates',8,sc.st==='pass'?2:sc.st==='warn'?4:7,6,'unique credentials, secure boot, signed updates',[]);}
  if(EL&&EL.cables.length)add('r_conn','Connector works loose','intermittent faults','vibration, poor retention',6,3,6,'locking connectors, vibration test',[]);
  if(ENVM){const g=id=>ENVM.C.find(c=>c.id===id),O=c=>c?({pass:2,warn:5,fail:8}[c.st]||3):0;
    if(g('chghot')||g('chgcold'))add('r_chgwin','Cell charged outside its temperature window','swelling, fire risk','hot day in the sun, or charging below 0 °C',9,Math.max(O(g('chghot')),O(g('chgcold'))),5,'charge inhibit outside 0 to 45 °C, light colour, charge window test',['chgt']);
    if(g('cond'))add('r_cond','Condensation inside','corrosion, short circuits','sealed body with a temperature swing',6,O(g('cond')),6,'vent membrane, conformal coating, cyclic damp heat test',['ip']);
    add('r_brittle','Enclosure cracks when cold','exposed electronics','material below its tough range',6,O(g('matcold')),4,'grade tough at the minimum temperature, cold drop test',['drop']);
    if(g('uv'))add('r_uv','Enclosure degrades in sunlight','cracking, colour change','UV exposure',4,O(g('uv')),5,'UV-stable grade, UV exposure test',[]);
    if(g('corr')||g('corrfix'))add('r_corr','Corrosion','seized fixings, failed seal','salt air',5,Math.max(O(g('corr')),O(g('corrfix'))),5,'anodise, stainless fixings, salt mist test',[]);}
  (PCFG.risks||[]).forEach((x,i)=>add('c'+i,x.mode,x.effect||'',x.cause||'',x.S||5,x.O||3,x.D||5,x.mitigation||'',x.links||[]));
  return R.sort((a,b)=>b.rpn-a.rpn);}
function ecoDiff(){const rv=S.revs[S.revs.length-1];if(!rv||!rv.snap)return{base:null,changes:[]};const cur=snapNow(),ch=[];
  Object.keys(Object.assign({},rv.snap,cur)).forEach(k=>{const a=JSON.stringify(rv.snap[k]),b=JSON.stringify(cur[k]);if(a!==b)ch.push({k,from:rv.snap[k],to:cur[k]});});return{base:rv.rev,changes:ch};}
function snapNow(){const s=last&&last.shape;return s?{process:S.proc,material:S.mat,finish:S.finish,board:S.board,battery:S.batt,fixings:S.fast,
  size:[+fmt(s.W,1),+fmt(s.D,1),+fmt(s.H,1)],wall:s.t?+fmt(s.t,2):null,functions:G.nodes.filter(n=>EB[n.type]&&!EB[n.type].fixed).map(n=>n.type).sort(),requirements:S.req,parts:Object.assign({},S.nom)}:{};}

/* ---------- the programme model ---------- */
let PM=null;
function progCompute(){
  const s=last&&last.shape;progState();if(!s||!REG){PM=null;return null;}
  const F=pflags(),tol=tolerance(s),mod=modal(s),rel=mtbf(F),der=derating(),jn=junctions(),tw=traceWidths(),cr=creepageEnv(creepage(F)),bud=budgets(s,F),df=dfa(),pk=packaging(s),co=carbon(s,F),
    rp=repair(F),rc=recycle(),ec=economics(),sch=progSchedule(),sup=supply(),prod=production(),fm=form(s),eg=ergo(s,F),fb=feedback(F),fw=fwPlan(F),sec=security(F),
    ifs=interfacesReg(s,F),tr=tradeoffs(),tp=testPlan(F),std=standards(F),eco=ecoDiff(),cpk=cpkPlan(s,tol);
  const ctx={sec};const risks=fmea(F,ctx);
  const R0=st=>st||'na',rq=(ids)=>worstOf(REQ.filter(r=>ids.includes(r.id)&&r.st!=='na').map(r=>r.st==='fail'?'fail':r.st==='warn'?'warn':'pass').concat(['na']));
  const elec=EL?worstOf(EL.checks.map(c=>c.st).concat(['pass'])):'na',elecIds=re=>EL?worstOf(EL.checks.filter(c=>re.test(c.id)).map(c=>c.st).concat(['pass'])):'na';
  const actReq=REQ.filter(r=>r.st!=='na'&&r.pri!=='Off'),creqs=(PCFG.customRequirements||[]);
  const untested=actReq.filter(r=>!tp.some(t=>t.reqs.includes(r.id))).map(r=>r.id).concat(creqs.filter(c=>!tp.some(t=>t.reqs.includes('c:'+c.id))).map(c=>c.id));
  const tstat=list=>{if(list.some(t=>t.st==='failed'))return'fail';if(list.length&&list.every(t=>t.st==='passed'||t.st==='waived'))return'pass';return'open';};
  const H={
    req:()=>{const m=REQ.filter(r=>r.pri==='Must');return m.some(r=>r.st==='fail')?'fail':m.some(r=>r.st==='warn')?'warn':'pass';},
    economics:()=>ec.st,interfaces:()=>ifs.length?(['evt','dvt','pvt','production','service'].includes(S.prog.phase)&&ifs.some(i=>!i.frozen)?'warn':'pass'):'open',
    budgets:()=>worstOf(bud.map(b=>b.st)),tradeoffs:()=>tr.every(t=>t.st==='pass')?'pass':'open',vtrace:()=>untested.length?'fail':'pass',
    compliance:()=>std.some(x=>x.st==='failed')?'fail':std.length&&std.every(x=>x.st==='passed'||x.st==='not needed')?'pass':'open',
    form:()=>fm.st,cmf:()=>'pass',ergo:()=>eg.st,feedback:()=>fb.st,behaviour:()=>EL&&EL.DEC.some(x=>!S.decisions[x.id])?'warn':'pass',packaging:()=>pk.st,
    dfm:()=>last.rep?worstOf(last.rep.rules.map(r=>r.st)):'na',tolerance:()=>tol.st,drop:()=>R0(simRes&&simRes.drop&&simRes.drop.st),press:()=>R0(simRes&&simRes.press&&simRes.press.st),
    modal:()=>mod?mod.st:'na',thermal:()=>R0(simRes&&simRes.thermal&&simRes.thermal.st),fill:()=>simRes&&simRes.fill&&!simRes.fill.na?simRes.fill.st:'na',ip:()=>rq(['ip']),
    harness:()=>EL?worstOf(EL.checks.filter(c=>c.lines.includes('harness')).map(c=>c.st).concat(['pass'])):'na',
    material:()=>{const M=matOf(),o=F.outdoor&&!M.metal&&['abs','absfr'].includes(S.mat)?'warn':'pass',c=F.outdoor&&M.metal&&S.finish==='asm'?'warn':'pass';return worstOf([o,c,elecIds(/^fire$/)]);},
    elec:()=>elec,rails:()=>elecIds(/^i|^v/),brownout:()=>elecIds(/^brown/),battery:()=>worstOf([elecIds(/^bpk|^batnc|^prot/),rq(['life','chgt'])]),
    tracew:()=>tw.length?worstOf(tw.map(r=>r.st)):'na',creepage:()=>cr.st,usbimp:()=>F.usb?'plan':'na',rf:()=>elecIds(/^rf|^ant|^sky/),error:()=>{const e=bud.find(b=>b.id==='error');return e?e.st:'na';},
    pins:()=>elecIds(/^pin|^i2c/),junction:()=>jn.st,emc:()=>EL&&EL.checks.some(c=>c.id==='emc')?elecIds(/^emc/):'plan',fwexec:()=>'plan',ota:()=>F.ota?'plan':(F.connected?'warn':'na'),
    protocol:()=>F.connected?'plan':'na',security:()=>sec.st,mtbf:()=>rel.st,derating:()=>der.st,dfa:()=>df.st,schedule:()=>sch.st,supply:()=>sup.st,
    bom:()=>last.bom?(last.bom.lines.some(l=>l.status==='conflict')?'fail':last.bom.lines.some(l=>l.status!=='verified')?'open':'pass'):'open',
    cost:()=>{const r=last.rep&&last.rep.rules.find(x=>/Process suits/.test(x.label));return r?r.st:'pass';},production:()=>'plan',cpk:()=>tol.st,
    tests:()=>tstat(tp.filter(t=>PHASES.findIndex(p=>p.id===t.phase)<=PHASES.findIndex(p=>p.id===S.prog.phase))),fmea:()=>risks.some(r=>r.rpn>=300&&!r.mit)?'fail':risks.some(r=>r.rpn>=200&&!r.mit)?'warn':'pass',
    labels:()=>'plan',repair:()=>rp.st,carbon:()=>co?co.st:'na',recycle:()=>rc.st,eco:()=>eco.base&&eco.changes.length?'warn':'pass',
    environment:()=>ENVM?ENVM.st:'na',techfile:()=>REG.techfile.every((d,i)=>S.prog.docs['d'+i]==='done')?'pass':'open'};
  const doms=REG.domains.map(d=>{const ap=applies(d.when,F),o=S.prog.dom[d.id]||{};if(!ap)return Object.assign({},d,{ap,st:'na',owner:o.owner||'',ev:o.ev||''});
    const hs=d.hooks.map(h=>H[h]?H[h]():'open');let st=hs.length?worstOf(hs):'open';if(d.impl==='tracked'&&st!=='fail')st=o.st==='done'?'pass':o.st==='progress'?'plan':'open';
    if(o.st==='done'&&st==='open')st='pass';return Object.assign({},d,{ap,st,hs,owner:o.owner||'',ev:o.ev||'',ust:o.st||''});});
  // phase exit criteria
  const g=gateState0(),ledger=PCFG.ledger||[],unrev=ledger.filter((x,i)=>!(S.prog.ledger['l'+i]||x.status)||((S.prog.ledger['l'+i]||x.status)==='unreviewed')).length;
  const asm=PCFG.assumptions||[],unconf=asm.filter(a=>!S.prog.confirmed[a.id]).length,mk=(PCFG.markets||[]).length;
  const tph=ph=>tp.filter(t=>t.phase===ph),crit={
    define:[['Intent ledger reviewed',ledger.length?(unrev?'open':'pass'):'open',ledger.length?`${ledger.length-unrev} of ${ledger.length} statements reviewed`:'no ledger recorded'],
      ['Assumptions confirmed',asm.length?(unconf?'open':'pass'):'pass',asm.length?`${asm.length-unconf} of ${asm.length} confirmed`:'none recorded'],
      ['Markets chosen',mk?'pass':'open',mk?(PCFG.markets||[]).join(', '):'add markets to the product config'],
      ['Every requirement has a value',g[0].ok?'pass':'fail',g[0].msg],['Every requirement has a planned test',untested.length?'fail':'pass',untested.length?untested.join(', '):'all covered']],
    concept:[['No blocking problems (gate 2)',g[1].ok?'pass':'fail',g[1].msg],['Every Must requirement met (gate 3)',g[2].ok?'pass':'fail',g[2].msg],['Interfaces drafted',ifs.length?'pass':'open',ifs.length+' interfaces'],
      ['Trade-offs have reasons',H.tradeoffs(),tr.filter(t=>t.st!=='pass').map(t=>t.id).join(', ')||'all recorded'],['Top risks have mitigations',H.fmea(),`highest RPN ${risks[0]?risks[0].rpn:0}`],
      ['Compliance plan',std.length?'pass':'open',std.length+' standards'],['Budgets inside targets',H.budgets(),bud.map(b=>b.name+' '+PST[b.st]).join(', ')]],
    evt:[['Background validation done (gate 4)',g[3].ok?'pass':'open',g[3].msg],['EVT tests passed',tstat(tph('evt')),`${tph('evt').filter(t=>t.st==='passed').length} of ${tph('evt').length}`],
      ['Interfaces frozen',ifs.every(i=>i.frozen)?'pass':'open',`${ifs.filter(i=>i.frozen).length} of ${ifs.length}`],
      ['Production tooling ordered',S.proc==='im'?(S.prog.miles.tooling?'pass':'open'):'pass',S.proc==='im'?(S.prog.miles.tooling?'ordered':'not ordered'):'no hard tooling']],
    dvt:[['DVT tests passed',tstat(tph('dvt')),`${tph('dvt').filter(t=>t.st==='passed').length} of ${tph('dvt').length}`],['Certification passed',H.compliance(),`${std.filter(x=>x.st==='passed').length} of ${std.length}`],
      ['BOM released',S.revs.length&&last.bom&&S.revs[S.revs.length-1].fp===last.bom.fp?'pass':'open',S.revs.length?'Rev '+S.revs[S.revs.length-1].rev:'not released'],['Technical file complete',H.techfile(),'']],
    pvt:[['PVT tests passed',tstat(tph('pvt')),`${tph('pvt').filter(t=>t.st==='passed').length} of ${tph('pvt').length}`],['Long-lead parts second-sourced',sup.st,sup.single.map(r=>r.item).join(', ')||'all covered'],
      ['Yield at or above target',S.prog.miles.yield!=null?(+S.prog.miles.yield>=prod.yieldT*100?'pass':'fail'):'open',S.prog.miles.yield!=null?S.prog.miles.yield+'%':'enter measured yield']],
    production:[['Field quality tracking in place',S.prog.dom.fieldq&&S.prog.dom.fieldq.st==='done'?'pass':'open',''],['Change control active',H.eco(),eco.base?`${eco.changes.length} change(s) since Rev ${eco.base}`:'no release yet']],
    service:[['Repair and take-back in place',rp.st,'repair score '+fmt(rp.score,1)+' of 10'],['End-of-life route recorded',S.prog.dom.eol&&S.prog.dom.eol.st==='done'?'pass':'open','']]};
  PM={F,tol,mod,rel,der,jn,tw,cr,bud,df,pk,co,rp,rc,ec,sch,sup,prod,fm,eg,fb,fw,sec,ifs,tr,tp,std,eco,cpk,risks,doms,crit,untested,H,ledger,asm,unconf,unrev};
  return PM;}
function gateState(){
  const g=gateState0(),C=PCFG.customRequirements||[],asm=PCFG.assumptions||[],un=asm.filter(a=>!S.prog||!S.prog.confirmed||!S.prog.confirmed[a.id]),led=(PCFG.ledger||[]).filter((x,i)=>{const s2=(S.prog&&S.prog.ledger&&S.prog.ledger['l'+i])||x.status;return!s2||s2==='unreviewed';});
  if(un.length||led.length){g[0].ok=false;g[0].msg=[un.length?`${un.length} assumption${un.length>1?'s':''} to confirm`:null,led.length?`${led.length} intent statement${led.length>1?'s':''} to review`:null].filter(Boolean).join(', ');}
  const bad=C.filter(c=>c.pri==='Must'&&((S.prog&&S.prog.creq&&S.prog.creq[c.id]&&S.prog.creq[c.id].st)==='notmet'));
  if(bad.length){g[2].ok=false;g[2].msg=`${bad.length} custom Must requirement${bad.length>1?'s':''} not met`;}
  const unv=C.filter(c=>c.pri==='Must'&&((S.prog&&S.prog.creq&&S.prog.creq[c.id]&&S.prog.creq[c.id].st)||'open')!=='met');
  if(unv.length&&g[3].ok){g[3].ok=false;g[3].msg=`${unv.length} custom Must requirement${unv.length>1?'s':''} still to verify`;}
  for(let i=1;i<4;i++)if(!g[i-1].ok&&g[i].ok){g[i].ok=false;g[i].msg='Waiting on earlier gates';}
  /* MetaForge: gates 2 to 4 are decided by checks of the placeholder enclosure, so for an authored form they are never green. */
  if(AUTHORED){for(let i=1;i<4;i++){g[i].ok=false;g[i].msg='Not assessed for an authored form';}}
  return g;}

/* ---------- design view additions: assumptions, custom requirements, rationale ---------- */
function renderIntentExtras(){
  progState();const A=PCFG.assumptions||[],C=PCFG.customRequirements||[],box=$('#asmBox'),cb=$('#creqList'),rb=$('#ratBox');
  if(box){box.hidden=!A.length;box.innerHTML=A.length?`<div class="lbl">Confirm these assumptions</div>${A.map(a=>`<label class="tog asm"><input type="checkbox" data-pset="confirmed.${esc(a.id)}"${S.prog.confirmed[a.id]?' checked':''}><span>${esc(a.text)}${a.ref?` <span class="eng muted">(${esc(a.ref)})</span>`:''}</span></label>`).join('')}`:'';}
  if(cb){cb.hidden=!C.length;cb.innerHTML=C.length?`<div class="lbl">Your own requirements</div>${C.map(c=>{const p=S.prog.creq[c.id]||{},st=p.st||'open';
    return `<li class="rq st-${st==='met'?'pass':st==='notmet'?'fail':'na'}"><div class="rqh"><span class="chip">${st==='met'?'Met':st==='notmet'?'Not met':'To verify'}</span><span class="rpri" style="border:0">${esc(c.pri||'Should')}</span><span class="conf eng">${esc(c.verify||'test')}, ${esc((c.phase||'dvt').toUpperCase())}</span>
      <select data-pset="creq.${esc(c.id)}.st" aria-label="Status" style="margin-left:auto"><option value="open"${st==='open'?' selected':''}>To verify</option><option value="met"${st==='met'?' selected':''}>Met</option><option value="notmet"${st==='notmet'?' selected':''}>Not met</option></select></div>
      <div class="ears">${esc(c.text)}</div><div class="rv">${c.measure?`<b>${esc(c.measure)}${c.target!=null?' '+esc(String(c.target)):''}</b> · `:''}${c.source?'source: '+esc(c.source):''}</div>
      <input class="mpn eng" data-pset="creq.${esc(c.id)}.ev" value="${esc(p.ev||'')}" placeholder="Evidence: report, link or note" aria-label="Evidence"></li>`;}).join('')}`:'';}
  if(rb){const RT=PCFG.rationale||{},keys=Object.keys(RT);rb.hidden=!keys.length;rb.innerHTML=keys.length?`<div class="lbl">Why these choices</div><dl class="rat">${keys.map(k=>`<dt>${esc(k)}</dt><dd>${esc(RT[k])}</dd>`).join('')}</dl>`:'';}
}

/* ---------- programme view ---------- */
const chipS=st=>`<span class="pchip p-${st}">${PST[st]||esc(st)}</span>`;
const card=(id,title,sum,body,cls='')=>{S.open=S.open||{};const o=S.open['p:'+id]!=null?S.open['p:'+id]:id==='gate';return `<section class="pcard ${cls}${o?' open':''}" id="pc-${id}"><button type="button" class="acc-h" data-pcard="${id}" aria-expanded="${o}"><span class="acc-t">${title}</span><span class="acc-s">${sum||''}</span><span class="acc-c" aria-hidden="true"></span></button><div class="acc-b">${body}</div></section>`;};
const tbl=(heads,rows)=>`<div class="tw"><table class="ptab"><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
const sel=(path,val,opts)=>`<select data-pset="${path}">${opts.map(([v,l])=>`<option value="${v}"${String(val)===v?' selected':''}>${l}</option>`).join('')}</select>`;
function renderProgramme(){
  const box=$('#progView');if(!box||box.hidden)return;if(box.contains(document.activeElement)&&document.activeElement.matches('input[type=text],input:not([type]),textarea'))return;
  const M=progCompute();if(!REG){box.innerHTML='<p class="note" style="padding:24px">This page was built without the programme registry. Rebuild it with the Formgraph plugin to see the programme view.</p>';return;}
  if(!M){box.innerHTML='<p class="note" style="padding:24px">Connect a form to see the programme.</p>';return;}
  const ph=S.prog.phase,pi=PHASES.findIndex(p=>p.id===ph),cr=M.crit[ph]||[],canAdv=cr.length&&cr.every(c=>c[1]==='pass'||c[1]==='plan')&&pi<PHASES.length-1;
  const loops=REG.loops.filter(l=>l.from===ph);
  let h=`<div class="phasebar">${PHASES.map((p,i)=>`<div class="ph${i<pi?' done':''}${i===pi?' cur':''}"><b>${esc(p.name)}</b><small>${esc(p.question)}</small></div>`).join('')}</div>`;
  h+=`<div class="pgrid">`;
  h+=card('gate',`Phase exit: ${esc(PHASES[pi].name)}`,`<span class="vsub">${canAdv?'Ready to move on':'Not ready yet'}</span>`,
    `<ul class="plist">${cr.map(c=>`<li>${chipS(c[1])}<span>${esc(c[0])}</span><small>${esc(c[2]||'')}</small></li>`).join('')}</ul>
     <div class="xrow"><button class="btn pri" type="button" data-padv${canAdv?'':' disabled'}>Move to ${pi<PHASES.length-1?esc(PHASES[pi+1].name):'end'}</button>
     ${loops.length?`<select id="loopTo" aria-label="Send back to">${loops.map(l=>`<option value="${l.to}">Back to ${esc(PHASES.find(p=>p.id===l.to).name)} (${esc(l.why)})</option>`).join('')}</select><input id="loopWhy" placeholder="What failed" aria-label="What failed"><button class="btn" type="button" data-ploop>Send back</button>`:''}</div>
     ${S.prog.loops.length?`<div class="lbl">Loops so far</div><ul class="plist">${S.prog.loops.map(l=>`<li>${chipS('warn')}<span>${esc(l.from.toUpperCase())} to ${esc(l.to.toUpperCase())}</span><small>${esc(l.date)}: ${esc(l.why||'')}</small></li>`).join('')}</ul>`:''}`,'wide');
  // effort matrix
  const cols=['Define','Concept','EVT','DVT','PVT','Production'],sym=['·','○','●'];
  h+=card('effort','Who is busy in this phase','',`${tbl(['Domain'].concat(cols.map((c,i)=>i===Math.min(pi,5)?`<span class="curc">${c}</span>`:c)),Object.values(REG.effort_rows).map(r=>`<tr><td>${esc(r.name)}</td>${r.effort.map((e,i)=>`<td class="eff e${e}${i===Math.min(pi,5)?' curc':''}">${sym[e]}</td>`).join('')}</tr>`))}
    <p class="note">● heavy, ○ light, · none.</p><ul class="notes eng">${REG.effort_notes.concat(REG.overlap_drivers).map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`);
  // intent ledger
  const led=M.ledger;h+=card('intent','Intent ledger',`<span class="vsub">${led.length?`${led.length-M.unrev} of ${led.length} reviewed`:'No ledger in this config'}</span>`,
    led.length?`<ul class="plist">${led.map((x,i)=>{const st=S.prog.ledger['l'+i]||x.status||'unreviewed';return `<li>${chipS(st==='mapped'||st==='custom'?'pass':st==='unrepresentable'?'warn':st==='out-of-scope'?'na':'open')}<span>${esc(x.text)}</span><small>${(x.maps||[]).map(esc).join(', ')||'not mapped'}${x.note?' · '+esc(x.note):''}</small>
      <span class="eng">${sel(`ledger.l${i}`,st,[['unreviewed','Unreviewed'],['mapped','Mapped'],['custom','Custom requirement'],['unrepresentable','Not representable'],['out-of-scope','Out of scope']])}</span></li>`;}).join('')}</ul>`:'<p class="note">Ask Claude to regenerate the product with /formgraph:edit to record every brief statement.</p>');
  // traceability
  const reqRows=REQ.filter(r=>r.pri!=='Off').map(r=>{const q=REQS.find(x=>x.id===r.id),ts=M.tp.filter(t=>t.reqs.includes(r.id));return `<tr><td>${esc(q.t.replace(/\[(\w+)\]/g,(m,k)=>{const o=q.in&&q.in.opts&&q.in.opts.find(o=>o[0]===S.req[k]);return o?o[1]:S.req[k];}))}</td><td>${esc(r.pri)}</td><td>${chipS(r.st==='na'?'na':r.st==='fail'?'fail':r.st==='warn'?'warn':'pass')}</td><td>${esc(r.how||'')}</td><td>${ts.map(t=>esc(t.name)+' ('+t.phase.toUpperCase()+')').join('<br>')||'<b class="bad">no test</b>'}</td></tr>`;})
    .concat((PCFG.customRequirements||[]).map(c=>{const ts=M.tp.filter(t=>t.reqs.includes('c:'+c.id)),st=(S.prog.creq[c.id]||{}).st||'open';return `<tr><td>${esc(c.text)}</td><td>${esc(c.pri||'Should')}</td><td>${chipS(st==='met'?'pass':st==='notmet'?'fail':'open')}</td><td>${esc(c.verify||'test')}</td><td>${ts.map(t=>esc(t.phase.toUpperCase())).join(', ')}</td></tr>`;}));
  h+=card('trace','Traceability (V-model)',`<span class="vsub">${M.untested.length?M.untested.length+' requirement(s) without a test':'every requirement has a planned test'}</span>`,
    `<div class="vmodel">${REG.vmodel.map(v=>`<div><span>${esc(v.left)}</span><i></i><span>${esc(v.right)}</span></div>`).join('')}</div>${tbl(['Requirement','Priority','Now','Proved by','Test'],reqRows)}`,'wide');
  // architecture
  const fnMap={sense:'Convert (measure)',comm:'Transmit',power:'Store and convert power',compute:'Control',ui:'Interact',conn:'Transmit (wired)'};
  h+=card('arch','Architecture and interfaces',`<span class="vsub">${M.ifs.filter(i=>i.frozen).length} of ${M.ifs.length} interfaces frozen</span>`,
    `<p class="note">${esc(REG.meeting_point)}</p><div class="lbl">Functions</div><ul class="plist">${(EL?EL.nodes:[]).map(n=>`<li>${chipS('pass')}<span>${esc(n.type==='e_board'?boardOf().name:n.type==='e_batt'?battOf().name:EB[n.type].name)}</span><small>${esc(fnMap[EB[n.type].cat]||'')}</small></li>`).join('')}</ul>
     ${tbl(['Kind','Between','What','Frozen'],M.ifs.map(i=>`<tr><td>${esc(i.kind)}</td><td>${esc(i.a)} and ${esc(i.b)}</td><td>${esc(i.what)}</td><td><input type="checkbox" data-pset="ifz.${esc(i.id)}"${i.frozen?' checked':''} aria-label="Frozen"></td></tr>`))}`,'wide');
  // budgets
  h+=card('budgets','Budgets','',tbl(['Budget','Now','Target','Status'],M.bud.map(b=>`<tr><td>${esc(b.name)}</td><td>${esc(b.val)}</td><td>${esc(b.lim)}</td><td>${chipS(b.st)}</td></tr>`))+
    (M.bud.find(b=>b.parts)?`<p class="note eng">Error budget: ${M.bud.find(b=>b.parts).parts.map(p=>esc(p[0])+' ±'+fmt(p[1],2)).join(', ')} °C, combined by root sum of squares.</p>`:'')+'<p class="note">Set targets in the product config under programme.budgets.</p>');
  // trade-offs
  h+=card('trade','Trade-offs',`<span class="vsub">${M.tr.filter(t=>t.st==='pass').length} of ${M.tr.length} with reasons</span>`,`<ul class="plist">${M.tr.map(t=>`<li>${chipS(t.st)}<span>${esc(t.q)} <b>${esc(t.chosen||'')}</b></span><small>${esc(t.reason||'no reason recorded')}${t.opts&&t.opts.length?' · options: '+t.opts.map(esc).join(', '):''}</small></li>`).join('')}</ul>`);
  // FMEA
  h+=card('fmea','Risk (FMEA)',`<span class="vsub">highest RPN ${M.risks[0]?M.risks[0].rpn:0}</span>`,tbl(['Failure mode','Effect','S','O','D','RPN','Mitigation'],M.risks.map(r=>`<tr><td>${esc(r.mode)}<small class="muted"><br>${esc(r.cause)}</small></td><td>${esc(r.effect)}</td>${['S','O','D'].map(k=>`<td><input class="sod" type="number" min="1" max="10" data-pset="risks.${r.id}.${k}" value="${r[k]}" aria-label="${k}"></td>`).join('')}<td class="${r.rpn>=200?'bad':''}">${r.rpn}</td><td><input data-pset="risks.${r.id}.mit" value="${esc(r.mit||'')}" aria-label="Mitigation"></td></tr>`))+'<p class="note">S severity, O occurrence, D detection, each 1 to 10. Occurrence follows the live checks until you set it.</p>','wide');
  // tests
  const tst=[['planned','Planned'],['running','Running'],['passed','Passed'],['failed','Failed'],['waived','Waived']];
  h+=card('tests','Test and validation plan',`<span class="vsub">${M.tp.filter(t=>t.st==='passed').length} of ${M.tp.length} passed</span>`,tbl(['Phase','Test','Level','Proves','Status','Evidence'],M.tp.slice().sort((a,b)=>PHASES.findIndex(p=>p.id===a.phase)-PHASES.findIndex(p=>p.id===b.phase)).map(t=>`<tr><td>${t.phase.toUpperCase()}</td><td>${esc(t.name)}${envCond(t.id)?`<small class="muted"><br>${esc(envCond(t.id))}</small>`:''}</td><td>${esc(t.level)}</td><td>${t.reqs.map(r=>esc(r.replace(/^c:/,''))).join(', ')||'general'}</td><td>${sel(`tests.${t.id}.st`,t.st,tst)}</td><td><input data-pset="tests.${t.id}.ev" value="${esc(t.ev)}" placeholder="report or link" aria-label="Evidence"></td></tr>`)),'wide');
  // compliance
  const ss=[['planned','Planned'],['booked','Booked'],['passed','Passed'],['failed','Failed'],['not needed','Not needed']],dvtS=M.sch.rows.find(r=>r.k==='dvt').start;
  h+=card('comp','Compliance and certification',`<span class="vsub">markets: ${esc((PCFG.markets||['UK','EU']).join(', '))}</span>`,tbl(['Standard','Area','Book by','Status','Test house'],M.std.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.area)}</td><td>${dstr(addW(dvtS,-x.weeks))}</td><td>${sel(`std.${x.id}.st`,x.st,ss)}</td><td><input data-pset="std.${x.id}.house" value="${esc(x.house)}" placeholder="accredited lab" aria-label="Test house"></td></tr>`))+
    (M.F.connected?`<div class="lbl">Security checklist${M.sec.pstiApplies?' (UK PSTI applies)':''}</div><ul class="plist">${M.sec.items.map(i=>`<li>${chipS(i.ok?'pass':i.psti&&M.sec.pstiApplies?'fail':'warn')}<span>${esc(i.name)}</span></li>`).join('')}</ul><p class="note">Set these under programme.firmware in the product config.</p>`:''),'wide');
  // schedule
  h+=card('sched','Schedule and long-lead items',`<span class="vsub">from ${dstr(M.sch.rows[0].start)}</span>`,tbl(['Phase','Starts','Ends'],M.sch.rows.map(r=>`<tr><td>${r.k.toUpperCase()}</td><td>${dstr(r.start)}</td><td>${dstr(r.end)}</td></tr>`))+
    `<ul class="plist">${M.sch.items.map(i=>`<li>${chipS(i.st)}<span>${esc(i.name)} by ${dstr(i.by)}</span><small>${esc(i.why)}</small></li>`).join('')}</ul>${S.proc==='im'?`<label class="tog"><input type="checkbox" data-pset="miles.tooling"${S.prog.miles.tooling?' checked':''}><span>Tooling ordered</span></label>`:''}`);
  // electronics depth
  h+=card('elecd','Electronics: power, PCB, signals, safety',`<span class="vsub">${esc(M.cr.v)}</span>`,
    `<div class="lbl">Junction temperatures (worst case ${fmt(M.jn.Ta||25,1)} °C inside)</div>${tbl(['Part','Power','θJA','Tj',''],M.jn.rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${fmt(r.P,2)} W</td><td>${r.theta} °C/W</td><td>${fmt(r.Tj,0)} °C</td><td>${chipS(r.st)}</td></tr>`))}
     <div class="lbl">Derating (use against rating, target 80% or less)</div>${tbl(['Item','Use',''],M.der.rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${fmt(r.u*100,0)}%</td><td>${chipS(r.st)}</td></tr>`))}
     <div class="lbl">PCB trace width for current (IPC-2221, outer layer, 1 oz, 10 °C rise)</div>${tbl(['Rail','Peak','Min width',''],M.tw.map(r=>`<tr><td>${esc(r.name)}</td><td>${fmt(r.I,2)} A</td><td>${fmt(r.w,2)} mm</td><td>${chipS(r.st)}</td></tr>`))}
     <div class="lbl">Creepage and clearance</div><p class="note">${esc(M.cr.v)}: clearance ${M.cr.cl} mm, creepage ${M.cr.cr} mm. ${esc(M.cr.note)}</p>
     ${M.F.usb?'<p class="note">USB 2.0 data pair: 90 Ω differential, length-matched within 1.25 mm. Check in layout.</p>':''}<p class="note">Electrical-rule checks run live under Electronics and wiring. Design-rule checks and layout happen in the PCB tool.</p>`,'wide');
  // firmware
  const fw=M.fw;h+=card('fw','Firmware and software',`<span class="vsub">${esc(fw.os)}</span>`,
    `<div class="cols2"><div><div class="lbl">Tasks</div><ul class="bul">${fw.tasks.map(t=>`<li>${esc(t)}</li>`).join('')}${fw.watchdog?'<li>Hardware watchdog</li>':''}</ul><div class="lbl">Interrupts and timers</div><ul class="bul">${fw.ints.concat(fw.timers).map(t=>`<li>${esc(t)}</li>`).join('')}</ul>
     ${fw.queues.length||fw.mutex.length?`<div class="lbl">Queues and locks</div><ul class="bul">${fw.queues.concat(fw.mutex).map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:''}</div>
     <div><div class="lbl">Modes and failsafes</div><ul class="bul">${fw.modes.concat(fw.fails).map(t=>`<li>${esc(t)}</li>`).join('')}</ul><div class="lbl">Boot path</div><ol class="bul">${fw.boot.map(t=>`<li>${esc(t)}</li>`).join('')}</ol><div class="lbl">Update path</div><ol class="bul">${fw.upd.map(t=>`<li>${esc(t)}</li>`).join('')}</ol></div></div>
     <div class="lbl">Data</div><p class="note">Protocol: ${esc(fw.proto)}. Message: ${fw.fields.map(f=>esc(f[0])+' ('+f[1]+' B)').join(', ')}, about ${fw.bytes} bytes. Configuration: ${fw.config.map(esc).join(', ')}.</p>
     <p class="note">Cloud, device management and companion apps are tracked under Domains.</p>`,'wide');
  // simulation verification
  const sims=[['Drop impact','energy method','reference formula','analytic','n/a','t_drop'],['Press deflection','plate and beam theory','reference formula','analytic','n/a',null],['Panel first mode',M.mod?fmt(M.mod.f,0)+' Hz':'n/a','clamped plate formula','analytic','n/a','t_vib'],
    ['Thermal, lumped','node network','energy balance','analytic','n/a','t_charge'],['3D heat','voxel conduction','energy balance','in browser','not run, single grid','t_charge'],['Mould fill','flow length ratio','process rule','rule','n/a',null],
    ['Battery life','duty cycle and Monte Carlo','power budget','analytic, 2,000 runs','converged','t_battlife'],['Link budget','margin','radio rule','rule','n/a','t_rf'],['Lid fit','Monte Carlo stack-up','Cpk','20,000 samples','converged','t_cpk'],['Reliability','parts count','FIT sum','analytic','n/a','t_life']];
  h+=card('simv','Simulation and verification','',tbl(['Model','Method','Reference case','Check','Mesh independence','Correlation to test'],sims.map(r=>{const t=r[5]&&M.tp.find(x=>x.id===r[5]);return `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td><td>${esc(r[4])}</td><td>${t?chipS(t.st==='passed'?'pass':t.st==='failed'?'fail':'open')+' '+esc(t.name):'none planned'}</td></tr>`;}))+
    `<p class="note">Lid fit: gap ${fmt(M.tol.mu,2)} mm, σ ${fmt(M.tol.sig,3)} mm, Cpk ${fmt(M.tol.cpk,2)}, ${fmt(M.tol.tight*100,2)}% too tight and ${fmt(M.tol.loose*100,2)}% too loose. Panel first mode ${M.mod?fmt(M.mod.f,0)+' Hz'+(M.mod.ribs>1?' with ribs':''):'n/a'} (aim for 150 Hz or more).</p>`,'wide');
  // manufacturing
  const d=M.df,pr=M.prod;h+=card('mfg','Manufacturing, assembly and supply',`<span class="vsub">assembly efficiency ${fmt(d.eff*100,0)}%</span>`,
    `<div class="lbl">Design for assembly</div><p class="note">${d.parts} parts and fixings, ${d.fast} screws, ${d.cab} cables, about ${Math.round(d.time)} s to assemble. Theoretical minimum ${d.nmin} parts. ${d.tips.map(esc).join('. ')}</p>
     <div class="lbl">Production</div><p class="note">${S.proc==='im'?`Moulding cycle about ${fmt(pr.cyc,0)} s. `:''}Line rate about ${fmt(pr.rate,0)} units per hour per station. Yield target ${fmt(pr.yieldT*100,0)}%. Measured yield: <input class="sod" data-pset="miles.yield" value="${esc(S.prog.miles.yield!=null?S.prog.miles.yield:'')}" aria-label="Measured yield">%</p>
     <ol class="bul">${pr.steps.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>
     <div class="lbl">Inspection and Cpk</div>${tbl(['Dimension','Nominal','Tolerance','Cpk',''],M.cpk.map(c=>`<tr><td>${esc(c.dim)}</td><td>${esc(c.nom)}</td><td>${esc(c.tolv)}</td><td>${c.cpk!=null?fmt(c.cpk,2):'measure at FAI'}</td><td>${chipS(c.st)}</td></tr>`))}
     <div class="lbl">Supply and lead times</div>${tbl(['Item','Lead time','Second source'],M.sup.rows.map(r=>`<tr><td>${esc(r.item)}</td><td>${r.w} weeks</td><td><input type="checkbox" data-pset="std.ss_${esc(r.id)}"${r.ss?' checked':''} aria-label="Second source"></td></tr>`))}
     <div class="lbl">Packaging and logistics</div><p class="note">Carton about ${M.pk.dims.map(x=>fmt(x,0)).join(' x ')} mm with ${M.pk.pd} mm padding, ${fmt(M.pk.mass,0)} g packed, volumetric weight ${fmt(M.pk.vol,2)} kg, chargeable ${fmt(M.pk.charge,2)} kg. Add the customs tariff code and country of origin to the shipping file.</p>`,'wide');
  // ID and HF
  h+=card('idhf','Industrial design and human factors','',`<ul class="plist">${M.fm.items.concat(M.eg.items).map(i=>`<li>${chipS(i.st)}<span>${esc(i.name)}: <b>${esc(i.val)}</b></span><small>${esc(i.note)}</small></li>`).join('')}</ul>
    <div class="lbl">CMF</div><p class="note">${esc((CMF.find(c=>c.id===S.color)||CMF[0]).name)}, ${esc(matOf().name)}, ${esc(finishOf().name)}.</p>
    <div class="lbl">Feedback for each event</div><ul class="plist">${M.fb.rows.map(r=>`<li>${chipS(r.st)}<span>${esc(r.e)}</span><small>${esc(r.ch)}</small></li>`).join('')}</ul>
    <div class="lbl">User flows</div><ul class="plist">${REG.flows.map(f=>{const v=S.prog.flows[f.id]!=null?S.prog.flows[f.id]:((PCFG.flows||{})[f.id]||'');return `<li>${chipS(v?'pass':'open')}<span>${esc(f.name)}</span><small>${esc(f.ask)}</small><input data-pset="flows.${f.id}" value="${esc(v)}" placeholder="Describe the ${esc(f.name.toLowerCase())} flow" aria-label="${esc(f.name)} flow"></li>`;}).join('')}</ul>`);
  // lifecycle
  h+=card('life','Lifecycle and sustainability',`<span class="vsub">repair score ${fmt(M.rp.score,1)} of 10</span>`,`<ul class="plist">${M.rp.pts.filter(p=>p[2]).map(p=>`<li>${chipS(p[0]?'pass':'open')}<span>${esc(p[1])}</span></li>`).join('')}</ul>
    ${M.co?`<div class="lbl">Carbon, cradle to gate (indicative)</div>${tbl(['Item','kg CO2e'],M.co.rows.map(r=>`<tr><td>${esc(r[0])}</td><td>${fmt(r[1],2)}</td></tr>`).concat([`<tr><td><b>Total</b></td><td><b>${fmt(M.co.tot,2)}</b></td></tr>`]))}`:''}
    <div class="lbl">End of life</div><p class="note">Material marking ${esc(M.rc.code)}. ${M.rc.notes.map(esc).join('. ')}${M.F.battery?'. Battery removable for separate collection; WEEE and battery take-back apply.':'.'}</p>
    <div class="lbl">Reliability</div><p class="note">Predicted MTBF about ${fmt(M.rel.years,0)} years (${Math.round(M.rel.lam)} FIT at ${fmt(M.rel.piT,2)} temperature factor), ${fmt(M.rel.R*100,1)}% survive ${M.rel.L} years. ${chipS(M.rel.st)}</p>
    <div class="lbl">Changes since the last release (ECO draft)</div>${M.eco.base?(M.eco.changes.length?`<ul class="plist">${M.eco.changes.map(c=>`<li>${chipS('warn')}<span>${esc(c.k)}</span><small>${esc(JSON.stringify(c.from))} to ${esc(JSON.stringify(c.to))}</small></li>`).join('')}</ul>`:`<p class="note">No changes since Rev ${esc(M.eco.base)}.</p>`):'<p class="note">No release yet. Releasing a BOM revision starts change control.</p>'}
    <div class="lbl">Technical file</div><ul class="plist">${REG.techfile.map((t,i)=>`<li><label class="tog"><input type="checkbox" data-pset="docs.d${i}" data-val="done"${S.prog.docs['d'+i]==='done'?' checked':''}><span>${esc(t)}</span></label></li>`).join('')}</ul>`);
  // business
  const ec=M.ec;h+=card('biz','Product and business',ec.price?`<span class="vsub">margin ${fmt(ec.margin*100,0)}%</span>`:'',
    `<p class="note">Unit cost about ${gbp(ec.cost)}: BOM ${gbp(ec.bom)}, assembly ${gbp(ec.labour)}, packaging ${gbp(ec.pack)}.${ec.price?` Price ${gbp(ec.price)}, margin ${fmt(ec.margin*100,0)}% against ${fmt(ec.target*100,0)}%. ${chipS(ec.st)}`:' Set programme.business.price to see the margin.'}</p>
     <div class="lbl">Variants</div><p class="note">${esc(((PCFG.business||{}).variants||CMF.map(c=>c.name)).join(', '))}</p>
     <p class="note">Intellectual property, contracts, liability insurance and pricing model are tracked under Domains with an owner and evidence.</p>`);
  // domains
  const grp=REG.groups.map(gx=>{const ds=M.doms.filter(x=>x.g===gx.n);return `<div class="dgrp"><div class="lbl">${gx.n}. ${esc(gx.name)}</div>${tbl(['Domain','Scope','How','Status','Owner','Evidence'],ds.map(x=>`<tr class="${x.ap?'':'muted'}"><td>${esc(x.name)}</td><td>${esc(x.scope)}</td><td>${x.impl==='modelled'?'Modelled':x.impl==='partial'?'Partly modelled':'Tracked'}</td><td>${chipS(x.st)}${x.ap&&x.impl!=='modelled'?' '+sel(`dom.${x.id}.st`,x.ust,[['','Set'],['progress','In progress'],['done','Done']]):''}</td><td><input data-pset="dom.${x.id}.owner" value="${esc(x.owner)}" placeholder="owner" aria-label="Owner"></td><td><input data-pset="dom.${x.id}.ev" value="${esc(x.ev)}" placeholder="link or note" aria-label="Evidence"></td></tr>`))}</div>`;}).join('');
  const cnt=k=>M.doms.filter(x=>x.st===k).length;
  h+=card('domains','All domains',`<span class="vsub">${M.doms.filter(x=>x.ap).length} apply: ${cnt('pass')} pass, ${cnt('warn')} at risk, ${cnt('fail')} fail, ${cnt('open')+cnt('plan')} open or planned</span>`,grp,'wide');
  // primitives map
  h+=card('prims','Primitives across disciplines','',REG.disciplines.map(dd=>`<div class="lbl">${esc(dd.name)}</div>${tbl(['Layer','Primitives','In Formgraph','How'],dd.layers.map(l=>`<tr><td>${esc(l.layer)}</td><td>${esc(l.items)}</td><td>${chipS(l.impl==='modelled'?'pass':l.impl==='partial'?'plan':'open')}</td><td>${esc(l.how)}</td></tr>`))}`).join('')+'<p class="note">Pass means the page models it, Planned means partly modelled, Open means tracked with an owner and evidence.</p>','wide eng');
  h+='</div>';box.innerHTML=h;
}
function setPath(path,v){const ks=path.split('.');let o=progState();for(let i=0;i<ks.length-1;i++){o[ks[i]]=o[ks[i]]&&typeof o[ks[i]]==='object'?o[ks[i]]:{};o=o[ks[i]];}o[ks[ks.length-1]]=v;}
document.addEventListener('change',e=>{const el=e.target.closest('[data-pset]');if(!el)return;let v=el.type==='checkbox'?(el.dataset.val?(el.checked?el.dataset.val:''):el.checked):el.value;
  if(el.classList.contains('sod')&&el.type==='number')v=clamp(parseInt(v,10)||1,1,10);setPath(el.dataset.pset,v);psetWrite(el.dataset.pset,v);if(el.matches('input:not([type=checkbox]),textarea'))el.blur();schedule();});
$('#progView').addEventListener('click',e=>{const pc=e.target.closest('[data-pcard]');if(pc){const sec=pc.closest('.pcard'),o=!sec.classList.contains('open');sec.classList.toggle('open',o);pc.setAttribute('aria-expanded',String(o));S.open=S.open||{};S.open['p:'+pc.dataset.pcard]=o;save();return;}const p=progState();
  if(e.target.closest('[data-padv]')){const i=PHASES.findIndex(x=>x.id===p.phase);if(i<PHASES.length-1){p.phase=PHASES[i+1].id;toast('Moved to '+PHASES[i+1].name+'.');schedule();}return;}
  if(e.target.closest('[data-ploop]')){const to=$('#loopTo').value,why=$('#loopWhy').value.trim();p.loops.push({from:p.phase,to,why,date:dstr(today())});p.phase=to;toast('Sent back to '+PHASES.find(x=>x.id===to).name+'.');schedule();}});
function applyPview(){const prog=S.pview==='prog';$('main').hidden=prog;$('#progView').hidden=!prog;document.querySelectorAll('[data-pview]').forEach(b=>b.setAttribute('aria-pressed',String((b.dataset.pview==='prog')===prog)));if(prog)renderProgramme();}
document.querySelectorAll('[data-pview]').forEach(b=>b.addEventListener('click',()=>{S.pview=b.dataset.pview;applyPview();save();}));
function progSummary(){const M=PM||progCompute();if(!M)return null;return{phase:S.prog.phase,exit:(M.crit[S.prog.phase]||[]).map(c=>({criterion:c[0],status:c[1],detail:c[2]})),
  ledger:{statements:M.ledger.length,unreviewed:M.unrev},assumptionsUnconfirmed:M.unconf,untestedRequirements:M.untested,
  domains:M.doms.filter(d=>d.ap).map(d=>({id:d.id,name:d.name,status:d.st,owner:d.owner||null})),
  budgets:M.bud.map(b=>({name:b.name,value:b.val,limit:b.lim,status:b.st})),risks:M.risks.slice(0,8).map(r=>({mode:r.mode,rpn:r.rpn,mitigation:r.mit})),
  tests:M.tp.map(t=>({id:t.id,name:t.name,phase:t.phase,status:t.st})),standards:M.std.map(x=>({id:x.id,name:x.name,status:x.st})),
  interfaces:{total:M.ifs.length,frozen:M.ifs.filter(i=>i.frozen).length},tradeoffsWithoutReason:M.tr.filter(t=>t.st!=='pass').map(t=>t.id),
  reliability:{mtbfYears:+fmt(M.rel.years,1),survivalAtLife:+fmt(M.rel.R,3)},tolerance:{cpk:+fmt(M.tol.cpk,2)},modalHz:M.mod?Math.round(M.mod.f):null,
  environment:ENVM?{range:[ENV.opMin,ENV.opMax],storage:[ENV.storeMin,ENV.storeMax],hotAir:+fmt(ENVM.TaHot,1),sun:ENV.sun,altitude:ENV.altitude_m,vibration:ENV.vibration,checks:ENVM.C.filter(c=>c.st!=='pass').map(c=>({check:c.label,value:c.val,limit:c.lim,status:c.st}))}:null,dfaEfficiency:+fmt(M.df.eff,3),carbonKg:M.co?+fmt(M.co.tot,2):null,repairScore:+fmt(M.rp.score,1),security:M.sec.st,schedule:M.sch.items.map(i=>({item:i.name,by:dstr(i.by)}))};}


/* ================= Environment: where the product lives ================= */
const ENVD={opMin:0,opMax:40,storeMin:-20,storeMax:60,humidity:[10,90],condensing:false,sun:'none',altitude_m:1000,salt:false,chemicals:[],uvHours:0,vibration:'fixed',outdoor:false,ventMembrane:false};
const ENV=Object.assign({},ENVD,(PCFG.environment&&PCFG.environment.outdoor)?{outdoor:true}:{},CFG.environment||{});
const MATENV={abs:[-20,80],absfr:[-20,80],pcabsfr:[-30,95],pc:[-40,120],pp:[0,100],al6061:[-100,150],al6082:[-100,150],pom:[-40,100],pa12:[-40,95],pa12gb:[-30,110],pla:[-20,50],petg:[-20,65],petgcf:[-20,70],asa:[-30,85]};
const ALPHA={graphite:0.9,cobalt:0.8,signal:0.6,arctic:0.3};
const VIBP={fixed:{f:0,name:'fixed mount',test:'IEC 60068-2-6 sine sweep 10 to 150 Hz, 1 g',shock:'IEC 60068-2-27, 15 g, 11 ms half-sine'},
  handheld:{f:100,name:'handheld',test:'IEC 60068-2-64 random 5 to 200 Hz, 1 g rms',shock:'IEC 60068-2-27, 30 g, 11 ms half-sine'},
  vehicle:{f:200,name:'vehicle',test:'IEC 60068-2-64 random 10 to 1,000 Hz, 3 g rms',shock:'IEC 60068-2-27, 50 g, 6 ms half-sine'},
  aircraft:{f:500,name:'aircraft',test:'IEC 60068-2-64 random 10 to 2,000 Hz, 5 g rms, or the DO-160 category',shock:'IEC 60068-2-27, 50 g, 6 ms half-sine'}};
let ENVM=null;
const matRange=()=>{const M=matOf();return MATENV[S.mat]||[M.tmin!=null?M.tmin:-20,M.tmax!=null?M.tmax:80];};
const fwInhibit=()=>!!((PCFG.firmware||{}).chargeInhibit);
function altMat(pred){const need=EL&&EL.hasBat;const m=MATS[S.proc].find(x=>x.id!==S.mat&&pred(x)&&(!need||x.metal||(UL[x.ul94]||0)>=2));return m?{act:'mat',mat:m.id,label:'Use '+m.name.split(',')[0]+(m.ul94&&m.ul94!=='HB'&&m.ul94!=='metal'&&m.name.includes('flame')?' FR':'')}:null;}
function envModel(s){
  if(!s)return null;const C=[],ck=o=>C.push(Object.assign({st:'pass',lines:[]},o)),M=matOf(),col=CMF.find(c=>c.id===S.color)||CMF[0];
  const alpha=ALPHA[S.color]||0.7,dSun=({none:0,partial:12,full:25}[ENV.sun]||0)*alpha,p=Math.exp(-(ENV.altitude_m||0)/8434),altF=1/Math.sqrt(p);
  const TaHot=ENV.opMax+dSun,TaCold=ENV.opMin,opW=EL?EL.avgW:0.2,chW=EL&&EL.chgPath?EL.chgW:0;
  const hotOp=thermalAt(s,opW*altF,TaHot),hotCh=chW?thermalAt(s,chW*altF,TaHot):null;
  const sunNote=dSun>0?` plus ${fmt(dSun,0)} °C of sun on a ${col.name.toLowerCase()} body`:'';
  const lightFix=dSun>0&&S.color!=='arctic'?{act:'color',color:'arctic',label:'Light colour'}:null;
  if(hotCh){const bt=hotCh.Ti;ck({id:'chghot',label:'Charging on the hottest day',val:`battery about ${fmt(bt,1)} °C`,lim:`45 °C charge limit, ${fmt(ENV.opMax,0)} °C air${sunNote}`,
    st:bt<=42?'pass':bt<=45?'warn':fwInhibit()?'warn':'fail',fix:lightFix,lines:['batt'],why:bt>45&&fwInhibit()?'charge inhibit holds charging until the cell cools':''});}
  if(EL&&EL.chgPath&&ENV.opMin<0)ck({id:'chgcold',label:'Charging below 0 °C',val:fwInhibit()?'charge inhibit set':'no charge inhibit',lim:'Li-ion must not charge below 0 °C',st:fwInhibit()?'pass':'fail',lines:['batt'],why:fwInhibit()?'':'set programme.firmware.chargeInhibit in the product config'});
  const lim=EL&&EL.hasBat?[55,60,'60 °C battery limit in use']:[65,70,'70 °C for commercial-grade parts'];
  ck({id:'elechot',label:'Electronics on the hottest day',val:`inside about ${fmt(hotOp.Ti,1)} °C`,lim:lim[2],st:hotOp.Ti<=lim[0]?'pass':hotOp.Ti<=lim[1]?'warn':'fail',fix:lightFix,lines:['shell']});
  if(EL&&EL.hasBat&&EL.life){const T=ENV.opMin,cf=T>=20?1:T>=0?0.85+0.15*T/20:T>=-20?0.5+0.35*(T+20)/20:0.4,life=EL.life*cf;
    ck({id:'coldlife',label:`Battery life at ${fmt(T,0)} °C`,val:`about ${fmt(life,1)} days`,lim:`${S.req.life} days required, ${fmt(cf*100,0)}% capacity when cold`,st:life>=S.req.life?'pass':life>=0.8*S.req.life?'warn':'fail',fix:S.batt!=='c18650'?{act:'batt',id:'c18650',label:'Bigger battery'}:null,lines:['batt']});
    if(EL.brown){const B=battOf(),rf=1+Math.max(0,20-T)/15,v=3.6-EL.Ipk/1000*(B.rint*rf+EL.brown.Rw);
      ck({id:'coldbrown',label:`Battery holds up while sending at ${fmt(T,0)} °C`,val:`${fmt(v,2)} V at 20% charge`,lim:`radio needs ${EL.brown.vmin} V`,st:v>=EL.brown.vmin+0.05?'pass':v>=EL.brown.vmin?'warn':'fail',lines:['batt']});}}
  const mr=matRange(),lo=Math.min(ENV.opMin,ENV.storeMin),hiS=Math.max(hotOp.hot+dSun*0.3,ENV.storeMax);
  ck({id:'matcold',label:`${M.name} in the cold`,val:`down to ${fmt(lo,0)} °C`,lim:`impact-tough to ${mr[0]} °C`,st:lo>=mr[0]?'pass':lo>=mr[0]-10?'warn':'fail',fix:lo<mr[0]?altMat(m=>(MATENV[m.id]||[-20])[0]<=lo):null,lines:['mat','shell']});
  ck({id:'mathot',label:`${M.name} in the heat`,val:`up to ${fmt(hiS,0)} °C`,lim:`softens near ${mr[1]} °C`,st:hiS<=mr[1]-15?'pass':hiS<=mr[1]-5?'warn':'fail',fix:hiS>mr[1]-15?altMat(m=>(MATENV[m.id]||[0,80])[1]-15>=hiS):null,lines:['mat','shell']});
  const uv=ENV.uvHours||(ENV.outdoor&&ENV.sun!=='none'?1500:0);
  if(uv>0){const poor=['abs','absfr','pp','pom','pla'].includes(S.mat),fair=['pc','pcabsfr','pa12','pa12gb','petg','petgcf'].includes(S.mat);
    ck({id:'uv',label:'Sunlight (UV)',val:`about ${uv.toLocaleString('en-GB')} h a year`,lim:poor?'this grade yellows and turns brittle':fair?'needs a UV-stabilised grade':M.metal?'metal, unaffected':'check the grade datasheet',
      st:poor?'fail':fair?(uv>2000?'warn':'pass'):'pass',fix:poor?altMat(m=>!['abs','absfr','pp','pom','pla'].includes(m.id)):null,lines:['mat']});}
  if(ENV.salt){if(M.metal)ck({id:'corr',label:'Salt corrosion',val:finishOf().name,lim:'anodised or coated aluminium',st:S.finish==='asm'?'fail':'pass',fix:S.finish==='asm'?{act:'finish',finish:'anod',label:'Anodise'}:null,lines:['shell']});
    ck({id:'corrfix',label:'Fixings in salt air',val:fastOf().name.split(',')[0],lim:'stainless or coated fixings',st:'warn',lines:['lidfix'],why:'specify A2 or A4 stainless when nominating the part number'});}
  if((ENV.chemicals||[]).length){const bad=['pc','pcabsfr'].includes(S.mat)&&ENV.chemicals.some(c=>/solvent|alcohol|clean|fuel|oil/i.test(c));
    ck({id:'chem',label:'Chemical exposure',val:ENV.chemicals.join(', '),lim:bad?'polycarbonate blends can stress-crack':'check the grade chemical resistance',st:bad?'warn':'pass',lines:['mat']});}
  const sealed=!(s.slots&&s.slots.n)||S.req.ip!=='none',swing=ENV.opMax-ENV.opMin,wet=ENV.condensing||((ENV.humidity||[0,0])[1]>=85&&swing>=20);
  if(wet)ck({id:'cond',label:'Condensation inside',val:sealed?'sealed body with a temperature swing':'vented body',lim:'vent membrane or open venting',st:!sealed||ENV.ventMembrane?'pass':'warn',lines:['shell'],why:ENV.ventMembrane?'vent membrane fitted':sealed?'set environment.ventMembrane to fit an ePTFE vent':''});
  if(ENV.condensing&&EL&&boardOf().elec)ck({id:'coat',label:'Board protection against moisture',val:'condensing environment',lim:'conformal coating on the board',st:'warn',lines:['board'],why:'add conformal coating to the PCB assembly specification'});
  const alt=ENV.altitude_m||0,aF=alt>5000?1.48:alt>4000?1.29+0.19*(alt-4000)/1000:alt>3000?1.14+0.15*(alt-3000)/1000:alt>2000?1+0.14*(alt-2000)/1000:1;
  ck({id:'alt',label:'Altitude',val:`${alt.toLocaleString('en-GB')} m`,lim:aF>1?`clearances x${fmt(aF,2)}, cooling about ${fmt((1-Math.sqrt(p))*100,0)}% weaker`:'within the 2,000 m insulation tables',st:alt<=2000?'pass':alt<=4000?'warn':'fail'});
  const VIB=VIBP[ENV.vibration]||VIBP.fixed,md=s.t?modal(s):null;
  if(VIB.f>0&&md)ck({id:'vib',label:`Vibration, ${VIB.name}`,val:`panel first mode ${fmt(md.f,0)} Hz`,lim:`above ${2*VIB.f} Hz, twice the ${VIB.f} Hz band`,st:md.f>=2*VIB.f?'pass':md.f>=VIB.f?'warn':'fail',lines:['shell']});
  if(['vehicle','aircraft'].includes(ENV.vibration)&&EL&&EL.cables.some(c=>/SH 1\.0/.test(c.fam)))ck({id:'vibconn',label:'Connectors under vibration',val:'friction-fit 1.0 mm connectors',lim:'positive-locking connectors',st:'warn',lines:['harness']});
  if(EL&&EL.hasBat&&ENV.storeMax>45)ck({id:'store',label:'Battery in storage',val:`up to ${ENV.storeMax} °C`,lim:'45 °C for long storage, 60 °C for under a month',st:ENV.storeMax<=60?'warn':'fail',lines:['batt']});
  const st=C.some(c=>c.st==='fail')?'fail':C.some(c=>c.st==='warn')?'warn':'pass';
  return{C,TaHot,TaCold,dSun,alpha,altF,p,aF,VIB,hotOp,hotCh,st};
}
function envCond(id){if(!ENVM)return'';const E=ENV,cold=E.opMin<0;return({
  t_temp:`Cold IEC 60068-2-1 at ${E.opMin} °C, dry heat IEC 60068-2-2 at ${Math.round(ENVM.TaHot)} °C, change of temperature IEC 60068-2-14 from ${E.storeMin} to ${E.storeMax} °C`,
  t_hum:E.condensing?'Cyclic damp heat IEC 60068-2-30, 25 to 55 °C, 95% RH':'Steady damp heat IEC 60068-2-78, 40 °C, 93% RH',
  t_salt:'Salt mist IEC 60068-2-52',t_vib:ENVM.VIB.test,t_shock:ENVM.VIB.shock,t_solar:'Solar radiation IEC 60068-2-5, 1,120 W/m²',t_uv:'UV exposure ISO 4892-2, about 1,000 h',
  t_lowpress:`Low pressure IEC 60068-2-13 at ${E.altitude_m} m`,t_chgwin:'Charge at 0 and 45 °C cell temperature, confirm the inhibit outside that window',
  t_ingress:S.req.ip!=='none'?`IEC 60529 ${S.req.ip.toUpperCase()}`:'',t_drop:`${S.req.drop} m onto concrete, IEC 60068-2-31${cold?`, repeat at ${E.opMin} °C`:''}`,
  t_charge:`Charge at ${Math.round(ENVM.TaHot)} °C air`}[id]||'');}
function renderEnv(){
  const sum=$('#envSum'),box=$('#envList');if(!sum)return;if(!ENVM){sum.textContent='';box.innerHTML='';return;}
  const E=ENV,col=CMF.find(c=>c.id===S.color)||CMF[0],h=E.humidity||[0,0];
  sum.innerHTML=`Works from ${E.opMin} to ${E.opMax} °C, stored from ${E.storeMin} to ${E.storeMax} °C. ${E.sun==='none'?'No direct sun.':`${E.sun==='full'?'Full':'Partial'} sun adds about ${fmt(ENVM.dSun,0)} °C to a ${esc(col.name.toLowerCase())} body.`} ${h[0]} to ${h[1]}% RH${E.condensing?', condensing':''}. ${(E.altitude_m||0).toLocaleString('en-GB')} m. ${esc(ENVM.VIB.name.charAt(0).toUpperCase()+ENVM.VIB.name.slice(1))}.${E.salt?' Salt air.':''}${E.ventMembrane?' Vent membrane fitted.':''}`;
  box.innerHTML=ENVM.C.map((c,i)=>`<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div>${c.why&&c.st!=='pass'?`<div class="rv">${esc(c.why)}</div>`:''}</div>${c.fix&&c.st!=='pass'?`<button class="fix" type="button" data-vfix="${i}">${esc(c.fix.label)}</button>`:'<span></span>'}</li>`).join('');
}
$('#envList').addEventListener('click',e=>{const b=e.target.closest('[data-vfix]');if(b&&ENVM)applyFix(ENVM.C[+b.dataset.vfix].fix);});
function creepageEnv(c){if(!ENVM||ENVM.aF<=1)return c;return Object.assign({},c,{cl:+(c.cl*ENVM.aF).toFixed(2),note:c.note+` Clearance raised x${fmt(ENVM.aF,2)} for ${ENV.altitude_m} m altitude.`,st:c.st==='pass'?'pass':c.st});}

/* ================= Clean layout: collapsible sections, short lists, menus ================= */
const ACC_OPEN={'Readiness':true};const openLists=new Set();
function accInit(){
  S.open=S.open||{};
  document.querySelectorAll('#dfm > section.blk').forEach(sec=>{
    if(sec.dataset.acc)return;const lbl=sec.querySelector(':scope > .lbl');const title=lbl?lbl.textContent.trim():'Details';sec.dataset.acc=title;
    const head=document.createElement('button');head.type='button';head.className='acc-h';
    head.innerHTML=`<span class="acc-t">${esc(title)}</span><span class="acc-s"><i class="sdot"></i><span></span></span><span class="acc-c" aria-hidden="true"></span>`;
    const body=document.createElement('div');body.className='acc-b';[...sec.childNodes].forEach(n=>{if(n!==lbl)body.appendChild(n);});if(lbl)lbl.remove();sec.append(head,body);
    const o=S.open[title]!=null?S.open[title]:!!ACC_OPEN[title];sec.classList.toggle('open',o);head.setAttribute('aria-expanded',String(o));
    head.addEventListener('click',()=>{const v=!sec.classList.contains('open');sec.classList.toggle('open',v);head.setAttribute('aria-expanded',String(v));S.open[title]=v;save();if(v)requestAnimationFrame(clampLists);});});
}
function accSummaries(){
  const s=last&&last.shape,set=(title,txt,st)=>{const sec=document.querySelector(`#dfm > section[data-acc="${title}"]`);if(!sec)return;const a=sec.querySelector('.acc-s');a.querySelector('span').textContent=txt||'';a.querySelector('.sdot').className='sdot'+(st?' s-'+st:'');};
  const w=a=>a.some(x=>x==='fail')?'fail':a.some(x=>x==='warn')?'warn':a.length?'pass':'';
  const g=gateState(),cur=g.find(x=>!x.ok);set('Readiness',cur?`Gate ${cur.n}: ${cur.msg}`:'All four gates pass',cur?(cur.n<=2?'warn':'open'):'pass');
  const act=REQ.filter(r=>r.st!=='na'),met=act.filter(r=>r.st==='pass').length;set('Requirements',AUTHORED?'not assessed':`${met} of ${act.length} met`,AUTHORED?'':w(REQ.filter(r=>r.pri==='Must').map(r=>r.st)));
  const run=Object.values(JOBS).filter(j=>j.status==='running'||j.status==='queued').length;set('Validation in the background',run?`${run} running`:'Up to date',run?'open':'pass');
  set('Manufacturing process',`${PROC[S.proc].short} · ${matOf().name.split(',')[0]} · ${S.qty.toLocaleString('en-GB')} units`,'');
  set('What it does and what goes inside',`${boardOf().name} · ${battOf().mass?battOf().name.split(',')[0]:'no battery'} · ${G.nodes.filter(n=>EB[n.type]&&EB[n.type].func).length} functions`,'');
  if(ENVM){const n=ENVM.C.filter(c=>c.st!=='pass').length;set('Where it lives',`${ENV.opMin} to ${ENV.opMax} °C${n?` · ${n} to look at`:''}`,ENVM.st);}
  if(last&&last.rep){const f=last.rep.rules.filter(r=>r.st==='fail').length,wn=last.rep.rules.filter(r=>r.st==='warn').length;set('Manufacturing verdict',f?`${f} blocking`:wn?`${wn} notes`:'Ready',f?'fail':wn?'warn':'pass');
    set('Checks',`${last.rep.rules.filter(r=>r.st==='pass').length} of ${last.rep.rules.length} pass`,f?'fail':wn?'warn':'pass');}
  if(simRes){const ks=Object.keys(SIMS),p=ks.filter(k=>simRes[k].st==='pass').length;set('Product tests',`${p} of ${ks.length} pass`,w(ks.map(k=>simRes[k].st).filter(x=>x!=='na')));}
  if(EL){const f=EL.checks.filter(c=>c.st==='fail').length,wn=EL.checks.filter(c=>c.st==='warn').length;set('Electronics and wiring',f?`${f} problems`:wn?`${wn} notes`:'All connected',f?'fail':wn?'warn':'pass');}
  else set('Electronics and wiring','None','');
  if(last&&last.bom){const L=last.bom.lines,v=L.filter(l=>l.status==='verified').length,c=L.some(l=>l.status==='conflict');set('Bill of materials',`${v} of ${L.length} verified`,c?'fail':v<L.length?'warn':'pass');}
  if(s)set('Part',`${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm`,'');
  if(s){const C=costs(s);set('Unit cost by process',`${gbp(C[S.proc].at(S.qty))} each at ${S.qty.toLocaleString('en-GB')}`,'');}
  if(CTX&&MFGP.length&&last&&last.shape){const R=mfgEval(last.shape),f=R.filter(r=>!r.pick||r.pick.N.length).length,tot=R.reduce((a,r)=>a+(r.pick?r.pick.cost*(r.p.qty||1):0),0);set('Made parts',`${R.length} parts · ${gbp(tot)} a set · ${mfgSuppliers(last.shape).length} suppliers`,f?'fail':'pass');}
  if(AF&&AFG){const f=AFC.filter(c=>c.st==='fail').length,wn=AFC.filter(c=>c.st==='warn').length;set('Airframe and CAD',`${fmt(AFG.span,0)} mm across the props · ${AFG.rotors} rotors`,f?'fail':wn?'warn':'pass');}
  if(VIEW&&VIEW.keyQuestions){const st=VIEW.keyQuestions.map(q=>{const r=srcVal(q.source);return r.st||(typeof r.v==='number'&&q.limit!=null?(cmpOp(r.v,q.op||'>=',q.limit)?'pass':'fail'):'open');});set('Key questions',`${st.filter(x=>x==='pass').length} of ${st.length} answered yes`,st.includes('fail')?'fail':st.includes('open')||st.includes('warn')?'warn':'pass');}
  if(VIEW&&VIEW.tryit==='mission'){const R=misCalc();if(R)set('Mission',`${fmt(R.left,0)} % left after ${fmt(R.T,0)} min`,R.left>=R.reserve?'pass':'fail');}
  if(CTX&&REFS.length){const f=REFC.filter(c=>c.st==='fail').length,wn=REFC.filter(c=>c.st==='warn').length;set('References',`${REFS.length} reference${REFS.length>1?'s':''}${REFC.length?` · ${REFC.filter(c=>c.st==='pass').length} of ${REFC.length} match`:''}`,f?'fail':wn?'warn':REFC.length?'pass':'');}
  if(CTX){if(OPTS.length)set('Options',`${OPTS.length} decisions · ${OPTS.filter(d=>(S.picks[d.id]||d.pick)!==d.pick).length} changed`,'');
    if(DFA){const f=DFA.C.filter(c=>c.st==='fail').length,wn=DFA.C.filter(c=>c.st==='warn').length;set('Assembly',`DFA index ${fmt(DFA.idx*100,0)} % · ${fmt(DFA.T,0)} s`,f?'fail':wn?'warn':'pass');}
    const sc=scenList(),sr=sc.map(x=>scenEval(x).st);set('Models and scenarios',`${sr.filter(x=>x==='pass').length} of ${sc.length} scenarios pass${MCR.status==='running'?' · tier 2 running':''}`,sr.includes('fail')?'fail':sr.includes('warn')||sr.includes('open')?'warn':sc.length?'pass':'');
    const pe=pendingEdits().length;set('Context',`${(CTX.parts||[]).length} parts · ${MACH.length} machines${pe?` · ${pe} edits to sync`:''}`,pe?'open':'');}
  set('Hand off',S.revs.length?`Rev ${S.revs[S.revs.length-1].rev} released`:'Not released','');
}
function clampLists(){
  const simple=document.documentElement.getAttribute('data-level')==='simple';
  document.querySelectorAll('#dfm ul.rlist, #dfm #rules, #dfm #reqList, #dfm ul.bl, #dfm #testList').forEach((ul,i)=>{
    const key=ul.id||('l'+i);ul.querySelectorAll(':scope > li.more-li').forEach(x=>x.remove());ul.querySelectorAll(':scope > li.clip').forEach(x=>x.classList.remove('clip'));
    if(openLists.has(key))return;const hidesPass=simple&&(ul.id==='rules'||ul.classList.contains('rlist'));
    const vis=[...ul.children].filter(li=>!(hidesPass&&li.classList.contains('st-pass')));const max=ul.id==='reqList'?5:4;if(vis.length<=max+1)return;
    vis.slice(max).forEach(li=>li.classList.add('clip'));const m=document.createElement('li');m.className='more-li';
    m.innerHTML=`<button type="button" class="pill" data-unclamp="${key}">Show ${vis.length-max} more<span class="acc-c" aria-hidden="true"></span></button>`;ul.appendChild(m);});
}
document.addEventListener('click',e=>{const u=e.target.closest('[data-unclamp]');if(u){openLists.add(u.dataset.unclamp);clampLists();return;}
  if(e.target.closest('.menu')&&!e.target.closest('.mi'))return;
  const mb=e.target.closest('.more > button');document.querySelectorAll('.more').forEach(m=>{const open=mb&&m.contains(mb)?m.querySelector('.menu').hidden:false;m.querySelector('.menu').hidden=!open;m.querySelector(':scope > button').setAttribute('aria-expanded',String(open));});
  if(e.target.closest('.menu .mi')){document.querySelectorAll('.more .menu').forEach(x=>x.hidden=true);}});
function renderAcc(){accSummaries();clampLists();}

/* ================= Minimisable sides ================= */
function applySides(){S.sides=S.sides||{};if(HOSTED)S.sides={};const m=$('main'),L=!!S.sides.l,R=!!S.sides.r;m.classList.toggle('hideL',L);m.classList.toggle('hideR',R);
  const el=$('#edgeL'),er=$('#edgeR');el.classList.toggle('flip',L);er.classList.toggle('flip',R);
  el.setAttribute('aria-label',L?'Show controls':'Hide controls');el.title=(L?'Show':'Hide')+' controls ( [ )';
  er.setAttribute('aria-label',R?'Show panel':'Hide panel');er.title=(R?'Show':'Hide')+' panel ( ] )';
  setTimeout(()=>{resize();if(!L&&S.level==='eng')drawWires();},230);}
function toggleSide(k){if(HOSTED)return;S.sides=S.sides||{};S.sides[k]=!S.sides[k];applySides();save();}
$('#edgeL').addEventListener('click',()=>toggleSide('l'));$('#edgeR').addEventListener('click',()=>toggleSide('r'));
document.addEventListener('keydown',e=>{const tg=e.target&&e.target.closest?e.target:null;if(e.ctrlKey||e.metaKey||e.altKey||(tg&&tg.closest('input,select,textarea,[contenteditable]')))return;if(e.key==='['){e.preventDefault();toggleSide('l');}else if(e.key===']'){e.preventDefault();toggleSide('r');}});

/* ================= Virtual device: 3D animation ================= */
const matBtnCap=new THREE.MeshStandardMaterial({color:new THREE.Color('#23272E').convertSRGBToLinear(),roughness:.4,metalness:.2});
const FX={rings:null,plug:null,keyBase:null};
function devSegs(){document.querySelectorAll('#dSpeedSeg [data-speed]').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.speed===dev.speed)));
  document.querySelectorAll('#dCovSeg [data-cov]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.cov===dev.cov)));}
function fxRings(){if(FX.rings)return FX.rings;const g=new THREE.Group();
  for(let i=0;i<3;i++){const m=new THREE.Mesh(new THREE.RingGeometry(0.88,1,56),new THREE.MeshBasicMaterial({color:new THREE.Color('#3BFF7A'),transparent:true,opacity:0,side:THREE.DoubleSide,depthWrite:false}));m.rotation.x=-Math.PI/2;g.add(m);}
  g.visible=false;FX.rings=g;return g;}
function fxPlug(){if(FX.plug)return FX.plug;const g=new THREE.Group(),metal=new THREE.MeshStandardMaterial({color:0xb8bcc4,metalness:.9,roughness:.25}),body=new THREE.MeshStandardMaterial({color:new THREE.Color('#1C1F24').convertSRGBToLinear(),roughness:.6});
  const tip=new THREE.Mesh(new THREE.BoxGeometry(6.5,2.4,8.2),metal);tip.position.x=-3.25;const over=new THREE.Mesh(new THREE.BoxGeometry(14,5.5,11),body);over.position.x=7;
  const cab=new THREE.Mesh(new THREE.CylinderGeometry(1.8,1.8,40,16),body);cab.rotation.z=Math.PI/2;cab.position.x=34;[tip,over,cab].forEach(m=>{m.castShadow=true;g.add(m);});g.visible=false;FX.plug=g;return g;}
function devFx(now){
  const s=last&&last.shape;if(!s)return;
  const st=dev.low?'low':dev.state,charging=dev.usb&&EL&&EL.chgPath&&dev.soc<1;let col='#3BFF7A',I=0;
  if(dev.flash>0){I=2.2;}else if(st==='transmit'&&EL&&EL.radioOK){I=Math.floor(now/120)%2?2.2:0.15;}else if(dev.low){col='#FF4040';I=now%1500<180?2.2:0;}
  else if(charging){col='#FFB020';I=0.7+0.6*Math.sin(now/350);}else if(st==='measure'||st==='locate'){col='#5AA9FF';I=1.3;}else{I=now%5000<90?0.9:0;}
  matLed.emissive.set(col);matLed.emissiveIntensity=EL&&EL.ledOK?I:0;
  const cap=part.children.find(m=>m.material===matBtnCap);
  if(cap){if(cap.userData.by==null)cap.userData.by=cap.position.y;const dt=now-(dev.pressAt||-1e9),k=dt<0?0:dt<90?dt/90:dt<240?1-(dt-90)/150:0;cap.position.y=cap.userData.by-0.9*Math.max(0,k);}
  const R=fxRings();if(R.parent!==part)part.add(R);const ant=EL&&EL.parts.find(p=>p.type==='e_ant');R.visible=!!(st==='transmit'&&EL&&EL.radioOK&&ant);
  if(R.visible){R.position.set(ant.cx,s.H+0.6,ant.cz);const span=Math.max(s.W,s.D)*0.5;R.children.forEach((m,i)=>{const ph=(now/1100+i/3)%1,r=3+ph*span;m.scale.set(r,r,r);m.material.opacity=0.6*(1-ph)*(1-ph);});}
  const P=fxPlug();if(P.parent!==part)part.add(P);const L=EL?layout(s):null,port=L&&L.port;
  if(port&&hasUSB()){const tan=Math.tan(s.A*DEG),xo=s.W/2-(port.y*tan+filletZ(port.y,s.H,s.F));if(dev.usb!==dev.usbShown){dev.usbShown=dev.usb;dev.usbAt=now;}
    const e=Math.min(1,(now-(dev.usbAt||0))/380);let x;if(dev.usb){x=xo+Math.pow(1-e,3)*20;P.visible=true;}else{x=xo+e*20;P.visible=e<1&&dev.usbAt>0;}P.position.set(x,port.y,0);}
  else P.visible=false;
  if(FX.keyBase==null)FX.keyBase=key.intensity;
  if(dev.play){const hr=(dev.t%86400)/3600,day=Math.max(0,Math.sin(Math.PI*(hr-6)/12));key.intensity=FX.keyBase*(0.3+0.7*day);}
}
function devMin(v){const p=$('#devp');p.classList.toggle('min',v);const b=$('#dMin');b.textContent=v?'+':'–';b.setAttribute('aria-label',v?'Expand':'Minimise');b.title=v?'Expand':'Minimise';}
$('#dMin').addEventListener('click',e=>{e.stopPropagation();devMin(!$('#devp').classList.contains('min'));});
$('#devp').addEventListener('click',e=>{const p=$('#devp');if(p.classList.contains('min')&&!e.target.closest('#devClose,#dMin'))devMin(false);});
function devFxOff(){if(FX.keyBase!=null)key.intensity=FX.keyBase;if(FX.rings)FX.rings.visible=false;if(FX.plug)FX.plug.visible=false;dev.usbShown=undefined;}

/* ================= Press the model ================= */
const ray=new THREE.Raycaster(),ndc=new THREE.Vector2(),cvs=renderer.domElement;let pickDown=null,lastHover=0;
const mtip=document.createElement('div');mtip.className='mtip';mtip.hidden=true;host.appendChild(mtip);
const isChildOf=(o,g)=>{while(o){if(o===g)return true;o=o.parent;}return false;};
function pickAt(cx,cy){
  const s=last&&last.shape;if(!s)return null;const r=cvs.getBoundingClientRect();ndc.set((cx-r.left)/r.width*2-1,-((cy-r.top)/r.height)*2+1);ray.setFromCamera(ndc,camera);
  const hits=ray.intersectObjects(part.children,true).filter(h=>h.object.visible&&!(FX.rings&&isChildOf(h.object,FX.rings)));if(!hits.length)return null;const h=hits[0];
  if(h.object.material===matBtnCap)return'button';
  if(FX.plug&&FX.plug.visible&&isChildOf(h.object,FX.plug))return'usb';
  const lp=part.worldToLocal(h.point.clone()),L=EL?layout(s):null;
  if(L&&L.port&&hasUSB()){const tan=Math.tan(s.A*DEG),xo=s.W/2-(L.port.y*tan+filletZ(L.port.y,s.H,s.F));if(Math.abs(lp.x-xo)<4&&Math.abs(lp.y-L.port.y)<5&&Math.abs(lp.z)<8)return'usb';}
  const bp=EL&&EL.parts.find(q=>q.type==='e_btn');if(bp&&Math.hypot(lp.x-bp.cx,lp.z-bp.cz)<4.5&&lp.y>s.H-1.5)return'button';
  return null;}
function pressModel(k){if(!dev.open)openDevice();if(k==='button')$('#dBtn').click();else if(k==='usb'&&!$('#dUsb').disabled)$('#dUsb').click();}
cvs.addEventListener('pointerdown',e=>{pickDown={x:e.clientX,y:e.clientY,t:performance.now()};});
cvs.addEventListener('pointerup',e=>{if(!pickDown)return;const d=Math.hypot(e.clientX-pickDown.x,e.clientY-pickDown.y),dt=performance.now()-pickDown.t;pickDown=null;if(d>5||dt>500)return;const k=pickAt(e.clientX,e.clientY);if(k)pressModel(k);});
cvs.addEventListener('pointermove',e=>{if(e.buttons){mtip.hidden=true;return;}const now=performance.now();if(now-lastHover<60)return;lastHover=now;const k=pickAt(e.clientX,e.clientY);
  cvs.style.cursor=k?'pointer':'';if(k){const r=host.getBoundingClientRect();mtip.textContent=k==='button'?'Press the button':dev.usb?'Unplug USB-C':'Plug in USB-C';mtip.style.left=(e.clientX-r.left+14)+'px';mtip.style.top=(e.clientY-r.top+14)+'px';mtip.hidden=false;}else mtip.hidden=true;
  if(k==='button'){matBtnCap.emissive.set(css('--accent'));matBtnCap.emissiveIntensity=0.35;}else matBtnCap.emissiveIntensity=0;});
cvs.addEventListener('pointerleave',()=>{mtip.hidden=true;cvs.style.cursor='';matBtnCap.emissiveIntensity=0;});
window.__formgraphPick=(x,y)=>pickAt(x,y);

/* ================= Context: options, assembly, models, scenarios, edits, shared record (formgraph@2) ================= */
const EB_BASE=EB_PRE,OPTS=CTX?(CTX.options||[]):[],CMP=CTX?(CTX.compiled||{}):{},CPART={};
if(CTX)(CTX.parts||[]).forEach(p=>CPART[p.id]=p);
S.picks=S.picks||{};S.userScen=S.userScen||[];S.edits=S.edits||[];
let DFA=null,OBS=null,DBH=null,SHARED_SCN=null,SHARED_EDITS=null,MODEL_OVR={};const DBQ=[];
function rebuildType(t){const b=EB[t];if(!b)return;
  TYPES[t]={cat:'elec',dom:'e',title:b.name,inputs:b.ins.map(([k,tt])=>({k,label:PL[k]||k,t:tt})),outputs:(b.outs||[]).map(([k,tt])=>({k,label:PL[k]||k,t:tt})),compute:()=>({})};
  const has=(id,k,dir)=>{const n=G.nodes.find(m=>m.id===id);if(!n||!TYPES[n.type])return true;return(dir==='in'?TYPES[n.type].inputs:TYPES[n.type].outputs).some(x=>x.k===k);};
  G.links=G.links.filter(l=>has(l.from[0],l.from[1],'out')&&has(l.to[0],l.to[1],'in'));}
function applyPick(d,id,quiet){
  if(d.kind==='part'){const c=CMP[id];if(!c)return;
    if(c.kind==='block'){EB[c.type]=Object.assign({},EB_BASE[c.type]||{},c.block);rebuildType(c.type);}
    else if(c.kind==='board'){BOARDS[c.id]=c.board;EB.e_board=Object.assign({},EB_BASE.e_board,c.ports||{});rebuildType('e_board');S.board=c.id;}
    else if(c.kind==='battery'){BATTS[c.id]=c.batt;S.batt=c.id;}}
  else if(d.kind==='machine'){const m=MACH.find(x=>x.id===id);if(!m)return;S.machine=id;S.proc=m.process;}
  else if(d.kind==='material')S.mat=id;else if(d.kind==='fixing')S.fast=id;
  S.picks[d.id]=id;recomputeOverrides();
  if(!quiet){autoWire(null);renderNodes();renderComp();renderProcess();schedule();}}
function snapNow(){const L=last&&last.bom?last.bom.lines:[];const rf=last&&last.rep?last.rep.rules.filter(r=>r.st==='fail').length:0,ef=EL?EL.checks.filter(c=>c.st==='fail').length:0,s=last&&last.shape;
  return{cost:L.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0),mass:L.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0),fails:rf+ef+(DFA?DFA.C.filter(c=>c.st==='fail').length:0),size:s?`${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm`:''};}
function userPick(d,id){if((S.picks[d.id]||d.pick)===id)return;OBS={what:`${d.label||d.id}: ${optName(d,id)}`,before:snapNow(),after:null};applyPick(d,id,false);dbWrite('choices',d.id,{pick:id,at:new Date().toISOString()});}
function optName(d,id){if(d.kind==='variant'){const v=(d.variants||[]).find(x=>x.id===id);return v?v.label:id;}if(d.kind==='part'){const p=CPART[id];return p?p.name:id;}if(d.kind==='machine'){const m=MACH.find(x=>x.id===id);return m?m.name:id;}
  if(d.kind==='material'){const m=Object.values(MATS).flat().find(x=>x.id===id);return m?m.name:id;}if(d.kind==='fixing'){const f=FASTS[id];return f?f.name:id;}return id;}
function machineScore(m,s){const L=m.limits||{},N=[];const base=(PROC0[m.process]&&PROC0[m.process].base)||m.process,C=s?costs0(s):null;
  const cost=C&&C[base]?C[base].at(S.qty)*(m.costFactor||1):NaN;if(!s)return{ok:true,N,cost};
  const bv=(m.buildVolume||[1e9,1e9,1e9]).slice().sort((a,b)=>b-a),dm=[s.W,s.D,s.H].sort((a,b)=>b-a);
  if(dm.some((v,i)=>v>bv[i]))N.push('too big for its build volume');
  if(L.wallMin&&(s.t||0)<L.wallMin-1e-6)N.push(`wall under its ${L.wallMin} mm minimum`);
  if(s.bosses&&s.bosses.n&&L.holeMin&&s.bosses.d<L.holeMin-1e-6)N.push(`screw holes under ${L.holeMin} mm`);
  if(s.slots&&s.slots.n&&L.slotMin&&s.slots.w<L.slotMin-1e-6)N.push(`slots under ${L.slotMin} mm`);
  if(L.draft&&s.A<L.draft-1e-6)N.push(`draft under ${L.draft}°`);
  const q=m.qtyRange;if(q&&(S.qty<q[0]||S.qty>q[1]))N.push(`makes ${q[0].toLocaleString('en-GB')} to ${q[1].toLocaleString('en-GB')} units`);
  if(m.materials&&m.process===S.proc&&!m.materials.includes(S.mat))N.push('does not offer this material');
  return{ok:!N.length,N,cost};}
function srcLabel(x){if(!x)return'';if(x==='user')return'your registry';if(x==='default')return'typical capability';try{return new URL(x).hostname.replace(/^www\./,'');}catch(e){return String(x);}}
function candHtml(d,id,on,s){let t=id,sub=[],note='',link='',dot='';
  if(d.kind==='variant'){const v=(d.variants||[]).find(x=>x.id===id)||{};t=v.label||id;sub.push(...Object.entries(v.set||{}).map(([k,x])=>`${k.split('.')[1]} ${fmt(x,x%1?2:0)}`));const pv=previewFor(v.set);
    return`<div class="ocw"><button type="button" class="ocand${on?' on':''}" data-dec="${esc(d.id)}" data-pick="${esc(id)}" aria-pressed="${on}"><span class="ot">${esc(t)}${pv.dot?`<i class="sdot s-${pv.dot}"></i>`:''}</span><span class="os">${esc(sub.join(' · '))}</span>${pv.html}${v.source?`<span class="os">${esc(v.source)}</span>`:''}</button><span></span></div>`;}
  if(d.kind==='part'&&(CPART[id]||{}).sets){const pv=previewFor(CPART[id].sets);note+=pv.html;}
  if(d.kind==='part'){const p=CPART[id]||{},c=CMP[id]||{},b=c.block||c.board||c.batt||{};t=p.name||id;
    sub.push(p.excluded?'excluded, typical stand-in':`${p.manufacturer||''} ${p.mpn||''}`.trim());
    if(p.cost&&p.cost.unit!=null)sub.push(gbp(p.cost.unit));if(p.mass!=null)sub.push(fmt(p.mass,1)+' g');
    const g=p.geometry&&p.geometry.envelope;if(g)sub.push(g.map(v=>fmt(v,0)).join(' × ')+' mm');
    const bus=[...new Set((p.interfaces||[]).filter(f=>f.bus!=='pwr').map(f=>f.bus.toUpperCase()))];if(bus.length)sub.push(bus.join(', '));
    if(b.cur){const v=Object.values(b.cur).filter(x=>typeof x==='number'),pk=v.length?Math.max(...v):0;if(pk)sub.push(fmt(pk,pk<1?2:0)+' mA peak');}
    if(p.retrieved)sub.push('read '+p.retrieved);if(!p.geometry)note='<span class="on2">no drawing, shown as a box</span>';
    if(p.excluded)dot='warn';if(p.datasheet)link=`<a class="olink" href="${esc(p.datasheet)}" target="_blank" rel="noopener">datasheet</a>`;}
  else if(d.kind==='machine'){const m=MACH.find(x=>x.id===id)||{};t=m.name||id;const r=machineScore(m,s);
    sub.push(`${(PROC0[m.process]||{}).short||m.process}`,srcLabel(m.source));if(isFinite(r.cost))sub.push(gbp(r.cost)+' a shell');if(m.leadDays)sub.push(m.leadDays+' days');
    const L=m.limits||{};if(L.wallMin!=null)sub.push(`wall from ${L.wallMin} mm`);if(L.tolerance!=null)sub.push(`±${L.tolerance} mm`);
    note=r.ok?'<span class="ok2">Can make this design</span>':`<span class="on2">${esc(r.N.join(', '))}</span>`;dot=r.ok?'pass':'fail';
    if(/^https?:/.test(m.source||''))link=`<a class="olink" href="${esc(m.source)}" target="_blank" rel="noopener">rules</a>`;}
  else if(d.kind==='material'){const m=(MATS[S.proc]||[]).find(x=>x.id===id)||Object.values(MATS).flat().find(x=>x.id===id)||{};t=m.name||id;
    sub.push(m.ul94==='metal'?'non-combustible':'UL94 '+(m.ul94||'?'));if(m.costKg)sub.push(gbp(m.costKg)+'/kg');if(!(MATS[S.proc]||[]).some(x=>x.id===id)){note='<span class="on2">not offered for this process</span>';dot='fail';}}
  else if(d.kind==='fixing'){const f=FASTS[id]||{};t=f.name||id;if(f.cost)sub.push(gbp(f.cost)+' each');if(f.procs&&!f.procs.includes(S.proc)){note='<span class="on2">not for this process</span>';dot='fail';}}
  const ex=d.kind==='part'&&!(CPART[id]||{}).excluded&&!pendingEdits().some(e=>e.kind==='exclude'&&e.part===id)?`<button class="olink" type="button" data-excl="${esc(id)}" style="all:unset;cursor:pointer;font-size:11px;color:var(--muted)">exclude</button>`:'';
  return`<div class="ocw"><button type="button" class="ocand${on?' on':''}" data-dec="${esc(d.id)}" data-pick="${esc(id)}" aria-pressed="${on}"><span class="ot">${esc(t)}${dot?`<i class="sdot s-${dot}"></i>`:''}</span><span class="os">${esc(sub.filter(Boolean).join(' · '))}</span>${note}</button><div style="display:grid;gap:2px">${link}${ex}</div></div>`;}
function obsHtml(){const a=OBS.before,b=OBS.after,dd=(x,u,f)=>{const v=x;return v===0?`no change`:`<span class="${v>0?'up':'dn'}">${v>0?'+':''}${f(v)}${u}</span>`;};
  return`<div class="obs"><b>${esc(OBS.what)}</b><br>cost per set ${dd(b.cost-a.cost,'',v=>gbp(Math.abs(v)).replace('£',v<0?'-£':'£'))} · mass ${dd(Math.round(b.mass-a.mass),' g',v=>v)} · problems ${a.fails} to ${b.fails}${a.size!==b.size?` · size now ${esc(b.size)}`:''}</div>`;}
function renderOptions(){const box=$('#optBody');if(!box)return;const sec=$('#optBlk');sec.hidden=!(CTX&&OPTS.length);if(sec.hidden)return;const s=last&&last.shape;
  if(OBS&&!OBS.after&&last)OBS.after=snapNow();
  box.innerHTML=(OBS&&OBS.after?obsHtml():'')+OPTS.map(d=>{const cur=S.picks[d.id]||d.pick;const cands=d.kind==='machine'?[...new Set([...d.candidates,...MACH.filter(m=>m.pending).map(m=>m.id)])]:d.candidates;
    return`<details class="opt" data-od="${esc(d.id)}"${S.open&&S.open['o:'+d.id]?' open':''}><summary><span>${esc(d.label||d.id)}</span><b>${esc(optName(d,cur))}</b></summary><div class="ocands">${cands.map(id=>candHtml(d,id,id===cur,s)).join('')}</div>${d.why?`<p class="note">Suggested: ${esc(optName(d,d.pick))}. ${esc(d.why)}</p>`:''}</details>`;}).join('');}
$('#optBody').addEventListener('click',e=>{const x=e.target.closest('[data-excl]');if(x){addEdit({kind:'exclude',part:x.dataset.excl,note:'Excluded on the page'});return;}
  const b=e.target.closest('[data-pick]');if(!b)return;const d=OPTS.find(o=>o.id===b.dataset.dec);if(d)userPick(d,b.dataset.pick);});
$('#optBody').addEventListener('toggle',e=>{const d=e.target.closest('[data-od]');if(!d)return;S.open=S.open||{};S.open['o:'+d.dataset.od]=d.open;save();},true);

/* ---------- Assembly (DFA): Boothroyd-Dewhurst style screening ---------- */
function dfaEval(s){if(!s||!last||!last.bom)return null;const L=last.bom.lines,C=[],ck=o=>C.push(Object.assign({st:'pass'},o));
  const isFast=l=>/fix$/.test(l.id),isLoose=l=>l.qtyUnit||l.id==='mat'||/^w_/.test(l.id)||/^cn_/.test(l.id)||l.id==='harness'||l.id==='membrane';
  const parts=L.filter(l=>!isFast(l)&&!isLoose(l)),nParts=parts.reduce((a,l)=>a+(l.qty||1),0);
  const fast=L.filter(isFast),nF=fast.reduce((a,l)=>a+(l.qty||0),0),kinds=new Set(fast.map(l=>l.spec)).size,cab=EL?EL.cables.length:0;
  const T=nParts*3+nF*7.8+cab*(2*4+6)+(L.some(l=>l.id==='membrane')?5:0)+mfgJoinT(s);
  const nMin=1+(L.some(l=>l.id==='lid')?1:0)+(L.some(l=>l.id==='board')?1:0)+(L.some(l=>l.id==='batt')?1:0),idx=Math.min(1,3*nMin/Math.max(T,1));
  ck({id:'dfa',label:'Design efficiency (DFA index)',val:`${fmt(idx*100,0)} %`,lim:'10 % or better (3 s per essential part over the total time)',st:idx>=0.1?'pass':'warn',why:`${nParts} parts, ${nMin} essential`});
  ck({id:'screws',label:'Screws to drive',val:String(nF),lim:`8 or fewer; more than ${Math.max(12,Math.round(1.5*nParts))} blocks Concept`,st:nF<=8?'pass':nF<=Math.max(12,Math.round(1.5*nParts))?'warn':'fail',why:'Each screw costs about 8 s; snap fits or fewer bosses remove them'});
  ck({id:'kinds',label:'Kinds of fastener',val:String(kinds),lim:'2 or fewer',st:kinds<=2?'pass':'warn',why:'One screw type means one driver and one bin'});
  {const miss=missing3D();ck({id:'geom3d',label:'3D for every picked component',val:miss.length?`${miss.length} missing`:'all present',lim:'each picked part has its geometry before CAD',st:miss.length?'fail':'pass',why:miss.length?'Missing: '+miss.join(', ')+'. Add their drawings to the context.':''});}
  ck({id:'dirs',label:'Assembly directions',val:'1, all from the open base',lim:'2 or fewer',st:'pass'});
  if(EL){const atB={};EL.cables.forEach(c=>{if(c.a==='board'||c.b==='board'){const k=c.fam+'|'+c.cond;atB[k]=(atB[k]||0)+1;}});const dup=Object.entries(atB).filter(([k,n])=>n>1);
    ck({id:'mates',label:'Connectors to mate',val:String(cab*2),lim:dup.length?'identical connectors on the board can be swapped':'each one different or keyed',st:dup.length?'warn':cab>6?'warn':'pass',why:dup.length?'Key them or use different pin counts so they cannot be swapped':''});}
  const lb=layout(s);if(lb&&lb.board&&lb.port)ck({id:'orient',label:'Board fits one way only',val:'rectangular hole pattern',lim:'the USB-C port must face its opening',st:'warn',why:'It can go in rotated 180°. Offset one hole or add a locating pin.'});
  {const sp=mfgSuppliers(s);if(sp.length)ck({id:'sup',label:'Suppliers for made parts',val:String(sp.length),lim:'4 or fewer to manage',st:sp.length<=4?'pass':'warn',why:'Fewer suppliers means fewer quotes, audits and deliveries: '+sp.join(', ')});}
  ck({id:'time',label:'Assembly time',val:`${fmt(T,0)} s`,lim:`about ${gbp(T/3600*36)} labour at £36 an hour`,st:'pass'});
  return{C,T,idx,nParts,nF};}
function levelChecks(){if(!EL)return;G.links.forEach(l=>{const a=G.nodes.find(n=>n.id===l.from[0]),b=G.nodes.find(n=>n.id===l.to[0]);if(!a||!b||!EB[a.type]||!EB[b.type])return;
  const la=(EB[a.type].lvl||{})[l.from[1]],lb=(EB[b.type].lvl||{})[l.to[1]];
  if(la&&lb&&Math.abs(la-lb)>0.3)EL.checks.push({id:'lvl_'+b.id,label:`Logic levels match, ${EB[a.type].name} to ${EB[b.type].name}`,val:`${la} V to ${lb} V`,lim:'within 0.3 V',st:'fail',lines:['e_'+b.id],fix:null,why:'Add a level shifter or choose a part at the same logic level'});});}

/* ---------- Models: safe formulas, tier 1 instantly, tier 2 Monte Carlo ---------- */
const MFN={min:Math.min,max:Math.max,sqrt:Math.sqrt,pow:Math.pow,abs:Math.abs,exp:Math.exp,log:Math.log,log10:Math.log10,sin:Math.sin,cos:Math.cos,tan:Math.tan,atan:Math.atan,floor:Math.floor,ceil:Math.ceil,round:Math.round,clamp:(v,a,b)=>Math.min(Math.max(v,a),b)},MCN={pi:Math.PI,g:9.81,e:Math.E};
function fparse(src){let i=0;const s=String(src),ws=()=>{while(i<s.length&&/\s/.test(s[i]))i++;};
  function atom(){ws();const c=s[i];if(c==='('){i++;const e=add();ws();if(s[i]!==')')throw Error('missing )');i++;return e;}
    const n=/^(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)/.exec(s.slice(i));if(n){i+=n[0].length;const k=+n[0];return()=>k;}
    const m=/^[A-Za-z_]\w*/.exec(s.slice(i));if(m){i+=m[0].length;const nm=m[0];ws();
      if(s[i]==='('){i++;const a=[];ws();if(s[i]!==')')for(;;){a.push(add());ws();if(s[i]===','){i++;continue;}break;}ws();if(s[i]!==')')throw Error('missing )');i++;
        const f=MFN[nm];if(!f)throw Error('unknown function '+nm);return v=>f(...a.map(x=>x(v)));}
      if(nm in MCN){const k=MCN[nm];return()=>k;}return v=>{if(!(nm in v))throw Error('unknown name '+nm);return v[nm];};}
    throw Error('unexpected '+(c||'end'));}
  function pw(){const b=atom();ws();if(s[i]==='*'&&s[i+1]==='*'){i+=2;const e=un();return v=>Math.pow(b(v),e(v));}return b;}
  function un(){ws();if(s[i]==='-'){i++;const e=un();return v=>-e(v);}if(s[i]==='+'){i++;return un();}return pw();}
  function mul(){let l=un();for(;;){ws();const c=s[i],a=l;if(c==='*'&&s[i+1]!=='*'){i++;const r=un();l=v=>a(v)*r(v);}else if(c==='/'){i++;const r=un();l=v=>a(v)/r(v);}else if(c==='%'){i++;const r=un();l=v=>a(v)%r(v);}else return l;}}
  function add(){let l=mul();for(;;){ws();const c=s[i],a=l;if(c==='+'){i++;const r=mul();l=v=>a(v)+r(v);}else if(c==='-'){i++;const r=mul();l=v=>a(v)-r(v);}else return l;}}
  const e=add();ws();if(i<s.length)throw Error('unexpected '+s[i]);return e;}
const MODELS=CTX?(CTX.models||[]):[];MODELS.forEach(m=>{m._f={};Object.entries(m.outputs||{}).forEach(([k,o])=>{try{m._f[k]=fparse(o.expr);}catch(e){m._f[k]=null;m._err=(m._err||'')+k+': '+e.message+'. ';}});});
function pageQ(){const s=last&&last.shape,L=last&&last.bom?last.bom.lines:[],T=battOf();
  return{'bom.mass_g':L.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0),'bom.cost_gbp':L.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0),'form.W':s?s.W:0,'form.D':s?s.D:0,'form.H':s?s.H:0,'form.t':s?(s.t||0):0,
    'form.volume_cm3':s?partVolume(s)/1000:0,'power.avg_mW':EL&&isFinite(EL.avgW)?EL.avgW*1000:0,'power.peak_mA':EL&&isFinite(EL.Ipk)?EL.Ipk:0,'battery.wh':T.cap?T.cap*3.7/1000:0,'battery.mah':T.cap||0,
    'env.opMin':ENV.opMin,'env.opMax':ENV.opMax,'env.altitude_m':ENV.altitude_m||0,'process.qty':S.qty};}
function evalModel(m,Q,over,rnd){const v={};Object.entries(m.inputs||{}).forEach(([k,inp])=>{let x=over&&over[k]!=null?over[k]:inp.from?Q[inp.from]:inp.value;if(rnd&&inp.spread)x*=1+(Math.random()*2-1)*inp.spread;v[k]=x;});
  const out={};Object.keys(m.outputs||{}).forEach(k=>{const f=m._f[k];let y=NaN;try{y=f?f(v):NaN;}catch(e){}out[k]=y;v[k]=y;});return out;}
const cmpOp=(a,op,b)=>op==='>='?a>=b:op==='<='?a<=b:op==='>'?a>b:op==='<'?a<b:Math.abs(a-b)<1e-9;
let MT1={},MCR={status:'idle',sig:'',res:{},share:{}};
function boundReqs(){return(PCFG.customRequirements||[]).filter(c=>c.model);}
function bindState(c){const v=MT1[c.model];if(v==null||!isFinite(v))return{st:'open',val:'no result'};const ok1=cmpOp(v,c.op||'>=',c.limit);
  const sh=MCR.status==='done'?MCR.share[c.id]:null;const st=sh!=null?(sh>=0.95?'pass':sh>=0.5?'warn':'fail'):(ok1?'warn':'fail');
  return{st,val:v,conf:sh!=null?'tier 2':'tier 1, provisional',share:sh};}
function ctxMustOpen(){return boundReqs().filter(c=>(c.pri||'Should')==='Must'&&bindState(c).st!=='pass').length;}
function runModels(){MT1={};if(!MODELS.length)return;const Q=pageQ();MODELS.forEach(m=>{const o=evalModel(m,Q,MODEL_OVR[m.id],false);Object.entries(o).forEach(([k,v])=>MT1[m.id+'.'+k]=v);});
  const sig=JSON.stringify([MODEL_OVR,Q,MODELS.map(m=>m.id),(PCFG.customRequirements||[]).map(c=>[c.model,c.op,c.limit])]);if(sig===MCR.sig)return;
  MCR={status:'running',sig,res:{},share:{},n:0,N:2000};const B=boundReqs(),acc={},pass={};B.forEach(c=>pass[c.id]=0);const my=MCR;
  (function step(){if(MCR!==my)return;for(let j=0;j<200&&my.n<my.N;j++,my.n++){const all={};MODELS.forEach(m=>{const o=evalModel(m,Q,MODEL_OVR[m.id],true);Object.entries(o).forEach(([k,v])=>all[m.id+'.'+k]=v);});
      Object.entries(all).forEach(([k,v])=>(acc[k]||(acc[k]=[])).push(v));B.forEach(c=>{if(cmpOp(all[c.model],c.op||'>=',c.limit))pass[c.id]++;});}
    if(my.n<my.N){setTimeout(step,0);return;}
    Object.entries(acc).forEach(([k,a])=>{a.sort((x,y)=>x-y);my.res[k]={p5:a[Math.floor(a.length*0.05)],p50:a[Math.floor(a.length*0.5)],p95:a[Math.floor(a.length*0.95)]};});
    B.forEach(c=>my.share[c.id]=pass[c.id]/my.N);my.status='done';renderCtx();renderGates();accSummaries();})();}

/* ---------- Scenarios: asked of the user, run against the models ---------- */
function scenList(){const base=CTX?(CTX.scenarios||[]):[];return base.concat(DBH&&SHARED_SCN?SHARED_SCN:S.userScen);}
function scenEval(sc){const Q=pageQ(),over={};Object.entries(sc.set||{}).forEach(([k,v])=>{const [m,i]=k.split('.');(over[m]||(over[m]={}))[i]=v;});
  const res=[];let st='pass';(sc.expect||[]).forEach(x=>{const [mid,o]=x.output.split('.'),m=MODELS.find(z=>z.id===mid);const v=m?evalModel(m,Q,Object.assign({},MODEL_OVR[mid]||{},over[mid]||{}),false)[o]:NaN;const ok=isFinite(v)&&cmpOp(v,x.op,x.value);res.push({x,v,ok});if(!ok)st='fail';});
  (sc.requires||[]).forEach(id=>{const r=REQ.find(q=>q.id===id),c=boundReqs().find(q=>q.id===id);const s2=r?r.st:c?bindState(c).st:'open';if(s2==='fail')st='fail';else if(s2!=='pass'&&s2!=='na'&&st!=='fail')st='warn';});
  if(!(sc.expect||[]).length&&!(sc.requires||[]).length)st=(sc.events||[]).length?'open':'open';return{st,res};}
function playScen(sc){const ev=sc.events||[];if(!ev.length)return;openDevice();let k=0;const nxt=()=>{if(k>=ev.length)return;const e=ev[k++];
  if(e==='button')$('#dBtn').click();else if(e==='usb'&&!$('#dUsb').disabled)$('#dUsb').click();setTimeout(nxt,900);};nxt();toast('Playing: '+sc.text);}
function renderModels(){const box=$('#mdlBody'),sec=$('#mdlBlk');if(!box)return;sec.hidden=!CTX;if(!CTX)return;const sc=scenList(),outs=[];MODELS.forEach(m=>Object.entries(m.outputs||{}).forEach(([k,o])=>outs.push([m.id+'.'+k,o.unit||'',m])));
  const B=boundReqs(),stc=st=>`<span class="chip">${st}</span>`;
  box.innerHTML=(MODELS.length?`<div class="vsub">${MCR.status==='running'?`Tier 2 running, ${Math.round(MCR.n/MCR.N*100)} %`:MCR.status==='done'?`Tier 2: ${MCR.N.toLocaleString('en-GB')} runs over the input spreads`:'Tier 1 results'}</div>`
    +outs.map(([k,u,m])=>{const v=MT1[k],r=MCR.res[k];return`<div class="mrow"><span>${esc(k)}</span><b>${fmt(v,2)} ${esc(u)}</b>${r?`<small>tier 2: ${fmt(r.p5,2)} to ${fmt(r.p95,2)} ${esc(u)} (5th to 95th percentile)</small>`:''}</div>`;}).join('')
    +(MODELS.some(m=>m._err)?`<p class="note" style="color:var(--fail)">${esc(MODELS.map(m=>m._err||'').join(''))}</p>`:'')
    +B.map(c=>{const b=bindState(c);return`<div class="mrow st-${b.st}"><span>${stc(b.st)} ${esc(c.id)}: ${esc(c.model)} ${esc(c.op||'>=')} ${c.limit}</span><b>${fmt(b.val,2)}</b><small>${esc(b.conf||'')}${b.share!=null?`, ${fmt(b.share*100,0)} % of runs pass`:''}</small></div>`;}).join('')
    :'<p class="note">No models yet. Requirements without a model stay open at gate 3.</p>')
    +`<div class="lbl" style="margin-top:10px">Scenario tests</div>`
    +(sc.length?sc.map((x,i)=>{const r=scenEval(x);return`<div class="sc st-${r.st}"><span class="chip">${r.st}</span><div>${esc(x.text)}<small>${x.by==='user'?'from you':'inferred, confirm it'}${r.res.map(q=>` · ${esc(q.x.output)} ${fmt(q.v,2)} ${esc(q.x.op)} ${q.x.value}`).join('')}</small></div>${(x.events||[]).length?`<button class="fix" type="button" data-play="${i}">Play</button>`:'<span></span>'}</div>`;}).join(''):'<p class="note">No scenario tests yet. Add the situations it must survive.</p>')
    +(outs.length?`<form class="cform" id="scForm"><b style="font-size:12px">Add a scenario</b><input name="text" placeholder="What happens, in your words" required>
      <div class="r2"><select name="out">${outs.map(([k])=>`<option>${esc(k)}</option>`).join('')}</select><div class="r2"><select name="op"><option>&gt;=</option><option>&lt;=</option><option>&gt;</option><option>&lt;</option></select><input name="val" type="number" step="any" placeholder="value" required></div></div>
      <div class="r2"><select name="inp"><option value="">No input change</option>${MODELS.flatMap(m=>Object.keys(m.inputs||{}).filter(k=>!(m.inputs[k]||{}).from).map(k=>`<option>${esc(m.id+'.'+k)}</option>`)).join('')}</select><input name="iv" type="number" step="any" placeholder="new input value"></div>
      <button class="btn" type="submit">Add scenario</button></form>`:'');}
$('#mdlBody').addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(b)playScen(scenList()[+b.dataset.play]);});
$('#mdlBody').addEventListener('submit',e=>{e.preventDefault();const f=e.target,v=k=>f.elements[k].value;const sc={id:'u'+Date.now().toString(36),text:v('text').trim(),by:'user',set:{},expect:[{output:v('out'),op:v('op'),value:+v('val')}],at:new Date().toISOString()};
  if(v('inp')&&v('iv')!=='')sc.set[v('inp')]=+v('iv');if(!sc.text)return;if(!DBH)S.userScen.push(sc);dbWrite('scenarios',sc.id,sc);toast('Scenario added. It goes back to Claude on the next sync.');schedule();});

/* ---------- Context edits: exclude a part, add a machine, correct a value ---------- */
function pendingEdits(){return DBH&&SHARED_EDITS?SHARED_EDITS:S.edits;}
function addEdit(e){e=Object.assign({id:'e'+Date.now().toString(36),at:new Date().toISOString()},e);if(!DBH)S.edits.push(e);dbWrite('contextEdits',e.id,e);
  if(e.kind==='machine'&&e.machine&&!MACH.some(m=>m.id===e.machine.id)){const pm=Object.assign({pending:true},e.machine);if(PROC0[pm.process])MACH.push(pm);ALLM.push(pm);}
  toast('Saved. Claude picks this up on the next /formgraph:sync.');schedule();}
function renderContextPanel(){const box=$('#ctxBody'),sec=$('#ctxBlk');if(!box)return;sec.hidden=!CTX;if(!CTX)return;const pe=pendingEdits();
  const parts=(CTX.parts||[]).filter(p=>!p.excluded);
  box.innerHTML=`<p class="note">${parts.length} real parts, each with a datasheet link and the date it was read. ${MACH.length} machines from ${[...new Set(MACH.map(m=>srcLabel(m.source)))].join(', ')}. Changes here go back to Claude with <b>/formgraph:sync</b>.</p>`
    +(pe.length?`<ul class="pend">${pe.map(e=>`<li>${e.kind==='exclude'?`Exclude ${esc((CPART[e.part]||{}).name||e.part)}`:e.kind==='machine'?`Add machine ${esc(e.machine.name)}`:`Set ${esc(e.part)} ${esc(e.field)} to ${esc(String(e.value))}`}${e.source?` (${esc(e.source)})`:''} · waiting for sync</li>`).join('')}</ul>`:'')
    +`<form class="cform" id="mForm"><b style="font-size:12px">Add a machine or supplier</b><input name="name" placeholder="Name, for example our MJF printer" required>
      <div class="r2"><select name="proc">${[...new Set([...Object.keys(PROC0),...Object.keys(CTX.processes||{})])].map(k=>`<option value="${k}">${esc((PROC0[k]||CTX.processes[k]||{}).name||k)}</option>`).join('')}</select><input name="lead" type="number" placeholder="lead time, days"></div>
      <div class="r2"><input name="bv" placeholder="build volume x,y,z mm"><input name="wall" type="number" step="any" placeholder="minimum wall mm"></div>
      <div class="r2"><input name="hole" type="number" step="any" placeholder="minimum hole mm"><input name="src" placeholder="source: user or a link"></div><button class="btn" type="submit">Add machine</button></form>
    <form class="cform" id="vForm"><b style="font-size:12px">Correct a value</b><select name="part">${parts.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>
      <div class="r2"><select name="field"><option value="mpn">part number</option><option value="cost.unit">unit cost</option><option value="mass">mass g</option><option value="other">something else</option></select><input name="value" placeholder="new value" required></div>
      <input name="src" placeholder="source, for example a link or a quote" required><button class="btn" type="submit">Save correction</button></form>`;}
$('#ctxBody').addEventListener('submit',e=>{e.preventDefault();const f=e.target,v=k=>(f.elements[k]&&f.elements[k].value||'').trim();
  if(f.id==='mForm'){const bv=v('bv').split(/[,x× ]+/).map(Number).filter(n=>n>0);const id=('user-'+v('name').toLowerCase().replace(/[^a-z0-9]+/g,'-')).slice(0,60);
    addEdit({kind:'machine',machine:{id,name:v('name'),process:v('proc'),source:v('src')||'user',buildVolume:bv.length===3?bv:undefined,leadDays:+v('lead')||undefined,limits:{wallMin:+v('wall')||undefined,holeMin:+v('hole')||undefined},materials:(MATS[v('proc')]||[]).map(m=>m.id).concat(((CTX.processes||{})[v('proc')]||{}).materials||[])}});}
  if(f.id==='vForm')addEdit({kind:'value',part:v('part'),field:v('field'),value:v('value'),source:v('src')});f.reset();});

/* ---------- MetaForge host bridge ----------
   Hosted in the MetaForge dashboard (a sandboxed iframe), the page keeps no
   shared state of its own: writes go to the host as fg:write messages, the
   host stores them in the twin and sends the current state back as
   fg:snapshot. fg:readonly freezes every edit control after the handover.
   Standalone (no parent frame), the page keeps the claude.use('db') path. */
const FG_ASSETS=(()=>{const el=document.getElementById('fg-assets');let a={};try{a=JSON.parse(el&&el.textContent||'{}')||{};}catch(e){}return{gltf:a.gltf||'engine/assets/GLTFExporter.js'};})();
const FG_COLLECTIONS=['choices','scenarios','contextEdits','assumptions','requirements','ledger','screening'];
let HOST_ORIGIN=null,READONLY=false;const HOSTQ=[];
/** The frame height a hosted page asks for, at a given width. Wide pages
 * sit the controls beside a view of up to 560 px; narrow ones stack. */
function hostedHeight(w,bar){const wide=w>=900,view=wide?560:Math.min(440,Math.max(300,w*0.7)),rest=wide?300:360;
  return clamp(bar+view+rest,480,960);}
window.__fgHostedHeight=hostedHeight;
function b64Of(u8){let s='';const n=0x8000;for(let i=0;i<u8.length;i+=n)s+=String.fromCharCode.apply(null,u8.subarray(i,i+n));return btoa(s);}
function hostPost(type,o,anyOrigin){if(!HOSTED)return;const m=Object.assign({fg:1,type},o||{});
  const to=HOST_ORIGIN||(anyOrigin?'*':null);if(!to){HOSTQ.push(()=>hostPost(type,o));return;}
  try{window.parent.postMessage(m,to);}catch(e){}}
function evId(){try{return crypto.randomUUID();}catch(e){return 'ev-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);}}
function hostUpsert(col,id,data){const arr=col==='scenarios'?SHARED_SCN:col==='contextEdits'?SHARED_EDITS:null;if(!arr)return;const i=arr.findIndex(x=>x.id===id);const v=Object.assign({},data,{id});if(i>=0)arr[i]=v;else arr.push(v);}
function hostWrite(col,id,data){if(FG_COLLECTIONS.indexOf(col)<0)return;
  if(READONLY){toast('Imagine is handed over. Rework it to make changes.');return;}
  id=String(id||evId());const clean=JSON.parse(JSON.stringify(data==null?{}:data));hostUpsert(col,id,clean);
  hostPost('fg:write',{collection:col,id,data:clean,event_id:evId()});}
function psetWrite(path,v){const ks=String(path).split('.'),at=new Date().toISOString();
  if(ks[0]==='confirmed'&&ks.length===2)dbWrite('assumptions',ks[1],{confirmed:!!v,at});
  else if(ks[0]==='ledger'&&ks.length===2){const i=parseInt(ks[1].slice(1),10),x=(PCFG.ledger||[])[i]||{};dbWrite('ledger',ks[1],{index:i,status:String(v),text:x.text||'',at});}
  else if(ks[0]==='creq'&&ks.length===3){const c=(PCFG.customRequirements||[]).find(q=>q.id===ks[1])||{};dbWrite('requirements',ks[1],Object.assign({id:ks[1],text:c.text||'',verify:c.verify||'test',source:'studio'},progState().creq[ks[1]]||{},{at}));}}
let SCR_SIG='',SCR_T=0;
function hostScreening(ev){if(!HOSTED||READONLY||!ev||!ev.rep)return;clearTimeout(SCR_T);SCR_T=setTimeout(()=>{
  const rules=(ev.rep.rules||[]).map(r=>({id:String(r.id||r.label||''),label:String(r.label||''),status:r.st,value:r.val==null?null:String(r.val),limit:r.lim==null?null:String(r.lim)}));
  const reqs=(REQ||[]).map(r=>({id:String(r.id),status:r.st,priority:r.pri||null}));
  const sig=JSON.stringify([S.proc,rules,reqs]);if(sig===SCR_SIG)return;SCR_SIG=sig;
  dbWrite('screening','latest',{engine:ENGINE,rule_version:ENGINE+'#'+(S.proc||'none'),process:S.proc||null,rules,requirements:reqs,at:new Date().toISOString()});},1500);}
function hostApply(cols){if(!cols||typeof cols!=='object')return;DBH=DBH||{hosted:true};
  const ent=c=>{const v=cols[c];if(!v||typeof v!=='object')return[];return Array.isArray(v)?v.filter(x=>x&&x.id!=null).map(x=>[String(x.id),x]):Object.entries(v).filter(([,x])=>x&&typeof x==='object');};
  let ch=false;
  ent('choices').forEach(([id,v])=>{if(id.startsWith('mfg-')){if(v.pick&&S.mfgPick[id.slice(4)]!==v.pick){S.mfgPick[id.slice(4)]=v.pick;ch=true;}return;}
    const dec=OPTS.find(o=>o.id===id);if(dec&&v.pick&&(S.picks[id]||dec.pick)!==v.pick&&dec.candidates.includes(v.pick)){applyPick(dec,v.pick,true);ch=true;}});
  SHARED_SCN=ent('scenarios').map(([id,v])=>Object.assign({},v,{id}));
  SHARED_EDITS=ent('contextEdits').map(([id,v])=>Object.assign({},v,{id}));
  SHARED_EDITS.forEach(e=>{if(e.kind==='machine'&&e.machine&&!ALLM.some(m=>m.id===e.machine.id)){const pm=Object.assign({pending:true},e.machine);if(PROC0[pm.process])MACH.push(pm);ALLM.push(pm);}});
  const p=progState();
  ent('assumptions').forEach(([id,v])=>{if(typeof v.confirmed==='boolean')p.confirmed[id]=v.confirmed;});
  ent('ledger').forEach(([id,v])=>{if(typeof v.status==='string')p.ledger[id]=v.status;});
  ent('requirements').forEach(([id,v])=>{const c=Object.assign({},p.creq[id]||{});['st','ev'].forEach(k=>{if(v[k]!=null)c[k]=String(v[k]);});p.creq[id]=c;});
  if(ch){autoWire(null);renderNodes();renderComp();renderProcess();}
  schedule();}
const RO_EDIT='input,select,textarea,button';
const RO_OK='[data-pview],[data-level],[data-view],[data-gtab],[data-speed],[data-cov],[data-pcard],[data-open],[data-ref],[data-sim],[data-play],[data-mc],[data-unclamp],#fit,#flip,#frame,#edgeL,#edgeR,.ibtn,.x,#simRun,#devBtn,#misPlay,#cpSpec,#cpReport,#cpPack,#cpGraph,[data-cpbom],#cadExport,#ioClose,.acc-h,#simIn *,#devp *,#misBody *';
function roBlocked(t){const c=t&&t.closest?t.closest(RO_EDIT):null;return !!(c&&!c.matches(RO_OK));}
function applyReadonly(){const de=document.documentElement;if(READONLY)de.setAttribute('data-readonly','');else de.removeAttribute('data-readonly');
  document.querySelectorAll(RO_EDIT).forEach(el=>{if(READONLY&&!el.matches(RO_OK)){if(!el.disabled){el.disabled=true;el.setAttribute('data-fg-ro','');}}else if(!READONLY&&el.hasAttribute('data-fg-ro')){el.disabled=false;el.removeAttribute('data-fg-ro');}});
  let b=document.querySelector('.fg-ro-banner');if(READONLY&&!b){b=document.createElement('div');b.className='fg-ro-banner';b.setAttribute('role','status');b.textContent='Handed over to Develop. This studio revision is read-only.';document.body.appendChild(b);}else if(!READONLY&&b)b.remove();}
['click','change','input','pointerdown','submit','dragstart'].forEach(t=>document.addEventListener(t,e=>{if(!READONLY)return;const tg=e.target;
  if(t==='submit'||roBlocked(tg)||(t==='pointerdown'&&tg.closest&&tg.closest('#nodes'))){e.preventDefault();e.stopImmediatePropagation();}},true));
document.addEventListener('keydown',e=>{if(READONLY&&(e.key==='Delete'||e.key==='Backspace')&&!(e.target&&e.target.closest&&e.target.closest('input,textarea'))){e.preventDefault();e.stopImmediatePropagation();}},true);
let roT=0;new MutationObserver(()=>{if(!READONLY||roT)return;roT=requestAnimationFrame(()=>{roT=0;applyReadonly();});}).observe(document.body,{childList:true,subtree:true});
if(HOSTED){
  window.addEventListener('message',e=>{if(e.source!==window.parent)return;const m=e.data;if(!m||typeof m!=='object'||m.fg!==1||typeof m.type!=='string')return;
    if(HOST_ORIGIN===null)HOST_ORIGIN=e.origin&&e.origin!=='null'?e.origin:'*';else if(HOST_ORIGIN!=='*'&&e.origin!==HOST_ORIGIN)return;
    if(m.type==='fg:snapshot'){hostApply(m.collections);HOSTQ.splice(0).forEach(f=>f());}
    else if(m.type==='fg:readonly'){READONLY=m.value===true;applyReadonly();}});
  $('#bomBody').addEventListener('click',e=>{const li=e.target.closest&&e.target.closest('[data-cmp]');if(li&&!e.target.closest('input,button,select'))hostPost('fg:select',{component_id:li.dataset.cmp});});
  // The height the page would like, from its width alone: the bar, the 3D
  // view at its bounded size and room for the panels, which scroll inside.
  // It never reads the document's height, which follows the frame's own
  // height and would grow the frame without end.
  let lastH=0;const postH=()=>{const h=Math.round(hostedHeight(document.documentElement.clientWidth,($('.bar')||{}).offsetHeight||0));if(h!==lastH){lastH=h;hostPost('fg:height',{px:h},true);}};
  try{const ro=new ResizeObserver(postH);ro.observe(document.documentElement);ro.observe($('.bar'));}catch(e){window.addEventListener('resize',postH);}
  const vb=$('[data-view="studio"]');if(vb)vb.textContent='Model';
}

/* ---------- Shared record: choices, scenarios and edits come back to Claude ---------- */
function dbWrite(col,id,data){if(HOSTED){hostWrite(col,id,data);return;}const f=()=>{try{const r=id?DBH.collection(col).doc(id).set(JSON.parse(JSON.stringify(data))):DBH.collection(col).add(data);r.catch(()=>toast('Could not save to the shared record.'));}catch(e){}};if(DBH)f();else if(window.claude)DBQ.push(f);}
if(!HOSTED&&CTX&&window.claude&&claude.use)(async()=>{try{const db=await claude.use('db');if(!db)return;DBH=db;
  db.collection('choices').onSnapshot(sn=>{let ch=false;sn.docs.forEach(d=>{if(d.id.startsWith('mfg-')){const v=d.data()||{};if(v.pick&&S.mfgPick[d.id.slice(4)]!==v.pick){S.mfgPick[d.id.slice(4)]=v.pick;ch=true;}return;}const v=d.data()||{},dec=OPTS.find(o=>o.id===d.id);if(dec&&v.pick&&(S.picks[d.id]||dec.pick)!==v.pick&&dec.candidates.includes(v.pick)){applyPick(dec,v.pick,true);ch=true;}});
    if(ch){autoWire(null);renderNodes();renderComp();renderProcess();schedule();}},()=>{});
  db.collection('scenarios').onSnapshot(sn=>{SHARED_SCN=sn.docs.map(d=>Object.assign({},d.data(),{id:d.id}));schedule();},()=>{});
  db.collection('contextEdits').onSnapshot(sn=>{SHARED_EDITS=sn.docs.map(d=>Object.assign({},d.data(),{id:d.id}));SHARED_EDITS.forEach(e=>{if(e.kind==='machine'&&e.machine&&!ALLM.some(m=>m.id===e.machine.id)){const pm=Object.assign({pending:true},e.machine);if(PROC0[pm.process])MACH.push(pm);ALLM.push(pm);}});schedule();},()=>{});
  DBQ.splice(0).forEach(f=>f());}catch(e){}})();

/* ---------- References: images, sketches, frames and meshes the design is checked against ---------- */
const REFS=CTX?(CTX.refs||[]):[],REFO={};let REFC=[];S.refShow=S.refShow||{};
const lbx=document.createElement('div');lbx.className='lbx';lbx.hidden=true;lbx.setAttribute('role','dialog');lbx.setAttribute('aria-label','Reference');document.body.appendChild(lbx);
lbx.addEventListener('click',e=>{if(e.target.closest&&e.target.closest('figure'))return;lbx.hidden=true;});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!lbx.hidden)lbx.hidden=true;});
function refMesh(r){if(REFO[r.id])return REFO[r.id];const buf=x=>Uint8Array.from(atob(x),c=>c.charCodeAt(0)).buffer;
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(buf(r.mesh.pos)),3));g.setIndex(new THREE.BufferAttribute(new Uint32Array(buf(r.mesh.idx)),1));
  g.computeBoundingBox();const bb=g.boundingBox;g.translate(-(bb.min.x+bb.max.x)/2,-bb.min.y,-(bb.min.z+bb.max.z)/2);g.computeVertexNormals();
  const col=r.role==='envelope'?'#d9534f':r.role==='target'?'#3b82f6':'#9aa3ad';
  const m=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:new THREE.Color(col).convertSRGBToLinear(),transparent:true,opacity:.26,depthWrite:false,roughness:.85,side:THREE.DoubleSide}));
  const at=r.at||[0,0,0],ro=(r.rotate||[0,0,0]).map(d=>d*Math.PI/180);m.position.set(at[0],at[1],at[2]);m.rotation.set(ro[0],ro[1],ro[2]);m.renderOrder=3;scene.add(m);REFO[r.id]=m;return m;}
const refShown=r=>S.refShow[r.id]!=null?S.refShow[r.id]:r.role!=='look';
function refVis(){REFS.filter(r=>r.kind==='mesh'&&r.mesh).forEach(r=>{refMesh(r).visible=refShown(r)&&S.view!=='sim';});}
function refChecks(s){const C=[];if(!s)return C;const D={'form.W':s.W,'form.D':s.D,'form.H':s.H,'form.R':s.R,'form.F':s.F,'form.A':s.A,'form.t':s.t,'form.P':s.D/s.W};
  REFS.forEach(r=>{const nm=r.title||r.id;
    (r.extracted||[]).forEach(x=>{if(!(x.what in D)||typeof x.value!=='number')return;const d=D[x.what],v=x.value,tol=r.tolerance||(x.confidence==='measured'?0.05:0.15),u=x.what==='form.P'?'':x.what==='form.A'?'°':' mm';
      if(r.role==='target'){const ok=Math.abs(d-v)<=Math.abs(v)*tol;C.push({id:'ref'+r.id+x.what,label:`${x.what.slice(5)} matches ${nm}`,val:`design ${fmt(d,1)}${u}`,lim:`reference ${fmt(v,1)}${u} ± ${Math.round(tol*100)} % (${x.confidence||'estimated'})`,st:ok?'pass':'warn',why:x.basis||''});}
      if(r.role==='envelope'&&['form.W','form.D','form.H'].includes(x.what)){const ok=d<=v+1e-6;C.push({id:'ref'+r.id+x.what,label:`${x.what.slice(5)} inside ${nm}`,val:`design ${fmt(d,1)} mm`,lim:`at most ${fmt(v,1)} mm`,st:ok?'pass':'fail',fix:!ok&&x.what==='form.W'?{act:'role',role:'W',target:v-0.5,label:'Fit inside'}:null,why:x.basis||''});}});
    if(r.kind==='mesh'&&r.stats){const z=r.stats.size,des=[s.W,s.H,s.D],ax=['width','height','depth'];
      if(r.role==='target')ax.forEach((a,i)=>{const tol=r.tolerance||0.1,ok=Math.abs(des[i]-z[i])<=z[i]*tol;C.push({id:'refm'+r.id+i,label:`${a} matches ${nm}`,val:`design ${fmt(des[i],0)} mm`,lim:`mesh ${fmt(z[i],0)} mm ± ${Math.round(tol*100)} %`,st:ok?'pass':'warn'});});
      if(r.role==='envelope')ax.forEach((a,i)=>{const ok=des[i]<=z[i]+1e-6;C.push({id:'refm'+r.id+i,label:`${a} inside ${nm}`,val:`design ${fmt(des[i],0)} mm`,lim:`envelope ${fmt(z[i],0)} mm`,st:ok?'pass':'fail',fix:!ok&&i===0?{act:'role',role:'W',target:z[0]-0.5,label:'Fit inside'}:null});});}});
  return C;}
function refCaption(r){const ex=(r.extracted||[]).map(x=>`<li><b>${esc(x.what)}</b>: ${esc(typeof x.value==='number'?fmt(x.value,1):String(x.value))}${x.unit&&typeof x.value==='number'?' '+esc(x.unit):''} <span style="color:var(--muted)">(${esc(x.confidence||'estimated')}${x.basis?', '+esc(x.basis):''})</span></li>`).join('');
  return`<b>${esc(r.title||r.id)}</b> · ${esc(r.kind)}${r.view?' · '+esc(r.view)+' view':''} · role ${esc(r.role||'look')}${r.time_s!=null?` · at ${fmt(r.time_s,1)} s`:''}${r.scale?` · scale ${fmt(r.scale.mm/r.scale.px,3)} mm per px (${esc(r.scale.basis||'known length')})`:''}${r.notes?`<br>${esc(r.notes)}`:''}${ex?`<ul>${ex}</ul>`:''}`;}
function renderRefs(){const box=$('#refBody'),sec=$('#refBlk');if(!box)return;sec.hidden=!REFS.length;if(!REFS.length)return;
  refVis();
  const imgs=REFS.filter(r=>r.data),meshes=REFS.filter(r=>r.kind==='mesh'),other=REFS.filter(r=>!r.data&&r.kind!=='mesh');
  box.innerHTML=(imgs.length?`<div class="refg">${imgs.map(r=>`<button type="button" data-ref="${esc(r.id)}" style="background-image:url('${r.data}')" aria-label="${esc(r.title||r.id)}"><span>${esc(r.title||r.view||r.kind)}</span></button>`).join('')}</div>`:'')
    +meshes.map(r=>{const z=r.stats?r.stats.size:null;return`<div class="rmesh"><span><b>${esc(r.title||r.fileName||r.id)}</b> · ${esc(r.role||'look')}</span><label class="tog" style="margin:0"><input type="checkbox" data-rshow="${esc(r.id)}"${refShown(r)?' checked':''}><span>Show in 3D</span></label>
      <small>${z?`${z.map(v=>fmt(v,0)).join(' × ')} mm (width × height × depth) · ${r.stats.triangles.toLocaleString('en-GB')} triangles${r.mesh&&r.mesh.reduced?`, shown as ${r.mesh.tris.toLocaleString('en-GB')}`:''} · ${esc(r.units)} in the file, ${esc(r.up)} up`:'no mesh data'}${r.notes?' · '+esc(r.notes):''}</small></div>`;}).join('')
    +other.map(r=>`<div class="rmesh"><span><b>${esc(r.title||r.id)}</b> · ${esc(r.kind)}${r.source==='chat'?', seen in chat':''}</span><span></span><small>${(r.extracted||[]).length} values read${r.frames?` · ${r.frames.length} frames`:''}${r.notes?' · '+esc(r.notes):''}</small></div>`).join('')
    +(REFC.length?`<ul class="rlist" style="margin-top:8px">${REFC.map((c,i)=>`<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div>${c.why&&c.st!=='pass'?`<div class="rv">${esc(c.why)}</div>`:''}</div>${c.fix&&c.st!=='pass'?`<button class="fix" type="button" data-rfix="${i}">${esc(c.fix.label)}</button>`:'<span></span>'}</li>`).join('')}</ul>`
      :'<p class="note">Mark a reference as a target or an envelope to check the design against it.</p>');}
$('#refBody').addEventListener('click',e=>{const f=e.target.closest('[data-rfix]');if(f){applyFix(REFC[+f.dataset.rfix].fix);return;}
  const b=e.target.closest('[data-ref]');if(!b)return;const r=REFS.find(x=>x.id===b.dataset.ref);if(!r)return;
  lbx.innerHTML=`<figure><img src="${r.data}" alt="${esc(r.title||r.id)}"><figcaption>${refCaption(r)}</figcaption></figure>`;lbx.hidden=false;});
$('#refBody').addEventListener('change',e=>{const c=e.target.closest('[data-rshow]');if(!c)return;S.refShow[c.dataset.rshow]=c.checked;refVis();save();});

function recomputeOverrides(){MODEL_OVR={};const put=set=>Object.entries(set||{}).forEach(([k,v])=>{const [m,i]=k.split('.');(MODEL_OVR[m]||(MODEL_OVR[m]={}))[i]=v;});
  OPTS.forEach(d=>{const id=S.picks[d.id]||d.pick;if(d.kind==='variant')put(((d.variants||[]).find(v=>v.id===id)||{}).set);else if(d.kind==='part')put((CPART[id]||{}).sets);});
  if(S.pp&&S.pp.prop&&S.pp.prop.din&&MODELS.some(m=>m.inputs&&m.inputs.prop_d))put({'hover.prop_d':S.pp.prop.din*0.0254});}
// What the bound requirements would read with these model inputs on top of the current picks
function previewFor(set){if(!MODELS.length||!last)return{html:'',dot:''};const ov=JSON.parse(JSON.stringify(MODEL_OVR));Object.entries(set||{}).forEach(([k,v])=>{const [m,i]=k.split('.');(ov[m]||(ov[m]={}))[i]=v;});
  const Q=pageQ(),out={};MODELS.forEach(m=>{const o=evalModel(m,Q,ov[m.id],false);Object.entries(o).forEach(([k,v])=>out[m.id+'.'+k]=v);});
  const B=boundReqs().filter(c=>c.model&&out[c.model]!=null);if(!B.length)return{html:'',dot:''};let bad=0;
  const parts=B.map(c=>{const v=out[c.model],ok=isFinite(v)&&cmpOp(v,c.op||'>=',c.limit);if(!ok)bad++;const u=(MODELS.find(m=>m.id===c.model.split('.')[0]).outputs[c.model.split('.')[1]]||{}).unit||'';
    return`<span class="${ok?'ok2':'on2'}">${esc(c.id)} ${fmt(v,v>100?0:1)}${u?' '+esc(u):''}</span>`;});
  return{html:`<span class="os">${parts.join(' · ')}</span>`,dot:bad?'fail':'pass'};}
/* ---------- Airframe: multirotor arms, motors, props, legs, payload and battery around the fuselage ---------- */
const AF=CFG.airframe&&CFG.airframe.type==='multirotor'?CFG.airframe:null;let AFL=0,AFSPAN=0,AFG=null,AFC=[];
const afMat={};function afM(c,o){const k=c+(o||'');return afMat[k]||(afMat[k]=new THREE.MeshStandardMaterial(Object.assign({color:new THREE.Color(c).convertSRGBToLinear(),roughness:.6,metalness:.15},o||{})));}
function modelInput(key){const [m,i]=key.split('.');if(MODEL_OVR[m]&&MODEL_OVR[m][i]!=null)return MODEL_OVR[m][i];const md=MODELS.find(x=>x.id===m);return md&&md.inputs[i]?md.inputs[i].value:null;}
function afPick(dec){const d=OPTS.find(o=>o.id===dec);if(!d)return null;const id=S.picks[d.id]||d.pick;return{d,id,part:CPART[id],variant:(d.variants||[]).find(v=>v.id===id)};}
function tubeBetween(a,b,r){const A=new THREE.Vector3(...a),B=new THREE.Vector3(...b),L=A.distanceTo(B),g=new THREE.CylinderGeometry(r,r,L,20);
  const q=new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),B.clone().sub(A).normalize());g.applyMatrix4(new THREE.Matrix4().compose(A.clone().add(B).multiplyScalar(.5),q,new THREE.Vector3(1,1,1)));return g;}
function afGeom(s){const a=AF,n=a.rotors||4,P=S.pp||{},pd=P.prop&&P.prop.din?P.prop.din*25.4:typeof a.prop_d==='number'?a.prop_d:((modelInput((a.prop_d||{}).from||'hover.prop_d')||0.381)*1000);
  const gap=P.arm&&P.arm.gap!=null?P.arm.gap/100:0.12,half=Math.hypot(s.W,s.D)/2,R=Math.max(pd*(1+gap)/(2*Math.sin(Math.PI/n)),half+20),mot=[P.motor&&P.motor.d!=null?P.motor.d:afMotorDefault()[0],P.motor&&P.motor.h!=null?P.motor.h:afMotorDefault()[1]],yA=s.H+(a.prop_above||15)-mot[1]-4,yR=s.H*0.62,legs={h:P.leg&&P.leg.h!=null?P.leg.h:((a.legs||{}).h||170),d:P.leg&&P.leg.d!=null?P.leg.d:((a.legs||{}).d||18)},ad=P.arm&&P.arm.d?P.arm.d:(a.arm_d||22),hk=P.hinge&&P.hinge.k?P.hinge.k:1.6;
  const ang=[...Array(n)].map((_,i)=>(a.layout==='plus'?0:Math.PI/n)+i*2*Math.PI/n),M=ang.map(t=>[R*Math.cos(t),yA,R*Math.sin(t)]);
  const pay=afPick((a.payload||{}).decision||'gim'),bat=afPick((a.battery||{}).decision||'battery');
  const pe=(pay&&pay.part&&pay.part.geometry&&pay.part.geometry.envelope)||(a.payload||{}).fallback||[110,90,110];
  const be=(bat&&bat.variant&&bat.variant.dims)||(a.battery||{}).fallback||[150,70,60];
  const adj=2*R*Math.sin(Math.PI/n),span=2*R+pd,propY=yA+mot[1]+4;
  const pm=modelInput('hover.payload_g')||0,bm=modelInput('hover.batt_g')||0,gx=s.W/2-pe[0]/2-6,at=(a.battery||{}).at||'balance';
  const bx=at==='balance'&&bm?Math.max(-s.W/2,Math.min(s.W/2,-(pm*gx)/bm)):at==='rear'?-s.W/2-be[0]*0.2:(typeof at==='number'?at:0);
  return{n,pd,R,yA,yR,mot,legs,M,ang,pe,be,adj,span,half,pay,bat,propY,gx,bx,at,ad,hk,gap};}
function addAirframe(s,add){const G=afGeom(s),a=AF,ac=a.accent||'#E8762D',arm=a.arms||'#2B2D30',ad=G.ad;AFG=G;AFSPAN=G.span;
  const footY=-(G.legs.h||170);AFL=-footY;
  G.M.forEach((m,i)=>{const t=G.ang[i],root=[Math.cos(t)*Math.min(s.W/2-8,G.half*0.72),G.yR,Math.sin(t)*Math.min(s.D/2-8,G.half*0.72)];
    add(tubeBetween(root,[m[0],m[1],m[2]],ad/2),1,'comp',afM(arm,{roughness:.35}),'arm '+(i+1));
    const hb=new THREE.BoxGeometry(ad*G.hk,ad*G.hk*0.875,ad*G.hk);hb.translate(...root);add(hb,1,'comp',afM('#3a3f47'),'arm hinge '+(i+1));
    const mg=new THREE.CylinderGeometry(G.mot[0]/2,G.mot[0]/2*0.92,G.mot[1],32);mg.translate(m[0],m[1]+G.mot[1]/2-4,m[2]);add(mg,1,'comp',afM('#1d1f22'),'motor '+(i+1));
    const pod=new THREE.CylinderGeometry(G.mot[0]/2*0.8,G.mot[0]/2*0.7,G.mot[1]*0.9,28);pod.translate(m[0],m[1]-G.mot[1]*0.45,m[2]);add(pod,1,'comp',afM('#26292d'),'motor pod '+(i+1));
    const py=G.propY,bl=G.pd/2,bw=Math.max(14,G.pd*0.075),phase=i*0.6;
    const nb=(S.pp&&S.pp.prop&&S.pp.prop.blades)||a.blades||2;for(let b=0;b<nb;b++){const th=phase+b*2*Math.PI/nb;const g=new THREE.BoxGeometry(bl*0.86,3,bw);g.translate(bl*0.43+bl*0.07,0,0);g.rotateY(th);g.translate(m[0],py,m[2]);add(g,1,'comp',afM('#202326',{roughness:.4}),'prop '+(i+1));
      const tip=new THREE.BoxGeometry(bl*0.12,3.2,bw*0.9);tip.translate(bl*0.94,0,0);tip.rotateY(th);tip.translate(m[0],py,m[2]);add(tip,1,'comp',afM(ac),'prop '+(i+1));}
    const disc=new THREE.CircleGeometry(G.pd/2,64);disc.rotateX(-Math.PI/2);disc.translate(m[0],py+0.5,m[2]);add(disc,1,'comp',afM('#9aa3ad',{transparent:true,opacity:.09,depthWrite:false,side:THREE.DoubleSide}),'prop disc');
    const lg=tubeBetween([m[0],m[1]-G.mot[1]*0.9,m[2]],[m[0]*0.97,footY+G.legs.d/2,m[2]*0.97],G.legs.d/2);add(lg,1,'comp',afM('#1d1f22'),'leg '+(i+1));
    const ft=new THREE.SphereGeometry(G.legs.d*0.6,16,10);ft.translate(m[0]*0.97,footY+G.legs.d*0.6,m[2]*0.97);add(ft,1,'comp',afM('#111'),'leg '+(i+1));});
  const [pl,pw,ph]=G.pe,gy=-ph/2-8,gx=G.gx;const gb=new THREE.BoxGeometry(pl,ph,pw);gb.translate(gx,gy,0);add(gb,1,'comp',afM('#2b2f36'),'payload gimbal');
  const gm=new THREE.BoxGeometry(pl*0.35,10,pw*0.35);gm.translate(gx,-4,0);add(gm,1,'comp',afM('#3a3f47'),'payload gimbal');
  const lens=new THREE.CylinderGeometry(Math.min(pw,ph)*0.18,Math.min(pw,ph)*0.18,8,24);lens.rotateZ(Math.PI/2);lens.translate(gx+pl/2+3,gy,0);add(lens,1,'comp',afM('#0b0d10',{roughness:.1,metalness:.6}),'payload gimbal');
  const [bl2,bw2,bh2]=G.be,bx=G.bx;const bb=new THREE.BoxGeometry(bl2,bh2,bw2);bb.translate(bx,G.at==='rear'?s.H*0.45:Math.max(bh2/2+(s.t||2)+1,s.H*0.4),0);add(bb,1,'comp',afM('#3a3f47'),'battery');
  const bt=new THREE.BoxGeometry(6,bh2*0.6,bw2*0.7);bt.translate(Math.min(bx-bl2/2-3,-s.W/2-3),G.at==='rear'?s.H*0.45:Math.max(bh2/2+(s.t||2)+1,s.H*0.4),0);add(bt,1,'comp',afM(ac),'battery');
  const ns=S.pp&&S.pp.sensors&&S.pp.sensors.n!=null?S.pp.sensors.n:(a.nose_sensors||0);for(let i=0;i<ns;i++){const z=(i-(ns-1)/2)*Math.min(26,s.D*0.8/Math.max(1,ns-1)),y=s.H*(i%2?0.55:0.72);const c=new THREE.CylinderGeometry(6,6,5,24);c.rotateZ(Math.PI/2);c.translate(s.W/2+1.5,y,z);add(c,1,'comp',afM('#0b0d10',{roughness:.1,metalness:.5}),'nose sensors');}
  if(a.top_light){const d=new THREE.SphereGeometry(7,20,12,0,Math.PI*2,0,Math.PI/2);d.translate(0,s.H,0);add(d,1,'comp',afM('#f3f6f9',{emissive:new THREE.Color('#ffffff'),emissiveIntensity:.25}),'top light');}}
function afChecks(s){if(!AF||!s)return[];const G=afGeom(s),C=[],ck=o=>C.push(Object.assign({st:'pass'},o));AFG=G;
  const gap=G.adj-G.pd;ck({id:'pp',label:'Props clear each other',val:`${fmt(gap,0)} mm between tips`,lim:`at least ${fmt(G.pd*0.08,0)} mm (8 % of the prop)`,st:gap>=G.pd*0.08?'pass':gap>0?'warn':'fail'});
  const body=G.R-G.pd/2-G.half,above=G.propY-s.H;ck({id:'pb',label:'Props clear the fuselage',val:above>=5?`props ${fmt(above,0)} mm above the body`:`${fmt(body,0)} mm in plan`,lim:'props above the body top, or 10 mm clear in plan',st:above>=5||body>=10?'pass':body>0?'warn':'fail'});
  if(G.at!=='rear'){const t=s.t||2,fit=G.be[0]<=s.W-2*t-2&&G.be[1]<=s.D-2*t-2&&G.be[2]<=s.H-2*t-2;ck({id:'bay',label:'Battery bay fits inside the fuselage',val:`${G.be.map(v=>fmt(v,0)).join(' × ')} mm pack`,lim:`inside ${fmt(s.W-2*t,0)} × ${fmt(s.D-2*t,0)} × ${fmt(s.H-2*t,0)} mm`,st:fit?'pass':'fail',why:'Enlarge the fuselage or pick a smaller pack'});}
  const bm=modelInput('hover.batt_g')||0,pm=modelInput('hover.payload_g')||0,fm=last&&last.bom?last.bom.lines.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0):0,fr=modelInput('hover.frame_g')||0;
  const bx=G.bx,gx=G.gx,tot=bm+pm+fm+fr,xcg=tot?(bm*bx+pm*gx)/tot:0,lim=G.R*0.05;
  ck({id:'cg',label:'Centre of gravity over the thrust centre',val:`${xcg>=0?'+':''}${fmt(xcg,0)} mm ${xcg>=0?'forward':'aft'}`,lim:`within ±${fmt(lim,0)} mm (5 % of the arm)`,st:Math.abs(xcg)<=lim?'pass':Math.abs(xcg)<=2*lim?'warn':'fail',why:'Move the battery or payload along the body to balance it'});
  ck({id:'span',label:'Size across the props',val:`${fmt(G.span,0)} mm`,lim:AF.dock?`dock takes ${AF.dock.join(' × ')} mm`:'no dock size given',st:AF.dock?(G.span<=Math.min(...AF.dock)?'pass':'fail'):'pass'});
  return C;}
function cadMeshes(){part.updateMatrixWorld(true);const groups={};part.traverse(o=>{if(!o.isMesh||!o.geometry||!o.visible||o.name==='prop disc'||o.name===SHELL_MESH+' cut')return;(groups[o.name]||(groups[o.name]=[])).push(o);});return groups;}
function stlOf(meshes){const tris=[];meshes.forEach(m=>{const g=m.geometry.index?m.geometry.toNonIndexed():m.geometry,p=g.attributes.position,v=new THREE.Vector3();
    for(let i=0;i<p.count;i++){v.fromBufferAttribute(p,i).applyMatrix4(m.matrixWorld);tris.push(v.x,-v.z,v.y);}});
  const n=tris.length/9,buf=new ArrayBuffer(84+n*50),dv=new DataView(buf);dv.setUint32(80,n,true);
  for(let i=0;i<n;i++){const o=84+i*50;for(let k=0;k<9;k++)dv.setFloat32(o+12+k*4,tris[i*9+k],true);}return new Uint8Array(buf);}
async function glbOf(){if(!THREE.GLTFExporter)await new Promise((ok,no)=>{const sc=document.createElement('script');sc.src=FG_ASSETS.gltf;sc.onload=ok;sc.onerror=no;document.head.appendChild(sc);});
  const root=new THREE.Group();root.scale.setScalar(0.001);const groups=cadMeshes();Object.entries(groups).forEach(([nm,ms])=>{const gg=new THREE.Group();gg.name=nm;ms.forEach(m=>{const c=new THREE.Mesh(m.geometry,m.material);c.applyMatrix4(m.matrixWorld);c.name=nm;gg.add(c);});root.add(gg);});
  return new Promise(ok=>new THREE.GLTFExporter().parse(root,r=>ok(new Uint8Array(r)),{binary:true}));}
async function cadFiles(){const miss=missing3D();if(miss.length)throw new Error('3D missing for: '+miss.join(', '));const id=CP.id||'product',out=[],groups=cadMeshes(),all=Object.values(groups).flat();
  out.push({name:`${id}-assembly.glb`,type:'model/gltf-binary',data:await glbOf()});out.push({name:`${id}-assembly.stl`,type:'model/stl',data:stlOf(all)});
  Object.entries(groups).forEach(([nm,ms])=>{if(/^(prop|leg|arm|motor|arm hinge|motor pod) \d+$/.test(nm)&&!/ 1$/.test(nm))return;out.push({name:`${id}-${nm.replace(/ 1$/,'').replace(/\W+/g,'-')}.stl`,type:'model/stl',data:stlOf(ms)});});return out;}
async function saveFile(f){if(HOSTED){hostPost('fg:download',{name:String(f.name),mime:String(f.type||'application/octet-stream'),base64:b64Of(f.data)});return true;}try{const dl=window.claude&&claude.use?await claude.use('downloads'):null;if(dl){await dl.save({filename:f.name,data:new Blob([f.data],{type:f.type})});return true;}}catch(e){if(e&&e.code==='declined')return false;}
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([f.data],{type:f.type}));a.download=f.name;document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},2000);return true;}
window.__fgCad=async()=>{const fs=await cadFiles();return fs.map(f=>({name:f.name,b64:btoa(Array.from(f.data,c=>String.fromCharCode(c)).join(''))}));};
function renderAirframe(){const box=$('#afBody'),sec=$('#afBlk');if(!box)return;sec.hidden=!(AF||CTX);if(sec.hidden)return;
  box.innerHTML=(AF&&AFG?`<div class="afrow"><span>Props</span><b>${AFG.n} × ${fmt(AFG.pd/25.4,1)} in (${fmt(AFG.pd,0)} mm)</b></div><div class="afrow"><span>Motor to motor, diagonal</span><b>${fmt(2*AFG.R,0)} mm</b></div>
      <div class="afrow"><span>Across the props</span><b>${fmt(AFG.span,0)} mm</b></div><div class="afrow"><span>Payload</span><b>${esc(AFG.pay&&AFG.pay.part?AFG.pay.part.name:'typical size')}</b></div><div class="afrow"><span>Battery</span><b>${esc(AFG.bat&&AFG.bat.variant?AFG.bat.variant.label:'typical size')}</b></div><div class="afrow"><span>Battery position</span><b>${AFG.at==='balance'?'placed to balance, ':''}${fmt(AFG.bx,0)} mm from centre</b></div>
      <ul class="rlist" style="margin-top:6px">${AFC.map(c=>`<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div>${c.why&&c.st!=='pass'?`<div class="rv">${esc(c.why)}</div>`:''}</div><span></span></li>`).join('')}</ul>
      <p class="note">Arms, motors and props are a parametric layout from the prop size and the picked parts, for concept checks. The fuselage shell is the part held to manufacturing limits.</p>`:'')
    +`<div class="cadp"><button class="btn" type="button" id="cadExport">Export CAD (GLB and STL)</button><p class="note">The assembly as GLB, the whole as STL, and each part as STL, in millimetres. Openings and vents are drawn on the shell, not cut into the STL yet; STEP comes with the cloud design stage.</p></div>`;}
$('#afBody').addEventListener('click',async e=>{if(!e.target.closest('#cadExport'))return;const b=e.target.closest('#cadExport');b.disabled=true;b.textContent='Preparing CAD...';
  try{const fs=await cadFiles();let n=0;for(const f of fs){if(await saveFile(f))n++;else break;}toast(`${n} CAD file${n===1?'':'s'} saved.`);}catch(err){toast('CAD export failed: '+(err&&err.message||err));}
  b.disabled=false;b.textContent='Export CAD (GLB and STL)';});

/* ---------- Made parts: each with its own process, material and supplier ---------- */
const MFGP=CTX?(CTX.manufacturing||[]):[],ALLM=CTX?(CTX.machines||[]):[];S.mfgPick=S.mfgPick||{};
function matById(id){return Object.values(MATS).flat().find(m=>m.id===id)||(CTX&&(CTX.materials||[]).find(m=>m.id===id))||{id,name:id,density:1.2,costKg:5};}
function mfgDims(p,s){p=ppPart(p);const t=p.thickness||p.wall||2;if(p.dims)return p.dims.slice();if(!AF||!s)return null;const G=afGeom(s),ad=G.ad,t0=G.ang[0];
  switch(p.from){case'airframe.arm':{const r=[Math.cos(t0)*Math.min(s.W/2-8,G.half*0.72),G.yR,Math.sin(t0)*Math.min(s.D/2-8,G.half*0.72)],m=G.M[0];return[Math.hypot(m[0]-r[0],m[1]-r[1],m[2]-r[2]),ad,ad];}
    case'airframe.hinge':return[ad*G.hk,ad*G.hk,ad*G.hk*0.875];
    case'airframe.leg':return[G.yA-G.mot[1]*0.9+(G.legs.h||170),G.legs.d,G.legs.d];
    case'airframe.motor_mount':return[G.mot[0]*1.3,G.mot[0]*1.3,t];
    case'airframe.battery_tray':return[G.be[0]+6,G.be[1]+6,t];
    case'airframe.gimbal_mount':return[G.pe[0]+10,G.pe[1]+10,t];}return null;}
function mfgVol(p,d){if(p.shape==='tube'){const D=d[1],w=Math.min(p.wall||1,D/2);return Math.PI/4*(D*D-(D-2*w)**2)*d[0]/1000;}
  if(p.shape==='plate')return d[0]*d[1]*(p.thickness||d[2]||2)/1000*0.8;return d[0]*d[1]*d[2]/1000*0.35;}
function mfgCost(m,p,d,vol,mat){if(m.price&&/per part/i.test(m.price.basis||'')&&typeof m.price.value==='number')return m.price.value*(m.costFactor||1);const pr=m.process,q=Math.max(1,S.qty*(p.qty||1)),bbox=d[0]*d[1]*d[2]/1000,matC=vol*(mat.density||1)/1000*(mat.costKg||5);let c;
  if(pr==='sls'||pr==='mjf')c=3.5+vol*0.35+bbox*0.01;else if(pr==='fdm')c=1+vol*0.12;
  else if(pr==='cnc')c=180/q+bbox*1.2*(mat.density||2.7)/1000*(mat.costKg||7)+(12+vol*0.6)*1.25;
  else if(pr==='im')c=6000/q+matC*1.08+0.2;
  else if(pr==='cfrp-tube'){const pm=(m.price&&/metre/i.test(m.price.basis||'')&&typeof m.price.value==='number')?m.price.value:25;c=d[0]/1000*pm+1.5;}
  else if(pr==='cfrp-plate')c=4+d[0]*d[1]/100*0.06;else if(pr==='sheet')c=60/q+6+d[0]*d[1]/100*0.02+matC;else c=matC*3+5;
  return c*(m.costFactor||1);}
function mfgCheck(m,p,d,matId){const N=[],L=m.limits||{},ds=d.slice().sort((a,b)=>b-a);
  if(m.buildVolume){const bv=m.buildVolume.slice().sort((a,b)=>b-a);if(ds.some((v,i)=>v>bv[i]+1e-6))N.push('too big for it');}
  if(m.maxLength&&d[0]>m.maxLength)N.push(`longer than its ${m.maxLength} mm`);
  const t=p.shape==='plate'?(p.thickness||d[2]):(p.wall||2);if(L.wallMin&&t<L.wallMin-1e-6)N.push(`${p.shape==='plate'?'thickness':'wall'} under its ${L.wallMin} mm minimum`);
  if(m.materials&&!m.materials.includes(matId))N.push('does not offer this material');
  const q=m.qtyRange,qt=S.qty*(p.qty||1);if(q&&(qt<q[0]||qt>q[1]))N.push(`makes ${q[0].toLocaleString('en-GB')} to ${q[1].toLocaleString('en-GB')}`);return N;}
function mfgEval(s){return MFGP.map(p0=>{const p=ppPart(p0),d=mfgDims(p,s)||[10,10,10],vol=mfgVol(p,d);
  const procs=(p.candidates||[]).map(c=>{const m=ALLM.find(x=>x.id===c.machine);return m&&{process:m.process,material:c.material};}).filter(Boolean);
  const extra=ALLM.filter(m=>m.pending&&!(p.candidates||[]).some(c=>c.machine===m.id)).map(m=>{const k=procs.find(x=>x.process===m.process);return k&&{machine:m.id,material:k.material};}).filter(Boolean);
  const C=(p.candidates||[]).concat(extra).map(c=>{const m=ALLM.find(x=>x.id===c.machine);if(!m)return null;const mat=matById(c.material);
    return{m,mat,N:mfgCheck(m,p,d,c.material),cost:mfgCost(m,p,d,vol,mat),mass:vol*(mat.density||1),lead:m.leadDays};}).filter(Boolean);
  const pk=S.mfgPick[p.id]||p.pick;return{p,d,vol,C,pick:C.find(c=>c.m.id===pk)||C[0]};});}
function mfgSuppliers(s){if(!CTX||!MFGP.length||!s)return[];const set=new Set(mfgEval(s).filter(r=>r.pick).map(r=>r.pick.m.name));const sm=machineOf();if(sm)set.add(sm.name);return[...set];}
function mfgFails(){if(!CTX||!MFGP.length||!last||!last.shape)return 0;return mfgEval(last.shape).filter(r=>!r.pick||r.pick.N.length).length;}
function mfgJoinT(s){if(!CTX||!MFGP.length||!s)return 0;const per={bond:30,clamp:8,snap:3,press:5,rivet:6};return MFGP.reduce((a,p)=>{const j=p.join||{};return a+(per[j.method]||0)*(p.qty||1);},0);}
function renderMfg(){const box=$('#mfgBody'),sec=$('#mfgBlk');if(!box)return;sec.hidden=!(CTX&&MFGP.length);if(sec.hidden||!last||!last.shape)return;
  const R=mfgEval(last.shape),tot=R.reduce((a,r)=>a+(r.pick?r.pick.cost*(r.p.qty||1):0),0),mass=R.reduce((a,r)=>a+(r.pick?r.pick.mass*(r.p.qty||1):0),0),lead=Math.max(0,...R.map(r=>r.pick&&r.pick.lead||0));
  box.innerHTML=`<div class="obs">${gbp(tot)} a set · ${fmt(mass,0)} g · ${mfgSuppliers(last.shape).length} suppliers with the shell · longest lead ${lead} days</div>`+R.map(r=>{const c=r.pick,st=!c||c.N.length?'fail':'pass';
    return`<details class="opt"${S.open&&S.open['m:'+r.p.id]?' open':''} data-mo="${esc(r.p.id)}"><summary><span>${esc(r.p.name)} × ${r.p.qty||1}</span><b><i class="sdot s-${st}"></i> ${esc(c?c.m.name:'none')}</b></summary>
      <p class="note">${r.d.map(v=>fmt(v,0)).join(' × ')} mm${r.p.from?' from the generated '+esc(r.p.from.split('.')[1].replace('_',' ')):''} · ${fmt(r.vol,1)} cm³ · joined by ${esc((r.p.join||{}).method||'none')}${(r.p.join||{}).fastener?' ('+esc(r.p.join.fastener)+' × '+(r.p.join.count||0)+')':''}</p>
      <div class="ocands">${r.C.map(x=>`<div class="ocw"><button type="button" class="ocand${x===c?' on':''}" data-mfg="${esc(r.p.id)}" data-m="${esc(x.m.id)}" aria-pressed="${x===c}"><span class="ot">${esc(x.m.name)}<i class="sdot s-${x.N.length?'fail':'pass'}"></i></span>
        <span class="os">${esc([(PROC0[x.m.process]||{}).short||(CTX.processes&&CTX.processes[x.m.process]||{}).short||x.m.process,x.mat.name||x.mat.id,gbp(x.cost)+' each',fmt(x.mass,0)+' g',x.lead?x.lead+' days':'',srcLabel(x.m.source)].filter(Boolean).join(' · '))}</span>
        ${x.N.length?`<span class="on2">${esc(x.N.join(', '))}</span>`:'<span class="ok2">Can make this part</span>'}</button>${/^https?:/.test(x.m.source||'')?`<a class="olink" href="${esc(x.m.source)}" target="_blank" rel="noopener">rules</a>`:'<span></span>'}</div>`).join('')}</div></details>`;}).join('')
    +'<p class="note">Sizes follow the generated airframe, so a bigger prop or pack changes every cost and mass here. Costs are estimates for each process; ask the suppliers for quotes.</p>';}
$('#mfgBody').addEventListener('click',e=>{const b=e.target.closest('[data-mfg]');if(!b)return;const id=b.dataset.mfg,m=b.dataset.m;if((S.mfgPick[id]||(MFGP.find(p=>p.id===id)||{}).pick)===m)return;
  OBS={what:`${(MFGP.find(p=>p.id===id)||{}).name}: ${(ALLM.find(x=>x.id===m)||{}).name}`,before:snapNow(),after:null};S.mfgPick[id]=m;dbWrite('choices','mfg-'+id,{pick:m,at:new Date().toISOString()});schedule();});
$('#mfgBody').addEventListener('toggle',e=>{const d=e.target.closest('[data-mo]');if(!d)return;S.open=S.open||{};S.open['m:'+d.dataset.mo]=d.open;save();},true);

/* ---------- Every picked component in 3D ---------- */
function pickedParts(){return OPTS.filter(d=>d.kind==='part').map(d=>CPART[S.picks[d.id]||d.pick]).filter(Boolean);}
function missing3D(){if(!CTX)return[];return pickedParts().filter(p=>!(p.geometry&&p.geometry.envelope)).map(p=>p.name||p.id);}
// Parts that are not electronics blocks, the board, the battery or the airframe payload sit on the fuselage floor, drawn from their geometry
function addOthers(s,add){if(!s.t)return;const payId=AF?(()=>{const d=OPTS.find(o=>o.id===((AF.payload||{}).decision||'gim'));return d?(S.picks[d.id]||d.pick):null;})():null;
  const list=pickedParts().filter(p=>p.role==='other'&&p.id!==payId&&!afBoughtIds().includes(p.id)&&p.geometry&&p.geometry.envelope);let x=-s.W/2+s.t+4,z=-s.D/2+s.t+4,rowD=0;const y0=s.t+0.5;
  list.forEach(p=>{const [L,W,H]=p.geometry.envelope;if(x+L>s.W/2-s.t-4){x=-s.W/2+s.t+4;z+=rowD+4;rowD=0;}
    const g=p.geometry.body&&p.geometry.body.length?p.geometry:{body:[{shape:'box',at:[0,0,H/2],size:[L,W,H],color:'#2b2f36'}]};
    const items=[];addBody(g,x+L/2,y0,z+W/2,(gg,pull,kind,mat)=>items.push([gg,pull,kind,mat]),false);items.forEach(([gg,pull,kind,mat])=>add(gg,pull,kind,mat,p.name||p.id));x+=L+4;rowD=Math.max(rowD,W);});}

/* ---------- Parameters for every part, sectioned by part name ---------- */
S.pp=S.pp||{};
const PPKEY={'airframe.arm':['arm','wall'],'airframe.hinge':['hinge','wall'],'airframe.leg':['leg','wall'],'airframe.motor_mount':['mmount','t'],'airframe.battery_tray':['tray','t'],'airframe.gimbal_mount':['gmount','t']};
function ppPart(p){const k=PPKEY[p.from];if(!k||!S.pp[k[0]]||S.pp[k[0]][k[1]]==null)return p;const v=S.pp[k[0]][k[1]];return Object.assign({},p,k[1]==='t'?{thickness:v}:{wall:v});}
function mfgOf(from){const p=MFGP.find(x=>x.from===from);if(!p||!last||!last.shape)return null;const r=mfgEval(last.shape).find(x=>x.p.id===p.id);return r;}
function ppDefs(){const D=[];if(!CTX)return D;const a=AF||{},mp=f=>MFGP.find(x=>x.from===f),lim=(f,key)=>{const r=mfgOf(f);const L=r&&r.pick?(r.pick.m.limits||{}):{};return key==='wall'?(L.wallMin||null):null;};
  const sec=(id,name,sum,params)=>D.push({id,name,sum,params});
  if(AF){const pd0=Math.round((((modelInput('hover.prop_d'))||0.381)*1000)/25.4);
    sec('prop','Props',G=>`${G.n} × ${fmt(G.pd/25.4,1)} in`,[['din','Diameter',8,24,0.5,'in',pd0,'Changes arm length, mass and flight time'],['blades','Blades',2,4,1,'',a.blades||2,'']]);
    sec('arm','Arms',G=>`${G.n} × ${fmt(G.ad,0)} mm tube`,[['d','Tube diameter',12,40,1,'mm',a.arm_d||22,''],['wall','Wall',0.5,4,0.1,'mm',(mp('airframe.arm')||{}).wall||1.2,'wall:airframe.arm'],['gap','Prop tip gap',5,40,1,'%',12,'Space between neighbouring props as a share of the prop']]);
    sec('hinge','Arm hinges',G=>`${fmt(G.ad*G.hk,0)} mm block`,[['k','Size against the tube',1.2,2.6,0.05,'×',1.6,''],['wall','Wall',1,8,0.5,'mm',(mp('airframe.hinge')||{}).wall||3,'wall:airframe.hinge']]);
    sec('motor','Motors',G=>`${fmt(G.mot[0],0)} × ${fmt(G.mot[1],0)} mm`,[['d','Diameter',20,90,1,'mm',afMotorDefault()[0],''],['h','Height',12,60,1,'mm',afMotorDefault()[1],'']]);
    sec('leg','Landing legs',G=>`${fmt(G.legs.h,0)} mm tall`,[['h','Height',60,320,5,'mm',(a.legs||{}).h||170,'Must clear the payload'],['d','Diameter',8,30,1,'mm',(a.legs||{}).d||18,''],['wall','Wall',0.5,4,0.1,'mm',(mp('airframe.leg')||{}).wall||1,'wall:airframe.leg']]);
    if(mp('airframe.motor_mount'))sec('mmount','Motor mounts',()=>'',[['t','Thickness',1,8,0.5,'mm',mp('airframe.motor_mount').thickness||3,'wall:airframe.motor_mount']]);
    if(mp('airframe.battery_tray'))sec('tray','Battery tray',()=>'',[['t','Thickness',0.8,5,0.1,'mm',mp('airframe.battery_tray').thickness||2,'wall:airframe.battery_tray']]);
    if(mp('airframe.gimbal_mount'))sec('gmount','Gimbal mount plate',()=>'',[['t','Thickness',1,8,0.5,'mm',mp('airframe.gimbal_mount').thickness||3,'wall:airframe.gimbal_mount']]);
    sec('sensors','Nose sensors',()=>'',[['n','Count',0,9,1,'',a.nose_sensors||0,'']]);}
  return D;}
function ppVal(sec,p){return S.pp[sec]&&S.pp[sec][p[0]]!=null?S.pp[sec][p[0]]:p[6];}
function renderPartParams(s){const box=$('#partq');if(!box||!CTX)return;const G=AF&&s?afGeom(s):null,defs=ppDefs();
  const ss=$('#ppShellSum');if(ss&&s)ss.textContent=`${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm`;
  const sig=defs.map(d=>d.id+d.params.map(p=>p[0]).join()).join('|')+'|'+pickedParts().map(p=>p.id).join();
  if(box.dataset.sig!==sig){box.dataset.sig=sig;
    box.innerHTML=defs.map(d=>`<details class="pp" data-pps="${d.id}"${S.open&&S.open['pp:'+d.id]?' open':''}><summary><span>${esc(d.name)}</span><small data-ppsum="${d.id}"></small></summary><div class="ppb">${d.params.map(p=>`<div class="qs" data-ppq="${d.id}.${p[0]}" title="${esc(p[7]&&!p[7].startsWith('wall:')?p[7]:'')}"><div class="qt"><span>${esc(p[1])}</span><span class="held" hidden>At limit</span><b></b></div><input class="rng" type="range" min="${p[2]}" max="${p[3]}" step="${p[4]}" value="${ppVal(d.id,p)}" aria-label="${esc(d.name+' '+p[1])}"></div>`).join('')}</div></details>`).join('')
      +(pickedParts().length?`<details class="pp" data-pps="bought"${S.open&&S.open['pp:bought']?' open':''}><summary><span>Bought parts</span><small>${pickedParts().length} parts</small></summary><div class="ppb">${pickedParts().map(p=>{const e=p.geometry&&p.geometry.envelope;return`<div class="ro"><b style="color:var(--fg);font-weight:500">${esc(p.name)}</b><br>${e?e.map(v=>fmt(v,1)).join(' × ')+' mm, from its drawing':'no 3D yet'} · change it under Options</div>`;}).join('')}</div></details>`:'');}
  defs.forEach(d=>{const sm=box.querySelector(`[data-ppsum="${d.id}"]`);if(sm)sm.textContent=(G&&d.sum(G))||d.params.map(p=>fmt(ppVal(d.id,p),decOf(p[4]))+(p[5]?' '+p[5]:'')).join(' · ');
    d.params.forEach(p=>{const q=box.querySelector(`[data-ppq="${d.id}.${p[0]}"]`);if(!q)return;const v=ppVal(d.id,p),r=q.querySelector('.rng');if(Math.abs(+r.value-v)>p[4]/2)r.value=v;
      q.querySelector('b').textContent=fmt(v,decOf(p[4]))+(p[5]?' '+p[5]:'');let lo=p[2],hi=p[3];
      if(p[7]&&p[7].startsWith('wall:')){const r2=mfgOf(p[7].slice(5)),L=r2&&r2.pick?(r2.pick.m.limits||{}):{};if(L.wallMin)lo=Math.max(lo,L.wallMin);}
      const span=p[3]-p[2];r.style.setProperty('--lo',clamp((lo-p[2])/span*100,0,100)+'%');r.style.setProperty('--hi',clamp((hi-p[2])/span*100,0,100)+'%');
      const hd=q.querySelector('.held');hd.hidden=!(v<lo-1e-9);hd.textContent=v<lo-1e-9?'Below the maker limit':'At limit';});});}
$('#quick').addEventListener('input',e=>{const q=e.target.closest('[data-ppq]');if(!q)return;const [sec,k]=q.dataset.ppq.split('.');S.pp[sec]=S.pp[sec]||{};S.pp[sec][k]=+e.target.value;recomputeOverrides();schedule();});
$('#quick').addEventListener('toggle',e=>{const d=e.target.closest('[data-pps]');if(!d||d.dataset.pps==='shell')return;S.open=S.open||{};S.open['pp:'+d.dataset.pps]=d.open;save();},true);

/* ---------- Readiness from parts up ---------- */
const PSTAGES=[['def','Defined'],['geo','3D'],['src','Sourced or makeable'],['int','Integrated']];
function partReadiness(){const rows=[],s=last&&last.shape;if(!s)return rows;
  const row=(group,name,st,notes)=>{const r={group,name,st,notes:notes||[]};r.ready=PSTAGES.every(([k])=>st[k]==='pass'||st[k]==='na');rows.push(r);};
  const fails=lines=>EL?EL.checks.filter(c=>c.st==='fail'&&(c.lines||[]).some(l=>lines.includes(l))):[];
  // the shell
  {const dfm=last.rep?last.rep.rules.filter(r=>r.st==='fail'):[],bc=last.bom?last.bom.checks.filter(c=>['fitBoard','bossClr','stack','fitBatt'].includes(c.id)&&c.st==='fail'):[],room=EL?EL.checks.filter(c=>/^room/.test(c.id)&&c.st==='fail'):[];
    row('Designed',SHELL_NAME,{def:'pass',geo:'pass',src:dfm.length?'fail':'pass',int:bc.length||room.length?'fail':'pass'},[...dfm.map(r=>r.label),...bc.map(c=>c.label),...room.map(c=>c.label)]);}
  // made parts
  if(CTX&&MFGP.length)mfgEval(s).forEach(r=>{const c=r.pick,af=AFC.filter(x=>x.st==='fail');
    row('Made',`${r.p.name}${(r.p.qty||1)>1?' × '+r.p.qty:''}`,{def:'pass',geo:'pass',src:!c?'fail':c.N.length?'fail':'pass',int:af.length?'fail':'pass'},[...(c?c.N:['no maker picked']),...af.map(x=>x.label)]);});
  // bought parts
  if(CTX){const payId=AF?(()=>{const d=OPTS.find(o=>o.id===((AF.payload||{}).decision||'gim'));return d?(S.picks[d.id]||d.pick):null;})():null;
    OPTS.filter(d=>d.kind==='part').forEach(d=>{const p=CPART[S.picks[d.id]||d.pick];if(!p)return;const N=[];
      const geo=p.geometry&&p.geometry.envelope?'pass':'fail';if(geo==='fail')N.push('no 3D from its drawing');
      const src=p.excluded||p.standIn?'open':p.mpn&&p.datasheet&&p.cost?'pass':'open';if(src!=='pass')N.push(p.excluded?'excluded, stand-in only':!p.cost?'no price yet':'no order code or datasheet');
      let int='na';const role=p.role||'';
      if(role.startsWith('fn:')||role==='board'||role==='battery'){const t=role==='board'?'e_board':role==='battery'?'e_batt':role.slice(3);const ns=G.nodes.filter(n=>n.type===t);
        if(!ns.length){int='open';N.push('not in the electronics');}else{const f=fails(ns.map(n=>lineOf(n)));int=f.length?'fail':'pass';f.forEach(c=>N.push(c.label));}}
      else if(p.id===payId)int=AFC.some(c=>c.st==='fail')?'fail':'pass';else N.push('wiring not modelled yet');
      row('Bought',d.label||p.name,{def:d.why?'pass':'open',geo,src,int},N);});}
  // airframe parts that are bought but not chosen yet
  if(AF){['motor','prop'].forEach(k=>{const d=OPTS.find(o=>o.id===k&&o.kind==='part');const nm=k==='motor'?'Motors':'Props';
    if(d){return;}row('Airframe',nm,{def:'pass',geo:'pass',src:'open',int:'pass'},[`no real ${k} picked yet; the size is a parameter`]);});}
  return rows;}
function renderPartReady(){const box=$('#partReady');if(!box)return;const R=partReadiness();if(!R.length){box.innerHTML='';return;}
  const ok=R.filter(r=>r.ready).length;let lastG='';
  box.innerHTML=`<div class="prsum"><b>${ok} of ${R.length} parts ready.</b> Each part passes ${PSTAGES.map(x=>x[1].toLowerCase()).join(', ')} before the system gates.</div>
    <table class="prt"><tr><th>Part</th>${PSTAGES.map(([k,l])=>`<th title="${esc(l)}">${esc(l.split(' ')[0])}</th>`).join('')}</tr>${R.map(r=>{const g=r.group!==lastG?`<tr class="g"><td colspan="5">${esc(r.group)}</td></tr>`:'';lastG=r.group;
      return g+`<tr class="${r.ready?'ok':''}" title="${esc(r.notes.join('; '))}"><td>${esc(r.name)}${r.notes.length&&!r.ready?`<br><small style="color:var(--muted)">${esc(r.notes.slice(0,2).join('; '))}</small>`:''}</td>${PSTAGES.map(([k])=>`<td><i class="pd ${r.st[k]}" aria-label="${esc(k+' '+r.st[k])}"></i></td>`).join('')}</tr>`;}).join('')}</table>
    <p class="note simple-only">Only parts that are not ready are listed. Switch to Engineer to see every part.</p>`;}

/* ---------- A view made for this product ---------- */
const VIEW=CFG.view||null;let viewApplied=false;
function srcVal(src){if(!src)return{v:null};const [k,r]=[src.split(':')[0],src.slice(src.indexOf(':')+1)];
  if(k==='model'){const v=MT1[r];return{v,unit:((MODELS.find(m=>m.id===r.split('.')[0])||{}).outputs||{})[r.split('.')[1]]&&MODELS.find(m=>m.id===r.split('.')[0]).outputs[r.split('.')[1]].unit};}
  if(k==='req'){const q=REQ.find(x=>x.id===r);if(q)return{st:q.st==='na'?'open':q.st,v:q.val};const c=boundReqs().find(x=>x.id===r);if(c){const b=bindState(c);return{st:b.st,v:b.val,share:b.share,limit:c.limit,op:c.op||'>='};}
    const cr=(PCFG.customRequirements||[]).find(x=>x.id===r);return cr?{st:'open',v:'no model',text:cr.target}:{v:null};}
  if(k==='check'){const pools=[AFC,DFA?DFA.C:[],REFC,EL?EL.checks:[],last&&last.rep?last.rep.rules:[],last&&last.bom?last.bom.checks:[]];for(const P of pools){const c=(P||[]).find(x=>x.id===r);if(c)return{st:c.st,v:c.val,lim:c.lim};}return{v:null};}
  if(k==='env'){if(!ENVM)return{v:null};const C=ENVM.C,w=C.find(c=>c.st==='fail')||C.find(c=>c.st==='warn')||C[0];return w?{st:w.st,v:w.val,lim:w.label+' · '+w.lim}:{v:null};}
  if(k==='parts'){const R=partReadiness(),ok=R.filter(x=>x.ready).length;return{st:ok===R.length?'pass':'open',v:`${ok} of ${R.length}`};}
  if(k==='af'){return{v:AFG?AFG[r]:null};}
  if(k==='bom'){const L=last&&last.bom?last.bom.lines:[];if(r==='cost')return{v:L.reduce((a,l)=>a+(l.qtyUnit?0:l.unit*l.qty),0)};if(r==='mass')return{v:L.reduce((a,l)=>a+(l.qtyUnit?0:l.mass*l.qty),0)};}
  return{v:null};}
function renderKQ(){const box=$('#kqBody'),sec=$('#kqBlk');if(!box)return;sec.hidden=!(VIEW&&VIEW.keyQuestions&&VIEW.keyQuestions.length);if(sec.hidden)return;
  box.innerHTML=VIEW.keyQuestions.map((q,i)=>{const r=srcVal(q.source);let st=r.st;const v=r.v;
    if(!st&&typeof v==='number'&&q.limit!=null)st=cmpOp(v,q.op||'>=',q.limit)?'pass':'fail';st=st||'open';
    const val=typeof v==='number'?fmt(v,q.dec!=null?q.dec:(Math.abs(v)>=100?0:1))+(q.unit||r.unit?' '+(q.unit||r.unit):''):(v==null?'n/a':String(v));
    const tgt=q.target||(q.limit!=null?`${q.op||'>='} ${q.limit}${q.unit?' '+q.unit:''}`:r.limit!=null?`${r.op} ${r.limit}`:(r.lim||''));
    return`<div class="kq st-${st}" data-kq="${i}"><span class="chip">${st}</span><span>${esc(q.label)}<small>${esc(tgt)}${r.share!=null?` · ${fmt(r.share*100,0)} % of runs pass`:''}</small></span><b>${esc(val)}</b></div>`;}).join('');}
$('#kqBody').addEventListener('click',e=>{const k=e.target.closest('[data-kq]');if(!k)return;const q=VIEW.keyQuestions[+k.dataset.kq];if(!q.section)return;
  const sec=document.querySelector(`#dfm > section[data-acc="${q.section}"]`);if(!sec)return;if(!sec.classList.contains('open'))sec.querySelector('.acc-h').click();sec.scrollIntoView({behavior:'smooth',block:'start'});});
function applyView(){if(!VIEW)return;const dfm=$('#dfm');
  if(VIEW.hideNa)document.documentElement.classList.add('hidena');
  if(VIEW.tryit==='mission'){const b=$('#devBtn');if(b)b.classList.add('vhide');}
  if(!viewApplied){viewApplied=true;const secs=[...dfm.querySelectorAll(':scope > section.blk')],by=t=>secs.find(x=>x.dataset.acc===t);
    const order=['Key questions'].concat(VIEW.tryit==='mission'?['Mission']:[],VIEW.sections||[]);const first=order.map(by).filter(Boolean);
    first.forEach(x=>dfm.appendChild(x));secs.filter(x=>!first.includes(x)).forEach(x=>dfm.appendChild(x));
    (VIEW.hide||[]).forEach(t=>{const x=by(t);if(x)x.classList.add('vhide');});
    ['Key questions','Mission'].forEach(t=>{const x=by(t);if(x&&!x.classList.contains('open'))x.querySelector('.acc-h').click();});}
  if(VIEW.studio){const quick=$('#quick'),pq=$('#partq');if(quick&&pq){const all=[...quick.querySelectorAll('details.pp')],by=id=>all.find(d=>d.dataset.pps===id);
    const want=VIEW.studio.map(by).filter(Boolean).concat(all.filter(d=>!VIEW.studio.includes(d.dataset.pps)));
    if(want.some((d,i)=>pq.children[i]!==d))want.forEach(d=>pq.appendChild(d));}}
  if(VIEW.summary&&last&&last.shape){$('#dims').textContent=VIEW.summary.map(x=>{const r=srcVal(x.source),v=r.v;return`${x.label} ${typeof v==='number'?(x.unit==='£'?gbp(v):fmt(v,x.dec!=null?x.dec:(Math.abs(v)>=100?0:1))+(x.unit?' '+x.unit:'')):'n/a'}`;}).join(' · ');}}
/* Mission mode: the product flies its mission against its models */
let MIS={cond:null,play:null};
function misCalc(){const M=VIEW.mission||{},legs=M.legs||[],conds=M.conditions||[{id:'calm',label:'Calm'}],c=conds.find(x=>x.id===MIS.cond)||conds[0];
  const num=x=>typeof x==='number'?x:typeof x==='string'?(modelInput(x)||1):1;
  const pr=MT1['hover.p_rotor_w'],pt=MT1['hover.p_total_w'],wh=modelInput('hover.wh')||0;if(!isFinite(pr)||!isFinite(pt)||!wh)return null;
  const other=pt-pr,pw=num(c.power),en=num(c.energy),E=wh*en;let t=0,used=0;const pts=[[0,100]],L=[];
  legs.forEach(l=>{const P=pr*(l.power||1)*pw+other,Wh=P*l.min/60;used+=Wh;t+=l.min;pts.push([t,Math.max(0,100*(1-used/E))]);L.push({name:l.name,min:l.min,P,left:100*(1-used/E)});});
  return{c,legs:L,pts,T:t,left:100*(1-used/E),reserve:(M.reserve||0.2)*100,E,events:M.events||[]};}
function renderMission(){const box=$('#misBody'),sec=$('#misBlk');if(!box)return;sec.hidden=!(VIEW&&VIEW.tryit==='mission');if(sec.hidden)return;const R=misCalc();
  if(!R){box.innerHTML='<p class="note">The mission needs the hover model.</p>';return;}const conds=(VIEW.mission||{}).conditions||[];
  const W=320,H=120,x=t=>8+t/R.T*(W-16),y=p=>H-14-p/100*(H-28),ok=R.left>=R.reserve;
  const path=R.pts.map((p,i)=>(i?'L':'M')+x(p[0]).toFixed(1)+' '+y(p[1]).toFixed(1)).join(' ');let t0=0;
  const segs=R.legs.map((l,i)=>{const a=x(t0);t0+=l.min;return`<rect x="${a.toFixed(1)}" y="${H-12}" width="${Math.max(1,x(t0)-a).toFixed(1)}" height="6" rx="2" fill="${['#9aa3ad','#6c757d','#3b82f6','#6c757d','#9aa3ad','#adb5bd'][i%6]}"><title>${esc(l.name)}</title></rect>`;}).join('');
  const ev=R.events.map(e=>`<line x1="${x(e.at)}" x2="${x(e.at)}" y1="8" y2="${H-14}" stroke="var(--warn)" stroke-dasharray="3 3"/><text x="${x(e.at)+3}" y="16" font-size="9" fill="var(--warn)">${esc(e.label||e.kind)}</text>`).join('');
  const fo=boundReqs().find(c=>c.id==='failover')||(PCFG.customRequirements||[]).find(c=>c.id==='failover');
  box.innerHTML=`<div class="mis"><div class="mrow2">${conds.map(c=>`<button type="button" class="chipbtn" data-mc="${esc(c.id)}" aria-pressed="${(MIS.cond||conds[0].id)===c.id}">${esc(c.label)}</button>`).join('')}<button type="button" class="btn" id="misPlay">Fly it</button></div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Battery through the mission"><line x1="8" x2="${W-8}" y1="${y(R.reserve)}" y2="${y(R.reserve)}" stroke="var(--fail)" stroke-dasharray="4 3"/><text x="${W-8}" y="${y(R.reserve)-3}" font-size="9" text-anchor="end" fill="var(--fail)">${fmt(R.reserve,0)} % reserve</text>
      <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>${segs}${ev}<line id="misCur" x1="8" x2="8" y1="8" y2="${H-14}" stroke="var(--fg)" opacity=".6"/></svg>
    <p class="prsum"><b class="${ok?'ok2':'on2'}">${ok?'Lands with':'Runs short:'} ${fmt(R.left,0)} % battery</b> after ${fmt(R.T,0)} min (${esc(R.c.label)}), against a ${fmt(R.reserve,0)} % reserve. <span id="misNow"></span></p>
    <div class="legs">${R.legs.map(l=>`<span>${esc(l.name)}</span><span>${fmt(l.min,0)} min · ${fmt(l.P,0)} W</span><span>${fmt(l.left,0)} %</span>`).join('')}</div>
    ${R.events.map(e=>`<p class="note">At ${fmt(e.at,0)} min: ${esc(e.label||e.kind)}.${fo?` Proved by "${esc(fo.id)}": ${esc(fo.text||'')}`:''}</p>`).join('')}</div>`;}
$('#misBody').addEventListener('click',e=>{const c=e.target.closest('[data-mc]');if(c){MIS.cond=c.dataset.mc;renderMission();return;}
  if(e.target.closest('#misPlay')){const R=misCalc();if(!R)return;cancelAnimationFrame(MIS.play);const t0=performance.now(),dur=5000;
    const step=()=>{const k=Math.min(1,(performance.now()-t0)/dur),t=k*R.T;const cur=$('#misCur');if(!cur)return;const W=320,xx=8+t/R.T*(W-16);cur.setAttribute('x1',xx);cur.setAttribute('x2',xx);
      let acc=0,leg=R.legs[R.legs.length-1];for(const l of R.legs){acc+=l.min;if(t<=acc){leg=l;break;}}let p=100;for(let i=1;i<R.pts.length;i++){if(t<=R.pts[i][0]){const a=R.pts[i-1],b=R.pts[i];p=a[1]+(b[1]-a[1])*(t-a[0])/((b[0]-a[0])||1);break;}p=R.pts[i][1];}
      const ev=R.events.find(e=>Math.abs(e.at-t)<R.T*0.03);$('#misNow').textContent=`Now: ${leg.name}, ${fmt(p,0)} % battery${ev?' · '+(ev.label||ev.kind):''}.`;
      part.position.y=(AF?AFL:0)+(leg.name.match(/land/i)?Math.max(0,1-(t-(R.T-leg.min))/leg.min)*80:leg.name.match(/launch|climb/i)?Math.min(1,t/Math.max(leg.min,0.1))*80:80);
      if(k<1)MIS.play=requestAnimationFrame(step);else part.position.y=AF?AFL:0;};step();}});

function afMotorDefault(){const m=AF&&AF.motor;if(m&&!Array.isArray(m)&&m.decision){const d=OPTS.find(o=>o.id===m.decision);const p=d&&CPART[S.picks[d.id]||d.pick];const e=p&&p.geometry&&p.geometry.envelope;
  if(e)return[e[0],e[2]];return m.fallback||[52,32];}return Array.isArray(m)?m:[52,32];}
function afBoughtIds(){if(!AF)return[];return[(AF.motor||{}).decision,AF.prop_decision].filter(Boolean).map(id=>{const d=OPTS.find(o=>o.id===id);return d?(S.picks[d.id]||d.pick):null;}).filter(Boolean);}
function ctxCompute(s){if(!CTX)return;REFC=refChecks(s);AFC=afChecks(s);levelChecks();DFA=dfaEval(s);runModels();
  // size the form around the real parts once, on first load; the user can change it afterwards
  if(EL&&!S.fitDone){const L0=layout(s),bc=last&&last.bom?last.bom.checks:bomChecks(s,bomLines(s),L0);
    const r=EL.checks.find(c=>/^room/.test(c.id)&&c.st==='fail'&&c.fix)||bc.filter(c=>['fitBoard','bossClr','fitBatt','stack'].includes(c.id)&&c.st==='fail'&&c.role).map(c=>({fix:{act:'role',role:c.role,target:c.target}}))[0];
    if(r&&(S.fitN||0)<12){S.fitN=(S.fitN||0)+1;setTimeout(()=>applyFix(r.fix),0);}else{S.fitDone=true;if(S.fitN)toast(`Sized the form around the parts: ${fmt(s.W,0)} × ${fmt(s.D,0)} × ${fmt(s.H,0)} mm.`);}}}
function renderCtx(){if(!CTX)return;renderOptions();const ul=$('#dfaList');$('#dfaBlk').hidden=!DFA;
  if(DFA)ul.innerHTML=DFA.C.map(c=>`<li class="rule st-${c.st}"><span class="chip">${c.st}</span><div class="rt"><div class="rl">${esc(c.label)}</div><div class="rv"><b>${esc(c.val)}</b> · ${esc(c.lim)}</div>${c.why&&c.st!=='pass'?`<div class="rv">${esc(c.why)}</div>`:''}</div><span></span></li>`).join('');
  renderModels();renderContextPanel();renderRefs();renderAirframe();renderMfg();renderKQ();renderMission();applyView();}
OPTS.forEach(d=>{const id=S.picks[d.id]||d.pick;if(d.candidates.includes(id))applyPick(d,id,true);});
if(CTX){autoWire(null);}
window.__fg=()=>({misCalc,srcVal,VIEW,partReadiness,ppDefs,mfgEval,MFGP,AFG,AFC,AF,REFC,REFS,REFO,recompute,ENGINE,CTX,S,EB,PROC,DFA,MT1,MCR,last,gateState,scenList,scenEval,bodyMats,EL});

/* ================= Recompute loop ================= */
let pending=false;
function schedule(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;recompute();});}
function recompute(){
  applyMachine();const ev=solve();EL=ev.shape?elecAnalyse(ev.shape):null;styleMaterial();buildPart(ev.shape);updateNodeReadouts(ev);ev.rep=renderPanel(ev);last=ev;
  simRes=ev.shape?simulate(ev.shape):null;ENVM=ev.shape?envModel(ev.shape):null;renderSim();scheduleJobs();REQ=reqEval(ev.shape);renderBOM();ctxCompute(ev.shape);renderQuick(ev);renderPartParams(ev.shape);renderElec();renderReqs();renderIntentExtras();renderEnv();renderCtx();renderGates();renderJobs();renderAcc();if(S.pview==='prog')renderProgramme();else PM=null;if(dev&&dev.open)renderDev();if(S.view==='sim')applySim(simAnim?simT:1);
  applyShown();
  if(ev.shape&&framedFor===null){frame(ev.shape);framedFor=1;}
  hostScreening(ev);
  save();
}
let saveT=0;function save(){clearTimeout(saveT);saveT=setTimeout(()=>{if(!RESETTING)store.set(SKEY,{G,S,product:CP.id||'default',engine:ENGINE,cfg:CFGH});},300);}

/* The part, or the calm empty state when the config has nothing to draw. */
function applyShown(){part.visible=SHOW_PART;if(grid)grid.visible=SHOW_PART;ground.visible=SHOW_PART;$('#viewEmpty').hidden=SHOW_PART;
  ['#dims','#cmf','#legend'].forEach(q=>{const el=$(q);if(el)el.classList.toggle('fg-off',!SHOW_PART);});}
function startShaping(){if(SHOW_PART||READONLY)return;SHOW_PART=true;framedFor=null;}
['#quick','#nodes'].forEach(q=>['input','change'].forEach(t=>$(q).addEventListener(t,startShaping,true)));

/* ================= Header actions, hand off ================= */
$('#reset').addEventListener('click',()=>{RESETTING=true;clearTimeout(saveT);try{localStorage.removeItem(SKEY);}catch(e){}location.reload();});
function toast(msg){const t=document.createElement('div');t.className='toast';t.setAttribute('role','status');t.textContent=msg;document.body.appendChild(t);setTimeout(()=>t.remove(),2200);}
function showText(txt){$('#ioBox').hidden=false;const io=$('#io');io.value=txt;io.focus();io.select();$('#ioApply').hidden=true;}
function copy(txt,what){try{navigator.clipboard.writeText(txt).then(()=>toast(what+' copied.'),()=>{showText(txt);toast('Select the text below and copy it.');});}catch(e){showText(txt);}}
function spec(){const s=last&&last.shape;if(!s)return null;const P=PROC[S.proc];const d=derive(s);
  return{tool:'Formgraph Studio',brand:'MetaForge',units:'mm, degrees',process:P.name,material:P.mat,finish:finishOf().name,volume_units:S.qty,
    form:{width:+fmt(s.W,2),depth:+fmt(s.D,2),height:+fmt(s.H,2),corner_radius:+fmt(s.R,2),top_fillet:+fmt(s.F,2),draft_deg:+fmt(s.A,2)},
    shell:s.t?{wall:+fmt(s.t,2),inside_corner_radius:+fmt(d.ri,2),inside_top_fillet:+fmt(Math.max(d.fi,0),2),open_face:'bottom'}:null,
    vent_slots:s.slots&&s.slots.n?{count:s.slots.n,width:+fmt(s.slots.w,2),length:+fmt(d.slotL,2),gap:s.slots.n>1?+fmt(d.gap,2):null,face:'top'}:null,
    bosses:s.bosses&&s.bosses.n?{count:s.bosses.n,hole_dia:+fmt(s.bosses.d,2),outer_dia:+fmt(2*s.bosses.d,2)}:null,
    ribs:s.ribs?{thickness:+fmt(s.ribs.t,2),height:+fmt(s.ribs.h,2),layout:'cross on X and Z'}:null,
    dfm:last.rep?last.rep.rules.map(r=>({check:r.label,value:r.val,limit:r.lim,status:r.st})):[]};}
$('#cpSpec').addEventListener('click',()=>{const s=spec();if(!s)return toast('Connect a form first.');copy(JSON.stringify(s,null,2),'Build spec');});
$('#cpReport').addEventListener('click',()=>{if(!last||!last.rep)return toast('Connect a form first.');const P=PROC[S.proc];
  copy([`DFM report, ${P.name} (${P.mat}, ${finishOf().name}), ${S.qty.toLocaleString('en-GB')} units`,$('#dims').textContent,'',...last.rep.rules.map(r=>`[${r.st.toUpperCase()}] ${r.label}: ${r.val} (${r.lim})`)].join('\n'),'Report');});
$('#cpGraph').addEventListener('click',()=>copy(JSON.stringify({G,S}),'Graph'));
$('#ldGraph').addEventListener('click',()=>{$('#ioBox').hidden=false;$('#ioApply').hidden=false;$('#io').value='';$('#io').focus();});
$('#ioClose').addEventListener('click',()=>{$('#ioBox').hidden=true;});
$('#ioApply').addEventListener('click',()=>{try{const o=JSON.parse($('#io').value);const g=o.G||o;
  if(!Array.isArray(g.nodes)||!Array.isArray(g.links)||!g.nodes.every(n=>TYPES[n.type]&&n.id))throw new Error('shape');
  G={nodes:g.nodes,links:g.links.filter(l=>Array.isArray(l.from)&&Array.isArray(l.to))};if(o.S)Object.assign(S,o.S);
  selId=null;renderProcess();renderNodes();startShaping();framedFor=null;schedule();requestAnimationFrame(fitGraph);$('#ioBox').hidden=true;toast('Graph loaded.');}
  catch(e){toast('That text is not a Formgraph graph. Paste the output of Copy graph.');}});

/* ================= Theme + boot ================= */
const onTheme=()=>{themeScene();renderer.render(scene,camera);};
try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',onTheme);}catch(e){}
new MutationObserver(onTheme).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
if(CP.name){$('#fgName').textContent=CP.name;}
{const ft=$('.foot');if(ft)ft.textContent+=' Engine '+ENGINE+'.';}
if(PENDING_WIRE){autoWire(null);PENDING_WIRE=false;}
renderScenarios();progState();accInit();applySides();applyPview();
themeScene();applyView();renderProcess();renderSimInputs();renderNodes();resize();recompute();
requestAnimationFrame(()=>requestAnimationFrame(()=>{if(S.level==='eng')fitGraph();}));
window.addEventListener('resize',()=>requestAnimationFrame(drawWires));
if(HOSTED)hostPost('fg:ready',{engine:ENGINE},true);
})();
