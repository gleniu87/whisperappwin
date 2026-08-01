# Handoff — stan na 2026-08-01 (sesja 3)

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
| Testy | **378, wszystkie przechodzą** — `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |

Uruchamianie: `.\run.ps1` (z konsolą) albo `.\run.ps1 -Hidden` (tylko tray).
Diagnostyka: `.\run.ps1 -Check`.

---

## NASTĘPNY KROK

**Interfejs jest dwujęzyczny i ma polskie znaki.** Cały tekst UI siedzi teraz
w `whisperdictate/i18n.py`; menu tray, dymek nagrywania i dialogi mówią po polsku
albo po angielsku, przełącznik jest w tray → *Język aplikacji*, a pierwsze
uruchomienie bierze język z Windows (fallback: angielski). Szczegóły niżej.

Otwarte, w kolejności wartości:

0. **Kliknąć nowe menu na żywo.** Zweryfikowane są: renderowanie dymka i okna
   nazw własnych (zrzuty ekranu — polskie znaki wychodzą poprawnie), pełne drzewo
   menu w obu językach (wypisane programowo) i 378 testów. **Nie** klikałem
   prawdziwego menu tray, nie przełączałem języka ani nie ruszałem głównego
   wyłącznika w działającej aplikacji — to pierwsza rzecz do sprawdzenia.
   Przy okazji: w `transcription.vocabulary` użytkownika nadal siedzi
   `Anthropica` (stary wpis, nieszkodliwy — patrz niżej). Zostawiłem go, żeby
   miał na czym sprawdzić nowe *Usuń zaznaczoną*; jego config to jego dane.

1. **Zweryfikować ratunek na żywo.** *Skopiuj ostatnią transkrypcję* jest
   zrobione i pokryte testami (w tym integracyjnym na prawdziwym schowku), ale
   nie klikane w działającej aplikacji. Scenariusz: podyktuj cokolwiek do okna
   bez pola tekstowego (np. pulpit), potem menu tray → *Skopiuj ostatnią
   transkrypcję* → `Ctrl+V` w edytorze. Przy okazji `Win+V`: ta pozycja **ma**
   tam być widoczna, samo dyktowanie **nie**.
2. **Anthropic API wciąż nieprzetestowane** — nadal brak klucza. Jedyny provider
   bez ani jednego realnego wywołania. Gdy klucz się pojawi:
   `.\run.ps1 -SetApiKey anthropic`, potem ten sam benchmark.
3. **Zdecydować, czy włączyć czyszczenie na stałe** i z jakim providerem.
   Dane do decyzji są już zebrane (tabela niżej) — brakuje tylko wyboru
   użytkownika. Nie zmieniaj domyślnego „wyłączone" za niego.
4. **Tryb `-Hidden`** (pythonw) i **tryb `toggle`** — nadal nietknięte na żywo.

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
  podpisane bieżącym wyborem, plus podmenu *Hotkey* i pozycja *Nazwy własne...*.
  Wybór modelu DeepSeeka (flash/pro) istniał wcześniej, ale ginął w płaskiej
  liście dziewięciu pozycji — użytkownik go nie znalazł. To była wada UI,
  nie brak funkcji.
- **Polskie znaki w dymku nagrywania — zweryfikowane na pikselach.** Zrzuty
  ekranu prawdziwego okna Tk pokazują „Ładuję model...", „Czyszczę tekst..."
  i detal `zażółć gęślą jaźń`. Skrypt: utwórz `Overlay` na withdrawn roocie,
  `set_state()`, kilka `root.update()`, potem `ImageGrab.grab()` po
  `winfo_rootx/rooty`. Testy tego nie łapią — to ta sama luka, przez którą
  w sesji 2 okno dialogowe zostało 1×1 w rogu ekranu.
- **Detekcja języka na tej maszynie**: `GetUserDefaultUILanguage()` → `0x415`
  (pl-PL) → `pl`. Zgadza się z oczekiwaniem.

## Czego NIE zweryfikowano na żywo (nowe w tej sesji)

- **Prawdziwe menu tray po zmianach** — labelki sprawdzone programowo i testami,
  ale nieklikane. Dotyczy zwłaszcza *Język aplikacji*: przełączenie przebudowuje
  całe menu (`Tray._rebuild_menu`), a `update_menu()` na żywej ikonie to jedyna
  ścieżka, której test nie odtwarza w pełni.
- **Dymek powiadomienia (balloon) z polskimi znakami** — idzie przez
  `Shell_NotifyIconW`, więc powinno być dobrze, ale nie widziałem tego na oczy.

- **Priming Whispera słownikiem** — połowa promptowa jest zmierzona (niżej),
  ale wpływ na **rozpoznanie** nie. Wymaga nagrania. To pierwsza rzecz do
  sprawdzenia: wpisz nazwy, podyktuj zdanie z „DeepSeek", porównaj `raw_text`
  w `history.jsonl` przed i po.
- **Dymek z propozycją po dyktowaniu** — kod jest, ale nie widziałem go na oczy.
  Wymaga realnego dyktowania, w którym model coś poprawi.
- **Zmiana hotkeya w locie** (`HotkeyListener.set_key`) — pokryta testami
  jednostkowymi, nieklikana w prawdziwym trayu.
- **Dialog *Nazwy własne...*** — od sesji 3 **przetestowany zrzutem ekranu**, nie
  tylko zbudowany: skrypt tworzy okno na withdrawn roocie, wpisuje nazwę, wysyła
  `<Return>`, zaznacza pozycję, klika *Usuń zaznaczoną* i łapie `ImageGrab.grab()`
  po każdym kroku. Da się to zrobić przez `root.after()` przed `wait_window()` —
  „nietestowalny headless" było za mocne. Logika (add/remove) ma normalne testy;
  ta droga jest do oglądania layoutu.

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
deklaruje jurysdykcję (`registry.hosting()`, od sesji 3 z katalogu tłumaczeń),
widoczną w `--check`, w oknie klucza i w README. **Nie chowaj tego.**

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

**Okno Tk parentowane do withdrawn roota MUSI mieć `transient()` warunkowo.**
`root` aplikacji jest withdrawn (nie ma okna głównego). `Toplevel` z
`transient(root)` na takim roocie **nigdy się nie mapuje** — zostaje 1×1 w rogu
i użytkownik nic nie widzi. Kolejność: `transient` tylko gdy
`root.winfo_viewable()`, potem `deiconify()`, `wait_visibility()`, `grab_set()`.
Tak robi `tkinter.simpledialog` i dlatego tamte dialogi działały, a mój własny
nie. Wyszło dopiero przy zrzucie ekranu — testy tego nie łapią.

**`add()` scala formy odmienione na podstawową.** Bez tego w słowniku lądują
`Anthropic` i `Anthropica` obok siebie — zdarzyło się naprawdę. Różnica ze spacją
(`Claude` vs `Claude Code`) to inne słowo, nie końcówka, i musi zostać dodane.

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

**Słownik ma dwa źródła i one się ŁĄCZĄ, nie zastępują.** `vocabulary.txt`
w repo (wersjonowane nazwy techniczne) + `transcription.vocabulary` w `%APPDATA%`
(prywatne: klienci, projekty, osoby — poza gitem). Każde czytanie słownika idzie
przez `vocabulary.combined()`. Jeśli gdzieś zobaczysz gołe
`config.get("transcription.vocabulary")` w ścieżce, która karmi Whispera albo
prompt — to błąd, gubi połowę listy.

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

**Menu tray nie ma linii statusu i nie ma jej odzyskiwać.** Była to jedna
nieaktywna pozycja ze stanem, modelem i providerem w jednym zdaniu. Windows
rozciąga popup do najdłuższej pozycji, więc przy włączonym czyszczeniu robiła
z menu pas przez pół ekranu — zgłoszone przez użytkownika. Wszystko, co mówiła,
jest gdzie indziej: stan w kolorze ikony i w tooltipie, wybory w podpisach
podmenu (`Model: {name}`, `Hotkey: {key}`, `Provider: {name}`). Pilnuje tego
`test_the_menu_carries_no_status_line` i `test_no_top_level_entry_is_absurdly_wide`.

**Główny wyłącznik jest odwrotnością `paused` i to jest celowe.** W menu jest
„Włącz dyktowanie" zaznaczone, gdy działa; kontroler mówi `set_paused` /
`State.PAUSED`. Nie ujednolicaj jednej strony do drugiej: stan maszyny nazywa się
„wstrzymane", a menu proponuje włączenie czegoś — to dwie połowy tego samego
faktu i każda jest czytelna po swojej stronie. Wyłącznik **nie** wyładowuje modelu
ani nie zdejmuje hooka; o to prosił użytkownik („żeby nie trzeba było zamykać
aplikacji"). Stara pozycja „Wstrzymaj dyktowanie" zniknęła — dwa przełączniki na
jeden stan, w przeciwnych fazach, to była pułapka.

**Słownik nazw własnych: dodawanie po jednej, nie jedna linia z przecinkami.**
Poprzednie okno to był `askstring` wypełniony całą listą jako jeden string. Przy
trzech nazwach działało, przy dziesięciu przestało: w polu na 400 znaków nic nie
widać, usunięcie wpisu to edycja wokół przecinków, a Enter zapisuje to, co
w linii akurat jest. Użytkownik zgłosił to sam („to tak spuchnie"). Teraz:
wpisz nazwę → Enter → ląduje na liście, pole się czyści; zaznacz → *Usuń
zaznaczoną*. Okno pokazuje **tylko część prywatną**, bo `vocabulary.txt` jest
wersjonowany i dialog nie ma prawa robić commita za użytkownika — pod listą jest
licznik nazw wspólnych i nazwa pliku, żeby było wiadomo, skąd biorą się pozostałe.

**`vocabulary.remove()` musi dopasowywać tak samo jak `add()`.** `add()` scala
`Anthropica` na `Anthropic`; gdyby `remove("Anthropic")` zostawiało odmienioną
kopię, nazwa wróciłaby do primingu po tym, jak użytkownik ją usunął. Pilnuje tego
`test_removing_the_stem_takes_its_inflections_too`.

**Skąd wzięło się `Anthropic` w oknie i `Anthropica` w configu (sesja 3).**
Realne zgłoszenie, warto znać oba źródła. `Anthropic` jest w `vocabulary.txt`
(wspólny, zaseedowany przeze mnie w sesji 2) — idzie do primingu, ale **nie**
pokazuje się w oknie, bo okno pokazuje tylko część prywatną. `Anthropica`
siedziało w `transcription.vocabulary` w `%APPDATA%`: zapisane przez UI (log,
`Slownik nazw wlasnych: 1 pozycji`), przy pustym `vocabulary_rejected`, czyli
**nie** przez „Nigdy o to nie pytaj". Najprawdopodobniej Enter w dialogu
propozycji — `<Return>` jest tam podpięty pod „Dopisz", a przycisk jest
`default="active"`. Zostawiłem to tak: dialog otwiera się z menu świadomie
i „dodaj" jest właściwą domyślną akcją, a od sesji 3 zły wpis da się usunąć
jednym kliknięciem. Sam wpis był nieszkodliwy — `combined()` scalał go na
`Anthropic` ze wspólnego pliku (zweryfikowane sondą), więc do Whispera nigdy
nie trafił.

**`refresh_vocabulary_suggestions()` łapie wyjątki z całego skanu, nie tylko
z czytania pliku.** `history.recent()` zwraca to, co `json.loads` zrobił z każdej
linii — uszkodzona historia może się sparsować do liczby albo listy, na czym
`pending()` wywala `AttributeError`. To leci z `_offer_vocabulary` na samym końcu
udanego dyktowania, więc tekst by się wkleił, a chwilę później overlay pokazałby
„błąd wewnętrzny". Znalezione przy pisaniu testu, nie na żywo.

**Dyktowania są wykluczone z historii schowka Windows i to nie jest to samo co
`restore_clipboard`.** Użytkownik zgłosił, że schowek się zaśmieca, mimo że
`restore_clipboard = true` od początku przywraca poprzednią zawartość. Przywrócenie
**nie usuwa wpisu z Win+V** — Windows zapisuje każdą zmianę w momencie, w którym
się dzieje. Dlatego `output.clipboard_history = false` (domyślnie) dokłada do
schowka dwa zarejestrowane formaty, `CanIncludeInClipboardHistory` i
`CanUploadToCloudClipboard`, oba jako DWORD 0 — ten sam mechanizm, z którego
korzystają menedżery haseł. Muszą być ustawione **w tej samej sesji schowka** co
tekst (między `EmptyClipboard` i `CloseClipboard`), inaczej nie dotyczą niczego.
Przywracana zawartość jest oznaczana tak samo, bo inaczej każde dyktowanie
dorzucałoby do Win+V duplikat wpisu użytkownika. Zweryfikowane na prawdziwym
schowku (`tests/test_output.py`, integracyjne — mock przyjąłby DWORD zapisany
odwrotnie i nikt by tego nie zauważył).

**Nie zamieniaj wklejania na wpisywanie znak po znaku.** Rozważone i odrzucone
razem z użytkownikiem, gdy pytał o wyłączenie schowka: przy `SendInput` każda nowa
linia w transkrypcji staje się Enterem, czyli wysyła w połowie wiadomość w Slacku
i Teams, 900 znaków wpisuje się wyraźnie dłużej niż wkleja, a autouzupełnianie
aplikacji wchodzi w drogę. Wykluczenie z historii daje to, o co chodziło
(czysty Win+V), bez żadnego z tych ryzyk.

**Dymek nagrywania pozycjonuje Win32, nie Tk.** `winfo_screenwidth()` to monitor
**główny**, a początek układu Tk to zawsze 0,0 — więc na dwóch monitorach dymek
zawsze wychodził na głównym, niezależnie od tego, gdzie użytkownik pisał. Teraz
`GetForegroundWindow` → `MonitorFromWindow(MONITOR_DEFAULTTONEAREST)` → `rcWork`.
Zmierzone na tej maszynie: aktywne okno na monitorze `(-1920, 0, 0, 1040)`, czyli
**na lewo od głównego** i z paskiem zadań; stary kod dawał `x=810` (monitor
główny), nowy `x=-1110`. Tk przyjmuje ujemny offset zapisany jako `+-1920+100`
i stawia okno dosłownie tam — sprawdzone przez `winfo_rootx()`, nie założone.
Arytmetyka siedzi w `position_in()` osobno od wywołań Win32, żeby dała się
testować; `rcWork` zamiast `rcMonitor`, żeby ominąć pasek zadań na tym konkretnym
ekranie. Pozycja jest przeliczana przy każdym **pokazaniu** dymka, nie w trakcie —
inaczej okno goniłoby aktywne okno po ekranach w środku nagrania.

**Historia NIE rośnie w nieskończoność i nie dorabiaj do niej kasowania po
czasie.** Użytkownik pytał, słusznie, czy `history.jsonl` urośnie do gigabajtów.
Nie: `history.py` przycina plik do `history.max_entries` (domyślnie 5000) co 100
dopisów, od pierwszej wersji. Zmierzone na jego realnych danych: mediana wpisu
667 B, średnia 871 B, max 3165 B → **sufit ~4,2 MB** przy domyślnym limicie
(2000 → ~1,7 MB, 1000 → ~0,83 MB), a parsowanie pełnych 5000 wpisów to 23 ms.
Zanim dołożysz kasowanie po dacie albo okno ustawień, powtórz ten pomiar —
przy tych liczbach to byłby kod bez powodu. **Nie schodź z limitem poniżej
~1000:** po każdym dyktowaniu skanowane jest 300 ostatnich wpisów
(`controller._SUGGESTION_WINDOW`), a `--suggest-vocabulary` czyta 1000, więc
mniejszy limit tnie propozycje słownika, nie tylko rozmiar pliku.

**„Skopiuj ostatnią transkrypcję" celowo NIE jest wykluczone z Win+V.** Odwrotnie
niż samo dyktowanie (`clipboard_history = false`): to użytkownik kopiuje świadomie,
więc wpis w historii schowka jest tym, czego chce. Pilnują tego dwa testy
patrzące w przeciwne strony — `test_rescue.py::test_and_is_visible_to_the_clipboard_history`
i `test_output.py::test_deliver_excludes_by_default`.

**Ratunek zamiast guarda przed wklejeniem — decyzja, nie zaniechanie.**
Użytkownik pytał o ostrzeżenie „nie masz sfokusowanego pola tekstowego, tekst
zniknie". Nie da się tego wiarygodnie stwierdzić w Electronie (Teams, VS Code,
przeglądarka): całe okno to jeden HWND, `hwndCaret` zwykle puste. Ostrzeżenie
sypiące fałszywymi alarmami w najczęściej używanych aplikacjach przestaje być
czytane, więc zamiast wróżby przed faktem jest odzysk po fakcie z `history.jsonl`.
Jeśli kiedyś sięgniesz po UI Automation (`GetFocusedElement`, `IsTextPatternAvailable`),
najpierw **zmierz** to na Teams i VS Code, nie na Notatniku.

**`_last_transcription()` odmawia przy wyłączonej historii, zamiast czytać plik.**
`History.append` nic nie robi, gdy `enabled = False`, więc najnowsza linia jest
z czasów, gdy zapis był włączony. Podanie jej jako „ostatniej transkrypcji" to
zła odpowiedź udająca dobrą.

**Wygląd okien: `tk.Entry` i `tk.Listbox` NIE MAJĄ opcji `padx`/`pady`.**
Sprawdzone: `'padx' in widget.keys()` → `False` dla obu. Dlatego pole to
`tk.Frame` z `highlightthickness=1` (biały, z 1-pikselową ramką w wybranym
kolorze), a bezramkowy widget siedzi w nim z paddingiem. Nie „upraszczaj" tego
z powrotem do gołego `Entry` — użytkownik zgłosił dokładnie ten objaw: kursor
i tekst przyklejone do krawędzi. Do tego `insertwidth=1` (Tk domyślnie ma 2,
Windows rysuje 1) i `ttk` zamiast `tk` na przyciski i etykiety, żeby motyw
`vista` dał natywny wygląd i systemowy font zamiast zaszytego „Segoe UI 10".
Zmierzone po zmianie: **kursor 1 px, odstęp od krawędzi pola 7 px** (było 2 px
i 0 px). **DPI nie było przyczyną** — `GetDpiForMonitor` po
`SetProcessDpiAwareness(2)` w osobnym procesie zwraca 96 dla obu monitorów.

**Wiersz statusu w oknie nazw własnych ma zawsze `text=" "`.** Wygląda na
przeoczenie, nie jest nim: komunikat o duplikacie pojawia się w trakcie pisania,
a puste `text=""` zwijałoby wiersz i przestawiało okno pod ręką użytkownika.

**Geometria monitorów siedzi w `ui/screens.py`, nie w `overlay.py`.** Potrzebują
jej dwie rzeczy (dymek nagrywania i okna dialogowe), a `dialogs` nie ma po co
importować całego overlaya. `position_in()` dla dymka, `centre_in()` dla okien —
oba czyste, obok nieczystego `focused_work_area()`.

**Prompt zawiera polskie słownictwo przerywników.** Prompt po angielsku wycina
„um" i zostawia „no więc yyy" nietknięte. To rdzeń, nie tłumaczenie.

**Brak polskich znaków w tray NIE był problemem czcionek ani kodowania.** Tak to
zgłosił użytkownik i tak to wyglądało, ale przyczyną były gołe stringi w źródłach:
`"Laduje model"`, `"Nazwy wlasne..."`. pystray na Windows używa `InsertMenuItemW`,
`Shell_NotifyIconW` i `MENUITEMINFO.dwTypeData` jako `LPCWSTR` — wszystko
szerokoznakowe (sprawdzone w źródłach pakietu). Tk rysuje diakrytyki bez żadnej
konfiguracji (sprawdzone zrzutem ekranu). **Jeśli kiedyś znowu zobaczysz „?"
zamiast „ą", szukaj w stringu, nie w czcionce.**

**`ui.language` i `transcription.language` to dwa osobne ustawienia.** Kuszące
jest zlanie ich w jedno; nie rób tego. Dyktowanie po angielsku z polskim menu jest
normalne, a `transcription.language` przyjmuje dodatkowo `auto`, które dla
interfejsu nie ma znaczenia. Oba przełączniki stoją w menu **obok siebie celowo**
— pojedyncza pozycja „Język" jest dokładnie tym, co wcześniej mieszało jedno
z drugim.

**Log i wyjście CLI zostają po polsku — to granica, nie przeoczenie.** Czyta je
ten, kto diagnozuje. Gdyby weszły do katalogu tłumaczeń, spuchłby kilkukrotnie bez
zysku dla użytkownika. Wyjątkiem są komunikaty wyjątków, które **trafiają do
dymka** (`AudioError`, `TranscriptionError`, `ClipboardError`, `fallback_note`) —
te są tłumaczone, bo widzi je użytkownik. `ProviderError` zostaje po polsku, bo
idzie wyłącznie do logu.

**`t()` ma `key` jako parametr pozycyjny (`def t(key, /, **fields)`).** To nie
ozdoba: wpis `menu.hotkey` ma placeholder `{key}`, więc bez `/` wywołanie
`t("menu.hotkey", key=...)` wywala się na „got multiple values for argument 'key'".
Złapały to testy przy pierwszym uruchomieniu. Nie „upraszczaj" sygnatury.

**Menu *Język aplikacji* pokazuje endonimy („Polski", „English") w obu
katalogach.** Wygląda na niedokończone tłumaczenie, nie jest nim: ktoś, kto
przełączył się na język, którego nie czyta, musi umieć wrócić.

**`credentials.source()` zwraca `env` / `store` / `none`, nie zdanie.** Dialog
klucza rozgałęzia się na tej wartości, a na przetłumaczonym zdaniu rozgałęzić się
nie da. Do pokazania człowiekowi jest `describe_source()`.

**`ProviderSpec` nie trzyma już `hosting` ani podpowiedzi w `label`.** Została
naga nazwa produktu („DeepSeek API"), bo ta jest identyczna w każdym języku.
Podpowiedź i jurysdykcję dają `registry.label_with_hint()` i `registry.hosting()`.
Deklaracja jurysdykcji **nadal jest widoczna** w `--check`, w oknie klucza i
w README — pilnuje tego `test_hosting_note_flags_the_jurisdiction_in_both_languages`,
który sprawdza „Chiny" po polsku i „China" po angielsku.

**Pierwsze uruchomienie pyta Windows o *język wyświetlania*, nie o region.**
`GetUserDefaultUILanguage()`, nie `locale.getlocale()`. To odpowiedź na pytanie
„w jakim języku ten człowiek czyta oprogramowanie"; region to ustawienie formatów
i na angielskim Windows z polskim regionem dałby polski, choć człowiek świadomie
ustawił jedno i drugie. Locale jest konsultowane **tylko** wtedy, gdy języka
wyświetlania nie da się odczytać (nie-Windows, brak ctypes). Rozstrzyga to
`test_the_display_language_outranks_the_locale`.

**`i18n.py` nie importuje niczego z pakietu i tak ma zostać.** Czytają z niego
`config`, `audio`, `transcriber`, `output` i `enhance/` — jeden import w drugą
stronę robi cykl w miejscu, w którym najtrudniej go zauważyć.

**Wykryty język jest zapisywany do configu, nie zgadywany przy każdym starcie.**
Świeży config dostaje go od razu; config z poprzedniej wersji (bez `ui.language`)
dostaje jednorazowy zapis przy najbliższym starcie. Dzięki temu w pliku widać, co
aplikacja robi. `test_a_config_that_already_has_the_setting_is_not_rewritten`
pilnuje, żeby to nie zamieniło się w zapis przy każdym wczytaniu.

**Testy traya MUSZĄ przypinać `ui.language`.** Świeży config bierze język
z Windows, więc na angielskiej maszynie każda asercja na polską labelkę padłaby.
`setUp` ustawia `pl` i przywraca poprzedni język przez `addCleanup` — język jest
stanem modułu, a jeden z testów świadomie go przełącza.

**Nie twórz w testach obiektów `pystray.Icon`, których nikt nie trzyma.** pystray
nazywa klasę okna Win32 `"<name><id(icon)>SystemTrayIcon"` i odrejestrowuje ją
tylko dla ikony, która faktycznie *działała* — w testach nigdy. Zebrana ikona
zwalnia adres, nowa może na nim wylądować, dostać tę samą nazwę klasy i wywalić
się na `ERROR_CLASS_ALREADY_EXISTS` — w losowym teście, nie w tym, który to
spowodował. Kosztowało jedną fałszywą diagnozę. Dlatego drugi tray w
`test_configured_but_absent_device_...` wisi na `self._extra_tray`.

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
