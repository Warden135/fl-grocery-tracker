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
  function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
  }

  // ---------- state ----------
  const state = {
    user: null, ready: false, tab: 'list', authMode: 'signup', authError: '',
    basket: null, plan: 1,
    feed: null, nearStores: null, report: { storeId: safeStore.get('lastStore') || '', product: null, error: '' },
    editingPrice: null, printTag: true, authDraft: {},
    rewards: null, alerts: null, installEvent: null,
  };

  const TABS = [
    ['list', 'List', 'My list'], ['deals', 'Deals', 'Where to shop'], ['report', 'Report', 'Report a price'],
    ['rewards', 'Rewards', 'Rewards'], ['me', 'Me', 'Me'],
  ];

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
      <h1>Shelf Scout</h1>
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
  function listView() {
    const b = state.basket;
    let body;
    if (!b) body = loading('Loading your list…');
    else if (!b.items.length) {
      body = `<div class="empty"><h2>Your list is empty</h2><p>Add what you need this week and we'll show where it costs least near ${esc(state.user.zip)}.</p></div>`;
    } else {
      body = `<ul class="rows">${b.items.map((it) => listRow(it, b)).join('')}</ul>`;
    }
    return header('My list', locationChip()) + `
      <form class="add" data-form="add-item" autocomplete="off">
        <label class="sr" for="add-input">Add an item</label>
        <div class="add-wrap"><input id="add-input" name="q" placeholder="Add an item, like eggs or milk" enterkeyhint="done" data-typeahead="add"><button class="btn" type="submit">Add</button></div>
        <ul id="sugg-add" class="sugg" hidden></ul>
      </form>` + body;
  }

  function listRow(it, b) {
    const opt = b.per_item[String(it.product_id)];
    const best = opt && opt.best;
    const right = it.checked ? '' : best ? mini(best.price) : '<span class="mini none">No price nearby yet</span>';
    const sub = it.checked ? 'Bought'
      : best ? `${esc(best.store)}, ${best.distance} mi, ${ageText(best.age_days)}`
        : 'Tap to see prices or report one';
    return `<li class="row ${it.checked ? 'done' : ''}">
      <button class="check" role="checkbox" aria-checked="${it.checked ? 'true' : 'false'}" aria-label="Bought ${esc(it.name)}" data-action="toggle" data-id="${it.item_id}" data-checked="${it.checked ? 0 : 1}"></button>
      <button class="row-main" data-action="open-item" data-pid="${it.product_id}" data-name="${esc(it.name)}">
        <span class="name">${esc(it.name)}${it.qty > 1 ? ` <span class="qty">× ${it.qty}</span>` : ''}</span>
        <span class="sub">${sub}</span>
      </button>${right}</li>`;
  }

  // ---------- deals ----------
  function dealsView() {
    const b = state.basket;
    const head = header('Where to shop', locationChip());
    if (!b) return head + loading('Finding the lowest prices…');
    const active = b.items.filter((i) => !i.checked);
    if (!active.length) {
      return head + `<div class="empty"><h2>Nothing to price yet</h2><p>Add items to your list and we'll compare stores within ${b.radius} miles.</p><button class="btn" data-action="tab" data-tab="list">Go to my list</button></div>`;
    }
    if (!b.stores.length) {
      return head + `<div class="empty"><h2>No prices within ${b.radius} miles</h2><p>Try a larger distance, or be the first to report a price for these items.</p><div class="actions"><button class="btn" data-action="open-location">Change distance</button><button class="btn quiet" data-action="tab" data-tab="report">Report a price</button></div></div>`;
    }
    const best = b.best;
    const sample = b.stores.some((s) => s.sample);
    const plan = b.plans.find((p) => p.stops === state.plan) || b.plans[0];
    const hero = tag({
      label: best.complete ? 'Cheapest for your whole list' : 'Closest match for your list',
      store: best.store, price: best.total, print: state.printTag,
      meta: [`${best.distance} miles away`, plural(b.priced_count, 'item', 'items'), best.missing ? `missing ${plural(best.missing, 'item', 'items')}` : ''],
    });
    return head + hero + (sample ? '<p class="note">These are sample prices. Real prices appear as shoppers report them.</p>' : '') + planView(b, plan) + `
      <h2>All stores within ${b.radius} miles</h2>` + b.stores.map(storeDetails).join('');
  }

  function planView(b, plan) {
    if (!plan) return '';
    const total = b.priced_count;
    const anyGap = b.plans.some((p) => p.missing > 0);
    const seg = b.plans.map((p) => `<button aria-pressed="${p.stops === plan.stops}" data-action="plan" data-stops="${p.stops}"><span>${p.stops === 1 ? '1 store' : p.stops + ' stops'}</span><b>${money(p.total)}</b>${anyGap ? `<span>${total - p.missing} of ${total} items</span>` : ''}</button>`).join('');
    const one = b.plans[0];
    let line;
    if (plan.stops === 1) line = plan.missing ? `This store has ${total - plan.missing} of your ${total} items.` : 'Everything from one store.';
    else if (plan.savings > 0) line = `<span class="good">Save ${money(plan.savings)}</span> compared with one store.`;
    else if (plan.savings === null) line = `Adds ${plural(one.missing - plan.missing, 'more item', 'more items')} compared with one store.`;
    else line = 'No savings over one store, so stick with one.';
    const covered = new Set(plan.stores.flatMap((s) => s.items.map((i) => i.product_id)));
    const gaps = b.items.filter((i) => !i.checked && !covered.has(i.product_id)).map((i) => i.name);
    return `<div class="seg" role="group" aria-label="Shopping plan">${seg}</div>
      <p class="plan-head">${line}</p>` + plan.stores.map((s) => `
      <section class="plan-store"><h3>${esc(s.store)} <span class="sub">${s.distance} mi</span></h3>
        <ul class="lines">${s.items.map((i) => `<li><span>${esc(i.name)}${i.qty > 1 ? ` × ${i.qty}` : ''}</span><b>${money(i.price * i.qty)}</b></li>`).join('')}</ul></section>`).join('') +
      (gaps.length ? `<p class="note">No price nearby yet for ${esc(gaps.join(', '))}. Report one from the Report tab.</p>` : '');
  }

  function storeDetails(s) {
    return `<details class="store"><summary><span class="grow"><span class="name">${esc(s.store)}</span>${sampleChip(s.sample)}<br><span class="sub">${s.distance} miles</span>${s.missing ? ` <span class="miss">missing ${s.missing}</span>` : ''}</span><span class="total">${money(s.total)}</span></summary>
      <ul class="lines">${s.items.map((i) => `<li><span>${esc(i.name)}${i.qty > 1 ? ` × ${i.qty}` : ''}</span><b>${i.price == null ? '<span class="miss">no price</span>' : money(i.price * i.qty)}</b></li>`).join('')}</ul></details>`;
  }

  // ---------- report ----------
  function reportView() {
    const r = state.report;
    const stores = state.nearStores;
    const opts = (stores || []).map((s) => `<option value="${s.id}" ${String(s.id) === String(r.storeId) ? 'selected' : ''}>${esc(s.name)} (${s.distance} mi)</option>`).join('');
    const form = `
      <p class="lead">Saw a price in the store? Add it and earn 5 points.</p>
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
    return header('Report a price') + form + `<h2>Help confirm prices</h2><p class="lead">Is the price still right? Confirming earns 1 point, and the shopper who reported it earns 2.</p>` + feed;
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
    if (!d) return header('Rewards') + loading();
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
    return header('Rewards') + `
      <div class="balance"><b>${u.points}</b><span class="sub">points to spend</span></div>
      <p class="pad"><strong>${esc(lv.name)}</strong> <span class="sub">level, ${u.earned} points earned in total</span></p>${progress}
      <h2>Perks</h2><ul class="rows">${perks}</ul>
      <h2>How to earn points</h2><ul class="rows">${rules}</ul>
      <p class="lead tight">Prices that look far off the usual need two confirmations before they count and before they earn points.</p>
      <h2>Top price scouts this month</h2><ul class="rows">${board}</ul>
      <h2>Your activity</h2><ul class="rows">${ledger}</ul>`;
  }

  // ---------- me ----------
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
        : `<p class="lead">No stores within ${u.radius_miles} miles yet. Add one from the Report tab.</p>`;
    const ios = /iphone|ipad/i.test(navigator.userAgent) && !navigator.standalone;
    const install = state.installEvent
      ? '<div class="pad"><button class="btn quiet" data-action="install">Install Shelf Scout on this device</button></div>'
      : ios ? '<p class="lead">To install on iPhone, tap Share, then Add to Home Screen.</p>' : '';
    return header('Me') + `
      <div class="row"><div class="grow"><div class="name">${esc(u.name)}</div><div class="sub">${esc(u.email)}</div></div><button class="chip" data-action="open-location">${esc(u.city || u.zip)} ${esc(u.zip)}</button></div>
      ${install}
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

  function navView() {
    return `<nav class="nav" aria-label="Main"><ul>${TABS.map(([id, label]) => `<li><button data-action="tab" data-tab="${id}" ${state.tab === id ? 'aria-current="page"' : ''}>${icon(id === 'deals' ? 'deals' : id)}<span>${label}</span></button></li>`).join('')}</ul></nav>`;
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
      const view = { list: listView, deals: dealsView, report: reportView, rewards: rewardsView, me: meView }[state.tab];
      const title = TABS.find((t) => t[0] === state.tab)[2];
      document.title = `${title} - Shelf Scout`;
      app.innerHTML = `<div class="shell"><main id="main" tabindex="-1">${view()}</main>${navView()}</div>`;
    }
    if ($('.tag.print')) state.printTag = false;
    window.scrollTo(0, y);
    if (state.focus) { const el = $(state.focus); if (el) el.focus(); state.focus = null; }
  }

  // ---------- data loading ----------
  async function loadBasket() {
    try { state.basket = await api('GET', '/api/basket'); } catch (e) { toast(e.message); }
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
    try {
      const [a, s] = await Promise.all([api('GET', '/api/alerts'), api('GET', '/api/stores/nearby')]);
      state.alerts = a;
      state.nearStores = s.stores;
    } catch (e) { toast(e.message); }
    render();
  }
  async function refreshUser() {
    try { state.user = (await api('GET', '/api/me')).user; } catch { /* ignore */ }
  }
  function loadTab() {
    ({ list: loadBasket, deals: loadBasket, report: loadReport, rewards: loadRewards, me: loadMe })[state.tab]();
  }
  function goTab(tab) {
    state.tab = tab;
    state.printTag = true;
    closeSheet();
    window.scrollTo(0, 0);
    render();
    loadTab();
    $('#main') && $('#main').focus({ preventScroll: true });
  }
  function invalidateData() {
    state.basket = null; state.feed = null; state.nearStores = null; state.rewards = null; state.alerts = null;
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
      const [pr, hist] = await Promise.all([api('GET', `/api/products/${pid}/prices`), api('GET', `/api/history/${pid}?days=60`)]);
      const item = state.basket && state.basket.items.find((i) => i.product_id === pid);
      const pts = hist.points;
      const trend = pts.length >= 3
        ? `<h3>Lowest price near you</h3>${spark(pts)}<p class="sub">${money(Math.min(...pts.map((p) => p.price)))} to ${money(Math.max(...pts.map((p) => p.price)))} over the last 60 days</p>` : '';
      const opts = pr.options.length
        ? `<h3>Prices within ${pr.radius} miles</h3><ul class="rows">${pr.options.slice(0, 8).map((o) => `<li class="row"><div class="grow"><div class="name">${esc(o.store)}${sampleChip(o.store_source === 'demo')}</div><div class="sub">${o.distance} mi, ${ageText(o.age_days)}${o.verified ? `, confirmed ${o.verified}×` : ''}</div></div>${mini(o.price)}</li>`).join('')}</ul>`
        : `<p class="lead">No prices within ${pr.radius} miles yet. Report one to help everyone nearby.</p>`;
      setSheetBody(`
        ${item ? `<div class="row"><span class="grow name">Quantity</span><div class="qty-ctl"><button data-action="qty" data-id="${item.item_id}" data-qty="${item.qty - 1}" aria-label="One fewer" ${item.qty <= 1 ? 'disabled' : ''}>−</button><output aria-live="polite">${item.qty}</output><button data-action="qty" data-id="${item.item_id}" data-qty="${item.qty + 1}" aria-label="One more">+</button></div></div>` : ''}
        ${trend}${opts}
        <h3>Price alert</h3>
        <form data-form="alert" data-pid="${pid}" class="actions" novalidate>
          <span class="money grow"><b>$</b><input name="target" inputmode="decimal" placeholder="Alert me at or below" aria-label="Alert me at or below"></span>
          <button class="btn" type="submit">Set alert</button></form>
        <div id="sheet-err"></div>
        <div class="actions gap"><button class="btn quiet" data-action="report-for" data-pid="${pid}" data-name="${esc(name)}">Report a price</button>
        ${item ? `<button class="link danger" data-action="del-item" data-id="${item.item_id}">Remove from list</button>` : ''}</div>`);
    } catch (e) { setSheetBody(errBox(e.message)); }
  }

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
    tab: (el) => goTab(el.dataset.tab),
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
    'del-item': async (el) => {
      try { await api('DELETE', `/api/list/${el.dataset.id}`); closeSheet(); toast('Removed from your list.'); await loadBasket(); } catch (e) { toast(e.message); }
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
      state.user = null; invalidateData(); state.tab = 'list'; state.authMode = 'login'; state.printTag = true; render();
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
        await refreshUser(); state.basket = null; state.feed = null; render(); loadReport();
      } catch (e) { r.error = e.message; render(); }
    },
    correct: async (f) => {
      const price = parsePrice(new FormData(f).get('price'));
      if (!(price > 0)) { toast('Enter the current price, like 3.49.'); return; }
      try {
        const res = await api('POST', '/api/prices', { store_id: Number(f.dataset.store), product_id: Number(f.dataset.product), price, corrects_price_id: Number(f.dataset.price) });
        toast(res.message); state.editingPrice = null;
        await refreshUser(); state.basket = null; state.feed = null; render(); loadReport();
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
  document.addEventListener('change', (ev) => {
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
    try { state.user = (await api('GET', '/api/me')).user; } catch { state.user = null; }
    state.ready = true;
    if (!state.user) state.authMode = 'signup';
    render();
    if (state.user) loadTab();
  })();
})();
