{
    'name': 'ЖКХ: Реализация услуг и учёт',
    'version': '17.0.1.0.0',
    'summary': 'Управление реализацией услуг ЖКХ, акты сверок, выписки банка, экспорт в СБИС',
    'description': """
        Модуль для управления услугами ЖКХ:
        - Документы реализации услуг (аналог 1С)
        - Акты выполненных работ с отправкой на email
        - Акты сверок с клиентами
        - Импорт банковских выписок (1С формат)
        - Экспорт документов в XML формат СБИС
        - Справочники контрагентов, объектов, услуг
    """,
    'author': 'JKH Services',
    'category': 'Accounting/JKH',
    'depends': [
        'base',
        'account',
        'mail',
        'product',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/service_types_data.xml',
        'views/service_act_views.xml',
        'views/reconciliation_act_views.xml',
        'views/bank_statement_views.xml',
        'views/menu_views.xml',
        'reports/service_act_report.xml',
        'reports/reconciliation_act_report.xml',
        'wizards/send_act_wizard_views.xml',
        'wizards/sbis_export_wizard_views.xml',
        'wizards/bank_import_wizard_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
