from __future__ import annotations

"""
Salary Payment Generator – Flask application
"""


import json
import os
import uuid
from datetime import datetime

from flask import (
    Flask, flash, redirect, render_template, request,
    send_from_directory, url_for,
)
from werkzeug.utils import secure_filename

import accounts as accts
from excel_parser import parse_excel
from generator import (
    generate_bonus_xml, generate_perdiem_xml, generate_salary_xml,
    load_config, next_target2_date, save_config,
)

# ── App setup ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
GENERATED_DIR = os.path.join(DATA_DIR, 'generated')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

for d in (UPLOAD_DIR, GENERATED_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'salary-generator-secret-2026')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB


# ── Helpers ────────────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def mask_iban(iban: str) -> str:
    if not iban or len(iban) < 8:
        return iban or ''
    return iban[:4] + '****' + iban[-4:]


def _load_history() -> list:
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save_history(history: list):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _resolve_accounts(records: list[dict]) -> list[dict]:
    """
    For each record, determine the IBAN source, update the DB, and tag the status.

    Statuses:
      OK        – IBAN resolved (from Excel or DB)
      NEW       – Excel provided an IBAN not previously in DB → saved
      UPDATED   – Excel provided an IBAN different from DB → updated
      MISSING   – No IBAN in Excel and not in DB → excluded from XML
    """
    resolved = []
    for rec in records:
        excel_iban = rec.get('iban')
        db_iban = accts.get_iban(rec['name'])

        if excel_iban:
            excel_iban_clean = excel_iban.upper().replace(' ', '')
            if not db_iban:
                status = 'NEW'
                accts.update_iban(rec['name'], excel_iban_clean, source='excel')
            elif db_iban != excel_iban_clean:
                status = 'UPDATED'
                accts.update_iban(rec['name'], excel_iban_clean, source='excel')
            else:
                status = 'OK'
            final_iban = excel_iban_clean
        else:
            if db_iban:
                status = 'OK'
                final_iban = db_iban
            else:
                status = 'MISSING'
                final_iban = None

        resolved.append({**rec, 'iban': final_iban, 'status': status})

    return resolved


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    config = load_config()
    return render_template('index.html', config=config)


@app.route('/upload', methods=['POST'])
def upload():
    # ── Company config (may be overridden per upload) ──
    config = load_config()
    config['company_name'] = request.form.get('company_name', config['company_name']).strip()
    config['debtor_iban'] = request.form.get('debtor_iban', config['debtor_iban']).strip()
    config['debtor_bic'] = request.form.get('debtor_bic', config['debtor_bic']).strip()
    save_config(config)

    # ── File validation ──
    if 'file' not in request.files:
        flash('No file part in request.', 'danger')
        return redirect(url_for('index'))
    f = request.files['file']
    if f.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('index'))
    if not allowed_file(f.filename):
        flash('Only .xlsx / .xls files are accepted.', 'danger')
        return redirect(url_for('index'))

    # ── Save upload ──
    uid = str(uuid.uuid4())[:8]
    safe_name = secure_filename(f.filename)
    upload_path = os.path.join(UPLOAD_DIR, f"{uid}_{safe_name}")
    f.save(upload_path)

    # ── Parse ──
    try:
        parsed = parse_excel(upload_path)
    except Exception as e:
        flash(f'Could not parse Excel file: {e}', 'danger')
        return redirect(url_for('index'))

    month = parsed['month']
    year = parsed['year']

    # ── Resolve accounts for salary records ──
    salary_resolved = _resolve_accounts(parsed['salary_records'])

    # ── Sync IBAN back into per-diem and bonus records from resolved salary records ──
    iban_by_name = {r['name']: r['iban'] for r in salary_resolved}
    status_by_name = {r['name']: r['status'] for r in salary_resolved}

    def _sync_iban(records):
        result = []
        for rec in records:
            iban = iban_by_name.get(rec['name']) or rec.get('iban')
            db_iban = accts.get_iban(rec['name'])
            final_iban = iban or db_iban
            status = 'MISSING' if not final_iban else 'OK'
            result.append({**rec, 'iban': final_iban, 'status': status})
        return result

    perdiem_resolved = _sync_iban(parsed['perdiem_records'])
    bonus_resolved = _sync_iban(parsed['bonus_records'])

    # ── Generate XMLs ──
    exec_date = next_target2_date()
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    company_slug = config['company_name'].replace(' ', '').replace('.', '').replace(',', '')
    month_str = f"{month:02d}"
    salary_filename = f"{company_slug}_salary_{month_str}_{year}.xml"
    perdiem_filename = f"{company_slug}_per_diem_{month_str}_{year}.xml"
    bonus_filename = f"{company_slug}_legal_bonus_{month_str}_{year}.xml"

    sal_ok = [r for r in salary_resolved if r['status'] != 'MISSING']
    pd_ok  = [r for r in perdiem_resolved if r['status'] != 'MISSING']
    bon_ok = [r for r in bonus_resolved if r['status'] != 'MISSING']

    salary_xml_bytes = generate_salary_xml(sal_ok, month, year, exec_date, timestamp, config)
    perdiem_xml_bytes = generate_perdiem_xml(pd_ok, month, year, exec_date, timestamp, config)

    salary_out = os.path.join(GENERATED_DIR, salary_filename)
    perdiem_out = os.path.join(GENERATED_DIR, perdiem_filename)

    with open(salary_out, 'wb') as fh:
        fh.write(salary_xml_bytes)
    with open(perdiem_out, 'wb') as fh:
        fh.write(perdiem_xml_bytes)

    # Only generate bonus XML when there are bonus payments
    has_bonus = len(bon_ok) > 0
    if has_bonus:
        bonus_xml_bytes = generate_bonus_xml(bon_ok, month, year, exec_date, timestamp, config)
        bonus_out = os.path.join(GENERATED_DIR, bonus_filename)
        with open(bonus_out, 'wb') as fh:
            fh.write(bonus_xml_bytes)

    # ── History ──
    missing_count = sum(1 for r in salary_resolved if r['status'] == 'MISSING')
    missing_count += sum(1 for r in perdiem_resolved if r['status'] == 'MISSING')
    missing_count += sum(1 for r in bonus_resolved if r['status'] == 'MISSING')
    history_entry = {
        'id': uid,
        'timestamp': datetime.now().isoformat(),
        'original_filename': safe_name,
        'month': month,
        'year': year,
        'salary_count': len(sal_ok),
        'perdiem_count': len(pd_ok),
        'bonus_count': len(bon_ok),
        'missing_count': missing_count,
        'salary_xml': salary_filename,
        'perdiem_xml': perdiem_filename,
    }
    if has_bonus:
        history_entry['bonus_xml'] = bonus_filename
    history = _load_history()
    history.insert(0, history_entry)
    _save_history(history[:50])  # keep last 50

    # ── Build result table (salary + perdiem + bonus rows) ──
    result_rows = []
    for r in salary_resolved:
        result_rows.append({
            'name': r['name'],
            'type': 'Salary',
            'amount': r['amount'],
            'iban': r['iban'],
            'masked_iban': mask_iban(r['iban']) if r['iban'] else '—',
            'status': r['status'],
        })
    for r in perdiem_resolved:
        result_rows.append({
            'name': r['name'],
            'type': 'Per Diem',
            'amount': r['amount'],
            'iban': r['iban'],
            'masked_iban': mask_iban(r['iban']) if r['iban'] else '—',
            'status': r['status'],
        })
    for r in bonus_resolved:
        result_rows.append({
            'name': r['name'],
            'type': 'Legal Bonus',
            'amount': r['amount'],
            'iban': r['iban'],
            'masked_iban': mask_iban(r['iban']) if r['iban'] else '—',
            'status': r['status'],
        })

    notifications = [r for r in salary_resolved if r['status'] in ('NEW', 'UPDATED')]
    missing = [r for r in result_rows if r['status'] == 'MISSING']

    return render_template(
        'result.html',
        month=month,
        year=year,
        exec_date=exec_date,
        result_rows=result_rows,
        notifications=notifications,
        missing=missing,
        salary_filename=salary_filename,
        perdiem_filename=perdiem_filename,
        bonus_filename=bonus_filename if has_bonus else None,
        salary_total=sum(r['amount'] for r in sal_ok),
        perdiem_total=sum(r['amount'] for r in pd_ok),
        bonus_total=sum(r['amount'] for r in bon_ok) if has_bonus else 0,
    )


@app.route('/download/<filename>')
def download(filename):
    safe = secure_filename(filename)
    return send_from_directory(GENERATED_DIR, safe, as_attachment=True)


# ── Bank Accounts page ─────────────────────────────────────────────────────────

@app.route('/accounts')
def accounts_page():
    all_accounts = accts.get_all()
    entries = sorted(all_accounts.values(), key=lambda x: x.get('name', ''))
    return render_template('accounts.html', entries=entries)


@app.route('/accounts/add', methods=['POST'])
def add_account():
    name = request.form.get('name', '').strip().upper()
    iban = request.form.get('iban', '').strip().upper().replace(' ', '')
    if not name or not iban:
        flash('Name and IBAN are required.', 'danger')
        return redirect(url_for('accounts_page'))
    accts.update_iban(name, iban, source='manual')
    flash(f'Account for {name} saved.', 'success')
    return redirect(url_for('accounts_page'))


@app.route('/accounts/edit/<path:name>', methods=['POST'])
def edit_account(name: str):
    iban = request.form.get('iban', '').strip().upper().replace(' ', '')
    if not iban:
        flash('IBAN is required.', 'danger')
        return redirect(url_for('accounts_page'))
    accts.update_iban(name, iban, source='manual')
    flash(f'Account for {name} updated.', 'success')
    return redirect(url_for('accounts_page'))


@app.route('/accounts/delete/<path:name>', methods=['POST'])
def delete_account(name: str):
    accts.delete_account(name)
    flash(f'Account for {name} deleted.', 'success')
    return redirect(url_for('accounts_page'))


# ── History page ───────────────────────────────────────────────────────────────

@app.route('/history')
def history():
    entries = _load_history()
    return render_template('history.html', entries=entries)


# ── Settings (company config) ─────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    config = load_config()
    if request.method == 'POST':
        config['company_name'] = request.form.get('company_name', '').strip()
        config['debtor_iban'] = request.form.get('debtor_iban', '').strip().upper().replace(' ', '')
        config['debtor_bic'] = request.form.get('debtor_bic', '').strip().upper()
        save_config(config)
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', config=config)


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=5001)
