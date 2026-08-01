# Architektura

## Skąd ten projekt

Oryginał (`jacek-gajewski-ice/whisper-app`) to natywna aplikacja macOS: Swift 6,
SwiftUI `MenuBarExtra`, AVAudioRecorder, Carbon event tapy na hotkey, `NSPasteboard`,
uprawnienia TCC, podpisywanie `codesign`. Żadna z tych warstw nie istnieje na
Windows, więc port polega na odtworzeniu **zachowania**, nie kodu.

Mapowanie warstw:

| Oryginał (macOS) | Ta wersja (Windows) |
|---|---|
| whisper.cpp (`whisper-cli`, proces potomny) | faster-whisper / CTranslate2 (w procesie) |
| AVAudioRecorder → plik WAV | sounddevice → tablica NumPy w pamięci |
| Carbon event tap na lewy ⌥ | pynput `Listener` na prawy Alt |
| SwiftUI `MenuBarExtra` | pystray (ikona zasobnika) |
| Panel SwiftUI | Tk `Toplevel` (overlay nagrywania) |
| `NSPasteboard` + ⌘V | `win32clipboard` + Ctrl+V przez pynput |
| `UserDefaults` | TOML w `%APPDATA%` |
| TCC (uprawnienia) | brak odpowiednika — Windows nie pyta o mikrofon per-aplikacja dla desktopu |

Zamiana whisper.cpp na faster-whisper nie jest kosmetyczna: znika proces potomny,
znika zapis WAV na dysk, a na GPU NVIDIA CTranslate2 z `float16` jest szybszy niż
whisper.cpp z CUDA. Kosztem jest cięższa instalacja (biblioteki cuDNN).

## Model wątków

To najłatwiejsza rzecz do zepsucia w tej aplikacji, więc jest opisana wprost.

```
main            pętla zdarzeń Tk — overlay, sprawdzanie sygnału zamknięcia
pystray         własna pętla komunikatów Win32 (run_detached)
pynput          globalny hook klawiatury (low-level)
worker-*        ładowanie modelu i transkrypcja, po jednym wątku na zadanie
```

Reguły, których pilnuje kod:

- **Callbacki hotkeya muszą wracać natychmiast.** Działają na wątku globalnego
  hooka klawiatury; zablokowanie go zatrzymuje obsługę klawiszy w całym systemie.
  Dlatego `DictationController.on_stop()` oddaje audio wątkowi roboczemu i wraca.
- **Tk wolno dotykać tylko z wątku głównego.** `Overlay.set_state()` wrzuca zdarzenie
  do `queue.Queue`, opróżnianej cyklicznym `after()`.
- **Zamknięcie idzie przez `threading.Event`.** Menu tray działa na swoim wątku i nie
  może wywołać `root.quit()`; ustawia zdarzenie, które wątek główny odpytuje.
- **Wyjątek w wątku roboczym nie może zniknąć po cichu.** `_tracked()` opakowuje
  każdy wątek, loguje traceback i przestawia stan na `ERROR`.

## Przepływ jednego dyktowania

```
prawy Alt wciśnięty
  └─ timer 300 ms ──(inny klawisz?)──> anulowane, to była kombinacja AltGr
       └─ próg minął
            └─ Recorder.start()          strumień PortAudio, mono f32 @ 16 kHz
                 ├─ overlay: czerwona kropka + miernik poziomu
                 └─ prawy Alt puszczony
                      └─ Recorder.stop() → np.ndarray
                           ├─ < min_seconds? odrzuć
                           └─ wątek roboczy:
                                ├─ Transcriber.transcribe()   VAD → CT2 → tekst
                                ├─ postprocess.process()      czyszczenie + zamiany
                                ├─ History.append()           JSONL (przed wklejeniem!)
                                └─ output.deliver()           schowek + Ctrl+V
```

Kolejność „historia przed wklejeniem" jest celowa: jeśli schowek jest zablokowany
przez inną aplikację, transkrypcja jest już zapisana i da się ją odzyskać.

## Moduły

| Plik | Odpowiedzialność |
|---|---|
| `config.py` | TOML z dostępem `get("sekcja.klucz")`, walidacja, atomowy zapis |
| `paths.py` | lokalizacje w `%APPDATA%` / `%LOCALAPPDATA%` |
| `runtime_cuda.py` | rejestracja DLL-i z pakietów `nvidia-*-cu12` przed importem ctranslate2 |
| `audio.py` | wykrywanie i wybór mikrofonu, przechwytywanie, resampling, miernik poziomu |
| `transcriber.py` | leniwe ładowanie modelu, CUDA z fallbackiem na CPU |
| `hotkey.py` | detekcja gestu prawy Alt, próg przytrzymania, ochrona AltGr |
| `output.py` | schowek, zwalnianie zablokowanych modyfikatorów, Ctrl+V |
| `postprocess.py` | czyszczenie tekstu, filtr halucynacji, słownik zamian |
| `history.py` | dopisywanie do JSONL, przycinanie |
| `sounds.py` | nieblokujące sygnały dźwiękowe |
| `enhance/` | czyszczenie transkrypcji przez LLM: prompty, providerzy, filtr wyjścia, klucz API |
| `controller.py` | maszyna stanów, orkiestracja, protokół `UiSink` |
| `ui/tray.py` | ikona zasobnika, menu, powiadomienia |
| `ui/overlay.py` | pływający wskaźnik nagrywania |
| `__main__.py` | CLI, logowanie, pojedyncza instancja, montaż zależności |

## Granica rdzeń / UI

`DictationController` nie importuje niczego z `ui/`. Zna wyłącznie protokół:

```python
class UiSink(Protocol):
    def set_state(self, state: State, detail: str = "") -> None: ...
    def notify(self, message: str, *, error: bool = False) -> None: ...
```

Tray i overlay to dwie niezależne implementacje, rejestrowane przez `add_ui()`.
Dołożenie okna z dashboardem (którego nie ma w tej wersji, a jest w oryginale)
sprowadza się do trzeciej implementacji tego protokołu — bez zmian w logice.

## Decyzje, które wyglądają dziwnie i są celowe

**Resampling liniowy zamiast polyphase z scipy.**
Front-end Whispera to spektrogram melowy przy 16 kHz, a energia mowy leży dobrze
poniżej częstotliwości Nyquista. Aliasing, który usunąłby porządny filtr, jest dla
modelu niesłyszalny. Ścieżka fallbacku i tak jest rzadka — WASAPI w trybie
współdzielonym zwykle sam podaje 16 kHz.

**`condition_on_previous_text=False`.**
Każde dyktowanie jest niezależne. Przenoszenie kontekstu między wypowiedziami to
główna przyczyna pętli powtórzeń w Whisperze.

**Modyfikatory nie anulują gestu hotkeya.**
Windows przy każdym AltGr wysyła syntetyczny lewy Ctrl. Gdyby modyfikatory liczyły
się jako „inny klawisz", hotkey na prawym Alcie nie wystrzeliłby ani razu.

**Przywracany jest tylko tekst ze schowka.**
`CF_UNICODETEXT` i nic więcej. Jeśli przed dyktowaniem w schowku był obrazek albo
lista plików, przepada. Zachowanie pełnej zawartości wymagałoby przechwycenia
wszystkich formatów łącznie z opóźnionym renderowaniem — nieproporcjonalnie dużo
kodu jak na ten zysk. Alternatywa dla wymagających: `output.restore_clipboard = false`.

**Menu mikrofonów pokazuje tylko WASAPI.**
Windows wystawia ten sam mikrofon przez cztery host API PortAudio. Na maszynie
testowej dawało to 26 pozycji na 3 fizyczne urządzenia. MME ucina nazwy na 31
znakach (`Mikrofon (Virtual Desktop Audio` — bez nawiasu zamykającego), WDM-KS
rozbija urządzenie wielokanałowe na wpisy per para kanałów, a WASAPI daje jeden
czysty wpis z prawdziwą częstotliwością. Rozwiązywanie nazwy też przeszukuje
najpierw WASAPI, żeby zapisana nazwa nie trafiła po cichu na gorszy wpis MME
tego samego mikrofonu. `--list-devices --all` pokazuje pełną listę.

**Urządzenie zapisywane po nazwie, nie po indeksie.**
Indeksy PortAudio przesuwają się przy każdej zmianie sprzętu. Indeks zapisany
dziś jutro wskazuje inny mikrofon — cicha awaria, która wygląda jak zepsuta
aplikacja.

**`refresh_devices()` restartuje PortAudio.**
Lista urządzeń jest migawką z momentu inicjalizacji, więc mikrofon podłączony
później jest niewidoczny. Restart jest bezpieczny tylko bez otwartego strumienia,
dlatego woła się go z menu (blokowane w trakcie nagrywania) i raz przy nieudanym
rozwiązaniu nazwy — czyli dokładnie w scenariuszu „odłączyłem kamerkę".

**Akcje menu pystray to domknięcia, nigdy `lambda x=wartosc:`.**
pystray wybiera sposób wywołania akcji na podstawie `__code__.co_argcount`: 0 =
wywołaj bez argumentów, 1 = podaj `Icon`. Argument domyślny **wlicza się** do tej
liczby, więc idiom late-bindingu `lambda n=device.name: ...` dostaje obiekt `Icon`
zamiast nazwy. Predykaty `checked` są odporne (pystray woła je z jednym
argumentem, więc drugi bierze wartość domyślną), ale akcje nie. Pilnuje tego
`tests/test_tray_menu.py`.

**Czyszczenie tekstu jest fail-soft i nigdy nie rzuca.**
`EnhancementService.enhance()` zwraca `None` przy każdej awarii — brak klucza,
limit API, timeout, błąd providera, a nawet nieoczekiwany wyjątek. `None` znaczy
„wklej surowy transkrypt". Pusty string znaczy co innego: sentinel `EMPTY`
z promptu, czyli „to był sam szum, nie wklejaj nic". To rozróżnienie jest
celowe — awaria i cisza wyglądają identycznie, jeśli oba zwracają pustkę.

**Transkrypcja jest opakowana w `<TRANSCRIPT>`.**
Bez tego dyktowanie, które przypadkiem jest pytaniem („czy możesz to sprawdzić"),
czyta się jak polecenie i model na nie odpowiada. Tag zamienia je w dane.

**Bezpiecznik na odpowiedź zamiast czyszczenia.**
Gdy wynik jest ponad trzykrotnie dłuższy od wejścia (i dłuższy niż 400 znaków),
jest odrzucany. Oczyszczony tekst ma długość zbliżoną do oryginału; wynik
wielokrotnie dłuższy to inny rodzaj tekstu — model odpowiedział zamiast oczyścić.

**Prompt zawiera polskie słownictwo przerywników.**
Prompt napisany po angielsku wycina „um" i zostawia „no więc yyy" nietknięte.
Lista polskich wypełniaczy i zwrotów autopoprawki to funkcjonalny rdzeń, nie
tłumaczenie.

**Rejestr providerów jest osobnym, bezimportowym modułem.**
`enhance/registry.py` nie importuje niczego z pakietu, więc czytają go zarówno
`config.py`, jak i `enhance/providers.py`, bez cyklu. Trzyma to, co odróżnia
providerów: `base_url`, zmienną środowiskową klucza, listę modeli i jurysdykcję.

**DeepSeek nie ma własnego klienta.**
Wystawia endpoint zgodny z protokołem Anthropic Messages, więc `MessagesApiProvider`
obsługuje oba — różni je wyłącznie `base_url` i klucz. Ignoruje `anthropic-beta`,
`anthropic-version`, `top_k` i `cache_control`; nie wysyłamy żadnego z nich. Jego
cache promptu jest automatyczny po stronie serwera, więc ignorowany `cache_control`
nie kosztuje nas trafień w cache.

**Zmiana providera przestawia model.**
Nazwy modeli nie przenoszą się między providerami. Zostawienie `claude-haiku-4-5`
po przejściu na DeepSeeka trafiłoby w cichy fallback ich API na `deepseek-v4-flash`
— działa, ale konfiguracja kłamie o tym, co faktycznie działa.

**Menu modeli używa predykatu `visible`, nie przebudowy.**
Menu pystray jest niezmienne po zbudowaniu, ale `visible` jest wyliczane przy
każdym wyświetleniu. Modele wszystkich providerów są zadeklarowane z góry i
ukrywane, gdy ich provider nie jest wybrany.

**Klucze API w Menedżerze poświadczeń, nie w configu — osobny wpis na providera.**
`config.toml` to zwykły tekst w profilu roamingowym, nadpisywany przy każdym
kliknięciu w tray. Menedżer poświadczeń szyfruje per użytkownik i trzyma klucz
poza wszystkim, co da się przypadkiem udostępnić. `Persist` ustawione na
`LOCAL_MACHINE`, żeby klucz nie wędrował z profilem domenowym.

**Dialogi Tk odpalane przez `MainThreadDispatcher`.**
Tray ma własną pętlę komunikatów Win32, a obiektów Tk wolno dotykać tylko
z wątku, który je utworzył. Akcja menu wrzuca wywołanie do kolejki opróżnianej
przez `after()` na wątku Tk.

**Model ładowany z wyprzedzeniem w tle.**
Konstrukcja `WhisperModel` to sekundy. Ładowanie przy pierwszym dyktowaniu
oznaczałoby, że pierwsze użycie po starcie jest zauważalnie wolniejsze.

## Czego brakuje względem oryginału

- Poprawianie transkrypcji przez LLM (`Enhancement/` w oryginale) — cała warstwa
  providerów (Anthropic, Ollama, Claude Code) i promptów.
- Okno dashboardu z ośmioma zakładkami.
- Menedżer modeli z paskiem postępu pobierania.
- Statystyki użycia (oryginał liczy słowa, czas, oszczędzony czas pisania).

Historia jest zapisywana w formacie, który wystarcza do zbudowania statystyk
później — każdy wpis ma `audio_seconds`, `elapsed_seconds`, `model`, `language`.
