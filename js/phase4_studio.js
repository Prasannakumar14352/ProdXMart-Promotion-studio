(function () {
  let wizardStep = 1;
  let selectedProductionId = null;

  function make(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }

  function currentCampaignSelections() {
    const sel = document.getElementById('mkt-skus');
    return sel ? [...sel.selectedOptions].map(o => o.value).filter(Boolean) : [];
  }

  function renderWizard() {
    const stepper = document.getElementById('wizard-stepper');
    const body = document.getElementById('wizard-step-body');
    const hint = document.getElementById('wiz-step-hint');
    const prevBtn = document.getElementById('wiz-prev');
    const nextBtn = document.getElementById('wiz-next');
    if (!stepper || !body) return;

    const steps = [
      { n: 1, title: '1. Campaign Type' },
      { n: 2, title: '2. Products' },
      { n: 3, title: '3. Language' },
      { n: 4, title: '4. Guardrails' },
      { n: 5, title: '5. Generate' },
      { n: 6, title: '6. Handoff' },
    ];

    stepper.replaceChildren();
    for (const s of steps) {
      const b = make('button', s.title, 'wizard-step-pill' + (wizardStep === s.n ? ' active' : wizardStep > s.n ? ' done' : ''));
      b.type = 'button';
      b.onclick = () => { wizardStep = s.n; renderWizard(); };
      stepper.append(b);
    }

    body.replaceChildren();
    const typeEl = document.getElementById('mkt-campaign-type');
    const langEl = document.getElementById('mkt-language');
    const catEl = document.getElementById('mkt-category');
    const seaEl = document.getElementById('mkt-season');
    const skuSel = document.getElementById('mkt-skus');
    const selectedSkus = currentCampaignSelections();

    if (wizardStep === 1) {
      body.append(make('strong', 'Step 1 — Choose Campaign Format & Theme'));
      const grid = make('div', '', 'agent-config-grid');
      const cloneType = document.createElement('select');
      ['single_product', 'multi_product', 'category', 'seasonal'].forEach(v => {
        cloneType.add(new Option({ single_product: 'Single product spotlight', multi_product: 'Multi-product showcase', category: 'Category-based collection', seasonal: 'Seasonal / Festival theme' }[v], v));
      });
      cloneType.value = typeEl?.value || 'single_product';
      cloneType.onchange = () => {
        if (typeEl) {
          typeEl.value = cloneType.value;
          typeEl.dispatchEvent(new Event('change'));
        }
        renderWizard();
      };
      const l1 = document.createElement('label'); l1.append(document.createTextNode('Campaign type'), cloneType);
      grid.append(l1);

      if (cloneType.value === 'category') {
        const cIn = document.createElement('input'); cIn.value = catEl?.value || ''; cIn.placeholder = 'e.g. Bags';
        cIn.oninput = () => { if (catEl) catEl.value = cIn.value; };
        const lc = document.createElement('label'); lc.append(document.createTextNode('Category filter'), cIn);
        grid.append(lc);
      }
      if (cloneType.value === 'seasonal') {
        const sIn = document.createElement('input'); sIn.value = seaEl?.value || ''; sIn.placeholder = 'e.g. Sankranti / Winter Essentials';
        sIn.oninput = () => { if (seaEl) seaEl.value = sIn.value; };
        const ls = document.createElement('label'); ls.append(document.createTextNode('Seasonal theme'), sIn);
        grid.append(ls);
      }
      body.append(grid);
    } else if (wizardStep === 2) {
      body.append(make('strong', `Step 2 — Select Verified Products (${selectedSkus.length} selected)`));
      body.append(make('p', 'Leave unchecked to let Marketing Agent auto-pick top eligible ready products.', 'hint'));
      const prods = (typeof state !== 'undefined' && state?.products) ? state.products : [];
      const wrap = make('div', '', 'wizard-prod-grid');
      for (const p of prods) {
        const lbl = make('label', '', 'wizard-prod-item');
        const chk = document.createElement('input');
        chk.type = 'checkbox';
        chk.checked = selectedSkus.includes(p.sku);
        chk.onchange = () => {
          if (skuSel) {
            Array.from(skuSel.options).forEach(opt => {
              if (opt.value === p.sku) opt.selected = chk.checked;
            });
          }
          window.__preselectCampaignSkus = currentCampaignSelections();
          renderWizard();
        };
        const info = make('div', '');
        info.append(make('strong', p.name), make('div', `${p.sku} · ₹${p.price} · Margin ₹${p.margin}${p.issues.length ? ' (Needs attention)' : ''}`, 'hint'));
        lbl.append(chk, info);
        wrap.append(lbl);
      }
      if (!prods.length) wrap.append(make('p', 'No products loaded in active workspace.', 'hint'));
      body.append(wrap);
    } else if (wizardStep === 3) {
      body.append(make('strong', 'Step 3 — Select Marketing Language'));
      const sel = document.createElement('select');
      sel.style.marginTop = '10px';
      sel.add(new Option('Telugu (తెలుగు) — Primary Regional Reels', 'te'));
      sel.add(new Option('English — Broad Audience', 'en'));
      sel.add(new Option('Bilingual (Telugu + English)', 'bilingual'));
      sel.value = langEl?.value || 'te';
      sel.onchange = () => { if (langEl) langEl.value = sel.value; };
      body.append(document.createElement('br'), sel);
    } else if (wizardStep === 4) {
      body.append(make('strong', 'Step 4 — Review Truth & Brand Guardrails'));
      const ul = make('ul', '', 'checklist-list');
      ul.append(
        make('li', `Campaign type: ${typeEl?.value || 'single_product'} | Language: ${langEl?.value || 'te'}`),
        make('li', `Products chosen: ${selectedSkus.length ? selectedSkus.join(', ') : 'Auto-select eligible Catalogue/Sales picks'}`),
        make('li', 'Strict Fact Grounding: Prices, stock, and benefits come strictly from verified product data.'),
        make('li', 'No auto-publishing and zero ad spend permitted.')
      );
      body.append(ul);
    } else if (wizardStep === 5) {
      body.append(make('strong', 'Step 5 — Generate Marketing Script, Captions & Storyboard'));
      body.append(make('p', 'Calls local Ollama with JSON schema validation + safe fallback templates.', 'hint'));
      const runBtn = make('button', 'Run Marketing Agent Now', 'primary');
      runBtn.type = 'button';
      runBtn.style.marginTop = '10px';
      runBtn.onclick = () => document.getElementById('mkt-generate')?.click();
      body.append(runBtn);
    } else if (wizardStep === 6) {
      body.append(make('strong', 'Step 6 — Hand Off Storyboard to Video Studio'));
      body.append(make('p', 'Open Video Studio with your selected SKUs or create Reel Drafts directly from the latest generated campaign.', 'hint'));
      const row = make('div', '', 'agent-table-actions');
      row.style.marginTop = '10px';
      const toVid = make('button', 'Open in Video Studio →', 'primary');
      toVid.type = 'button';
      toVid.onclick = () => {
        const vidSel = document.getElementById('vid-skus');
        if (vidSel && selectedSkus.length) {
          Array.from(vidSel.options).forEach(o => { o.selected = selectedSkus.includes(o.value); });
        }
        window.StudioUI?.navigate('video');
      };
      row.append(toVid);
      body.append(row);
    }

    if (hint) hint.textContent = `Step ${wizardStep} of 6`;
    if (prevBtn) {
      prevBtn.disabled = wizardStep <= 1;
      prevBtn.onclick = () => { if (wizardStep > 1) { wizardStep--; renderWizard(); } };
    }
    if (nextBtn) {
      nextBtn.disabled = wizardStep >= 6;
      nextBtn.onclick = () => { if (wizardStep < 6) { wizardStep++; renderWizard(); } };
    }
  }

  function deriveCampaignState(campaign, agentState) {
    const prods = agentState?.video_productions || [];
    const social = agentState?.social_posts || [];
    const skus = new Set(campaign.selected_skus || []);
    const matchingProd = prods.find(v => (v.skus || []).some(s => skus.has(s)));
    const matchingPost = social.find(p => skus.has(p.sku));
    if (matchingPost && matchingPost.status === 'Published') return { label: 'Published', cls: 'badge success' };
    if (matchingPost && ['Approved', 'Scheduled'].includes(matchingPost.status)) return { label: matchingPost.status, cls: 'badge success' };
    if (matchingProd && matchingProd.qa_status === 'passed') return { label: 'Video & QA Ready', cls: 'badge info' };
    if (matchingProd) return { label: 'Video Rendered', cls: 'badge warning' };
    if ((campaign.linked_job_ids || []).length) return { label: 'Reel Drafts Created', cls: 'badge info' };
    return { label: 'Script Ready', cls: 'badge' };
  }

  function renderCampaignCards(agentState) {
    const host = document.getElementById('campaign-cards-list');
    if (!host) return;
    const campaigns = agentState?.marketing_campaigns || [];
    host.replaceChildren();
    if (!campaigns.length) return;
    for (const c of campaigns.slice(0, 6)) {
      const card = make('article', '', 'campaign-state-card');
      const top = make('div', '', 'meta');
      const st = deriveCampaignState(c, agentState);
      top.append(make('strong', `${c.campaign_type.replace('_', ' ')} (${c.language.toUpperCase()})`), make('span', st.label, st.cls));
      const skus = make('p', `SKUs: ${(c.selected_skus || []).join(', ') || 'None'} · ${(c.linked_job_ids || []).length} draft(s)`, 'hint');
      const act = make('div', '', 'agent-table-actions');
      const sendVid = make('button', 'Send to Video Studio');
      sendVid.type = 'button';
      sendVid.onclick = () => {
        const vidSel = document.getElementById('vid-skus');
        if (vidSel) {
          Array.from(vidSel.options).forEach(o => { o.selected = (c.selected_skus || []).includes(o.value); });
        }
        window.StudioUI?.navigate('video');
      };
      const dupBtn = make('button', 'Duplicate Campaign');
      dupBtn.type = 'button';
      dupBtn.onclick = () => {
        const typeEl = document.getElementById('mkt-campaign-type');
        const langEl = document.getElementById('mkt-language');
        const catEl = document.getElementById('mkt-category');
        const seaEl = document.getElementById('mkt-season');
        const skuSel = document.getElementById('mkt-skus');
        if (typeEl) { typeEl.value = c.campaign_type; typeEl.dispatchEvent(new Event('change')); }
        if (langEl) langEl.value = c.language;
        if (catEl) catEl.value = c.category || '';
        if (seaEl) seaEl.value = c.season_theme || '';
        const skusList = c.selected_skus || [];
        window.__preselectCampaignSkus = skusList;
        if (skuSel) Array.from(skuSel.options).forEach(o => { o.selected = skusList.includes(o.value); });
        const wiz = document.getElementById('campaign-wizard');
        if (wiz) wiz.hidden = false;
        wizardStep = 2;
        renderWizard();
        wiz?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        if (typeof notice === 'function') notice(`Duplicated campaign settings for ${(skusList || []).join(', ') || c.campaign_type} into Wizard.`);
      };
      act.append(sendVid, dupBtn);
      card.append(top, skus, act);
      host.append(card);
    }
  }

  function renderVideoStudio(agentState) {
    const prods = agentState?.video_productions || [];
    const sel = document.getElementById('vid-studio-select');
    const player = document.getElementById('vid-stage-player');
    const emptyEl = document.getElementById('vid-stage-empty');
    const badges = document.getElementById('vid-stage-badges');
    const mediaList = document.getElementById('vid-studio-media');
    const timeline = document.getElementById('vid-timeline');
    const layoutNote = document.getElementById('vid-layout-note');
    if (!sel || !player) return;

    sel.replaceChildren();
    if (!prods.length) {
      sel.add(new Option('No video productions yet', ''));
      player.hidden = true;
      if (emptyEl) emptyEl.hidden = false;
      if (badges) badges.replaceChildren();
      if (timeline) {
        timeline.replaceChildren(make('p', 'Timeline tracks will appear after generating a video production.', 'hint'));
      }
      if (mediaList) {
        const curProducts = (typeof state !== 'undefined' && state?.products) ? state.products.slice(0, 6) : [];
        mediaList.replaceChildren();
        for (const p of curProducts) {
          const item = make('div', '', 'studio-media-item');
          if (p.has_photo) {
            const img = new Image(); img.alt = p.name;
            img.src = '/photo?' + new URLSearchParams({ mode: typeof mode === 'function' ? mode() : 'store', sku: p.sku });
            item.append(img);
          }
          const txt = make('div', '');
          txt.append(make('strong', p.name), make('div', `${p.sku} · ${p.photo_count} photo(s)`, 'hint'));
          item.append(txt);
          mediaList.append(item);
        }
      }
      return;
    }

    if (!selectedProductionId || !prods.some(p => p.id === selectedProductionId)) {
      selectedProductionId = prods[0].id;
    }
    for (const p of prods) {
      sel.add(new Option(`${p.id} · ${p.duration_seconds}s · ${(p.skus || []).join(', ')} (${p.qa_status})`, p.id));
    }
    sel.value = selectedProductionId;
    sel.onchange = () => {
      selectedProductionId = sel.value;
      renderVideoStudio(window.__latestAgentState);
    };

    const current = prods.find(p => p.id === selectedProductionId) || prods[0];
    const meta = current.metadata || {};
    player.hidden = false;
    if (emptyEl) emptyEl.hidden = true;
    const srcUrl = (meta.video_url || `/video/${current.id}.mp4`) + '?t=' + encodeURIComponent(current.updated_at || '');
    if (player.getAttribute('data-loaded-src') !== srcUrl) {
      player.src = srcUrl;
      player.setAttribute('data-loaded-src', srcUrl);
    }

    if (badges) {
      badges.replaceChildren();
      for (const txt of [
        `${meta.width || 1080}×${meta.height || 1920}`,
        `${current.duration_seconds}s · ${current.language.toUpperCase()}`,
        `QA: ${current.qa_status}`,
        `Review: ${current.human_review_status}`,
      ]) {
        badges.append(make('span', txt, 'catalog-pill'));
      }
    }

    if (layoutNote) {
      layoutNote.textContent = `Layouts: ${(meta.category_layouts || []).join(', ') || 'standard'} · Voiceover: ${meta.voiceover_status || 'auto'} · Ducking: ${meta.ducking_applied ? 'enabled' : 'off'}`;
    }

    const scenes = meta.scenes || [];
    if (mediaList) {
      mediaList.replaceChildren();
      for (const sku of current.skus || []) {
        const item = make('div', '', 'studio-media-item');
        const img = new Image(); img.alt = sku;
        img.src = '/photo?' + new URLSearchParams({ mode: typeof mode === 'function' ? mode() : 'store', sku });
        img.onerror = () => { img.hidden = true; };
        item.append(img, make('div', `Product ${sku}`));
        mediaList.append(item);
      }
      scenes.forEach((sc, idx) => {
        const row = make('div', '', 'studio-media-item');
        const txt = make('div', '');
        txt.append(make('strong', `Scene ${idx + 1} (${sc.duration_seconds}s)`), make('div', sc.caption_text || sc.heading_text || sc.hook_text || 'Product showcase', 'hint'));
        row.append(txt);
        mediaList.append(row);
      });
    }

    if (timeline) {
      timeline.replaceChildren();
      timeline.append(make('strong', `Synchronized Production Timeline (${current.duration_seconds}s)`));
      const videoTrack = make('div', '', 'timeline-track');
      videoTrack.append(make('span', 'Scenes', 'timeline-track-lbl'));
      const vLane = make('div', '', 'timeline-lane');
      const totalDur = scenes.reduce((acc, s) => acc + Number(s.duration_seconds || 1), 0) || current.duration_seconds || 15;
      if (scenes.length) {
        scenes.forEach((sc, i) => {
          const blk = make('div', `S${i + 1}: ${sc.duration_seconds}s`, 'timeline-block');
          blk.style.flex = String(sc.duration_seconds || 1);
          blk.title = sc.heading_text || sc.caption_text || '';
          vLane.append(blk);
        });
      } else {
        const blk = make('div', `Full Video (${totalDur}s)`, 'timeline-block');
        blk.style.flex = '1';
        vLane.append(blk);
      }
      videoTrack.append(vLane);

      const capTrack = make('div', '', 'timeline-track');
      capTrack.append(make('span', 'Captions', 'timeline-track-lbl'));
      const cLane = make('div', '', 'timeline-lane');
      if (scenes.length) {
        scenes.forEach(sc => {
          const blk = make('div', sc.caption_text || sc.hook_text || 'Overlay', 'timeline-block');
          blk.style.flex = String(sc.duration_seconds || 1);
          cLane.append(blk);
        });
      } else {
        const blk = make('div', 'Synchronized On-Screen Text', 'timeline-block');
        blk.style.flex = '1';
        cLane.append(blk);
      }
      capTrack.append(cLane);

      const voTrack = make('div', '', 'timeline-track');
      voTrack.append(make('span', 'Voiceover', 'timeline-track-lbl'));
      const voLane = make('div', '', 'timeline-lane');
      const voBlk = make('div', `Narration (${meta.voiceover_status || 'auto'} · ${current.language.toUpperCase()})`, 'timeline-block audio');
      voBlk.style.flex = '1';
      voLane.append(voBlk);
      voTrack.append(voLane);

      const musTrack = make('div', '', 'timeline-track');
      musTrack.append(make('span', 'Music', 'timeline-track-lbl'));
      const mLane = make('div', '', 'timeline-lane');
      const mBlk = make('div', `Rights-Cleared BGM (${meta.ducking_applied ? 'Ducked under narration' : 'Full bed'})`, 'timeline-block music');
      mBlk.style.flex = '1';
      mLane.append(mBlk);
      musTrack.append(mLane);

      timeline.append(videoTrack, capTrack, voTrack, musTrack);
    }
  }

  function refreshPhase4Studio(route) {
    renderWizard();
    if (window.__latestAgentState) {
      renderCampaignCards(window.__latestAgentState);
      renderVideoStudio(window.__latestAgentState);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const wiz = document.getElementById('campaign-wizard');
    document.getElementById('camp-toggle-wizard')?.addEventListener('click', () => {
      if (wiz) wiz.hidden = !wiz.hidden;
    });
    renderWizard();
  });

  window.refreshPhase4Studio = refreshPhase4Studio;
})();
