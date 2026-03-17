from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ReconciliationAct(models.Model):
    """Акт сверки взаиморасчётов с контрагентом"""
    _name = 'jkh.reconciliation.act'
    _description = 'Акт сверки взаиморасчётов'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, name desc'

    name = fields.Char(
        string='Номер',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Новый'),
        tracking=True,
    )
    date = fields.Date(
        string='Дата составления',
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Контрагент',
        required=True,
        tracking=True,
    )
    period_from = fields.Date(
        string='Период с',
        required=True,
        tracking=True,
    )
    period_to = fields.Date(
        string='Период по',
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Организация',
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    line_ids = fields.One2many(
        'jkh.reconciliation.act.line',
        'act_id',
        string='Операции',
    )
    balance_start = fields.Monetary(
        string='Сальдо на начало периода',
        tracking=True,
    )
    debit_total = fields.Monetary(
        string='Итого оборот (дебет)',
        compute='_compute_totals',
        store=True,
    )
    credit_total = fields.Monetary(
        string='Итого оборот (кредит)',
        compute='_compute_totals',
        store=True,
    )
    balance_end = fields.Monetary(
        string='Сальдо на конец периода',
        compute='_compute_totals',
        store=True,
    )
    state = fields.Selection([
        ('draft', 'Черновик'),
        ('sent', 'Отправлен'),
        ('confirmed', 'Подтверждён'),
        ('signed', 'Подписан'),
    ], string='Статус', default='draft', tracking=True)

    note = fields.Text(string='Примечание')
    email_sent = fields.Boolean(string='Отправлен по email', default=False)

    @api.depends('line_ids.debit', 'line_ids.credit', 'balance_start')
    def _compute_totals(self):
        for act in self:
            act.debit_total = sum(act.line_ids.mapped('debit'))
            act.credit_total = sum(act.line_ids.mapped('credit'))
            act.balance_end = act.balance_start + act.debit_total - act.credit_total

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Новый')) == _('Новый'):
                vals['name'] = self.env['ir.sequence'].next_by_code('jkh.reconciliation.act') or _('Новый')
        return super().create(vals_list)

    def action_generate_from_moves(self):
        """
        Автозаполнение строк акта сверки из трёх источников (в порядке приоритета):
        1. Проведённые бухгалтерские проводки (account.move.line) — приоритет
        2. Подтверждённые акты реализации (jkh.service.act) — если проводок нет
        3. Проведённые строки банковских выписок (jkh.bank.statement.line)
        """
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('Укажите контрагента перед заполнением.'))
        if not self.period_from or not self.period_to:
            raise UserError(_('Укажите период сверки.'))

        self.line_ids.unlink()
        raw_lines = []

        # --- Источник 1: бухгалтерские проводки ---
        acc_domain = [
            ('partner_id', '=', self.partner_id.id),
            ('date', '>=', self.period_from),
            ('date', '<=', self.period_to),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', 'in', ['asset_receivable', 'liability_payable']),
        ]
        move_lines = self.env['account.move.line'].search(acc_domain, order='date asc')
        for ml in move_lines:
            raw_lines.append({
                'date': ml.date,
                'document': ml.move_id.name,
                'description': ml.name or ml.move_id.ref or ml.move_id.name or '',
                'debit': ml.debit,
                'credit': ml.credit,
                'move_line_id': ml.id,
                'source': 'accounting',
            })

        # --- Источник 2: акты реализации (если нет бухгалтерских проводок) ---
        if not raw_lines:
            service_acts = self.env['jkh.service.act'].search([
                ('partner_id', '=', self.partner_id.id),
                ('date', '>=', self.period_from),
                ('date', '<=', self.period_to),
                ('state', 'in', ['confirmed', 'sent', 'done']),
            ], order='date asc')
            for act in service_acts:
                raw_lines.append({
                    'date': act.date,
                    'document': act.name,
                    'description': 'Реализация услуг за период %s \u2013 %s' % (
                        act.period_from, act.period_to
                    ),
                    'debit': act.amount_total,
                    'credit': 0.0,
                    'source': 'service_act',
                })

        # --- Источник 3: банковские выписки (оплаты от контрагента) ---
        bank_lines = self.env['jkh.bank.statement.line'].search([
            ('partner_id', '=', self.partner_id.id),
            ('date', '>=', self.period_from),
            ('date', '<=', self.period_to),
            ('statement_id.state', '=', 'posted'),
        ], order='date asc')
        for bl in bank_lines:
            if bl.amount > 0:
                raw_lines.append({
                    'date': bl.date,
                    'document': bl.payment_order_number or bl.ref or '',
                    'description': bl.name or 'Оплата',
                    'debit': 0.0,
                    'credit': bl.amount,
                    'source': 'bank',
                })
            elif bl.amount < 0:
                raw_lines.append({
                    'date': bl.date,
                    'document': bl.payment_order_number or bl.ref or '',
                    'description': bl.name or 'Списание',
                    'debit': abs(bl.amount),
                    'credit': 0.0,
                    'source': 'bank',
                })

        if not raw_lines:
            raise UserError(_(
                'За период с %s по %s не найдено ни подтверждённых актов реализации, '
                'ни бухгалтерских проводок, ни банковских операций для контрагента "%s".'
            ) % (self.period_from, self.period_to, self.partner_id.name))

        # Сортируем по дате и создаём строки
        raw_lines.sort(key=lambda x: x['date'])
        lines_to_create = []
        for row in raw_lines:
            vals = {
                'date': row['date'],
                'document': row['document'],
                'description': row['description'],
                'debit': row['debit'],
                'credit': row['credit'],
            }
            if row.get('move_line_id'):
                vals['move_line_id'] = row['move_line_id']
            lines_to_create.append((0, 0, vals))

        self.line_ids = lines_to_create

        # Отображаем количество найденных строк
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Акт сверки заполнен'),
                'message': _('Добавлено строк: %d') % len(lines_to_create),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_send_email(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Отправить акт сверки по email'),
            'res_model': 'jkh.send.act.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_reconciliation_ids': self.ids,
                'default_act_type': 'reconciliation',
            },
        }

    def action_confirm(self):
        self.state = 'confirmed'

    def action_signed(self):
        self.state = 'signed'


class ReconciliationActLine(models.Model):
    """Строки акта сверки"""
    _name = 'jkh.reconciliation.act.line'
    _description = 'Строка акта сверки'
    _order = 'date asc, id'

    act_id = fields.Many2one(
        'jkh.reconciliation.act',
        string='Акт сверки',
        required=True,
        ondelete='cascade',
    )
    date = fields.Date(string='Дата', required=True)
    document = fields.Char(string='Документ')
    description = fields.Char(string='Наименование')
    debit = fields.Monetary(string='Дебет (нам должны)', currency_field='currency_id')
    credit = fields.Monetary(string='Кредит (мы должны)', currency_field='currency_id')
    currency_id = fields.Many2one(related='act_id.currency_id', store=True)
    move_line_id = fields.Many2one('account.move.line', string='Проводка', readonly=True)
