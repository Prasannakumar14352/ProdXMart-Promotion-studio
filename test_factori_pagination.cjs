const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const scan=vm.runInNewContext(readFileSync(__dirname+'/factori-bridge/catalogue-scan.js','utf8')+';scanCatalogue');
const state=()=>({pages:0,total:null,cards:[],keys:new Set(),seenPages:new Set()});
const card=i=>({sku:'TEST-'+i,name:'Item '+i,price:100,cost:75});

test('32 catalogue pages, delayed DOM updates, duplicate SKU and final-page selection',async()=>{
  const list=state();
  let current=1, loading=0, moves=0;
  await scan(list,{
    snapshot:async()=>{
      const show=loading-->0?current-1:current;
      return {current,total:32,end:current===32,cards:[card(show),card(0)]};
    },
    advance:async n=>{assert.equal(n,current);moves++;current++;loading=3;return true;},
    progress:()=>{},wait:async()=>{}
  });
  assert.equal(moves,31);
  assert.equal(list.pages,32);
  assert.equal(list.cards.length,33);
  assert.equal(list.cards.at(-1).sku,'TEST-32');
});

test('unrecognized navigation pauses with progress, manual resume adds remaining products',async()=>{
  const list=state();
  let current=1;
  const actions={snapshot:async()=>({current,cards:[card(current)],end:current===2}),advance:async()=>false,progress:()=>{},wait:async()=>{}};
  await assert.rejects(scan(list,actions),/page 2/);
  assert.equal(list.pages,1);
  current=2;
  await scan(list,actions);
  assert.deepEqual(list.cards.map(p=>p.sku),['TEST-1','TEST-2']);
});

test('unchanged page is not counted twice',async()=>{
  const list=state();
  await assert.rejects(scan(list,{snapshot:async()=>({cards:[card(1)],end:false}),advance:async()=>true,progress:()=>{},wait:async()=>{}}),/page 2/);
  assert.equal(list.pages,1);
});

test('detects next, numbered pages and an explicitly disabled final control',()=>{
  function inspect(text,labels,n){
    const container={};
    const controls=labels.map(([label,disabled])=>({
      innerText:label,title:'',disabled,parentElement:container,
      matches:()=>false,getAttribute:()=>null,getClientRects:()=>[{}],querySelector:()=>null,
      closest:q=>q.includes('inert')?null:container
    }));
    const document={body:{innerText:text},querySelectorAll:()=>controls};
    return vm.runInNewContext(readFileSync(__dirname+'/factori-bridge/pagination.js','utf8')+';factoriPager('+n+')',{document});
  }
  assert.equal(inspect('Page 1 of 32',[['1'],['2'],['Next']],1).control.innerText,'2');
  assert.equal(inspect('31 / 32',[['Next']],31).end,false);
  assert.equal(inspect('Page 32 of 32',[['Next',true]],32).end,true);
  assert.equal(inspect('',[],1).end,false);
});

test('scans beyond 32 pages dynamically and deduplicates by SKU + variant', async () => {
  const list = state();
  let current = 1;
  await scan(list, {
    snapshot: async () => ({
      current,
      total: 40,
      end: current === 40,
      cards: current === 1
        ? [
            { sku: 'BAG-10', variant: 'Grey', name: 'Khadi Bag', price: 799, cost: 450 },
            { sku: 'BAG-10', variant: 'Blue', name: 'Khadi Bag', price: 799, cost: 450 },
          ]
        : current === 2
        ? [{ sku: 'BAG-10', variant: 'Grey', name: 'Khadi Bag', price: 799, cost: 450 }]
        : [card(current)],
    }),
    advance: async () => { current++; return true; },
    progress: () => {},
    wait: async () => {},
  });
  assert.equal(list.pages, 40);
  assert.equal(list.total, 40);
  // Page 1 (2 variants) + Page 2 duplicate skipped + Pages 3..40 (38 items) = 40 unique items.
  assert.equal(list.cards.length, 40);
  assert.equal(list.cards[0].variant, 'Grey');
  assert.equal(list.cards[1].variant, 'Blue');
  assert.equal(list.cards.at(-1).page, 40);
});
