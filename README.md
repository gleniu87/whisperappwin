# WhisperDictate for Windows

Lokalne dyktowanie *hold-to-talk*: przytrzymujesz klawisz, mówisz, puszczasz — tekst
wkleja się tam, gdzie masz kursor. Wszystko liczy się na Twoim komputerze, nic nie
wychodzi do chmury.

Windowsowy odpowiednik macOS-owego
[WhisperDictate](https://github.com/jacek-gajewski-ice/whisper-app). Odwzorowuje
jego zachowanie, nie kod: oryginał to Swift + SwiftUI + AVFoundation + Core Audio i
nie ma możliwości uruchomienia go tutaj. Zamiast whisper.cpp używamy
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2), który na
GPU NVIDIA jest szybszy niż whisper.cpp, a na CPU porównywalny.

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

Menu tray → *Czyszczenie tekstu* → **Nazwy wlasne...** (albo `transcription.vocabulary`).
Lista po przecinku, nazwy wieloczłonowe dozwolone:

```
DeepSeek, Claude Code, Anthropic, ICE InsureTech, Tomasz Glen
```

Jedna lista trafia w **dwa** miejsca:

- do `initial_prompt` Whispera — żeby usłyszał je poprawnie i problem nie powstał,
- do promptu modelu czyszczącego — żeby naprawił to, co mimo wszystko przekręcił.

Żadna połowa nie wystarcza sama. Priming czasem nie zadziała, a naprawa po fakcie
zostawia przekręcony tekst w historii i nie pomaga przy wyłączonym czyszczeniu.
Prompt zawiera jawny zakaz dopisywania nazw z listy do transkrypcji, w których nic
ich nie przypomina — bez tego model zaczyna je wstawiać tam, gdzie ich nie było.

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
  są dwa: Anthropic API i Claude Code CLI. Interfejs `Provider` jest jednometodowy,
  więc dołożenie kolejnego to jedna klasa.
- **Okno z dashboardem** (8 zakładek, statystyki, przeglądarka historii). Historia
  jest zapisywana do JSONL, ale przegląda się ją w edytorze.
- **Pobieranie modeli z paskiem postępu.** Zajmuje się tym Hugging Face Hub.

Architektura jest rozdzielona (rdzeń nie wie nic o UI), więc dołożenie okna nie
wymaga przepisywania logiki. Szczegóły: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Licencja

Kod tego repozytorium: MIT. Modele Whisper: MIT (OpenAI). faster-whisper: MIT.
