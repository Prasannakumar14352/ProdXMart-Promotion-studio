// Run with node --test test_factori_details.cjs
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function scripts(names, globals) {
  const context = vm.createContext(globals);
  for (const name of names) vm.runInContext(readFileSync(__dirname + '/factori-bridge/' + name, 'utf8'), context);
  return context;
}

test('storefront reads full product identity, exact available units and only its gallery', () => {
  const name = 'Grey Khadi Fabric Laptop Bag';
  const url = 'https://prodxmart.com/bags/grey-skufactori8432';
  const urls = ['front', 'back', 'inside', 'side'].map(view=>'https://cdn.factori.com/bag-8432-'+view+'.webp');
  const pics = [...urls, urls[1], 'https://cdn.factori.com/lamp-101-0.webp'].map(src=>({
    currentSrc:src, alt:'', closest:()=>null
  }));
  const main = {innerText:'Quantity\n1\n66 available', querySelector:()=>({textContent:name, closest:()=>null}), querySelectorAll:()=>pics};
  const document = {querySelectorAll:()=>[], querySelector:selector=>selector==='main' ? main : null};
  const context = scripts(['fields.js','storefront.js'], {URL, document, location:new URL(url),
    chrome:{runtime:{onMessage:{addListener(){}}}}});
  const product = JSON.parse(JSON.stringify(context.storeDetails()));
  assert.deepEqual(product, {name,sku:'SKUFACTORI8432',stock:66,images:urls,product_url:url});
  assert.equal(context.factoriStock('0 available'), 0);
  assert.equal(context.factoriStock('Stock: 1,234'), 1234);
  assert.equal(context.factoriStock('Quantity 1 · 22 ratings · In stock'), null);
  assert.equal(context.factoriStock('1 available\n3 available'), null);
  assert.equal(context.factoriSku('SKU: BAG-GREY-02'), 'BAG-GREY-02');
});

test('structured product supplies SKU, stock and image when visual labels are absent', () => {
  const product={'@type':'Product',name:'Lamp',sku:'LAMP-42',offers:{inventoryLevel:{value:'0'}},
    image:[{url:'https://cdn.factori.com/lamp-42.webp'}]};
  const main={innerText:'Quantity 1',querySelector:()=>null,querySelectorAll:()=>[]};
  const document={querySelectorAll:()=>[{textContent:JSON.stringify({'@graph':[product]})}],querySelector:s=>s==='main'?main:null};
  const ctx=scripts(['fields.js','storefront.js'],{URL,document,location:new URL('https://prodxmart.com/items/lamp'),
    chrome:{runtime:{onMessage:{addListener(){}}}}});
  const item=ctx.storeDetails();
  assert.equal(item.sku,'LAMP-42');
  assert.equal(item.stock,0);
  assert.equal(item.images[0],product.image[0].url);
});

test('SMP apparel gallery finds a lazy-loaded supplier photo without importing a related shirt', () => {
  const sku='SMP787747', name='Maroon Boys HI Neck Taqua Kids Printed Casual TShirt';
  const url='https://prodxmart.com/products/maroon-shirt-smp787747';
  const photo='https://retailer-images.example/SMP787747-front.webp';
  function lazyImage(src) {
    return {alt:'',currentSrc:'data:image/gif;base64,AA==',src:'',closest:()=>null,
      getAttribute:key=>key==='data-src'?src:null};
  }
  const main={innerText:'1,455 available', querySelector:()=>({textContent:name,closest:()=>null}),
    querySelectorAll:()=>[lazyImage(photo),lazyImage('https://retailer-images.example/SMP787748-front.webp')]};
  const document={querySelectorAll:()=>[],querySelector:s=>s==='main'?main:null};
  const ctx=scripts(['fields.js','storefront.js'],{URL,document,location:new URL(url),
    chrome:{runtime:{onMessage:{addListener(){}}}}});
  const result=ctx.storeDetails();
  assert.equal(result.sku,sku);
  assert.deepEqual(Array.from(result.images),[photo]);
});
