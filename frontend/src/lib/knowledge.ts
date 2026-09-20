/**
 * Knowledge base entries shown on the Knowledge page.
 *
 * These are the durable, human-checkable notes EVA keeps alongside Attention
 * items: who is involved, which account or project a thread belongs to, and
 * which working rules the workspace runs on. Everything here is derived from
 * the demo workspace (its synthetic inbox, calendar and decisions), so each
 * entry names the source it came from instead of linking to a live system.
 */

export type KnowledgeKind = "person" | "account" | "project" | "rule";

export type KnowledgeConfidence = "confirmed" | "inferred";

export type KnowledgeEntry = {
  id: string;
  kind: KnowledgeKind;
  name: string;
  /** Who or what this entry is about, in one line. */
  role: string;
  summary: string;
  facts: string[];
  tags: string[];
  confidence: KnowledgeConfidence;
  /** Where the note came from — synthetic demo sources only. */
  source: string;
};

export const KNOWLEDGE_KIND_LABELS: Record<KnowledgeKind, string> = {
  person: "People",
  account: "Accounts",
  project: "Projects",
  rule: "Working rules",
};

export const KNOWLEDGE_KIND_ORDER: KnowledgeKind[] = ["person", "account", "project", "rule"];

export const KNOWLEDGE_ENTRIES: KnowledgeEntry[] = [
  {
    id: "k-person-anna-kowalska",
    kind: "person",
    name: "Anna Kowalska",
    role: "Kontakt handlowy po stronie ACME",
    summary:
      "Prowadzi negocjację komercyjną i przesyła agendę przeglądów. Odpowiada za terminy, których ACME oczekuje tego samego dnia.",
    facts: [
      "Autorka zaktualizowanej wyceny z odpowiedzią wymaganą dzisiaj do 11:00.",
      "Przesłała agendę przeglądu komercyjnego — to ona spina wątek cenowy ze spotkaniem.",
      "Kanał: poczta; nie używa wspólnego dokumentu do uzgodnień cenowych.",
    ],
    tags: ["ACME", "negocjacje", "terminy"],
    confidence: "confirmed",
    source: "Skrzynka demo · wątek „Zaktualizowana wycena ACME”",
  },
  {
    id: "k-person-marta-nowak",
    kind: "person",
    name: "Marta Nowak",
    role: "Zakupy po stronie dostawcy infrastruktury",
    summary:
      "Przygotowuje porównania ofert i prosi o decyzję w konkretnym terminie. Podaje ceny w formacie, który da się przeliczyć bez dodatkowych pytań.",
    facts: [
      "Przesłała dwie oferty na stacje robocze: A 18 900 zł, B 17 400 zł.",
      "Oczekuje decyzji oznaczonej jako wymagana, nie jako propozycji.",
    ],
    tags: ["zakupy", "dostawcy"],
    confidence: "confirmed",
    source: "Skrzynka demo · „Decyzja wymagana: dostawca stacji roboczych”",
  },
  {
    id: "k-person-piotr-zielinski",
    kind: "person",
    name: "Piotr Zieliński",
    role: "Budżet i podróże służbowe",
    summary:
      "Sygnalizuje odchylenia budżetowe z wyprzedzeniem i prosi o akceptację konkretnej kwoty, a nie o generalną zgodę.",
    facts: [
      "Zgłosił przekroczenie planu podróży o 18% (12 000 zł).",
      "Równolegle wysłał warianty dojazdu do Berlina do wyboru.",
    ],
    tags: ["budżet", "podróże"],
    confidence: "confirmed",
    source: "Skrzynka demo · „Budżet podróży 18% powyżej planu”",
  },
  {
    id: "k-person-ewa-lose",
    kind: "person",
    name: "Ewa Loś",
    role: "Produkt — materiały na przeglądy",
    summary:
      "Dostarcza drafty (roadmapa, slajdy KPI) i spodziewa się komentarzy w formie listy punktów, nie długiej odpowiedzi.",
    facts: [
      "Autorka draftu roadmapy Q4 i slajdów KPI na przegląd.",
      "Materiały trafiają do Evy jako kontekst spotkań, bez terminu wymuszającego decyzję.",
    ],
    tags: ["produkt", "kontekst"],
    confidence: "confirmed",
    source: "Skrzynka demo · „Draft roadmapy Q4”, „Slajdy KPI na przegląd”",
  },
  {
    id: "k-account-acme",
    kind: "account",
    name: "ACME",
    role: "Klient wdrożeniowy — otwarta negocjacja cenowa",
    summary:
      "Najważniejszy otwarty wątek w tym tygodniu: rozbieżność cenowa i przegląd komercyjny, na którym Eva cytuje korespondencję.",
    facts: [
      "Rozbieżność: 84 000 zł (nowa wycena) wobec 71 000 zł uzgodnionych wcześniej.",
      "Przegląd komercyjny jest pierwszym spotkaniem dnia i ma wysoki priorytet.",
      "Eva łączy e-maile ACME ze spotkaniem, więc briefing cytuje konkretną wiadomość, nie sam kalendarz.",
    ],
    tags: ["ACME", "przychody", "decyzja"],
    confidence: "confirmed",
    source: "Skrzynka demo + kalendarz · spotkanie „Przegląd oferty ACME”",
  },
  {
    id: "k-project-workstations",
    kind: "project",
    name: "Zakup stacji roboczych",
    role: "Przetarg na sprzęt — decyzja w toku",
    summary:
      "Dwie oferty na stole, różnica 1 500 zł. Decyzja jest odnotowana jako wymagana, więc trafia do sekcji Decisions zamiast znikać w skrzynce.",
    facts: [
      "Oferta A: 18 900 zł; oferta B: 17 400 zł.",
      "Eva klasyfikuje wątek jako decyzję finansową z kwotą, nie jako wiadomość do przeczytania.",
    ],
    tags: ["sprzęt", "zakupy"],
    confidence: "confirmed",
    source: "Skrzynka demo · „Decyzja wymagana: dostawca stacji roboczych”",
  },
  {
    id: "k-project-travel-budget",
    kind: "project",
    name: "Budżet podróżny Q4",
    role: "Odchylenie budżetowe do akceptacji",
    summary:
      "Plan przekroczony o 12 000 zł. Wątek jest decyzją, ale bez pilności — dokładnie ten przypadek, który Focus potrafi odłożyć bez utraty kontekstu.",
    facts: [
      "Przekroczenie 18% planu; kwota do akceptacji: 12 000 zł.",
      "Warianty dojazdu do Berlina czekają na wybór w tym samym wątku.",
    ],
    tags: ["budżet", "FYI→decyzja"],
    confidence: "confirmed",
    source: "Skrzynka demo · „Budżet podróży 18% powyżej planu”",
  },
  {
    id: "k-project-incident",
    kind: "project",
    name: "Incydent API produkcyjnego",
    role: "Alert operacyjny — wysoka pilność",
    summary:
      "Jedyny wątek w demo, który podnosi priorytet do wysokiego bez udziału człowieka: komunikat zawiera jasny sygnał pilności i mierzalny wskaźnik.",
    facts: [
      "Wskaźnik błędów API: 37%.",
      "Nie ma kwoty finansowej — Eva nie dopisuje jej na siłę do decyzji.",
      "Ten typ wiadomości przechodzi przez Focus nawet przy progu „medium and above”.",
    ],
    tags: ["operacje", "pilne"],
    confidence: "confirmed",
    source: "Skrzynka demo · „PILNE: błąd API produkcyjnego wzrósł do 37%”",
  },
  {
    id: "k-rule-currency",
    kind: "rule",
    name: "Rozliczenia w PLN",
    role: "Zasada polityki decyzyjnej v1",
    summary:
      "Decyzja finansowa ma jedną walutę. Mieszanie symboli w jednym wątku kończy się odrzuceniem propozycji, więc kwoty z skrzynek są zawsze przeliczane na złotówki.",
    facts: [
      "Polityka v1 wymaga pojedynczej, spójnej waluty dla każdej decyzji.",
      "Kwota bez waluty nie tworzy decyzji finansowej — trafia do FYI.",
    ],
    tags: ["polityka", "finanse"],
    confidence: "confirmed",
    source: "Polityka decyzyjna v1 (backend)",
  },
  {
    id: "k-rule-focus",
    kind: "rule",
    name: "Zasady sesji Focus",
    role: "Jak Eva filtruje powiadomienia w trakcie pracy",
    summary:
      "Focus nie wycisza skrzynki — odkłada to, co nie jest pilne, i zwraca je w podsumowaniu po zakończeniu bloku.",
    facts: [
      "Próg domyślnie „medium and above”; można ustawić tylko wysokie albo wszystkie.",
      "Wyjątki wyłącznie dla dokładnych adresów e-mail — bez masek domen i słów kluczowych.",
      "Po zatrzymaniu lub naturalnym końcu bloku Eva pokazuje, ile rzeczy odłożyła i ile z nich to decyzje.",
    ],
    tags: ["focus", "powiadomienia"],
    confidence: "confirmed",
    source: "A04 · moduł Focus (backend)",
  },
  {
    id: "k-rule-no-send",
    kind: "rule",
    name: "Eva nie wysyła e-maili",
    role: "Granica narzędzi",
    summary:
      "Eva proponuje i przygotowuje, ale nie odpowiada w Twoim imieniu. Wysyłka poczty, płatności i podpisy umów są poza listą dozwolonych narzędzi.",
    facts: [
      "Kategorie „gmail.send”, „payment.execute”, „contract.sign” są zablokowane na poziomie rejestru narzędzi.",
      "Jedyna dopuszczalna zmiana zewnętrzna to kalendarz — i tylko po zatwierdzeniu przez Ciebie.",
    ],
    tags: ["bezpieczeństwo", "narzędzia"],
    confidence: "confirmed",
    source: "Rejestr narzędzi · lista zabronionych nazw (backend)",
  },
  {
    id: "k-rule-taxonomy",
    kind: "rule",
    name: "Czy to decyzja, akcja czy FYI?",
    role: "Słownik Evy",
    summary:
      "Trzy klasy, które decydują o tym, gdzie wiadomość trafi i czy Focus może ją odłożyć. Pilność jest osobnym przełącznikiem, nie czwartą klasą.",
    facts: [
      "Decyzja: wymaga Twojego rozstrzygnięcia (zwykle kwota, zgoda, wybór wariantu).",
      "Akcja: konkretne zadanie do wykonania, bez wyboru wariantu.",
      "FYI: kontekst; można go odłożyć bez ryzyka.",
      "Pilność (urgent) podbija priorytet, ale nie zmienia klasy wiadomości.",
    ],
    tags: ["klasyfikacja", "slownik"],
    confidence: "confirmed",
    source: "A06 · reguły Attention (backend)",
  },
];
