# Phantom Vikings DPS – verze přes Google Apps Script (bez hostingu)

```
ACT/OverlayPlugin -> relay.py (tvůj PC) --HTTPS POST--> Apps Script web appka (doPost, CacheService)
                                                          ^
                              raideři otevřou URL appky --+  (stránka si bere data přes getData)
```

## 1) Apps Script projekt
1. Jdi na [script.google.com](https://script.google.com) a dej **Nový projekt**.
2. Soubor `Code.gs` přepiš obsahem `appsscript/Code.gs`.
3. Přidej soubor **HTML** (+ → HTML) pojmenovaný přesně `Index` a vlož do něj obsah `appsscript/Index.html`.
4. *Project Settings (ozubené kolo) → Script properties → Add script property*: `TOKEN` = dlouhý náhodný řetězec.

## 2) Nasazení
1. **Deploy → New deployment → typ Web app**.
2. *Execute as:* **Me**, *Who has access:* **Anyone**.
3. Potvrď oprávnění (Google ukáže varování u neověřené appky, rozklikni *Advanced → Go to …*).
4. Zkopíruj **Web app URL** (končí `/exec`). Tohle je odkaz pro raidery **i** adresa pro relay.

Po každé změně kódu musíš udělat **Deploy → Manage deployments → Edit → New version**, jinak běží stará verze.

## 3) Relay
1. Zkopíruj `relay/config.appsscript.example.json` na `relay/config.json`.
2. Vyplň `endpoint` (URL z kroku 2) a `token` (stejný jako TOKEN ve Script properties).
3. Dál stejně jako dřív: OverlayPlugin WSServer na 10501, `pip install -r requirements.txt`, `start-relay.bat`.

## 4) Zkouška
- Otevři URL appky v prohlížeči: uvidíš stránku. Pro kontrolu dat připiš `?data=1` za URL, uvidíš čisté JSON.
- Po spuštění relay a boji (nebo panáka) se čísla objeví do pár sekund.

## Omezení
- **Latence** je vyšší než u hostingu: Apps Script odpovídá zhruba po sekundě a stránka se obnovuje každé 4 s.
- **Cache:** data jsou v CacheService (max 6 h, obnovuje se každým pushem, může být kdykoli vyčištěna). Po restartu appky nebo vyčištění cache stránka počká na další push.
- **Kvóty:** Google uvádí pro běžné účty 90 minut denně na triggery a 30 současných běhů na uživatele ([kvóty](https://developers.google.com/apps-script/guides/services/quotas)). Nenašel jsem tam, zda se do toho počítají i požadavky na web appku. Pokud by se počítaly, 8 hráčů pollujících každé 4 s může při dlouhém raidu narazit. Kdyby se objevila chyba o kvótě, zvyš v `Index.html` `POLL_MS` na 6000–8000 a v `config.json` `push_interval_seconds` na 5.
- **Veřejné čtení:** URL zná kdokoli, komu ji pošleš. Token chrání jen zápis.
- Appku jsem **netestoval přímo u Googlu** (nemám tu přístup), jen relay proti simulaci a syntaxi kódu. První spuštění tedy ber jako ověření.
