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
async function openEditor(sku){
  if(mode()!=='store'||saving)return;
  try{
    let p=null;
    if(sku){const r=await fetch('/product?'+new URLSearchParams({sku}));p=await r.json();if(!r.ok)throw Error(p.error);}
    if(mode()!=='store')return;
    editing=p;form.reset();$('#form-error').textContent='';$('#form-title').textContent=p?'Edit product':'Add product';
    $('#categories').replaceChildren(...[...new Set((state?.products||[]).map(x=>x.category))].map(c=>element('option',c)));
    if(p)for(const k of ['product_url','sku','name','category','price','cost','fees','stock','feature_te'])form.elements[k].value=p[k];
    form.elements.sku.readOnly=!!p;form.elements.media_ok.checked=p?.media_ok?.toLowerCase()==='yes';
    selection=(p?.photos||[]).map((name,index)=>({name,preview:'/photo?'+new URLSearchParams({mode:'store',sku,index})}));
    showPhotos();margin();dialog.showModal();dialog.querySelector('.form-scroll').scrollTop=0;form.elements.product_url.focus();
  }catch(e){notice(e.message,true);}
}
$('#add').onclick=()=>openEditor();
function closeForm(){if(!saving)dialog.close();}
$('#cancel').onclick=closeForm;$('#close').onclick=closeForm;
dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
dialog.addEventListener('close',()=>{selection.filter(x=>x.file).forEach(x=>URL.revokeObjectURL(x.preview));selection=[];});
$('#uploads').onchange=e=>{
  const files=[...e.target.files];e.target.value='';$('#form-error').textContent='';
  if(selection.length+files.length>6 || files.some(f=>f.size>5*1024*1024)){
    $('#form-error').textContent='Select up to six photos, no larger than 5 MB each.';return;
  }
  for(const file of files)selection.push({file,preview:URL.createObjectURL(file)});
  showPhotos();
};
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
    dialog.close();$('#search').value='';$('#filter').value='';openTab('catalogue');
    await refresh();notice(j.message);
  }catch(err){if(dialog.open)$('#form-error').textContent=err.message;else notice(err.message,true);}
  finally{saving=false;$('#save').disabled=false;$('#save').textContent='Save product';}
};
refresh().catch(e=>notice(e.message,true));
