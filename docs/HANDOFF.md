# Handoff — stan na 2026-08-01

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
| Testy | **162, wszystkie przechodzą** — `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |

Uruchamianie: `.\run.ps1` (z konsolą) albo `.\run.ps1 -Hidden` (tylko tray).
Diagnostyka: `.\run.ps1 -Check`.

---

## NASTĘPNY KROK (jedyna otwarta rzecz)

Użytkownik ma klucz API DeepSeeka i chciał porównać go z Haiku. Zapis klucza
wysypywał się na `TypeError` — **naprawione w `f8ab41b`, ale klucz nie został
zapisany**. Trzeba powtórzyć:

```powershell
cd C:\claude_projects\whisperappwin
.\run.ps1 -SetApiKey deepseek
.\run.ps1 -Benchmark "no wiec yyy wez sprawdz czy ten handler znaczy ten parser user_id sie nie wywala na pustym stringu bo jakby mi sie wydaje ze tam jest blad"
```

Tekst benchmarku jest dobrany celowo: ma przerywniki (`no wiec`, `yyy`, `jakby`),
autopoprawkę (`ten handler znaczy ten parser`) i identyfikator `user_id`, którego
model **nie powinien** ruszyć.

**Czego nie wiemy i co ma rozstrzygnąć benchmark:**

1. **Opóźnienie DeepSeeka z Polski.** To decyduje, nie cena — przy dyktowaniu
   różnica 1 s vs 3 s jest ważniejsza niż 27 zł/miesiąc.
2. **Jakość polskiego** DeepSeeka na tym zadaniu.

`getpass` działa poprawnie w prawdziwym oknie PowerShell. **Nie** działa pod Git
Bash (`!` w sesji Claude Code idzie przez bash) — tam potrafi wypisać klucz na
ekran, dlatego `--set-api-key` czyta ze stdin, gdy stdin nie jest tty.

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

## Czego NIE zweryfikowano

- **DeepSeek w ogóle** — kod napisany, testy jednostkowe przechodzą, ale **żadne
  realne wywołanie nie poszło**. To jest następny krok.
- **Anthropic API jako provider czyszczenia** — brak klucza, nigdy nie odpalone
  na żywo. Ścieżka kodu jest ta sama co DeepSeeka (`MessagesApiProvider`), więc
  benchmark pokryje obie, jeśli będzie klucz.
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
| Claude Code CLI, pełny prompt (3425 znaków) | **23,4 s** ← ta liczba się liczy |
| Haiku 4.5 | $1 / $5 za 1M, ~$0.0026 na dyktowanie |
| DeepSeek V4 Flash | $0.14 / $0.28 za 1M, ~$0.00025 na dyktowanie |
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
- **Nie zawężaj mu wyboru.** Zaproponowałem tylko providerów Anthropic i słusznie
  to zakwestionował — DeepSeek jest 10× tańszy. Przy pytaniach o warianty
  uwzględniaj opcje spoza oczywistej ścieżki.
- **Mów wprost, gdy coś jest niesprawdzone.** Docenia „tego nie zweryfikowałem"
  bardziej niż gładkie podsumowanie.
