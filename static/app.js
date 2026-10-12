(() => {
  'use strict';

  // ---------- helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (n) => '$' + Number(n).toFixed(2);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const safeStore = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(method, path, body) {
    const opts = { method, headers: { 'X-App': '1' }, credentials: 'same-origin' };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let res;
    try { res = await fetch(path, opts); } catch { throw new ApiError("You're offline. Reconnect and try again.", 0); }
    let data = {};
    try { data = await res.json(); } catch { /* empty body */ }
    if (res.status === 401 && state.user && !path.startsWith('/api/auth/')) {
      state.user = null;
      render();
    }
    if (!res.ok) throw new ApiError(data.error || 'Something went wrong. Try again.', res.status);
    return data;
  }

  function ageText(days) {
    if (days == null) return '';
    if (days < 0.05) return 'just now';
    if (days < 1) return 'today';
    if (days < 2) return 'yesterday';
    if (days < 14) return `${Math.round(days)} days ago`;
    return 'over 2 weeks ago';
  }

  let toastTimer;
  function toast(message, undo) {
    const el = $('#toast');
    el.textContent = '';
    const span = document.createElement('span');
    span.textContent = message;
    el.append(span);
    state.toastUndo = undo || null;
    if (undo) {
      const b = document.createElement('button');
      b.className = 'toast-btn';
      b.dataset.action = 'toast-undo';
      b.textContent = 'Undo';
      el.append(b);
    }
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; state.toastUndo = null; }, undo ? 9000 : 4000);
  }

  // ---------- state ----------
  const state = {
    user: null, ready: false, tab: 'list', authMode: 'signup', authError: '',
    basket: null, plan: 1, compare: null, reminders: null, deals: null, purchases: null,
    ltab: 'items', rsub: 'points', brands: {}, toastUndo: null, programsSaving: false,
    feed: null, nearStores: null, report: { storeId: safeStore.get('lastStore') || '', product: null, error: '' },
    editingPrice: null, printTag: true, authDraft: {},
    rewards: null, alerts: null, installEvent: null,
  };

  const TABS = [
    ['list', 'List', 'My list'], ['deals', 'Deals', 'Deals'], ['rewards', 'Rewards', 'Rewards'], ['me', 'Me', 'Me'],
  ];
  const STRATEGIES = [
    ['lowest', 'Lowest cost', 'Groceries plus estimated driving, whatever the number of stops.'],
    ['fewest', 'Fewest stores', 'One store whenever possible.'],
    ['value', 'Best value', 'Adds a second or third store only if it saves at least your minimum, after driving.'],
  ];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const fmtDate = (iso) => { if (!iso) return ''; const [, m, d] = iso.slice(0, 10).split('-').map(Number); return `${MONTHS[m - 1]} ${d}`; };
  const inDays = (n) => (n < -1 ? `${-n} days ago` : n === -1 ? 'yesterday' : n === 0 ? 'today' : n === 1 ? 'tomorrow' : `in ${n} days`);

  // ---------- small view pieces ----------
  function icon(name) {
    const p = {
      list: '<path d="M8 6h12M8 12h12M8 18h12"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
      deals: '<path d="M3 12.5V4h8.5L21 13.5 13.5 21 3 12.5Z"/><circle cx="7.5" cy="8.5" r="1.2"/>',
      report: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
      rewards: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3Z"/>',
      me: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    }[name];
    return `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  }

  function tag({ label, store, price, meta = [], print = false }) {
    const [d, c] = Number(price).toFixed(2).split('.');
    return `<div class="tag${print ? ' print' : ''}">
      ${label ? `<div class="tag-label">${esc(label)}</div>` : ''}
      <div class="tag-store">${esc(store)}</div>
      <div class="tag-price" aria-hidden="true"><span class="cur">$</span><span class="dol">${d}</span><span class="cents">${c}</span></div>
      <span class="sr">${money(price)}</span>
      <div class="tag-meta">${meta.filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join('')}</div>
    </div>`;
  }

  function mini(price, big = false) {
    const [d, c] = Number(price).toFixed(2).split('.');
    return `<span class="mini${big ? ' big' : ''}"><span class="sr">${money(price)}</span><span aria-hidden="true" class="cur">$</span><span aria-hidden="true">${d}</span><span aria-hidden="true" class="cents">${c}</span></span>`;
  }

  const brandOf = (k) => state.brands[k] || { key: k, name: k };
  // Official logo file if one has been added to static/brands/, otherwise the chain's name in its brand color.
  function chainBadge(k, cls = '') {
    const b = brandOf(k);
    return b.logo ? `<img class="brand-logo ${cls}" src="${esc(b.logo)}" alt="${esc(b.name)}">`
      : `<span class="brand b-${esc(k)} ${cls}">${esc(b.name)}</span>`;
  }
  const QTEXT = { verified: 'Verified', reported: 'Unconfirmed', sample: 'Sample' };
  function qualityChip(v) {
    const parts = [`<span class="qchip q-${v.quality}">${QTEXT[v.quality]}</span>`];
    if (v.stale) parts.push('<span class="qchip q-stale">Old price</span>');
    return parts.join('');
  }
  const sampleChip = (on) => (on ? '<span class="sample">sample</span>' : '');
  function who(a) {
    if (!a) return '';
    return `<span class="${a.gold ? 'gold' : ''}">${esc(a.name)}</span>${a.badge ? `<span class="badge">${esc(a.badge)}</span>` : ''}`;
  }
  const stars = (n) => `<span class="stars" aria-hidden="true">${'★'.repeat(Math.round(n))}${'☆'.repeat(5 - Math.round(n))}</span><span class="sr">${n} out of 5 stars</span>`;
  const header = (title, extra = '') => `<header class="top"><h1>${esc(title)}</h1>${extra}</header>`;
  const loading = (text = 'Loading…') => `<p class="lead" role="status">${esc(text)}</p>`;
  const errBox = (msg) => (msg ? `<p class="err" role="alert">${esc(msg)}</p>` : '');
  const locationChip = () => `<button class="chip" data-action="open-location">${state.user.radius_miles} miles from ${esc(state.user.zip)}</button>`;

  function spark(points) {
    const w = 300, h = 64, pad = 5;
    const vals = points.map((p) => p.price);
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const step = (w - 2 * pad) / Math.max(1, points.length - 1);
    const xy = points.map((p, i) => [pad + i * step, h - pad - ((p.price - lo) / span) * (h - 2 * pad)]);
    const d = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
    const [lx, ly] = xy[xy.length - 1];
    return `<svg viewBox="0 0 ${w} ${h}" class="spark" role="img" aria-label="Lowest price near you over time, from ${money(lo)} to ${money(hi)}">
      <path d="${d}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="5" fill="var(--tag)" stroke="currentColor" stroke-width="2"/></svg>`;
  }

  // ---------- sign in / sign up ----------
  function authView() {
    const up = state.authMode === 'signup';
    return `<div class="auth">
      <h1>Anil's Grocery</h1>
      ${tag({ label: 'Example', store: 'Cheapest eggs near you', price: 2.89, meta: ['Updated today'], print: state.printTag })}
      <p class="lead">Build a grocery list and see which store near you charges the least. Report prices you see and earn rewards.</p>
      <form data-form="auth" novalidate>
        ${errBox(state.authError)}
        ${up ? `<label class="field"><span>Display name</span><input name="name" autocomplete="nickname" maxlength="40" value="${esc(state.authDraft.name)}" required><small>Shown on your reviews and price reports.</small></label>` : ''}
        <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" inputmode="email" value="${esc(state.authDraft.email)}" required></label>
        <label class="field"><span>Password</span><input name="password" type="password" autocomplete="${up ? 'new-password' : 'current-password'}" required>${up ? '<small>At least 8 characters.</small>' : ''}</label>
        ${up ? `<label class="field"><span>ZIP code</span><input name="zip" inputmode="numeric" autocomplete="postal-code" maxlength="5" pattern="[0-9]{5}" value="${esc(state.authDraft.zip)}" required><small>We use it to find stores near you.</small></label>` : ''}
        <div class="pad"><button class="btn wide" type="submit">${up ? 'Create account' : 'Sign in'}</button></div>
      </form>
      <div class="pad"><button class="link" data-action="switch-auth">${up ? 'I already have an account' : 'Create an account'}</button></div>
    </div>`;
  }

  // ---------- list ----------
  function remRow(t) {
    const e = t.estimate;
    const when = e.days_left < 0 ? `Estimated to have run out ${inDays(e.days_left)}`
      : e.days_left === 0 ? 'Estimated to run out today' : `Estimated to run out ${inDays(e.days_left)}`;
    const d = `data-action="rem" data-pid="${t.product_id}"`;
    return `<div class="rem"><div class="name">${esc(t.name)}</div>
      <div class="sub">${when}. This is an estimate from how often you buy it (about every ${e.interval_days} days).</div>
      <div class="actions"><button class="btn small" ${d} data-do="add">Add to list</button>
        <button class="btn small quiet" ${d} data-do="snooze">Snooze 3 days</button>
        <button class="btn small quiet" ${d} data-do="complete">Already restocked</button>
        <button class="link" ${d} data-do="dismiss">Dismiss</button></div></div>`;
  }

  function remindersCard() {
    const r = state.reminders;
    if (!r || !r.enabled || !r.due.length) return '';
    return `<section class="remind" aria-labelledby="rem-h"><h2 id="rem-h">Time to restock?</h2>
      ${r.due.slice(0, 3).map(remRow).join('')}
      ${r.due.length > 3 ? `<p class="sub pad">${r.due.length - 3} more under Me.</p>` : ''}</section>`;
  }

  function listView() {
    const b = state.basket, c = state.compare;
    const favs = state.reminders ? state.reminders.favorites.filter((f) => !(b && b.items.some((i) => i.product_id === f.product_id))) : [];
    let body;
    if (!b) body = loading('Loading your list…');
    else if (!b.items.length) {
      body = `<div class="empty"><h2>Your list is empty</h2><p>Add what you need this week. We'll compare Winn-Dixie, Publix and Walmart near ${esc(state.user.zip)}.</p></div>`;
    } else if (state.ltab === 'compare') {
      body = compareView();
    } else {
      body = `<ul class="rows">${b.items.map((it) => listRow(it, c)).join('')}</ul>${c ? listFootnote(c) : ''}`;
    }
    const seg = b && b.items.length ? `<div class="seg tabs" role="group" aria-label="List view">
      <button aria-pressed="${state.ltab === 'items'}" data-action="ltab" data-t="items"><b>My items</b></button>
      <button aria-pressed="${state.ltab === 'compare'}" data-action="ltab" data-t="compare"><b>Compare stores</b></button></div>` : '';
    return `<p class="hello">Hi, ${esc(state.user.name.split(' ')[0])}</p>` + header('My list', locationChip()) + remindersCard() + `
      <form class="add" data-form="add-item" autocomplete="off">
        <label class="sr" for="add-input">Add an item</label>
        <div class="add-wrap"><input id="add-input" name="q" placeholder="Add an item, like eggs or milk" enterkeyhint="done" data-typeahead="add"><button class="btn" type="submit">Add</button></div>
        <ul id="sugg-add" class="sugg" hidden></ul>
      </form>` + `<div class="favs"><button class="chip" data-action="receipt">Scan a receipt</button>${favs.map((f) => `<button class="chip" data-action="fav-add" data-pid="${f.product_id}">+ ${esc(f.name)}</button>`).join('')}</div>` + seg + body;
  }

  function listFootnote(c) {
    const bits = [];
    if (c.flags.sample) bits.push('Prices marked Sample are demonstration data, not real store prices.');
    if (c.unpriced.length) bits.push(`No price data yet for ${c.unpriced.map((u) => u.name).join(', ')}.`);
    return bits.length ? `<p class="note">${esc(bits.join(' '))}</p>` : '';
  }

  function productMeta(it) {
    return [it.brand, it.package_size].filter(Boolean).join(', ');
  }

  function listRow(it, c) {
    const ci = c && c.items.find((i) => i.item_id === it.item_id);
    const best = ci && ci.best && ci.prices[ci.best];
    let right = '', sub;
    if (it.checked) sub = 'In your cart';
    else if (!c) sub = 'Checking prices…';
    else if (best) {
      right = `<span class="price-col">${mini(best.effective)}${chainBadge(ci.best, 'sm')}</span>`;
      const n = Object.values(ci.prices).filter(Boolean).length;
      sub = n > 1 ? `Lowest of ${n} stores${ci.save_vs_next > 0 ? `, saves ${money(ci.save_vs_next)}` : ''}` : `Only ${brandOf(ci.best).name} has data`;
      if (best.quality !== 'verified') sub += best.quality === 'sample' ? ' (sample)' : ' (unconfirmed)';
    } else if (ci && ci.reason && Object.values(ci.prices).some(Boolean)) {
      const k = Object.keys(ci.prices).find((x) => ci.prices[x]);
      right = `<span class="price-col">${mini(ci.prices[k].effective)}${chainBadge(k, 'sm')}</span>`;
      sub = ci.reason;
    } else {
      right = '<span class="mini none">No price data yet</span>';
      sub = 'Tap to report a price you saw.';
    }
    const meta = productMeta(it);
    return `<li class="row ${it.checked ? 'done' : ''}">
      <button class="check" role="checkbox" aria-checked="${it.checked ? 'true' : 'false'}" aria-label="In cart: ${esc(it.name)}" data-action="toggle" data-id="${it.item_id}" data-checked="${it.checked ? 0 : 1}"></button>
      <button class="row-main" data-action="open-item" data-pid="${it.product_id}" data-name="${esc(it.name)}">
        <span class="name">${esc(it.name)}${it.qty > 1 ? ` <span class="qty">× ${it.qty}</span>` : ''}</span>
        ${meta ? `<span class="sub">${esc(meta)}</span>` : ''}
        <span class="sub">${esc(sub)}</span>
      </button>${right}
      <button class="icon-btn" data-action="remove" data-id="${it.item_id}" aria-label="Remove ${esc(it.name)} from list"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></li>`;
  }

  // ---------- compare ----------
  function priceCell(k, v, best) {
    if (!v) return `<div class="cell none">${chainBadge(k, 'sm')}<span class="na">No price data</span></div>`;
    return `<div class="cell${best ? ' best' : ''}">${chainBadge(k, 'sm')}
      ${mini(v.effective)}
      ${v.offer ? `<span class="was">Shelf price ${money(v.price)}</span><span class="offer">${esc(v.offer.title)}</span>` : ''}
      ${best ? '<span class="lowest">Lowest</span>' : ''}
      <span class="qline">${qualityChip(v)}</span>
      ${v.locked_offer ? `<span class="locked">Deal needs your ${esc(brandOf(k).name)} account</span>` : ''}</div>`;
  }

  function itemCompare(it) {
    const meta = productMeta(it);
    const cells = Object.keys(it.prices).map((k) => priceCell(k, it.prices[k], it.best === k)).join('');
    const sav = it.best && it.save_vs_highest > 0
      ? `<p class="saves">Saves ${money(it.save_vs_next)} vs the next best store, up to ${money(it.save_vs_highest)} vs the highest${it.qty > 1 ? ` (for ${it.qty})` : ''}.</p>` : '';
    return `<article class="icard"><div class="icard-head"><button class="row-main" data-action="open-item" data-pid="${it.product_id}" data-name="${esc(it.name)}">
        <span class="name">${esc(it.name)}${it.qty > 1 ? ` <span class="qty">× ${it.qty}</span>` : ''}</span>
        <span class="sub">${esc(meta || 'Brand and size not set. Prices are for the item as named.')}</span></button></div>
      <div class="cells">${cells}</div>${sav}
      <p class="why">${esc(it.reason)}</p></article>`;
  }

  function planBlock(p) {
    return p.stores.map((s) => `<section class="plan-store"><h3>${chainBadge(s.chain, 'sm')} <span class="sub">${esc(s.store)}, ${s.distance} mi</span></h3>
      <ul class="lines">${s.items.map((i) => `<li><span>${esc(i.name)}${i.qty > 1 ? ` × ${i.qty}` : ''}${i.offer ? `<br><span class="offer">${esc(i.offer.title)} saves ${money(i.offer.saves)}</span>` : ''}</span><b>${money(i.line_total)}</b></li>`).join('')}
        <li class="sub-total"><span>Store total</span><b>${money(s.subtotal)}</b></li></ul></section>`).join('');
  }

  function compareView() {
    const c = state.compare;
    if (!c) return loading('Comparing stores…');
    const active = c.items.filter((i) => !i.checked);
    if (!active.length) return '<div class="empty"><h2>Nothing to compare</h2><p>Everything on your list is already in your cart.</p></div>';
    const strat = state.strategy || c.chosen;
    const seg = `<div class="seg tabs" role="group" aria-label="Shopping strategy">${STRATEGIES.map(([k, label]) => `<button aria-pressed="${k === strat}" data-action="strategy" data-s="${k}"><b>${label}</b></button>`).join('')}</div>
      <p class="sub pad hint">${esc(STRATEGIES.find((x) => x[0] === strat)[2])}</p>`;
    const rec = c.recs[strat];
    let hero;
    if (!rec) {
      hero = `<div class="empty"><h2>No store has prices for this list yet</h2><p>We only show prices we actually have, so there is nothing to total. Report prices you see in stores to fill this in.</p><button class="btn" data-action="tab" data-tab="report">Report a price</button></div>`;
    } else {
      const single = rec.stops === 1 ? c.singles.find((s) => s.chain === rec.chains[0]) : null;
      const names = rec.chains.map((k) => brandOf(k).name).join(' + ');
      const heroMeta = [
        rec.stops === 1 ? `${rec.stores[0].distance} miles away` : `${rec.stops} stops`,
        rec.offer_savings > 0 ? `includes ${money(rec.offer_savings)} in verified deals` : '',
        single && single.vs_priciest > 0 ? `${money(single.vs_priciest)} less than the priciest store` : '',
        rec.missing ? `missing ${plural(rec.missing, 'item', 'items')}` : '',
      ];
      hero = `<div class="reco"><div class="badges" aria-hidden="true">${rec.chains.map((k) => chainBadge(k)).join('<span class="plus">+</span>')}</div>` +
        tag({ label: strat === 'fewest' ? 'Fewest stores' : strat === 'lowest' ? 'Lowest cost' : 'Best value', store: names, price: rec.total, meta: heroMeta, print: state.printTag }) + '</div>';
      hero += `<p class="plan-head">${rec.stops === 1 ? 'Shop it all at one store.' : 'Split your list across these stores.'}</p>` + planBlock(rec);
      if (c.cost_per_mile) hero += `<p class="sub pad">Estimated driving: ${money(rec.travel_cost)} (round trips at ${money(c.cost_per_mile)} per mile, straight-line distance). Not included in the total above.</p>`;
      if (c.multi_verdict) hero += `<p class="verdict ${c.multi_verdict.worth ? 'yes' : ''}">${esc(c.multi_verdict.text)}</p>`;
      if (c.budget) hero += `<p class="verdict ${c.budget.over > 0 ? '' : 'yes'}">${c.budget.over > 0 ? `About ${money(c.budget.over)} over your ${money(c.budget.budget)} budget.` : `Within your ${money(c.budget.budget)} budget, with about ${money(-c.budget.over)} to spare.`}</p>`;
    }
    const totals = c.singles.length ? `<h2>If you shop at just one store</h2><ul class="rows">${c.singles.map((s) => `<li class="row"><div class="grow">${chainBadge(s.chain, 'sm')}<div class="sub">${esc(s.store)}, ${s.distance} mi${s.missing ? ` <span class="miss">missing ${s.missing}</span>` : ''}</div>
        ${s.offer_savings > 0 ? `<div class="sub good">Includes ${money(s.offer_savings)} in deals</div>` : ''}${s.vs_priciest > 0 ? `<div class="sub good">${money(s.vs_priciest)} less than the priciest option</div>` : ''}</div><span class="total">${money(s.total)}</span></li>`).join('')}</ul>` : '';
    const notes = c.notes.length ? `<ul class="notes">${c.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : '';
    const other = state.basket && state.basket.stores.length ? `<details class="other"><summary>All stores within ${state.basket.radius} miles, any chain</summary>${state.basket.stores.map(storeDetails).join('')}</details>` : '';
    return seg + hero + totals + notes + `<h2>Item by item</h2>` + active.map(itemCompare).join('') + other;
  }

  function storeDetails(s) {
    return `<details class="store"><summary><span class="grow"><span class="name">${esc(s.store)}</span>${sampleChip(s.sample)}<br><span class="sub">${s.distance} miles</span>${s.missing ? ` <span class="miss">missing ${s.missing}</span>` : ''}</span><span class="total">${money(s.total)}</span></summary>
      <ul class="lines">${s.items.map((i) => `<li><span>${esc(i.name)}${i.qty > 1 ? ` × ${i.qty}` : ''}</span><b>${i.price == null ? '<span class="miss">no price</span>' : money(i.price * i.qty)}</b></li>`).join('')}</ul></details>`;
  }

  // ---------- deals ----------
  function offerCard(o) {
    const kind = { sale: 'Weekly sale', coupon: 'Coupon', loyalty: 'Loyalty offer' }[o.kind] || o.kind;
    const price = o.sale_price != null ? `Sale price ${money(o.sale_price)}` : o.amount_off != null ? `${money(o.amount_off)} off` : '';
    let mine;
    if (!o.product_id) mine = '<span class="sub">General offer. We can\'t estimate what it saves on your list.</span>';
    else if (!o.on_list) mine = '<span class="sub">Not on your list.</span>';
    else if (o.saves != null) mine = `<span class="good">Saves about ${money(o.saves)} on your list</span>`;
    else mine = '<span class="sub">On your list, but we have no real price at this store to compare, so no savings estimate.</span>';
    return `<li class="offer-card"><div class="grow"><div class="name">${esc(o.title)}</div>
      <div class="sub">${esc(kind)}${o.product ? ` · ${esc(o.product)}` : ''}${price ? ` · ${price}` : ''}</div>
      <div class="sub">${o.expires_on ? `Ends ${fmtDate(o.expires_on)}` : 'No end date listed'}${o.starts_on ? `, starts ${fmtDate(o.starts_on)}` : ''}</div>
      ${o.min_spend ? `<div class="sub">Needs ${money(o.min_spend)} minimum spend.</div>` : ''}${o.max_qty ? `<div class="sub">Limit ${o.max_qty}.</div>` : ''}
      ${o.conditions ? `<div class="sub">${esc(o.conditions)}</div>` : ''}
      ${o.requires_account ? `<div class="${o.account_ok ? 'sub' : 'miss'}">${o.account_ok ? `Uses your ${esc(o.chain_name)} account.` : `Needs a ${esc(o.chain_name)} account. Mark yours under Rewards to count it.`}</div>` : ''}
      <div>${mine}</div><div class="sub src">Source: ${esc(o.source)}</div></div></li>`;
  }

  function providerRows(list) {
    return `<ul class="rows">${list.map((p) => `<li class="row top"><div class="grow"><div class="name">${p.chain ? chainBadge(p.chain, 'sm') + ' ' : ''}${esc(p.label)}</div><div class="sub">${esc(p.detail)}</div></div>
      <span class="${p.connected ? 'good' : 'miss'}">${p.connected ? 'Connected' : 'Not connected'}</span></li>`).join('')}</ul>`;
  }

  function dealsView() {
    const head = header('Deals', locationChip());
    const d = state.deals;
    if (!d) return head + loading('Loading offers…');
    const sums = Object.entries(d.list_savings);
    const summary = sums.length
      ? `<div class="note good-note"><strong>On your list:</strong> ${sums.map(([k, v]) => `${esc(brandOf(k).name)} deals save about ${money(v)}`).join('; ')}. Only offers you can use today are counted, once per item.</div>`
      : d.offers.length && d.list_size ? '<p class="note">None of the current offers match items on your list that we have real prices for.</p>' : '';
    let body;
    if (!d.offers.length) {
      body = `<div class="empty"><h2>No weekly ads or coupons loaded yet</h2>
        <p>Winn-Dixie, Publix and Walmart don't offer a public feed of their weekly ads, so Anil's Grocery doesn't show deals it can't verify. When a feed is connected, sales and coupons will appear here with their expiry dates and conditions, and anything that matches your list will be counted in your savings.</p></div>`;
    } else {
      body = ['winndixie', 'publix', 'walmart'].map((k) => {
        const list = d.offers.filter((o) => o.chain === k);
        return list.length ? `<h2>${chainBadge(k)}</h2><ul class="rows">${list.map(offerCard).join('')}</ul>` : '';
      }).join('');
    }
    return head + summary + body + `<h2>Where this data comes from</h2>` + providerRows(d.providers);
  }

  // ---------- report ----------
  function reportView() {
    const r = state.report;
    const stores = state.nearStores;
    const opts = (stores || []).map((s) => `<option value="${s.id}" ${String(s.id) === String(r.storeId) ? 'selected' : ''}>${esc(s.name)} (${s.distance} mi)</option>`).join('');
    const form = `
      <div class="pad"><button class="btn wide quiet" data-action="receipt">Scan a receipt: many prices at once</button></div>
      <p class="lead tight">Or add one price you saw. You earn 5 points per price.</p>
      <form data-form="report" autocomplete="off" novalidate>
        ${errBox(r.error)}
        <label class="field"><span>Store</span>
          <select name="store" required><option value="" ${r.storeId ? '' : 'selected'} disabled>Choose a store</option>${opts}<option value="__new">My store isn't listed</option></select></label>
        <div class="field rel"><span><label for="rep-item">Item</label></span>
          <input id="rep-item" name="item" placeholder="Search, like eggs" value="${esc(r.product ? r.product.name : '')}" data-typeahead="report" required>
          <ul id="sugg-report" class="sugg" hidden></ul></div>
        <label class="field"><span>Price</span><span class="money"><b>$</b><input name="price" inputmode="decimal" placeholder="0.00" required></span></label>
        <div class="pad"><button class="btn wide" type="submit">Submit price</button></div>
      </form>`;
    let feed;
    if (!state.feed) feed = loading('Loading prices to confirm…');
    else if (!state.feed.length) feed = '<p class="lead">Nothing to confirm nearby right now. Check back later.</p>';
    else feed = `<ul class="rows">${state.feed.map(feedRow).join('')}</ul>`;
    return form + `<h2>Help confirm prices</h2><p class="lead">Is the price still right? Confirming earns 1 point, and the shopper who reported it earns 2.</p>` + feed;
  }

  function feedRow(f) {
    const editing = state.editingPrice === f.price_id;
    return `<li class="row top"><div class="grow">
      <div class="name">${esc(f.product)}</div>
      <div class="sub">${money(f.price)} at ${esc(f.store)}${sampleChip(f.sample)}, ${f.distance} mi</div>
      <div class="sub">${f.author ? `Reported ${ageText(f.age_days)} by ${who(f.author)}` : `Seen ${ageText(f.age_days)}`}${f.status === 'pending' ? ' <span class="miss">Unusual price</span>' : ''}</div>
      ${editing ? `<form data-form="correct" data-price="${f.price_id}" data-store="${f.store_id}" data-product="${f.product_id}" class="actions" novalidate>
          <span class="money grow"><b>$</b><input name="price" inputmode="decimal" placeholder="Current price" aria-label="Current price for ${esc(f.product)}" required></span>
          <button class="btn small" type="submit">Send price</button><button class="btn small quiet" type="button" data-action="cancel-edit">Cancel</button></form>`
        : `<div class="actions"><button class="btn small" data-action="confirm" data-id="${f.price_id}">Still right</button><button class="btn small quiet" data-action="edit-price" data-id="${f.price_id}">Price changed</button></div>`}
    </div></li>`;
  }

  // ---------- rewards ----------
  function rewardsView() {
    const d = state.rewards;
    if (!d) return loading();
    const u = d.user, lv = u.level;
    const progress = lv.next_at
      ? `<progress max="${lv.next_at - lv.floor}" value="${u.earned - lv.floor}" aria-label="Progress to ${esc(lv.next_name)}"></progress><p class="sub pad">${lv.next_at - u.earned} more points to ${esc(lv.next_name)}</p>`
      : '<p class="sub pad">You reached the top level.</p>';
    const perks = d.catalog.map((r) => `<li class="row"><div class="grow"><div class="name">${esc(r.name)}</div><div class="sub">${esc(r.description)}</div></div>
      ${r.owned ? '<span class="good">Unlocked</span>' : `<span class="cost">${r.cost}</span><button class="btn small" data-action="redeem" data-key="${r.key}" ${u.points < r.cost ? 'disabled' : ''}>Unlock</button>`}</li>`).join('');
    const rules = d.rules.map((r) => `<li class="row"><span class="cost">+${r.points}</span><span class="grow">${esc(r.text)}</span></li>`).join('');
    const ledger = d.ledger.length
      ? d.ledger.map((l) => `<li class="row"><span class="grow">${esc(l.text)}<br><span class="sub">${esc(l.created_at.slice(0, 10))}</span></span><span class="cost ${l.delta < 0 ? 'sale' : 'good'}">${l.delta > 0 ? '+' : ''}${l.delta}</span></li>`).join('')
      : '<li class="row sub">No activity yet. Report your first price to start earning.</li>';
    const board = d.leaderboard.length
      ? d.leaderboard.map((b, i) => `<li class="row"><span class="cost">${i + 1}</span><span class="grow">${who(b.author)}${b.you ? ' <span class="sub">(you)</span>' : ''}</span><span class="cost">${b.points}</span></li>`).join('')
      : '<li class="row sub">No one has earned points this month yet.</li>';
    return `
      <div class="balance"><b>${u.points}</b><span class="sub">points to spend</span></div>
      <p class="pad"><strong>${esc(lv.name)}</strong> <span class="sub">level, ${u.earned} points earned in total</span></p>${progress}
      <h2>Perks</h2><ul class="rows">${perks}</ul>
      <h2>How to earn points</h2><ul class="rows">${rules}</ul>
      <p class="lead tight">Prices that look far off the usual need two confirmations before they count and before they earn points.</p>
      <h2>Top price scouts this month</h2><ul class="rows">${board}</ul>
      <h2>Your activity</h2><ul class="rows">${ledger}</ul>`;
  }

  // ---------- rewards section (points, reporting, store programs) ----------
  const PROGRAM_INFO = {
    winndixie: 'Store loyalty account',
    publix: 'Store account for digital coupons',
    walmart: 'Store account',
  };

  function programsView() {
    const mine = state.user.prefs.programs;
    return `<p class="lead">Store accounts can unlock member-only offers. Tell us which ones you have, and offers that need an account will count toward your savings once a verified offer feed exists.</p>
      <ul class="rows">${['winndixie', 'publix', 'walmart'].map((k) => {
        const name = brandOf(k).name;
        return `<li class="row top"><div class="grow">${chainBadge(k)}
          <div class="sub gap">${esc(PROGRAM_INFO[k])}. Anil's Grocery can't connect to your ${esc(name)} account yet, so it can't see your points or apply coupons for you.</div>
          <label class="check-line"><input type="checkbox" data-program="${k}" ${mine.includes(k) ? 'checked' : ''}> I have a ${esc(name)} account</label>
          <div class="sub">What a connection would need: the sign-in details for that account. We would never ask for your password in this app, and nothing is collected today.</div></div></li>`;
      }).join('')}</ul>
      <h2>Rewards earned</h2>
      <p class="lead">Anil's Grocery never says you earned store rewards or used a coupon unless a confirmed purchase or a verified offer shows it. Today no store connection exists, so no store rewards are shown. The points below are Anil's Grocery's own, for reporting prices.</p>`;
  }

  function rewardsHub() {
    const sub = state.rsub;
    const seg = `<div class="seg tabs" role="group" aria-label="Rewards sections">
      <button aria-pressed="${sub === 'points'}" data-action="rsub" data-t="points"><b>My points</b></button>
      <button aria-pressed="${sub === 'report'}" data-action="rsub" data-t="report"><b>Report a price</b></button>
      <button aria-pressed="${sub === 'programs'}" data-action="rsub" data-t="programs"><b>Store programs</b></button></div>`;
    const body = sub === 'report' ? reportView() : sub === 'programs' ? programsView() : rewardsView();
    return header('Rewards') + seg + body;
  }

  // ---------- me ----------
  function moneyField(name, label, value, hint) {
    return `<label class="field"><span>${label}</span><span class="money"><b>$</b><input name="${name}" inputmode="decimal" value="${value == null ? '' : esc(value)}" placeholder="0.00"></span>${hint ? `<small>${hint}</small>` : ''}</label>`;
  }

  function prefsForm() {
    const p = state.user.prefs;
    const chains = ['winndixie', 'publix', 'walmart'].map((k) => `<label class="check-line"><input type="checkbox" name="chain" value="${k}" ${p.preferred_chains.includes(k) ? 'checked' : ''}> ${chainBadge(k, 'sm')}</label>`).join('');
    return `<form data-form="prefs" novalidate>
      <div id="pref-err"></div>
      <fieldset class="field"><legend>Preferred stores</legend>${chains}<small>Leave all unchecked to compare all three.</small></fieldset>
      <label class="field"><span>Shopping strategy</span><select name="strategy">${STRATEGIES.map(([k, l]) => `<option value="${k}" ${p.strategy === k ? 'selected' : ''}>${l}</option>`).join('')}</select><small>${esc(STRATEGIES.find((x) => x[0] === p.strategy)[2])}</small></label>
      ${moneyField('budget', 'Budget per shopping trip (optional)', p.budget, '')}
      <label class="field"><span>People in your household (optional)</span><input name="household" inputmode="numeric" maxlength="2" value="${p.household == null ? '' : p.household}"></label>
      ${moneyField('min_savings', 'Smallest saving worth an extra stop', p.min_savings, 'Best value adds a store only if it saves at least this much after driving.')}
      ${moneyField('cost_per_mile', 'Driving cost per mile', p.cost_per_mile, 'For estimates only, using straight-line distance. Enter 0 to ignore driving.')}
      <div class="field"><label class="check-line"><input type="checkbox" name="verified_only" ${p.verified_only ? 'checked' : ''}> Only use verified prices</label>
        <small>Leaves out sample data and unconfirmed shopper reports. You will see fewer prices.</small>
        <label class="check-line"><input type="checkbox" name="reminders_on" ${p.reminders_on ? 'checked' : ''}> Remind me when it's time to restock</label></div>
      <label class="field"><span>Favorite brands (optional)</span><input name="fav_brands" maxlength="200" value="${esc(p.fav_brands)}" placeholder="Comma separated"><small>Items from these brands are listed first when you search.</small></label>
      <label class="field"><span>Words to avoid (optional)</span><input name="avoid_terms" maxlength="200" value="${esc(p.avoid_terms)}" placeholder="Like peanut, pork"><small>Items with these words in the name or brand won't be suggested.</small></label>
      <label class="field"><span>Dietary notes (optional)</span><input name="diet_note" maxlength="200" value="${esc(p.diet_note)}"><small>Saved for you. It doesn't filter results; use the words to avoid for that.</small></label>
      <div class="pad"><button class="btn wide" type="submit">Save preferences</button></div></form>`;
  }

  function restockSection() {
    const r = state.reminders;
    if (!r) return loading();
    const conf = (e) => (e.manual ? 'set by you' : e.based_on === 1 ? 'early estimate, 1 gap between purchases'
      : `estimate from ${e.based_on} gaps between purchases`);
    const tracked = r.tracked.length ? `<ul class="rows">${r.tracked.slice(0, 12).map((t) => {
      const e = t.estimate;
      const line = e ? `About every ${e.interval_days} days (${conf(e)}). Next restock around ${fmtDate(e.runs_out)}, ${inDays(e.days_left)}. Estimate only.`
        : `Bought ${plural(t.purchases, 'time', 'times')}. Buy it once more and we'll estimate when you run out, or set your own interval.`;
      return `<li class="row top"><div class="grow"><div class="name">${esc(t.name)}</div><div class="sub">${esc(line)}</div>${t.reminders ? '' : '<div class="sub">Reminders off for this item.</div>'}</div>
        <button class="chip" data-action="open-item" data-pid="${t.product_id}" data-name="${esc(t.name)}">Edit</button></li>`;
    }).join('')}</ul>` : '<p class="lead">Nothing tracked yet. When you take an item off your list and tell us you bought it, we start learning how often you buy it.</p>';
    const favs = r.favorites.length ? `<h3 class="sec">Favorites</h3><ul class="rows">${r.favorites.map((f) => `<li class="row"><span class="grow name">${esc(f.name)}</span><button class="link" data-action="unfav" data-pid="${f.product_id}">Remove</button></li>`).join('')}</ul>` : '';
    const ex = r.excluded.length ? `<h3 class="sec">Never suggest</h3><ul class="rows">${r.excluded.map((f) => `<li class="row"><span class="grow name">${esc(f.name)}</span><button class="link" data-action="unexclude" data-pid="${f.product_id}">Allow again</button></li>`).join('')}</ul>` : '';
    return tracked + favs + ex;
  }

  function historySection() {
    const h = state.purchases;
    if (!h) return loading();
    const sv = h.savings_count
      ? `<p class="lead">Estimated savings: <strong>${money(h.est_savings)}</strong> on ${plural(h.savings_count, 'purchase', 'purchases')} where you entered the price you paid. It compares that price with the highest real price among the three stores at the time, so treat it as an estimate.</p>`
      : '<p class="lead">Estimated savings show up once you enter the store and price when you mark an item as bought, and we have real prices for at least two stores.</p>';
    const list = h.purchases.length ? `<ul class="rows">${h.purchases.slice(0, 15).map((p) => `<li class="row"><div class="grow"><div class="name">${esc(p.name)}${p.qty > 1 ? ` <span class="qty">× ${p.qty}</span>` : ''}</div>
        <div class="sub">${fmtDate(p.bought_at)}${p.chain_name ? `, ${esc(p.chain_name)}` : ''}${p.paid != null ? `, paid ${money(p.paid)} each` : ''}</div></div>
        <button class="link danger" data-action="del-purchase" data-id="${p.id}" aria-label="Delete purchase of ${esc(p.name)}">Delete</button></li>`).join('')}</ul>`
      : '<p class="lead">No purchases yet.</p>';
    return `<p class="lead">${plural(h.total, 'purchase', 'purchases')} recorded.</p>${sv}${list}`;
  }

  function meView() {
    const u = state.user;
    const alerts = state.alerts === null ? loading()
      : state.alerts.length ? `<ul class="rows">${state.alerts.map((a) => `<li class="row"><div class="grow"><div class="name">${esc(a.name)}</div>
          <div class="sub">At or below ${money(a.target_price)}</div>
          <div class="sub">${a.triggered ? `<span class="good">Now ${money(a.best.price)} at ${esc(a.best.store)}, ${a.best.distance} mi</span>` : 'Not there yet'}</div></div>
          <button class="link danger" data-action="del-alert" data-id="${a.id}" aria-label="Remove alert for ${esc(a.name)}">Remove</button></li>`).join('')}</ul>`
        : '<p class="lead">No alerts yet. Open any item on your list to set one.</p>';
    const stores = state.nearStores === null ? loading()
      : state.nearStores.length ? `<ul class="rows">${state.nearStores.map((s) => `<li class="row"><button class="row-main" data-action="open-store" data-id="${s.id}"><span class="name">${esc(s.name)}${sampleChip(s.sample)}</span>
          <span class="sub">${s.distance} mi, ${s.review_count ? `${stars(s.rating)} ${s.rating} (${s.review_count})` : 'No reviews yet'}</span></button></li>`).join('')}</ul>`
        : `<p class="lead">No stores within ${u.radius_miles} miles yet. Add one under Rewards, Report a price.</p>`;
    const ios = /iphone|ipad/i.test(navigator.userAgent) && !navigator.standalone;
    const install = state.installEvent
      ? '<div class="pad"><button class="btn quiet" data-action="install">Install Anil\'s Grocery on this device</button></div>'
      : ios ? '<p class="lead">To install on iPhone, tap Share, then Add to Home Screen.</p>' : '';
    return header('Me') + `
      <div class="row"><div class="grow"><div class="name">${esc(u.name)}</div><div class="sub">${esc(u.email)}</div></div><button class="chip" data-action="open-location">${esc(u.city || u.zip)} ${esc(u.zip)}</button></div>
      ${install}
      <h2>Shopping preferences</h2>${prefsForm()}
      <h2>Restock reminders</h2>${restockSection()}
      <h2>Purchase history</h2>${historySection()}
      <h2>Price alerts</h2>${alerts}
      <h2>Stores and reviews</h2>${stores}
      <h2>Send feedback</h2>
      <form data-form="feedback" novalidate>
        <div id="fb-err"></div>
        <label class="field"><span>What is it about?</span><select name="category"><option value="idea">An idea</option><option value="bug">Something broke</option><option value="wrong_price">A wrong price</option><option value="other">Something else</option></select></label>
        <label class="field"><span>Message</span><textarea name="message" maxlength="1000" required></textarea></label>
        <div class="pad"><button class="btn" type="submit">Send feedback</button></div>
      </form>
      <div class="pad tall"><button class="btn quiet" data-action="logout">Sign out</button>
      <p class="sub gap">ZIP codes by Zippopotam.us. Store locations from OpenStreetMap contributors when imported. Prices are reported by shoppers and may be out of date.</p></div>`;
  }

  // Primary navigation sits at the top: List, Deals, Rewards, Me.
  function navView() {
    return `<header class="topbar"><div class="topbar-in"><span class="brandmark">Anil's Grocery</span>
      <nav class="nav" aria-label="Main"><ul>${TABS.map(([id, label]) => `<li><button data-action="tab" data-tab="${id}" ${state.tab === id ? 'aria-current="page"' : ''}>${icon(id)}<span>${label}</span></button></li>`).join('')}</ul></nav></div></header>`;
  }

  // ---------- render ----------
  function render() {
    const app = $('#app');
    const y = window.scrollY;
    document.documentElement.dataset.perk = state.user && state.user.perks.includes('citrus_theme') ? 'citrus' : '';
    if (!state.ready) { app.innerHTML = ''; return; }
    if (!state.user) {
      app.innerHTML = authView();
    } else {
      const view = { list: listView, deals: dealsView, rewards: rewardsHub, me: meView }[state.tab];
      const title = TABS.find((t) => t[0] === state.tab)[2];
      document.title = `${title} - Anil's Grocery`;
      app.innerHTML = `${navView()}<div class="shell"><main id="main" tabindex="-1">${view()}</main></div>`;
    }
    if ($('.tag.print')) state.printTag = false;
    window.scrollTo(0, y);
    if (state.focus) { const el = $(state.focus); if (el) el.focus(); state.focus = null; }
  }

  // ---------- data loading ----------
  const tryApi = (path) => api('GET', path).catch((e) => { toast(e.message); return null; });

  async function loadBasket() {
    const [b, c, r] = await Promise.all([tryApi('/api/basket'), tryApi('/api/compare'), tryApi('/api/reminders')]);
    if (b) state.basket = b;
    if (c) state.compare = c;
    if (r) state.reminders = r;
    if (!state.strategy && state.user) state.strategy = state.user.prefs.strategy;
    render();
  }
  async function loadDeals() {
    const d = await tryApi('/api/deals');
    if (d) state.deals = d;
    render();
  }
  async function loadReport() {
    try {
      const [s, f] = await Promise.all([api('GET', '/api/stores/nearby'), api('GET', '/api/prices/verify-feed')]);
      state.nearStores = s.stores;
      state.feed = f.feed;
    } catch (e) { toast(e.message); }
    render();
  }
  async function loadRewards() {
    try { state.rewards = await api('GET', '/api/rewards'); state.user = state.rewards.user; } catch (e) { toast(e.message); }
    render();
  }
  async function loadMe() {
    const [a, s, r, p] = await Promise.all([tryApi('/api/alerts'), tryApi('/api/stores/nearby'),
      tryApi('/api/reminders'), tryApi('/api/purchases')]);
    if (a) state.alerts = a;
    if (s) state.nearStores = s.stores;
    if (r) state.reminders = r;
    if (p) state.purchases = p;
    render();
  }
  async function refreshUser() {
    try { const d = await api('GET', '/api/me'); state.user = d.user; state.brands = Object.fromEntries(d.brands.map((b) => [b.key, b])); } catch { /* ignore */ }
  }
  function loadRewardsHub() {
    if (state.rsub === 'report') loadReport();
    else if (state.rsub === 'points') loadRewards();
    else render();
  }
  function loadTab() {
    ({ list: loadBasket, deals: loadDeals, rewards: loadRewardsHub, me: loadMe })[state.tab]();
  }
  function goTab(tab) {
    if (tab === 'report') { state.rsub = 'report'; tab = 'rewards'; }
    state.tab = tab;
    state.printTag = true;
    closeSheet();
    window.scrollTo(0, 0);
    render();
    loadTab();
    $('#main') && $('#main').focus({ preventScroll: true });
  }
  function invalidateData() {
    state.basket = null; state.compare = null; state.reminders = null; state.deals = null; state.purchases = null;
    state.feed = null; state.nearStores = null; state.rewards = null; state.alerts = null;
  }

  // ---------- sheets ----------
  let lastFocus = null;
  function openSheet(title, html) {
    lastFocus = document.activeElement;
    const root = $('#sheet-root');
    $('#app').inert = true;
    root.innerHTML = `<div class="backdrop" data-action="close-sheet"></div>
      <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
        <div class="sheet-head"><h2 id="sheet-title">${esc(title)}</h2><button class="chip" data-action="close-sheet" aria-label="Close">Close</button></div>
        <div id="sheet-body">${html}</div></div>`;
    $('.sheet', root).focus?.();
    const first = $('.sheet input, .sheet select, .sheet textarea', root);
    if (first) first.focus(); else $('.sheet .chip', root).focus();
  }
  function setSheetBody(html) { const b = $('#sheet-body'); if (b) b.innerHTML = html; }
  function closeSheet() {
    const root = $('#sheet-root');
    if (!root.firstChild) return;
    $('#app').inert = false;
    root.innerHTML = '';
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  async function openItem(pid, name) {
    openSheet(name, loading('Loading prices…'));
    try {
      const [pr, hist, usage] = await Promise.all([api('GET', `/api/products/${pid}/prices`), api('GET', `/api/history/${pid}?days=60`), api('GET', `/api/products/${pid}/usage`)]);
      const item = state.basket && state.basket.items.find((i) => i.product_id === pid);
      const ci = state.compare && state.compare.items.find((i) => i.product_id === pid);
      const pts = hist.points;
      const trend = pts.length >= 3
        ? `<h3>Lowest price near you</h3>${spark(pts)}<p class="sub">${money(Math.min(...pts.map((p) => p.price)))} to ${money(Math.max(...pts.map((p) => p.price)))} over the last 60 days</p>` : '';
      const opts = pr.options.length
        ? `<h3>All prices within ${pr.radius} miles</h3><ul class="rows">${pr.options.slice(0, 8).map((o) => `<li class="row"><div class="grow"><div class="name">${esc(o.store)}${sampleChip(o.store_source === 'demo')}</div><div class="sub">${o.distance} mi, ${ageText(o.age_days)}${o.verified ? `, confirmed ${o.verified}×` : ''}</div></div>${mini(o.price)}</li>`).join('')}</ul>`
        : `<p class="lead">No prices within ${pr.radius} miles yet. Report one to help everyone nearby.</p>`;
      const meta = [pr.product.brand, pr.product.package_size].filter(Boolean).join(', ');
      const three = ci ? `<h3>Winn-Dixie, Publix and Walmart</h3><div class="cells">${Object.keys(ci.prices).map((k) => priceCell(k, ci.prices[k], ci.best === k)).join('')}</div><p class="why">${esc(ci.reason)}</p>` : '';
      const e = usage.estimate, u = usage.prefs;
      const usageLine = e
        ? `Estimated to run out around ${fmtDate(e.runs_out)} (${inDays(e.days_left)}), based on ${e.manual ? 'the interval you set' : plural(e.based_on, 'gap', 'gaps') + ' between your purchases'}. This is an estimate, not a guarantee.`
        : usage.count ? `You've bought this ${plural(usage.count, 'time', 'times')}. Buy it once more and we'll estimate when you run out, or set your own interval below.`
          : 'No purchases yet. When you mark this item as bought, we start learning how often you need it.';
      setSheetBody(`
        ${meta ? `<p class="sub">${esc(meta)}</p>` : ''}
        ${item ? `<div class="row"><span class="grow name">Quantity</span><div class="qty-ctl"><button data-action="qty" data-id="${item.item_id}" data-qty="${item.qty - 1}" aria-label="One fewer" ${item.qty <= 1 ? 'disabled' : ''}>−</button><output aria-live="polite">${item.qty}</output><button data-action="qty" data-id="${item.item_id}" data-qty="${item.qty + 1}" aria-label="One more">+</button></div></div>` : ''}
        ${three}${trend}${opts}
        <h3>Restock reminders</h3>
        <p class="sub">${esc(usageLine)}</p>
        <form data-form="itemprefs" data-pid="${pid}" novalidate>
          <div id="usage-err"></div>
          <label class="field"><span>Restock about every (days)</span><input name="interval_days" inputmode="numeric" maxlength="3" value="${u.interval_days || ''}" placeholder="Learn from my purchases"></label>
          <label class="field"><span>Usual quantity</span><input name="usual_qty" inputmode="numeric" maxlength="2" value="${u.usual_qty || ''}" placeholder="${e ? e.usual_qty : '1'}"></label>
          <label class="check-line"><input type="checkbox" name="reminders" ${u.reminders ? 'checked' : ''}> Remind me about this item</label>
          <div class="pad"><button class="btn small" type="submit">Save</button></div></form>
        <h3>Price alert</h3>
        <form data-form="alert" data-pid="${pid}" class="actions" novalidate>
          <span class="money grow"><b>$</b><input name="target" inputmode="decimal" placeholder="Alert me at or below" aria-label="Alert me at or below"></span>
          <button class="btn" type="submit">Set alert</button></form>
        <div id="sheet-err"></div>
        <div class="actions gap"><button class="btn quiet" data-action="report-for" data-pid="${pid}" data-name="${esc(name)}">Report a price</button>
        <button class="btn quiet" data-action="fav" data-pid="${pid}" data-on="${u.favorite ? 0 : 1}">${u.favorite ? 'Remove from favorites' : 'Add to favorites'}</button>
        <button class="link danger" data-action="exclude" data-pid="${pid}">Never suggest this item</button>
        ${item ? `<button class="link danger" data-action="remove" data-id="${item.item_id}">Remove from list</button>` : ''}</div>`);
    } catch (e) { setSheetBody(errBox(e.message)); }
  }

  function openRemove(itemId) {
    const it = state.basket && state.basket.items.find((i) => i.item_id === itemId);
    if (!it) return;
    const last = safeStore.get('lastChain') || '';
    openSheet('Did you buy this item?', `
      <p class="name">${esc(it.name)}${it.qty > 1 ? ` <span class="qty">× ${it.qty}</span>` : ''}</p>
      <p class="sub gap">If you bought it, add where and what you paid. That shares the price with other shoppers and earns points.</p>
      <div class="pair">
        <label class="field"><span>Store</span><select id="rm-chain"><option value="">Not sure</option>${['winndixie', 'publix', 'walmart'].map((k) => `<option value="${k}" ${k === last ? 'selected' : ''}>${esc(brandOf(k).name)}</option>`).join('')}</select></label>
        <label class="field"><span>Price each</span><span class="money"><b>$</b><input id="rm-paid" inputmode="decimal" placeholder="0.00"></span></label>
      </div>
      <div id="rm-err"></div>
      <div class="stack">
        <button class="btn wide" data-action="remove-yes" data-id="${itemId}">Yes, I bought it</button>
        <button class="btn wide quiet" data-action="remove-no" data-id="${itemId}">No, remove it</button>
      </div>
      <p class="sub">Yes saves it to your purchase history, which is how we learn when you will need it again. No takes it off your list without recording anything. You can undo either one.</p>`);
  }

  // ---------- receipts ----------
  function fileToJpeg(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onerror = () => reject(new Error("We couldn't read that file."));
      fr.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error("That file isn't a photo we can open."));
        img.onload = () => {
          const k = Math.min(1, 1600 / Math.max(img.width, img.height));
          const cv = document.createElement('canvas');
          cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
          cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
          resolve(cv.toDataURL('image/jpeg', 0.82).split(',')[1]);
        };
        img.src = fr.result;
      };
      fr.readAsDataURL(file);
    });
  }

  function receiptStart(msg = '') {
    setSheetBody(`${errBox(msg)}
      <label class="field"><span>Take or choose a photo of the receipt</span><input id="receipt-file" type="file" accept="image/*" capture="environment"><small>The photo is sent to Anthropic's Claude to read the items, and isn't stored by Anil's Grocery. Needs the server to have a key set.</small></label>
      <form data-form="receipt-text" novalidate>
        <label class="field"><span>Or paste the receipt text</span><textarea name="text" placeholder="MILK WHL GAL  3.49&#10;EGGS LG 12CT  2.99"></textarea><small>Works with e-receipts and store app receipts. Nothing is sent anywhere.</small></label>
        <button class="btn wide" type="submit">Read receipt</button></form>`);
  }

  async function openReceipt() {
    openSheet('Scan a receipt', loading());
    try { if (!state.nearStores) state.nearStores = (await api('GET', '/api/stores/nearby')).stores; } catch (e) { toast(e.message); }
    receiptStart();
  }

  async function readReceipt(payload) {
    setSheetBody(loading('Reading your receipt…'));
    try {
      const d = await api('POST', '/api/receipts/read', payload);
      if (!d.lines.length) { receiptStart("We couldn't find any items with prices. Try a clearer photo or paste the text."); return; }
      receiptReview(d);
    } catch (e) { receiptStart(e.message); }
  }

  function receiptReview(d) {
    const real = (state.nearStores || []).filter((s) => !s.sample);
    const lastStore = safeStore.get('lastStore');
    const pick = real.find((s) => d.chain && chainKeyOf(s.chain) === d.chain) || real.find((s) => String(s.id) === lastStore) || real[0];
    const stores = real.length
      ? `<label class="field"><span>Which store is this receipt from?</span><select name="store">${real.map((s) => `<option value="${s.id}" ${pick && s.id === pick.id ? 'selected' : ''}>${esc(s.name)} (${s.distance} mi)</option>`).join('')}</select></label>`
      : '<p class="err" role="alert">No real stores near you are listed yet. Add your store first, then scan again.</p><button type="button" class="btn quiet" data-action="add-store-open">Add a store</button>';
    const lines = d.lines.map((l, i) => {
      const c = l.candidates[0];
      const good = c && c.score >= 0.6;
      return `<li class="rline"><label class="check-line"><input type="checkbox" name="inc-${i}" ${good ? 'checked' : ''} ${c ? '' : 'disabled'} aria-label="Save ${esc(l.name)}"> <span class="grow"><span class="name">${esc(l.name)}</span>${l.qty > 1 ? ` <span class="qty">× ${l.qty}</span>` : ''}</span></label>
        ${c ? `<select name="pid-${i}" aria-label="Item for ${esc(l.name)}">${l.candidates.map((x) => `<option value="${x.product_id}">${esc(x.name)}</option>`).join('')}</select>`
          : '<span class="sub">No matching item. Add it to your list and scan again.</span>'}
        <span class="money"><b>$</b><input name="price-${i}" inputmode="decimal" value="${l.price.toFixed(2)}" aria-label="Price of ${esc(l.name)}"></span>
        <input type="hidden" name="qty-${i}" value="${l.qty}"></li>`;
    }).join('');
    setSheetBody(`<form data-form="receipt-save" data-n="${d.lines.length}" novalidate>
      <div id="receipt-err"></div>${stores}
      <p class="sub">Check each match and price. Only ticked lines are saved. Unticked or unmatched lines are ignored.</p>
      <ul class="rlines">${lines}</ul>
      <p class="sub gap">Saving shares these prices with other shoppers (5 points each, up to your daily limit), adds them to your purchase history, and removes them from your list.</p>
      <button class="btn wide" type="submit" ${real.length ? '' : 'disabled'}>Save prices</button></form>`);
  }
  const chainKeyOf = (chain) => Object.keys(state.brands).find((k) => String(chain || '').toLowerCase().replace(/[^a-z]/g, '').startsWith(k));

  async function openStore(id) {
    openSheet('Store', loading());
    try {
      const d = await api('GET', `/api/stores/${id}`);
      const mine = d.mine || { rating: 0, comment: '' };
      setSheetBody(`
        <p class="sub">${esc(d.store.name)}${sampleChip(d.store.sample)}${d.store.address ? `, ${esc(d.store.address)}` : ''}</p>
        <p>${d.review_count ? `${stars(d.rating)} <strong>${d.rating}</strong> <span class="sub">${plural(d.review_count, 'review', 'reviews')}</span>` : '<span class="sub">No reviews yet</span>'}</p>
        <h3>${d.mine ? 'Update your review' : 'Write a review'}</h3>
        <form data-form="review" data-id="${id}" novalidate>
          <div id="review-err"></div>
          <div class="star-pick" role="group" aria-label="Your rating">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-action="star" data-n="${n}" aria-pressed="${n === mine.rating}" aria-label="${n} ${n === 1 ? 'star' : 'stars'}">★</button>`).join('')}</div>
          <input type="hidden" name="rating" value="${mine.rating || ''}">
          <label class="field tall"><span>Comment</span><textarea name="comment" maxlength="500">${esc(mine.comment)}</textarea><small>Write at least 20 characters in your first review to earn 3 points.</small></label>
          <button class="btn" type="submit">${d.mine ? 'Update review' : 'Save review'}</button>
        </form>
        <h3>Reviews</h3>
        ${d.reviews.length ? d.reviews.map((r) => `<div class="review">${stars(r.rating)} <strong>${who(r.author)}</strong> <span class="sub">${esc(r.created_at.slice(0, 10))}</span>${r.comment ? `<p>${esc(r.comment)}</p>` : ''}</div>`).join('') : '<p class="sub">Be the first to review this store.</p>'}`);
    } catch (e) { setSheetBody(errBox(e.message)); }
  }

  function openLocation() {
    const u = state.user;
    openSheet('Search area', `
      <form data-form="location" novalidate>
        <div id="loc-err"></div>
        <div class="field"><span>Distance</span>
          <div class="seg flush" role="group" aria-label="Distance">${[5, 10, 15].map((r) => `<button type="button" data-action="radius" data-r="${r}" aria-pressed="${r === u.radius_miles}"><b>${r}</b><span>miles</span></button>`).join('')}</div></div>
        <input type="hidden" name="radius" value="${u.radius_miles}">
        <label class="field tall"><span>ZIP code</span><input name="zip" inputmode="numeric" maxlength="5" pattern="[0-9]{5}" value="${esc(u.zip)}"></label>
        <button class="btn wide" type="submit">Save</button>
      </form>`);
  }

  function openAddStore() {
    openSheet('Add a store', `
      <form data-form="add-store" novalidate>
        <div id="store-err"></div>
        <label class="field"><span>Store name</span><input name="name" maxlength="80" placeholder="Publix, Aldi, Corner Market"></label>
        <label class="field"><span>Street address (optional)</span><input name="address" maxlength="120" autocomplete="street-address"></label>
        <label class="field"><span>ZIP code</span><input name="zip" inputmode="numeric" maxlength="5" value="${esc(state.user.zip)}"></label>
        <button class="btn wide" type="submit">Add store</button>
      </form>`);
  }

  // ---------- typeahead ----------
  let suggTimer;
  let suggItems = [];
  function attachTypeahead() {
    document.querySelectorAll('[data-typeahead]').forEach((input) => {
      if (input.dataset.bound) return;
      input.dataset.bound = '1';
      input.addEventListener('input', () => {
        const kind = input.dataset.typeahead;
        if (kind === 'report') state.report.product = null;
        clearTimeout(suggTimer);
        suggTimer = setTimeout(() => showSuggestions(input, kind), 150);
      });
    });
  }
  async function showSuggestions(input, kind) {
    const box = $(`#sugg-${kind}`);
    const q = input.value.trim();
    if (!q) { box.hidden = true; return; }
    try {
      suggItems = await api('GET', `/api/products?q=${encodeURIComponent(q)}`);
    } catch { return; }
    const exact = suggItems.some((p) => p.name.toLowerCase() === q.toLowerCase());
    box.innerHTML = suggItems.slice(0, 6).map((p) => `<li><button type="button" data-action="pick" data-kind="${kind}" data-id="${p.id}" data-name="${esc(p.name)}">${esc(p.name)}</button></li>`).join('') +
      (!exact && q.length >= 2 ? `<li><button type="button" data-action="new-product" data-kind="${kind}" data-name="${esc(q)}">Add “${esc(q)}” as a new item</button></li>` : '');
    box.hidden = !box.innerHTML;
  }

  async function resolveProduct(name) {
    const list = await api('GET', `/api/products?q=${encodeURIComponent(name)}`);
    const exact = list.find((p) => p.name.toLowerCase() === name.toLowerCase());
    if (exact) return exact;
    return api('POST', '/api/products', { name });
  }

  async function addToList(product) {
    await api('POST', '/api/list', { product_id: product.id });
    state.basket = null;
    state.focus = '#add-input';
    render();
    await loadBasket();
    state.focus = '#add-input';
    render();
  }

  // ---------- actions ----------
  const actions = {
    tab: (el) => { if (el.dataset.tab === 'rewards' && el.closest('.nav')) state.rsub = 'points'; goTab(el.dataset.tab); },
    ltab: (el) => { state.ltab = el.dataset.t; state.printTag = true; render(); },
    rsub: (el) => { state.rsub = el.dataset.t; render(); loadRewardsHub(); },
    strategy: (el) => {
      state.strategy = el.dataset.s; state.printTag = true; render();
      api('PATCH', '/api/prefs', { strategy: el.dataset.s }).then((r) => { state.user = r.user; }).catch((e) => toast(e.message));
    },
    remove: (el) => openRemove(Number(el.dataset.id)),
    receipt: () => openReceipt(),
    'add-store-open': () => openAddStore(),
    'remove-yes': async (el) => {
      const chain = $('#rm-chain') ? $('#rm-chain').value : '';
      const raw = $('#rm-paid') ? $('#rm-paid').value.trim() : '';
      const paid = raw ? parsePrice(raw) : null;
      if (raw && !(paid > 0)) { setErr('#rm-err', 'Enter the price like 3.49, or leave it blank.'); return; }
      try {
        const r = await api('POST', `/api/list/${el.dataset.id}/remove`, { bought: true, chain: chain || null, paid });
        if (chain) safeStore.set('lastChain', chain);
        closeSheet();
        const shared = r.price_shared ? ` Price shared with ${r.price_shared.store}${r.price_shared.points ? `, +${r.price_shared.points} points` : ''}.` : '';
        toast(`Marked ${r.name} as bought.${shared}`, async () => {
          try { await api('POST', `/api/purchases/${r.purchase_id}/undo`, {}); toast('Put back on your list.'); await loadBasket(); } catch (e) { toast(e.message); }
        });
        await loadBasket();
      } catch (e) { setErr('#rm-err', e.message); toast(e.message); }
    },
    'remove-no': async (el) => {
      try {
        const r = await api('POST', `/api/list/${el.dataset.id}/remove`, { bought: false });
        closeSheet();
        toast(`Removed ${r.name} from your list.`, async () => {
          try { await api('POST', '/api/list', { product_id: r.product_id, qty: r.qty }); toast('Put back on your list.'); await loadBasket(); } catch (e) { toast(e.message); }
        });
        await loadBasket();
      } catch (e) { toast(e.message); }
    },
    'toast-undo': () => { const fn = state.toastUndo; state.toastUndo = null; $('#toast').hidden = true; if (fn) fn(); },
    rem: async (el) => {
      const act = el.dataset.do;
      try {
        const r = await api('POST', `/api/reminders/${el.dataset.pid}`, { action: act, days: 3 });
        if (act === 'complete') {
          toast('Marked as restocked.', async () => { try { await api('POST', `/api/purchases/${r.purchase_id}/undo`, { restore: false }); await loadBasket(); } catch (e) { toast(e.message); } });
        } else toast({ add: 'Added to your list.', snooze: "Snoozed for 3 days.", dismiss: 'Dismissed until your next purchase.' }[act]);
        await loadBasket();
      } catch (e) { toast(e.message); }
    },
    'fav-add': async (el) => {
      try { await api('POST', '/api/list', { product_id: Number(el.dataset.pid) }); await loadBasket(); } catch (e) { toast(e.message); }
    },
    fav: async (el) => {
      try { await api('PATCH', `/api/products/${el.dataset.pid}/prefs`, { favorite: el.dataset.on === '1' }); state.reminders = null; await openItem(Number(el.dataset.pid), $('#sheet-title').textContent); loadTab(); } catch (e) { toast(e.message); }
    },
    exclude: async (el) => {
      try { await api('PATCH', `/api/products/${el.dataset.pid}/prefs`, { excluded: true }); closeSheet(); toast("Got it. We won't suggest this item or remind you about it."); invalidateData(); render(); loadTab(); } catch (e) { toast(e.message); }
    },
    unfav: async (el) => { try { await api('PATCH', `/api/products/${el.dataset.pid}/prefs`, { favorite: false }); await loadMe(); } catch (e) { toast(e.message); } },
    unexclude: async (el) => { try { await api('PATCH', `/api/products/${el.dataset.pid}/prefs`, { excluded: false }); await loadMe(); } catch (e) { toast(e.message); } },
    'del-purchase': async (el) => { try { await api('DELETE', `/api/purchases/${el.dataset.id}`); await loadMe(); } catch (e) { toast(e.message); } },
    'switch-auth': () => { state.authMode = state.authMode === 'signup' ? 'login' : 'signup'; state.authError = ''; render(); },
    'close-sheet': () => closeSheet(),
    'open-location': () => openLocation(),
    'open-item': (el) => openItem(Number(el.dataset.pid), el.dataset.name),
    'open-store': (el) => openStore(el.dataset.id),
    plan: (el) => { state.plan = Number(el.dataset.stops); render(); },
    radius: (el) => {
      el.parentElement.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === el)));
      $('input[name="radius"]').value = el.dataset.r;
    },
    star: (el) => {
      el.parentElement.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.n) === Number(el.dataset.n))));
      $('input[name="rating"]').value = el.dataset.n;
    },
    toggle: async (el) => {
      try { await api('PATCH', `/api/list/${el.dataset.id}`, { checked: el.dataset.checked === '1' }); await loadBasket(); } catch (e) { toast(e.message); }
    },
    qty: async (el) => {
      try {
        await api('PATCH', `/api/list/${el.dataset.id}`, { qty: Number(el.dataset.qty) });
        await loadBasket();
        const it = state.basket.items.find((i) => i.item_id === Number(el.dataset.id));
        if (it) openItem(it.product_id, it.name);
      } catch (e) { toast(e.message); }
    },
    'report-for': (el) => { state.report.product = { id: Number(el.dataset.pid), name: el.dataset.name }; goTab('report'); },
    pick: async (el) => {
      const kind = el.dataset.kind;
      $(`#sugg-${kind}`).hidden = true;
      if (kind === 'add') { $('#add-input').value = ''; try { await addToList({ id: Number(el.dataset.id) }); } catch (e) { toast(e.message); } }
      else { state.report.product = { id: Number(el.dataset.id), name: el.dataset.name }; $('#rep-item').value = el.dataset.name; $('input[name="price"]').focus(); }
    },
    'new-product': async (el) => {
      try {
        const p = await api('POST', '/api/products', { name: el.dataset.name });
        $(`#sugg-${el.dataset.kind}`).hidden = true;
        if (el.dataset.kind === 'add') { $('#add-input').value = ''; await addToList(p); }
        else { state.report.product = { id: p.id, name: p.name }; $('#rep-item').value = p.name; $('input[name="price"]').focus(); }
      } catch (e) { toast(e.message); }
    },
    confirm: async (el) => {
      try {
        const r = await api('POST', `/api/prices/${el.dataset.id}/vote`, { vote: 'confirm' });
        toast(r.message);
        await refreshUser(); invalidateData(); state.feed = null; render(); loadReport();
      } catch (e) { toast(e.message); }
    },
    'edit-price': (el) => { state.editingPrice = Number(el.dataset.id); render(); const i = $('form[data-form="correct"] input'); if (i) i.focus(); },
    'cancel-edit': () => { state.editingPrice = null; render(); },
    redeem: async (el) => {
      try {
        const r = await api('POST', `/api/rewards/${el.dataset.key}/redeem`);
        state.user = r.user; toast(r.message); await loadRewards();
      } catch (e) { toast(e.message); }
    },
    'del-alert': async (el) => {
      try { await api('DELETE', `/api/alerts/${el.dataset.id}`); await loadMe(); } catch (e) { toast(e.message); }
    },
    install: async () => {
      if (!state.installEvent) return;
      state.installEvent.prompt();
      await state.installEvent.userChoice.catch(() => {});
      state.installEvent = null; render();
    },
    logout: async () => {
      try { await api('POST', '/api/auth/logout'); } catch { /* still sign out locally */ }
      state.user = null; invalidateData(); state.tab = 'list'; state.ltab = 'items'; state.rsub = 'points'; state.strategy = null; state.authMode = 'login'; state.printTag = true; render();
    },
  };

  const parsePrice = (s) => {
    const n = parseFloat(String(s || '').replace(/[$,\s]/g, '').replace(',', '.'));
    return Number.isFinite(n) ? n : NaN;
  };
  const setErr = (sel, msg) => { const el = $(sel); if (el) el.innerHTML = errBox(msg); };

  const forms = {
    auth: async (f) => {
      const fd = Object.fromEntries(new FormData(f));
      state.authError = '';
      state.authDraft = { name: fd.name || '', email: fd.email || '', zip: fd.zip || '' };
      try {
        const path = state.authMode === 'signup' ? '/api/auth/signup' : '/api/auth/login';
        const body = state.authMode === 'signup' ? fd : { email: fd.email, password: fd.password };
        state.user = (await api('POST', path, body)).user;
        state.authDraft = {};
        invalidateData(); state.tab = 'list'; render(); loadTab();
      } catch (e) { state.authError = e.message; render(); }
    },
    'add-item': async (f) => {
      const q = new FormData(f).get('q').trim();
      if (!q) return;
      try { $('#sugg-add').hidden = true; f.reset(); await addToList(await resolveProduct(q)); } catch (e) { toast(e.message); }
    },
    report: async (f) => {
      const fd = new FormData(f);
      const r = state.report;
      r.error = '';
      const price = parsePrice(fd.get('price'));
      const storeId = fd.get('store');
      const name = (fd.get('item') || '').trim();
      if (!storeId || storeId === '__new') r.error = 'Choose a store.';
      else if (!name) r.error = 'Enter the item.';
      else if (!(price > 0)) r.error = 'Enter the price, like 3.49.';
      if (r.error) { render(); return; }
      try {
        const product = r.product || await resolveProduct(name);
        const res = await api('POST', '/api/prices', { store_id: Number(storeId), product_id: product.id, price });
        safeStore.set('lastStore', storeId); r.storeId = storeId; r.product = null;
        toast(res.message);
        await refreshUser(); state.basket = null; state.compare = null; state.deals = null; state.feed = null; render(); loadReport();
      } catch (e) { r.error = e.message; render(); }
    },
    correct: async (f) => {
      const price = parsePrice(new FormData(f).get('price'));
      if (!(price > 0)) { toast('Enter the current price, like 3.49.'); return; }
      try {
        const res = await api('POST', '/api/prices', { store_id: Number(f.dataset.store), product_id: Number(f.dataset.product), price, corrects_price_id: Number(f.dataset.price) });
        toast(res.message); state.editingPrice = null;
        await refreshUser(); state.basket = null; state.compare = null; state.deals = null; state.feed = null; render(); loadReport();
      } catch (e) { toast(e.message); }
    },
    alert: async (f) => {
      const target = parsePrice(new FormData(f).get('target'));
      if (!(target > 0)) { setErr('#sheet-err', 'Enter the price you want, like 2.99.'); return; }
      try { await api('POST', '/api/alerts', { product_id: Number(f.dataset.pid), target_price: target }); closeSheet(); state.alerts = null; toast('Alert set. See it under Me.'); }
      catch (e) { setErr('#sheet-err', e.message); }
    },
    review: async (f) => {
      const fd = new FormData(f);
      try {
        const r = await api('POST', `/api/stores/${f.dataset.id}/reviews`, { rating: Number(fd.get('rating')), comment: fd.get('comment') });
        toast(r.message); await refreshUser(); state.nearStores = null; await openStore(f.dataset.id); loadTab();
      } catch (e) { setErr('#review-err', e.message); }
    },
    location: async (f) => {
      const fd = new FormData(f);
      try {
        state.user = (await api('PATCH', '/api/me', { zip: String(fd.get('zip')).trim(), radius_miles: Number(fd.get('radius')) })).user;
        closeSheet(); invalidateData(); render(); loadTab(); toast('Search area updated.');
      } catch (e) { setErr('#loc-err', e.message); }
    },
    'add-store': async (f) => {
      const fd = Object.fromEntries(new FormData(f));
      try {
        const s = await api('POST', '/api/stores', fd);
        state.report.storeId = String(s.id); safeStore.set('lastStore', String(s.id));
        closeSheet(); state.nearStores = null; toast('Store added.'); loadTab();
      } catch (e) { setErr('#store-err', e.message); }
    },
    'receipt-text': async (f) => {
      const text = String(new FormData(f).get('text') || '').trim();
      if (!text) { receiptStart('Paste the receipt text or choose a photo.'); return; }
      await readReceipt({ text });
    },
    'receipt-save': async (f) => {
      const fd = new FormData(f);
      const items = [];
      for (let i = 0; i < Number(f.dataset.n); i += 1) {
        if (fd.get(`inc-${i}`) !== 'on') continue;
        const price = parsePrice(fd.get(`price-${i}`));
        if (!(price > 0)) { setErr('#receipt-err', 'Check the prices you ticked.'); return; }
        items.push({ product_id: Number(fd.get(`pid-${i}`)), price, qty: Number(fd.get(`qty-${i}`)) || 1 });
      }
      if (!items.length) { setErr('#receipt-err', 'Tick at least one line to save.'); return; }
      try {
        const r = await api('POST', '/api/receipts/submit', { store_id: Number(fd.get('store')), items });
        safeStore.set('lastStore', String(fd.get('store')));
        closeSheet(); await refreshUser(); invalidateData(); render(); loadTab(); toast(r.message);
      } catch (e) { setErr('#receipt-err', e.message); }
    },
    prefs: async (f) => {
      const fd = new FormData(f);
      const num = (k) => { const v = String(fd.get(k) || '').trim(); return v === '' ? null : parsePrice(v); };
      const hh = String(fd.get('household') || '').trim();
      const body = {
        preferred_chains: fd.getAll('chain'), strategy: fd.get('strategy'), budget: num('budget'),
        household: hh === '' ? null : Number(hh), min_savings: num('min_savings') ?? 0, cost_per_mile: num('cost_per_mile') ?? 0,
        verified_only: fd.get('verified_only') === 'on', reminders_on: fd.get('reminders_on') === 'on',
        fav_brands: fd.get('fav_brands'), avoid_terms: fd.get('avoid_terms'), diet_note: fd.get('diet_note'),
      };
      if (Object.values(body).some((v) => typeof v === 'number' && Number.isNaN(v))) { setErr('#pref-err', 'Check the numbers you entered.'); return; }
      try {
        state.user = (await api('PATCH', '/api/prefs', body)).user;
        state.strategy = state.user.prefs.strategy;
        state.compare = null; state.deals = null; state.reminders = null;
        toast('Preferences saved.'); render(); loadMe();
      } catch (e) { setErr('#pref-err', e.message); }
    },
    itemprefs: async (f) => {
      const fd = new FormData(f);
      const int = (k) => { const v = String(fd.get(k) || '').trim(); return v === '' ? null : Number(v); };
      try {
        await api('PATCH', `/api/products/${f.dataset.pid}/prefs`, { interval_days: int('interval_days'), usual_qty: int('usual_qty'), reminders: fd.get('reminders') === 'on' });
        closeSheet(); state.reminders = null; toast('Saved.'); loadTab();
      } catch (e) { setErr('#usage-err', e.message); }
    },
    feedback: async (f) => {
      const fd = Object.fromEntries(new FormData(f));
      try { const r = await api('POST', '/api/feedback', fd); f.reset(); setErr('#fb-err', ''); toast(r.message); }
      catch (e) { setErr('#fb-err', e.message); }
    },
  };

  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-action]');
    if (!el) return;
    const fn = actions[el.dataset.action];
    if (fn) { ev.preventDefault(); fn(el); }
  });
  document.addEventListener('submit', (ev) => {
    const f = ev.target.closest('form[data-form]');
    if (!f) return;
    ev.preventDefault();
    const btn = $('button[type="submit"]', f);
    if (btn) btn.disabled = true;
    Promise.resolve(forms[f.dataset.form](f)).finally(() => { if (btn && document.contains(btn)) btn.disabled = false; });
  });
  document.addEventListener('change', async (ev) => {
    if (ev.target.matches('#receipt-file')) {
      const file = ev.target.files[0];
      if (!file) return;
      setSheetBody(loading('Preparing your photo…'));
      try { await readReceipt({ image: await fileToJpeg(file), mime: 'image/jpeg' }); } catch (e) { receiptStart(e.message); }
      return;
    }
    if (ev.target.matches('[data-program]')) {
      const boxes = [...document.querySelectorAll('[data-program]')];
      api('PATCH', '/api/prefs', { programs: boxes.filter((b) => b.checked).map((b) => b.dataset.program) })
        .then((r) => { state.user = r.user; state.compare = null; state.deals = null; toast('Saved.'); })
        .catch((e) => { ev.target.checked = !ev.target.checked; toast(e.message); });
      return;
    }
    if (ev.target.matches('select[name="store"]')) {
      if (ev.target.value === '__new') { ev.target.value = state.report.storeId || ''; openAddStore(); }
      else { state.report.storeId = ev.target.value; }
    }
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeSheet(); });
  new MutationObserver(attachTypeahead).observe($('#app'), { childList: true, subtree: true });

  window.addEventListener('beforeinstallprompt', (ev) => { ev.preventDefault(); state.installEvent = ev; if (state.user && state.tab === 'me') render(); });
  window.addEventListener('offline', () => toast("You're offline. Prices will refresh when you reconnect."));

  // ---------- start ----------
  (async function start() {
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    try { await refreshUser(); } catch { state.user = null; }
    state.ready = true;
    if (!state.user) state.authMode = 'signup';
    render();
    if (state.user) loadTab();
  })();
})();
