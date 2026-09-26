const crossSection=document.getElementById('validation');
const crossMap=document.getElementById('cross-visual');
const componentButtons=[...document.querySelectorAll('[data-component]')];
window.refreshCrossVisuals=function(index){
 crossMap.dataset.scenario=String(index);
 const selected=index===2?1:0;
 componentButtons.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===selected)));
 document.getElementById('candidate-a-state').textContent=selected===0?'Selected':'Compare';
 document.getElementById('candidate-b-state').textContent=selected===1?'Selected':'Compare';
 const text={
 'node-mech-state':['Baseline evidence','Previous load: stale','New interface: recheck'],
 'node-power-state':['Static torque: pass','Static torque: fail','Static torque: pass'],
 'node-thermal-state':['Example budget: pass','New load: re-evaluate','New component: evaluate'],
 'map-revision':['REV A / 2 KG LOAD CASE','REV A / 4 KG LOAD CASE','REV B / 4 KG LOAD CASE'],
 'structural-output-state':['Example baseline','Stale evidence','Stale evidence'],
 'thermal-output-state':['Example baseline','Stale evidence','Not evaluated'],
 'structural-output-copy':['Connect the result to the geometry and load case it evaluated.','The previous 2 kg result does not validate the new 4 kg load.','A different actuator changes the interface. Re-evaluate the housing.'],
 'thermal-output-copy':['Review the power assumptions before comparing heat with cooling capacity.','The previous power estimate is stale. Recalculate losses for the new load.','The new actuator needs new thermal inputs. This image is not evidence.'],
 'selection-load':['2 kg payload','4 kg payload','4 kg payload'],
 'selection-result':['Actuator A meets the example static load.','Actuator A falls below the example torque demand.','Actuator B clears the example static torque check.'],
 'selection-followup':['Physical verification and controller evaluation are still required.','Select the higher-torque candidate to explore a revised design.','Check the new mounting, driver, cooling and control requirements before proceeding.']};
 for(const [id,values] of Object.entries(text))document.getElementById(id).textContent=values[index];
};
componentButtons.forEach((b,i)=>b.addEventListener('click',()=>showScenario(i===1?2:activeScenario===0?0:1)));
document.querySelectorAll('[data-evidence]').forEach(b=>b.addEventListener('click',()=>{
 const index=Number(b.dataset.evidence);showEvidence(index);
 document.querySelectorAll('[data-evidence]').forEach(node=>node.setAttribute('aria-pressed',String(Number(node.dataset.evidence)===index)));
 document.getElementById('evidence-detail').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});
}));
const crossMotion=document.getElementById('cross-motion'),crossPreference=matchMedia('(prefers-reduced-motion: reduce)');let crossPaused=crossPreference.matches,crossVisible=true;
function syncCrossMotion(){crossSection.classList.toggle('cross-stopped',crossPaused||!crossVisible||document.hidden);crossMotion.textContent=crossPaused?'▶ Play motion':'Ⅱ Pause motion';crossMotion.setAttribute('aria-label',crossPaused?'Play cross-domain animation':'Pause cross-domain animation')}
crossMotion.addEventListener('click',()=>{crossPaused=!crossPaused;syncCrossMotion()});crossPreference.addEventListener('change',e=>{if(e.matches)crossPaused=true;syncCrossMotion()});document.addEventListener('visibilitychange',syncCrossMotion);
if('IntersectionObserver'in window)new IntersectionObserver(entries=>{crossVisible=entries[0].isIntersecting;syncCrossMotion()},{threshold:.02}).observe(crossSection);
window.refreshCrossVisuals(activeScenario);syncCrossMotion();
