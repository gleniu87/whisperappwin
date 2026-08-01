# WhisperDictate for Windows

> **Port na Windows aplikacji [WhisperDictate](https://github.com/jacek-gajewski-ice/whisper-app)
> autorstwa Jacka Gajewskiego.** Oryginał działa wyłącznie na macOS. To jest
> odtworzenie jego zachowania na Windows — pomysł, projekt i decyzje produktowe
> pochodzą stamtąd.

Lokalne dyktowanie *hold-to-talk*: przytrzymujesz klawisz, mówisz, puszczasz — tekst
wkleja się tam, gdzie masz kursor. Wszystko liczy się na Twoim komputerze, nic nie
wychodzi do chmury.

## Stosunek do oryginału

**To nie jest fork ani tłumaczenie kodu — to niezależna implementacja tego samego
zachowania.** Nie dało się inaczej: oryginał to Swift + SwiftUI + AVFoundation +
Core Audio, czyli frameworki, których na Windows po prostu nie ma. Żadna linijka
kodu nie została przeniesiona, bo nie było czego przenosić.

Co pochodzi z oryginału:

- **Cały pomysł na produkt** — hold-to-talk, praca lokalna, ikona w zasobniku
  zamiast okna, wklejanie do aktywnej aplikacji.
- **Decyzje produktowe**, łącznie z tymi nieoczywistymi: czyszczenie tekstu przez
  LLM **domyślnie wyłączone** (`Helpers.swift`: `enhanceTranscription = false`),
  sentinel `EMPTY` na ciszę, fail-soft do surowej transkrypcji.
- **Konstrukcja promptu czyszczącego** — `Enhancement/CustomPrompt.swift`, które
  z kolei kredytuje [FreeFlow](https://github.com/zachlatta/freeflow)
  i [VoiceInk](https://github.com/Beingpax/VoiceInk).

Co jest tutejsze, bo musiało być:

| | oryginał (macOS) | tutaj (Windows) |
|---|---|---|
| Silnik | whisper.cpp | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2 |
| Audio | AVFoundation / Core Audio | PortAudio przez `sounddevice`, z fallbackiem WASAPI → DirectSound → MME |
| UI | SwiftUI | pystray + Tk |
| Hotkey | Carbon / NSEvent | pynput, z obsługą kolizji AltGr na polskim układzie |
| Klucze API | Keychain | Menedżer poświadczeń Windows |

Doszło też kilka rzeczy, których oryginał nie ma, bo wynikają z Windows albo
z pracy po polsku: wybór mikrofonu po nazwie (indeksy PortAudio się przesuwają),
polskie słownictwo przerywników w prompcie, słownik nazw własnych zasilający
Whispera i model czyszczący, oraz providerzy DeepSeek i Claude Code CLI.

Na GPU NVIDIA faster-whisper jest szybszy niż whisper.cpp, a na CPU porównywalny.

## Wymagania

| | |
|---|---|
| System | Windows 10 1809+ / Windows 11 |
| Python | 3.10 lub nowszy |
| GPU (opcjonalnie) | NVIDIA z ≥6 GB VRAM dla `large-v3-turbo` |
| Dysk | ~1 GB na zależności + ~1.6 GB na model |

Bez GPU też działa — na nowoczesnym CPU `large-v3-turbo` transkrybuje mniej więcej
w tempie mowy, a mniejsze modele (`small`, `medium`) znacznie szybciej.

## Instalacja

```powershell
git clone <adres-repo> C:\claude_projects\whisperappwin
cd C:\claude_projects\whisperappwin
.\setup.ps1
```

`setup.ps1` tworzy lokalny `.venv`, instaluje zależności, wykrywa kartę NVIDIA i
dociąga do niej biblioteki cuBLAS/cuDNN, a na koniec uruchamia diagnostykę.
Wymuszenie trybu CPU: `.\setup.ps1 -Cpu`.

Pierwsze uruchomienie pobiera model (~1.6 GB) z Hugging Face do
`%LOCALAPPDATA%\WhisperDictateWin\models`. Kolejne startują z dysku.

## Użycie

```powershell
.\run.ps1              # z konsolą (widzisz logi)
.\run.ps1 -Hidden      # w tle, tylko ikona w zasobniku
```

**Przytrzymaj prawy Alt, mów, puść.** Tekst pojawi się w aktywnym oknie.

Ikona w zasobniku pokazuje stan kolorem — szary (gotowy), czerwony (nagrywanie),
niebieski (transkrypcja), żółty (ładowanie modelu). Z jej menu przełączysz język,
model i **mikrofon**, wstrzymasz dyktowanie i otworzysz konfigurację, historię lub log.

### Wybór mikrofonu

Menu **Mikrofon** pokazuje urządzenia WASAPI — po jednym na fizyczny sprzęt.
Windows wystawia ten sam mikrofon przez cztery API (MME, DirectSound, WASAPI,
WDM-KS), więc pełna lista PortAudio potrafi mieć 25 pozycji na 3 mikrofony;
WASAPI to ta z pełnymi nazwami i prawdziwą częstotliwością próbkowania.

Wybór zapisuje się **po nazwie, nie po indeksie** — indeksy PortAudio przesuwają
się przy każdym podłączeniu sprzętu, więc zapisany dziś numer jutro wskazuje inne
urządzenie.

Odłączenie wybranego mikrofonu (np. kamerki USB) nie psuje aplikacji: przy
następnym dyktowaniu przeskanuje sprzęt ponownie, a jeśli urządzenia nadal nie ma
— nagra z domyślnego systemowego i powie Ci o tym powiadomieniem. Urządzenie
zostaje zaznaczone w menu jako *(niepodlaczony)*, żeby było widać, na co aplikacja
czeka. **Odswiez liste** wymusza ponowne wykrycie sprzętu (PortAudio buforuje listę
przy starcie, więc świeżo podłączony mikrofon inaczej się nie pojawi).

### Klawisz dyktowania a polskie znaki

Domyślnie **prawy Ctrl**. Zmienisz w menu tray → *Hotkey*: prawy/lewy Ctrl,
prawy/lewy Alt, Scroll Lock, Pause. W konfiguracji (`hotkey.key`) przejdą też
`f1`–`f20`. Zmiana działa od razu, bez restartu.

**Prawego Alta lepiej nie używać na polskim układzie**, i to jest jedyny powód,
dla którego domyślnym klawiszem nie jest on: na układzie *Polski (programisty)*
prawy Alt **to** AltGr — klawisz, którym piszesz `ą ę ó ś ł ż ź ć ń`. Trzy
mechanizmy łagodzą kolizję:

1. **Nic nie jest przechwytywane.** Hook tylko obserwuje klawiaturę; AltGr dociera
   do aplikacji nietknięty, niezależnie od tego, czy WhisperDictate działa.
2. **Próg przytrzymania (300 ms).** `AltGr+a` to naciśnięcie poniżej 100 ms i nigdy
   nie przekroczy progu. Dopiero świadome przytrzymanie startuje nagrywanie.
3. **Anulowanie na inny klawisz.** Naciśnięcie litery przy trzymanym AltGr przerywa
   gest — to była kombinacja znakowa, nie dyktowanie.

Klawisze modyfikujące są z punktu 3 wyłączone celowo: Windows przy każdym AltGr
wysyła dodatkowo syntetyczny lewy Ctrl, więc gdyby modyfikatory anulowały gest,
hotkey nie zadziałałby ani razu.

To wystarcza na *większość* naciśnięć, ale nie na wszystkie — dłuższe zawahanie
przy `ą` potrafi przekroczyć próg i włączyć nagrywanie. Przy klawiszu, który
naciskasz kilkadziesiąt razy na akapit, „prawie zawsze dobrze" jest za mało.
Stąd prawy Ctrl jako domyślny; opcja `alt_r` została, bo na układzie *Polski
(214)* i na klawiaturach bez AltGr problem nie występuje.

**Jeśli aktualizujesz starszą instalację**, w Twoim `config.toml` nadal siedzi
`key = "alt_r"` — nowa domyślna wartość dotyczy tylko świeżych konfiguracji.
Przełącz w menu tray.

### Nazwy własne, które Whisper przekręca

Whisper nie zna nazw, których nie ma powodu się spodziewać: „DeepSeek" wraca jako
`Dipsick`, `dipsyka`, `Deepsika`. Model czyszczący zwykle tego nie naprawi — nie
ma się czego uchwycić, a zgadywanie byłoby halucynacją.

Słownik ma **dwie części, które się łączą**:

| | gdzie | co tam trzymać |
|---|---|---|
| wspólna | [`vocabulary.txt`](vocabulary.txt) w repo, **wersjonowana** | nazwy techniczne i publiczne — narzędzia, biblioteki, modele |
| prywatna | `transcription.vocabulary` w `%APPDATA%`, **poza gitem** | nazwy klientów, projektów i osób |

Podział jest celowy: wspólną bazę techniczną warto wersjonować i mieć na każdej
maszynie, a nazw z pracy nie chcesz wypchnąć na GitHuba jednym `git push`.
`config.toml` jest w `.gitignore` od pierwszego commita.

Prywatną część edytujesz z menu tray → *Czyszczenie tekstu* → **Nazwy wlasne...**,
wspólną — zwykłym edytorem. Format obu jest ten sam: po przecinku albo po jednej
w linii, `#` zaczyna komentarz, nazwy wieloczłonowe dozwolone.

```
DeepSeek, Claude Code, Anthropic, Kubernetes, Terraform
```

Nazwy zapisuj w **formie podstawowej** (`Anthropic`, nie `Anthropica`) — odmianą
zajmuje się model.

Jedna lista trafia w **dwa** miejsca:

- do `initial_prompt` Whispera — żeby usłyszał je poprawnie i problem nie powstał,
- do promptu modelu czyszczącego — żeby naprawił to, co mimo wszystko przekręcił.

Żadna połowa nie wystarcza sama. Priming czasem nie zadziała, a naprawa po fakcie
zostawia przekręcony tekst w historii i nie pomaga przy wyłączonym czyszczeniu.
Prompt zawiera jawny zakaz dopisywania nazw z listy do transkrypcji, w których nic
ich nie przypomina — bez tego model zaczyna je wstawiać tam, gdzie ich nie było.

Zmierzony efekt drugiej połowy (`deepseek-v4-flash`, ten sam tekst, po 3 przebiegi):

| | `DeepSeek` poprawnie | `Sonnet` poprawnie |
|---|---|---|
| bez słownika | 2 / 3 | **0 / 3** |
| ze słownikiem | 3 / 3 | **2 / 3** |

Wpływ na priming Whispera nie został zmierzony — to udokumentowane zachowanie
`initial_prompt`, nie pomiar na konkretnym głosie.

#### Jeden słownik na oba języki

Dyktujesz po polsku i po angielsku z **tej samej listy**. Nazwy zapisujesz
w **formie podstawowej** (`DeepSeek`, nie `DeepSeeka`) — model odmienia je sam
i robi to zgodnie z językiem zdania:

| dyktowanie | w słowniku | w wyniku |
|---|---|---|
| „przełączmy na dipsicka" | `DeepSeek` | „przełączmy na **DeepSeeka**" |
| „switch to dipsick" | `DeepSeek` | „switch to **DeepSeek**" |

Dwa słowniki nie są potrzebne i byłyby kłopotliwe: `transcription.language`
przyjmuje `auto`, więc przy autodetekcji nie dałoby się wybrać właściwej listy
przed transkrypcją. Whisper i tak dostaje jeden `initial_prompt`, a nazwy własne
brzmią zwykle tak samo w obu językach.

Sekcja słownika w prompcie jest po polsku i **celowo** każe odmieniać po polsku,
mimo że dla angielskiego brzmi to bez sensu. Przepisanie jej na neutralną
językowo zostało zmierzone i wypadło **gorzej**: polski spadł z 14/16 na 9/16
przy odtwarzaniu `Sonnet`, a angielski i tak był 8/8 w obu wersjach — bo lock
językowy doklejany na końcu promptu i tak nadpisuje tę instrukcję.

#### Słownik, który uzupełnia się sam

Czasem model czyszczący **sam** rozpozna przekręconą nazwę z kontekstu — tak Sonnet
zamienił `Dipsick` na `Deepseek`, nie mając żadnego słownika. Taka poprawka
naprawia jednak tylko ten jeden tekst: Whisper się nie uczy i następnym razem
przekręci nazwę tak samo.

Aplikacja wyłapuje te momenty, porównując `raw_text` z tekstem po czyszczeniu
w historii, i proponuje dopisanie nazwy do słownika. Wtedy zaczyna działać
zapobiegawczo — **mocniejszy model uczy słabszego raz, a potem problem nie
powstaje**. Za rozpoznanie płacisz jeden raz, nie przy każdym dyktowaniu.

```powershell
.\run.ps1 -SuggestVocabulary              # co znalazło w historii
.\run.ps1 -AddVocabulary "DeepSeek, Sonnet"
```

W trybie automatycznym (*Czyszczenie tekstu* → *Proponuj nazwy wlasne*, domyślnie
włączone) po dyktowaniu pojawia się dymek, a w menu licznik *Propozycje slownika (N)...*
— widoczny tylko wtedy, gdy jest co przeglądać. Nic nie kradnie focusu i **nic nie
trafia do słownika bez Twojego potwierdzenia**: błędny wpis psułby wszystkie
przyszłe dyktowania, i to u źródła. Puste pole w oknie propozycji oznacza „nie
pytaj o to słowo ponownie".

Filtr jest celowo ostry, bo model czyszczący zmienia mnóstwo słów. Odpadają
różnice wyłącznie w diakrytykach i wielkości liter (`wez`→`weź`), odmiana tego
samego rdzenia (`alta`→`Altu`), zamiany bez podobieństwa brzmienia i przeróbki
wielu słów naraz. Zostają zamiany jedno słowo na jedno, brzmiące podobnie, gdzie
wynik wygląda na nazwę własną. Na 22 dyktowaniach dało to 1 kandydata — trafionego.

Ograniczenie, które warto znać: to nie pomoże przy nazwie, której model czyszczący
**nigdy** nie zgadnie. Pierwsze wystąpienie czegoś zupełnie nietypowego trzeba
wpisać ręcznie. To uzupełnienie ręcznego słownika, nie zamiennik.

## Czyszczenie tekstu przez LLM

Surowa transkrypcja zawiera wszystko, co powiedziałeś — łącznie z `yyy`, `no więc`,
`jakby`, `w sensie` i poprawkami w locie. Warstwa czyszcząca przepuszcza ją przez
model, zanim trafi do schowka:

```
PRZED:  ...bo myślałem, że ta oryginalna aplikacja od Jacka, to ona jakby te,
        ucina takie, wiesz, że przerabia te moje słowa...
PO:     ...bo myślałem, że oryginalna aplikacja od Jacka przerabia te moje słowa...
```

Robi cztery rzeczy: wycina przerywniki, stosuje autopoprawki (`wyślij do Marka
znaczy do Marcina` → `wyślij do Marcina`), poprawia interpunkcję i polskie znaki,
a przy samej ciszy zwraca sentinel `EMPTY` i nic nie wkleja. Nie rusza
identyfikatorów, ścieżek, flag CLI ani `camelCase`.

**Domyślnie wyłączone**, tak jak w oryginale. Włączasz z menu tray →
*Czyszczenie tekstu* → *Włącz czyszczenie*.

Reszta ustawień siedzi w tym samym podmenu, w trzech grupach — każda podpisana
aktualnym wyborem, więc stan widać bez rozwijania:

```
Czyszczenie tekstu >
    [ ] Wlacz czyszczenie
    ---
    Provider: DeepSeek API >     Anthropic API / DeepSeek API / Claude Code CLI
    Model: deepseek-v4-flash >   modele wybranego providera
    Styl: Domyslny >             Domyslny / Czat / Doslowny
    ---
    Klucz API: anthropic...
    Klucz API: deepseek...
```

Lista modeli pokazuje **tylko modele aktywnego providera** — po przełączeniu na
DeepSeeka masz tam `deepseek-v4-flash` i `deepseek-v4-pro`, po przełączeniu na
Anthropic `claude-haiku-4-5` i `claude-sonnet-5`.

### Który provider

| | Anthropic API | DeepSeek API | Claude Code CLI |
|---|---|---|---|
| Model domyślny | `claude-haiku-4-5` | `deepseek-v4-flash` | `claude-sonnet-5` |
| Input / 1M | $1.00 | **$0.14** | — |
| Output / 1M | $5.00 | **$0.28** | — |
| Koszt / dyktowanie | ~$0.0026 | ~$0.00025 | subskrypcja |
| Czas | ~1 s (nie mierzone) | **~1,5 s** (zmierzone z PL) | **~5 s** (zmierzone) |
| Klucz API | wymagany | wymagany | **niepotrzebny** |
| Ruch idzie do | Anthropic (USA) | **DeepSeek (Chiny)** | Anthropic (Twoja subskrypcja) |

DeepSeek jest ~10× tańszy i mówi protokołem Anthropic Messages pod innym
`base_url`, więc obsługuje go ten sam klient. Cache promptu też działa lepiej:
minimalny cache'owalny prefiks Anthropic dla Haiku 4.5 to 4096 tokenów, a nasz
prompt systemowy ma ~1100 — czyli u Anthropic **cache w ogóle nie zadziała**,
a DeepSeek cache'uje automatycznie bez progu.

Opóźnienie DeepSeeka z Polski wyszło ~1,5 s dla obu modeli — czyli cena nie jest
tu kompromisem za czekanie. Polszczyzna na teście z przerywnikami, autopoprawką
i identyfikatorem `user_id` wypadła poprawnie: `user_id` nietknięty.

**Przez Claude Code CLI wybieraj Sonneta, nie Haiku.** Wbrew intuicji: w pięciu
przebiegach tego samego tekstu haiku-4-5 zajmował 19,8–60+ s (dwa razy przekroczył
limit czasu), a sonnet-5 trzymał się 4,2–5,7 s. Narzut CLI dominuje nad szybkością
samego modelu. Dlatego domyślnym modelem CLI jest Sonnet.

**Zanim włączysz DeepSeeka: ruch idzie na serwery w Chinach.** Do treści
służbowych używaj Claude Code CLI (idzie przez Twoją subskrypcję) albo wyłącz
czyszczenie zupełnie.

Nie zgaduj, który jest najlepszy — zmierz na swoim tekście:

```powershell
.\run.ps1 -Benchmark "no wiec yyy wyslij to do Marka znaczy do Marcina"
```

Przepuszcza ten sam tekst przez każdego gotowego providera i wypisuje czasy oraz
wyniki obok siebie.

### Porównanie jakościowe

Szybkość to połowa pytania. Druga połowa brzmi „czy mogę mu zaufać z moim
tekstem" — i odpowiada na nią:

```powershell
.\run.ps1 -Quality                      # wszyscy gotowi providerzy
.\run.ps1 -Quality -Provider deepseek   # tylko jeden
```

Przepuszcza stały zestaw trudnych transkrypcji (identyfikatory, `camelCase`,
ścieżki, autopoprawki, „nie" jako kontrast, halucynacje Whispera na ciszy,
transkrypcja będąca poleceniem) i sprawdza **błędy mechaniczne**: zgubiony
identyfikator, zostawiony przerywnik, model odpowiadający zamiast czyścić.
Styl oceniasz sam — tego nie da się zmierzyć.

Zestaw jest też testem regresyjnym promptu: zmień `prompts.py` albo model,
uruchom ponownie i zobacz, co się zepsuło. Przypadki są w
`whisperdictate/enhance/quality.py`, każdy z uzasadnieniem, po co istnieje.

Zmierzone (12 przypadków, jeden przebieg):

| | Błędy | Śr. czas |
|---|---|---|
| `deepseek-v4-pro` | 0 / 12 | 1,54 s |
| `deepseek-v4-flash` | 0 / 12 | 1,60 s |
| `claude_cli` + `claude-haiku-4-5` | 0 / 12 | 20,9 s |
| `claude_cli` + `claude-sonnet-5` | 1 / 12 | 4,25 s |

Jeden przebieg nie wystarcza — te modele nie są deterministyczne. Na powtórzeniu
×5 najtrudniejszych przypadków **Pro okazał się wyraźnie spójniejszy niż Flash**
przy tym samym czasie: Flash zwrócił nazwę własną w trzech różnych formach
(`DeepSick`, `Deepsika`, `DeepSeeka`) i raz zamienił znaczące „jakby" na
„jakieś"; Pro dał pięć razy ten sam wynik. Jeśli zależy Ci na powtarzalności,
Pro nie kosztuje tu czasu — tylko tokeny.

### Klucze API

Nie trafiają do pliku konfiguracyjnego. Lądują w **Menedżerze poświadczeń
Windows**, szyfrowane per użytkownik, osobny wpis na providera:

```powershell
.\run.ps1 -SetApiKey anthropic
.\run.ps1 -SetApiKey deepseek
```

Albo z menu tray → *Czyszczenie tekstu* → *Klucz API: …*. Zmienne
`ANTHROPIC_API_KEY` i `DEEPSEEK_API_KEY` mają pierwszeństwo; każdy provider
czyta wyłącznie swoją, więc jedna nie przesłania drugiej.

### Zasada fail-soft

Awaria czyszczenia **nigdy nie kosztuje dyktowania**. Brak klucza, limit API, brak
sieci, timeout, błąd providera — wkleja się surowa transkrypcja, a powód ląduje
w logu. Historia zapisuje obie wersje (`text` i `raw_text`), więc złe czyszczenie
też nie niszczy oryginału.

Osobny bezpiecznik: gdy odpowiedź modelu jest nieproporcjonalnie długa względem
transkrypcji, jest odrzucana. To przypadek, w którym model **odpowiedział** na
Twoje dyktowanie zamiast je oczyścić — wtedy lepszy jest surowy tekst.

Test bez mikrofonu:

```powershell
.\run.ps1 -Enhance "no wiec yyy wyslij to do Marka znaczy do Marcina"
```

## Konfiguracja

Plik: `%APPDATA%\WhisperDictateWin\config.toml` (tworzony przy pierwszym starcie).
Pełny opis opcji z komentarzami: [`config.example.toml`](config.example.toml).

Najczęściej zmieniane:

| Klucz | Domyślnie | Znaczenie |
|---|---|---|
| `transcription.language` | `"pl"` | `pl`, `en` albo `auto` |
| `transcription.model` | `"large-v3-turbo"` | mniejszy = szybszy, mniej dokładny |
| `hotkey.key` | `"alt_r"` | `alt_l`, `ctrl_r`, `f1`–`f20`, `scroll_lock`, `pause` |
| `hotkey.mode` | `"hold"` | `hold` albo `toggle` |
| `audio.device` | `null` | nazwa mikrofonu; ustawiana z menu tray |
| `output.auto_paste` | `true` | `false` = tylko schowek, bez Ctrl+V |
| `[replacements]` | pusta | słownik zamian, np. `"kubernetes" = "Kubernetes"` |

Aplikacja **nadpisuje** ten plik przy zmianie ustawień z menu — komentarze w nim
nie przetrwają. Notatki trzymaj w `config.example.toml`.

## Diagnostyka

```powershell
.\run.ps1 -Check                      # CUDA, mikrofony, hotkey, ładowanie modelu
.\run.ps1 -ListDevices                # mikrofony (WASAPI)
.\run.ps1 -ListDevices -All           # + duplikaty z MME/DirectSound/WDM-KS
.\run.ps1 -Record 5                   # nagraj 5 s i wypisz transkrypcję (bez hotkeya)
.\run.ps1 -Record 5 -Device "Anker"   # ...z konkretnego mikrofonu, bez zmiany configu
.\run.ps1 -SetApiKey                  # zapisz klucz API w Menedżerze poświadczeń
.\run.ps1 -Enhance "no wiec yyy test" # przetestuj czyszczenie tekstu
.\run.ps1 -Trace                      # logowanie DEBUG
```

Log: `%APPDATA%\WhisperDictateWin\whisperdictate.log`.
Historia transkrypcji: `%APPDATA%\WhisperDictateWin\history.jsonl`.

### Typowe problemy

**„CTranslate2 NIEDOSTEPNY" albo praca na CPU mimo karty NVIDIA**
Brakuje bibliotek CUDA. `.\.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt`.
Potrzebny jest sterownik NVIDIA obsługujący CUDA 12 (R525 lub nowszy).

**Hotkey nie reaguje**
Jeśli okno z fokusem działa jako administrator, a WhisperDictate nie, Windows
zablokuje mu podglądanie klawiatury (User Interface Privilege Isolation). Uruchom
aplikację z tymi samymi uprawnieniami co docelowe okno.

**Tekst wkleja się w złe miejsce albo wcale**
Podnieś `output.paste_delay_ms` (np. do 250). Niektóre aplikacje oparte o Electron
czytają schowek asynchronicznie. Alternatywa: `output.auto_paste = false` i Ctrl+V
ręcznie.

**Transkrypcje z niczego, np. „Napisy stworzone przez społeczność Amara.org"**
Klasyczna halucynacja Whispera na ciszy. Upewnij się, że
`transcription.vad_filter = true`, i sprawdź `-ListDevices`, czy nagrywasz z
właściwego mikrofonu.

## Czego tu nie ma

Oryginał ma kilka rzeczy, których ta wersja świadomie nie odtwarza:

- **Providerzy OpenAI, OpenRouter i Ollama** dla czyszczenia tekstu. Zaimplementowane
  są trzy: Anthropic API, DeepSeek API i Claude Code CLI (dwa ostatnie to dodatek,
  oryginał ich nie ma). Interfejs `Provider` jest jednometodowy, więc dołożenie
  kolejnego to jedna klasa.
- **Okno z dashboardem** (8 zakładek, statystyki, przeglądarka historii). Historia
  jest zapisywana do JSONL, ale przegląda się ją w edytorze.
- **Pobieranie modeli z paskiem postępu.** Zajmuje się tym Hugging Face Hub.

Architektura jest rozdzielona (rdzeń nie wie nic o UI), więc dołożenie okna nie
wymaga przepisywania logiki. Szczegóły: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Podziękowania

- **[Jacek Gajewski](https://github.com/jacek-gajewski-ice)** — autor oryginalnego
  [WhisperDictate](https://github.com/jacek-gajewski-ice/whisper-app) na macOS.
  Ten projekt istnieje, bo tamten istniał pierwszy: pomysł, projekt interakcji
  i decyzje produktowe są jego. Jego `CLAUDE.md` z notatkami inżynierskimi był
  najlepszą dokumentacją, jaką można było mieć przy odtwarzaniu zachowania.
- **[FreeFlow](https://github.com/zachlatta/freeflow)**
  i **[VoiceInk](https://github.com/Beingpax/VoiceInk)** — wzorzec promptu
  czyszczącego, za oryginałem, który je kredytuje.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** (SYSTRAN)
  i **[CTranslate2](https://github.com/OpenNMT/CTranslate2)** — silnik transkrypcji.
- **[Whisper](https://github.com/openai/whisper)** (OpenAI) — model.

## Licencja

Kod tego repozytorium: MIT. Modele Whisper: MIT (OpenAI). faster-whisper: MIT.

Kod nie jest pochodną oryginału (żadna linijka nie została przeniesiona — to inny
język i inne frameworki), więc licencja tamtego projektu nie ma tu zastosowania.
Atrybucja powyżej jest kwestią uczciwości, nie wymogu prawnego.
