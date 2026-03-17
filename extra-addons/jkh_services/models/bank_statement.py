from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class JkhBankStatement(models.Model):
    """Банковская выписка (импорт из 1С формата)"""
    _name = 'jkh.bank.statement'
    _description = 'Банковская выписка'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, name desc'

    name = fields.Char(
        string='Номер',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Новый'),
    )
    date = fields.Date(
        string='Дата выписки',
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    bank_account_id = fields.Many2one(
        'res.partner.bank',
        string='Банковский счёт',
        domain="[('company_id', '=', company_id)]",
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Журнал банка',
        domain=[('type', '=', 'bank')],
        required=True,
    )
    balance_start = fields.Monetary(
        string='Входящий остаток',
        tracking=True,
    )
    balance_end = fields.Monetary(
        string='Исходящий остаток',
        compute='_compute_balance_end',
        store=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
    )
    line_ids = fields.One2many(
        'jkh.bank.statement.line',
        'statement_id',
        string='Операции',
    )
    total_debit = fields.Monetary(
        string='Итого приход',
        compute='_compute_totals',
        store=True,
    )
    total_credit = fields.Monetary(
        string='Итого расход',
        compute='_compute_totals',
        store=True,
    )
    state = fields.Selection([
        ('draft', 'Черновик'),
        ('posted', 'Проведена'),
    ], default='draft', string='Статус', tracking=True)

    imported_file = fields.Char(string='Имя импортированного файла', readonly=True)

    @api.depends('line_ids.amount')
    def _compute_totals(self):
        for stmt in self:
            stmt.total_debit = sum(l.amount for l in stmt.line_ids if l.amount > 0)
            stmt.total_credit = abs(sum(l.amount for l in stmt.line_ids if l.amount < 0))

    @api.depends('balance_start', 'total_debit', 'total_credit')
    def _compute_balance_end(self):
        for stmt in self:
            stmt.balance_end = stmt.balance_start + stmt.total_debit - stmt.total_credit

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Новый')) == _('Новый'):
                vals['name'] = self.env['ir.sequence'].next_by_code('jkh.bank.statement') or _('Новый')
        return super().create(vals_list)

    def action_post(self):
        """Провести выписку — создать банковские транзакции в Odoo"""
        for stmt in self:
            if stmt.state == 'posted':
                raise UserError(_('Выписка уже проведена.'))
            for line in stmt.line_ids:
                if not line.partner_id:
                    raise UserError(
                        _('Строка "%s" не имеет контрагента. Сопоставьте все строки перед проведением.') % line.name
                    )
            stmt.state = 'posted'

    def action_import_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Импорт банковской выписки'),
            'res_model': 'jkh.bank.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_statement_id': self.id},
        }


class JkhBankStatementLine(models.Model):
    """Строки банковской выписки"""
    _name = 'jkh.bank.statement.line'
    _description = 'Строка банковской выписки'
    _order = 'date asc, id'

    statement_id = fields.Many2one(
        'jkh.bank.statement',
        string='Выписка',
        required=True,
        ondelete='cascade',
    )
    date = fields.Date(string='Дата', required=True)
    name = fields.Char(string='Назначение платежа', required=True)
    partner_id = fields.Many2one('res.partner', string='Контрагент')
    partner_inn = fields.Char(string='ИНН контрагента')
    partner_account = fields.Char(string='Счёт контрагента')
    amount = fields.Monetary(
        string='Сумма (+ приход, - расход)',
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(related='statement_id.currency_id', store=True)
    ref = fields.Char(string='Номер документа')
    payment_order_number = fields.Char(string='Номер п/п')
    is_matched = fields.Boolean(string='Сопоставлен', default=False)
    note = fields.Text(string='Примечание')

    @api.onchange('partner_inn')
    def _onchange_partner_inn(self):
        if self.partner_inn:
            partner = self.env['res.partner'].search([('vat', '=', self.partner_inn)], limit=1)
            if partner:
                self.partner_id = partner
