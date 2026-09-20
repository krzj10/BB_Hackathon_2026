"""Deterministic synthetic executive-workday inbox for demo mode.

SYNTHETIC DATA ONLY - every address is on an ``.example`` domain, no real
person, company or private content. The wording deliberately uses the exact
deterministic vocabularies of :mod:`app.attention.rules` (Polish and English
decision/action phrases, PLN amounts) so the REAL rules produce the intended
classifications without any special-casing for demo mode.

Amounts are PLN because policy v1 pins ``financial_high.currency == "PLN"``:
any other currency is reported as an unsupported-currency decision and never
receives the HIGH financial floor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoMessage:
    """One synthetic message with a STABLE id (repeatable resets dedup on it).

    ``linked_event_ids`` ties a thread to a demo calendar event, which is how
    the grounded briefing (B03) associates stored evidence with a meeting.
    """

    fixture_id: str
    sender_email: str
    subject: str
    body: str
    linked_event_ids: tuple[str, ...] = ()


# Newest first: reset stamps fixture i at (reset_time - 2*i) seconds, which
# stays inside the Gmail poll overlap window (EVA_GMAIL_POLL_OVERLAP_SECONDS).
DECISION_MESSAGES: tuple[DemoMessage, ...] = (
    DemoMessage(
        fixture_id="acme-pricing",
        sender_email="anna.kowalska@acme.example",
        subject="Zaktualizowana wycena ACME - odpowiedź potrzebna dzisiaj do 11:00",
        body=(
            "Cześć,\n\n"
            "przesyłam zaktualizowaną wycenę dla ACME. Poprzednia propozycja za "
            "pierwszy rok: 71 000 zł. Nowa propozycja: 84 000 zł - opłata "
            "wdrożeniowa jest niższa, ale cykliczne wsparcie techniczne wyższe.\n\n"
            "Proszę zatwierdzić odpowiedź do 11:00, żeby trzymać termin wdrożenia.\n\n"
            "Anna Kowalska\nACME, Commercial"
        ),
        linked_event_ids=("demo-acme-review",),
    ),
    DemoMessage(
        fixture_id="workstation-supplier",
        sender_email="marta.nowak@procurement.example",
        subject="Decyzja wymagana: dostawca stacji roboczych",
        body=(
            "Cześć,\n\n"
            "dostaliśmy dwie wiążące oferty na 12 stacji roboczych.\n\n"
            "Opcja A - Nordbit: 18 900 zł, dostawa 4 dni robocze, gwarancja 3 lata.\n"
            "Opcja B - Komputronik Biznes: 17 400 zł, dostawa 18 dni roboczych, "
            "gwarancja 2 lata.\n\n"
            "Potrzebuję decyzji do 14:00, żeby zarezerwować produkcję.\n\n"
            "Marta Nowak\nProcurement"
        ),
    ),
    DemoMessage(
        fixture_id="travel-budget",
        sender_email="piotr.zielinski@firma.example",
        subject="Budżet podróży 18% powyżej planu - prośba o akceptację",
        body=(
            "Cześć,\n\n"
            "wydatki na podróże w tym kwartale są 18% powyżej planu. Mamy dwie "
            "ścieżki:\n\n"
            "1) zamrażamy podróże inne niż klienckie do końca kwartału;\n"
            "2) zwiększasz budżet o 12 000 zł.\n\n"
            "Proszę o akceptację jednej z opcji, potrzebuję jej do raportu.\n\n"
            "Piotr Zieliński\nCFO"
        ),
    ),
    DemoMessage(
        fixture_id="prod-incident",
        sender_email="ops-alert@firma.example",
        subject="PILNE: błąd API produkcyjnego wzrósł do 37%",
        body=(
            "Baseline błędów to poniżej 2%. Od 20 minut 37% żądań kończy się "
            "błędem 5xx, klienci zgłaszają wpływ na integracje.\n\n"
            "Eskalacja do zarządu: potrzebuję decyzji o rollbacku ostatniego "
            "wdrożenia - natychmiast.\n\n"
            "Operations Alert"
        ),
    ),
)

CONTEXT_MESSAGES: tuple[DemoMessage, ...] = (
    DemoMessage(
        fixture_id="acme-agenda",
        sender_email="anna.kowalska@acme.example",
        subject="Agenda: przegląd komercyjny ACME",
        body=(
            "Cześć,\n\n"
            "przesyłam agendę przeglądu komercyjnego: wycena, harmonogram "
            "wdrożenia, model wsparcia.\n\nProszę sprawdzić punkty 2 i 3 przed "
            "spotkaniem.\n\nAnna"
        ),
        linked_event_ids=("demo-acme-review",),
    ),
    DemoMessage(
        fixture_id="q4-roadmap",
        sender_email="ewa.lose@produkt.example",
        subject="Draft roadmapy Q4",
        body=(
            "Hej,\n\n"
            "draft roadmapy Q4 jest w dokumencie. Największe ryzyko to zależność "
            "od integracji płatniczej.\n\nProszę przesłać komentarze do końca "
            "tygodnia.\n\nEwa"
        ),
    ),
    DemoMessage(
        fixture_id="candidate-cv",
        sender_email="rekrutacja@firma.example",
        subject="Kandydat: senior backend engineer - CV",
        body=(
            "Cześć,\n\n"
            "kandydat z polecenia, 8 lat doświadczenia, ostatnio platformy "
            "płatnicze.\n\nProszę sprawdzić profil i dać znać, czy dajemy mu "
            "rozmowę techniczną.\n\nZespół rekrutacji"
        ),
    ),
    DemoMessage(
        fixture_id="berlin-travel",
        sender_email="piotr.zielinski@firma.example",
        subject="Opcje podróży do Berlina",
        body=(
            "Cześć,\n\n"
            "trzy opcje dojazdu na spotkanie w Berlinie: pociąg rano, samolot "
            "po południu, wieczorny FlixBus.\n\nProszę przesłać preferowaną "
            "opcję, żebym zarezerwował.\n\nPiotr"
        ),
    ),
    DemoMessage(
        fixture_id="legal-redlines",
        sender_email="kancelaria@prawnik.example",
        subject="Umowa wdrożeniowa - poprawki prawne",
        body=(
            "Dzień dobry,\n\n"
            "przesyłamy redlines umowy wdrożeniowej: odpowiedzialność, kary "
            "umowne, wyjście z umowy.\n\nProszę sprawdzić paragrafy 7 i 9 przed "
            "podpisem.\n\nKancelaria"
        ),
    ),
    DemoMessage(
        fixture_id="infra-status",
        sender_email="infra@firma.example",
        subject="Status infrastruktury - podsumowanie tygodnia",
        body=(
            "Cześć,\n\n"
            "tygodniowy status: dostępność 99,97%, migracja bazy zakończona bez "
            "incydentów, dwa wolne runbooki do uzupełnienia.\n\nBez akcji z Twojej "
            "strony - do wglądu.\n\nZespół infrastruktury"
        ),
    ),
    DemoMessage(
        fixture_id="kpi-deck",
        sender_email="ewa.lose@produkt.example",
        subject="Slajdy KPI na przegląd",
        body=(
            "Hej,\n\n"
            "slajdy KPI są gotowe: przychód, churn, pipeline wdrożeń.\n\n"
            "Proszę sprawdzić slajd 4 - liczby z analityki różnią się o 3% "
            "od finansów.\n\nEwa"
        ),
    ),
    DemoMessage(
        fixture_id="office-survey",
        sender_email="biuro@firma.example",
        subject="Ankieta: układ biurka i stref ciszy",
        body=(
            "Cześć,\n\n"
            "krótka ankieta o układzie biurka i strefach ciszy na nowym piętrze.\n\n"
            "Proszę przesłać odpowiedź, jeśli masz preferencje - bez pośpiechu.\n\n\n"
            "Zespół biurowy"
        ),
    ),
)

_NOISE: tuple[tuple[str, str, str], ...] = (
    ("news@weekly-data.example", "Weekly digest: dane i analityka",
     "Twój cotygodniowy newsletter o danych. Unsubscribe: https://example.invalid/u1"),
    ("promo@sklep-electro.example", "Promocja tygodnia: do -40% na akcesoria",
     "Promocja obowiązuje do końca tygodnia. Zobacz ofertę w sklepie."),
    ("reporty@analytics.example", "Automatyczny raport dzienny",
     "Raport wygenerowany automatycznie. Żadna akcja nie jest wymagana."),
    ("powiadomienia@kurier.example", "Twoja przesyłka nadana",
     "Paczka została nadana i będzie dostarczona kurierem. Numer śledzenia w aplikacji."),
    ("finanse@bank-example.example", "Potwierdzenie przelewu zrealizowane",
     "Operacja została zrealizowana. Szczegóły w historii konta."),
    ("webinary@marketing-weekly.example", "Zaproszenie na webinar o automatyzacji",
     "Dołącz do bezpłatnego webinaru. Newsletter technologiczny, unsubscribe: https://example.invalid/u2"),
    ("oferty@subskrypcja.example", "Oferta przedłużenia subskrypcji",
     "Przygotowaliśmy dla Ciebie ofertę przedłużenia. Promocja dla stałych klientów."),
    ("badania@ankiety.example", "Ankieta: 3 minuty o Twoich narzędziach",
     "Podziel się opinią w krótkiej ankiecie. Nagroda: losowanie nagród."),
    ("community@forum-dev.example", "Aktualności społeczności programistów",
     "Najciekawsze wątki tygodnia w społeczności. Unsubscribe: https://example.invalid/u3"),
    ("katalog@wyposazenie-biura.example", "Katalog wyposażenia biur 2026",
     "Nowy katalog produktów biurowych. Promocje na pierwsze zamówienie."),
    ("newsletter@bezpieczenstwo.example", "Newsletter: bezpieczeństwo w chmurze",
     "Comiesięczny newsletter o bezpieczeństwie. Unsubscribe: https://example.invalid/u4"),
    ("system@monitoring.example", "Automatyczne alerty: podsumowanie dobowe",
     "Podsumowanie dobowe monitoringu wygenerowane automatycznie. Bez akcji."),
    ("promo@ksiegowosc-online.example", "Rabat na moduł faktur",
     "Promocja na moduł faktur dla nowych klientów. Sprawdź warunki oferty."),
    ("powiadomienia@bank-example.example", "Wyciąg miesięczny dostępny",
     "Wyciąg za zakończony miesiąc jest dostępny w bankowości elektronicznej."),
    ("webinary@sprzet.example", "Szkolenie online: skanery i drukarki",
     "Krótkie szkolenie produktowe online. Zapisy otwarte, newsletter sprzętowy."),
    ("oferty@hotel-biznes.example", "Oferta dla podróżujących służbowo",
     "Stała cena dla rezerwacji służbowych. Promocja dla kont firmowych."),
    ("newsletter@gamedev.example", "Weekly digest ze świata gier",
     "Cotygodniowy newsletter o grach i silnikach. Unsubscribe: https://example.invalid/u5"),
    ("reporty@backup.example", "Automatyczny raport kopii zapasowych",
     "Kopie zapasowe zakończone sukcesem. Raport automatyczny, bez akcji."),
)

NOISE_MESSAGES: tuple[DemoMessage, ...] = tuple(
    DemoMessage(
        fixture_id=f"noise-{index + 1:02d}",
        sender_email=sender,
        subject=subject,
        body=body,
    )
    for index, (sender, subject, body) in enumerate(_NOISE)
)

#: Canonical demo inbox, newest first.
DEMO_MESSAGES: tuple[DemoMessage, ...] = DECISION_MESSAGES + CONTEXT_MESSAGES + NOISE_MESSAGES

# --------------------------------------------------------------------------- #
# Focus-mode interrupt probes (injected while the app is running)
# --------------------------------------------------------------------------- #

#: Must stay FYI/LOW: no decision vocabulary, no amount, no urgency marker.
FOCUS_NORMAL = DemoMessage(
    fixture_id="focus-normal",
    sender_email="ewa.lose@produkt.example",
    subject="Zaktualizowane slajdy na przyszły tydzień",
    body=(
        "Cześć,\n\n"
        "przesyłam zaktualizowane slajdy na przyszły tydzień. Bez akcji z Twojej "
        "strony, tylko do wglądu przed przeglądem.\n\nEwa"
    ),
)

#: Decision intent + explicit urgency: the real rules give it the HIGH floor
#: with urgent=True, which is what Focus delivery surfaces during a session.
FOCUS_URGENT = DemoMessage(
    fixture_id="focus-urgent",
    sender_email="ops-alert@firma.example",
    subject="PILNE: potwierdzona awaria Portalu Klienta",
    body=(
        "Awaria potwierdzona: 42% użytkowników nie może się zalogować, rosną "
        "zgłoszenia od klientów.\n\n"
        "Rollback wymaga decyzji zarządu - potrzebuję decyzji natychmiast.\n\n"
        "Operations Alert"
    ),
)
