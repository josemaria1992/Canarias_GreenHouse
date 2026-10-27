// Execute the actual mount function with two equally labelled type panels.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(process.argv[2]);
const code = fs.readFileSync(path.join(root, 'src/aquacrop/web/app.js'), 'utf8');
const start = code.indexOf('function mount(id, ...kids)');
const end = code.indexOf('\n  function toast(', start);
assert(start >= 0 && end > start);
const label = 'Edit (creates the next draft version)';
const first = {type_id:'pump', open:true, label};
const second = {type_id:'tank', open:false, label};
const container = {
  dataset:{view:'equipment-types'}, children:[first,second],
  querySelectorAll(selector) {
    if(selector === 'details > summary') return this.children.map(x => ({textContent:x.label,parentElement:x}));
    return [];
  },
  replaceChildren(...children){ this.children=children; }
};
const context = {$:()=>container,rendering:null,renderingView:null,edits:{seq:0,id:null},S:{view:'equipment-types'},pressed:0,fieldKeys:()=>[],drawn:0};
const mount = vm.runInNewContext('(' + code.slice(start,end) + ')', context);
const freshFirst = {type_id:'pump',open:false,label};
const freshSecond = {type_id:'tank',open:false,label};
mount('v-equipment-types', freshFirst, freshSecond);
console.log('TYPE_EDITOR_REFRESH',JSON.stringify({before:[first.open,second.open],after:container.children.map(x=>x.open)}));
assert.equal(freshFirst.open,false); // first editor closed because the second editor had the same label
