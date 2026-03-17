from odoo import models, fields


class ResPartner(models.Model):
    """Расширение контрагента для ЖКХ"""
    _inherit = 'res.partner'

    inn = fields.Char(
        string='ИНН',
        related='vat',
        store=True,
    )
    kpp = fields.Char(string='КПП')
    ogrn = fields.Char(string='ОГРН/ОГРНИП')
    okpo = fields.Char(string='ОКПО')
    okved = fields.Char(string='ОКВЭД')

    contract_ids = fields.One2many(
        'jkh.service.act',
        'partner_id',
        string='Акты реализации',
    )
    service_act_count = fields.Integer(
        string='Актов реализации',
        compute='_compute_service_act_count',
    )
    reconciliation_act_count = fields.Integer(
        string='Актов сверки',
        compute='_compute_reconciliation_act_count',
    )

    legal_address = fields.Char(string='Юридический адрес')
    bank_details = fields.Text(string='Банковские реквизиты')

    is_housing_org = fields.Boolean(string='Жилищная организация', default=False)
    object_address = fields.Char(string='Адрес обслуживаемого объекта')

    sbis_guid = fields.Char(string='GUID в СБИС', copy=False)
    sbis_inn = fields.Char(string='ИНН для СБИС')

    def _compute_service_act_count(self):
        for partner in self:
            partner.service_act_count = self.env['jkh.service.act'].search_count([
                ('partner_id', '=', partner.id)
            ])

    def _compute_reconciliation_act_count(self):
        for partner in self:
            partner.reconciliation_act_count = self.env['jkh.reconciliation.act'].search_count([
                ('partner_id', '=', partner.id)
            ])

    def action_view_service_acts(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Акты реализации',
            'res_model': 'jkh.service.act',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
        }

    def action_view_reconciliation_acts(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Акты сверки',
            'res_model': 'jkh.reconciliation.act',
            'view_mode': 'tree,form',
            'domain': [('partner_id', '=', self.id)],
        }
