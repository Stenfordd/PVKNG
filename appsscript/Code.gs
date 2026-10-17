/**
 * Phantom Vikings DPS - Apps Script verze (bez hostingu)
 *
 * relay.py (tvůj PC) --POST--> doPost() --uloží do CacheService
 * raideři otevřou URL web appky -> doGet() vrátí stránku -> stránka si bere data přes getData()
 *
 * Token se nastavuje ve Project Settings -> Script properties -> TOKEN.
 * Apps Script nevidí HTTP hlavičky, proto relay posílá token v těle (pole "_token").
 */

const CHUNK = 30000;   // znaků na kus (CacheService má limit 100 KB na hodnotu)
const TTL = 21600;     // 6 h = maximum CacheService; každý push ho obnoví

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!token || body._token !== token) return json_({ ok: false, error: 'forbidden' });
    delete body._token;
    put_('latest', JSON.stringify(body));
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  // ?data=1 vrátí čisté JSON (hodí se pro ladění v prohlížeči)
  if (e && e.parameter && e.parameter.data) {
    const body = get_('latest') || '{}';
    // JSONP: stránka na GitHub Pages si data načte přes <script>, takže nehrozí problém s CORS
    const cb = e.parameter.callback;
    if (cb && /^[A-Za-z_$][\w$.]*$/.test(cb)) {
      return ContentService.createTextOutput(cb + '(' + body + ')')
        .setMimeType(ContentService.MimeType.JAVASCRIPT);
    }
    return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
  }
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Phantom Vikings DPS')
    .setFaviconUrl('https://static.thenounproject.com/png/dragon-icon-1878520-512.png')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// volá stránka přes google.script.run
function getData() {
  return get_('latest') || '';
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function put_(key, str) {
  const cache = CacheService.getScriptCache();
  const n = Math.max(1, Math.ceil(str.length / CHUNK));
  const obj = {};
  for (let i = 0; i < n; i++) obj[key + '_' + i] = str.substr(i * CHUNK, CHUNK);
  obj[key + '_n'] = String(n);
  cache.putAll(obj, TTL);
}

function get_(key) {
  const cache = CacheService.getScriptCache();
  const n = parseInt(cache.get(key + '_n') || '0', 10);
  if (!n) return null;
  const keys = [];
  for (let i = 0; i < n; i++) keys.push(key + '_' + i);
  const parts = cache.getAll(keys);
  let s = '';
  for (let i = 0; i < n; i++) {
    const p = parts[key + '_' + i];
    if (p == null) return null;   // část vypadla z cache -> raději nic než rozbité JSON
    s += p;
  }
  return s;
}
