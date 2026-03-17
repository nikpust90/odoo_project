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
        """Автоматически заполнить строки из проводок бухгалтерии"""
        self.ensure_one()
        self.line_ids.unlink()

        domain = [
            ('partner_id', '=', self.partner_id.id),
            ('date', '>=', self.period_from),
            ('date', '<=', self.period_to),
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', 'in', ['asset_receivable', 'liability_payable']),
        ]
        move_lines = self.env['account.move.line'].search(domain, order='date asc')

        lines_to_create = []
        for ml in move_lines:
            lines_to_create.append((0, 0, {
                'date': ml.date,
                'document': ml.move_id.name,
                'description': ml.name or ml.move_id.ref or '',
                'debit': ml.debit,
                'credit': ml.credit,
                'move_line_id': ml.id,
            }))
        self.line_ids = lines_to_create
        return True

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
