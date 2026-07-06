# 数据浏览 Tab 基金/项目切换设计文档

## 概览

在"数据浏览" Tab 中增加数据类型切换（项目 / 基金），选择基金时展示基金公允价值明细及汇总统计。

## 前端设计

### 布局（方案 A：顶部下拉切换）

```
数据浏览 Tab
├── 数据类型: [项目数据 ▼]        ← 新增
├── 选择项目: [-- 请选择 -- ▼]    ← 项目模式下显示
├── 选择期间: [-- 请选择 -- ▼]
├── [刷新列表] [下载财报] [删除数据]
│
└── 右侧展示区域:
    ├── 财务数据（项目模式）或 基金明细（基金模式）
    └── 衍生指标（项目模式）或 基金汇总（基金模式）
```

当数据类型切换为"基金数据"时：
- "选择项目"标签变为"选择基金"
- 下拉框选项从项目列表变为基金列表
- 右侧表格展示 `fund_fair_values` 的各项目明细
- 新增汇总卡片：总成本、总公允价值

### 基金明细表格列

| 列名 | 来源字段 |
|---|---|
| 项目 | `project_name` |
| 剩余投资成本 | `cost` |
| 项目公允价值 | `fair_value` |
| 累计退出回收资金 | `total_return` |
| 备注 | `remark` |
| 最后回款日期 | `last_payment_date` |

### 基金汇总统计

在表格上方展示汇总卡片：
- **总成本**: `sum(cost)`
- **总公允价值**: `sum(fair_value)`

## 后端 API

### 新增路由

| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/funds` | GET | 返回所有基金名列表（去重） |
| `/api/fund_periods` | GET | 参数 `fund`，返回该基金的所有期间 |
| `/api/fund_data` | GET | 参数 `fund`, `period`，返回明细+汇总 |
| `/api/delete_fund_period` | POST | 参数 `fund_name`, `period`，删除数据 |
| `/api/download_fund_report` | GET | 参数 `fund`, `period`，下载 Excel |

### 响应格式

**GET /api/funds**
```json
{"funds": ["并购一期", "浦江一期", "并购三期"]}
```

**GET /api/fund_periods?fund=并购一期**
```json
{"periods": ["2026-03-31"]}
```

**GET /api/fund_data?fund=并购一期&period=2026-03-31**
```json
{
  "items": [
    {"project_name": "BI股份有限公司", "cost": 41453.0, "fair_value": 138932.9, ...}
  ],
  "summary": {
    "total_cost": 123456.0,
    "total_fair_value": 789012.0
  }
}
```

## 实现范围

### 本次实现
- `templates/index.html`：数据浏览区域新增数据类型切换、基金模式 UI、JS 函数
- `app.py`：新增 `/api/funds`, `/api/fund_periods`, `/api/fund_data`, `/api/delete_fund_period`, `/api/download_fund_report`
- `core/exporter.py`：新增 `export_fund_report(fund, period)` 函数（或独立文件 `core/fund_exporter.py`）

### 暂不考虑
- 基金数据编辑功能
- 多基金对比视图
- 基金指标自动计算（DPI/TVPI）
