from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import date
import logging

_logger = logging.getLogger(__name__)


class ServiceAct(models.Model):
    """Документ реализации услуг (аналог документа реализации в 1С)"""
    _name = 'jkh.service.act'
    _description = 'Реализация услуг'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, name desc'

    name = fields.Char(
        string='Номер документа',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Новый'),
        tracking=True,
    )
    date = fields.Date(
        string='Дата',
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Контрагент',
        required=True,
        tracking=True,
        domain=[('is_company', '=', True)],
    )
    address = fields.Char(
        string='Адрес объекта',
        related='partner_id.street',
        store=True,
    )
    contract_number = fields.Char(
        string='Номер договора',
        tracking=True,
    )
    contract_date = fields.Date(
        string='Дата договора',
    )
    period_from = fields.Date(
        string='Период с',
        required=True,
    )
    period_to = fields.Date(
        string='Период по',
        required=True,
    )
    line_ids = fields.One2many(
        'jkh.service.act.line',
        'act_id',
        string='Услуги',
        copy=True,
    )
    amount_untaxed = fields.Monetary(
        string='Сумма без НДС',
        compute='_compute_amounts',
        store=True,
    )
    amount_tax = fields.Monetary(
        string='НДС',
        compute='_compute_amounts',
        store=True,
    )
    amount_total = fields.Monetary(
        string='Итого',
        compute='_compute_amounts',
        store=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Валюта',
        default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection([
        ('draft', 'Черновик'),
        ('confirmed', 'Подтверждён'),
        ('sent', 'Отправлен'),
        ('done', 'Закрыт'),
        ('cancel', 'Отменён'),
    ], string='Статус', default='draft', tracking=True)

    note = fields.Text(string='Примечание')

    invoice_id = fields.Many2one(
        'account.move',
        string='Счёт-фактура',
        readonly=True,
        copy=False,
    )

    email_sent = fields.Boolean(
        string='Акт отправлен по email',
        default=False,
        readonly=True,
    )

    sbis_exported = fields.Boolean(
        string='Выгружено в СБИС',
        default=False,
        readonly=True,
    )

    sbis_export_date = fields.Datetime(
        string='Дата выгрузки в СБИС',
        readonly=True,
    )

    company_id = fields.Many2one(
        'res.company',
        string='Организация',
        default=lambda self: self.env.company,
    )

    @api.depends('line_ids.price_subtotal', 'line_ids.price_tax')
    def _compute_amounts(self):
        for act in self:
            act.amount_untaxed = sum(act.line_ids.mapped('price_subtotal'))
            act.amount_tax = sum(act.line_ids.mapped('price_tax'))
            act.amount_total = act.amount_untaxed + act.amount_tax

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Новый')) == _('Новый'):
                vals['name'] = self.env['ir.sequence'].next_by_code('jkh.service.act') or _('Новый')
        return super().create(vals_list)

    def action_confirm(self):
        for act in self:
            if not act.line_ids:
                raise UserError(_('Невозможно подтвердить акт без строк услуг.'))
            act.state = 'confirmed'

    def action_cancel(self):
        for act in self:
            if act.state == 'done':
                raise UserError(_('Нельзя отменить закрытый документ.'))
            act.state = 'cancel'

    def action_draft(self):
        self.state = 'draft'

    def action_done(self):
        self.state = 'done'

    def action_send_email(self):
        """Открыть визард отправки по email"""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Отправить акт по email'),
            'res_model': 'jkh.send.act.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_act_ids': self.ids,
                'default_act_type': 'service',
            },
        }

    def action_export_sbis(self):
        """Открыть визард экспорта в СБИС"""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Экспорт в СБИС XML'),
            'res_model': 'jkh.sbis.export.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_act_ids': self.ids,
            },
        }

    def action_create_invoice(self):
        """Создать счёт на основе акта реализации"""
        self.ensure_one()
        if self.invoice_id:
            raise UserError(_('Счёт уже создан: %s') % self.invoice_id.name)

        invoice_lines = []
        for line in self.line_ids:
            invoice_lines.append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': line.quantity,
                'price_unit': line.price_unit,
                'tax_ids': line.tax_ids.ids,
            }))

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': self.date,
            'ref': self.name,
            'invoice_line_ids': invoice_lines,
        })
        self.invoice_id = invoice
        return {
            'type': 'ir.actions.act_window',
            'name': _('Счёт'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
        }


class ServiceActLine(models.Model):
    """Строки документа реализации услуг"""
    _name = 'jkh.service.act.line'
    _description = 'Строка реализации услуг'
    _order = 'sequence, id'

    act_id = fields.Many2one(
        'jkh.service.act',
        string='Акт',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(string='№', default=10)
    product_id = fields.Many2one(
        'product.product',
        string='Услуга/Товар',
        required=True,
        domain=[('type', 'in', ['service', 'consu'])],
    )
    name = fields.Char(
        string='Наименование',
        required=True,
    )
    quantity = fields.Float(
        string='Количество',
        default=1.0,
        digits='Product Unit of Measure',
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Ед. изм.',
    )
    price_unit = fields.Float(
        string='Цена',
        digits='Product Price',
    )
    tax_ids = fields.Many2many(
        'account.tax',
        string='Налоги',
    )
    price_subtotal = fields.Monetary(
        string='Сумма без НДС',
        compute='_compute_price',
        store=True,
        currency_field='currency_id',
    )
    price_tax = fields.Monetary(
        string='НДС',
        compute='_compute_price',
        store=True,
        currency_field='currency_id',
    )
    price_total = fields.Monetary(
        string='Итого',
        compute='_compute_price',
        store=True,
        currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        related='act_id.currency_id',
        store=True,
    )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.name = self.product_id.name
            self.price_unit = self.product_id.lst_price
            self.uom_id = self.product_id.uom_id
            self.tax_ids = self.product_id.taxes_id

    @api.depends('quantity', 'price_unit', 'tax_ids')
    def _compute_price(self):
        for line in self:
            subtotal = line.quantity * line.price_unit
            taxes = line.tax_ids.compute_all(
                line.price_unit,
                line.currency_id,
                line.quantity,
            )
            line.price_subtotal = taxes['total_excluded']
            line.price_tax = taxes['total_included'] - taxes['total_excluded']
            line.price_total = taxes['total_included']
