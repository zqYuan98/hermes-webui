import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / "extensions" / "extensions.json"
STORE = ROOT / "extensions" / "hub" / "hub-store.js"
COST = ROOT / "extensions" / "hub" / "agent-cost.js"
HUB = ROOT / "extensions" / "hub" / "hub.js"
CSS = ROOT / "extensions" / "hub" / "hub.css"
NODE = shutil.which("node")


def run_cost(expression: str):
    if not NODE:
        pytest.skip("node is required for AgentCost behavior tests")
    script = f"""
global.window = {{}};
require({json.dumps(str(COST))});
const AgentCost = window.AgentCost;
const result = ({expression});
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def run_store(body: str):
    if not NODE:
        pytest.skip("node is required for HubStore behavior tests")
    script = f"""
const storage = {{'hermes-hub.session':'sid','hermes-hub.root':'/hub'}};
global.localStorage = {{getItem:k=>storage[k]||'',setItem:(k,v)=>storage[k]=v,removeItem:k=>delete storage[k]}};
global.window = {{}};
let remoteText = '';
let readError = null;
window.api = async function(path, opts) {{
  if (path.startsWith('/api/list')) return {{items:[]}};
  if (path === '/api/hub/init') return {{ok:true}};
  if (path.startsWith('/api/file?')) {{
    if (readError) throw readError;
    return {{content: remoteText}};
  }}
  if (path === '/api/file/save' || path === '/api/file/create') {{
    remoteText = JSON.parse(opts.body).content;
    return {{ok:true}};
  }}
  throw new Error('unexpected api '+path);
}};
require({json.dumps(str(STORE))});
(async()=>{{
{body}
}})().catch(err=>{{console.error(err);process.exit(1);}});
"""
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def test_agent_cost_script_loads_before_hub_and_store_scaffolds_dedicated_file():
    manifest = json.loads(EXTENSIONS.read_text(encoding="utf-8"))
    scripts = manifest["extensions"][0]["scripts"]
    assert scripts.index("hub/agent-cost.js") < scripts.index("hub/hub.js")

    source = STORE.read_text(encoding="utf-8")
    assert "agentCosts: 'hub-agent-costs.json'" in source
    assert "subscriptions: []" in source
    assert "expenses: []" in source
    assert "budgets: []" in source
    assert "hub-agent-costs.json" in source.split("var HUB_README", 1)[1]
    assert "hub-model-resources.json" not in source


def test_summary_distinguishes_confirmed_zero_pending_and_no_cost():
    data = {
        "subscriptions": [
            {"id": "s1", "status": "active", "startMonth": "2026-01", "monthlyAmount": 100, "amountStatus": "confirmed"},
            {"id": "s2", "status": "active", "startMonth": "2026-01", "monthlyAmount": "", "amountStatus": "pending"},
            {"id": "s3", "status": "configured", "startMonth": "2026-01", "monthlyAmount": 50, "amountStatus": "confirmed"},
            {"id": "s4", "status": "active", "startMonth": "2026-01", "monthlyAmount": 0, "amountStatus": "confirmed"},
        ],
        "expenses": [
            {"id": "e1", "month": "2026-08", "costType": "usage", "amount": 80, "amountStatus": "confirmed"},
            {"id": "e2", "month": "2026-08", "costType": "infrastructure", "amount": 20, "amountStatus": "confirmed"},
            {"id": "e3", "month": "2026-08", "costType": "other", "amount": "", "amountStatus": "pending"},
            {"id": "e4", "month": "2026-08", "costType": "usage", "amount": "", "amountStatus": "no_cost"},
            {"id": "e5", "month": "2026-07", "costType": "usage", "amount": 40, "amountStatus": "confirmed"},
        ],
        "budgets": [{"id": "b1", "month": "2026-08", "amount": 250}],
    }
    summary = run_cost(f"AgentCost.summarize({json.dumps(data)}, '2026-08')")
    assert summary == {
        "month": "2026-08",
        "subscription": 100,
        "usage": 80,
        "cloudServer": 0,
        "proxyNetwork": 0,
        "infrastructure": 20,
        "other": 20,
        "consumption": 100,
        "total": 200,
        "budget": 250,
        "budgetVariance": -50,
        "previousTotal": 140,
        "momAmount": 60,
        "momPercent": pytest.approx(42.857142857142854),
        "pendingCount": 3,
        "confirmedCount": 4,
    }


def test_subscription_requires_start_month_and_paused_requires_end_month():
    missing_start = run_cost(
        "AgentCost.validateSubscription({status:'active', startMonth:'', endMonth:''})"
    )
    paused_without_end = run_cost(
        "AgentCost.validateSubscription({status:'paused', startMonth:'2026-01', endMonth:''})"
    )
    valid = run_cost(
        "AgentCost.validateSubscription({status:'paused', startMonth:'2026-01', endMonth:'2026-06'})"
    )
    assert "开始月份" in missing_start
    assert "结束月份" in paused_without_end
    assert valid == ""

    malformed = {
        "subscriptions": [
            {"id": "legacy", "status": "active", "startMonth": "", "monthlyAmount": 88, "amountStatus": "confirmed"}
        ],
        "expenses": [],
        "budgets": [],
    }
    trend = run_cost(f"AgentCost.trend({json.dumps(malformed)}, '2026-08', 6)")
    assert [row["total"] for row in trend] == [0, 0, 0, 0, 0, 0]
    assert trend[-1]["pendingCount"] == 1

    paused_malformed = {
        "subscriptions": [
            {"id": "paused", "status": "paused", "startMonth": "2026-01", "endMonth": "", "monthlyAmount": 88, "amountStatus": "confirmed"}
        ],
        "expenses": [],
        "budgets": [],
    }
    paused_summary = run_cost(f"AgentCost.summarize({json.dumps(paused_malformed)}, '2026-08')")
    paused_attention = run_cost(f"AgentCost.subscriptionNeedsAttention({json.dumps(paused_malformed['subscriptions'][0])}, '2026-08')")
    assert paused_summary["total"] == 0
    assert paused_summary["pendingCount"] == 1
    assert paused_attention is True


def test_subscription_history_survives_pause_and_mom_zero_is_null():
    data = {
        "subscriptions": [
            {
                "id": "s1",
                "status": "paused",
                "startMonth": "2026-01",
                "endMonth": "2026-06",
                "monthlyAmount": 99,
                "amountStatus": "confirmed",
            }
        ],
        "expenses": [],
        "budgets": [],
    }
    historical = run_cost(f"AgentCost.summarize({json.dumps(data)}, '2026-06')")
    after_pause = run_cost(f"AgentCost.summarize({json.dumps(data)}, '2026-07')")
    assert historical["total"] == 99
    assert historical["momPercent"] == 0
    assert after_pause["total"] == 0
    assert after_pause["momPercent"] == -100

    empty = run_cost("AgentCost.summarize({subscriptions:[], expenses:[], budgets:[]}, '2026-08')")
    assert empty["previousTotal"] == 0
    assert empty["momPercent"] is None


def test_monthly_record_count_drives_sidebar_not_pending_count():
    data = {
        "subscriptions": [
            {"id": "active", "status": "active", "startMonth": "2026-08", "monthlyAmount": 10, "amountStatus": "confirmed"},
            {"id": "configured", "status": "configured", "startMonth": "2026-08", "monthlyAmount": "", "amountStatus": "pending"},
            {"id": "ended", "status": "paused", "startMonth": "2026-01", "endMonth": "2026-07", "monthlyAmount": 5, "amountStatus": "confirmed"},
            {"id": "future", "status": "active", "startMonth": "2026-09", "monthlyAmount": 5, "amountStatus": "confirmed"},
        ],
        "expenses": [
            {"id": "confirmed", "month": "2026-08", "amount": 2, "amountStatus": "confirmed"},
            {"id": "free", "month": "2026-08", "amount": "", "amountStatus": "no_cost"},
            {"id": "other-month", "month": "2026-07", "amount": 3, "amountStatus": "confirmed"},
        ],
        "budgets": [],
    }
    assert run_cost(f"AgentCost.monthlyRecordCount({json.dumps(data)}, '2026-08')") == 4

    source = HUB.read_text(encoding="utf-8")
    assert "AgentCost.monthlyRecordCount" in source
    assert "summarize(d.agentCosts || {}, selectedAgentCostMonth()).pendingCount" not in source


def test_six_month_trend_is_chronological_and_includes_selected_month():
    data = {
        "subscriptions": [],
        "expenses": [
            {"id": f"e{i}", "month": month, "costType": "usage", "amount": amount, "amountStatus": "confirmed"}
            for i, (month, amount) in enumerate(
                [("2026-03", 10), ("2026-04", 20), ("2026-05", 30), ("2026-06", 40), ("2026-07", 50), ("2026-08", 60)]
            )
        ],
        "budgets": [],
    }
    trend = run_cost(f"AgentCost.trend({json.dumps(data)}, '2026-08', 6)")
    assert [row["month"] for row in trend] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert [row["total"] for row in trend] == [10, 20, 30, 40, 50, 60]


def test_cloud_servers_proxy_subscriptions_and_allocations_are_first_class_costs():
    data = {
        "subscriptions": [
            {
                "id": "cloud-overseas", "status": "active", "startMonth": "2026-01",
                "monthlyAmount": 120, "amountStatus": "confirmed", "costCategory": "cloud_server",
                "region": "overseas", "allocation": "company_self",
            },
            {
                "id": "proxy", "status": "active", "startMonth": "2026-01",
                "monthlyAmount": 80, "amountStatus": "confirmed", "costCategory": "proxy_subscription",
                "region": "overseas", "allocation": "company_self",
            },
            {
                "id": "prepaid-api", "status": "active", "startMonth": "2026-01",
                "monthlyAmount": 25, "amountStatus": "confirmed", "costCategory": "model_usage",
                "region": "domestic", "allocation": "company_self",
            },
            {
                "id": "customer-openclaw", "status": "active", "startMonth": "2026-08",
                "monthlyAmount": "", "amountStatus": "pending", "costCategory": "cloud_server",
                "allocation": "customer_project", "allocationName": "客户待补", "deploymentUse": "customer_openclaw",
            },
        ],
        "expenses": [
            {
                "id": "department-server", "month": "2026-08", "amount": 300,
                "amountStatus": "confirmed", "costCategory": "cloud_server",
                "allocation": "internal_department", "allocationName": "业务部门",
                "deploymentUse": "department_openclaw",
            },
            {
                "id": "api", "month": "2026-08", "amount": 50,
                "amountStatus": "confirmed", "costCategory": "model_usage", "allocation": "company_self",
            },
        ],
        "budgets": [],
    }
    summary = run_cost(f"AgentCost.summarize({json.dumps(data)}, '2026-08')")
    assert summary["subscription"] == 0
    assert summary["usage"] == 75
    assert summary["cloudServer"] == 420
    assert summary["proxyNetwork"] == 80
    assert summary["infrastructure"] == 500
    assert summary["total"] == 575
    assert summary["pendingCount"] == 1

    explicit_other = run_cost(
        "AgentCost.summarize({subscriptions:[],expenses:[{id:'legacy-mixed',month:'2026-08',costCategory:'other',costType:'infrastructure',amount:20,amountStatus:'confirmed'}],budgets:[]}, '2026-08')"
    )
    assert explicit_other["other"] == 20
    assert explicit_other["infrastructure"] == 0
    assert explicit_other["total"] == 20

    allocation = run_cost(f"AgentCost.allocationBreakdown({json.dumps(data)}, '2026-08')")
    assert allocation == {
        "company_self": 275,
        "customer_project": 0,
        "internal_department": 300,
        "personal_reimbursement": 0,
        "unassigned": 0,
    }


def test_confirmed_cloud_cost_requires_unique_billing_resource_id():
    data = {
        "subscriptions": [
            {
                "id": "cloud-1", "status": "active", "startMonth": "2026-08",
                "costCategory": "cloud_server", "billingResourceId": "ins-001",
                "monthlyAmount": 100, "amountStatus": "confirmed",
            }
        ],
        "expenses": [],
        "budgets": [],
    }
    missing = run_cost(
        "AgentCost.validateCloudIdentity({costCategory:'cloud_server', amountStatus:'confirmed', billingResourceId:''}, {subscriptions:[],expenses:[]}, '')"
    )
    duplicate = run_cost(
        f"AgentCost.validateCloudIdentity({{id:'cloud-2',costCategory:'cloud_server',amountStatus:'confirmed',billingResourceId:' INS-001 '}}, {json.dumps(data)}, 'cloud-2')"
    )
    historical = {
        "subscriptions": [],
        "expenses": [
            {
                "id": "purchase", "month": "2026-07", "costCategory": "cloud_server",
                "billingResourceId": "ins-001", "amount": 300, "amountStatus": "confirmed",
            }
        ],
        "budgets": [],
    }
    non_overlapping = run_cost(
        f"AgentCost.validateCloudIdentity({{id:'aug-fee',month:'2026-08',costCategory:'cloud_server',amountStatus:'confirmed',billingResourceId:'ins-001'}}, {json.dumps(historical)}, 'aug-fee')"
    )
    pending = run_cost(
        f"AgentCost.validateCloudIdentity({{costCategory:'cloud_server',amountStatus:'pending',billingResourceId:''}}, {json.dumps(data)}, '')"
    )
    assert "实例标识" in missing
    assert "重复" in duplicate
    assert non_overlapping == ""
    assert pending == ""


def test_agent_cost_store_merges_external_additions_and_rejects_same_row_conflicts():
    merged = run_store("""
remoteText=JSON.stringify({version:1,updatedAt:'base',subscriptions:[{id:'s1',name:'Base'}],expenses:[],budgets:[]});
await window.HubStore.init();
const local=await window.HubStore.read('agentCosts',{strict:true});
local.subscriptions[0].name='Local';
remoteText=JSON.stringify({version:1,updatedAt:'remote',subscriptions:[{id:'s1',name:'Base'},{id:'s2',name:'Remote add'}],expenses:[],budgets:[]});
await window.HubStore.write('agentCosts',local);
process.stdout.write(JSON.stringify(JSON.parse(remoteText)));
""")
    assert {row["id"]: row["name"] for row in merged["subscriptions"]} == {
        "s1": "Local",
        "s2": "Remote add",
    }

    conflict = run_store("""
remoteText=JSON.stringify({version:1,updatedAt:'base',subscriptions:[{id:'s1',name:'Base'}],expenses:[],budgets:[]});
await window.HubStore.init();
const local=await window.HubStore.read('agentCosts',{strict:true});
local.subscriptions[0].name='Local';
remoteText=JSON.stringify({version:1,updatedAt:'remote',subscriptions:[{id:'s1',name:'Remote'}],expenses:[],budgets:[]});
let error=''; try { await window.HubStore.write('agentCosts',local); } catch (err) { error=err.message; }
process.stdout.write(JSON.stringify({error, persisted:JSON.parse(remoteText)}));
""")
    assert "其他页面或 Agent 修改" in conflict["error"]
    assert conflict["persisted"]["subscriptions"][0]["name"] == "Remote"


def test_agent_cost_store_only_treats_404_as_missing_and_blocks_other_read_failures():
    blocked = run_store("""
remoteText='ORIGINAL';
readError=Object.assign(new Error('server failed'),{status:500});
await window.HubStore.init();
let readValue=null; try { readValue=await window.HubStore.read('agentCosts'); } catch (_) {}
let writeError=''; try { await window.HubStore.write('agentCosts',{version:1,subscriptions:[],expenses:[],budgets:[]}); } catch (err) { writeError=err.message; }
process.stdout.write(JSON.stringify({readValue,writeError,remoteText}));
""")
    assert blocked["readValue"] is None
    assert "拒绝" in blocked["writeError"] or "禁止" in blocked["writeError"]
    assert blocked["remoteText"] == "ORIGINAL"

    created = run_store("""
readError=Object.assign(new Error('not found'),{status:404});
await window.HubStore.init();
const initial=await window.HubStore.read('agentCosts');
readError=null;
remoteText='';
await window.HubStore.write('agentCosts',initial);
process.stdout.write(JSON.stringify(JSON.parse(remoteText)));
""")
    assert created["subscriptions"] == []
    assert created["expenses"] == []
    assert created["budgets"] == []


def test_agent_cost_store_strictly_validates_structure_and_ids():
    for malformed in [
        {"version": 1, "subscriptions": "bad", "expenses": [], "budgets": []},
        {"version": 1, "subscriptions": [], "expenses": ["bad"], "budgets": []},
        {"version": 1, "subscriptions": [{"id": "same"}, {"id": "same"}], "expenses": [], "budgets": []},
    ]:
        result = run_store(f"""
remoteText=JSON.stringify({json.dumps(malformed)});
await window.HubStore.init();
let error=''; try {{ await window.HubStore.read('agentCosts',{{strict:true}}); }} catch (err) {{ error=err.message; }}
process.stdout.write(JSON.stringify({{error,remoteText}}));
""")
        assert "无法解析" in result["error"]
        assert json.loads(result["remoteText"]) == malformed


def test_agent_cost_store_fails_closed_on_corrupted_json():
    result = run_store("""
remoteText='{broken';
await window.HubStore.init();
let readError=''; try { await window.HubStore.read('agentCosts',{strict:true}); } catch (err) { readError=err.message; }
let writeError=''; try { await window.HubStore.write('agentCosts',{version:1,subscriptions:[],expenses:[],budgets:[]}); } catch (err) { writeError=err.message; }
process.stdout.write(JSON.stringify({readError,writeError,remoteText}));
""")
    assert "无法解析" in result["readError"]
    assert "禁止覆盖" in result["writeError"]
    assert result["remoteText"] == "{broken"


def test_hub_ui_is_agent_cost_focused_and_has_monthly_crud_controls():
    source = HUB.read_text(encoding="utf-8")
    assert "id: 'agentCosts'" in source
    assert "label: 'Agent 成本'" in source
    assert "公司每月 Agent 消耗与订阅费用" in source
    assert "type=\"month\"" in source or "type', 'month'" in source
    for text in [
        "总费用", "Agent/API", "云服务器", "代理与网络", "预算偏差", "环比", "待补录",
        "近 6 个月", "续费提醒", "客户项目", "公司其他部门", "个人代付待报销",
        "客户小龙虾", "内部部门小龙虾", "国内", "境外", "待补录与重复核验",
        "明确免费请填写 0", "本月无费用",
    ]:
        assert text in source
    for action in ["new-agent-subscription", "new-agent-expense", "save-agent-budget", "agent-cost-prev-month", "agent-cost-next-month"]:
        assert action in source
    assert "saveAgentCosts" in source
    assert "AgentCost.subscriptionNeedsAttention" in source
    assert "amountBadge(row, 'monthlyAmount', month)" in source
    assert "已重新读取磁盘数据" in source
    assert "模型/API与服务器资源台账" not in source
    assert "服务器资源主数据" not in source
    assert "API Key" not in source

    css = CSS.read_text(encoding="utf-8")
    assert ".hub-agent-cost" in css
    assert "@media" in css
