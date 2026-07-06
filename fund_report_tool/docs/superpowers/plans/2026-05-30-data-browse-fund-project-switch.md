# 数据浏览 Tab 基金/项目切换实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在"数据浏览" Tab 中增加数据类型切换（项目/基金），选择基金时展示基金公允价值明细及汇总统计。

**Architecture:** 新增 5 个后端 API 路由处理基金数据查询/删除/导出；前端在数据浏览区域顶部增加数据类型下拉框，通过 JS 动态切换 UI 和调用不同 API；基金报告导出使用 pandas 直接生成 Excel，不依赖模板匹配。

**Tech Stack:** FastAPI, SQLAlchemy, pandas, openpyxl, Bootstrap 5

---

## 文件结构

| 文件 | 操作 | 说明 |
|---|---|---|
| `app.py` | 修改 | 新增 `/api/funds`, `/api/fund_periods`, `/api/fund_data`, `/api/delete_fund_period`, `/api/download_fund_report` |
| `core/fund_exporter.py` | 新建 | 基金期间数据导出为 Excel |
| `templates/index.html` | 修改 | 数据浏览区域新增数据类型切换、基金模式 UI、JS 函数 |

---

### Task 1: 新增后端 API 路由

**Files:**
- Modify: `app.py`

**上下文：** 在 `app.py` 末尾（`api_download_report` 路由之后），新增 5 个 API 路由。需要导入 `FundFairValue` 模型。

- [ ] **Step 1: 在 app.py 导入区添加 FundFairValue**

找到：
```python
from core.database import init_db, get_session, ProjectFinancial, ProjectMetric
```

改为：
```python
from core.database import init_db, get_session, ProjectFinancial, ProjectMetric, FundFairValue
```

- [ ] **Step 2: 新增 /api/funds 路由**

在 `api_download_report` 路由之后添加：

```python
@app.get("/api/funds")
async def api_funds():
    session = get_session()
    try:
        fund_names = [
            r[0] for r in session.query(FundFairValue.fund_name).distinct().order_by(FundFairValue.fund_name).all()
        ]
        return JSONResponse({"funds": fund_names})
    finally:
        session.close()
```

- [ ] **Step 3: 新增 /api/fund_periods 路由**

```python
@app.get("/api/fund_periods")
async def api_fund_periods(fund: str):
    session = get_session()
    try:
        periods = [
            r[0] for r in session.query(FundFairValue.period)
            .filter_by(fund_name=fund).distinct().order_by(FundFairValue.period.desc()).all()
        ]
        return JSONResponse({"periods": periods})
    finally:
        session.close()
```

- [ ] **Step 4: 新增 /api/fund_data 路由**

```python
from sqlalchemy import func

@app.get("/api/fund_data")
async def api_fund_data(fund: str, period: str):
    session = get_session()
    try:
        items = session.query(FundFairValue).filter_by(
            fund_name=fund, period=period
        ).order_by(FundFairValue.id).all()

        total_cost = session.query(func.sum(FundFairValue.cost)).filter_by(
            fund_name=fund, period=period
        ).scalar() or 0.0

        total_fair_value = session.query(func.sum(FundFairValue.fair_value)).filter_by(
            fund_name=fund, period=period
        ).scalar() or 0.0

        return JSONResponse({
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
            ],
            "summary": {
                "total_cost": round(total_cost, 2),
                "total_fair_value": round(total_fair_value, 2),
            }
        })
    finally:
        session.close()
```

- [ ] **Step 5: 新增 /api/delete_fund_period 路由**

```python
@app.post("/api/delete_fund_period")
async def api_delete_fund_period(request: Request):
    body = await request.json()
    fund_name = body.get("fund_name")
    period = body.get("period")

    if not fund_name or not period:
        return JSONResponse({"error": "fund_name and period are required"}, status_code=400)

    session = get_session()
    try:
        deleted = session.query(FundFairValue).filter_by(
            fund_name=fund_name, period=period
        ).delete()
        session.commit()
        return JSONResponse({
            "status": "success",
            "records_deleted": deleted
        })
    except Exception as e:
        session.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        session.close()
```

- [ ] **Step 6: 新增 /api/download_fund_report 路由**

在 app.py 导入区添加：
```python
from core.fund_exporter import export_fund_report
```

```python
@app.get("/api/download_fund_report")
async def api_download_fund_report(fund: str, period: str):
    try:
        output_path = export_fund_report(fund, period)
        filename = os.path.basename(output_path)
        return FileResponse(output_path, filename=filename)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**验证：**
```bash
curl -s http://localhost:8000/api/funds | python3 -m json.tool
curl -s "http://localhost:8000/api/fund_periods?fund=并购一期" | python3 -m json.tool
curl -s "http://localhost:8000/api/fund_data?fund=并购一期&period=2026-03-31" | python3 -m json.tool
```

---

### Task 2: 创建 fund_exporter.py

**Files:**
- Create: `core/fund_exporter.py`

- [ ] **Step 1: 创建 core/fund_exporter.py**

```python
import os
import pandas as pd
from core.database import get_session, FundFairValue


def export_fund_report(fund_name: str, period: str, output_dir: str = 'data/outputs') -> str:
    """Export fund fair value data to an Excel file."""
    session = get_session()
    try:
        items = session.query(FundFairValue).filter_by(
            fund_name=fund_name, period=period
        ).order_by(FundFairValue.id).all()
    finally:
        session.close()

    if not items:
        raise ValueError(f'No data found for {fund_name} {period}')

    os.makedirs(output_dir, exist_ok=True)
    safe_fund = fund_name.replace('/', '_').replace('\\', '_')
    output_path = os.path.join(output_dir, f'{safe_fund}_{period}_基金公允价值.xlsx')

    data = []
    for i in items:
        data.append({
            '基金': i.fund_name,
            '项目': i.project_name,
            '剩余投资成本': i.cost,
            '项目公允价值': i.fair_value,
            '累计退出回收资金': i.total_return,
            '备注': i.remark or '',
            '最后回款日期': i.last_payment_date or '',
        })

    df = pd.DataFrame(data)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='基金公允价值', index=False)
        worksheet = writer.sheets['基金公允价值']
        # Auto-adjust column widths
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width

    return output_path
```

**验证：**
```bash
python3 -c "
from core.fund_exporter import export_fund_report
path = export_fund_report('并购一期', '2026-03-31')
print('Exported to:', path)
"
```

---

### Task 3: 修改前端 index.html

**Files:**
- Modify: `templates/index.html`

**上下文：** 数据浏览区域（`id="browse"` 的 tab-pane）当前只有"选择项目"和"选择期间"两个下拉框。需要：
1. 顶部增加"数据类型"下拉框（project/fund）
2. 切换 fund 模式时，"选择项目"标签变为"选择基金"，选项变为基金列表
3. 右侧展示区域切换为基金明细表 + 汇总卡片
4. 下载和删除按钮根据模式调用不同 API

- [ ] **Step 1: 修改数据浏览区域的 HTML 结构**

找到数据浏览区域（约第 108-135 行）：

```html
        <!-- Tab 4: Data Browse -->
        <div class="tab-pane fade" id="browse" role="tabpanel">
            <div class="row">
                <div class="col-md-3">
                    <div class="mb-3">
                        <label class="form-label">选择项目</label>
                        <select id="browseProject" class="form-select" onchange="loadPeriods()">
                            <option value="">-- 请选择 --</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">选择期间</label>
                        <select id="browsePeriod" class="form-select" onchange="loadData()">
                            <option value="">-- 请选择 --</option>
                        </select>
                    </div>
                    <button class="btn btn-primary" onclick="loadProjects()">刷新列表</button>
                    <button class="btn btn-success" onclick="downloadReport()">下载财报</button>
                    <button class="btn btn-danger" onclick="deleteProjectPeriod()">删除数据</button>
                </div>
                <div class="col-md-9">
                    <h6>财务数据</h6>
                    <div id="browseDataTable" class="result-box mb-3"></div>
                    <h6>衍生指标</h6>
                    <div id="browseMetricsTable" class="result-box"></div>
                </div>
            </div>
        </div>
```

替换为：

```html
        <!-- Tab 4: Data Browse -->
        <div class="tab-pane fade" id="browse" role="tabpanel">
            <div class="row">
                <div class="col-md-3">
                    <div class="mb-3">
                        <label class="form-label">数据类型</label>
                        <select id="browseType" class="form-select" onchange="toggleBrowseMode()">
                            <option value="project">项目数据</option>
                            <option value="fund">基金数据</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="form-label" id="browseEntityLabel">选择项目</label>
                        <select id="browseEntity" class="form-select" onchange="loadEntityPeriods()">
                            <option value="">-- 请选择 --</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">选择期间</label>
                        <select id="browsePeriod" class="form-select" onchange="loadData()">
                            <option value="">-- 请选择 --</option>
                        </select>
                    </div>
                    <button class="btn btn-primary" onclick="loadBrowseEntities()">刷新列表</button>
                    <button class="btn btn-success" onclick="downloadBrowseReport()">下载财报</button>
                    <button class="btn btn-danger" onclick="deleteBrowsePeriod()">删除数据</button>
                </div>
                <div class="col-md-9">
                    <div id="browseSummaryBox"></div>
                    <h6 id="browseDataTitle">财务数据</h6>
                    <div id="browseDataTable" class="result-box mb-3"></div>
                    <h6 id="browseMetricsTitle">衍生指标</h6>
                    <div id="browseMetricsTable" class="result-box"></div>
                </div>
            </div>
        </div>
```

- [ ] **Step 2: 新增 toggleBrowseMode 和基金相关 JS 函数**

在 `<script>` 区块末尾（`window.addEventListener('resize', ...)` 之前），添加：

```javascript
// --- Browse Tab: Project/Fund Switch ---

function toggleBrowseMode() {
    const mode = document.getElementById('browseType').value;
    const entityLabel = document.getElementById('browseEntityLabel');
    const dataTitle = document.getElementById('browseDataTitle');
    const metricsTitle = document.getElementById('browseMetricsTitle');

    if (mode === 'fund') {
        entityLabel.textContent = '选择基金';
        dataTitle.textContent = '基金项目明细';
        metricsTitle.textContent = '基金汇总';
    } else {
        entityLabel.textContent = '选择项目';
        dataTitle.textContent = '财务数据';
        metricsTitle.textContent = '衍生指标';
    }

    document.getElementById('browseEntity').innerHTML = '<option value="">-- 请选择 --</option>';
    document.getElementById('browsePeriod').innerHTML = '<option value="">-- 请选择 --</option>';
    document.getElementById('browseDataTable').innerHTML = '';
    document.getElementById('browseMetricsTable').innerHTML = '';
    document.getElementById('browseSummaryBox').innerHTML = '';
    loadBrowseEntities();
}

async function loadBrowseEntities() {
    const mode = document.getElementById('browseType').value;
    const sel = document.getElementById('browseEntity');
    sel.innerHTML = '<option value="">-- 请选择 --</option>';
    document.getElementById('browsePeriod').innerHTML = '<option value="">-- 请选择 --</option>';
    document.getElementById('browseDataTable').innerHTML = '';
    document.getElementById('browseMetricsTable').innerHTML = '';
    document.getElementById('browseSummaryBox').innerHTML = '';

    if (mode === 'fund') {
        const res = await fetch('/api/funds');
        const data = await res.json();
        sel.innerHTML += data.funds.map(f => `<option value="${f}">${f}</option>`).join('');
    } else {
        await loadProjects();
    }
}

async function loadEntityPeriods() {
    const mode = document.getElementById('browseType').value;
    const entity = document.getElementById('browseEntity').value;
    const sel = document.getElementById('browsePeriod');
    sel.innerHTML = '<option value="">-- 请选择 --</option>';
    if (!entity) return;

    if (mode === 'fund') {
        const res = await fetch(`/api/fund_periods?fund=${encodeURIComponent(entity)}`);
        const data = await res.json();
        sel.innerHTML += data.periods.map(p => `<option value="${p}">${p}</option>`).join('');
    } else {
        await loadPeriods();
    }
}

async function loadData() {
    const mode = document.getElementById('browseType').value;
    if (mode === 'fund') {
        await loadFundData();
    } else {
        await loadProjectData();
    }
}

async function loadFundData() {
    const fund = document.getElementById('browseEntity').value;
    const period = document.getElementById('browsePeriod').value;
    if (!fund || !period) return;

    const res = await fetch(`/api/fund_data?fund=${encodeURIComponent(fund)}&period=${encodeURIComponent(period)}`);
    const data = await res.json();

    // Summary cards
    const summary = data.summary || {};
    let summaryHtml = `
        <div class="row mb-3">
            <div class="col-md-6">
                <div class="card bg-light">
                    <div class="card-body p-2">
                        <small class="text-muted">总成本</small>
                        <div class="h5 mb-0">${summary.total_cost !== undefined ? summary.total_cost.toLocaleString() : '-'}</div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card bg-light">
                    <div class="card-body p-2">
                        <small class="text-muted">总公允价值</small>
                        <div class="h5 mb-0">${summary.total_fair_value !== undefined ? summary.total_fair_value.toLocaleString() : '-'}</div>
                    </div>
                </div>
            </div>
        </div>
    `;
    document.getElementById('browseSummaryBox').innerHTML = summaryHtml;

    // Detail table
    let html = '<table class="table table-sm"><thead><tr><th>项目</th><th>剩余投资成本</th><th>项目公允价值</th><th>累计退出回收资金</th><th>备注</th><th>最后回款日期</th></tr></thead><tbody>';
    for (const item of data.items) {
        html += `<tr>
            <td>${item.project_name}</td>
            <td>${item.cost !== null ? item.cost.toLocaleString() : '-'}</td>
            <td>${item.fair_value !== null ? item.fair_value.toLocaleString() : '-'}</td>
            <td>${item.total_return !== null ? item.total_return.toLocaleString() : '-'}</td>
            <td>${item.remark || '-'}</td>
            <td>${item.last_payment_date || '-'}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    document.getElementById('browseDataTable').innerHTML = html;

    // Metrics area: show summary again as simple table
    let mhtml = '<table class="table table-sm"><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>';
    mhtml += `<tr><td>项目数量</td><td>${data.items.length}</td></tr>`;
    mhtml += `<tr><td>总成本</td><td>${summary.total_cost !== undefined ? summary.total_cost.toLocaleString() : '-'}</td></tr>`;
    mhtml += `<tr><td>总公允价值</td><td>${summary.total_fair_value !== undefined ? summary.total_fair_value.toLocaleString() : '-'}</td></tr>`;
    if (summary.total_cost > 0 && summary.total_fair_value !== undefined) {
        const ratio = ((summary.total_fair_value / summary.total_cost - 1) * 100).toFixed(2);
        mhtml += `<tr><td>公允价值/成本比例</td><td>${ratio}%</td></tr>`;
    }
    mhtml += '</tbody></table>';
    document.getElementById('browseMetricsTable').innerHTML = mhtml;
}

// Rename existing loadData for project mode
async function loadProjectData() {
    const project = document.getElementById('browseEntity').value;
    const period = document.getElementById('browsePeriod').value;
    if (!project || !period) return;

    document.getElementById('browseSummaryBox').innerHTML = '';

    const [dataRes, metricsRes] = await Promise.all([
        fetch(`/api/data?project=${encodeURIComponent(project)}&period=${encodeURIComponent(period)}`),
        fetch(`/api/metrics_data?project=${encodeURIComponent(project)}&period=${encodeURIComponent(period)}`)
    ]);
    const data = await dataRes.json();
    const metrics = await metricsRes.json();

    let html = '<table class="table table-sm"><thead><tr><th>报表类型</th><th>指标</th><th>数值</th></tr></thead><tbody>';
    for (const item of data.items) {
        html += `<tr><td>${item.report_type}</td><td>${item.item_name}</td><td>${item.value !== null ? item.value : '-'}</td></tr>`;
    }
    html += '</tbody></table>';
    document.getElementById('browseDataTable').innerHTML = html;

    let mhtml = '<table class="table table-sm"><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>';
    for (const m of metrics.metrics) {
        mhtml += `<tr><td>${m.metric_name}</td><td>${m.metric_value !== null ? m.metric_value.toFixed(4) : '-'}</td></tr>`;
    }
    mhtml += '</tbody></table>';
    document.getElementById('browseMetricsTable').innerHTML = mhtml;
}

async function downloadBrowseReport() {
    const mode = document.getElementById('browseType').value;
    const entity = document.getElementById('browseEntity').value;
    const period = document.getElementById('browsePeriod').value;
    if (!entity || !period) {
        alert('请先选择项目和期间');
        return;
    }

    if (mode === 'fund') {
        const url = `/api/download_fund_report?fund=${encodeURIComponent(entity)}&period=${encodeURIComponent(period)}`;
        window.open(url, '_blank');
    } else {
        const url = `/api/download_report?project=${encodeURIComponent(entity)}&period=${encodeURIComponent(period)}`;
        window.open(url, '_blank');
    }
}

async function deleteBrowsePeriod() {
    const mode = document.getElementById('browseType').value;
    const entity = document.getElementById('browseEntity').value;
    const period = document.getElementById('browsePeriod').value;
    if (!entity || !period) {
        alert('请先选择项目和期间');
        return;
    }

    if (!confirm(`确定要删除 ${entity} ${period} 的所有数据吗？此操作不可恢复。`)) {
        return;
    }

    if (mode === 'fund') {
        const res = await fetch('/api/delete_fund_period', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({fund_name: entity, period: period})
        });
        const data = await res.json();
        if (data.status === 'success') {
            alert(`删除成功：${data.records_deleted} 条基金数据`);
            loadBrowseEntities();
            document.getElementById('browseDataTable').innerHTML = '';
            document.getElementById('browseMetricsTable').innerHTML = '';
            document.getElementById('browseSummaryBox').innerHTML = '';
        } else {
            alert('删除失败：' + (data.error || '未知错误'));
        }
    } else {
        await deleteProjectPeriod();
    }
}
```

- [ ] **Step 3: 修改原有的 loadProjects 函数，兼容新的 entity select**

找到 `loadProjects` 函数，修改它以填充 `browseEntity`：

```javascript
async function loadProjects() {
    const res = await fetch('/api/projects');
    const data = await res.json();
    const sel = document.getElementById('browseEntity');
    sel.innerHTML = '<option value="">-- 请选择 --</option>' +
        data.projects.map(p => `<option value="${p}">${p}</option>`).join('');
    document.getElementById('browsePeriod').innerHTML = '<option value="">-- 请选择 --</option>';
    document.getElementById('browseDataTable').innerHTML = '';
    document.getElementById('browseMetricsTable').innerHTML = '';
    document.getElementById('browseSummaryBox').innerHTML = '';
}
```

- [ ] **Step 4: 修改原有的 loadPeriods 函数，兼容新的 entity select**

```javascript
async function loadPeriods() {
    const project = document.getElementById('browseEntity').value;
    if (!project) return;
    const res = await fetch(`/api/periods?project=${encodeURIComponent(project)}`);
    const data = await res.json();
    const sel = document.getElementById('browsePeriod');
    sel.innerHTML = '<option value="">-- 请选择 --</option>' +
        data.periods.map(p => `<option value="${p}">${p}</option>`).join('');
}
```

- [ ] **Step 5: 修改原有的 deleteProjectPeriod 函数，兼容新的 select IDs**

```javascript
async function deleteProjectPeriod() {
    const project = document.getElementById('browseEntity').value;
    const period = document.getElementById('browsePeriod').value;
    if (!project || !period) {
        alert('请先选择项目和期间');
        return;
    }
    if (!confirm(`确定要删除 ${project} ${period} 的所有数据吗？此操作不可恢复。`)) {
        return;
    }

    const res = await fetch('/api/delete_project_period', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({project_name: project, period: period})
    });
    const data = await res.json();
    if (data.status === 'success') {
        alert(`删除成功：${data.financial_records_deleted} 条财务数据，${data.metric_records_deleted} 条指标数据`);
        loadBrowseEntities();
        document.getElementById('browseDataTable').innerHTML = '';
        document.getElementById('browseMetricsTable').innerHTML = '';
        document.getElementById('browseSummaryBox').innerHTML = '';
    } else {
        alert('删除失败：' + (data.error || '未知错误'));
    }
}
```

- [ ] **Step 6: 修改初始化代码和 tab 切换事件**

找到初始化代码：
```javascript
// Initialize UI on load
updateTemplateUI();
toggleTemplateMode();

// Load projects on tab switch
document.getElementById('browse-tab').addEventListener('shown.bs.tab', loadProjects);
```

改为：
```javascript
// Initialize UI on load
updateTemplateUI();
toggleTemplateMode();

// Load browse entities on tab switch
document.getElementById('browse-tab').addEventListener('shown.bs.tab', loadBrowseEntities);
```

**验证：** 刷新网页，切换到"数据浏览" Tab，切换数据类型下拉框，观察：
1. "选择项目"标签是否变为"选择基金"
2. 基金模式下刷新列表是否加载基金名
3. 选择基金后是否加载期间
4. 选择期间后是否展示基金明细和汇总卡片

---

### Task 4: 端到端测试

- [ ] **Step 1: 重启 uvicorn 服务**

```bash
# 找到并重启 uvicorn
pkill -f "uvicorn app:app" ; sleep 1 ; uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

- [ ] **Step 2: 测试 API**

```bash
curl -s http://localhost:8000/api/funds | python3 -m json.tool
curl -s "http://localhost:8000/api/fund_periods?fund=并购一期" | python3 -m json.tool
curl -s "http://localhost:8000/api/fund_data?fund=并购一期&period=2026-03-31" | python3 -m json.tool
```

- [ ] **Step 3: 测试下载**

```bash
curl -s -o /tmp/fund_test.xlsx "http://localhost:8000/api/download_fund_report?fund=并购一期&period=2026-03-31"
ls -lh /tmp/fund_test.xlsx
```

- [ ] **Step 4: 网页测试**

1. 打开 `http://localhost:8000`
2. 切换到"数据浏览" Tab
3. 数据类型选择"基金数据"
4. 选择"并购一期"，期间"2026-03-31"
5. 观察右侧是否正确展示项目明细 + 总成本/总公允价值汇总卡片
6. 点击"下载财报"，检查下载的 Excel 是否正确
7. 切回"项目数据"，确认原有项目浏览功能正常

---

## Self-Review

### Spec Coverage Check

| Spec 要求 | 对应任务 |
|---|---|
| 数据类型切换（项目/基金） | Task 3 Step 1-2 toggleBrowseMode |
| 基金列表 API | Task 1 Step 2 /api/funds |
| 基金期间 API | Task 1 Step 3 /api/fund_periods |
| 基金数据 API（含汇总） | Task 1 Step 4 /api/fund_data |
| 基金删除 API | Task 1 Step 5 /api/delete_fund_period |
| 基金下载 API | Task 1 Step 6 /api/download_fund_report |
| 基金报告导出 | Task 2 fund_exporter.py |
| 前端基金明细表格 | Task 3 Step 2 loadFundData |
| 总成本/总公允价值汇总 | Task 3 Step 2 summary cards |
| 原有项目浏览功能保留 | Task 3 Step 3-5 loadProjects/loadPeriods 兼容 |

**无遗漏。**

### Placeholder Scan

- 无 "TBD", "TODO", "implement later"
- 所有代码块包含完整可运行代码
- 所有步骤包含具体文件路径

### Type Consistency Check

- API 路由参数名：`fund` (query param), `fund_name` (JSON body) — 一致
- JS 函数：`loadBrowseEntities`, `loadEntityPeriods`, `loadData`, `loadFundData`, `loadProjectData` — 命名清晰
- 汇总字段名：`total_cost`, `total_fair_value` — 前后端一致

---

## 执行选项

Plan complete and saved to `docs/superpowers/plans/2026-05-30-data-browse-fund-project-switch.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints for review

Which approach?
