'use strict';
// Exercise the actual browser script without a browser, dependencies or network.
// This verifies request/interaction logic; visual and browser behavior use CUA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'fastnews/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'fastnews/static/index.html'), 'utf8');

function writingOutline(id='outline-v1') {
 return {version_id:id,stage:'outline',decision_revision:1,created_at:'2026-09-13T10:00:00Z',stale_selection:false,
  checks:{errors:[],status:'ready_to_confirm'},source_evidence:[],content:{text:'合成框架，等待人工确认',suggestions:[],changes:[],
   dispositions:[],claims:[],supplement_queries:['公开产业问题']}};
}
function writingEdition(id='ed-one') {
 const value=edition();value.edition.edition_id=id;value.decision.decision_revision=1;
 value.writing={versions:[writingOutline()],runs:[]};return value;
}

class Element {
  constructor(id) {
    this.id = id; this.value = ''; this.disabled = false; this.checked = false;
    this.open = false; this.dataset = {}; this._text = ''; this._html = '';
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
    };
  }
  set textContent(value) { this._text = String(value); this._html = String(value); }
  get textContent() { return this._text; }
  set innerHTML(value) { this._html = String(value); this._text = String(value).replace(/<[^>]+>/g, ''); }
  get innerHTML() { return this._html; }
  scrollIntoView() {}
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll() { return []; }
  querySelector() { return new Element('mock-child'); }
}

function news(date, count = 1) {
  return {
    date, is_fixture: false, counts: {items: count, sources_with_items: count ? 1 : 0, undated: 0, historical_gaps: 0},
    items: Array.from({length: count}, (_, index) => ({
      article_id: 'article-' + index, title: date + ' 合成测试标题 ' + index,
      url: 'https://example.invalid/news-' + index, source_id: 'miit', source_name: '测试来源',
      display_date: date, date_basis: 'report_published_at', report_published_at: date,
      first_published_at: null, retrieved_at: '2026-09-15T05:00:00Z', source_revision_at: null,
      lead: '测试摘要', warnings: ['仅列表元数据'], source_chain: [], pending: true,
    })),
    undated_items: [], notices: [], coverage: {complete: false},
  };
}

function edition() {
  return {
    edition: {edition_id: 'ed-one', product_code: '513130', product_name: '测试产品', edition_date: '2026-09-13', news_cutoff: '2026-09-12T23:59:59+08:00', market_cutoffs: {}},
    route: {groups: {within_cutoff: [{event_id: 'evt-one', title: '人工候选', phase: 'unknown', cutoff_state: 'within_cutoff', metadata_versions: [{metadata: {report_published_at: '2026-09-12'}}], source_chain: [], gaps: []}]}},
    facts: [{fact_id: 'fact-one', event_id: 'evt-one', statement: '合成事实原句', evidence_state: 'pending', cutoff_state: 'eligible', kind: 'reported', evidence: []}],
    evidence: [], assessments: [], selection_notices: [], decision: {selected_event_id: null, selected_fact_ids: [], decision_revision: 0},
  };
}

async function harness(options = {}) {
  const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element(match[1])]));
  elements.get('group').value = 'all';
  const listeners = new Map();
  const storage = new Map();
  const calls = [];
  const state = {
    csrf_token: 'test-local-token', workspace_id: 'vm-only', is_fixture: false,
    default_day: '2026-09-15', default_cutoff: '2026-09-14T23:59:59+08:00',
    products: options.products || [{code: '513130', name: '测试产品'}], editions: [], active_job: null,
    sources: {live_connected: 1, sources: [{source_id: 'miit', name: '测试来源', expected_morning_scan: true, capability_status: 'ready_metadata', last_success_at: '2026-09-15T03:00:00Z'}]},
    reader_profile:{profile_id:'default',name:'默认方案',settings:{host:'codex',web_search:true}},
    reader_profiles:[{profile_id:'default',name:'默认方案'},{profile_id:'reader_test',name:'我的方案'}],
    reader_sources:[{source_id:'miit',name:'测试来源',kind:'builtin',url:'https://example.org',enabled:true,sections:['sector'],categories:['policy']}],
    source_categories:{morning:'综合晨报',policy:'政策与宏观',technology:'科技'},
    host_status:{codex:{installed:true,note:'测试状态'}},
  };
  let handler = options.handler;
  const clone = value => JSON.parse(JSON.stringify(value));
  const context = vm.createContext({
    document: {
      querySelectorAll() { return []; },
      getElementById(id) { assert.ok(elements.has(id), 'Unknown DOM id: ' + id); return elements.get(id); },
      addEventListener(name, callback) { listeners.set(name,[...(listeners.get(name)||[]),callback]); },
    },
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    fetch: async (url, request) => {
      const call = {url, method: request.method, headers: request.headers, body: request.body === undefined ? undefined : JSON.parse(request.body)};
      calls.push(call);
      let result = handler ? await handler(call) : undefined;
      if (result === undefined) {
        if (url === '/api/state' || url.startsWith('/api/state?')) result = state;
        else if (url.startsWith('/api/daily?')) result = news(new URL('http://localhost' + url).searchParams.get('date'));
        else if (url.startsWith('/api/edition?')) result = edition();
        else throw Error('Unexpected request: ' + url);
      }
      const status = result.httpStatus || 200;
      return {ok: status >= 200 && status < 300, status, json: async () => clone(result.payload === undefined ? result : result.payload)};
    },
    setTimeout: (callback, delay) => setTimeout(callback, delay === 900 ? 0 : delay),
    clearTimeout, AbortController, URL, Date, console, TypeError, crypto:require('node:crypto').webcrypto,
  });
  await vm.runInContext(script, context, {filename: 'fastnews/static/app.js'});
  if(options.reader)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/reader.js'),'utf8'),context,{filename:'reader.js'});
  if(options.workflow)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/workflow.js'),'utf8'),context,{filename:'workflow.js'});
  if(options.exploration)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/exploration.js'),'utf8'),context,{filename:'exploration.js'});
  if(options.writing)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/writing.js'),'utf8'),context,{filename:'writing.js'});
  if(options.workspace)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/workspace.js'),'utf8'),context,{filename:'workspace.js'});
  if(options.visuals)vm.runInContext(fs.readFileSync(path.join(root,'fastnews/static/visuals.js'),'utf8'),context,{filename:'visuals.js'});
  return {
    el: id => elements.get(id), calls, state, storage,
    on: async (name, event) => {for(const fn of listeners.get(name)||[])await fn(event);},
    route: value => { handler = value; },
    read: expression => JSON.parse(vm.runInContext('JSON.stringify(' + expression + ')', context)),
    evaluate: expression => vm.runInContext(expression,context),
  };
}

test('opening without an Edition reads the current day and shows headlines', async () => {
  const app = await harness();
  assert.deepEqual(app.calls.map(call => call.url), ['/api/state', '/api/daily?date=2026-09-15']);
  assert.equal(app.el('news-day').value, '2026-09-15');
  assert.match(app.el('daily-items').innerHTML, /2026-09-15 合成测试标题/);
  assert.match(app.el('daily-count').textContent, /1 条/);
  assert.equal(app.read('editionId'), '');
  assert.equal(app.read('basket'), null);
});

test('the product picker includes appended registry products without creating or choosing an edition', async () => {
  const products = [{code:'513130',name:'测试产品'}, {code:'588710',name:'半导体设备测试'},
    {code:'588090',name:'科创50ETF华泰柏瑞'}];
  const app = await harness({products});
  assert.match(app.el('product').innerHTML, /588090/);
  assert.match(app.el('product').innerHTML, /科创50ETF华泰柏瑞/);
  app.el('product').value='588090'; app.el('product').onchange();
  assert.equal(app.read('editionId'), '');
  assert.equal(app.read('basket'), null);
  assert.ok(app.calls.every(call => call.method === 'GET'));
});

test('changing the date reads that day without creating an Edition', async () => {
  const app = await harness();
  app.el('news-day').value = '2026-09-14';
  await app.el('news-day').onchange();
  assert.equal(app.calls.at(-1).url, '/api/daily?date=2026-09-14');
  assert.match(app.el('daily-title').textContent, /2026-09-14/);
  assert.ok(app.calls.every(call => call.method === 'GET'));
  await app.el('today').onclick();
  assert.equal(app.calls.at(-1).url, '/api/daily?date=2026-09-15');
  await app.el('yesterday').onclick();
  assert.equal(app.calls.at(-1).url, '/api/daily?date=2026-09-14');
});

test('the scan sends the selected date, waits for completion and explains zero new articles', async () => {
  const app = await harness();
  let polls = 0;
  app.route(call => {
    if (call.url === '/api/scan') return {job_id: 'job-zero'};
    if (call.url === '/api/job?id=job-zero') {
      polls += 1;
      return {action: 'scan', date: '2026-09-14', status: polls === 1 ? 'running' : 'completed', progress: [], error: null,
        result: {articles_new: 0, sources: [{source_id: 'miit', status: 'partial', items_seen: 1}]}};
    }
  });
  app.el('news-day').value = '2026-09-14';
  await app.el('scan').onclick();
  const post = app.calls.find(call => call.url === '/api/scan');
  assert.deepEqual(post.body, {date: '2026-09-14', section:'morning'});
  assert.equal(post.headers['X-Fastnews-CSRF'], 'test-local-token');
  assert.equal(polls, 2);
  assert.match(app.el('job').innerHTML, /2026-09-14：已存 1 条有日期信息，本次新增 0 篇/);
  assert.match(app.el('job').innerHTML, /已保存过/);
  assert.match(app.el('daily-items').innerHTML, /2026-09-14 合成测试标题/);
  assert.equal(app.el('scan').disabled, false);
  assert.equal(app.read('busy'), false);
});

test('probe opens the source states and never says it fetched news', async () => {
  const app = await harness();
  app.route(call => {
    if (call.url === '/api/probe') return {job_id: 'probe-one'};
    if (call.url === '/api/job?id=probe-one') return {action: 'probe', status: 'completed', progress: [], error: null, result: {summary: {registered_sources: 26, live_connected: 4}}};
  });
  await app.el('probe').onclick();
  assert.equal(app.el('source-panel').open, true);
  assert.match(app.el('job').innerHTML, /4 个采集器可用/);
  assert.match(app.el('job').innerHTML, /只检查连接/);
  assert.match(app.el('message').textContent, /不会新增新闻/);
  assert.ok(!app.calls.some(call => call.url === '/api/scan'));
  assert.equal(app.el('probe').disabled, false);
});

test('network failures are caught by UI handlers and leave a visible recovery message', async () => {
  const app = await harness();
  app.route(call => {
    if (call.url === '/api/scan') throw new TypeError('Connection refused');
  });
  await assert.doesNotReject(() => app.el('scan').onclick());
  assert.match(app.el('message').textContent, /华泰柏瑞快讯看板/);
  assert.match(app.el('job').textContent, /暂时连不上/);
  assert.equal(app.el('job').classList.contains('error'), true);
  assert.equal(app.el('scan').disabled, false);
  assert.equal(app.read('busy'), false);
});

test('a failed server job exposes its error and does not leave action buttons locked', async () => {
  const app = await harness();
  app.route(call => {
    if (call.url === '/api/scan') return {job_id: 'failed-one'};
    if (call.url === '/api/job?id=failed-one') return {action: 'scan', date: '2026-09-15', status: 'failed', progress: [], error: '来源列表读取失败（测试）', result: null};
  });
  await assert.doesNotReject(() => app.el('scan').onclick());
  assert.match(app.el('job').textContent, /来源列表读取失败/);
  assert.match(app.el('message').textContent, /来源列表读取失败/);
  assert.equal(app.el('job').classList.contains('error'), true);
  assert.equal(app.el('scan').disabled, false);
});

test('daily API errors replace stale results and are caught instead of rejecting unhandled', async () => {
  const app = await harness();
  app.route(call => call.url.startsWith('/api/daily?') ? {httpStatus: 400, payload: {error: '不能选择未来日期'}} : undefined);
  app.el('news-day').value = '2026-09-16';
  await assert.doesNotReject(() => app.el('news-day').onchange());
  assert.equal(app.read('daily'), null);
  assert.match(app.el('daily-items').innerHTML, /不能选择未来日期/);
  assert.match(app.el('message').textContent, /不能选择未来日期/);
  assert.ok(!app.el('daily-items').innerHTML.includes('2026-09-15 合成测试标题'));
});

test('a slow previous-date response cannot overwrite the most recent selected day', async () => {
  const app = await harness();
  let finishOld;
  app.route(call => {
    if (call.url === '/api/daily?date=2026-09-13') return new Promise(resolve => { finishOld = resolve; });
  });
  app.el('news-day').value = '2026-09-13';
  const oldRequest = app.el('news-day').onchange();
  app.el('news-day').value = '2026-09-14';
  await app.el('news-day').onchange();
  finishOld(news('2026-09-13'));
  await oldRequest;
  assert.equal(app.read('daily.date'), '2026-09-14');
  assert.match(app.el('daily-items').innerHTML, /2026-09-14 合成测试标题/);
});

test('Edition loading and refresh preserve both news date and manual unsaved draft', async () => {
  const app = await harness();
  app.el('news-day').value = '2026-09-14';
  await app.el('news-day').onchange();
  app.el('editions').value = 'ed-one';
  await app.el('editions').onchange();
  assert.equal(app.el('news-day').value, '2026-09-14');
  assert.equal(app.el('day').value, '2026-09-13');
  app.on('change', {target: {dataset: {main: 'evt-one'}}});
  app.on('change', {target: {dataset: {fact: 'fact-one'}, checked: true}});
  app.el('mainline').value = '用户手写的取舍角度';
  app.el('mainline').oninput();
  const chosen = app.read('basket');
  await app.el('refresh').onclick();
  assert.equal(app.el('news-day').value, '2026-09-14');
  assert.deepEqual(app.read('basket'), chosen);
  assert.equal(app.read('dirty'), true);
  assert.equal(app.el('mainline').value, '用户手写的取舍角度');
  assert.equal(app.el('export').disabled, true);
  assert.ok(!app.calls.some(call => call.url === '/api/decision'));
});


test('changing product then researching creates the right edition and keeps the old draft', async () => {
  const app = await harness();
  app.el('editions').value = 'ed-one'; await app.el('editions').onchange();
  app.el('mainline').value='用户原有草稿'; app.el('mainline').oninput();
  const savedDraft = app.storage.get('fastnews:vm-only:ed-one:draft');
  app.el('product').value='517120'; app.el('product').onchange();
  const next=edition(); next.edition={...next.edition,edition_id:'ed-drug',product_code:'517120',product_name:'创新药测试'};
  app.route(call=>{
    if(call.url==='/api/edition')return {edition_id:'ed-drug'};
    if(call.url==='/api/edition?id=ed-drug')return next;
    if(call.url==='/api/research')return {job_id:'research-drug'};
    if(call.url==='/api/job?id=research-drug')return {action:'research',status:'completed',progress:[],error:null,result:{view:next.edition,match_counts:{core:1,background:0},fixed_source_discovery:{articles_new:1}}};
  });
  await app.el('research').onclick();
  assert.equal(app.calls.find(c=>c.url==='/api/edition').body.product_code,'517120');
  assert.deepEqual(app.calls.find(c=>c.url==='/api/research').body,{edition_id:'ed-drug',product_code:'517120',fetch_public_text:false,use_host:true});
  assert.equal(app.storage.get('fastnews:vm-only:ed-one:draft'),savedDraft);
  assert.equal(app.el('product').value,'517120');
  assert.match(app.el('job').innerHTML,/517120/);
  assert.ok(!app.calls.some(c=>c.url==='/api/decision'));
});

test('research uses matching saved edition without creating duplicate or adopting a topic', async () => {
  const app=await harness();app.el('editions').value='ed-one';await app.el('editions').onchange();
  app.route(call=>{
    if(call.url==='/api/research')return {job_id:'repeat'};
    if(call.url==='/api/job?id=repeat')return {action:'research',status:'completed',progress:[],error:null,result:{view:edition().edition,match_counts:{core:0,background:0},fixed_source_discovery:{articles_new:0}}};
  });
  await app.el('research').onclick();
  assert.ok(!app.calls.some(c=>c.url==='/api/edition'||c.url==='/api/decision'));
  assert.equal(app.read('basket.selected_event_id'),null);
  assert.equal(app.el('research').disabled,false);
});

test('switching to STAR 50 hides the prior product report and restores it without changing saved choices', async () => {
  const app=await harness({reader:true,workflow:true});
  app.el('editions').value='ed-one';await app.el('editions').onchange();
  app.evaluate(`detail.reading_report={status:'partial',host:'fixture',output:{items:[{rank:1,item_id:'old',title:'原产品合成线索',summary:'合成摘要',why:'合成原因',sources:[]}],coverage:[],gaps:[]}};renderReaderReports()`);
  const basket=app.read('basket');
  assert.match(app.el('product-report').innerHTML,/原产品合成线索/);
  app.el('product').value='588090';app.el('product').onchange();
  assert.doesNotMatch(app.el('product-report').innerHTML,/原产品合成线索/);
  assert.match(app.el('product-report').innerHTML,/已切换观察产品/);
  assert.deepEqual(app.read('basket'),basket);
  app.el('product').value='513130';app.el('product').onchange();
  assert.match(app.el('product-report').innerHTML,/原产品合成线索/);
  assert.ok(app.calls.every(call=>call.method==='GET'));
});

test('product view separates current core, background, old and post-cutoff items', async () => {
  const app=await harness();const e=edition();
  const item=(id,lane,window)=>({...e.route.groups.within_cutoff[0],event_id:id,title:id,product_match:{lane,report_window:window,explanation:'合成匹配依据',excerpts:[]}});
  e.route.groups.within_cutoff=[item('专项线索','core','in_window'),item('宏观背景','background','in_window'),item('历史旧闻','core','older'),item('截点以外','core','after_cutoff')];
  app.route(c=>c.url.startsWith('/api/edition?')?e:undefined);
  app.el('editions').value='ed-one';await app.el('editions').onchange();
  assert.match(app.el('events').innerHTML,/专项线索/);
  assert.ok(!/宏观背景|历史旧闻|截点以外/.test(app.el('events').innerHTML));
  app.el('relevance').value='background';app.el('relevance').onchange();
  assert.match(app.el('events').innerHTML,/宏观背景/);assert.ok(!app.el('events').innerHTML.includes('专项线索'));
  app.el('relevance').value='all';app.el('relevance').onchange();
  assert.match(app.el('events').innerHTML,/历史旧闻/);assert.match(app.el('events').innerHTML,/截点以外/);
  assert.equal(app.read('basket.selected_event_id'),null);
});

test('morning briefings are prominent and retain missing-source explanations', async () => {
  const app=await harness();app.state.morning_briefings=[{source_id:'cls_morning',name:'财联社早报',adapter_available:true,url:'https://www.cls.cn/subject/1151',note:'元数据'}, {source_id:'yicai_overnight',name:'第一财经昨夜今晨',adapter_available:false,url:'https://www.yicai.com/',note:'站点限制自动读取，需授权供稿或手工材料'}];
  app.route(c=>{if(c.url.startsWith('/api/daily?')){const d=news('2026-09-15');d.items.push({...d.items[0],source_id:'cls_morning',source_name:'财联社早报',title:'【早报】合成标题'});return d;}});
  await app.el('refresh').onclick();
  assert.match(app.el('daily-briefings').innerHTML,/【早报】合成标题/);
  assert.ok(!app.el('daily-items').innerHTML.includes('【早报】合成标题'));
  assert.match(app.el('briefing-sources').innerHTML,/第一财经昨夜今晨/);
  assert.match(app.el('briefing-sources').innerHTML,/需授权供稿/);
});

test('one-click cutoff uses the browsed date and the server clock for today', async () => {
  const app=await harness();app.state.server_now='2026-09-15T14:05:00+08:00';
  app.el('news-day').value='2026-09-14';await app.el('cutoff-news-day').onclick();
  assert.equal(app.el('cutoff').value,'2026-09-14T23:59:59+08:00');
  app.el('news-day').value='2026-09-15';await app.el('cutoff-news-day').onclick();
  assert.equal(app.el('cutoff').value,'2026-09-15T14:05:00+08:00');
  assert.equal(app.el('day').value,'2026-09-15');
  assert.match(app.read("displayTime('2026-09-14T23:12:44Z')"),/2026-09-15 07:12:44 北京时间/);
  assert.ok(!app.calls.some(c=>c.url==='/api/edition'));
});


test('a product-specific morning excerpt leads the card and preserves its source headline', async () => {
  const app=await harness();app.state.morning_briefings=[{source_id:'cls_hk_morning',name:'港股早报',adapter_available:true,url:'https://www.cls.cn/subject/8705',note:'元数据'}];
  await app.el('refresh').onclick();
  const e=edition();e.route.groups.within_cutoff[0]={...e.route.groups.within_cutoff[0],title:'原晨报标题：科技与政策',source_chain:[{source_id:'cls_hk_morning',source_url:'https://www.cls.cn/detail/123'}],product_match:{lane:'core',report_window:'in_window',explanation:'观察变量：加息',excerpts:[{text:'合成利率摘要：央行加息',url:'https://www.cls.cn/detail/123'}]}};
  app.route(c=>c.url.startsWith('/api/edition?')?e:undefined);
  app.el('editions').value='ed-one';await app.el('editions').onchange();
  assert.match(app.el('events').innerHTML,/data-view="evt-one">合成利率摘要：央行加息<\/button>/);
  assert.match(app.el('events').innerHTML,/所在晨报：原晨报标题：科技与政策/);
  assert.match(app.el('events').innerHTML,/https:\/\/www.cls.cn\/detail\/123/);
});


test('AI digest sends chosen day and shows a saved report without selecting any topic',async()=>{
  const app=await harness({reader:true});
  const report={report_id:'report-one',host:'codex',status:'partial',output:{items:[{title:'测试摘要',summary:'合成研究线索',category:'policy',sources:[]}],coverage:[],gaps:[]},usage:{}};
  app.route(call=>{
    if(call.url==='/api/digest')return {job_id:'digest-one'};
    if(call.url==='/api/job?id=digest-one')return {action:'digest',status:'completed',progress:[],result:report};
    if(call.url.startsWith('/api/daily?'))return {...news('2026-09-14'),reading_report:report};
  });
  app.el('news-day').value='2026-09-14';
  await app.el('digest').onclick();
  assert.deepEqual(app.calls.find(c=>c.url==='/api/digest').body,{date:'2026-09-14',section:'morning'});
  assert.match(app.el('morning-report').innerHTML,/合成研究线索/);
  assert.equal(app.read('basket'),null);
  assert.ok(!app.calls.some(c=>c.url.includes('decision')||c.url.includes('reading-adopt')));
});

test('personal profile is carried on reads and writes and preserves the manual basket',async()=>{
  const app=await harness({reader:true});
  await app.evaluate("loadEdition('ed-one')");
  app.evaluate("basket.selected_event_id='evt-one';persistDraft()");
  app.el('reader-profile').value='reader_test';
  await app.el('reader-profile').onchange();
  assert.ok(app.calls.some(c=>c.url==='/api/state?profile=reader_test'));
  assert.ok(app.calls.some(c=>c.url.includes('edition?id=ed-one&profile=reader_test')));
  assert.equal(app.read('basket.selected_event_id'),'evt-one');
  app.route(call=>call.url==='/api/reader-profile'?{profile_id:'reader_test'}:undefined);
  await app.evaluate("api('reader-profile',{profile_id:'reader_test',name:'我的方案'})");
  assert.equal(app.calls.at(-1).body.reader_profile,'reader_test');
});

test('model text is escaped and product suggestions only expose explicit adoption buttons',async()=>{
  const app=await harness({reader:true});
  const report={report_id:'r',host:'codex',status:'partial',output:{items:[{item_id:'one',rank:1,
    title:'<script>attack()</script>',summary:'<img src=x onerror=attack()>',why:'测试',category:'technology',
    sources:[{url:'javascript:attack()',title:'不安全链接'}],date_state:'pending_time'}]},usage:{}};
  const markup=app.evaluate('reportHTML('+JSON.stringify(report)+',"product")');
  assert.ok(!markup.includes('<script>'));
  assert.ok(!markup.includes('href="javascript:'));
  assert.ok(markup.includes('&lt;script&gt;'));
  assert.ok(markup.includes('加入人工候选池'));
  assert.ok(markup.includes('发布日期待核实'));
  assert.ok(!markup.includes('checked'));
  assert.equal(app.calls.filter(c=>c.method==='POST').length,0);
});

test('query gaps and older background dates stay visible without selecting a clue',async()=>{
 const app=await harness({reader:true});
 const r={report_id:'r',host:'codex',status:'partial',output:{items:[{item_id:'one',rank:1,
  title:'背景｜合成设备数据',summary:'合成季度背景',why:'制造投资观察',time_role:'background',date_state:'background_before_window',
  sources:[{url:'https://example.org/stat',title:'合成来源',excerpt:'合成片段',published_at:'2026-09-03',first_published_at:'2026-09-02',data_period_start:'2026-04-01',data_period_end:'2026-06-30'}]}],
  search_audit:{observed_query_count:3,query_budget:24,directions:[{name:'生产数据',status:'queried'},{name:'<script>投资数据</script>',status:'no_query_observed'}]}}};
 const markup=app.evaluate('reportHTML('+JSON.stringify(r)+',"product")');
 assert.match(markup,/实际查询 3 次/);assert.match(markup,/产业观察方向 1\/2/);
 assert.match(markup,/非本窗口新增消息/);assert.match(markup,/2026-04-01/);assert.match(markup,/2026-06-30/);
 assert.match(markup,/本轮未观察到查询/);assert.ok(!markup.includes('<script>'));
 assert.equal(app.calls.filter(c=>c.method==='POST').length,0);
});

test('workspace switching keeps unsaved selections and does not make API calls', async()=>{
 const app=await harness({workflow:true,workspace:true});
 app.el('editions').value='ed-one';await app.el('editions').onchange();
 app.el('mainline').value='尚未保存的人工角度';app.el('mainline').oninput();
 const before=app.read('basket'),calls=app.calls.length;
 for(const name of ['product','materials','morning','materials'])app.evaluate(`showWorkspace('${name}',false)`);
 assert.equal(app.el('materials-workspace').hidden,false);
 assert.equal(app.el('product-workspace').hidden,true);
 assert.deepEqual(app.read('basket'),before);assert.equal(app.el('mainline').value,'尚未保存的人工角度');
 assert.equal(app.calls.length,calls);assert.equal(app.read('dirty'),true);
});

test('until now uses server clock without research or automatic selection',async()=>{
 const app=await harness();app.state.server_now='2026-09-15T15:07:03+08:00';
 await app.el('cutoff-now').onclick();
 assert.equal(app.el('cutoff').value,app.state.server_now);
 assert.equal(app.el('day').value,app.state.default_day);
 assert.equal(app.read('basket'),null);assert.ok(app.calls.every(c=>c.method==='GET'));
});

test('only retryable source failures offer a source retry and details remain escaped',async()=>{
 const app=await harness({reader:true});
 app.evaluate(`state.source_categories={technology:'科技'};`);
 const source={source_id:'demo',name:'测试来源',categories:['technology'],health:{kind:'network_error',title:'连接暂时失败',note:'解析服务不可用',retryable:true,error_code:'<script>unsafe</script>'}};
 app.evaluate('state.testSource='+JSON.stringify(source));
 let markup=app.read('sourceCard(state.testSource)');
 assert.match(markup,/data-retry-source="demo"/);assert.match(markup,/&lt;script&gt;/);assert.ok(!markup.includes('<script>'));
 app.evaluate('state.testSource.health.retryable=false');
 assert.ok(!app.read('sourceCard(state.testSource)').includes('data-retry-source='));
});

function workflowEdition(){const e=edition();e.candidate_pool=[1,2].map(n=>({event_id:'clue-'+n,title:'合成线索'+n,summary:'<script>合成摘要</script>',why:'观察依据',active:true,sources:[{url:'https://example.org/'+n,source_name:'测试来源',published_at:'2026-09-12'}],origin:'ai_research',report_id:'r',item_id:'item_'+n}));return e;}

test('new candidate pool has no automatic selection; manual check/order/notes survive reload',async()=>{
 const app=await harness({reader:true,workflow:true});const e=workflowEdition();app.route(c=>c.url.startsWith('/api/edition?')?e:undefined);
 await app.evaluate("loadEdition('ed-one')");
 assert.equal(app.read('basket.selected_clue_ids').length,0);assert.ok(!app.el('pool-list').innerHTML.includes(' checked'));
 assert.ok(!app.el('pool-list').innerHTML.includes('<script>'));
 app.on('change',{target:{dataset:{clue:'clue-1'},checked:true}});
 app.on('change',{target:{dataset:{clue:'clue-2'},checked:true}});
 await app.evaluate("workflowClick({dataset:{clueUp:'clue-2'}})");
 await app.el('add-note').onclick();
 app.evaluate("basket.manual_notes[0].content='用户自己的补充';basket.manual_notes[0].selected=true;persistDraft()");
 const before=app.read('basket');await app.el('refresh').onclick();
 assert.deepEqual(app.read('basket'),before);assert.deepEqual(app.read('basket.selected_clue_ids'),['clue-2','clue-1']);
 assert.match(app.el('manual-notes').innerHTML,/用户自己的补充/);
 assert.equal(app.el('export').disabled,true);
});

test('pool removal cancels just that clue and preserves unrelated unsaved edits',async()=>{
 const app=await harness({reader:true,workflow:true});const e=workflowEdition();
 app.route(c=>{if(c.url.startsWith('/api/edition?'))return e;if(c.url==='/api/pool-remove'){e.candidate_pool[0].active=false;return {decision_revision:0};}});
 await app.evaluate("loadEdition('ed-one')");
 app.evaluate("basket.selected_clue_ids=['clue-1','clue-2'];basket.mainline_note='保持这个角度';persistDraft()");
 await app.evaluate("removeFromPool('clue-1')");
 assert.deepEqual(app.read('basket.selected_clue_ids'),['clue-2']);assert.equal(app.read('basket.mainline_note'),'保持这个角度');
 assert.equal(app.calls.find(c=>c.url==='/api/pool-remove').body.expected_revision,0);
 assert.ok(!app.el('pool-list').innerHTML.includes('data-clue="clue-1"'));
});

test('failed pool removal leaves draft and membership untouched',async()=>{
 const app=await harness({reader:true,workflow:true});const e=workflowEdition();
 app.route(c=>c.url.startsWith('/api/edition?')?e:c.url==='/api/pool-remove'?{httpStatus:400,payload:{error:'选择版本已变化'}}:undefined);
 await app.evaluate("loadEdition('ed-one')");app.evaluate("basket.selected_clue_ids=['clue-1'];persistDraft()");const before=app.read('basket');
 await app.on('click',{target:{closest:()=>({dataset:{poolRemove:'clue-1'}})}});
 assert.deepEqual(app.read('basket'),before);assert.equal(app.read('activePool().length'),2);assert.match(app.el('message').textContent,/选择版本已变化/);
});

test('multi-clue saved background exports without requiring a main event and shows preview',async()=>{
 const app=await harness({reader:true,workflow:true});const e=workflowEdition();
 e.decision={selected_event_id:null,selected_fact_ids:[],selected_clue_ids:['clue-1'],manual_notes:[],decision_revision:1};
 app.route(c=>c.url.startsWith('/api/edition?')?e:c.url==='/api/export'?{export_id:'export-test',markdown_preview:'# 合成背景\n已选线索'}:undefined);
 await app.evaluate("loadEdition('ed-one')");assert.equal(app.el('export').disabled,false);await app.el('export').onclick();
 assert.match(app.el('handoff-preview').textContent,/已选线索/);assert.match(app.el('downloads').innerHTML,/export-test/);
 app.el('mainline').value='新的角度';app.el('mainline').oninput();assert.equal(app.el('export').disabled,true);assert.equal(app.el('downloads').innerHTML,'');
});

test('changed product cannot accidentally export the previous edition background',async()=>{
 const app=await harness({reader:true,workflow:true});await app.evaluate("loadEdition('ed-one')");app.el('product').value='517120';
 await app.el('save').onclick();assert.match(app.el('message').textContent,/产品或截点已改变/);assert.ok(!app.calls.some(c=>c.url==='/api/decision'));
});

test('product research sends only explicit extra keywords, never the local manual notes',async()=>{
 const app=await harness({reader:true,workflow:true});await app.evaluate("loadEdition('ed-one')");
 app.el('search-keywords').value='云收入，平台监管';app.evaluate("basket.manual_notes=[{content:'PRIVATE-NOTE',selected:true}]");
 app.route(c=>c.url==='/api/research'?{job_id:'job-search'}:c.url==='/api/job?id=job-search'?{status:'failed',action:'research',progress:[],error:'测试停止'}:undefined);
 await app.el('research').onclick();const req=app.calls.find(c=>c.url==='/api/research');
 assert.deepEqual(req.body.extra_keywords,['云收入','平台监管']);assert.ok(!JSON.stringify(req.body).includes('PRIVATE-NOTE'));
});

test('manual source shortcut keeps the reference and draft without selecting or sending it',async()=>{
 const app=await harness({reader:true,workflow:true,workspace:true,visuals:true});
 await app.evaluate("loadEdition('ed-one')");
 app.state.reader_sources.push({source_id:'manual-reference',name:'手动来源<script>',url:'https://example.org/reference',enabled:true,sections:['sector'],categories:['policy'],usage_mode:'manual',alternatives:[]});
 await app.evaluate('refreshState()');
 app.evaluate("basket.mainline_note='保留未保存的主线';persistDraft();renderReader()");
 assert.match(app.el('manual-sources').innerHTML,/打开查阅/);
 assert.ok(!app.el('sector-sources').innerHTML.includes('manual-reference'));
 assert.ok(!app.el('manual-sources').innerHTML.includes('<script>'));
 const before=app.calls.length;
 await app.evaluate("workflowClick({dataset:{sourceNote:'manual-reference'}})");
 const note=app.read('basket.manual_notes[0]');
 assert.equal(note.source_url,'https://example.org/reference');
 assert.equal(note.source_name,'手动来源<script>');
 assert.equal(note.content,'');assert.equal(note.selected,false);
 assert.equal(app.read('basket.mainline_note'),'保留未保存的主线');
 assert.equal(app.calls.length,before);
 assert.equal(app.el('materials-workspace').hidden,false);
 assert.match(app.el('manual-notes').innerHTML,/手动来源&lt;script&gt;/);
});

test('morning chart counts come from the saved report and empty data remains explicit',async()=>{
 const app=await harness({reader:true,workflow:true,visuals:true});
 assert.match(app.el('topic-chart').innerHTML,/尚无分类数据/);
 assert.equal(app.el('overview-articles').textContent,'1');
 app.evaluate("daily.reading_report={output:{items:[{category:'technology'},{category:'technology'},{category:'policy'}]}};renderVisuals()");
 assert.match(app.el('topic-caption').textContent,/3 条/);
 assert.match(app.el('topic-chart').innerHTML,/data-reading-category="technology"/);
 assert.match(app.el('topic-chart').innerHTML,/<strong>2<\/strong>/);
 assert.match(app.el('topic-chart').innerHTML,/<progress max="2" value="2"/);
 assert.match(app.el('topic-chart').innerHTML,/<progress max="2" value="1"/);
 assert.ok(!app.el('topic-chart').innerHTML.includes('style='),'CSP forbids inline style attributes');
 assert.ok(!app.el('source-coverage-visual').innerHTML.includes('style='));
 assert.equal(app.read('basket'),null);
 assert.ok(app.calls.every(c=>c.method==='GET'));
});


test('targeted evidence cards escape quotes and retain pending material identity', async () => {
  const app=await harness({reader:true,workflow:true});
  const source={url:'https://example.org/public',title:'来源',excerpt:'<script>bad()</script>',
    material_identity:'host_report_pending',retrieved_at:'2026-09-15T00:00:00Z',tool_ref:'unverified_ref',source_excerpt_verified:false};
  const card=app.evaluate('evidenceDetails('+JSON.stringify({sources:[source],claims:[],missing_items:['缺少原文核对']})+')');
  assert.match(card,/宿主报告待复核/);
  assert.match(card,/&lt;script&gt;/);
  assert.doesNotMatch(card,/<script>/);
  assert.match(card,/缺少原文核对/);
});

test('same event offers explicit merge for new evidence or claims', async () => {
  const app=await harness({reader:true,workflow:true});
  app.evaluate(`detail={candidate_pool:[{event_id:'event-1',active:true,research_identity:'identity-1',material_fingerprints:['a'],claims:[]}]}`);
  let markup=app.evaluate(`reportPoolButton({report_id:'new'},{item_id:'item_1',research_identity:'identity-1',material_fingerprints:['a','b']})`);
  assert.match(markup,/合并新增依据到原线索/);
  markup=app.evaluate(`reportPoolButton({report_id:'new'},{item_id:'item_1',research_identity:'identity-1',material_fingerprints:['a'],claims:[{claim_fingerprint:'c',evidence_fingerprint:'a'}]})`);
  assert.match(markup,/合并新增依据到原线索/);
  markup=app.evaluate(`reportPoolButton({report_id:'new'},{item_id:'item_1',research_identity:'identity-1',material_fingerprints:['a'],claims:[]})`);
  assert.match(markup,/已加入候选池/);
});

test('failed refresh keeps the previous usable report visible', async () => {
  const app=await harness({reader:true,workflow:true});
  const previous={status:'partial',host:'codex',report_id:'old',output:{items:[{item_id:'item_1',title:'原有研究线索',summary:'先前可用结果',sources:[],rank:1}],gaps:[],coverage:[]}};
  const markup=app.evaluate('reportHTML('+JSON.stringify({status:'failed',previous_report:previous})+',"product")');
  assert.match(markup,/本次宿主任务未完成/);
  assert.match(markup,/原有研究线索/);
  assert.match(markup,/以下保留上次可用结果/);
});

test('technical source failure can display host search without a morning badge', async () => {
  const app=await harness({reader:true});
  const markup=app.evaluate('sourceCard('+JSON.stringify({source_id:'fda',name:'FDA',url:'https://www.fda.gov',categories:['medicine'],usage_mode:'search_only',search_enabled:true,host_read_allowed:true})+')');
  assert.match(markup,/产品定点补搜/);
  assert.doesNotMatch(markup,/程序晨扫/);
});


function semanticClue(index,role='recent'){
 return {item_id:'item_'+index,report_id:'report-one',research_identity:'identity-'+index,title:'合成标题 '+index,
 summary:'合成公开摘要',why:'合成产品关系',why_now:'仍能解释本期经营状态',editorial_role:role,
 validity_state:'active',validity_reason:'合成有效性说明',sources:[{url:'https://example.org/'+index,published_at:'2026-09-10',source_name:'合成来源'}],
 material_fingerprints:[],claims:[],missing_items:[],latest_increment:index===11};
}
function clueGroups(){return {recent:Array.from({length:11},(_,i)=>semanticClue(i+1)),background:[semanticClue(20,'background')],pending:[],
 runs:[{report_id:'report-one',mode:'explore',finished_at:'2026-09-15T01:00:00Z',count:1,gaps:['合成覆盖缺口'],usage:{total_elapsed_seconds:12}}],limits:{recent_items:10,background_items:5}};}

test('product groups retain two roles, collapsed extras, source dates and escaped text',async()=>{
 const app=await harness({reader:true,workflow:true,exploration:true});
 await app.evaluate("loadEdition('ed-one')");
 const groups=clueGroups();groups.recent[0].why_now='<script>unsafe</script>';
 await app.evaluate('detail.product_clues='+JSON.stringify(groups)+';renderReaderReports()');
 const output=app.el('product-report').innerHTML;
 assert.match(output,/近期线索/);assert.match(output,/产品背景/);assert.match(output,/data-extra-clues="recent"/);
 assert.match(output,/本轮新增/);assert.match(output,/2026-09-10/);
 assert.match(output,/&lt;script&gt;unsafe/);assert.doesNotMatch(output,/<script>unsafe/);
});

test('exploration completion refreshes only results while preserving unsaved text and editing controls',async()=>{
 let finish;const gate=new Promise(resolve=>finish=resolve);let requested=false;
 const updated=edition();updated.product_clues=clueGroups();
 const app=await harness({reader:true,workflow:true,exploration:true,handler:async call=>{
  if(call.url==='/api/explore'){requested=true;return {job_id:'explore-one'};}
  if(call.url.startsWith('/api/job?')){await gate;return {job_id:'explore-one',action:'explore',status:'completed',edition_id:'ed-one',reader_profile:'default',progress:[],result:{output:{items:[semanticClue(11)]}}};}
  if(call.url.startsWith('/api/edition?'))return updated;
 }});
 await app.evaluate("loadEdition('ed-one')");
 app.el('explore-question').value='更多背景';
 const running=app.el('explore').onclick();
 for(let i=0;i<20&&!requested;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(requested,true);assert.equal(app.el('product').disabled,false);assert.equal(app.el('save').disabled,false);
 assert.equal(app.el('research').disabled,true);
 app.el('mainline').value='探查期间正在编辑的主线';app.el('mainline').oninput();
 const before=app.read('basket');finish();await running;
 assert.deepEqual(app.read('basket'),before);assert.equal(app.read('dirty'),true);
 assert.equal(app.el('mainline').value,'探查期间正在编辑的主线');
 assert.match(app.el('product-report').innerHTML,/本轮新增/);
 assert.equal(app.read('busy'),false);
 const request=app.calls.find(c=>c.url==='/api/explore').body;
 assert.equal(request.edition_id,'ed-one');assert.equal(request.research_question,'更多背景');assert.ok(request.request_id);
 assert.equal(request.news_cutoff,undefined);
});

test('exploration result cannot overwrite another selected edition or its draft',async()=>{
 const app=await harness({reader:true,workflow:true,exploration:true});
 await app.evaluate("loadEdition('ed-one')");
 await app.evaluate("editionId='ed-two';basket.mainline_note='另一产品草稿';dirty=true");
 const before=app.calls.length;
 await app.evaluate("refreshExplorationResults({edition_id:'ed-one',reader_profile:'default'})");
 assert.equal(app.calls.length,before);assert.equal(app.read('basket.mainline_note'),'另一产品草稿');
});

test('stopped exploration preserves draft and exposes no fabricated results',async()=>{
 const app=await harness({reader:true,workflow:true,exploration:true,handler:call=>{
  if(call.url.startsWith('/api/job?'))return {job_id:'stop-one',action:'explore',status:'timed_out',edition_id:'ed-one',progress:[],error:'达到时间上限'};
 }});
 await app.evaluate("loadEdition('ed-one')");
 await app.evaluate("basket.mainline_note='保留';persistDraft()");
 await app.evaluate("pollExploration('stop-one')");
 assert.equal(app.read('basket.mainline_note'),'保留');assert.equal(app.read('dirty'),true);
 assert.match(app.el('message').textContent,/达到时间上限/);assert.equal(app.el('explore-stop').hidden,true);
});
const writingOptions={workflow:true,writing:true,exploration:true};
test('writing renders a framework without copying it into the confirmation editor',async()=>{
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?writingEdition():undefined});
 await app.evaluate("loadEdition('ed-one')");
 assert.match(app.el('writing-outline-result').textContent,/合成框架/);
 assert.equal(app.el('writing-final').value,'');assert.equal(app.el('writing-generate').disabled,true);
 app.el('writing-final').value='人工改好的框架';app.el('writing-final').oninput();
 assert.equal(app.el('writing-generate').disabled,false);
});
test('writing editor, feedback and material draft survive result completion',async()=>{
 let polls=0;
 const app=await harness({...writingOptions,handler:c=>{
  if(c.url.startsWith('/api/edition?'))return writingEdition();
  if(c.url==='/api/writing-outline')return {job_id:'w-one'};
  if(c.url==='/api/job?id=w-one'){polls++;return {job_id:'w-one',action:'writing-outline',stage:'outline',edition_id:'ed-one',status:polls===1?'running':'completed',reader_profile:'default',progress:[]};}
 }});
 await app.evaluate("loadEdition('ed-one')");
 app.el('writing-final').value='不能覆盖的最终框架';app.el('writing-final').oninput();
 app.el('writing-feedback').value='请保留我的意见';app.el('writing-feedback').oninput();
 await app.el('writing-outline').onclick();
 assert.equal(app.el('writing-final').value,'不能覆盖的最终框架');
 assert.equal(app.el('writing-feedback').value,'请保留我的意见');
 assert.equal(polls,2);assert.equal(app.calls.filter(c=>c.url==='/api/writing-draft').length,0);
});
test('writing sends confirmation before draft and preserves pasted modifications',async()=>{
 const app=await harness({...writingOptions,handler:c=>{
  if(c.url.startsWith('/api/edition?'))return writingEdition();
  if(c.url==='/api/writing-confirm')return {...writingOutline('confirmed'),stage:'confirmed_outline',content:{confirmed_text:c.body.text}};
  if(c.url==='/api/writing-draft')return {job_id:'draft-task'};
  if(c.url==='/api/job?id=draft-task')return {job_id:'draft-task',action:'writing-draft',stage:'draft',edition_id:'ed-one',status:'completed',progress:[]};
 }});
 await app.evaluate("loadEdition('ed-one')");
 app.el('writing-final').value='用户粘贴并补充的最终框架';app.el('writing-final').oninput();
 await app.el('writing-generate').onclick();
 const writes=app.calls.filter(c=>c.method==='POST');
 assert.equal(writes[0].url,'/api/writing-confirm');assert.equal(writes[0].body.text,'用户粘贴并补充的最终框架');
 assert.equal(writes[1].url,'/api/writing-draft');assert.equal(writes[1].body.confirmed_version_id,'confirmed');
});
test('writing editor isolates Editions and restores unfinished text on return',async()=>{
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?writingEdition(new URL('http://local'+c.url).searchParams.get('id')):undefined});
 await app.evaluate("loadEdition('ed-one')");app.el('writing-final').value='一期未保存框架';app.el('writing-final').oninput();
 await app.evaluate("loadEdition('ed-two')");assert.equal(app.el('writing-final').value,'');
 app.el('writing-final').value='二期草稿';app.el('writing-final').oninput();
 await app.evaluate("loadEdition('ed-one')");assert.equal(app.el('writing-final').value,'一期未保存框架');
});
test('writing jobs allow manual editing and switching while heavy controls stay disabled',async()=>{
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?writingEdition():undefined});
 await app.evaluate("loadEdition('ed-one');writingJob={job_id:'running'};setBusy(true)");
 assert.equal(app.el('product').disabled,false);assert.equal(app.el('save').disabled,false);
 assert.equal(app.el('writing-final').disabled,false);assert.equal(app.el('writing-outline').disabled,true);
 assert.equal(app.el('research').disabled,true);
});
test('stale selection blocks writing from an older confirmed outline',async()=>{
 const e=writingEdition();e.writing.versions[0].stale_selection=true;
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?e:undefined});
 await app.evaluate("loadEdition('ed-one')");app.el('writing-final').value='旧框架';app.el('writing-final').oninput();
 assert.equal(app.el('writing-generate').disabled,true);assert.match(app.el('writing-state').textContent,/旧选材/);
});
test('writing supplement sends only the visible public question and frame ID',async()=>{
 const app=await harness({...writingOptions,reader:true,handler:c=>{
  if(c.url.startsWith('/api/edition?'))return writingEdition();
  if(c.url==='/api/explore')return {job_id:'gap-task'};
  if(c.url==='/api/job?id=gap-task')return {job_id:'gap-task',action:'explore',edition_id:'ed-one',status:'completed',progress:[],result:{output:{items:[]}}};
 }});
 await app.evaluate("loadEdition('ed-one')");app.el('writing-final').value='私人草稿不应作为搜索词';app.el('writing-final').oninput();
 app.el('writing-question').value='公开产业问题';app.el('writing-question').oninput();
 await app.el('writing-supplement').onclick();
 const body=app.calls.find(c=>c.url==='/api/explore').body;
 assert.equal(body.research_question,'公开产业问题');assert.deepEqual(body.writing_context,{outline_version_id:'outline-v1'});
 assert.doesNotMatch(JSON.stringify(body),/私人草稿/);
});
test('must-include flags are explicit and do not automatically select a material',async()=>{
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?writingEdition():undefined});
 await app.evaluate("loadEdition('ed-one')");await app.evaluate("basket.selected_clue_ids=['clue-one'];workflowChange({dataset:{mustMaterial:'clue-one'},checked:true})");
 assert.deepEqual(app.read('basket.must_include_ids'),['clue-one']);
 await app.evaluate("workflowChange({dataset:{clue:'clue-one'},checked:false})");
 assert.deepEqual(app.read('basket.must_include_ids'),[]);
});

test('switching to a saved product restores its Edition without starting generation',async()=>{
 const one=writingEdition(),two=writingEdition('ed-two');two.edition.product_code='588090';
 const app=await harness({...writingOptions,products:[{code:'513130',name:'产品一'},{code:'588090',name:'产品二'}],
  handler:c=>c.url.startsWith('/api/edition?')?(c.url.includes('ed-two')?two:one):undefined});
 await app.evaluate('state.editions='+JSON.stringify([one.edition,two.edition]));
 await app.evaluate("loadEdition('ed-one')");
 app.el('writing-final').value='产品一未确认的修改';app.el('writing-final').oninput();
 app.el('product').value='588090';await app.el('product').onchange();
 assert.equal(app.read('editionId'),'ed-two');assert.equal(app.el('writing-final').value,'');
 app.el('product').value='513130';await app.el('product').onchange();
 assert.equal(app.el('writing-final').value,'产品一未确认的修改');
 assert.ok(app.calls.every(c=>c.method==='GET'));
});

test('older skill drafts remain readable but cannot be copied as currently checked output',async()=>{
 const e=writingEdition();e.writing.versions.push({version_id:'draft-old',stage:'draft',decision_revision:1,
  created_at:'2026-09-13T11:00:00Z',stale_skill_version:true,checks:{errors:[]},
  content:{title:'历史标题',body_characters:10,paragraphs:[{text:'保留历史正文',sources:[{name:'实际出处',url:'https://example.org/article',published_at:'2026-09-12'}]}],risk_notice:{text:'风险提示',missing:[]}}});
 const app=await harness({...writingOptions,handler:c=>c.url.startsWith('/api/edition?')?e:undefined});
 await app.evaluate("loadEdition('ed-one')");
 const output=app.el('writing-draft-result').innerHTML;
 assert.match(output,/历史标题/);assert.match(output,/保留历史正文/);
 assert.match(output,/旧版技能/);assert.match(output,/data-copy-draft="draft-old" disabled/);
 assert.match(output,/<details class="paragraph-sources">/);
 assert.equal(app.el('writing-framework-panel').open,false);
});

test('program recheck adds a version while preserving the final editor and avoiding a model task',async()=>{
 const app=await harness({...writingOptions,handler:c=>{
  if(c.url.startsWith('/api/edition?'))return writingEdition();
  if(c.url==='/api/writing-recheck')return {...writingOutline('recheck-version'),parent_id:c.body.version_id};
 }});
 await app.evaluate("loadEdition('ed-one')");app.el('writing-final').value='尚未确认的人工修改';app.el('writing-final').oninput();
 const button={dataset:{recheckDraft:'draft-one'}};
 await app.on('click',{target:{closest:()=>button}});
 assert.equal(app.el('writing-final').value,'尚未确认的人工修改');
 assert.equal(app.read('detail.writing.versions').at(-1).parent_id,'draft-one');
 assert.deepEqual(app.calls.filter(c=>c.method==='POST').map(c=>c.url),['/api/writing-recheck']);
});
