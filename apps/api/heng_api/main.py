"""FastAPI 组合根（§15）。M0 端点用 RESTful 子路径（execute/dry-run），M1 增加 MCP Server。"""
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from heng_contracts.models import ActionRequest, ActorRef, ApprovalDecision, new_id
from heng_kernel import db
from heng_actions.compose import build_engine, ensure_tenant
from heng_eval.fixture import build_fixture
from heng_agent.orchestrator import Orchestrator
from heng_agent.providers import RuleBasedProvider
from heng_llm.gateway import LLMGateway, OpenAICompatProvider

ENTITY_MODELS = {
    "sales.order": db.SalesOrder, "sales.order.line": db.SalesOrderLine,
    "journal.entry": db.JournalEntry, "journal.line": db.JournalLine,
    "stock.quant": db.StockQuant, "stock.move": db.StockMove,
    "tax.invoice": db.Invoice, "res.partner": db.Partner, "product.sku": db.Sku,
}


class ActionBody(BaseModel):
    args: dict = Field(default_factory=dict)
    tenant_id: str = "t_demo"
    actor_type: str = "human"
    actor_id: str = "u_api"
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    on_behalf_of: str | None = None
    client_request_id: str = Field(default_factory=lambda: new_id("req"))


class CopilotBody(BaseModel):
    utterance: str
    tenant_id: str = "t_demo"
    user_id: str = "u_boss"
    sensitive: bool = False


class ApproveBody(BaseModel):
    approver_id: str
    decision: str = "approve"
    reason: str = ""
    tenant_id: str = "t_demo"


def create_app(engine=None, sf=None, inv=None) -> FastAPI:
    if engine is None:
        engine, sf, inv = build_engine(os.environ.get("HENG_DB_URL", "sqlite:///heng_dev.db"))
    app = FastAPI(title="HengERP API", version="0.1.0")

    providers = [RuleBasedProvider()]
    base, key = os.environ.get("HENG_LLM_BASE_URL"), os.environ.get("HENG_LLM_API_KEY")
    if base and key:
        providers.insert(0, OpenAICompatProvider("cloud-llm", base, key,
                                                 os.environ.get("HENG_LLM_MODEL", "qwen-max"),
                                                 is_private=False))
    gateway = LLMGateway(providers)
    orch = Orchestrator(engine, gateway)

    @app.on_event("startup")
    def _seed():
        ensure_tenant(sf, "t_demo", "演示租户")
        with sf() as s:
            n = s.execute(select(db.Partner).where(db.Partner.tenant_id == "t_demo")).scalars().all()
        if not n:
            build_fixture(engine, sf, "t_demo")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "clock": engine.clock.isoformat(),
                "llm_offline": gateway.offline, "edition": "community-M0"}

    @app.post("/v1/actions/{action}/execute")
    def execute(action: str, body: ActionBody):
        req = ActionRequest(action=action, args=body.args,
                            actor=ActorRef(type=body.actor_type, id=body.actor_id,
                                           tenant_id=body.tenant_id, scopes=body.scopes),
                            on_behalf_of=body.on_behalf_of,
                            client_request_id=body.client_request_id)
        return engine.execute(req).model_dump(mode="json")

    @app.post("/v1/actions/{action}/dry-run")
    def dry_run(action: str, body: ActionBody):
        req = ActionRequest(action=action, args=body.args, dry_run=True,
                            actor=ActorRef(type=body.actor_type, id=body.actor_id,
                                           tenant_id=body.tenant_id, scopes=body.scopes),
                            client_request_id=body.client_request_id)
        return engine.execute(req).model_dump(mode="json")

    @app.post("/v1/copilot/messages")
    def copilot(body: CopilotBody):
        return orch.run(body.tenant_id, body.user_id, body.utterance, sensitive=body.sensitive)

    @app.post("/v1/approvals/{approval_id}/approve")
    def approve(approval_id: str, body: ApproveBody):
        res = engine.approve(approval_id, ApprovalDecision(approver_id=body.approver_id,
                                                           decision=body.decision, reason=body.reason),
                             body.tenant_id)
        return res.model_dump(mode="json")

    @app.get("/v1/entities/{entity}")
    def entities(entity: str, tenant_id: str = "t_demo"):
        model = ENTITY_MODELS.get(entity)
        if model is None:
            raise HTTPException(404, f"unknown entity: {entity}")
        with sf() as s:
            rows = s.execute(select(model).where(model.tenant_id == tenant_id)).scalars().all()
            out = []
            for r in rows:
                d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
                out.append({k: (str(v) if hasattr(v, "isoformat") or v.__class__.__name__ == "Decimal" else v)
                            for k, v in d.items()})
            return out

    @app.get("/v1/audit/actions")
    def audit(tenant_id: str = "t_demo", trace_id: str | None = None, limit: int = 50):
        with sf() as s:
            stmt = select(db.AuditLog).where(db.AuditLog.tenant_id == tenant_id)
            if trace_id:
                stmt = stmt.where(db.AuditLog.trace_id == trace_id)
            rows = s.execute(stmt.order_by(db.AuditLog.created_at.desc()).limit(limit)).scalars().all()
            return [{"id": r.id, "action": r.action, "actor": f"{r.actor_type}:{r.actor_id}",
                     "status": r.status, "trace_id": r.trace_id,
                     "created_at": r.created_at.isoformat()} for r in rows]

    @app.get("/v1/agents/lineage/{trace_id}")
    def lineage(trace_id: str, tenant_id: str = "t_demo"):
        with sf() as s:
            row = s.execute(select(db.AgentActionLog).where(
                db.AgentActionLog.tenant_id == tenant_id,
                db.AgentActionLog.trace_id == trace_id)).scalars().first()
            if row is None:
                raise HTTPException(404, "lineage not found")
            return {"utterance": row.utterance, "plan": row.plan, "steps": row.steps,
                    "evidence": row.evidence, "model_id": row.model_id,
                    "prompt_version": row.prompt_version, "confidence": row.confidence,
                    "escalated": row.escalated, "cost_tokens": row.cost_tokens}

    @app.get("/v1/invariants")
    def invariants(tenant_id: str = "t_demo"):
        with sf() as s:
            violations = inv.check_all(s, tenant_id, persist=True)
        return {"status": "violation" if violations else "ok", "violations": violations}

    @app.get("/", response_class=HTMLResponse)
    def workbench():
        return HTMLResponse(WORKBENCH_HTML)

    return app


WORKBENCH_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>衡 ERP · M0 工作台</title>
<style>
body{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#222}
header{background:#1a2b4a;color:#fff;padding:14px 24px;font-size:18px}
main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 24px}
section{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h2{font-size:15px;margin:0 0 12px;color:#1a2b4a}
input,button{font-size:14px;padding:8px 10px;border-radius:6px;border:1px solid #ccc}
input{width:70%}button{background:#1a2b4a;color:#fff;border:none;cursor:pointer;margin-left:6px}
pre{background:#f0f2f5;padding:10px;border-radius:6px;font-size:12px;overflow:auto;max-height:260px}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left}
.card{border:1px solid #e0c060;background:#fffbe8;border-radius:6px;padding:10px;margin:8px 0;font-size:13px}
.tag{display:inline-block;background:#1a2b4a;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;margin-right:6px}
</style></head><body>
<header>衡 ERP · M0 双通道工作台 <span style="font-size:12px;opacity:.8">（AI 离线降级链已启用：规则模板兜底，工作台不依赖 LLM）</span></header>
<main>
<section><h2>通道 B · Copilot（一句话开单 / 问数）</h2>
<input id=utt value="给华东李总发200台A100，按上次的价格，含税"><button onclick=send()>发送</button>
<div id=reply></div><div id=cards></div></section>
<section><h2>通道 A · 单据与账（纯确定性，AI 全挂可用）</h2>
<button onclick=load()>刷新</button>
<h2 style="margin-top:12px">销售订单</h2><table id=orders></table>
<h2 style="margin-top:12px">库存现存</h2><table id=quants></table>
<h2 style="margin-top:12px">不变量断言</h2><pre id=inv></pre></section>
</main>
<script>
async function send(){
 const r=await fetch('/v1/copilot/messages',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({utterance:document.getElementById('utt').value})});
 const j=await r.json();
 document.getElementById('reply').innerHTML='<pre>'+j.reply+'\\n置信度:'+j.confidence+' 模型:'+j.model.model_id+' 升级:'+j.escalated+'</pre>';
 const cs=document.getElementById('cards');cs.innerHTML='';
 (j.cards||[]).forEach(c=>{
  const d=document.createElement('div');d.className='card';
  d.innerHTML='<span class=tag>'+c.risk+'</span><b>'+c.action+'</b> 审批单:'+c.approval_id+
   '<pre>价税合计:'+(c.data_preview.total_amount||'')+' 税额:'+(c.data_preview.total_tax||'')+
   '\\n证据:'+JSON.stringify(c.evidence)+'</pre>'+
   '<button onclick="approve(\\''+c.approval_id+'\\')">确认落库(L1)</button>';
  cs.appendChild(d);});
 load();}
async function approve(id){
 const r=await fetch('/v1/approvals/'+id+'/approve',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({approver_id:'u_boss',decision:'approve'})});
 const j=await r.json();alert('审批结果:'+j.status+(j.data?' 单据:'+JSON.stringify(j.data).slice(0,120):''));load();}
async function load(){
 const o=await (await fetch('/v1/entities/sales.order')).json();
 document.getElementById('orders').innerHTML='<tr><th>单号</th><th>客户</th><th>状态</th><th>价税合计</th><th>税额</th></tr>'+
  o.map(x=>'<tr><td>'+x.no+'</td><td>'+x.partner_id+'</td><td>'+x.state+'</td><td>'+x.total_amount+'</td><td>'+x.total_tax+'</td></tr>').join('');
 const q=await (await fetch('/v1/entities/stock.quant')).json();
 document.getElementById('quants').innerHTML='<tr><th>商品</th><th>仓</th><th>现存</th></tr>'+
  q.map(x=>'<tr><td>'+x.sku_id+'</td><td>'+x.warehouse_id+'</td><td>'+x.qty+'</td></tr>').join('');
 const i=await (await fetch('/v1/invariants')).json();
 document.getElementById('inv').textContent=JSON.stringify(i,null,1);}
load();
</script></body></html>"""


app = create_app()
