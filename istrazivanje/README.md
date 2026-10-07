# Kako gradovi objavljuju raspored odvoza (stanje 7. 10. 2026.)

## Datoteke

- `jls_davatelj.csv`: svih 556 gradova i općina, njihov davatelj usluge odvoza (s OIB-om), broj stanovnika i kućanstava (Popis 2021.). Izvor je ministarska tablica godišnjih izvješća davatelja za 2024. (ISGO portal, „IRDJU 2024“, ožujak 2026.). Stupac `promjena_2026` navodi 23 jedinice kojima je davatelj promijenjen prema evidenciji javne usluge iz listopada 2026.
- `jls_davatelj_crosscheck.csv`: ista usporedba, red po jedinici, s popisom iz 2022. i evidencijom iz 2026.
- `izvori.csv`: za 33 najveća grada (i općine koje dijele istog davatelja). Sadrži stranicu s rasporedom, format, razinu detalja (zona, ulica, kućni broj), oblik rasporeda (pravila ili datumi), način objave pomaka zbog blagdana, vrste otpada, procjenu napora i status skripte.

## Ukratko

U Hrvatskoj odvoz obavlja 195 davatelja u 556 jedinica lokalne samouprave. Najveći po broju općina su Eko-Flor Plus (48), Čistoća Zadar (19), Pre-Kom (14) i Čistoća Varaždin (13). Po broju ljudi prednjače Zagreb (767 tisuća) i Čistoća Split (205 tisuća). Mull-Trans je od 2026. preuzeo dio općina Eko-Flora.

Kako se rasporedi objavljuju:

| Način | Gradovi | Što to znači za skripte |
|---|---|---|
| Javni API ili JSON | Zagreb, Osijek, Zadar, Varaždin, Slavonski Brod, Požega, Pula | Najpouzdanije, ali mnogo upita; Požega i Pula daju samo nekoliko mjeseci unaprijed |
| PDF ili HTML s datumima | Karlovac, Koprivnica, Samobor, Kutina, Vukovar, Bjelovar, Sisak | Čita se iz teksta, uz provjeru stupaca |
| Pravila („ponedjeljkom“, „2. i 4. utorak“) | Velika Gorica, Zaprešić, Šibenik, Split, Slavonski Brod, Vinkovci | Datumi se računaju (`pravila.py`); blagdane treba znati posebno |
| Kalendar u boji (PDF) | Rijeka, Čakovec, Kaštela, Makarska, Dugo Selo, Đakovo, Mull-Trans | Boja polja = vrsta otpada (`kalendar_boje.py`) |
| Slike ili sken | Trogir, Dubrovnik, Virovitica, Draganić | Ručni unos ili čitanje boja iz slike |
| Nedostupno odavde | Rovinj (Cloudflare), Poreč (preuzimanje se prekida) | Ručno preuzimanje |

Zapažanja:

- **Split nema raspored za 2026.** Na stranici su samo tekstualna pravila po blokovima, s pokvarenim slovima, a velik dio grada je na zajedničkim kontejnerima. Najbolje je zatražiti podatke od Čistoće Split.
- **Centri Rijeke, Pule i Dubrovnika** većinom imaju zajedničke kontejnere, pa za ta kućanstva nema dana odvoza.
- **Blagdani se rješavaju na četiri načina:**
  - pomak je ugrađen u datume (Sisak, Karlovac, Koprivnica, Požega);
  - objavljeno je pravilo (Velika Gorica, Kutina, Krapina, Sisak);
  - pomak se objavljuje samo u vijestima (Osijek, Slavonski Brod, Zaprešić, Split);
  - pomaka nema, odvoz je i na blagdane (Zagreb, Rijeka, Zadar, Varaždin).
- **Neki izvori su zastarjeli ili ih postoji više:** Šibenik je iz 2024., Zaprešić iz 2025., a za Svetu Nedelju gradska i Mull-Transova kopija PDF-a se razlikuju.
- **Ulice su često skraćene** („M. Krleže“) ili podijeljene po kućnim brojevima i parnosti. Za pretragu po ulici treba ih uskladiti sa službenim registrom (Zagreb ima otvoreni registar ulica).
