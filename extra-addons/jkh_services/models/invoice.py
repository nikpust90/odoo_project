from odoo import models, fields, api, _
from odoo.exceptions import UserError


class JkhInvoice(models.Model):
    """Счёт на оплату — отдельный документ ЖКХ (не связан с account.move)"""
    _name = 'jkh.invoice'
    _description = 'Счёт на оплату'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, name desc'

    name = fields.Char(
        string='Номер счёта',
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
    date_due = fields.Date(
        string='Срок оплаты',
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Покупатель',
        required=True,
        tracking=True,
    )
    contract_number = fields.Char(string='Номер договора')
    contract_date = fields.Date(string='Дата договора')

    # Источник — может быть создан из акта реализации
    service_act_id = fields.Many2one(
        'jkh.service.act',
        string='На основании акта',
        copy=False,
        readonly=True,
    )

    line_ids = fields.One2many(
        'jkh.invoice.line',
        'invoice_id',
        string='Позиции',
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
        string='Итого к оплате',
        compute='_compute_amounts',
        store=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
    )

    # Банковские реквизиты для оплаты
    bank_account_id = fields.Many2one(
        'res.partner.bank',
        string='Расчётный счёт',
        domain="[('partner_id', '=', company_id)]",
    )
    bank_name = fields.Char(
        string='Банк',
        compute='_compute_bank_details',
        store=True,
    )
    bank_bic = fields.Char(
        string='БИК',
        compute='_compute_bank_details',
        store=True,
    )
    bank_account_number = fields.Char(
        string='Расчётный счёт №',
        compute='_compute_bank_details',
        store=True,
    )

    state = fields.Selection([
        ('draft', 'Черновик'),
        ('confirmed', 'Выставлен'),
        ('sent', 'Отправлен'),
        ('paid', 'Оплачен'),
        ('cancel', 'Отменён'),
    ], string='Статус', default='draft', tracking=True)

    note = fields.Text(string='Примечание / Назначение платежа')
    email_sent = fields.Boolean(string='Отправлен по email', default=False)
    sbis_exported = fields.Boolean(string='Выгружено в СБИС', default=False)

    @api.depends('line_ids.price_subtotal', 'line_ids.price_tax')
    def _compute_amounts(self):
        for inv in self:
            inv.amount_untaxed = sum(inv.line_ids.mapped('price_subtotal'))
            inv.amount_tax = sum(inv.line_ids.mapped('price_tax'))
            inv.amount_total = inv.amount_untaxed + inv.amount_tax

    @api.depends('bank_account_id')
    def _compute_bank_details(self):
        for inv in self:
            bank = inv.bank_account_id
            if bank:
                inv.bank_account_number = bank.acc_number or ''
                inv.bank_name = bank.bank_id.name if bank.bank_id else ''
                inv.bank_bic = bank.bank_id.bic if bank.bank_id else ''
            else:
                inv.bank_account_number = ''
                inv.bank_name = ''
                inv.bank_bic = ''

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Новый')) == _('Новый'):
                vals['name'] = self.env['ir.sequence'].next_by_code('jkh.invoice') or _('Новый')
        return super().create(vals_list)

    def action_confirm(self):
        for inv in self:
            if not inv.line_ids:
                raise UserError(_('Добавьте хотя бы одну позицию.'))
            inv.state = 'confirmed'

    def action_cancel(self):
        self.state = 'cancel'

    def action_draft(self):
        self.state = 'draft'

    def action_paid(self):
        self.state = 'paid'

    def action_preview_pdf(self):
        self.ensure_one()
        return self.env.ref('jkh_services.action_report_invoice').report_action(self)

    def action_send_email(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Отправить счёт по email'),
            'res_model': 'jkh.send.act.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_ids': self.ids,
                'default_act_type': 'invoice',
            },
        }

    def action_export_sbis(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Экспорт счёта в СБИС XML'),
            'res_model': 'jkh.sbis.export.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_ids': self.ids,
                'default_export_type': 'invoice',
            },
        }


class JkhInvoiceLine(models.Model):
    """Позиции счёта на оплату"""
    _name = 'jkh.invoice.line'
    _description = 'Позиция счёта'
    _order = 'sequence, id'

    invoice_id = fields.Many2one(
        'jkh.invoice',
        string='Счёт',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product',
        string='Услуга/Товар',
    )
    name = fields.Char(string='Наименование', required=True)
    quantity = fields.Float(string='Кол-во', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='Ед. изм.')
    price_unit = fields.Float(string='Цена', digits='Product Price')
    tax_ids = fields.Many2many('account.tax', string='НДС')

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
    currency_id = fields.Many2one(related='invoice_id.currency_id', store=True)

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
            taxes = line.tax_ids.compute_all(
                line.price_unit, line.currency_id, line.quantity,
            )
            line.price_subtotal = taxes['total_excluded']
            line.price_tax = taxes['total_included'] - taxes['total_excluded']
            line.price_total = taxes['total_included']
