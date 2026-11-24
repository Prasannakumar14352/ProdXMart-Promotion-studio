const el = id=>document.getElementById(id);
const state = {pages:0,total:null,cards:[],keys:new Set(),seenPages:new Set()};
let source;
function render() {
  const query = el('search').value.trim().toLowerCase();
  const selected = el('list').value;
  const options = [];
  state.cards.forEach((card,index)=>{
    if (![card.name,card.sku].some(s=>(s || '').toLowerCase().includes(query))) return;
    options.push(new Option([card.name,card.sku,'₹'+card.price.toLocaleString('en-IN')].filter(Boolean).join(' · '),String(index)));
  });
  el('list').replaceChildren(...options);
  el('list').value=selected;
  if(el('list').selectedIndex<0 && options.length) el('list').selectedIndex=0;
  el('count').textContent=options.length+' of '+state.cards.length;
  el('review').disabled=el('list').selectedIndex<0;
}
function summary(){
  return state.pages+' page'+(state.pages===1?'':'s')+(state.total?' of '+state.total:'')+' read · '+state.cards.length+' products.';
}
async function scan(){
  el('recovery').hidden=true;
  try {
    if(source===undefined) source=(await chrome.tabs.create({url:'https://retailing.factori.com/dashboard/products',active:false})).id;
    await scanCatalogue(state,{
      snapshot:n=>chrome.tabs.sendMessage(source,{type:'catalogue-page',expectedPage:n}).catch(()=>null),
      advance:n=>chrome.tabs.sendMessage(source,{type:'catalogue-next',expectedPage:n}).catch(()=>false),
      wait:()=>new Promise(resolve=>setTimeout(resolve,400)),
      progress:()=>{render();el('status').textContent='Scanning · '+summary();}
    });
    el('status').textContent='Scan complete · '+summary();
    await chrome.tabs.remove(source).catch(()=>{}); source=undefined;
  } catch(error) {
    el('status').textContent=summary()+' '+error.message;
    el('recovery').hidden=false;
  }
}
el('show').onclick=()=>chrome.tabs.update(source,{active:true}).catch(()=>{el('status').textContent='The Factori tab was closed. Reopen Browse all pages to start a new scan.';});
el('resume').onclick=scan;
el('search').oninput=render;
el('review').onclick=()=>{
  const card=state.cards[Number(el('list').value)];
  if(card)chrome.tabs.create({url:'http://127.0.0.1:8765/#factori='+encodeURIComponent(JSON.stringify(card))});
};
window.addEventListener('pagehide',()=>{if(source!==undefined)chrome.tabs.remove(source).catch(()=>{});});
scan();
