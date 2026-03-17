{
    'name': 'ЖКХ: Реализация услуг и учёт',
    'version': '17.0.1.1.0',
    'summary': 'Управление реализацией услуг ЖКХ, акты сверок, счета, выписки банка, экспорт в СБИС',
    'author': 'JKH Services',
    'category': 'Accounting/JKH',
    'depends': ['base', 'account', 'mail', 'product', 'contacts'],
    'data': [
        'security/ir.model.access.csv',
        'data/service_types_data.xml',
        # Views (без меню — меню в самом конце, после всех actions)
        'views/service_act_views.xml',
        'views/reconciliation_act_views.xml',
        'views/bank_statement_views.xml',
        'views/invoice_views.xml',
        # Reports
        'reports/service_act_report.xml',
        'reports/reconciliation_act_report.xml',
        'reports/invoice_report.xml',
        'reports/profit_report.xml',
        # Wizards (views + actions)
        'wizards/send_act_wizard_views.xml',
        'wizards/sbis_export_wizard_views.xml',
        'wizards/bank_import_wizard_views.xml',
        'wizards/profit_report_wizard_views.xml',
        # Меню — строго последним, когда все actions уже определены
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
