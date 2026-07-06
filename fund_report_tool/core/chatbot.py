import json
import os
import re
from core import llm_helper
from core.database import get_session, ProjectFinancial, FundFinancial, ProjectMetric, FundFairValue
from core.metrics import _METRIC_ALIASES


def process_message(user_message, context=None):
    system_prompt = """你是基金投后数据助理。请解析用户消息，以JSON格式返回：
{
  "intent": "fill_template" | "query_data" | "draw_chart" | "calculate_metric" | "show_fund_data" | "clarify_intent" | "general_chat",
  "params": {},
  "response": "给用户的自然语言回复"
}

对于 fill_template 意图，params 必须包含：
- template_path: 模板文件路径（如用户提供则提取，否则为null）
- layout: {"company_axis": "row"|"col", "metric_axis": "row"|"col", "company_header_row": 数字或null, "company_header_col": 数字或null, "metric_header_row": 数字或null, "metric_header_col": 数字或null}
- period: 目标期间，如 "2024-12-31"
- mapping_overrides: 用户指定的映射修正，如{"资产总计": "总资产"}

layout 的 axis 语义非常关键（务必正确理解）：
- company_axis="row": 公司名在某一列中纵向排列，每个公司占一行。例如"公司名在C列" → company_axis="row", company_header_col=3
- company_axis="col": 公司名在某一行中横向排列，每个公司占一列。例如"公司名在第2行" → company_axis="col", company_header_row=2
- metric_axis="row": 指标名在某一列中纵向排列，每个指标占一行。例如"指标在A列" → metric_axis="row", metric_header_col=1
- metric_axis="col": 指标名在某一行中横向排列，每个指标占一列。例如"指标在第2行"或"表头在第2行" → metric_axis="col", metric_header_row=2

常见 LP 模板布局示例：
1. "公司名在第一列，指标在第一行" → company_axis="row", metric_axis="col", company_header_col=1, metric_header_row=1
2. "公司名在C列，表头在第二行" → company_axis="row", metric_axis="col", company_header_col=3, metric_header_row=2
3. "公司名在第一行，指标在A列" → company_axis="col", metric_axis="row", company_header_row=1, metric_header_col=1

对于 query_data 意图，params 必须包含：
- project_name: 项目名
- metric_name: 指标名（标准化名称）
- period_range: 期间范围描述，如 "近三年"

对于 draw_chart 意图，params 必须包含：
- project_name: 项目名
- metric_name: 指标名（标准化名称）
- period_range: 期间范围描述，如 "近三年"
- chart_type: 图表类型，可选 "line" | "bar" | "pie"，用户未指定时默认为 "line"
当用户明确要求画图时使用此意图，如"画个折线图""看一下趋势""做个对比图"。

对于 confirm_mappings 意图，params 为空，表示用户确认之前大模型给出的模糊映射并保存到配置。
对于 reject_mappings 意图，params 为空，表示用户拒绝之前大模型给出的模糊映射，不保存且不填充对应单元格。
对于 list_projects 意图，params 为空，返回所有项目名列表。
对于 list_periods 意图，params 包含 project_name，返回该项目所有期间。
对于 show_data 意图，params 包含 project_name 和 period，返回该项目期间的完整财务数据和衍生指标。
对于 show_fund_data 意图，params 包含 fund_name 和 period，返回该基金期间的投资项目明细和汇总统计（如总成本、总公允价值）。
对于 clarify_intent 意图，params 必须包含：
- options: [{"label": "选项描述", "intent": "对应意图", "params": {}}]
当用户消息模糊、信息不完整、可能对应多种操作时，主动使用此意图列出 2-4 个最可能的选项让用户选择。不要猜测，直接列出选项。

对于 show_fund_data 意图，params 必须包含：
- fund_name: 基金名（如"并购一期"）
- period: 期间（如"2026-03-31"）
当用户询问基金信息时使用，例如"并购一期有几个项目""浦江一期的公允价值是多少""基金数据"

对于 calculate_metric 意图，params 必须包含：
- project_name: 项目名
- metric_name: 基础指标名（如"营业收入"）
- period: 目标期间，如 "2025-12-31"
- calculation_type: "yoy_growth" | "inventory_days" | "receivable_days"
当用户询问需要跨期间计算的指标时使用：
- "同比增长" / "同比变化" → calculation_type="yoy_growth"
- "存货周转天数" → calculation_type="inventory_days"
- "应收账款周转天数" / "应收周转天数" → calculation_type="receivable_days"

如果信息不完整，intent 仍填对应值，但 response 中友好地询问缺失信息。"""

    context_str = json.dumps(context, ensure_ascii=False) if context else "无上下文"
    prompt = f"用户消息：{user_message}\n当前上下文：{context_str}\n请解析并返回JSON。"

    raw = llm_helper.generate(prompt, system_prompt)

    # Fallback: keyword-based intent detection when LLM is unavailable
    if not raw:
        result = _fallback_intent(user_message, context)
        if result:
            return result
        return {"intent": "general_chat", "params": {}, "response": "抱歉，AI服务暂时不可用，请稍后重试。"}

    try:
        result = json.loads(raw)
        if "intent" not in result:
            result["intent"] = "general_chat"
        if "params" not in result:
            result["params"] = {}
        if "response" not in result:
            result["response"] = ""

        # Fallback validation: when LLM layout conflicts with rule-based parsing,
        # use rule-based result for axis/position because it's deterministic.
        if result.get("intent") == "fill_template":
            fb = _fallback_intent(user_message, context)
            if fb and fb.get("intent") == "fill_template":
                ll_layout = result["params"].setdefault("layout", {})
                fb_layout = fb["params"].get("layout", {})
                # For positions explicitly specified by user in natural language,
                # rule-based parser is more reliable than LLM.
                for key in ["company_axis", "metric_axis",
                            "company_header_row", "company_header_col",
                            "metric_header_row", "metric_header_col"]:
                    fb_val = fb_layout.get(key)
                    ll_val = ll_layout.get(key)
                    # If LLM returns None but rule parser has a value, use rule parser
                    if fb_val is not None and ll_val is None:
                        ll_layout[key] = fb_val
                    # If both have values but axis disagrees, trust rule parser for axis
                    # (axis is the most commonly misinterpreted field)
                    if key in ("company_axis", "metric_axis") and fb_val is not None and ll_val is not None and fb_val != ll_val:
                        ll_layout[key] = fb_val

        # Guard against LLM over-inference based on context:
        # Only downgrade fill_template (file-writing action). Read-only intents
        # (query, chart, list, show) are trusted from LLM to avoid unnecessary
        # clarification loops.
        if result.get("intent") == "fill_template":
            fb = _fallback_intent(user_message, context)
            if fb is None:
                result["intent"] = "clarify_intent"
                result["params"] = {
                    "options": [
                        {"label": "填写LP报告模板", "intent": "fill_template", "params": {}},
                        {"label": "查询项目财务数据", "intent": "query_data", "params": {}},
                        {"label": "浏览已存储的数据", "intent": "show_data", "params": {}},
                        {"label": "列出所有项目", "intent": "list_projects", "params": {}}
                    ]
                }
                result["response"] = "抱歉，我没太理解您的意思。您是想做以下哪件事？"

        # If LLM returns general_chat, try fallback and clarify
        if result.get("intent") == "general_chat":
            fb = _fallback_intent(user_message, context)
            if fb:
                return fb
            operation_keywords = ["填", "查", "看", "数据", "模板", "报告", "公司", "项目", "期间", "年份", "指标", "生成", "下载", "列表"]
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in operation_keywords):
                result["intent"] = "clarify_intent"
                result["params"] = {
                    "options": [
                        {"label": "填写LP报告模板", "intent": "fill_template", "params": {}},
                        {"label": "查询项目财务数据", "intent": "query_data", "params": {}},
                        {"label": "浏览已存储的数据", "intent": "show_data", "params": {}},
                        {"label": "列出所有项目", "intent": "list_projects", "params": {}}
                    ]
                }
                result["response"] = result.get("response") or "抱歉，我没太理解您的具体需求。您是想做以下哪件事？"

        return result
    except json.JSONDecodeError:
        fb = _fallback_intent(user_message, context)
        if fb:
            return fb
        return {"intent": "general_chat", "params": {}, "response": raw or "收到，请详细说明您的需求。"}


_CN_NUMS = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    '两': 2,
}


def _parse_number(text):
    """Parse Arabic or Chinese numerals (1-10) into int."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    # Simple Chinese numeral: single char like '二'
    if text in _CN_NUMS:
        return _CN_NUMS[text]
    # Compound like '十二' (not handled, rare for rows/cols)
    return None


def _col_letter_to_num(letter):
    """Convert Excel column letter (A-Z) or number to 1-based column number."""
    letter = letter.strip().upper()
    if len(letter) == 1 and 'A' <= letter <= 'Z':
        return ord(letter) - ord('A') + 1
    parsed = _parse_number(letter)
    if parsed:
        return parsed
    return None


def _fallback_intent(user_message, context):
    """Rule-based intent fallback when LLM is unavailable."""
    msg = user_message.lower()

    # confirm_mappings / reject_mappings
    if any(k in msg for k in ["确认", "是的", "对的", "没问题", "保存映射", "记下来"]):
        return {"intent": "confirm_mappings", "params": {}, "response": "正在保存映射..."}
    if any(k in msg for k in ["不对", "不要", "跳过", "排除", "取消", "错误"]):
        return {"intent": "reject_mappings", "params": {}, "response": "已取消保存映射，正在重新生成不填这些指标的文件..."}

    # Direct intent name selection (from clarify options)
    if msg in ("fill_template", "query_data", "list_projects", "list_periods", "show_data"):
        intent_map = {
            "fill_template": ("fill_template", "好的，请上传LP模板并描述行列结构，例如\"公司名在第一列，指标在第一行，填2024年数据\"。"),
            "query_data": ("query_data", "请告诉我您想查询哪个项目、哪个指标和哪个期间。"),
            "list_projects": ("list_projects", "正在获取项目列表..."),
            "list_periods": ("list_periods", "请告诉我您想查看哪个项目的期间列表。"),
            "show_data": ("show_data", "请告诉我您想查看哪个项目的哪个期间的数据。")
        }
        intent_name, resp = intent_map[msg]
        return {"intent": intent_name, "params": {}, "response": resp}

    # list_projects
    if any(k in msg for k in ["有哪些公司", "有哪些项目", "库里有什么", "数据库里有什么",
                               "数据库里", "所有公司", "所有项目", "有什么数据", "有哪些数据"]):
        return {"intent": "list_projects", "params": {}, "response": "当前数据库中的项目如下："}

    # list_periods
    m = re.search(r'(.+?)(?:有|的)(?:哪些|什么)(?:期间|年份|数据)', msg)
    if m:
        project = m.group(1).strip()
        # Avoid treating generic words like "database" or pronouns as project names
        if project not in ("数据库", "你", "系统", "我"):
            return {"intent": "list_periods", "params": {"project_name": project}, "response": f"{project} 的期间数据如下："}

    # fill_template — check before show_data because phrases like "填2025数据"
    # contain the word "数据" which show_data would otherwise catch.
    if any(k in msg for k in ["填", "填充", "生成", "填写"]):
        layout = {}
        period = None

        # Detect period (any 20xx year, since we're already in fill_template branch)
        pm = re.search(r'(20\d{2})', msg)
        if pm:
            period = f"{pm.group(1)}-12-31"

        # Detect layout keywords
        # ------------------------------------------------------------------
        # Axis semantics:
        #   company_axis="col"  → company names spread across COLUMNS (one per col)
        #   company_axis="row"  → company names spread across ROWS    (one per row)
        #   metric_axis="col"   → metric names spread across COLUMNS  (one per col)
        #   metric_axis="row"   → metric names spread across ROWS     (one per row)
        # ------------------------------------------------------------------

        company_col_m = re.search(r'公司名在[第]?([a-zA-Z\d一二三四五六七八九十]+)列', user_message)
        company_row_m = re.search(r'公司名在[第]?([\d一二三四五六七八九十]+)行', user_message)
        metric_col_m = re.search(r'指标在[第]?([a-zA-Z\d一二三四五六七八九十]+)列', user_message)
        metric_row_m = re.search(r'指标在[第]?([\d一二三四五六七八九十]+)行', user_message)
        header_row_m = re.search(r'表头在[第]?([\d一二三四五六七八九十]+)行', user_message)

        if "公司名在第一行" in user_message or "公司名在行" in user_message:
            # Company names across columns → company_axis="col", metrics down rows
            layout["company_axis"] = "col"
            layout["metric_axis"] = "row"
        elif company_col_m or "公司名在第一列" in user_message or "公司名在列" in user_message:
            # Company names down a column → company_axis="row", metrics across cols
            layout["company_axis"] = "row"
            layout["metric_axis"] = "col"
        elif "指标在第一行" in user_message or "指标在行" in user_message or header_row_m or "表头在第一行" in user_message:
            # Metric names across columns → metric_axis="col", companies down rows
            layout["company_axis"] = "row"
            layout["metric_axis"] = "col"
        elif metric_col_m or "指标在第一列" in user_message or "指标在列" in user_message:
            # Metric names down a column → metric_axis="row", companies across cols
            layout["company_axis"] = "col"
            layout["metric_axis"] = "row"
        else:
            # Default: companies in columns, metrics in rows (most common LP layout)
            layout["company_axis"] = "col"
            layout["metric_axis"] = "row"

        # Extract specific header positions from user description
        if company_col_m:
            col_num = _col_letter_to_num(company_col_m.group(1))
            if col_num:
                layout["company_header_col"] = col_num
                layout["_user_specified_company_col"] = True
        if company_row_m:
            row_num = _parse_number(company_row_m.group(1))
            if row_num:
                layout["company_header_row"] = row_num
                layout["_user_specified_company_row"] = True
        if metric_col_m:
            col_num = _col_letter_to_num(metric_col_m.group(1))
            if col_num:
                layout["metric_header_col"] = col_num
                layout["_user_specified_metric_col"] = True
        if metric_row_m:
            row_num = _parse_number(metric_row_m.group(1))
            if row_num:
                layout["metric_header_row"] = row_num
                layout["_user_specified_metric_row"] = True
        if header_row_m:
            row_num = _parse_number(header_row_m.group(1))
            if row_num:
                layout["metric_header_row"] = row_num
                layout["_user_specified_metric_row"] = True

        # Set defaults for anything not explicitly provided
        layout.setdefault("company_header_row", 1)
        layout.setdefault("company_header_col", 1)
        layout.setdefault("metric_header_row", 1)
        layout.setdefault("metric_header_col", 1)

        params = {
            "template_path": context.get("template_path") if context else None,
            "layout": layout,
            "mapping_overrides": {}
        }
        if period:
            params["period"] = period

        return {"intent": "fill_template", "params": params, "response": "已理解您的填表需求，正在处理。"}

    # show_fund_data
    fund_keywords = ["并购一期", "浦江一期", "并购三期", "基金"]
    if any(k in msg for k in fund_keywords):
        # Extract fund name
        fund_name = None
        for fk in ["并购一期", "浦江一期", "并购三期"]:
            if fk in msg:
                fund_name = fk
                break
        # Extract period
        pm = re.search(r'(20\d{2})', msg)
        period = f"{pm.group(1)}-12-31" if pm else None
        if fund_name:
            params = {"fund_name": fund_name}
            if period:
                params["period"] = period
            return {"intent": "show_fund_data", "params": params, "response": f"正在查询 {fund_name} 的基金数据..."}

    # show_data
    m = re.search(r'(.+?)(20\d{2}).*?(?:资产负债表|利润表|数据|明细)', msg)
    if m:
        project = m.group(1).strip()
        period = f"{m.group(2)}-12-31"
        return {"intent": "show_data", "params": {"project_name": project, "period": period}, "response": f"{project} {period} 的财务数据如下："}

    # calculate_metric - must check before query_data because "营业收入同比增长" contains "营业收入"
    # yoy_growth
    if "同比" in msg or ("增长" in msg and re.search(r'20\d{2}', msg)):
        m = re.search(r'(.+?)(20\d{2}).*?(营业收入|净利润|总资产|营收|收入|成本|负债|存货|应收账款|营业利润|管理费用|管理成本|管理开支|经营活动现金流量净额|经营现金流|经营性现金流|经营现金流净额|研发费用|研发支出|研究开发费用|毛利率|销售毛利率)', msg)
        if m:
            project = m.group(1).strip()
            period = f"{m.group(2)}-12-31"
            metric = m.group(3).strip()
            metric_map = {
                "营收": "营业收入", "收入": "营业收入", "成本": "营业成本",
                "负债": "负债合计", "净利润": "净利润", "总资产": "总资产",
                "存货": "存货", "营业收入": "营业收入", "应收账款": "应收账款",
                "营业利润": "营业利润",
                "管理费用": "管理费用", "管理成本": "管理费用", "管理开支": "管理费用",
                "经营活动现金流量净额": "经营活动现金流量净额", "经营现金流": "经营活动现金流量净额",
                "经营性现金流": "经营活动现金流量净额", "经营现金流净额": "经营活动现金流量净额",
                "研发费用": "研发费用", "研发支出": "研发费用", "研究开发费用": "研发费用",
                "毛利率": "毛利率", "销售毛利率": "毛利率"
            }
            std_metric = metric_map.get(metric, metric)
            return {
                "intent": "calculate_metric",
                "params": {"project_name": project, "metric_name": std_metric, "period": period, "calculation_type": "yoy_growth"},
                "response": f"正在计算 {project} {period} 的 {std_metric} 同比增长率。"
            }

    # inventory_days
    if "存货周转天数" in msg:
        m = re.search(r'(.+?)(20\d{2})', msg)
        if m:
            project = m.group(1).strip()
            period = f"{m.group(2)}-12-31"
            return {
                "intent": "calculate_metric",
                "params": {"project_name": project, "metric_name": "存货", "period": period, "calculation_type": "inventory_days"},
                "response": f"正在计算 {project} {period} 的存货周转天数。"
            }

    # receivable_days
    if "应收账款周转天数" in msg or "应收周转天数" in msg:
        m = re.search(r'(.+?)(20\d{2})', msg)
        if m:
            project = m.group(1).strip()
            period = f"{m.group(2)}-12-31"
            return {
                "intent": "calculate_metric",
                "params": {"project_name": project, "metric_name": "应收账款", "period": period, "calculation_type": "receivable_days"},
                "response": f"正在计算 {project} {period} 的应收账款周转天数。"
            }

    # draw_chart
    chart_keywords = ["画图", "图表", "折线图", "柱状图", "饼图", "趋势图", "对比图", "可视化"]
    if any(k in msg for k in chart_keywords):
        # Try to extract chart type
        chart_type = "line"
        if "饼图" in msg or "环形图" in msg or "占比" in msg:
            chart_type = "pie"
        elif "柱状图" in msg or "条形图" in msg or "对比" in msg:
            chart_type = "bar"
        elif "折线图" in msg or "趋势" in msg or "走势" in msg:
            chart_type = "line"

        # Extract project, metric, period - reuse query_data patterns
        m = re.search(r'(.+?)(近.*年|20\d{2}.*20\d{2}).*?(存货|总资产|负债|营收|净利润|营业收入|应收账款|营业成本|管理费用|销售费用|财务费用|营业利润|毛利率)', msg)
        if not m:
            m = re.search(r'(.+?)(?:的)?(存货|总资产|负债|营收|净利润|营业收入|应收账款|营业成本|管理费用|销售费用|财务费用|营业利润|毛利率)', msg)
        if m:
            project = m.group(1).strip()
            metric = m.group(3).strip() if len(m.groups()) == 3 else m.group(2).strip()
            metric_map = {
                "存货": "存货", "总资产": "总资产", "负债": "负债合计",
                "营收": "营业收入", "净利润": "净利润", "营业收入": "营业收入",
                "应收账款": "应收账款", "营业成本": "营业成本",
                "管理费用": "管理费用", "销售费用": "销售费用",
                "财务费用": "财务费用", "营业利润": "营业利润", "毛利率": "毛利率"
            }
            std_metric = metric_map.get(metric, metric)
            years_match = re.findall(r'20\d{2}', user_message)
            period_range = "-".join(years_match) if years_match else "近三年"
            return {
                "intent": "draw_chart",
                "params": {
                    "project_name": project,
                    "metric_name": std_metric,
                    "period_range": period_range,
                    "chart_type": chart_type
                },
                "response": f"正在绘制 {project} 的 {std_metric} {chart_type} 图..."
            }

    # query_data
    m = re.search(r'(.+?)(近.*年|20\d{2}.*20\d{2}|20\d{2}).*?(存货|总资产|负债|营收|收入|净利润|营业收入|应收账款)', msg)
    if not m:
        m = re.search(r'(.+?)(?:20\d{2}.*?)?(?:的)?(存货|总资产|负债|营收|收入|净利润|营业收入|应收账款)', msg)
    if m:
        project = m.group(1).strip()
        metric = m.group(3).strip() if len(m.groups()) == 3 else m.group(2).strip()
        metric_map = {"存货": "存货", "总资产": "总资产", "负债": "负债合计", "营收": "营业收入", "收入": "营业收入", "净利润": "净利润", "营业收入": "营业收入", "应收账款": "应收账款"}
        std_metric = metric_map.get(metric, metric)
        years_match = re.findall(r'20\d{2}', user_message)
        period_range = "-".join(years_match) if years_match else "近三年"
        return {
            "intent": "query_data",
            "params": {"project_name": project, "metric_name": std_metric, "period_range": period_range},
            "response": f"正在查询 {project} 的 {std_metric} 情况。"
        }

    return None


def execute_fill(params, db_path=None):
    from core import filler

    template_path = params.get("template_path")
    if not template_path or not os.path.exists(template_path):
        return {"success": False, "error": "未找到模板文件，请先上传模板。"}

    layout = params.get("layout", {})
    period = params.get("period")
    overrides = params.get("mapping_overrides", {})

    try:
        # Step 1: Fill with hard matches only (difflib + aliases), no LLM
        output_path, review_path, stats = filler.fill_template_with_layout(
            template_path, layout=layout, period=period,
            mapping_overrides=overrides, db_path=db_path, use_llm=False
        )

        # Step 2: Scan unmatched headers with LLM to collect candidates
        # (These are NOT filled yet — user must confirm first)
        from core import matcher
        matcher.clear_llm_match_log()
        seen = set()
        for item in stats.get("metric_scan_log", []):
            if not item.get("matched") and item.get("raw"):
                raw = item["raw"]
                if raw not in seen:
                    seen.add(raw)
                    matcher.match_header(raw, use_llm=True)
        llm_matches = matcher.get_llm_match_log()

        msg = (
            f"已生成正式版和审核版。"
            f"识别到 {stats['companies_found']} 个公司、{stats['metrics_found']} 个指标，"
            f"填充了 {stats['filled_count']} 个单元格"
        )
        if stats['filled_count'] == 0:
            msg += (
                f"。未填充原因可能是："
                f"(1) 模板表头文字和数据库标准名未匹配上，识别的指标为 {stats['metric_names'] or '空'}；"
                f"(2) 数据库里没有该项目/期间的数据。"
            )
        # Append diagnostic scan logs for debugging
        diag = stats.get('company_scan_log', [])
        if diag:
            recognized = [d for d in diag if d['recognized']]
            filtered = [d for d in diag if not d['recognized']]
            msg += f"\n【公司扫描】共扫描 {len(diag)} 个单元格，识别 {len(recognized)} 个"
            if filtered:
                msg += f"，过滤掉 {len(filtered)} 个（如：{', '.join(d['raw'] for d in filtered[:3])}）"
        mdiag = stats.get('metric_scan_log', [])
        if mdiag:
            matched = [d for d in mdiag if d['matched']]
            unmatched = [d for d in mdiag if not d['matched']]
            msg += f"\n【指标扫描】共扫描 {len(mdiag)} 个单元格，匹配 {len(matched)} 个"
            if unmatched:
                msg += f"，未匹配 {len(unmatched)} 个（如：{', '.join(d['raw'] for d in unmatched[:5])}）"
        layout_info = stats.get('layout_used')
        if layout_info:
            msg += f"\n【实际使用的布局】{layout_info}"

        # Show LLM fuzzy matches for user confirmation one by one
        pending_llm_queue = []
        if llm_matches:
            pending_llm_queue = [{"raw": raw, "standard": std} for raw, std in llm_matches]
            first = pending_llm_queue[0]
            msg += "\n\n【以下指标由大模型模糊匹配，请逐一确认：】\n"
            msg += f'第 1/{len(pending_llm_queue)} 个："{first["raw"]}" → "{first["standard"]}"\n'
            msg += '这个映射对吗？请回复「确认」或「不对」。'

        return {
            "success": True,
            "output": os.path.basename(output_path),
            "review": os.path.basename(review_path),
            "stats": stats,
            "message": msg,
            "pending_llm_queue": pending_llm_queue,
            "current_llm_index": 0,
            "confirmed_llm_mappings": {},
            "rejected_llm_mappings": []
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _resolve_project_name(project_name, all_projects):
    if project_name in all_projects:
        return project_name
    # Case-insensitive exact match
    lower_pn = project_name.lower()
    for db_name in all_projects:
        if db_name.lower() == lower_pn:
            return db_name
    # Case-insensitive substring match both ways
    for db_name in all_projects:
        lower_db = db_name.lower()
        if lower_pn in lower_db or lower_db in lower_pn:
            return db_name
    # Token-based match: e.g. "A公司" → ["A", "公司"] matches "公司A" → ["公司", "A"]
    tokens = re.findall(r"[A-Za-z0-9]+|[一-鿿]+", project_name)
    if tokens:
        for db_name in all_projects:
            db_tokens = re.findall(r"[A-Za-z0-9]+|[一-鿿]+", db_name)
            if all(t in db_tokens for t in tokens):
                return db_name
    return project_name


def execute_query(params, db_path=None):
    project_name = params.get("project_name")
    metric_name = params.get("metric_name")
    period_range = params.get("period_range", "")

    years = re.findall(r"20\d{2}", period_range)
    if not years:
        years = ["2023", "2024", "2025"]

    session = get_session(db_path=db_path)
    try:
        all_projects = [r[0] for r in session.query(ProjectFinancial.project_name).distinct().all()]
        resolved_name = _resolve_project_name(project_name, all_projects)

        results = []
        for year in sorted(years):
            period = f"{year}-12-31"
            metric_rec = session.query(ProjectMetric).filter_by(
                project_name=resolved_name, period=period, metric_name=metric_name
            ).first()
            if metric_rec:
                results.append({"期间": period, "指标": metric_name, "数值": metric_rec.metric_value})
                continue

            # Try exact match first, then aliases
            names_to_try = [metric_name]
            if metric_name in _METRIC_ALIASES:
                names_to_try.extend(_METRIC_ALIASES[metric_name])

            fin_rec = None
            for name in names_to_try:
                fin_rec = session.query(ProjectFinancial).filter_by(
                    project_name=resolved_name, period=period, item_name=name
                ).first()
                if fin_rec:
                    break

            if fin_rec:
                results.append({"期间": period, "指标": metric_name, "数值": fin_rec.value})
            else:
                results.append({"期间": period, "指标": metric_name, "数值": None})

        return {"success": True, "data": results}
    finally:
        session.close()


def execute_draw_chart(params, db_path=None):
    """Execute chart drawing by reusing query logic and formatting for ECharts."""
    chart_type = params.get("chart_type", "line")

    # Reuse existing query logic
    query_result = execute_query(params, db_path=db_path)
    if not query_result.get("success"):
        return query_result

    rows = query_result.get("data", [])
    if not rows:
        return {"success": False, "error": "未查询到数据，无法绘图。"}

    x_axis = [row["期间"] for row in rows]
    series_data = []
    for row in rows:
        val = row["数值"]
        series_data.append(val if val is not None else 0)

    return {
        "success": True,
        "chart_type": chart_type,
        "title": f"{params.get('project_name', '')} {params.get('metric_name', '')} 趋势",
        "x_axis": x_axis,
        "series": [
            {
                "name": params.get("metric_name", ""),
                "data": series_data
            }
        ]
    }


def execute_list_projects(db_path=None):
    session = get_session(db_path=db_path)
    try:
        names = [r[0] for r in session.query(ProjectFinancial.project_name).distinct().all()]
        return {"success": True, "projects": names}
    finally:
        session.close()


def execute_list_periods(params, db_path=None):
    project_name = params.get("project_name")
    session = get_session(db_path=db_path)
    try:
        periods = [r[0] for r in session.query(ProjectFinancial.period)
                   .filter_by(project_name=project_name).distinct().order_by(ProjectFinancial.period.desc()).all()]
        return {"success": True, "periods": periods}
    finally:
        session.close()


def execute_show_data(params, db_path=None):
    project_name = params.get("project_name")
    period = params.get("period")
    session = get_session(db_path=db_path)
    try:
        items = session.query(ProjectFinancial).filter_by(project_name=project_name, period=period).all()
        metrics = session.query(ProjectMetric).filter_by(project_name=project_name, period=period).all()
        return {
            "success": True,
            "project_name": project_name,
            "period": period,
            "items": [{"item_name": i.item_name, "value": i.value, "report_type": i.report_type} for i in items],
            "metrics": [{"metric_name": m.metric_name, "metric_value": m.metric_value} for m in metrics]
        }
    finally:
        session.close()


def execute_fund_summary(params, db_path=None):
    """Query fund fair value data: project list, summary stats."""
    fund_name = params.get("fund_name")
    period = params.get("period")

    session = get_session(db_path=db_path)
    try:
        # Find available periods if none specified
        if not period:
            periods = [r[0] for r in session.query(FundFairValue.period)
                       .filter_by(fund_name=fund_name).distinct().order_by(FundFairValue.period.desc()).all()]
            if not periods:
                return {"success": False, "error": f"未找到 {fund_name} 的任何数据"}
            period = periods[0]

        items = session.query(FundFairValue).filter_by(
            fund_name=fund_name, period=period
        ).order_by(FundFairValue.id).all()

        if not items:
            return {"success": False, "error": f"{fund_name} {period} 没有数据"}

        total_cost = sum(i.cost or 0 for i in items)
        total_fair_value = sum(i.fair_value or 0 for i in items)
        total_return = sum(i.total_return or 0 for i in items)

        return {
            "success": True,
            "fund_name": fund_name,
            "period": period,
            "project_count": len(items),
            "total_cost": round(total_cost, 2),
            "total_fair_value": round(total_fair_value, 2),
            "total_return": round(total_return, 2),
            "items": [
                {
                    "project_name": i.project_name,
                    "cost": i.cost,
                    "fair_value": i.fair_value,
                    "total_return": i.total_return,
                    "remark": i.remark,
                    "last_payment_date": i.last_payment_date,
                }
                for i in items
            ]
        }
    finally:
        session.close()


def execute_calculate_metric(params, db_path=None):
    project_name = params.get("project_name")
    metric_name = params.get("metric_name")
    period = params.get("period")
    calculation_type = params.get("calculation_type")

    session = get_session(db_path=db_path)
    try:
        all_projects = [r[0] for r in session.query(ProjectFinancial.project_name).distinct().all()]
        resolved_name = _resolve_project_name(project_name, all_projects)

        if calculation_type == "yoy_growth":
            year_match = re.search(r'(20\d{2})', period)
            if not year_match:
                return {"success": False, "error": "无法解析期间年份"}
            year = int(year_match.group(1))
            prev_period = f"{year - 1}-12-31"

            current_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=period, item_name=metric_name
            ).first()
            prev_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=prev_period, item_name=metric_name
            ).first()

            current_val = current_rec.value if current_rec else None
            prev_val = prev_rec.value if prev_rec else None

            if current_val is None:
                return {"success": False, "error": f"{resolved_name} {period} 的 {metric_name} 数据不存在"}
            if prev_val is None:
                return {"success": False, "error": f"{resolved_name} {prev_period} 的 {metric_name} 数据不存在，无法计算同比增长"}
            if prev_val == 0:
                return {"success": False, "error": f"{resolved_name} {prev_period} 的 {metric_name} 为0，无法计算同比增长"}

            growth = (current_val - prev_val) / abs(prev_val) * 100
            msg = (
                f"{resolved_name} {period} 的 {metric_name} 为 {current_val:.2f}，"
                f"{prev_period} 为 {prev_val:.2f}，同比增长 {growth:+.2f}%"
            )
            return {
                "success": True,
                "calculation_type": "yoy_growth",
                "metric_name": metric_name,
                "period": period,
                "prev_period": prev_period,
                "current_value": current_val,
                "prev_value": prev_val,
                "growth_rate": round(growth, 2),
                "message": msg
            }

        elif calculation_type == "inventory_days":
            days_multiplier = 360
            if "Q" in period.upper() or "q" in period:
                days_multiplier = 90

            inv_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=period, item_name="存货"
            ).first()
            cost_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=period, item_name="营业成本"
            ).first()

            if not inv_rec or inv_rec.value is None:
                return {"success": False, "error": f"{resolved_name} {period} 的存货数据不存在"}
            if not cost_rec or cost_rec.value is None:
                return {"success": False, "error": f"{resolved_name} {period} 的营业成本数据不存在"}

            year_match = re.search(r'(20\d{2})', period)
            prev_inv_val = None
            if year_match:
                prev_period = f"{int(year_match.group(1)) - 1}-12-31"
                prev_inv_rec = session.query(ProjectFinancial).filter_by(
                    project_name=resolved_name, period=prev_period, item_name="存货"
                ).first()
                if prev_inv_rec:
                    prev_inv_val = prev_inv_rec.value

            if prev_inv_val is not None:
                avg_inventory = (prev_inv_val + inv_rec.value) / 2
                avg_note = f"平均存货 = ({prev_inv_val:.2f} + {inv_rec.value:.2f}) / 2 = {avg_inventory:.2f}"
            else:
                avg_inventory = inv_rec.value
                avg_note = f"期初存货缺失，使用期末存货 {inv_rec.value:.2f} 作为平均存货"

            if cost_rec.value == 0:
                return {"success": False, "error": "营业成本为0，无法计算周转天数"}

            days = (avg_inventory / cost_rec.value) * days_multiplier
            msg = (
                f"{resolved_name} {period} 的存货周转天数为 {days:.2f} 天。\n"
                f"计算过程：{avg_note}，营业成本 {cost_rec.value:.2f}，"
                f"周转天数 = {avg_inventory:.2f} / {cost_rec.value:.2f} × {days_multiplier} = {days:.2f}"
            )
            return {
                "success": True,
                "calculation_type": "inventory_days",
                "period": period,
                "inventory": inv_rec.value,
                "cost": cost_rec.value,
                "avg_inventory": avg_inventory,
                "days": round(days, 2),
                "message": msg
            }

        elif calculation_type == "receivable_days":
            days_multiplier = 360
            if "Q" in period.upper() or "q" in period:
                days_multiplier = 90

            rec_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=period, item_name="应收账款"
            ).first()
            rev_rec = session.query(ProjectFinancial).filter_by(
                project_name=resolved_name, period=period, item_name="营业收入"
            ).first()

            if not rec_rec or rec_rec.value is None:
                return {"success": False, "error": f"{resolved_name} {period} 的应收账款数据不存在"}
            if not rev_rec or rev_rec.value is None:
                return {"success": False, "error": f"{resolved_name} {period} 的营业收入数据不存在"}

            year_match = re.search(r'(20\d{2})', period)
            prev_rec_val = None
            if year_match:
                prev_period = f"{int(year_match.group(1)) - 1}-12-31"
                prev_rec_rec = session.query(ProjectFinancial).filter_by(
                    project_name=resolved_name, period=prev_period, item_name="应收账款"
                ).first()
                if prev_rec_rec:
                    prev_rec_val = prev_rec_rec.value

            if prev_rec_val is not None:
                avg_receivable = (prev_rec_val + rec_rec.value) / 2
                avg_note = f"平均应收账款 = ({prev_rec_val:.2f} + {rec_rec.value:.2f}) / 2 = {avg_receivable:.2f}"
            else:
                avg_receivable = rec_rec.value
                avg_note = f"期初应收账款缺失，使用期末值 {rec_rec.value:.2f} 作为平均值"

            if rev_rec.value == 0:
                return {"success": False, "error": "营业收入为0，无法计算周转天数"}

            days = (avg_receivable / rev_rec.value) * days_multiplier
            msg = (
                f"{resolved_name} {period} 的应收账款周转天数为 {days:.2f} 天。\n"
                f"计算过程：{avg_note}，营业收入 {rev_rec.value:.2f}，"
                f"周转天数 = {avg_receivable:.2f} / {rev_rec.value:.2f} × {days_multiplier} = {days:.2f}"
            )
            return {
                "success": True,
                "calculation_type": "receivable_days",
                "period": period,
                "receivable": rec_rec.value,
                "revenue": rev_rec.value,
                "avg_receivable": avg_receivable,
                "days": round(days, 2),
                "message": msg
            }

        else:
            return {"success": False, "error": f"未知的计算类型: {calculation_type}"}
    finally:
        session.close()


def _save_mapping_to_config(raw, standard):
    import yaml
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    mappings = config.get('mappings', [])
    found = False
    for m in mappings:
        if m['standard'] == standard:
            aliases = m.get('aliases', [])
            if raw not in aliases:
                aliases.append(raw)
                m['aliases'] = aliases
            found = True
            break
    if not found:
        mappings.append({
            "standard": standard,
            "aliases": [raw],
            "category": "other",
            "db_source": "project"
        })

    config['mappings'] = mappings
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)


def execute_confirm_mappings(context, db_path=None):
    """Confirm the current LLM fuzzy mapping one by one."""
    pending = context.get("pending_llm_queue", []) if context else []
    current_idx = context.get("current_llm_index", 0) if context else 0
    confirmed = context.get("confirmed_llm_mappings", {}) if context else {}
    rejected = context.get("rejected_llm_mappings", []) if context else []

    if not pending or current_idx >= len(pending):
        return {"success": False, "error": "没有待确认的映射。"}

    current = pending[current_idx]
    raw = current.get("raw")
    std = current.get("standard")

    if raw and std:
        _save_mapping_to_config(raw, std)
        confirmed[raw] = std

    current_idx += 1

    template_path = context.get("template_path")
    layout = context.get("layout", {})
    period = context.get("period")

    if template_path and os.path.exists(template_path):
        from core import filler
        overrides = dict(confirmed)
        for r in rejected:
            overrides[r] = None
        output_path, review_path, stats = filler.fill_template_with_layout(
            template_path, layout=layout, period=period,
            mapping_overrides=overrides, db_path=db_path, use_llm=False
        )
    else:
        output_path = None
        review_path = None

    if current_idx < len(pending):
        next_item = pending[current_idx]
        msg = (
            f'已保存映射 "{raw}" → "{std}"。\n\n'
            f'第 {current_idx + 1}/{len(pending)} 个：'
            f'"{next_item["raw"]}" → "{next_item["standard"]}"\n'
            f'这个映射对吗？请回复「确认」或「不对」。'
        )
        return {
            "success": True,
            "message": msg,
            "output": os.path.basename(output_path) if output_path else None,
            "review": os.path.basename(review_path) if review_path else None,
            "current_llm_index": current_idx,
            "confirmed_llm_mappings": confirmed,
            "rejected_llm_mappings": rejected,
            "pending_llm_queue": pending,
            "done": False
        }
    else:
        msg = f'已保存映射 "{raw}" → "{std}"。所有大模型匹配已处理完毕。'
        return {
            "success": True,
            "message": msg,
            "output": os.path.basename(output_path) if output_path else None,
            "review": os.path.basename(review_path) if review_path else None,
            "current_llm_index": current_idx,
            "confirmed_llm_mappings": confirmed,
            "rejected_llm_mappings": rejected,
            "pending_llm_queue": pending,
            "done": True
        }


def execute_reject_mappings(context, db_path=None):
    """Reject the current LLM fuzzy mapping and move to next."""
    from core import filler

    pending = context.get("pending_llm_queue", []) if context else []
    current_idx = context.get("current_llm_index", 0) if context else 0
    confirmed = context.get("confirmed_llm_mappings", {}) if context else {}
    rejected = context.get("rejected_llm_mappings", []) if context else []

    if not pending or current_idx >= len(pending):
        return {"success": False, "error": "没有待拒绝的映射。"}

    current = pending[current_idx]
    raw = current.get("raw")
    if raw:
        rejected.append(raw)

    current_idx += 1

    template_path = context.get("template_path")
    layout = context.get("layout", {})
    period = context.get("period")

    if template_path and os.path.exists(template_path):
        overrides = dict(confirmed)
        for r in rejected:
            overrides[r] = None
        output_path, review_path, stats = filler.fill_template_with_layout(
            template_path, layout=layout, period=period,
            mapping_overrides=overrides, db_path=db_path, use_llm=False
        )
    else:
        output_path = None
        review_path = None

    if current_idx < len(pending):
        next_item = pending[current_idx]
        msg = (
            f'已跳过映射 "{raw}"。\n\n'
            f'第 {current_idx + 1}/{len(pending)} 个：'
            f'"{next_item["raw"]}" → "{next_item["standard"]}"\n'
            f'这个映射对吗？请回复「确认」或「不对」。'
        )
        return {
            "success": True,
            "message": msg,
            "output": os.path.basename(output_path) if output_path else None,
            "review": os.path.basename(review_path) if review_path else None,
            "current_llm_index": current_idx,
            "confirmed_llm_mappings": confirmed,
            "rejected_llm_mappings": rejected,
            "pending_llm_queue": pending,
            "done": False
        }
    else:
        msg = f'已跳过映射 "{raw}"。所有大模型匹配已处理完毕。'
        return {
            "success": True,
            "message": msg,
            "output": os.path.basename(output_path) if output_path else None,
            "review": os.path.basename(review_path) if review_path else None,
            "current_llm_index": current_idx,
            "confirmed_llm_mappings": confirmed,
            "rejected_llm_mappings": rejected,
            "pending_llm_queue": pending,
            "done": True
        }
