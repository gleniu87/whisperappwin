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

### Prawy Alt a polskie znaki

Na układzie *Polski (programisty)* prawy Alt to AltGr — klawisz, którym piszesz
`ą ę ó ś ł ż ź ć ń`. Trzy mechanizmy sprawiają, że hotkey mu nie przeszkadza:

1. **Nic nie jest przechwytywane.** Hook tylko obserwuje klawiaturę; AltGr dociera
   do aplikacji nietknięty, niezależnie od tego, czy WhisperDictate działa.
2. **Próg przytrzymania (300 ms).** `AltGr+a` to naciśnięcie poniżej 100 ms i nigdy
   nie przekroczy progu. Dopiero świadome przytrzymanie startuje nagrywanie.
3. **Anulowanie na inny klawisz.** Naciśnięcie litery przy trzymanym AltGr przerywa
   gest — to była kombinacja znakowa, nie dyktowanie.

Klawisze modyfikujące są z punktu 3 wyłączone celowo: Windows przy każdym AltGr
wysyła dodatkowo syntetyczny lewy Ctrl, więc gdyby modyfikatory anulowały gest,
hotkey nie zadziałałby ani razu.

Jeśli mimo to przeszkadza, zmień `hotkey.key` w konfiguracji na `f9`,
`scroll_lock` albo `ctrl_r`, albo podnieś `hold_threshold_ms`.

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

### Który provider

| | Anthropic API | Claude Code CLI |
|---|---|---|
| Czas na dyktowanie | **~1 s** (Haiku 4.5) | **~23 s** (zmierzone) |
| Klucz API | wymagany | niepotrzebny |
| Koszt | ułamki grosza za dyktowanie | Twoja subskrypcja |

API jest domyślne — przy dyktowaniu opóźnienie jest odczuwalne od razu. CLI to
opcja, gdy nie chcesz zarządzać kluczem.

### Klucz API

Nie trafia do pliku konfiguracyjnego. Ląduje w **Menedżerze poświadczeń Windows**,
szyfrowany per użytkownik:

```powershell
.\run.ps1 -SetApiKey
```

Albo z menu tray → *Czyszczenie tekstu* → *Ustaw klucz API…*. Zmienna
`ANTHROPIC_API_KEY` ma pierwszeństwo, jeśli ustawiona.

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
