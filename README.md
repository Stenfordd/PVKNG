# Phantom Vikings DPS

Live DPS meter for the Phantom Vikings Free Company (FFXIV, Lich).

```
ACT + OverlayPlugin -> relay/relay.py (PC) -> Google Apps Script -> index.html (GitHub Pages)
```

- `index.html` – the web page served by GitHub Pages. It reads the data from the Apps Script web app.
- `relay/` – script that runs on the PC with ACT and pushes the data. Copy `config.appsscript.example.json` to `config.json` and fill in the URL and token (never commit `config.json`).
- `appsscript/` – `Code.gs` and `Index.html` for the Apps Script project.
- `src/` – page template and `build.py` that generates `index.html` and `appsscript/Index.html`.
- `HOWTO-appsscript.md` – setup guide (Czech).
