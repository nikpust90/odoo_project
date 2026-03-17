from odoo import models, fields, api, _
from odoo.exceptions import UserError
import base64
import io
import logging
from datetime import datetime

_logger = logging.getLogger(__name__)


class BankImportWizard(models.TransientModel):
    """
    Визард импорта банковских выписок.
    Поддерживает форматы:
    - 1С v8 (текстовый формат *.txt / *.1c)
    - CSV (разделитель точка с запятой)
    """
    _name = 'jkh.bank.import.wizard'
    _description = 'Импорт банковской выписки'

    statement_id = fields.Many2one(
        'jkh.bank.statement',
        string='Выписка',
    )
    import_format = fields.Selection([
        ('1c', '1С v8 (текстовый формат)'),
        ('csv', 'CSV (разделитель ";")'),
    ], string='Формат файла', required=True, default='1c')

    file_data = fields.Binary(string='Файл выписки', required=True)
    file_name = fields.Char(string='Имя файла')

    journal_id = fields.Many2one(
        'account.journal',
        string='Журнал банка',
        domain=[('type', '=', 'bank')],
        required=True,
    )

    preview_lines = fields.Text(
        string='Предварительный просмотр',
        readonly=True,
    )
    lines_count = fields.Integer(string='Строк найдено', readonly=True)

    def _parse_1c_format(self, content):
        """
        Парсит формат 1С v8 (КодировкаФайла, ВерсияФормата 1.02/1.03).
        Структура: секции, разделённые СекцияДокумент / КонецДокумента.
        """
        lines = content.split('\n')
        records = []
        current = {}
        in_document = False
        meta = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line == 'СекцияДокумент':
                in_document = True
                current = {}
                continue
            if line == 'КонецДокумента':
                if current:
                    records.append(current)
                in_document = False
                current = {}
                continue
            if line in ('КонецФайла',):
                break

            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                if in_document:
                    current[key] = value
                else:
                    meta[key] = value

        return meta, records

    def _parse_csv_format(self, content):
        """Парсит CSV формат с разделителем ;"""
        import csv
        records = []
        reader = csv.DictReader(io.StringIO(content), delimiter=';')
        for row in reader:
            records.append(dict(row))
        return {}, records

    def _normalize_record(self, raw, fmt):
        """Нормализует запись из любого формата в единый словарь"""
        if fmt == '1c':
            date_str = raw.get('ДатаДок', raw.get('Дата', ''))
            try:
                date_val = datetime.strptime(date_str, '%d.%m.%Y').date()
            except (ValueError, TypeError):
                date_val = None

            amount_str = raw.get('Сумма', '0').replace(',', '.').replace(' ', '')
            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            direction = raw.get('ВидДокумента', raw.get('Приход', ''))
            if raw.get('Расход'):
                amount = -abs(amount)
            elif raw.get('Приход'):
                amount = abs(amount)

            return {
                'date': date_val,
                'name': raw.get('НазначениеПлатежа', raw.get('Основание', ''))[:500],
                'partner_inn': raw.get('ИНН', ''),
                'partner_name': raw.get('ПолучательРасчСчет', raw.get('ПлательщикРасчСчет', '')),
                'amount': amount,
                'ref': raw.get('НомерДок', raw.get('Номер', '')),
                'payment_order_number': raw.get('НомерДок', ''),
                'partner_account': raw.get('ПолучательСчет', raw.get('ПлательщикСчет', '')),
            }
        else:
            date_str = raw.get('date', raw.get('Дата', ''))
            try:
                date_val = datetime.strptime(date_str, '%d.%m.%Y').date()
            except (ValueError, TypeError):
                date_val = None

            amount_str = str(raw.get('amount', raw.get('Сумма', '0'))).replace(',', '.').replace(' ', '')
            try:
                amount = float(amount_str)
            except ValueError:
                amount = 0.0

            return {
                'date': date_val,
                'name': raw.get('purpose', raw.get('НазначениеПлатежа', ''))[:500],
                'partner_inn': raw.get('inn', raw.get('ИНН', '')),
                'partner_name': raw.get('partner', raw.get('Контрагент', '')),
                'amount': amount,
                'ref': raw.get('ref', raw.get('Номер', '')),
                'payment_order_number': raw.get('payment_order', ''),
                'partner_account': raw.get('account', ''),
            }

    def action_preview(self):
        """Предварительный просмотр без сохранения"""
        self.ensure_one()
        if not self.file_data:
            raise UserError(_('Загрузите файл выписки.'))

        content = base64.b64decode(self.file_data).decode('cp1251', errors='replace')

        if self.import_format == '1c':
            _, records = self._parse_1c_format(content)
        else:
            _, records = self._parse_csv_format(content)

        preview_lines = []
        for i, rec in enumerate(records[:10]):
            norm = self._normalize_record(rec, self.import_format)
            preview_lines.append(
                f"{norm['date']} | {norm['amount']:>12.2f} | {norm['partner_inn']:>12} | {norm['name'][:60]}"
            )

        self.preview_lines = '\n'.join(preview_lines)
        self.lines_count = len(records)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_import(self):
        """Импортировать выписку и создать строки"""
        self.ensure_one()
        if not self.file_data:
            raise UserError(_('Загрузите файл выписки.'))

        content = base64.b64decode(self.file_data).decode('cp1251', errors='replace')

        if self.import_format == '1c':
            meta, records = self._parse_1c_format(content)
        else:
            meta, records = self._parse_csv_format(content)

        if not records:
            raise UserError(_('Файл не содержит данных или формат не распознан.'))

        # Создаём или используем существующую выписку
        if not self.statement_id:
            stmt_date = None
            if meta.get('ДатаНачала'):
                try:
                    stmt_date = datetime.strptime(meta['ДатаНачала'], '%d.%m.%Y').date()
                except ValueError:
                    pass

            self.statement_id = self.env['jkh.bank.statement'].create({
                'journal_id': self.journal_id.id,
                'date': stmt_date or fields.Date.context_today(self),
                'imported_file': self.file_name or 'import',
            })

        stmt = self.statement_id
        stmt.line_ids.unlink()

        lines_to_create = []
        for rec in records:
            norm = self._normalize_record(rec, self.import_format)
            if not norm['date']:
                continue

            partner = None
            if norm['partner_inn']:
                partner = self.env['res.partner'].search(
                    [('vat', '=', norm['partner_inn'])], limit=1
                )

            lines_to_create.append({
                'statement_id': stmt.id,
                'date': norm['date'],
                'name': norm['name'] or 'Без назначения',
                'amount': norm['amount'],
                'partner_id': partner.id if partner else False,
                'partner_inn': norm['partner_inn'],
                'partner_account': norm['partner_account'],
                'ref': norm['ref'],
                'payment_order_number': norm['payment_order_number'],
                'is_matched': bool(partner),
            })

        self.env['jkh.bank.statement.line'].create(lines_to_create)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Банковская выписка'),
            'res_model': 'jkh.bank.statement',
            'res_id': stmt.id,
            'view_mode': 'form',
        }
