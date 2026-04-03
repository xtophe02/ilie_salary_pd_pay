# Salary Payment Generator

Upload a monthly Excel payroll file → generate 2 SEPA XML files (pain.001.001.03):
- **Salary XML** — one credit transfer per employee
- **Per-Diem XML** — one credit transfer per employee with a per-diem amount

## Quick start (Docker)

```bash
# Build and run
docker compose up -d

# Open in browser
open http://localhost:5000
```

The `./data/` folder is mounted as a volume — accounts DB, uploads, and generated XMLs
all survive container restarts.

## Expected Excel format

| Col | Header contains | Content |
|-----|----------------|---------|
| A   | SEN / NAME     | Employee full name (UPPERCASE) |
| B   | SALARY         | Net salary amount (EUR) |
| C   | PERDIEM / PER DIEM | Per-diem amount (EUR) — 0 or blank = no per-diem payment |
| D   | BONUS / LEGAL  | Optional bonus added to salary |
| E   | COMMENTS / IBAN | Employee IBAN (optional — looked up from DB if missing) |

The parser auto-detects column positions from header keywords.
Row 1 may be blank (like the Feb 2026 template) — the header row is found automatically.

## Bank account resolution

1. IBAN present in file → notify + use + update DB
2. IBAN absent in file → look up from `data/accounts_db.json`
3. Neither found → employee excluded from XML, shown as **MISSING** in UI

## XML structure

`pain.001.001.03` — namespace `urn:iso:std:iso:20022:tech:xsd:pain.001.001.03`

| Field | Value |
|-------|-------|
| Debtor | Configurable (default: VENTHONE S.A.) |
| Debtor IBAN | Configurable (default: LU570141073189750000) |
| Debtor BIC | Configurable (default: CELLLULL) |
| Creditor BIC | `NOTPROVIDED` |
| ChrgBr | `SLEV` |
| ReqdExctnDt | Next TARGET2 business day |

Holidays excluded: Jan 1, Good Friday, Easter Monday, May 1, Dec 25–26.

## File naming

```
{CompanyName}_salary_{MM}_{YYYY}.xml
{CompanyName}_per_diem_{MM}_{YYYY}.xml
```

## Project structure

```
salary-payment-generator/
├── app.py            Flask routes
├── generator.py      SEPA XML builder + TARGET2 calendar
├── excel_parser.py   Excel → employee records
├── accounts.py       Bank account database (JSON)
├── templates/        Jinja2 HTML templates
├── data/
│   ├── accounts_db.json   Known IBANs (pre-seeded from Dec 2025)
│   ├── config.json        Company/debtor settings
│   ├── history.json       Conversion log
│   ├── uploads/           Uploaded Excel files
│   └── generated/         Output XML files
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```
