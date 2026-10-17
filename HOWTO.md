# Phantom Vikings DPS – návod krok za krokem

```
FFXIV + ACT + OverlayPlugin          tvůj PC                       webhosting               raideři
 (WebSocket 127.0.0.1:10501)  ->  relay.py  --HTTPS POST-->  receive.php -> latest.json  <--  index.html (polling)
```

PC jen **odesílá**. Nic nepotřebuje příchozí spojení, takže nepotřebuješ veřejnou IP, port forwarding ani ngrok.

## Co je v balíčku

| Soubor | Kam patří |
|---|---|
| `relay/relay.py`, `config.example.json`, `requirements.txt`, `start-relay.bat` | tvůj PC (Windows, kde běží ACT) |
| `web/index.html`, `receive.php`, `.htaccess`, `config.example.php` | webhosting, složka např. `/dps/` |

---

## Krok 1 – Webhosting (cca 5 minut)

1. Ve správci hostingu (nebo přes FTP, např. FileZilla) vytvoř složku `dps`.
2. Nahraj do ní **`index.html`, `receive.php`, `.htaccess`**.
3. Zkopíruj `config.example.php` na `config.php`, změň `$TOKEN` na dlouhý náhodný řetězec (min. 32 znaků, třeba z [generátoru hesel](https://bitwarden.com/password-generator/)) a nahraj `config.php` taky.
4. Ověř, že složka je zapisovatelná pro PHP (obvykle ano; jinak chmod 755 nebo 775).
5. Otevři `https://TVUJ-WEB.cz/dps/` – uvidíš „čekám na data…“. To je správně.

Požadavky: PHP 7.4+ a HTTPS. Žádná databáze.

## Krok 2 – ACT a OverlayPlugin

1. V ACT ve složce Plugins musí být **FFXIV_ACT_Plugin** (parser) a **OverlayPlugin**. Stažení a návod: [OverlayPlugin na GitHubu](https://github.com/OverlayPlugin/OverlayPlugin).
2. V ACT: *Plugins → OverlayPlugin.dll → WSServer*. Zapni **Start**, port nech **10501**, adresa **127.0.0.1**.
3. Pozor: server nikam ven nepouštěj (nech 127.0.0.1), relay je na stejném PC.

## Krok 3 – Relay na PC

1. Nainstaluj [Python 3.10+](https://www.python.org/downloads/) (při instalaci zaškrtni **Add to PATH**).
2. Rozbal složku `relay` třeba na plochu a v ní spusť v příkazové řádce:
   ```
   pip install -r requirements.txt
   ```
3. Zkopíruj `config.example.json` na `config.json` a vyplň:
   - `endpoint`: `https://TVUJ-WEB.cz/dps/receive.php`
   - `token`: **stejný** jako v `config.php` na hostingu
4. Spusť `start-relay.bat` (nebo `python relay.py`). Měl by vypsat „Připojeno k OverlayPlugin“.
5. Otevři web. Po prvním souboji (nebo i na cvičném panákovi) se objeví čísla.

Pokaždé před raidem stačí: spustit ACT, spustit `start-relay.bat`, poslat raiderům odkaz.

## Co web umí

- **Live** – aktuální souboj: DPS, damage, Crit %, DH %, Max hit (z ACT).
- **Overall večera** – damage všech dokončených soubojů / součet jejich délek. Souboj se počítá, až když skončí a trval aspoň 20 s (`min_fight_seconds`).
- **Souboje** – historie wipů a killů.
- **Spelly** – per-spell tabulka. **Experimentální**, viz kalibrace níže.

Relay si ukládá stav večera do `session_state.json`, takže po pádu nebo restartu se overall nezapomene. Nový večer: `python relay.py --reset`.

## Proč to nepřetíží hosting

- Hráči stahují jeden malý statický soubor `latest.json`, žádné PHP ani databáze při čtení.
- `receive.php` ukládá soubor atomicky (zapíše tmp a přejmenuje), takže nikdo nikdy nenačte půlku.
- Prohlížeč se ptá s `no-cache`, server odpoví `304 Not Modified`, když se nic nezměnilo, a JSON je komprimovaný (gzip).
- Při skryté záložce se polling zpomalí na 10 s.
- Reálně: 8 hráčů × 1 požadavek / 2 s = 4 req/s, což zvládne i nejlevnější hosting.
- Pokud tvůj hosting omezuje počet PHP volání: zvyš `push_interval_seconds` na 3–5.

## Typ obsahu (dungeon / raid / trial / ultimate)

Relay si z log řádku o změně zóny zjistí ID zóny a podle souboru `relay/zones.json` pozná, kde jste:

- **Dungeon:** Overall (karta Run) začíná od nuly při každém vstupu do dungeonu, pully se dělí na boss a trash (odhad: boss je pull, kde padl jediný nepřítel a trval aspoň 25 s). Minimální délka pullu je 3 s (`min_fight_seconds_dungeon`).
- **Raid, trial, ultimate:** Overall se počítá za celý večer (karta Tonight), karta Attempts ukazuje pokusy po bossech s kill/wipe a nejlepším pullem.
- **Cokoli jiného:** jednoduchý výchozí pohled.

Výsledek souboje: kill = padl boss (název souboje) nebo přišla zpráva o dokončení obsahu, wipe = přišel fade po wipu. Když nic z toho nepřijde, výsledek se nezobrazí.

Seznam zón pochází z herních dat ([ffxiv-datamining](https://github.com/xivapi/ffxiv-datamining)). Po velkém patchi ho obnovíš příkazem `python tools/build_zones.py` (stáhne čerstvá data a přepíše `relay/zones.json`).

## Tabulka spellů

Spelly se skládají z raw log řádků 21/22 podle dokumentace formátu ([LogGuide](https://github.com/OverlayPlugin/cactbot/blob/main/docs/LogGuide.md)): damage včetně velkých hitů, crit a direct hit, pety se přičítají majiteli. Damage over time se do spellů nerozpadá, ukazuje se jako řádek „Other“.

Kdyby crit nebo DH nesedělo s ACT, spusť `python relay.py --dump`, odehraj souboj a pošli řádky z `relay/dump.jsonl`. Bity se dají přepsat v `config.json` (`crit_mask`, `dh_mask`).

## GitHub (volitelně)

Balíček je připravený i pro repozitář: `.gitignore` už vynechává `config.json`, `config.php`, `latest.json` a `dump.jsonl`, takže tokeny se nezveřejní.

```
git init
git add .
git commit -m "Phantom Vikings DPS"
git remote add origin https://github.com/TVUJ-UCET/pv-dps.git
git push -u origin main
```

Na GitHub Pages web provozovat nejde (nemá PHP a data se nahrávají za běhu), ale repo se hodí jako záloha a verzování. Nikdy do něj nedávej skutečný token.

## Bezpečnost

- Token chrání jen **zápis**. Čtení je veřejné pro každého, kdo zná URL. Chceš-li to omezit, dej složku `/dps/` pod Basic Auth v administraci hostingu (nebo použij nepředvídatelný název složky).
- Index obsahuje jen jména postav a čísla z boje, nic citlivého.

## Řešení problémů

| Příznak | Příčina / oprava |
|---|---|
| Relay píše „Spojení s ACT spadlo“ | WSServer v OverlayPluginu neběží, jiný port nebo adresa |
| Relay píše „Server vrátil 403“ | Token v `config.json` ≠ token v `config.php` |
| Relay píše „Server vrátil 500“ | Složka na hostingu není zapisovatelná |
| Web říká „data stará N s“ | Relay neběží, nebo vypadl internet |
| Overall je prázdný | Souboj musí skončit a trvat ≥ 20 s |
| Spelly prázdné nebo špatné crit/DH | Viz kapitola Tabulka spellů |
| Web ukazuje starý stav i po restartu | `python relay.py --reset` a tvrdý refresh (Ctrl+F5) |

## Další nápady

- Barevné role/joby, rozklikávací spelly, grafy DPS v čase (`history` už se posílá).
- Více raidů najednou (každý s jiným tokenem a složkou).
- Archiv večera: na konci raidu uložit `latest.json` pod datem.
