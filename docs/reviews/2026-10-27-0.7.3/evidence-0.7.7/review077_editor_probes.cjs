// Independent observations; minimal DOM, no rendered-browser claim.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=process.cwd();
const code=fs.readFileSync(path.join(root,'tests/js/editor_meter.test.js'),'utf8').split('const ortho =')[0]
 .replace('const test = require("node:test");','')
 .replace('require("../../src/aquacrop/web/router.js")',`require(${JSON.stringify(path.join(root,'src/aquacrop/web/router.js'))})`)
 .replace('require("../../src/aquacrop/web/hydraulic-editor.js")',`require(${JSON.stringify(path.join(root,'src/aquacrop/web/hydraulic-editor.js'))})`);
new Function('require',code+`
 const ed=editor({p:[100,200],v:[510,200]}); ed.insertMeter('e1','m');
 const m=ed.get().diagram.symbols.find(s=>s.node==='m');
 console.log('meter_on_10px_grid',JSON.stringify({meter:m,on_grid:m.x%10===0&&m.y%10===0}));
`)(require);
const source=fs.readFileSync(path.join(root,'src/aquacrop/web/twin-graph.js'),'utf8');
const newfn=source.slice(source.indexOf('function newDesign(v)'),source.indexOf('async function exportOverview'));
const G={loaded:{topo:{graph:{nodes:[{id:'old'}]}},dia:{diagram:{width:1000,height:800,grid:10,
 symbols:[{node:'old'}],routes:[{edge:'old'}],sketches:[{id:'old-pipe'}],placeholders:[{id:'old-tank'}],
 shapes:[{id:'old-box'}],texts:[{id:'old-label'}]}}},arm:'new',msg:{}};
let result;
new Function('G','build','draw',newfn+';return newDesign;')(G,(v,graph,diagram)=>{result={graph,diagram}},()=>{})({});
console.log('empty_design',JSON.stringify(result));
