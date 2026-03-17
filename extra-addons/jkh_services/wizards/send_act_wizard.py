from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class SendActWizard(models.TransientModel):
    """Визард отправки актов по email"""
    _name = 'jkh.send.act.wizard'
    _description = 'Отправка акта по email'

    act_type = fields.Selection([
        ('service', 'Акт реализации услуг'),
        ('reconciliation', 'Акт сверки'),
    ], string='Тип документа', required=True, default='service')

    act_ids = fields.Many2many(
        'jkh.service.act',
        string='Акты реализации',
    )
    reconciliation_ids = fields.Many2many(
        'jkh.reconciliation.act',
        string='Акты сверки',
    )

    email_from = fields.Char(
        string='От кого',
        default=lambda self: self.env.user.email or '',
    )
    subject = fields.Char(
        string='Тема письма',
        default='Акт выполненных работ',
    )
    body = fields.Html(
        string='Текст письма',
        default='''<p>Уважаемый партнёр,</p>
<p>Направляем Вам акт выполненных работ.</p>
<p>Просим подписать и вернуть один экземпляр.</p>
<br/>
<p>С уважением,<br/>%(company)s</p>''',
    )
    attach_pdf = fields.Boolean(string='Прикрепить PDF', default=True)

    partner_ids = fields.Many2many(
        'res.partner',
        string='Получатели',
        compute='_compute_partners',
        store=True,
        readonly=False,
    )

    @api.depends('act_ids', 'reconciliation_ids', 'act_type')
    def _compute_partners(self):
        for wizard in self:
            partners = self.env['res.partner']
            if wizard.act_type == 'service':
                partners = wizard.act_ids.mapped('partner_id')
            else:
                partners = wizard.reconciliation_ids.mapped('partner_id')
            wizard.partner_ids = partners.filtered(lambda p: p.email)

    @api.onchange('act_type')
    def _onchange_act_type(self):
        if self.act_type == 'service':
            self.subject = 'Акт выполненных работ'
        else:
            self.subject = 'Акт сверки взаиморасчётов'

    def action_send(self):
        """Отправить акты по email"""
        self.ensure_one()

        if not self.partner_ids:
            raise UserError(_('Не найдены получатели с email адресами.'))

        mail_template = None

        if self.act_type == 'service':
            records = self.act_ids
            report_ref = 'jkh_services.action_report_service_act'
        else:
            records = self.reconciliation_ids
            report_ref = 'jkh_services.action_report_reconciliation_act'

        sent_count = 0
        for record in records:
            partner = record.partner_id
            if not partner.email:
                _logger.warning('Контрагент %s не имеет email', partner.name)
                continue

            attachments = []
            if self.attach_pdf:
                try:
                    pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                        report_ref, [record.id]
                    )
                    attachments.append((
                        f'Акт_{record.name}.pdf',
                        pdf_content,
                    ))
                except Exception as e:
                    _logger.error('Ошибка генерации PDF для %s: %s', record.name, e)

            mail_values = {
                'email_from': self.email_from,
                'email_to': partner.email,
                'subject': f'{self.subject} № {record.name} от {record.date}',
                'body_html': self.body % {'company': self.env.company.name},
                'attachment_ids': [],
            }

            mail = self.env['mail.mail'].create(mail_values)

            for fname, fcontent in attachments:
                attachment = self.env['ir.attachment'].create({
                    'name': fname,
                    'datas': fcontent,
                    'res_model': 'mail.mail',
                    'res_id': mail.id,
                    'mimetype': 'application/pdf',
                })
                mail.attachment_ids = [(4, attachment.id)]

            mail.send()
            record.email_sent = True
            if self.act_type == 'service' and record.state == 'confirmed':
                record.state = 'sent'
            sent_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Отправка завершена'),
                'message': _('Успешно отправлено %d акт(ов).') % sent_count,
                'type': 'success',
                'sticky': False,
            },
        }
