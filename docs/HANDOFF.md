# Handoff — stan na 2026-08-01 (sesja 2)

Notatka przekazania między sesjami. `README.md` opisuje, jak używać;
`docs/ARCHITECTURE.md` — dlaczego kod wygląda tak, a nie inaczej. Ten plik mówi,
**gdzie jesteśmy i co dalej**.

---

## Czym to jest

Windowsowy odpowiednik macOS-owej aplikacji
[`jacek-gajewski-ice/whisper-app`](https://github.com/jacek-gajewski-ice/whisper-app)
(WhisperDictate) — dyktowanie hold-to-talk, wszystko lokalnie poza opcjonalnym
czyszczeniem tekstu.

Oryginał to Swift + SwiftUI + AVFoundation i **nie ma szans działać na Windows**;
to była pierwsza rzecz, którą ustaliliśmy. Odtwarzamy jego *zachowanie*, nie kod.
Mapowanie warstw jest w `docs/ARCHITECTURE.md`.

Repo referencyjne jest sklonowane w scratchpadzie poprzedniej sesji — jeśli
znowu będzie potrzebne:
`git clone --depth 1 https://github.com/jacek-gajewski-ice/whisper-app`.
Ma własny `CLAUDE.md` z notatkami inżynierskimi Jacka — warto zajrzeć przed
odtwarzaniem kolejnej funkcji.

## Środowisko

| | |
|---|---|
| Repo | `C:\claude_projects\whisperappwin` (git, branch `main`, 8 commitów) |
| Venv | `.\.venv` (Python 3.12.7 z Anacondy jako baza) |
| Sprzęt | i7-12700H, 64 GB RAM, RTX 3070 Ti Laptop (8 GB VRAM) |
| GPU | działa: `large-v3-turbo @ cuda/float16`, ~29× realtime |
| Mikrofon | `audio.device = "Mikrofon (Anker PowerConf C200)"` |
| Testy | **265, wszystkie przechodzą** — `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |

Uruchamianie: `.\run.ps1` (z konsolą) albo `.\run.ps1 -Hidden` (tylko tray).
Diagnostyka: `.\run.ps1 -Check`.

---

## NASTĘPNY KROK

**Benchmark DeepSeeka jest zamknięty** — klucz był już zapisany, benchmark
poszedł, wynik niżej. Przy okazji wyszły trzy usterki, wszystkie naprawione.

Otwarte, w kolejności wartości:

1. **Anthropic API wciąż nieprzetestowane** — nadal brak klucza. Jedyny provider
   bez ani jednego realnego wywołania. Gdy klucz się pojawi:
   `.\run.ps1 -SetApiKey anthropic`, potem ten sam benchmark.
2. **Zdecydować, czy włączyć czyszczenie na stałe** i z jakim providerem.
   Dane do decyzji są już zebrane (tabela niżej) — brakuje tylko wyboru
   użytkownika. Nie zmieniaj domyślnego „wyłączone" za niego.
3. **Tryb `-Hidden`** (pythonw) i **tryb `toggle`** — nadal nietknięte na żywo.

`getpass` działa poprawnie w prawdziwym oknie PowerShell. **Nie** działa pod Git
Bash (`!` w sesji Claude Code idzie przez bash) — tam potrafi wypisać klucz na
ekran, dlatego `--set-api-key` czyta ze stdin, gdy stdin nie jest tty.

### Co rozstrzygnął benchmark

Tekst testowy (przerywniki, autopoprawka `handler znaczy parser`, identyfikator
`user_id`, którego model nie powinien ruszyć):

```powershell
.\run.ps1 -Benchmark "no wiec yyy wez sprawdz czy ten handler znaczy ten parser user_id sie nie wywala na pustym stringu bo jakby mi sie wydaje ze tam jest blad"
```

- **Opóźnienie DeepSeeka z Polski: ~1,5 s** (flash 1,62 s, pro 1,48 s). To było
  główne pytanie i odpowiedź jest korzystna — 10× taniej *bez* płacenia czasem.
- **Polski: dobry.** Oba modele wycięły przerywniki, zastosowały autopoprawkę
  i **zostawiły `user_id` nietknięte**. Pro trzyma „Weź" (bliżej tonu mówiącego),
  flash częściej je usuwa.
- **Claude CLI: bierz Sonneta.** Zmierzone 5 razy: haiku-4-5 19,8–60+ s (dwa razy
  timeout), sonnet-5 stabilnie 4,2–5,7 s. Narzut CLI dominuje nad modelem, więc
  „mniejszy model = szybciej" tu nie obowiązuje. Domyślny model CLI zmieniony na
  Sonneta (`registry.py`).

---

## Co jest zrobione i zweryfikowane na żywo

- **Dyktowanie end-to-end** — działa, potwierdzone przez użytkownika. Hotkey →
  nagranie → Whisper na GPU → wklejenie.
- **Hold-to-talk na prawym Alcie** współistnieje z AltGr: hook nic nie
  przechwytuje, próg 300 ms, litera w trakcie anuluje gest. Modyfikatory są
  wyłączone z anulowania, bo Windows przy AltGr wysyła syntetyczny lewy Ctrl.
- **Wybór mikrofonu w tray**, zapisywany po nazwie (indeksy PortAudio się
  przesuwają), z fallbackiem gdy urządzenie zniknie.
- **Fallback między host API** — WASAPI → DirectSound → MME. Zweryfikowany
  z zsabotowanym WASAPI: schodzi na DirectSound i łapie realny dźwięk.
- **Czyszczenie tekstu przez LLM** — zweryfikowane na żywo przez Claude Code CLI
  na prawdziwym dyktowaniu użytkownika. Wycięło przerywniki, zachowało sens.
- **Menu tray** — wszystkie pozycje wywołane programowo tak, jak robi to pystray.

- **DeepSeek na żywo** — oba modele, wielokrotnie, przez benchmark i przez sondy
  bezpośrednio na API. Działa, ~1,5 s, poprawna polszczyzna.
- **Czyszczenie włączone na stałe przez użytkownika** (`enabled = true`,
  `claude_cli` / `claude-sonnet-5`) i przetestowane na jego realnym dyktowaniu
  938 znaków. Sonnet poprawił przekręcone przez Whispera nazwy własne
  (`Dipsick` → `Deepseek`, `sonet` → `Sonnet`) i podzielił wypowiedź na akapity.
- **`--quality`** — porównanie jakościowe na 12 trudnych transkrypcjach,
  uruchomione na wszystkich czterech gotowych kombinacjach provider/model.
- **Menu tray przebudowane** — Provider / Model / Styl jako osobne podmenu
  podpisane bieżącym wyborem, plus podmenu *Hotkey* i pozycja *Nazwy wlasne...*.
  Wybór modelu DeepSeeka (flash/pro) istniał wcześniej, ale ginął w płaskiej
  liście dziewięciu pozycji — użytkownik go nie znalazł. To była wada UI,
  nie brak funkcji.

## Czego NIE zweryfikowano na żywo (nowe w tej sesji)

- **Priming Whispera słownikiem** — połowa promptowa jest zmierzona (niżej),
  ale wpływ na **rozpoznanie** nie. Wymaga nagrania. To pierwsza rzecz do
  sprawdzenia: wpisz nazwy, podyktuj zdanie z „DeepSeek", porównaj `raw_text`
  w `history.jsonl` przed i po.
- **Dymek z propozycją po dyktowaniu** — kod jest, ale nie widziałem go na oczy.
  Wymaga realnego dyktowania, w którym model coś poprawi.
- **Zmiana hotkeya w locie** (`HotkeyListener.set_key`) — pokryta testami
  jednostkowymi, nieklikana w prawdziwym trayu.
- **Dialog *Nazwy wlasne...*** — wymaga dispatchera Tk, nietestowalny headless.

## Czego NIE zweryfikowano

- **Anthropic API jako provider czyszczenia** — brak klucza, nigdy nie odpalone
  na żywo. Ścieżka kodu jest ta sama co DeepSeeka (`MessagesApiProvider`), więc
  benchmark pokryje ją, jeśli klucz się pojawi. Uwaga: `_THINKS_BY_DEFAULT`
  zakłada, że Haiku 4.5 nie myśli bez proszenia — **to założenie z tej samej
  rodziny, co obalone założenie o DeepSeeku**. Sprawdź je, gdy będzie klucz.
- **Tryb `-Hidden`** (pythonw, bez konsoli) — nieprzetestowany.
- **Tryb `toggle`** hotkeya — pokryty testami, nieużywany na żywo.

---

## Decyzje, które łatwo przypadkiem cofnąć

Każda z nich kosztowała diagnozę. Nie zmieniaj ich bez powodu.

**Czyszczenie tekstu jest domyślnie WYŁĄCZONE.** Tak jak w oryginale
(`Helpers.swift`: `enhanceTranscription = false`). To nie przeoczenie.

**Użytkownik świadomie rozdziela zastosowania:** DeepSeek tylko do prywatnych
projektów, do treści służbowych czyszczenie wyłączone albo przez Claude Code CLI
(idzie przez jego subskrypcję, żaden trzeci podmiot). Dlatego każdy provider
deklaruje jurysdykcję (`registry.ProviderSpec.hosting`), widoczną w `--check`,
w oknie klucza i w README. **Nie chowaj tego.**

**`None` ≠ pusty string w `EnhancementService.enhance()`.** `None` = „wklej
surowy transkrypt" (awaria). Pusty = sentinel `EMPTY` = „to był sam szum, nie
wklejaj nic". Zlanie tego w jedno sprawi, że padnięte API będzie wyglądać jak
cisza.

**Provider nigdy nie zwraca pustego stringa — rzuca `ProviderError`.** To druga
połowa reguły wyżej i została dopisana dopiero po tym, jak zdarzyła się na żywo:
`deepseek-v4-pro` odpowiedział samym blokiem `thinking`, ze `stop_reason=end_turn`,
czyli formalnie sukcesem. Kod skleił „żadnych bloków text" w `""`, co dalej znaczy
`EMPTY` — **dyktowanie zniknęłoby bez śladu w logu**. Dotyczy obu providerów:
odpowiedzi API bez bloku `text` i pustego `stdout` z CLI przy kodzie wyjścia 0.
Pilnują tego `EmptyReplyTest` i `CliEmptyOutputTest`.

**Modele DeepSeeka MYŚLĄ, jeśli im tego nie zabronić.** Wcześniejszy komentarz
w `_THINKS_BY_DEFAULT` twierdził inaczej — to było założenie, nie pomiar, i było
fałszywe: `thinking` pojawił się w 4/4 sondach. Na `v4-flash` rozumowanie zjadało
cały budżet 1024 tokenów (`stop_reason=max_tokens`, zero tekstu) w **100%**
wywołań, więc flash nie działał w ogóle. Z `thinking: {"type": "disabled"}` —
6/6 czystych odpowiedzi w 35–44 tokenach. Nie usuwaj tych modeli ze zbioru.

**Domyślny model `claude_cli` to Sonnet, nie Haiku.** Wygląda na pomyłkę, nie jest
nią — patrz pomiary wyżej. Odwrócenie kolejności w `registry.py` sprawi, że
domyślny wybór CLI będzie tym, który regularnie przekracza limit 60 s.
Użytkownik potwierdził ten wybór wprost.

**Domyślny hotkey to prawy Ctrl, nie prawy Alt.** Oryginał miał prawy Alt i były
do tego trzy mechanizmy obronne (brak przechwytywania, próg 300 ms, anulowanie na
literę) — opisane w README i nadal działające. **Nie wystarczyły w praktyce:**
użytkownik zgłosił, że przy pisaniu „ą" nagrywanie i tak się włącza. Próg łapie
szybkie naciśnięcia, ale nie zawahanie. Przy klawiszu naciskanym kilkadziesiąt
razy na akapit „prawie zawsze" jest za mało. `alt_r` zostało jako opcja — na
układzie *Polski (214)* problemu nie ma. To zmiana wynikająca z użytkowania,
nie z pomiaru; nie cofaj jej bez rozmowy z użytkownikiem.

**Słownik nazw własnych idzie w DWA miejsca, nie w jedno.** `transcription.vocabulary`
→ `initial_prompt` Whispera **oraz** sekcja w prompcie czyszczenia
(`vocabulary.py`). Kuszące jest uprościć to do jednego; nie rób tego. Sam priming
czasem nie zadziała, a sama naprawa po fakcie zostawia przekręcony tekst
w `history.jsonl` i nie działa przy wyłączonym czyszczeniu. Sekcja promptu ma
jawny zakaz dopisywania nazw z listy — bez niego model wstawia je tam, gdzie ich
nie było.

**Propozycje słownika NIGDY nie dopisują się same.** Próg to jedno wystąpienie —
i jest bezpieczny wyłącznie dlatego, że filtrem jest użytkownik. Gdyby wpisy
lądowały w słowniku bez potwierdzenia, błędny wpis trafiłby do `initial_prompt`
i psuł **wszystkie** przyszłe dyktowania, w dodatku u źródła. Nie „usprawniaj"
tego na automatyczne dodawanie.

**Sekcja słownika w prompcie każe odmieniać „po polsku" i tak ma zostać.**
Wygląda na przeoczenie z czasów, gdy aplikacja była tylko polska — użytkownik
dyktuje też po angielsku, więc instrukcja jest tam bez sensu. **Przepisałem ją
na neutralną językowo i zmierzyłem: jest gorzej.** Polski spadł z 14/16 na 9/16
przy odtwarzaniu `Sonnet` (deepseek-v4-flash, ten sam tekst), angielski 8/8
w obu wersjach. Angielski nie potrzebował naprawy, bo `language_directive` jest
doklejany PO tej sekcji i ją nadpisuje. Cofnięte. Pilnuje tego
`test_section_keeps_the_polish_declension_cue`. Nie „naprawiaj" tego bez
powtórzenia pomiaru.

**Jeden słownik na oba języki, nazwy w formie podstawowej.** Model odmienia sam
(`DeepSeek` → „na DeepSeeka" po polsku, „to DeepSeek" po angielsku). Dwóch list
nie da się rozsądnie zrobić: `transcription.language` przyjmuje `auto`, więc przy
autodetekcji nie wiadomo, którą wybrać przed transkrypcją.

**`_fold()` ręcznie mapuje `ł` → `l`.** To nie jest nadgorliwość: `ł` (U+0142) to
atomowy codepoint **bez dekompozycji NFD**, w przeciwieństwie do ą, ć, ę, ń, ó, ś,
ź, ż. Bez tej mapy „ustawilem" i „ustawiłem" porównują się jako różne słowa,
co rozjeżdża dopasowanie sekwencji i gubi prawdziwą poprawkę w tym samym zdaniu.
Kosztowało trzy padnięte testy, zanim to zobaczyłem.

**Domyślny model DeepSeeka to Flash — na wyraźne życzenie użytkownika.** Powód,
który podał (Pro wolniejszy), **nie potwierdził się w pomiarach**: Pro wyszedł
1,54 s przeciw 1,60 s Flasha, czyli szybciej. Powiedziałem mu to; wybór Flasha
i tak jest zasadny ze względu na tokeny i to jego decyzja. Nie zmieniaj jej,
ale nie powielaj też uzasadnienia „Pro jest wolniejszy" — jest nieprawdziwe.

**Jakościowo Pro > Flash, mimo że oba dają 0 błędów w jednym przebiegu.**
Różnica wychodzi dopiero na powtórzeniach ×5: Flash zwrócił tę samą nazwę własną
jako `DeepSick` / `Deepsika` / `DeepSeeka` i raz zamienił znaczące „jakby" na
„jakieś" (4/5); Pro pięć razy to samo. **Pojedynczy przebieg `--quality` nie
rozstrzyga o modelu** — przy takim porównaniu powtarzaj najtrudniejsze przypadki.

**`CredWrite` chce `str`, `CredRead` zwraca `bytes`.** API jest asymetryczne.
Zweryfikowane empirycznie, pokryte `tests/test_credentials.py` (test
integracyjny — mock by tego nie złapał).

**Akcje menu pystray to domknięcia, nigdy `lambda x=wartosc:`.** pystray wybiera
sposób wywołania po `__code__.co_argcount`, a argument domyślny się wlicza — taka
lambda dostanie obiekt `Icon` zamiast wartości. Pilnuje tego
`tests/test_tray_menu.py`.

**Otwieranie strumienia audio musi obejmować `start()`.** WASAPI waliduje leniwie:
`InputStream(...)` przechodzi, a `.start()` odrzuca. To był realny błąd
produkcyjny (`118fe29`).

**Prompt zawiera polskie słownictwo przerywników.** Prompt po angielsku wycina
„um" i zostawia „no więc yyy" nietknięte. To rdzeń, nie tłumaczenie.

---

## Zmierzone liczby (nie zgaduj ponownie)

| | |
|---|---|
| Whisper `large-v3-turbo` na CUDA | ~29× realtime, ładowanie z cache 2.9 s |
| Claude Code CLI, trywialny prompt | 6,1 s |
| **DeepSeek V4 Pro**, pełny prompt, z Polski | **1,48 s** |
| **DeepSeek V4 Flash**, pełny prompt, z Polski | **1,62 s** |
| **Claude CLI + Sonnet 5**, pełny prompt | **4,2–5,7 s** (5 prób, stabilnie) |
| **Claude CLI + Haiku 4.5**, pełny prompt | **19,8–60+ s** (5 prób, 2× timeout) |
| Dyktowanie 938 znaków: Sonnet / Flash | 10,2 s / 3,7 s — czas rośnie z długością |
| `--quality`, 12 przypadków, błędy | pro 0, flash 0, haiku 0, sonnet 1 |
| `--quality`, średni czas na przypadek | pro 1,54 s, flash 1,60 s, sonnet 4,25 s, haiku 20,9 s |
| Słownik w prompcie, flash, `Sonnet` poprawnie | **0/3 bez słownika → 2/3 ze słownikiem** |
| Słownik w prompcie, flash, `DeepSeek` poprawnie | 2/3 → 3/3 (mała próbka, kierunek jasny) |
| Kandydaci do słownika z 22 dyktowań | 1, trafiony (po odfiltrowaniu odmiany `alta`→`Altu`) |
| Haiku 4.5 | $1 / $5 za 1M, ~$0.0026 na dyktowanie |
| DeepSeek V4 Flash | $0.14 / $0.28 za 1M, ~$0.00025 na dyktowanie |
| Prompt systemowy `default` + lock `pl` | 3425 znaków, ~1150 tokenów wejścia |
| Min. cache'owalny prefiks Haiku 4.5 | 4096 tokenów — nasz prompt ma ~1100, **cache nie działa** |
| Anker PowerConf, WASAPI | przyjmuje **tylko** 48 kHz; MME/DirectSound łykają wszystko |

## Pułapki środowiska

- Domyślne wejście audio w Windows u użytkownika to **„Virtual Desktop Audio"** —
  urządzenie wirtualne, nagra ciszę. Stąd konieczność jawnego wyboru mikrofonu.
- Windows wystawia 3 fizyczne mikrofony jako **26 pozycji PortAudio** przez
  4 host API. Menu pokazuje tylko WASAPI.
- `!` w sesji Claude Code idzie przez **bash**, nie PowerShell — backslashe giną.
- Katalog roboczy sesji resetuje się między wywołaniami narzędzi; używaj ścieżek
  absolutnych albo `Push-Location`.

## Możliwe kierunki (nic nie jest obiecane)

- Okno z dashboardem (oryginał ma 8 zakładek). Rdzeń nie importuje niczego
  z `ui/` — to trzecia implementacja protokołu `UiSink`.
- Kolejni providerzy czyszczenia (OpenAI, Ollama). `Provider` ma jedną metodę.
- Statystyki z historii — `history.jsonl` ma już `audio_seconds`,
  `elapsed_seconds`, `model`, `language`, `raw_text`.

## Jak pracować z tym użytkownikiem

Weryfikuje i wychwytuje niedoróbki — słusznie. Trzy rzeczy, które się sprawdziły:

- **Mierz, nie zgaduj.** Kilka razy moje założenie („CLI to 6 s", „to mój błąd")
  było błędne, a rozstrzygał dopiero log albo sonda. Log aplikacji
  (`%APPDATA%\WhisperDictateWin\whisperdictate.log`) rozstrzygnął dwie diagnozy.
  W sesji 2 to samo powtórzyło się w gorszej formie: **pewny siebie komentarz
  w kodzie** („modele DeepSeeka nie myślą bez proszenia") był zmyśleniem, które
  zablokowało cały provider. Komentarz opisujący zachowanie cudzego API to
  hipoteza — 20-linijkowa sonda drukująca surową odpowiedź rozstrzygnęła w minutę
  to, czego benchmark nie pokazywał. Konsola pokazywała tylko „NIEUDANE"; log
  i sonda pokazały, że to dwa różne błędy, a jeden z nich cicho gubił tekst.
- **Nie zawężaj mu wyboru.** Zaproponowałem tylko providerów Anthropic i słusznie
  to zakwestionował — DeepSeek jest 10× tańszy. Przy pytaniach o warianty
  uwzględniaj opcje spoza oczywistej ścieżki.
- **Mów wprost, gdy coś jest niesprawdzone.** Docenia „tego nie zweryfikowałem"
  bardziej niż gładkie podsumowanie.
