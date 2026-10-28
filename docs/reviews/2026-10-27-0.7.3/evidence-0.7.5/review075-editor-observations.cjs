// Independent observations at 7a70ee6; assertions intentionally capture defects.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const sourceRoot = path.resolve(process.argv[2]);
const blocks = fs.readFileSync(path.join(sourceRoot, 'src/aquacrop/web/blocks.js'), 'utf8');
const start = blocks.indexOf('function dutyPicker(');
const end = blocks.indexOf('\n  }', start) + 4;
const h = (tag, attrs, ...kids) => ({tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter(Boolean)});
const picker = vm.runInNewContext('(' + blocks.slice(start, end) + ')', {h});
const body = {members: ['pump-a', 'pump-b'], duty: null};
const dom = picker(body, body.members.map(id => ({id, kind: 'pump'})), {});
function find(x, tag) { return (x && x.tag === tag ? [x] : []).concat(...((x && x.kids) || []).map(c => find(c, tag))); }
const selects = find(dom, 'select');
selects[0].attrs.onchange({target: {value: 'pump-a'}});
selects[1].attrs.onchange({target: {value: 'pump-b'}});
console.log('DUTY_PICKER after selecting both pumps:', JSON.stringify(body.duty));
assert.equal(body.duty, null);

function fakeEl(tag) {
  return {tag, children: [], attrs: {}, style: {}, dataset: {}, classList: {toggle(){},add(){},remove(){}},
    setAttribute(k,v){this.attrs[k]=v;}, getAttribute(k){return this.attrs[k];}, appendChild(c){this.children.push(c);return c;},
    append(...c){this.children.push(...c);}, replaceChildren(...c){this.children=c;}, addEventListener(){}, insertBefore(c){this.children.unshift(c);},
    get childNodes(){return this.children;}, focus(){},setPointerCapture(){},getScreenCTM(){return null;},
    set textContent(v){this._t=v;},set className(v){this.attrs.class=v;}};
}
global.document={createElementNS:(_,t)=>fakeEl(t),createElement:t=>fakeEl(t),createTextNode:text=>({text})};
global.window={};global.requestAnimationFrame=f=>setTimeout(f,0);global.cancelAnimationFrame=()=>{};
window.AqRouter=require(path.join(sourceRoot,'src/aquacrop/web/router.js'));
require(path.join(sourceRoot,'src/aquacrop/web/hydraulic-editor.js'));
const E=window.AqHydraulicEditor;
const symbols={tank:{w:100,h:120,ports:[{id:'out',flow:'out',side:'E',offset:0.85,label:'Outlet',many:true}]},
  sensor:{w:30,h:30,ports:[]},pump:{w:40,h:40,ports:[{id:'in',flow:'in',side:'W',offset:0.5,label:'Inlet'}]}};
const items={tank:{kind:'tank',name:'Tank',capacity_L:5000},level:{kind:'sensor',name:'Level',level:true},pump:{kind:'pump',name:'Pump'}};
const graph={nodes:Object.keys(items).map(id=>({id,item:id,kind:items[id].kind})),
  edges:[{id:'pipe',src:{node:'tank',port:'out'},dst:{node:'pump',port:'in'}}],
  measures:[{sensor:'level',node:'tank',mode:'TAP'}]};
const diagram={width:900,height:600,grid:10,symbols:[{node:'tank',x:100,y:100},{node:'level',x:120,y:120},{node:'pump',x:600,y:182}],routes:[{edge:'pipe',points:[]}]};
const ed=E.create(fakeEl('div'),{symbols,items,graph,diagram,readOnly:false});
const before=ed.get().diagram.symbols.find(s=>s.node==='level');
const moved=ed.moveSymbol('tank',1200,100,true);
const after=ed.get().diagram.symbols.find(s=>s.node==='level');
const tank=ed.get().diagram.symbols.find(s=>s.node==='tank');
console.log('REJECTED_TANK_MOVE',JSON.stringify({moved,tankX:tank.x,sensorBefore:before.x,sensorAfter:after.x}));
assert.equal(moved,false);
assert.equal(tank.x,100);
assert.notEqual(after.x,before.x);
