const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function addressForm(fetch) {
  const handlers = {}, events = [], formHandlers = {};
  let timer;
  const input = {value:'', setAttribute(){}, focus(){}, addEventListener(name, fn){handlers[name]=fn;}};
  const token = {value:'previous-address-token'};
  const results = {hidden:true, children:[], replaceChildren(){this.children=[];}, append(button){this.children.push(button);}, addEventListener(){}};
  const status = {textContent:''};
  const nodes = {'[data-address-input]':input, '[name="address_token"]':token, '.address-results':results, '.address-status':status};
  const form = {querySelector:key=>nodes[key] || null, addEventListener(name,fn){formHandlers[name]=fn;}, hasAttribute:()=>false,
    classList:{contains:()=>false}, dispatchEvent:event=>events.push(event), contains:()=>true};
  const document = {querySelectorAll:key=>key==='[data-address-form]'?[form]:[], querySelector:()=>null, getElementById:()=>null,
    addEventListener(){}, createElement(){return {setAttribute(){}, addEventListener(name,fn){this[name]=fn;}};}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/market/market.js'),'utf8'), {
    document, window:{addEventListener(){}}, location:{hash:''}, fetch, AbortController,
    setTimeout(fn){timer=fn; return 1;}, clearTimeout(){timer=null;},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
  });
  return {input,token,results,status,events,
    async type(value){input.value=value;handlers.input();if(timer){const fn=timer;timer=null;await fn();}},
    async select(){await results.children[0].click();},
  };
}

for (const level of ['city','street']) {
  test(`selecting a ${level} continues typing without verifying a house`,async()=>{
    const calls=[];
    const partial={value:level==='city'?'Московская обл, г Одинцово':'Московская обл, г Одинцово, ул Ленина',refine:true,level};
    const ui=addressForm(async url=>{calls.push(url);return {ok:true,json:async()=>({results:[partial]})};});
    await ui.type(level==='city'?'Оди':'Одинцово, Лени');
    assert.equal(ui.results.hidden,false);
    await ui.select();
    assert.equal(ui.input.value,partial.value+', ');
    assert.equal(ui.token.value,'');
    assert.equal(ui.events.length,0);
    assert.equal(calls.length,1);
    assert.match(ui.status.textContent,level==='city'?/улиц/:/дом/);
  });
}

test('street prefix can be completed to a verified house',async()=>{
  const calls=[];
  const ui=addressForm(async url=>{
    calls.push(url);
    if(url.startsWith('/addresses/resolve/'))return {ok:true,json:async()=>({result:{value:'Одинцово, ул Ленина, д 1',token:'signed-house',latitude:'55.67',longitude:'37.27'}})};
    return {ok:true,json:async()=>({results:calls.length===1?[{value:'Одинцово, ул Ленина',refine:true,level:'street'}]:[{value:'Одинцово, ул Ленина, д 1',selection_token:'selected-house'}]})};
  });
  await ui.type('Одинцово, Лени');await ui.select();
  await ui.type(ui.input.value+'1');await ui.select();
  assert.equal(ui.token.value,'signed-house');
  assert.equal(ui.events.length,1);
  assert.equal(ui.events[0].type,'address:selected');
  assert.equal(ui.events[0].detail.latitude,'55.67');
  assert.equal(calls.filter(url=>url.startsWith('/addresses/resolve/')).length,1);
});
