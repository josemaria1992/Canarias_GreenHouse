// Pure-model/source-function observations; not rendered-browser acceptance.
const fs=require('fs'),path=require('path');
const M=require(path.resolve('src/aquacrop/web/type-form.js'));
const T=JSON.parse(fs.readFileSync(process.argv[2],'utf8').replace(/^\uFEFF/,''));
const f=M.startFrom('button',T); f.name='Switch';
console.log('new_button_without_polarity_answer',JSON.stringify({roles:f.roles,problems:M.problems(f)}));
const r=M.startFrom('flow',T);r.name='Pressure';
// The actual readingRow change handler updates these three presentation fields only.
r.roles[0].measure='pressure';r.roles[0].label='Pressure';r.roles[0].unit='bar';
console.log('measurement_after_round_trip',JSON.stringify(M.fromBody(M.toBody(r),{name:r.name}).roles[0]));
const src=fs.readFileSync('src/aquacrop/web/twin-graph.js','utf8');
const code=src.slice(src.indexOf('function migrationBox(v)'),src.indexOf('function finishBox()'));
const G={editing:false,migration:null,loaded:{topo:{graph:{nodes:[],edges:[],measures:[]}},dia:{diagram:{symbols:[],routes:[]}}},
 rec:{drawing:'legacy',ready:true,questions:[{id:'old',answer:'yes'}],blockers:[]}};
const render=new Function('G','reconcileBox',code+';return migrationBox;')(G,(_v,m)=>({showsReady:m.ready,currentNodes:G.loaded.topo.graph.nodes.length}));
console.log('saved_empty_design_reconciliation',JSON.stringify(render({})));
