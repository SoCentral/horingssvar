# hent_horingssvar

Laster ned alle høringssvar fra en høringsside på **regjeringen.no** eller **stortinget.no** og lagrer dem lokalt:
svar skrevet som tekst blir markdown-filer, og vedlegg (PDF, Word o.l.) lastes ned uendret.

Utviklet av SoCentral for å gjøre det enkelt å analysere høringsrunder, for eksempel med AI-verktøy
eller i et felles dokumentarkiv.

## Hva du får

For hver høring lages en egen mappe, f.eks. `~/horingssvar/stortinget-10005660/`:

```
stortinget-10005660/
├── 001 - Alversund Musikklag.md          # Tekstsvar som markdown
├── 002 - Agder Fylkeskommune (1).pdf     # Vedlegg, nummerert som svaret
├── 002 - Agder Fylkeskommune (2).pdf
├── ...
├── _oversikt.md                          # Tabell over alle svar, type og filnavn
├── _metode.md                            # Metode, prosess og bruk av AI
├── nye_vedlegg/                          # Filer som kom til i senere kjøringer
└── .cache/                               # Mellomlager (kan slettes)
```

Hver md-fil starter med avsender, kildelenke og metadata (dato, svartype o.l.), etterfulgt av selve svaret.
Overskrifter, fet skrift, lister og avsnitt er bevart. Har svaret vedlegg, er de listet nederst med filnavn.

## Installasjon

Krever Python 3.9 eller nyere.

```bash
git clone https://github.com/SoCentral/horingssvar.git
cd horingssvar
python3 -m pip install --user -r requirements.txt
```

## Bruk

Gi URL-en til høringssiden som argument:

```bash
python3 hent_horingssvar.py "https://www.stortinget.no/no/Hva-skjer-pa-Stortinget/Horing/horing/?h=10005660"
python3 hent_horingssvar.py "https://www.regjeringen.no/no/dokumenter/innspel-til-kulturarenastrategien/id3158922/"
```

Sett alltid URL-en i anførselstegn, siden den ofte inneholder `?` og `&`.

Alternativt kan du sette `HORING_URL` øverst i scriptet og kjøre det uten argument.

### Valg

| Valg | Standard | Beskrivelse |
|---|---|---|
| `--out MAPPE` | `~/horingssvar` | Rotmappe. Hver høring får en undermappe her. |
| `--min-chars N` | `300` | Grense mellom tekstsvar og filsvar (se under). |
| `--pause SEK` | `3` | Pause mellom forespørsler. Øk ved rate limiting. |

Eksempel:

```bash
python3 hent_horingssvar.py "URL" --out ~/Documents/horinger --pause 5
```

## Hvordan svarene behandles

| Situasjon | Resultat |
|---|---|
| Svaret har minst `--min-chars` tegn tekst på siden | md-fil. Eventuelle vedlegg lastes også ned. |
| Svaret har kortere tekst og ett eller flere vedlegg | Kun vedleggene lastes ned (svaret ligger i filen). |
| Svaret har kort tekst og ingen vedlegg | md-fil. |
| Listen lenker direkte til en fil | Filen lastes ned. |

Grensen på 300 tegn skiller ekte tekstsvar fra korte følgetekster som "Vedlagt følger vårt innspill".

## Kjøre på nytt

Scriptet kan kjøres så mange ganger du vil på samme høring:

- **Sider** som allerede er hentet, leses fra `.cache/`. Bare nye svar hentes fra nettet.
- **Filer** som allerede finnes, lastes ikke ned på nytt.
- **Nye filer** fra senere kjøringer havner i `nye_vedlegg/`, så du lett ser hva som har kommet til.
- **md-filer, `_oversikt.md` og `_metode.md`** skrives på nytt hver gang.

Feiler noe underveis (nettverk, rate limiting), kjør scriptet på nytt. Det fortsetter der det slapp.

> **Merk:** Filene nummereres etter rekkefølgen i listen på nettstedet (ofte alfabetisk).
> Kommer det nye svar midt i listen, forskyves numrene, og filer kan da lastes ned på nytt med nytt nummer.
> For en ren mappe: slett alt unntatt `.cache/` og kjør på nytt.

## Rate limiting

regjeringen.no begrenser antall forespørsler på kort tid (HTTP 429). Scriptet håndterer dette selv:
det venter det serveren ber om, eller 1-10 minutter, og prøver igjen opptil seks ganger.
Ser du `Rate limit (429). Venter ...`, er det bare å la det jobbe.

## Støttede nettsteder

| Nettsted | URL-format | Hvordan det leses |
|---|---|---|
| regjeringen.no | `.../dokumenter/<navn>/id<nr>/` | Listen hentes side for side (`showSvar=true&page=N`). Hvert svar leses fra `.hearing-answer`. |
| stortinget.no | `.../Horing/horing/?h=<nr>` | Listen og svarene ligger som JSON-data i siden (React), og leses derfra. |

### Legge til et nytt nettsted

Hvert nettsted er en egen klasse i scriptet (`Regjeringen`, `Stortinget`) med samme grensesnitt:

| Metode | Gjør |
|---|---|
| `matches(url)` | Returnerer `True` hvis URL-en hører til nettstedet. |
| `slug(url)` | Mappenavn for høringen, f.eks. `stortinget-10005660`. |
| `list_items(url, cache)` | Returnerer `(tittel, [{"name", "url"}, ...])`. |
| `is_direct_file(url)` | `True` hvis lenken i listen går rett til en fil. |
| `parse_item(html, url)` | Returnerer `{"meta": {...}, "text": "markdown", "files": [{"label", "url"}]}`. |

Lag en ny klasse og legg den til i `SITES`. Resten (nedlasting, filnavn, oversikt, gjenkjøring) er felles.

Tips: Hent først rå HTML med `curl -sL -A "Mozilla/5.0" "URL" -o side.html` og se hvor dataene ligger.

## Begrensninger

- Tabeller og bilder i tekstsvar blir ikke med i markdown. Se originalen via kildelenken.
- Vedlegg konverteres ikke til tekst. Bruk et eget verktøy hvis du trenger fulltekst fra PDF-er.
- Endrer nettstedene oppsettet sitt, må tilsvarende klasse oppdateres.

## Bruk av AI

Scriptet er utviklet med hjelp fra Claude (Anthropic). Selve høringssvarene hentes og lagres uendret:
innholdet blir ikke oppsummert, tolket eller omskrevet av AI. Hver nedlasting får en `_metode.md`
som dokumenterer dette.
