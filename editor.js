const form = $('#product-form');
const dialog = $('#editor');
let editing = null;
let selection = [];
let saving = false;

function renderProducts() {
  if (!state) return;
  const query = $('#search').value.toLowerCase().trim();
  const filter = $('#filter').value;
  const products = state.products.filter(p => {
    const ready = p.issues.length === 0;
    const matches = [p.name,p.sku,p.category].some(v => v.toLowerCase().includes(query));
    return matches && (!filter || (filter === 'ready') === ready);
  });
  $('#count').textContent = products.length + (products.length === 1 ? ' product' : ' products');
  if (!products.length) {
    const empty = element('div',''); empty.className='empty';
    const fresh = state.products.length === 0;
    empty.append(element('h3',fresh?'Add your first product':'No matching products'),
      element('p',fresh?'Paste a ProdXMart link and add the details you want to use in a Reel.':'Try another search or change the readiness filter.'));
    if (fresh && mode()==='store') {
      const b=element('button','Add product'); b.onclick=()=>openEditor();empty.append(b);
    }
    $('#products').replaceChildren(empty);
    return;
  }
  const t=document.createElement('table');
  const h=t.createTHead().insertRow(), body=t.createTBody();
  ['Product','Price','Stock','Unit margin','Readiness',''].forEach(n=>h.append(element('th',n)));
  for (const p of products) {
    const tr=body.insertRow(), idCell=tr.insertCell(), id=element('div','');id.className='product-id';
    if(p.has_photo){
      const image=new Image(); image.alt=''; image.loading='lazy';
      image.src='/photo?'+new URLSearchParams({mode:mode(),sku:p.sku});
      id.append(image);
    }else{const blank=element('span',p.name.slice(0,1));blank.className='thumb-fallback';id.append(blank);}
    const name=element('div','');name.append(element('strong',p.name),element('small',p.sku+' · '+p.category));id.append(name);idCell.append(id);
    for (const [label,value] of [['Price',money(p.price)],['Stock',p.stock],['Unit margin',money(p.margin)]]){
      const cell=tr.insertCell();cell.dataset.label=label;cell.textContent=value;
    }
    const readiness=tr.insertCell(), tag=element('span',p.issues.length?'Needs attention':'Ready to promote');
    tag.className='tag'+(p.issues.length?' hold':'');readiness.append(tag);
    if(p.issues.length){const note=element('small',p.issues.join(' · '));note.className='why';readiness.append(note);}
    const actions=tr.insertCell();
    if(mode()==='store'){
      const b=element('button','Edit');b.setAttribute('aria-label','Edit '+p.name);b.onclick=()=>openEditor(p.sku);actions.append(b);
    }
  }
  $('#products').replaceChildren(t);
}
$('#search').oninput=renderProducts;$('#filter').onchange=renderProducts;

function margin(){
  const val=k=>form.elements[k].value;
  $('#margin').textContent=val('price')&&val('cost')&&val('fees')?
    'Estimated margin per unit  '+money(Number(val('price'))-Number(val('cost'))-Number(val('fees'))):'';
}
['price','cost','fees'].forEach(k=>form.elements[k].oninput=margin);
function showPhotos(){
  const box=$('#photo-list');box.replaceChildren();
  selection.forEach((item,i)=>{
    const tile=element('div','');tile.className='photo';
    const img=new Image();img.src=item.preview;img.alt='Product photo '+(i+1);
    const b=element('button','Remove');b.type='button';b.setAttribute('aria-label','Remove photo '+(i+1));
    b.onclick=()=>{if(item.file)URL.revokeObjectURL(item.preview);selection.splice(i,1);showPhotos();};
    tile.append(img,b);box.append(tile);
  });
}
async function openEditor(sku, imported){
  if(mode()!=='store'||saving)return;
  try{
    let p=null;
    if(sku){const r=await fetch('/product?'+new URLSearchParams({sku}));p=await r.json();if(!r.ok)throw Error(p.error);}
    if(mode()!=='store')return;
    editing=p;form.reset();$('#photo-status').textContent='';$('#form-error').textContent='';$('#form-title').textContent=p?'Edit product':'Add product';
    $('#categories').replaceChildren(...[...new Set((state?.products||[]).map(x=>x.category))].map(c=>element('option',c)));
    if(p)for(const k of ['product_url','sku','name','category','price','cost','fees','stock','feature_te'])form.elements[k].value=p[k];
    form.elements.sku.readOnly=!!p;form.elements.media_ok.checked=p?.media_ok?.toLowerCase()==='yes';
    selection=(p?.photos||[]).map((name,index)=>({name,preview:'/photo?'+new URLSearchParams({mode:'store',sku,index})}));
    if(imported){
      form.elements.price.value=imported.price;
      form.elements.cost.value=imported.cost;
      if(imported.name && (!p || imported.fullName || !/…|\.\.\./.test(imported.name)))form.elements.name.value=imported.name;
      if(!p)form.elements.sku.value=imported.sku || '';
      if(imported.product_url)form.elements.product_url.value=imported.product_url;
      if(Number.isSafeInteger(imported.stock) && imported.stock>=0)form.elements.stock.value=imported.stock;
      const urls=(imported.images || []).slice(0,Math.max(0,6-selection.length));
      const results=await Promise.allSettled(urls.map(downloadProductPhoto));
      if(mode()!=='store' || importedCard!==imported)return;
      for(const result of results){
        if(result.status==='fulfilled')selection.push({file:result.value,preview:URL.createObjectURL(result.value)});
      }
      if(results.some(r=>r.status==='fulfilled'))form.elements.media_ok.checked=false;
      const failed=results.filter(r=>r.status==='rejected');
      if(failed.length)$('#form-error').textContent=failed.length+' photo(s) could not be imported. '+failed[0].reason.message+' You can choose the file or paste the photo.';
    }
    showPhotos();margin();dialog.showModal();dialog.querySelector('.form-scroll').scrollTop=0;
    (imported && p ? form.elements.price : form.elements.product_url).focus();
  }catch(e){notice(e.message,true);}
}
async function downloadProductPhoto(url){
  const parsed=new URL(url);
  if(['cdn.factori.com','prodxmart.com','www.prodxmart.com'].includes(parsed.hostname)){
    try{
      const data=await readerRequest('store-photo',url);
      const bytes=Uint8Array.from(atob(data.base64),char=>char.charCodeAt(0));
      return new File([bytes],'Factori product photo',{type:data.mime});
    }catch{
      // The existing Studio downloader also supports public HTTPS redirects.
    }
  }
  const response=await fetch('/product-photo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode:'store',url})});
  if(!response.ok)throw Error((await response.json()).error || 'Photo download failed.');
  return new File([await response.blob()],'Product photo.jpg',{type:'image/jpeg'});
}
$('#fetch-photos').onclick=async()=>{
  if(saving)return;
  const button=$('#fetch-photos'), status=$('#photo-status');
  const link=form.elements.product_url.value.trim();
  if(!link){status.textContent='Enter the ProdXMart product page link first.';return;}
  if(selection.length>=6){status.textContent='Remove a photo to make space first.';return;}
  saving=true;button.disabled=true;$('#save').disabled=true;
  try{
    status.textContent='Reading the saved product page…';
    const sku=form.elements.sku.value.trim().toLowerCase();
    let product;
    try{
      product=await readerRequest('store-details',link);
    }catch{
      status.textContent='Trying the public product page…';
    }
    if(!product?.images?.length && !(sku && product?.sku && product.sku.toLowerCase()!==sku)){
      const response=await fetch('/store-photos',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({mode:'store',url:link,sku,name:form.elements.name.value})});
      const data=await response.json();
      if(!response.ok)throw Error(data.error || 'Could not read the public product page.');
      product=data;
    }
    if(sku && product.sku && product.sku.toLowerCase()!==sku)throw Error('The page SKU differs from this product. Check the saved product link.');
    const urls=product.images.filter(url=>!selection.some(item=>item.url===url)).slice(0,6-selection.length);
    if(!urls.length)throw Error(product.images.length?'These photos are already selected.':'No gallery photos found. Open the listing, copy the product image and paste it here.');
    status.textContent='Downloading product photos…';
    const results=await Promise.allSettled(urls.map(downloadProductPhoto));
    let added=0;
    results.forEach((result,index)=>{
      if(result.status==='fulfilled'){
        selection.push({url:urls[index],file:result.value,preview:URL.createObjectURL(result.value)});
        added++;
      }
    });
    if(added){form.elements.media_ok.checked=false;showPhotos();}
    const error=results.find(result=>result.status==='rejected');
    status.textContent=added+' photo(s) added.'+(error?' '+error.reason.message:' Review and save the product.');
  }catch(error){status.textContent=error.message;}
  finally{saving=false;button.disabled=false;$('#save').disabled=false;}
};
$('#add').onclick=()=>openEditor();
function closeForm(){if(!saving)dialog.close();}
$('#cancel').onclick=closeForm;$('#close').onclick=closeForm;
dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
dialog.addEventListener('close',()=>{selection.filter(x=>x.file).forEach(x=>URL.revokeObjectURL(x.preview));selection=[];});
function addPhotos(files){
  if(saving)return;
  $('#form-error').textContent='';
  if(selection.length+files.length>6 || files.some(f=>f.size>5*1024*1024)){
    $('#form-error').textContent='Select up to six photos, no larger than 5 MB each.';return;
  }
  for(const file of files)selection.push({file,preview:URL.createObjectURL(file)});
  showPhotos();
}
$('#uploads').onchange=e=>{
  addPhotos([...e.target.files]);
  e.target.value='';
};
dialog.addEventListener('paste',event=>{
  const photos=Array.from(event.clipboardData?.files||[]).filter(file=>file.type.startsWith('image/'));
  if(!photos.length)return;
  event.preventDefault();
  addPhotos(photos);
});
function encoded(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onerror=()=>reject(Error('Could not read '+file.name));
    reader.onload=()=>resolve({base64:reader.result.split(',')[1]});reader.readAsDataURL(file);
  });
}
form.onsubmit=async e=>{
  e.preventDefault();if(saving)return;$('#form-error').textContent='';
  saving=true;$('#save').disabled=true;$('#save').textContent='Saving…';
  try{
    const links=$('#image-links').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    if(links.length+selection.length>6 && (links.length || selection.some(x=>x.file)))throw Error('Keep up to six photos in total.');
    const d=Object.fromEntries(new FormData(form));d.mode='store';d.version=editing?.version||'';d.media_ok=form.elements.media_ok.checked;
    d.keep=selection.filter(x=>!x.file).map(x=>x.name);
    d.new=[...await Promise.all(selection.filter(x=>x.file).map(x=>encoded(x.file))),...links.map(url=>({url}))];
    const r=await fetch('/product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    const j=await r.json();if(!r.ok)throw Error(j.error||'Could not save product.');
    dialog.close();importedCard=null;$('#factori-review').hidden=true;$('#search').value='';$('#filter').value='';openTab('catalogue');
    await refresh();notice(j.message);
  }catch(err){if(dialog.open)$('#form-error').textContent=err.message;else notice(err.message,true);}
  finally{saving=false;$('#save').disabled=false;$('#save').textContent='Save product';}
};
let importedCard = null;
function readerRequest(type,url){
  const readerId=document.documentElement.dataset.factoriReader;
  if(!readerId || readerId==='1')return Promise.reject(Error('Reload extension 1.3.2 or later and refresh Studio.'));
  return new Promise((resolve,reject)=>{
    const id=crypto.randomUUID();
    function finish(){
      clearTimeout(timer);
      window.removeEventListener('message',receive);
    }
    function receive(event){
      if(event.source!==window || event.origin!==location.origin || event.data?.studioResponse!==id)return;
      finish();
      if(event.data.value)resolve(event.data.value);
      else reject(Error(event.data.error || 'Extension unavailable.'));
    }
    const timer=setTimeout(()=>{finish();reject(Error('The product reader timed out.'));},20000);
    window.addEventListener('message',receive);
    window.postMessage({readerId,id,type,url},location.origin);
  });
}

function readFactoriImport(){
  const prefix = '#factori=';
  if(!location.hash.startsWith(prefix))return;
  const payload = location.hash.slice(prefix.length);
  history.replaceState(null, '', location.pathname + location.search);
  const card = JSON.parse(decodeURIComponent(payload));
  if(!card || typeof card.name !== 'string' || !card.name.trim() ||
      !Number.isFinite(card.price) || card.price <= 0 || !Number.isFinite(card.cost) || card.cost < 0){
    throw Error('Incomplete Factori pricing. Reopen the product card and try again.');
  }
  card.images=Array.isArray(card.images)?card.images.filter(url=>typeof url==='string').slice(0,6):[];
  importedCard = card;
  $('#factori-url').value=card.product_url || '';
  displayImport(card);
  if($('#factori-url').value)loadStoreDetails();
  else $('#factori-status').textContent='Paste the product link from your store to read its name, SKU, available units and gallery.';
}
function displayImport(card){
  const target = $('#factori-target');
  target.replaceChildren(new Option('Choose a product', ''), new Option('Add as a new product', 'new'));
  const sameName = state.products.filter(product => product.name === card.name);
  const match = card.sku ? state.products.find(p => p.sku.toUpperCase() === card.sku) :
    (sameName.length === 1 ? sameName[0] : null);
  for(const product of state.products){
    if(card.sku && product.sku.toUpperCase() !== card.sku)continue;
    target.add(new Option(product.name + ' · ' + product.sku, 'sku:' + product.sku));
  }
  if(match){
    target.value='sku:' + match.sku;
    if(!$('#factori-url').value)$('#factori-url').value=match.product_url || '';
  }
  else if(!state.products.length || card.sku)target.value='new';
  $('#factori-values').textContent = card.name + ' · Customer pays ' + money(card.price) +
    ' · Factori price ' + money(card.cost) + (Number.isFinite(card.profit) ? ' · Factori profit per sale ' + money(card.profit) : '');
  $('#factori-values').textContent+=' · SKU '+(card.sku || 'unavailable')+' · Units '+(card.stock ?? 'unavailable')+' · Photos '+card.images.length;
  $('#factori-apply').disabled=!target.value;
  $('#factori-review').hidden=false;
}
function importBusy(busy){
  for(const id of ['factori-target','factori-url','factori-read','factori-dismiss'])$('#'+id).disabled=busy;
  $('#factori-apply').disabled=busy || !$('#factori-target').value;
}
async function loadStoreDetails(){
  const card=importedCard;
  const url=$('#factori-url').value.trim();
  if(!card || !url)return;
  importBusy(true);
  $('#factori-status').textContent='Reading the product page…';
  try{
    if(!document.documentElement.dataset.factoriReader)await new Promise(resolve=>setTimeout(resolve,450));
    const details=await readerRequest('store-details',url);
    if(card!==importedCard)return;
    if(card.sku && details.sku && card.sku.toUpperCase()!==details.sku.toUpperCase())
      throw Error('The page SKU differs from the selected Factori card. Check the link.');
    card.name=details.name || card.name;
    card.fullName=Boolean(details.name);
    card.sku=details.sku || card.sku;
    card.stock=details.stock ?? card.stock;
    card.images=[...new Set([...details.images,...card.images])].slice(0,6);
    card.product_url=details.product_url;
    displayImport(card);
    $('#factori-status').textContent='Review the imported details. Enter any unavailable fields in the editor.';
  }catch(error){$('#factori-status').textContent=error.message;}
  finally{importBusy(false);}
}
$('#factori-read').onclick=loadStoreDetails;
$('#factori-target').onchange=e=>{
  $('#factori-apply').disabled=!e.target.value;
  if(!importedCard?.product_url){
    const product=state.products.find(p=>'sku:'+p.sku===e.target.value);
    $('#factori-url').value=product?.product_url || '';
  }
};
$('#factori-apply').onclick=async()=>{
  const target=$('#factori-target').value;
  if(!importedCard || !target)return;
  importBusy(true);
  $('#factori-status').textContent='Preparing the product and its photos…';
  try{await openEditor(target==='new' ? null : target.slice(4),importedCard);}
  finally{importBusy(false);$('#factori-status').textContent='Review the editor and save the product.';}
};
$('#factori-dismiss').onclick=()=>{importedCard=null;$('#factori-review').hidden=true;};
refresh().then(readFactoriImport).catch(e=>notice(e.message,true));
