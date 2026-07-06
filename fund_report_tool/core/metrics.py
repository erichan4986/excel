from core.database import get_session, ProjectFinancial, FundFinancial, ProjectMetric, FundMetric


# Template-mode aliases for metrics lookup
_METRIC_ALIASES = {
    "总资产": ["资产总计"],
    "营业成本": ["减：营业成本"],
    "营业收入": ["一、营业收入"],
    "收入": ["营业收入", "一、营业收入"],
}


def get_item_value(session, model, period, item_name, project_name=None):
    names = [item_name]
    if item_name in _METRIC_ALIASES:
        names.extend(_METRIC_ALIASES[item_name])
    results = []
    for name in names:
        query = session.query(model).filter_by(period=period, item_name=name)
        if project_name and hasattr(model, 'project_name'):
            query = query.filter_by(project_name=project_name)
        results.extend(query.all())
    if not results:
        return None
    non_zero = [r for r in results if r.value is not None and r.value != 0]
    if non_zero:
        return max(non_zero, key=lambda r: abs(r.value)).value
    return results[0].value


def calc_debt_ratio(project_name, period, db_path=None):
    session = get_session(db_path=db_path)
    try:
        total_assets = get_item_value(session, ProjectFinancial, period, "总资产", project_name)
        total_liabilities = get_item_value(session, ProjectFinancial, period, "负债合计", project_name)
        if total_assets is not None and total_liabilities is not None and total_assets != 0:
            ratio = total_liabilities / total_assets
            _save_project_metric(session, project_name, period, "资产负债率", ratio)
            return ratio
        return None
    finally:
        session.close()


def calc_inventory_days(project_name, period, db_path=None):
    session = get_session(db_path=db_path)
    try:
        inventory = get_item_value(session, ProjectFinancial, period, "存货", project_name)
        cost = get_item_value(session, ProjectFinancial, period, "营业成本", project_name)
        if inventory is not None and cost is not None and cost != 0:
            days = (inventory / cost) * 90
            _save_project_metric(session, project_name, period, "存货周转天数", days)
            return days
        return None
    finally:
        session.close()


def calc_receivable_days(project_name, period, db_path=None):
    session = get_session(db_path=db_path)
    try:
        receivable = get_item_value(session, ProjectFinancial, period, "应收账款", project_name)
        revenue = get_item_value(session, ProjectFinancial, period, "营业收入", project_name)
        if receivable is not None and revenue is not None and revenue != 0:
            days = (receivable / revenue) * 90
            _save_project_metric(session, project_name, period, "应收周转天数", days)
            return days
        return None
    finally:
        session.close()


def calc_dpi(period, db_path=None):
    session = get_session(db_path=db_path)
    try:
        distributed = get_item_value(session, FundFinancial, period, "累计已分配")
        paid_in = get_item_value(session, FundFinancial, period, "实缴资本")
        if distributed is not None and paid_in is not None and paid_in != 0:
            dpi = distributed / paid_in
            _save_fund_metric(session, period, "DPI", dpi)
            return dpi
        return None
    finally:
        session.close()


def calc_tvpi(period, db_path=None):
    session = get_session(db_path=db_path)
    try:
        distributed = get_item_value(session, FundFinancial, period, "累计已分配")
        unrealized = get_item_value(session, FundFinancial, period, "未实现价值")
        paid_in = get_item_value(session, FundFinancial, period, "实缴资本")
        if paid_in is not None and paid_in != 0:
            tvpi = ((distributed or 0) + (unrealized or 0)) / paid_in
            _save_fund_metric(session, period, "TVPI", tvpi)
            return tvpi
        return None
    finally:
        session.close()


def _save_project_metric(session, project_name, period, metric_name, metric_value):
    session.query(ProjectMetric).filter_by(
        project_name=project_name, period=period, metric_name=metric_name
    ).delete()
    session.add(ProjectMetric(
        project_name=project_name, period=period,
        metric_name=metric_name, metric_value=metric_value
    ))
    session.commit()


def _save_fund_metric(session, period, metric_name, metric_value):
    session.query(FundMetric).filter_by(
        period=period, metric_name=metric_name
    ).delete()
    session.add(FundMetric(
        period=period, metric_name=metric_name, metric_value=metric_value
    ))
    session.commit()
