from odoo import models, fields, api, _
from odoo.exceptions import UserError
from collections import defaultdict
import logging

_logger = logging.getLogger(__name__)


class ProfitReportWizard(models.TransientModel):
    """
    Отчёт по выручке и прибыли на основе актов реализации.
    Группировка: по месяцам, по контрагентам, по видам услуг.
    """
    _name = 'jkh.profit.report.wizard'
    _description = 'Отчёт по прибыли (выручка)'

    date_from = fields.Date(
        string='С',
        required=True,
        default=lambda self: fields.Date.today().replace(day=1, month=1),
    )
    date_to = fields.Date(
        string='По',
        required=True,
        default=fields.Date.context_today,
    )
    group_by = fields.Selection([
        ('month', 'По месяцам'),
        ('partner', 'По контрагентам'),
        ('service', 'По видам услуг'),
    ], string='Группировать по', default='month', required=True)

    partner_ids = fields.Many2many(
        'res.partner',
        string='Контрагенты',
        help='Оставьте пустым для всех',
    )
    state_filter = fields.Selection([
        ('all', 'Все (кроме черновиков и отменённых)'),
        ('confirmed', 'Только подтверждённые'),
        ('done', 'Только закрытые'),
    ], string='Статус актов', default='all')

    company_id = fields.Many2one(
        'res.company',
        string='Организация',
        default=lambda self: self.env.company,
    )

    # Результаты (вычисляются при генерации)
    result_line_ids = fields.One2many(
        'jkh.profit.report.line',
        'wizard_id',
        string='Строки отчёта',
        readonly=True,
    )
    total_revenue = fields.Monetary(
        string='Итого выручка (без НДС)',
        readonly=True,
        currency_field='currency_id',
    )
    total_tax = fields.Monetary(
        string='Итого НДС',
        readonly=True,
        currency_field='currency_id',
    )
    total_with_tax = fields.Monetary(
        string='Итого с НДС',
        readonly=True,
        currency_field='currency_id',
    )
    acts_count = fields.Integer(string='Количество актов', readonly=True)
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    report_generated = fields.Boolean(default=False)

    def _get_acts_domain(self):
        domain = [
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('company_id', '=', self.company_id.id),
        ]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.state_filter == 'confirmed':
            domain.append(('state', '=', 'confirmed'))
        elif self.state_filter == 'done':
            domain.append(('state', '=', 'done'))
        else:
            domain.append(('state', 'in', ['confirmed', 'sent', 'done']))
        return domain

    def action_generate(self):
        """Сформировать отчёт"""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_('Дата начала не может быть позже даты окончания.'))

        acts = self.env['jkh.service.act'].search(
            self._get_acts_domain(), order='date asc'
        )

        if not acts:
            raise UserError(_(
                'За период с %s по %s не найдено ни одного акта реализации.'
            ) % (self.date_from, self.date_to))

        # Очищаем старые строки
        self.result_line_ids.unlink()

        groups = defaultdict(lambda: {'revenue': 0.0, 'tax': 0.0, 'total': 0.0, 'count': 0})

        for act in acts:
            if self.group_by == 'month':
                key = act.date.strftime('%Y-%m')
                label = act.date.strftime('%B %Y').capitalize()
            elif self.group_by == 'partner':
                key = act.partner_id.id
                label = act.partner_id.name
            else:
                key = 'all'
                label = 'Все услуги'

            groups[key]['label'] = label
            groups[key]['revenue'] += act.amount_untaxed
            groups[key]['tax'] += act.amount_tax
            groups[key]['total'] += act.amount_total
            groups[key]['count'] += 1

            # Для группировки по услугам разбиваем по строкам
            if self.group_by == 'service':
                groups_svc = defaultdict(lambda: {'revenue': 0.0, 'tax': 0.0, 'total': 0.0, 'count': 0})
                for line in act.line_ids:
                    svc_key = line.product_id.id if line.product_id else 'other'
                    svc_label = line.product_id.name if line.product_id else 'Прочие услуги'
                    groups_svc[svc_key]['label'] = svc_label
                    groups_svc[svc_key]['revenue'] += line.price_subtotal
                    groups_svc[svc_key]['tax'] += line.price_tax
                    groups_svc[svc_key]['total'] += line.price_total
                    groups_svc[svc_key]['count'] += 1
                groups = groups_svc
                break

        lines_data = []
        for key, data in groups.items():
            lines_data.append({
                'wizard_id': self.id,
                'group_label': data.get('label', str(key)),
                'revenue': data['revenue'],
                'tax': data['tax'],
                'total_with_tax': data['total'],
                'acts_count': data['count'],
            })

        self.env['jkh.profit.report.line'].create(lines_data)
        self.write({
            'total_revenue': sum(acts.mapped('amount_untaxed')),
            'total_tax': sum(acts.mapped('amount_tax')),
            'total_with_tax': sum(acts.mapped('amount_total')),
            'acts_count': len(acts),
            'report_generated': True,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_print_pdf(self):
        """Печать отчёта в PDF"""
        self.ensure_one()
        if not self.report_generated:
            self.action_generate()
        return self.env.ref('jkh_services.action_report_profit').report_action(self)


class ProfitReportLine(models.TransientModel):
    """Строки отчёта по прибыли"""
    _name = 'jkh.profit.report.line'
    _description = 'Строка отчёта по прибыли'
    _order = 'group_label'

    wizard_id = fields.Many2one('jkh.profit.report.wizard', ondelete='cascade')
    group_label = fields.Char(string='Группа')
    revenue = fields.Monetary(string='Выручка без НДС', currency_field='currency_id')
    tax = fields.Monetary(string='НДС', currency_field='currency_id')
    total_with_tax = fields.Monetary(string='Итого с НДС', currency_field='currency_id')
    acts_count = fields.Integer(string='Актов')
    currency_id = fields.Many2one(related='wizard_id.currency_id')

    @property
    def revenue_share(self):
        total = self.wizard_id.total_revenue
        return (self.revenue / total * 100) if total else 0.0
