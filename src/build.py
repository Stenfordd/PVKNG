#!/usr/bin/env python3
"""Z jedné šablony src/page.html vygeneruje tři varianty webu:
  appsscript/Index.html  - stránka servírovaná přímo z Apps Scriptu
  docs/index.html        - stránka pro GitHub Pages (data čte z Apps Scriptu přes JSONP)
  web/index.html         - stránka pro klasický hosting s receive.php
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAVICON = "https://static.thenounproject.com/png/dragon-icon-1878520-512.png"
APPS_URL = os.environ.get(
    "APPS_URL",
    "https://script.google.com/macros/s/VLOZ-SVE-DEPLOYMENT-ID/exec")

LOADER_APPS = """const POLL = 4000, HIDDEN_POLL = 10000;
function load(done, fail) {
  google.script.run.withSuccessHandler(done).withFailureHandler(fail).getData();
}"""

LOADER_GH = """const APPS_URL = '%s';
const POLL = 3000, HIDDEN_POLL = 10000;
// JSONP: Apps Script vrátí callback({...}), načte se jako <script>, CORS tím odpadá
function load(done, fail) {
  const cb = '__pv' + Date.now();
  const s = document.createElement('script');
  const cleanup = () => { clearTimeout(t); delete window[cb]; s.remove(); };
  const t = setTimeout(() => { cleanup(); fail(); }, 15000);
  window[cb] = obj => { cleanup(); done(JSON.stringify(obj)); };
  s.onerror = () => { cleanup(); fail(); };
  s.src = APPS_URL + '?data=1&callback=' + cb + '&_=' + Date.now();
  document.head.appendChild(s);
}""" % APPS_URL

LOADER_WEB = """const POLL = 2000, HIDDEN_POLL = 10000;
function load(done, fail) {
  fetch('latest.json', { cache: 'no-cache' }).then(r => r.ok ? r.text() : '').then(done).catch(fail);
}"""

page = open(os.path.join(HERE, "page.html"), encoding="utf-8").read()
fav = f'<link rel="icon" type="image/png" href="{FAVICON}">'

for out, loader, with_fav in (
        ("appsscript/Index.html", LOADER_APPS, False),   # favicon se tam nastavuje v Code.gs
        ("docs/index.html", LOADER_GH, True),
        ("web/index.html", LOADER_WEB, True)):
    html = page.replace("/*__LOADER__*/", loader).replace("<!--FAVICON-->", fav if with_fav else "")
    path = os.path.join(ROOT, out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("vygenerováno", out)
