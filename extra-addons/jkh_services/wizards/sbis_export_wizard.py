from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime
import base64
import xml.etree.ElementTree as ET
import xml.dom.minidom
import logging

_logger = logging.getLogger(__name__)


class SbisExportWizard(models.TransientModel):
    """
    Визард экспорта документов реализации в XML формат для СБИС.
    Формирует УПД (Универсальный передаточный документ) в формате ФНС.
    """
    _name = 'jkh.sbis.export.wizard'
    _description = 'Экспорт в СБИС XML (УПД)'

    act_ids = fields.Many2many(
        'jkh.service.act',
        string='Документы для экспорта',
    )
    export_format = fields.Selection([
        ('upd', 'УПД (Универсальный передаточный документ)'),
        ('act', 'Акт выполненных работ'),
    ], string='Формат документа', default='upd', required=True)

    function_type = fields.Selection([
        ('schf', 'СЧФ — только счёт-фактура'),
        ('dop', 'ДОП — только передаточный документ'),
        ('schfdop', 'СЧФДОП — счёт-фактура и передаточный документ'),
    ], string='Функция документа', default='schfdop', required=True)

    xml_file = fields.Binary(string='XML файл', readonly=True, attachment=True)
    xml_filename = fields.Char(string='Имя файла', readonly=True)
    state = fields.Selection([
        ('draft', 'Готов к экспорту'),
        ('done', 'Экспорт выполнен'),
    ], default='draft')

    def _get_tax_code(self, tax_amount_percent):
        """Код ставки НДС по формату ФНС"""
        mapping = {
            0: '5',    # 0%
            10: '2',   # 10%
            20: '3',   # 20%
            -1: '6',   # Без НДС
        }
        return mapping.get(int(tax_amount_percent), '6')

    def _format_date(self, d):
        if not d:
            return ''
        if isinstance(d, str):
            return datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m.%Y')
        return d.strftime('%d.%m.%Y')

    def _build_upd_xml(self, act):
        """
        Строит XML документа УПД в формате ФНС России.
        Структура соответствует приказу ФНС ММВ-7-15/820@ (версия 5.01).
        """
        company = act.company_id or self.env.company
        partner = act.partner_id

        root = ET.Element('Файл')
        root.set('ИдФайл', f'ON_NSCHFDOPPR__{company.vat or "0000000000"}_{partner.vat or "0000000000"}_{act.date.strftime("%Y%m%d")}_{act.name.replace("/", "_")}')
        root.set('ВерсПрог', 'Odoo JKH 17.0')
        root.set('ВерсФорм', '5.01')

        doc = ET.SubElement(root, 'Документ')
        doc.set('КНД', '1115131')
        doc.set('Функция', self.function_type.upper())
        doc.set('ПоФункции', 'ОКД')
        doc.set('ДатаИнфПр', datetime.now().strftime('%d.%m.%Y'))
        doc.set('ВремИнфПр', datetime.now().strftime('%H.%M.%S'))
        doc.set('НаимЭконСубПрод', company.name or '')
        doc.set('ИННЮЛ', company.vat or '')
        doc.set('КПП', company.partner_id.kpp if hasattr(company.partner_id, 'kpp') else '')

        # Сведения о счёт-фактуре
        sv_schf = ET.SubElement(doc, 'СвСчФакт')
        sv_schf.set('НомерСчФ', act.name)
        sv_schf.set('ДатаСчФ', self._format_date(act.date))
        sv_schf.set('КодОКВ', '643')

        # Продавец
        sv_prod = ET.SubElement(sv_schf, 'СвПрод')
        id_sp = ET.SubElement(sv_prod, 'ИдСв')
        sv_yul_prod = ET.SubElement(id_sp, 'СвЮЛ')
        sv_yul_prod.set('НаимОрг', company.name or '')
        sv_yul_prod.set('ИННЮЛ', company.vat or '')
        sv_yul_prod.set('КПП', getattr(company.partner_id, 'kpp', '') or '')
        addr_prod = ET.SubElement(sv_prod, 'Адрес')
        addr_prod_text = ET.SubElement(addr_prod, 'АдрИнф')
        addr_prod_text.set('КодСтр', '643')
        addr_prod_text.set('АдрТекст', company.partner_id.street or '')

        # Покупатель
        sv_pok = ET.SubElement(sv_schf, 'СвПокуп')
        id_pok = ET.SubElement(sv_pok, 'ИдСв')
        sv_yul_pok = ET.SubElement(id_pok, 'СвЮЛ')
        sv_yul_pok.set('НаимОрг', partner.name or '')
        sv_yul_pok.set('ИННЮЛ', partner.vat or '')
        sv_yul_pok.set('КПП', getattr(partner, 'kpp', '') or '')
        addr_pok = ET.SubElement(sv_pok, 'Адрес')
        addr_pok_text = ET.SubElement(addr_pok, 'АдрИнф')
        addr_pok_text.set('КодСтр', '643')
        addr_pok_text.set('АдрТекст', partner.street or '')

        # Таблица товаров/услуг
        table = ET.SubElement(doc, 'ТаблСчФакт')
        for idx, line in enumerate(act.line_ids, 1):
            item = ET.SubElement(table, 'СведТов')
            item.set('НомСтр', str(idx))
            item.set('НаимТов', line.name or '')
            item.set('ОКЕИ_Тов', '796')
            item.set('КолТов', str(line.quantity))
            item.set('ЦенаТов', f'{line.price_unit:.2f}')
            item.set('СтТовБезНДС', f'{line.price_subtotal:.2f}')

            # НДС
            nalog = ET.SubElement(item, 'СумНал')
            tax_rate = 0
            if line.tax_ids:
                tax_rate = line.tax_ids[0].amount
            if tax_rate > 0:
                nds = ET.SubElement(nalog, 'СумНал')
                nds.set('НалСт', f'{int(tax_rate)}%')
                nds.set('СумНал', f'{line.price_tax:.2f}')
            else:
                bez_nds = ET.SubElement(nalog, 'БезНДС')

            item.set('СтТовУчНал', f'{line.price_total:.2f}')

        # Итого
        vsego = ET.SubElement(table, 'ВсегоОпл')
        vsego.set('СтТовБезНДСВсего', f'{act.amount_untaxed:.2f}')
        vsego.set('СтТовУчНалВсего', f'{act.amount_total:.2f}')

        # Сведения о передаче (для функций ДОП и СЧФДОП)
        if self.function_type in ('dop', 'schfdop'):
            sv_per = ET.SubElement(doc, 'СвПродПер')
            sv_per_doc = ET.SubElement(sv_per, 'СвПер')
            sv_per_doc.set('СодОпер', f'Реализация услуг по договору № {act.contract_number or "б/н"} от {self._format_date(act.contract_date)}')
            sv_per_doc.set('ДатаПер', self._format_date(act.date))
            if act.period_from and act.period_to:
                sv_per_doc.set('ОснПер', f'Договор за период с {self._format_date(act.period_from)} по {self._format_date(act.period_to)}')

        # Подписант
        podpisant = ET.SubElement(doc, 'Подписант')
        podpisant.set('ОблПолн', '1')
        podpisant.set('Статус', '1')
        podpisant.set('ОснПолн', 'Устав')
        fio = ET.SubElement(podpisant, 'ФИО')
        fio.set('Фамилия', '')
        fio.set('Имя', '')
        fio.set('Отчество', '')

        return root

    def action_export(self):
        """Генерировать XML файлы для всех выбранных актов"""
        self.ensure_one()

        if not self.act_ids:
            raise UserError(_('Не выбраны документы для экспорта.'))

        for act in self.act_ids:
            if act.state == 'draft':
                raise UserError(
                    _('Документ "%s" в статусе черновика. Подтвердите перед экспортом.') % act.name
                )

        if len(self.act_ids) == 1:
            act = self.act_ids[0]
            xml_root = self._build_upd_xml(act)
            xml_string = xml.dom.minidom.parseString(
                ET.tostring(xml_root, encoding='utf-8', xml_declaration=True)
            ).toprettyxml(indent='  ', encoding='utf-8')

            filename = f'UPD_{act.name.replace("/", "_")}_{act.date.strftime("%Y%m%d")}.xml'
            self.xml_file = base64.b64encode(xml_string)
            self.xml_filename = filename

            now = datetime.now()
            act.sbis_exported = True
            act.sbis_export_date = now
            if act.state == 'confirmed':
                act.state = 'sent'

            self.state = 'done'
            return {
                'type': 'ir.actions.act_window',
                'res_model': self._name,
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'new',
            }
        else:
            # Несколько документов — создаём ZIP архив
            import zipfile
            import io

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for act in self.act_ids:
                    xml_root = self._build_upd_xml(act)
                    xml_string = xml.dom.minidom.parseString(
                        ET.tostring(xml_root, encoding='utf-8', xml_declaration=True)
                    ).toprettyxml(indent='  ', encoding='utf-8')
                    fname = f'UPD_{act.name.replace("/", "_")}_{act.date.strftime("%Y%m%d")}.xml'
                    zf.writestr(fname, xml_string)
                    act.sbis_exported = True
                    act.sbis_export_date = datetime.now()

            zip_buffer.seek(0)
            self.xml_file = base64.b64encode(zip_buffer.read())
            self.xml_filename = f'SBIS_Export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
            self.state = 'done'

            return {
                'type': 'ir.actions.act_window',
                'res_model': self._name,
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'new',
            }
