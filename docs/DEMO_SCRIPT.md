# EVA — scenariusz pokazu (demo mode + głos)

Wszystko poniżej działa **bez Google, bez internetu do usług zewnętrznych i bez
danych prywatnych**: `EVA_DATA_PROVIDER=demo` podmienia wyłącznie źródło danych,
cała reszta (reguły Attention, Decision Inbox, Focus, strzeżone zatwierdzanie
i wykonywanie akcji z odbiorem) to prawdziwy kod EVA.

## 1. Uruchomienie

```powershell
# backend (demo + głos): katalog repo\backend
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend w trybie REST: katalog repo\frontend
$env:VITE_EVA_CLIENT="rest"; npm run dev
```

Otwórz **http://localhost:5173** — zawsze `localhost`, nigdy adres IP:
przeglądarka udostępnia mikrofon tylko w secure context.

Wymagania głosu (maszynowe, poza repozytorium): `faster-whisper` + model
`small` oraz ffmpeg wskazany przez `EVA_FFMPEG_BINARY` w `.env`.
Kontrola: `GET /api/health` → `components.stt.status = ready`.

## 2. Głos Evy (TTS zainstalowany w systemie)

Eva czyta odpowiedzi za pośrednictwem `speechSynthesis` przeglądarki, czyli
głosów zainstalowanych w Windows. Angielski jest domyślnie (`Zira`), polski
trzeba dodać — **raz**, z uprawnieniami administratora:

```powershell
# PowerShell JAKO ADMINISTRATOR
Add-WindowsCapability -Online -Name "Language.TextToSpeech~~~pl-PL~0.0.1.0"
```

albo bez wiersza polecenia: *Ustawienia → Czas i język → Wymowa → Dodaj głosy →
Polski*. Nie trzeba wpisywać żadnej nazwy głosu — kod dopasowuje go po prefiksie
BCP-47 (`pl`).

Wariant zero-instalacji: Chrome/Edge mają sieciowe głosy neuronowe. Sprawdź w
DevTools (F12 → Console):

```js
[...speechSynthesis.getVoices()].filter(v => v.lang.toLowerCase().startsWith("pl")).map(v => `${v.name} (${v.lang})`)
```

Jeśli lista jest niepusta, Eva będzie mówić po polsku od razu.

## 3. Wgranie danych demonstracyjnych

```powershell
Invoke-RestMethod -Method Post http://localhost:5173/api/demo/reset -Headers @{"X-EVA-Session-ID"="demo"}
```

| warstwa | zawartość |
|---|---|
| Decyzje (HIGH) | wycena ACME 84 000 zł (poprzednio 71 000 zł), dostawca stacji roboczych A/B, budżet podróży +12 000 zł, incydent „PILNE: błąd API 37%” |
| Kontekst (MEDIUM) | agenda ACME, roadmapa Q4, CV kandydata, podróż do Berlina, redlines umowy, KPI, status infrastruktury, ankieta biurowa |
| Szum (LOW, 19) | newslettery, promocje, raporty automatyczne, przesyłki, webinary — celowo wyciszone |
| Kalendarz (`Dziś`) | ACME +35 min (45 min) **nachodzące** na Investor call +65 min, Deep Work +120 min, Product weekly, pociąg do Warszawy |

Reset jest deterministyczny i powtarzalny — można go klikać między próbami.

## 4. Pokaz w 7 krokach (około 4,5 minuty)

**Krok 1 — Attention (30 s).** Ekran główny: 30 spraw, na górze cztery
`HIGH / decision_required`, na dole newsletterowy szum. Powiedz: *reguły są
deterministyczne, model może tylko podnieść priorytet, nigdy obniżyć*.

**Krok 2 — Głos (60 s).** Przytrzymaj orb i powiedz:

> „Które decyzje czekają na moją odpowiedź?”

Puść → transkrypt z językiem i pewnością → **Ask Eva** → po ~6 s odpowiedź
Evy, przeczytana na głos. Dowód: transkrypcja lokalna (whisper), rozumowanie na
konfigurowanym modelu self-hosted, żadnej chmury.

**Krok 3 — Głos na konkretach (45 s).** Ten sam orb:

> „Podaj szczegóły wyceny ACME i ryzyka.”

Eva cytuje fakty z maili i spotkanie z kalendarza (identyfikatory źródeł
`gmail:...`, `demo-event:...`).

**Krok 4 — Zatwierdzenie z odbiorem (60 s).** Decyzje → „Dodatkowe miejsca w
licencji” → *Akceptuj* → ekran potwierdzenia (challenge: digest argumentów) →
zatwierdź. Status `resolved`, a w szczegółach akcji
`executed_externally: false` — EVA zapisała decyzję, nie wykonała żadnego
przelewu ani zobowiązania.

**Krok 5 — Briefing spotkania (40 s).** Pokazuje kolizję w kalendarzu i fakty z
poczty w jednym ujęciu. W UI: zakładka **Briefings** — lista scenariuszy
(*Monday Briefing*, *Briefing after PTO*) plus briefing dla każdego spotkania z
dzisiejszego kalendarza; bloki nachodzące na siebie mają znacznik `Conflict`.
Ten sam briefing można pokazać z konsoli:

```powershell
Invoke-RestMethod -Method Post http://localhost:5173/api/briefing/meeting -ContentType "application/json" -Body '{"meeting_ref":{"calendar_id":"primary","event_id":"demo-acme-review"},"language":"pl"}' | Select-Object -ExpandProperty briefing | Format-List
```

**Krok 6 — Focus i przerwanie (45 s).** Focus włączamy z karty **Focus** na
ekranie głównym: *Start Focus* odpala licznik (format `mm:ss`, pasek postępu),
który odlicza do zera, a **Stop Focus** przerywa sesję i pokazuje podsumowanie
(ile spraw odłożono, ile z nich to decyzje). Ten sam przebieg z konsoli:

```powershell
$h=@{"X-EVA-Session-ID"="demo";"X-EVA-Request-ID"="f1"}
Invoke-RestMethod -Method Post http://localhost:5173/api/focus/start -Headers $h -ContentType "application/json" -Body '{"duration_minutes":45,"threshold":"high","sender_overrides":[]}'
# spokojna sprawa -> NIE przerywa Focusu
Invoke-RestMethod -Method Post http://localhost:5173/api/demo/inject -Headers $h -ContentType "application/json" -Body '{"kind":"normal"}'
# awaria Portalu Klienta -> PRZERYWA (HIGH + urgent, trafia do Decision Inbox)
Invoke-RestMethod -Method Post http://localhost:5173/api/demo/inject -Headers $h -ContentType "application/json" -Body '{"kind":"urgent"}'
```

**Krok 7 — Knowledge (20 s).** Zakładka **Knowledge**: ludzie, konta, projekty
i zasady pracy, na których Eva opiera klasyfikację — każda pozycja ma podane
źródło, a licznik „w Attention” jest liczony na żywo z bieżącej klasyfikacji.
Pokaż wyszukiwarkę (np. `ACME`) i filtr kategorii.

## 5. Co powiedzieć o bezpieczeństwie (30 s)

- Klasyfikacja jest **deterministyczna**; LLM pełni rolę wyłącznie wzmacniacza,
  a podłoga reguł jest wymuszana po jego odpowiedzi.
- `gmail.send`, `payment.execute`, `contract.sign` i inne operacje zobowiązujące
  są na stałej liście zabronionych narzędzi — agent nie może ich nawet zaoferować.
- Tryb demo nie nawiązuje połączeń z Google; klucze i endpointy żyją w `.env`
  (gitignored), dane demonstracyjne są syntetyczne (domeny `.example`, kwoty w PLN).

## 6. Awaryjne sytuacje

| objaw | reakcja |
|---|---|
| puste ekrany | ponowny `POST /api/demo/reset` |
| mikrofon milczy | czy to `localhost`; czy `/api/health` pokazuje `stt: ready`; nagranie ≤ 30 s |
| Eva nie mówi po polsku | dodaj głos wg sekcji 2 albo użyj Chrome/Edge z głosem sieciowym |
| brak odpowiedzi asystenta | sprawdź `POST /api/settings/llm/test`; model self-hosted musi być uruchomiony |
