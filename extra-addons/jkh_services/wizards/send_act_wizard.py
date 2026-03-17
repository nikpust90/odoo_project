from odoo import models, fields, api, _
from odoo.exceptions import UserError
import base64
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header

_logger = logging.getLogger(__name__)


class SendActWizard(models.TransientModel):
    """Визард отправки актов/счетов по email — прямой SMTP без очереди"""
    _name = 'jkh.send.act.wizard'
    _description = 'Отправка документа по email'

    act_type = fields.Selection([
        ('service', 'Акт реализации услуг'),
        ('reconciliation', 'Акт сверки'),
        ('invoice', 'Счёт на оплату'),
    ], string='Тип документа', required=True, default='service')

    act_ids = fields.Many2many('jkh.service.act', string='Акты реализации')
    reconciliation_ids = fields.Many2many('jkh.reconciliation.act', string='Акты сверки')
    invoice_ids = fields.Many2many('jkh.invoice', string='Счета на оплату')

    email_from = fields.Char(
        string='От кого',
        default=lambda self: self._default_email_from(),
    )
    subject = fields.Char(string='Тема письма', default='Документы от компании')
    body = fields.Html(string='Текст письма')
    attach_pdf = fields.Boolean(string='Прикрепить PDF', default=True)

    partner_ids = fields.Many2many(
        'res.partner',
        string='Получатели',
        compute='_compute_partners',
        store=True,
        readonly=False,
    )

    smtp_server = fields.Char(string='SMTP сервер', compute='_compute_smtp', store=False)
    smtp_ok = fields.Boolean(string='SMTP настроен', compute='_compute_smtp', store=False)

    def _default_email_from(self):
        # Берём из настроек исходящей почты Odoo
        mail_server = self.env['ir.mail_server'].search([], order='sequence asc', limit=1)
        if mail_server:
            return mail_server.smtp_user or ''
        return self.env.user.email or ''

    @api.depends()
    def _compute_smtp(self):
        mail_server = self.env['ir.mail_server'].search([], order='sequence asc', limit=1)
        for rec in self:
            if mail_server:
                rec.smtp_server = mail_server.smtp_host
                rec.smtp_ok = True
            else:
                rec.smtp_server = False
                rec.smtp_ok = False

    @api.depends('act_ids', 'reconciliation_ids', 'invoice_ids', 'act_type')
    def _compute_partners(self):
        for wizard in self:
            partners = self.env['res.partner']
            if wizard.act_type == 'service':
                partners = wizard.act_ids.mapped('partner_id')
            elif wizard.act_type == 'reconciliation':
                partners = wizard.reconciliation_ids.mapped('partner_id')
            elif wizard.act_type == 'invoice':
                partners = wizard.invoice_ids.mapped('partner_id')
            wizard.partner_ids = partners.filtered(lambda p: p.email)

    @api.onchange('act_type')
    def _onchange_act_type(self):
        subjects = {
            'service': 'Акт выполненных работ',
            'reconciliation': 'Акт сверки взаиморасчётов',
            'invoice': 'Счёт на оплату',
        }
        self.subject = subjects.get(self.act_type, 'Документы')
        company = self.env.company.name
        self.body = '''<p>Уважаемый партнёр,</p>
<p>Направляем Вам документ для ознакомления и подписания.</p>
<br/>
<p>С уважением,<br/><strong>%s</strong></p>''' % company

    def _get_smtp_connection(self, mail_server):
        """Открывает SMTP-соединение на основе настроек Odoo"""
        host = mail_server.smtp_host
        port = mail_server.smtp_port
        enc = mail_server.smtp_encryption  # none / starttls / ssl

        if enc == 'ssl':
            context = ssl.create_default_context()
            conn = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        else:
            conn = smtplib.SMTP(host, port, timeout=30)
            if enc == 'starttls':
                conn.ehlo()
                conn.starttls()
                conn.ehlo()

        if mail_server.smtp_user and mail_server.smtp_pass:
            conn.login(mail_server.smtp_user, mail_server.smtp_pass)
        return conn

    def _build_mime_message(self, to_email, to_name, subject, body_html, attachments):
        """Формирует MIME-сообщение"""
        msg = MIMEMultipart('mixed')
        from_name = self.env.company.name
        from_email = self.email_from
        msg['From'] = '%s <%s>' % (str(Header(from_name, 'utf-8')), from_email)
        msg['To'] = '%s <%s>' % (str(Header(to_name, 'utf-8')), to_email)
        msg['Subject'] = str(Header(subject, 'utf-8'))
        msg['Reply-To'] = from_email

        # HTML тело
        html_part = MIMEText(body_html or '', 'html', 'utf-8')
        msg.attach(html_part)

        # Вложения
        for fname, fdata in attachments:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(fdata)
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                'attachment',
                filename=('utf-8', '', fname),
            )
            msg.attach(part)

        return msg

    def action_send(self):
        """Отправить документы по email напрямую через SMTP (без очереди)"""
        self.ensure_one()

        if not self.partner_ids:
            raise UserError(_('Нет получателей с email-адресом. Укажите email в карточке контрагента.'))

        # Получаем настройки исходящей почты
        mail_server = self.env['ir.mail_server'].search([], order='sequence asc', limit=1)
        if not mail_server:
            raise UserError(_(
                'Исходящий почтовый сервер не настроен.\n'
                'Перейдите: Настройки → Технические → Исходящая почта → Создать'
            ))
        if not self.email_from:
            raise UserError(_('Укажите email отправителя.'))

        # Определяем записи и шаблон отчёта
        if self.act_type == 'service':
            records = self.act_ids
            report_ref = 'jkh_services.action_report_service_act'
        elif self.act_type == 'reconciliation':
            records = self.reconciliation_ids
            report_ref = 'jkh_services.action_report_reconciliation_act'
        else:
            records = self.invoice_ids
            report_ref = 'jkh_services.action_report_invoice'

        if not records:
            raise UserError(_('Не выбраны документы для отправки.'))

        errors = []
        sent_count = 0

        try:
            smtp_conn = self._get_smtp_connection(mail_server)
        except Exception as e:
            raise UserError(_(
                'Не удалось подключиться к SMTP-серверу %s:%s\nОшибка: %s\n\n'
                'Проверьте настройки: Настройки → Технические → Исходящая почта'
            ) % (mail_server.smtp_host, mail_server.smtp_port, str(e)))

        try:
            for record in records:
                partner = record.partner_id
                if not partner.email:
                    errors.append('Нет email у контрагента: %s' % partner.name)
                    continue

                # Формируем PDF-вложение
                attachments = []
                if self.attach_pdf:
                    try:
                        pdf_bytes, _ctype = self.env['ir.actions.report']._render_qweb_pdf(
                            report_ref, [record.id]
                        )
                        fname = '%s_%s.pdf' % (
                            self.act_type == 'service' and 'Акт' or
                            self.act_type == 'reconciliation' and 'АктСверки' or 'Счёт',
                            record.name.replace('/', '_')
                        )
                        attachments.append((fname, pdf_bytes))
                    except Exception as e:
                        _logger.error('PDF generation failed for %s: %s', record.name, e)
                        errors.append('Ошибка PDF для %s: %s' % (record.name, str(e)))
                        continue

                subject_full = '%s № %s от %s' % (self.subject, record.name, record.date)
                body_html = (self.body or '') + '''
                    <br/><hr style="border:none;border-top:1px solid #eee;"/>
                    <p style="font-size:11px;color:#999;">
                        Это письмо сформировано автоматически. Пожалуйста, не отвечайте на него.
                    </p>'''

                msg = self._build_mime_message(
                    partner.email, partner.name,
                    subject_full, body_html, attachments
                )

                try:
                    smtp_conn.sendmail(self.email_from, [partner.email], msg.as_bytes())
                    record.email_sent = True
                    if self.act_type == 'service' and record.state == 'confirmed':
                        record.state = 'sent'
                    sent_count += 1
                    _logger.info('Email sent to %s for %s', partner.email, record.name)
                except Exception as e:
                    errors.append('Ошибка отправки на %s: %s' % (partner.email, str(e)))
                    _logger.error('SMTP send error to %s: %s', partner.email, e)
        finally:
            try:
                smtp_conn.quit()
            except Exception:
                pass

        if errors and sent_count == 0:
            raise UserError(_('Отправка не удалась:\n') + '\n'.join(errors))

        msg_parts = [_('Успешно отправлено: %d') % sent_count]
        if errors:
            msg_parts.append(_('Ошибки (%d):\n%s') % (len(errors), '\n'.join(errors)))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Отправка завершена'),
                'message': '\n'.join(msg_parts),
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
            },
        }
