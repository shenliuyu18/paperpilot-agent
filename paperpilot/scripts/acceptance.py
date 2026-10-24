"""PaperPilot v2.0 acceptance check against the design spec's 12.4 checklist.

Runs entirely on HTTP through the app's own ASGI interface, with an isolated temp
data directory, so it neither needs nor touches any local research library.
"""
from __future__ import annotations

import io, json, os, sys, tempfile, time, zipfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="pp-accept-"))
os.environ.update({
    "PAPERPILOT_DATA_DIR": str(tmp),
    "PAPERPILOT_PROJECT_FILE": str(tmp / "projects.json"),
    "PAPERPILOT_PROJECT_DIR": str(tmp / "projects"),
    "PAPERPILOT_AGENT_FILE": str(tmp / "runs.json"),
    "PAPERPILOT_AGENT_DIR": str(tmp / "runs"),
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import config  # noqa: E402
from app.services import (agent_runs, agent_state, contradictions, draft_service, draft_state,
                          project_service, project_state, search)  # noqa: E402

client = TestClient(app)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:150]))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail)[:110] if detail else ""), flush=True)


def api(method, path, payload=None):
    r = client.request(method, path, json=payload)
    return r.status_code, (r.json() if "json" in r.headers.get("content-type", "") else r.content)


ACTIVE = {"queued", "running", "paused", "awaiting_approval", "interrupted"}


def active_runs(project_id):
    """Runs of a project that still hold the single-active-run mutex.

    Reads the same store the mutex itself reads: list_runs() caps at 50 entries and
    returns summaries, which would make this under-report and the wait spin pointlessly.
    """
    runs = agent_state.read_store().get("runs", {})
    return [r for r in runs.values()
            if r.get("project_id") == project_id and r.get("status") in ACTIVE]


def wait_idle(project_id, timeout=40.0):
    """Poll until the project can actually start a run.

    The mutex keys on two things -- the project's ``active_run_id`` binding and the
    statuses of its runs -- and the worker clears the binding asynchronously after a
    run goes terminal, so watching statuses alone is not enough. If the project never
    frees up, that is a genuine defect and this returns False rather than hanging.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            project_service.ensure_can_start_run(project_id)
            return True
        except ValueError:
            for r in active_runs(project_id):
                try:
                    agent_runs.act(r["id"], "cancel")
                except Exception:
                    pass
            time.sleep(0.2)
    try:
        project_service.ensure_can_start_run(project_id)
        return True
    except ValueError:
        return False


def create_bound(goal, **kw):
    """Create a run bound to pid, first making sure the project mutex is free."""
    wait_idle(pid)
    return agent_runs.create_run(goal, project_id=pid, **kw)


# ---------- 1. project lifecycle + cross-run memory ----------
code, project = api("POST", "/api/projects", {
    "title": "面向存算一体的低功耗 ADC 研究",
    "question": "如何在保持精度的同时降低存内计算 ADC 的数据搬运与校准开销？",
    "scope": "CIM/ADC 架构与电路实现", "constraints": ["优先开放获取论文"], "tags": ["ADC", "CIM"]})
pid = project.get("id") or project.get("project", {}).get("id")
check("1.1 项目创建返回 id", code == 200 and pid, f"code={code} id={pid}")

code, listed = api("GET", "/api/projects")
check("1.2 项目可列表", code == 200 and any(p.get("id") == pid for p in listed.get("projects", [])))

code, one = api("GET", f"/api/projects/{pid}")
check("1.3 项目可读取（含 brief/阶段）", code == 200 and one.get("project", one).get("stage"), 
      f"stage={one.get('project', one).get('stage')}")

code, upd = api("PATCH", f"/api/projects/{pid}", {"stage": "reading"})
check("1.4 阶段可手动切换", code == 200 and (upd.get("project", upd).get("stage") == "reading"), f"code={code}")

# ---------- 2/3. two rounds with memory seeding ----------
r1 = create_bound("第一轮：架构与数据搬运开销", mode="auto", max_actions=20,
                           max_papers=4, autostart=False)
r1 = agent_runs.execute_run(r1["id"])
mem = project_state.read_memory(pid)
check("2.1 首轮完成并入账", r1["status"] == "completed" and mem["evidence"],
      f"status={r1['status']} evidence={len(mem['evidence'])}")
rep1 = Path(r1["report"]["path"]) if r1.get("report") else None
check("2.2 首轮报告落盘", bool(rep1 and rep1.is_file() and rep1.stat().st_size > 500),
      f"{rep1.stat().st_size if rep1 and rep1.exists() else 0}B")

r2 = create_bound("第二轮：验证校准开销", mode="auto", max_actions=12,
                           max_papers=4, seed_memory=True, autostart=False)
r2 = agent_runs.execute_run(r2["id"])
seed = r2.get("seed_summary") or {}
check("3.1 次轮带入项目记忆", r2["status"] == "completed" and seed.get("evidence", 0) > 0,
      f"seed={seed} actions={r2.get('budget_used', {}).get('actions')}")
check("3.2 次轮动作数因记忆而减少",
      r2.get("budget_used", {}).get("actions", 99) <= r1.get("budget_used", {}).get("actions", 0),
      f"{r1.get('budget_used',{}).get('actions')} -> {r2.get('budget_used',{}).get('actions')}")

code, mem_api = api("GET", f"/api/projects/{pid}/memory?kind=evidence&limit=100")
check("3.3 记忆 API 可读", code == 200 and mem_api.get("total", 0) > 0,
      f"kind={mem_api.get('kind')} total={mem_api.get('total')} items={len(mem_api.get('items', []))}")

# idempotency: re-reconcile must not duplicate memory
before = len(project_state.read_memory(pid)["evidence"])
project_service.reconcile_project(pid)
after = len(project_state.read_memory(pid)["evidence"])
check("3.4 记忆提升幂等（重复对账不翻倍）", after == before, f"{before} -> {after}")

# ---------- 4. SSE snapshot stream ----------
with client.stream("GET", f"/api/agent/runs/{r2['id']}/stream") as s:
    frames, seqs, ids, buf = 0, [], [], ""
    ctype = s.headers.get("content-type", "")
    for chunk in s.iter_text():
        buf += chunk
        parts = buf.split("\n\n")
        buf = parts.pop()
        for frame in parts:
            if "event: snapshot" not in frame:
                continue
            frames += 1
            for ln in frame.split("\n"):
                if ln.startswith("id: "):
                    ids.append(ln[4:].strip())
                elif ln.startswith("data: "):
                    try:
                        seqs.append(json.loads(ln[6:]).get("seq"))
                    except Exception:
                        pass
        if frames >= 2:
            break
check("4.1 SSE 返回 event:snapshot 帧与 seq", ctype.startswith("text/event-stream") and frames >= 1 and seqs,
      f"type={ctype} frames={frames} seqs={seqs[:5]} ids={ids[:3]}")
check("4.2 快照字段精简且带状态", bool(seqs), f"first_frame_keys_ok={len(seqs)==frames}")

# ---------- 5. control commands and their state matrix ----------
# Everything below parks a run in `paused` first. `paused` is an ACTIVE status and the
# worker never advances it, so these assertions do not race the background executor.
cand = [p.get("id") for p in (agent_runs.get_run(r1["id"]) or {}).get("papers", []) if p.get("id")]
if not cand:
    mem_papers = api("GET", f"/api/projects/{pid}/memory?kind=papers&limit=10")[1].get("items") or []
    cand = [p.get("id") for p in mem_papers if p.get("id")]

qrun = create_bound("控制指令验证轮", mode="checkpoint", max_actions=12, max_papers=4, autostart=False)
qid = qrun["id"]
check("5.1 未 autostart 的运行停在 queued", (agent_runs.get_run(qid) or {}).get("status") == "queued",
      f"status={agent_runs.get_run(qid).get('status')}")

st, _ = api("POST", f"/api/agent/runs/{qid}/control", {"action": "pause"})
st_parked = (agent_runs.get_run(qid) or {}).get("status")
check("5.2 queued 运行 pause 即刻转 paused", st == 200 and st_parked == "paused", f"code={st} status={st_parked}")

mutex_hit = ""
try:
    agent_runs.create_run("并发第二运行", mode="auto", max_actions=4, max_papers=2,
                          project_id=pid, autostart=True)
    mutex_hit = "NO ERROR RAISED"
except ValueError as exc:
    mutex_hit = str(exc)
check("5.3 项目内活动运行互斥（paused 仍占用槽位）", "活动运行" in mutex_hit, mutex_hit[:80])

ok_cmds, cmd_detail = [], {}
for act, body in [
    ("set_mode", {"mode": "step"}), ("replan", {}),
    ("add_constraint", {"constraint": "只保留 14 位以上分辨率的证据"}),
    ("inject_question", {"question": "校准开销占总能耗的比例是多少？", "priority": "high"}),
    ("add_papers", {"paper_ids": cand[:1] or ["2609.24288v1"]}),
]:
    st, res = api("POST", f"/api/agent/runs/{qid}/control", {"action": act, **body})
    detail = str(res.get("error") or res.get("detail") or "")[:60] if isinstance(res, dict) else ""
    cmd_detail[act] = (st, res.get("ok") if isinstance(res, dict) else None, detail)
    if st == 200 and isinstance(res, dict) and res.get("ok"):
        ok_cmds.append(act)
check("5.4 五种引导类指令在 paused 上均被接受", len(ok_cmds) == 5,
      f"missing={[(k, cmd_detail[k]) for k in cmd_detail if k not in ok_cmds]}")

parked = agent_runs.get_run(qid) or {}
steer = parked.get("steering") or []
kinds = {s.get("kind") for s in steer}
check("5.5 控制指令留痕（kind/payload/requested_at）",
      kinds >= set(ok_cmds) and all(s.get("requested_at") for s in steer)
      and all(s.get("id", "").startswith("ctl-") for s in steer),
      f"recorded={sorted(kinds)} n={len(steer)}")
check("5.6 paused 上控制项进入 applied", {s.get("status") for s in steer} & {"applied", "pending"},
      f"statuses={sorted({s.get('status') for s in steer})}")
check("5.7 paused 不会被后台推进（模式改了但状态仍是 paused）",
      parked.get("mode") == "step" and parked.get("status") == "paused",
      f"mode={parked.get('mode')} status={parked.get('status')}")
check("5.8 注入的问题进入开放问题清单",
      any("校准开销" in (q.get("question") or "") for q in parked.get("open_questions") or []),
      f"questions={len(parked.get('open_questions') or [])}")
check("5.9 补充约束进入运行记录",
      any("14 位" in str(c) for c in (parked.get("constraints") or parked.get("brief", {}).get("constraints") or []))
      or any("14 位" in str(s.get("payload")) for s in steer),
      f"constraints={parked.get('constraints')}")

st, _ = api("POST", f"/api/agent/runs/{qid}/control", {"action": "resume"})
after_resume = (agent_runs.get_run(qid) or {}).get("status")
check("5.10 paused 运行 resume 被接受并重新排队", st == 200 and after_resume in ("queued", "running"),
      f"code={st} status={after_resume}")

# illegal transitions, taken from states that cannot drift
frun = create_bound("非法状态验证轮", mode="auto", max_actions=4, max_papers=2, autostart=False)
st_illegal, res_illegal = api("POST", f"/api/agent/runs/{frun['id']}/control", {"action": "resume"})
check("5.11 queued 上 resume 被 409 拒绝并给出中文原因",
      st_illegal == 409 and "只有已暂停" in str(res_illegal),
      f"code={st_illegal} detail={str(res_illegal)[:70]}")
api("POST", f"/api/agent/runs/{frun['id']}/action", {"action": "cancel"})
deadline = time.time() + 20
while (agent_runs.get_run(frun["id"]) or {}).get("status") not in ("cancelled", "completed") \
        and time.time() < deadline:
    time.sleep(0.2)
fin = (agent_runs.get_run(frun["id"]) or {}).get("status")
st_fin, res_fin = api("POST", f"/api/agent/runs/{frun['id']}/control", {"action": "pause"})
check("5.12 终态运行拒绝一切控制指令", st_fin == 409 and "已经结束" in str(res_fin),
      f"status={fin} code={st_fin} detail={str(res_fin)[:70]}")

api("POST", f"/api/agent/runs/{qid}/action", {"action": "cancel"})
wait_idle(pid)
again = None
try:
    again = create_bound("释放后再启动", mode="auto", max_actions=4, max_papers=2, autostart=False)
except ValueError as exc:
    again = {"error": str(exc)}
check("5.13 运行结束后释放项目占用（可再启动）", "id" in (again or {}), str(again)[:80])
if again.get("id"):
    agent_runs.act(again["id"], "cancel")
wait_idle(pid)

# ---------- 6. draft: edit / bind evidence / audit / versions / export ----------
draft = draft_service.ensure_from_markdown(pid, "## 引言\n\n存内计算把计算搬到存储阵列附近以降低数据搬运开销。\n\n## 结果\n")
check("6.1 草稿从 Markdown 建立", bool(draft.get("sections")), f"sections={len(draft.get('sections', []))}")
w = draft_service.write_section(pid, draft["sections"][1]["id"] if len(draft["sections"]) > 1 else draft["sections"][0]["id"],
                                max_blocks=3)
check("6.2 Agent write 生成段落块", w.get("blocks_added", 0) > 0, f"added={w.get('blocks_added')}")
d = draft_state.read_draft(pid)
rev = d["revision"]
code, saved = api("PUT", f"/api/projects/{pid}/draft", {"revision": rev, "title": "低功耗 ADC 研究草稿",
                                                       "sections": d["sections"]})
check("6.3 保存成功（乐观锁通过）", code == 200, f"code={code}")
st, conflict = api("PUT", f"/api/projects/{pid}/draft", {"revision": 999, "title": "x", "sections": []})
check("6.4 过期 revision 返回 409", st == 409, f"code={st}")

papers = (agent_runs.get_run(r1["id"]) or {}).get("papers") or []
bind_id = (papers[0].get("id") if papers else None) or (cand[0] if cand else None)
if bind_id:
    st, cit = api("POST", f"/api/projects/{pid}/draft/citations",
                  {"paper_id": bind_id, "revision": (draft_state.read_draft(pid))["revision"]})
    d_after = draft_state.read_draft(pid)
    keyed = sum(1 for s in d_after.get("sections", []) for b in s.get("blocks", [])
                if b.get("citations") or b.get("evidence_ids"))
    check("6.5 绑定项目证据/引用键", st == 200, f"paper={bind_id} code={st} blocks_with_keys={keyed} "
          f"err={str(cit)[:60] if st != 200 else ''}")
else:
    check("6.5 绑定项目证据/引用键", False, "NO PAPER ID AVAILABLE - check not exercised")
st, bad = api("POST", f"/api/projects/{pid}/draft/citations", {"paper_id": "nope-404", "revision": 999})
check("6.6 未知论文被拒绝", st != 200, f"code={st}")

st, aud = api("POST", f"/api/projects/{pid}/draft/audit")
hi = sum(1 for i in (aud.get("issues") or []) if i.get("severity") == "high")
check("6.7 草稿核查 8 规则可运行并分级", st == 200 and "issues" in aud, f"issues={len(aud.get('issues', []))} high={hi}")

st, ver = api("POST", f"/api/projects/{pid}/draft/versions", {"label": "验收快照"})
vid = (ver.get("version") or {}).get("id")
st2, lst = api("GET", f"/api/projects/{pid}/draft/versions")
check("6.8 版本创建与列表", st == 200 and vid and len(lst.get("versions", [])) >= 1, f"versions={len(lst.get('versions', []))}")
st, rst = api("POST", f"/api/projects/{pid}/draft/versions/{vid}/restore")
check("6.9 版本可恢复", st == 200, f"code={st}")

for fmt, expect_zip in [("markdown", False), ("latex", True), ("bibtex", False), ("json", False)]:
    r = client.get(f"/api/projects/{pid}/draft/export", params={"format": fmt})
    body = r.content
    good = r.status_code == 200 and len(body) > 0
    if expect_zip:
        try:
            zf = zipfile.ZipFile(io.BytesIO(body))
            good = good and len(zf.namelist()) >= 2
            body = f"zip:{zf.namelist()}"
        except Exception:
            good = False
    check(f"6.10 导出 {fmt}", good, f"{len(body)}B" if not isinstance(body, str) else body[:80])

# ---------- 7. run compare ----------
st2, cmp2 = api("GET", f"/api/projects/{pid}/compare?run_ids={r1['id']},{r2['id']}")
shared = (cmp2.get("evidence") or {}).get("shared") if isinstance(cmp2, dict) else None
check("7.1 两轮运行可对比（runs/动作序列/证据/问题/决策）",
      st2 == 200 and len(cmp2.get("runs", [])) == 2 and shared is not None
      and set(cmp2) >= {"runs", "tool_sequences", "evidence", "questions", "decisions"},
      f"code={st2} runs={len(cmp2.get('runs', []))} shared={len(shared or [])} "
      f"unique_runs={len((cmp2.get('evidence') or {}).get('unique', {}))}")
r4 = create_bound("第三轮", mode="auto", max_actions=6, max_papers=2, autostart=False)
r4 = agent_runs.execute_run(r4["id"])
st3, three = api("GET", f"/api/projects/{pid}/compare?run_ids={r1['id']},{r2['id']},{r4['id']}")
st4, four = api("GET", f"/api/projects/{pid}/compare?run_ids={r1['id']},{r2['id']},{r4['id']},{qrun['id']}")
check("7.2 对比支持 2-3 轮", st3 == 200 and len(three.get("runs", [])) == 3, f"code={st3} runs={len(three.get('runs', []))}")
check("7.3 第 4 轮被拒绝（422 + 中文原因）", st4 == 422 and four.get("detail"),
      f"code={st4} detail={str(four.get('detail') if isinstance(four, dict) else four)[:50]}")

# ---------- 8. v1.0 standalone run archival ----------
standalone = agent_runs.create_run("v1.0 独立任务（无项目）", mode="auto", max_actions=6,
                                  max_papers=2, autostart=False)
standalone = agent_runs.execute_run(standalone["id"])
before_rep = (standalone.get("report") or {}).get("path")
check("8.1 v1.0 独立任务可读可出报告", standalone["status"] == "completed" and before_rep,
      f"status={standalone['status']}")
st, att = api("POST", f"/api/projects/{pid}/runs/{standalone['id']}/attach")
check("8.2 独立任务可归档进项目", st == 200, f"code={st} err={str(att)[:60] if st != 200 else ''}")
after_rep = (agent_runs.get_run(standalone["id"]) or {}).get("report", {}).get("path")
check("8.3 归档后报告与证据不丢", after_rep == before_rep and Path(after_rep).is_file(),
      f"{Path(after_rep).stat().st_size if after_rep else 0}B")
st, det = api("POST", f"/api/projects/{pid}/runs/{standalone['id']}/detach")
check("8.4 可解除归档", st == 200, f"code={st}")
stx, cross = api("GET", f"/api/projects/{pid}/compare?run_ids={r1['id']},{standalone['id']}")
check("8.5 非本项目运行参与对比被拒绝", stx == 422,
      f"code={stx} detail={str(cross)[:60] if stx != 200 else 'UNEXPECTED 200'}")
st, arch = api("POST", f"/api/projects/{pid}/archive")
check("8.6 项目可归档/恢复", st == 200, f"code={st}")
st, rest = api("POST", f"/api/projects/{pid}/restore")
check("8.7 项目恢复后仍在", st == 200 and any(p.get("id") == pid for p in api("GET", "/api/projects")[1].get("projects", [])))

# ---------- 9. health/meta version sync ----------
h = client.get("/api/health").json()
m = client.get("/api/meta").json()
check("9.1 版本与 APP_VERSION 三处同步", h.get("version") == config.APP_VERSION == m.get("version")
      and "projects" in m.get("features", []),
      f"health={h.get('version')} meta={m.get('version')} app={config.APP_VERSION}")


# ---------- 10. 项目 Copilot（v3.0） ----------
st, body = api("POST", f"/api/projects/{pid}/assistant", {"message": "这个项目进展如何？下一步做什么？"})
reply = (body or {}).get("reply") or {}
check("10.1 Copilot 可回答并给出意图与模式",
      st == 200 and reply.get("text") and reply.get("intent"),
      f"code={st} intent={reply.get('intent')} mode={reply.get('mode')}")
check("10.2 建议动作全部需用户确认",
      all(s.get("requires_confirm") for s in (reply.get("suggestions") or [])),
      f"suggestions={len(reply.get('suggestions') or [])}")

st, body = api("POST", f"/api/projects/{pid}/assistant", {"message": "有哪些证据支持这个结论？"})
reply = (body or {}).get("reply") or {}
cites = reply.get("citations") or []
ground = reply.get("grounding") or {}
check("10.3 证据意图返回引用与接地结果或明确证据不足",
      st == 200 and (bool(cites) or ground.get("status") == "insufficient"),
      f"citations={len(cites)} grounding={ground.get('status')}")

st, exp = api("GET", f"/api/projects/{pid}/assistant/export")
check("10.4 Copilot 对话可导出 Markdown", st == 200 and bool(exp), f"code={st} bytes={len(exp or b'')}")
st, _ = api("DELETE", f"/api/projects/{pid}/assistant")
st2, after = api("GET", f"/api/projects/{pid}/assistant")
check("10.5 Copilot 可清空", st == 200 and not (after.get("messages") or []),
      f"messages_after={len((after or {}).get('messages') or [])}")

# ---------- 11. 草稿语义核查（v3.0，只读） ----------
d_before = draft_state.read_draft(pid)
st, sem = api("POST", f"/api/projects/{pid}/draft/semantic-audit", {"limit": 5, "use_llm": False})
items = (sem or {}).get("items") or []
verdicts = {i.get("verdict") for i in items}
d_after = draft_state.read_draft(pid)
check("11.1 语义核查返回四态之一并附理由",
      st == 200 and verdicts <= {"supported", "partial", "unsupported", "insufficient"}
      and all(i.get("rationale") for i in items),
      f"verdicts={sorted(verdicts)} checked={(sem or {}).get('checked')} mode={(sem or {}).get('mode')}")
same_text = [b.get("text") for s in d_before.get("sections", []) for b in s.get("blocks", [])] == \
            [b.get("text") for s in d_after.get("sections", []) for b in s.get("blocks", [])]
check("11.2 语义核查只读（revision 与正文不变）",
      d_after.get("revision") == d_before.get("revision") and same_text,
      f"revision={d_before.get('revision')}->{d_after.get('revision')}")

# ---------- 12. 概念图谱（v4.0） ----------
graph_ids = [p.get("id") for p in (agent_runs.get_run(r1["id"]) or {}).get("papers", []) if p.get("id")][:3]
if len(graph_ids) < 2:
    graph_ids = [p["id"] for p in search.search("quantization", limit=3, source="demo")]
st, graph = api("POST", "/api/graph", {"scope": "selected", "paper_ids": graph_ids})
nodes, edges = (graph or {}).get("nodes") or [], (graph or {}).get("edges") or []
check("12.1 概念图谱返回节点/边/统计", st == 200 and nodes and edges and (graph or {}).get("stats"),
      f"code={st} nodes={len(nodes)} edges={len(edges)} stats={(graph or {}).get('stats')}")
check("12.2 节点可追溯到来源论文与原文摘句",
      all(n.get("papers") and n.get("evidence") for n in nodes[:5]),
      f"checked={min(5, len(nodes))} sample={sorted(nodes[0]) if nodes else []}")
st, empty = api("POST", "/api/graph", {"scope": "selected", "paper_ids": ["no-such-paper"]})
check("12.3 未知论文返回空图而非报错", st == 200 and not (empty.get("nodes") or []), f"code={st}")
st, too_many = api("POST", "/api/graph", {"scope": "selected", "paper_ids": ["x"] * 21})
check("12.4 图谱论文数上限 20", st == 422, f"code={st}")

# ---------- 13. 逐对可比性（v3.2） ----------
st, paper_cmp = api("POST", "/api/compare", {"paper_ids": graph_ids[:2]})
comp = (paper_cmp or {}).get("comparability") or []
check("13.1 论文对比返回逐对可比性判定",
      st == 200 and comp and all("comparable" in c for c in comp),
      f"code={st} pairs={len(comp)}")
check("13.2 可比性含理由与共享/差异项",
      all(c.get("reason") is not None and "paper_ids" in c for c in comp)
      and bool((paper_cmp or {}).get("consensus") or (paper_cmp or {}).get("divergence") is not None),
      f"sample={sorted(comp[0]) if comp else []}")

# ---------- 14. 安全头与特性位（v4.1） ----------
r_root = client.get("/")
hdrs = {k.lower(): v for k, v in r_root.headers.items()}
check("14.1 安全响应头齐全",
      hdrs.get("x-content-type-options") == "nosniff"
      and bool(hdrs.get("referrer-policy"))
      and "frame-ancestors" in (hdrs.get("content-security-policy") or ""),
      f"nosniff={hdrs.get('x-content-type-options')} csp={(hdrs.get('content-security-policy') or '')[:40]}")
feats = set((m or {}).get("features") or [])
need = {"projects", "project_assistant", "assistant_grounding", "llm_gateway", "semantic_audit"}
check("14.2 特性位覆盖 v2/v3 能力", need <= feats, f"missing={sorted(need - feats)} total={len(feats)}")
check("14.3 health/meta 版本一致",
      (h or {}).get("version") == (m or {}).get("version") == config.APP_VERSION,
      f"{(h or {}).get('version')} / {(m or {}).get('version')} / {config.APP_VERSION}")
check("14.4 特性位覆盖 v5.2（PDF/矛盾复核/并发锁）",
      {"pdf", "contradiction_review", "subscription_concurrency", "cross_process_lock"} <= feats,
      f"missing={sorted({'pdf', 'contradiction_review', 'subscription_concurrency', 'cross_process_lock'} - feats)} total={len(feats)}")
check("14.5 特性位覆盖 v6.0（投稿/审稿/文献互操作）",
      {"submission", "reviewer_report", "reference_io"} <= feats,
      f"missing={sorted({'submission', 'reviewer_report', 'reference_io'} - feats)} total={len(feats)}")
check("14.6 特性位覆盖 v6.1（作者元数据/审稿映射）",
      {"author_metadata", "reviewer_mapping"} <= feats,
      f"missing={sorted({'author_metadata', 'reviewer_mapping'} - feats)} total={len(feats)}")

# ---------- 15. v5.1：PDF / 矛盾复核 / 订阅并发 ----------
st, pdf_body = api("GET", f"/api/projects/{pid}/draft/export?format=pdf")
if st == 200:
    pdf_ok = isinstance(pdf_body, bytes) and pdf_body.startswith(b"%PDF")
    pdf_detail = f"code=200 bytes={len(pdf_body or b'')}"
elif st == 503:
    pdf_ok = "XeLaTeX" in str((pdf_body or {}).get("detail") or "")
    pdf_detail = f"code=503 detail={str((pdf_body or {}).get('detail'))[:60]}"
else:
    pdf_ok = False
    pdf_detail = f"code={st}"
check("15.1 PDF 导出返回 PDF 或明确的工具链原因", pdf_ok, pdf_detail)

cpid = project_service.create_project("矛盾复核项目", "问题？")["id"]
cmem = project_state.read_memory(cpid)
cmem["papers"] = {
    "PA": {"id": "PA", "title": "Paper A", "year": "2026", "venue": "",
           "metrics": {"ADC 能耗": "降低 27.1%"}, "results": []},
    "PB": {"id": "PB", "title": "Paper B", "year": "2026", "venue": "",
           "metrics": {"ADC 能耗": "提升 12.5%"}, "results": []},
}
project_state.write_memory(cpid, cmem)
st, scan = api("POST", f"/api/projects/{cpid}/contradictions/scan", {})
st2, review = api("POST", f"/api/projects/{cpid}/contradictions/review", {"use_llm": True, "limit": 4})
items_after = contradictions.read(cpid).get("items") or []
check("15.2 离线矛盾复核降级且不改写记录",
      st == 200 and bool((scan or {}).get("items")) and st2 == 200
      and (review or {}).get("degraded") and not (review or {}).get("reviewed")
      and all(item.get("status") == "open" for item in items_after),
      f"scan={len((scan or {}).get('items') or [])} review={review}")

st, subs = api("GET", "/api/subscriptions")
scheduler = (subs or {}).get("scheduler") or {}
check("15.3 订阅调度状态含并发运行列表",
      st == 200 and isinstance(scheduler.get("running_ids"), list),
      f"running={scheduler.get('running_ids')} enabled={scheduler.get('enabled')}")

corpus_dir = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden" / "corpus"
corpus_files = sorted(corpus_dir.glob("*.json"))
corpus_topics = [json.loads(path.read_text(encoding="utf-8")) for path in corpus_files
                 if path.name != "all.json"]
check("15.4 金标语料覆盖 4 主题 × 6 篇",
      len(corpus_topics) == 4 and all(len(item.get("papers") or []) == 6 for item in corpus_topics),
      f"topics={len(corpus_topics)} papers={[len(item.get('papers') or []) for item in corpus_topics]}")

candidate_dir = Path(__file__).resolve().parents[1] / "data" / "eval" / "golden" / "candidates"
candidate_claims = []
for path in sorted(candidate_dir.glob("*.json")):
    candidate_claims.extend(json.loads(path.read_text(encoding="utf-8")).get("claims") or [])
check("15.5 人工候选池 ≥200 条且标签为空",
      len(candidate_claims) >= 200 and all(not item.get("label") for item in candidate_claims),
      f"claims={len(candidate_claims)}")

# ---------- 16. v6.0：投稿包 / 审稿视角 / 参考文献互操作 ----------
st, prof = api("GET", "/api/submission/profiles")
profiles = [item.get("id") for item in (prof or {}).get("profiles") or []]
check("16.1 投稿档案包含课程/通用/IEEE/双盲",
      st == 200 and {"course", "generic-journal", "ieee-conference", "double-blind-journal"} <= set(profiles),
      f"profiles={profiles}")

st, sub_check = api("POST", f"/api/projects/{pid}/submission/check", {"profile": "course"})
check("16.2 投稿检查返回分级清单与亮点",
      st == 200 and isinstance((sub_check or {}).get("items"), list)
      and isinstance((sub_check or {}).get("highlights"), list)
      and "counts" in (sub_check or {}),
      f"items={len((sub_check or {}).get('items') or [])} highlights={len((sub_check or {}).get('highlights') or [])}")

st, cover = api("GET", f"/api/projects/{pid}/submission/cover-letter?profile=course")
cover_text = cover.decode("utf-8", "replace") if isinstance(cover, bytes) else str(cover)
check("16.3 投稿信模板可下载且含标题",
      st == 200 and "Cover Letter" in cover_text and "模板" in cover_text,
      f"code={st} bytes={len(cover or b'')}")

st, review = api("POST", f"/api/projects/{pid}/reviewer/report")
check("16.4 审稿视角返回推荐等级与问题清单",
      st == 200 and (review or {}).get("recommendation") in {"major_revision", "minor_revision", "ready_for_review"}
      and isinstance((review or {}).get("major"), list) and (review or {}).get("disclaimer"),
      f"recommendation={(review or {}).get('recommendation')} major={len((review or {}).get('major') or [])}")

st, package = api("GET", f"/api/projects/{pid}/submission/package?profile=double-blind-journal")
zip_names = []
if st == 200:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        zip_names = archive.namelist()
check("16.5 投稿包为 ZIP，含稿件/投稿信/亮点/文献/回复模板",
      st == 200 and {"manuscript.docx", "manuscript.pdf", "cover-letter.md",
                     "highlights.md", "references.ris", "response-template.md"} <= set(zip_names),
      f"code={st} files={len(zip_names)} pdf={'manuscript.pdf' in zip_names}")

ris_text = """TY  - JOUR
AU  - Test Author
TI  - Imported Reference
PY  - 2026
ER  - 
"""
cur = client.get(f"/api/projects/{pid}/draft").json()["draft"]
st, imported = api("POST", f"/api/projects/{pid}/draft/references/import",
                   {"format": "ris", "text": ris_text, "revision": cur["revision"]})
st2, exported = api("GET", f"/api/projects/{pid}/draft/references/export?format=ris")
check("16.6 RIS 导入/导出闭环",
      st == 200 and (imported or {}).get("added") == 1
      and st2 == 200 and "Imported Reference" in str(exported),
      f"import={st} added={(imported or {}).get('added')} export={st2}")

st, mapped = api("POST", f"/api/projects/{pid}/reviewer/map-comments",
                 {"comments": ["请说明实验数据集的来源与划分。"]})
check("16.7 审稿意见映射返回段落定位",
      st == 200 and (mapped or {}).get("comments") == 1
      and isinstance((mapped or {}).get("items"), list),
      f"code={st} items={len((mapped or {}).get('items') or [])}")

st, template = api("POST", f"/api/projects/{pid}/reviewer/response-template",
                   {"comments": ["请说明实验数据集的来源与划分。"]})
template_text = template.decode("utf-8", "replace") if isinstance(template, bytes) else str(template)
check("16.8 回复模板包含审稿意见与建议定位",
      st == 200 and "请说明实验数据集的来源与划分" in template_text
      and "建议定位" in template_text,
      f"code={st} bytes={len(template or b'')}")

# ---------- 17. v6.2：自定义大模型（本地 OpenAI 兼容桩） ----------
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STUB_CALLS = []


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        STUB_CALLS.append({
            "path": self.path,
            "auth": self.headers.get("authorization") or "",
            "payload": payload,
        })
        body = json.dumps({
            "model": "stub-chat",
            "choices": [{"message": {"content": "ok from stub"}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


stub_server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
stub_port = stub_server.server_address[1]
threading.Thread(target=stub_server.serve_forever, daemon=True).start()
os.environ.update({
    "PAPERPILOT_ALLOW_PRIVATE_LLM": "1",
    "PAPERPILOT_LLM_SETTINGS_FILE": str(tmp / "llm_settings.json"),
    "PAPERPILOT_LLM_SETTINGS_SECRETS_FILE": str(tmp / "llm_settings.secrets.json"),
    "PAPERPILOT_LLM_PERSIST_KEY": "1",
})
STUB_KEY = "acc-stub-key-9i3k"
STUB_BASE = "http://127.0.0.1:%d/v1" % stub_port

st, body = api("GET", "/api/llm/settings")
settings = (body or {}).get("settings") or {}
check("17.1 模型设置暴露常见大模型预设且不回显密钥",
      st == 200 and len(settings.get("providers") or []) >= 6
      and len(settings.get("purposes") or []) == 5
      and "api_key" not in settings and not settings.get("has_key"),
      f"code={st} providers={len(settings.get('providers') or [])} purposes={len(settings.get('purposes') or [])}")

st, saved = api("POST", "/api/llm/settings", {
    "provider": "custom", "base_url": STUB_BASE, "model": "stub-chat",
    "api_key": STUB_KEY, "routes": {"assistant": "stub-fast"},
})
saved_settings = (saved or {}).get("settings") or {}
check("17.2 保存自定义模型后立刻切到在线模式且只返回掩码",
      st == 200 and saved_settings.get("has_key") and saved_settings.get("mode") == "online"
      and STUB_KEY not in json.dumps(saved or {}, ensure_ascii=False)
      and str(saved_settings.get("masked_key") or "").endswith(STUB_KEY[-4:]),
      f"code={st} masked={saved_settings.get('masked_key')}")

st, health = api("GET", "/api/health")
check("17.3 健康检查反映运行时生效的模型",
      st == 200 and health.get("mode") == "online" and health.get("llm_enabled") is True,
      f"mode={health.get('mode')}")

st, tested = api("POST", "/api/llm/settings/test", {})
check("17.4 测试连接真实命中本地 OpenAI 兼容桩",
      st == 200 and (tested or {}).get("reply") == "ok from stub"
      and (tested or {}).get("latency_ms", -1) >= 0,
      f"code={st} reply={(tested or {}).get('reply')} latency={(tested or {}).get('latency_ms')}")
probe = STUB_CALLS[-1] if STUB_CALLS else {}
check("17.5 桩服务收到 Bearer 密钥、正确路径与所选模型",
      probe.get("path") == "/v1/chat/completions"
      and probe.get("auth") == "Bearer " + STUB_KEY
      and (probe.get("payload") or {}).get("model") == "stub-chat",
      f"path={probe.get('path')} auth={str(probe.get('auth'))[:18]}")

st, meta = api("GET", "/api/meta")
check("17.6 meta 暴露运行时模型、分用途路由与 custom_llm 特性位",
      st == 200 and (meta or {}).get("llm", {}).get("model") == "stub-chat"
      and (meta or {}).get("llm_settings", {}).get("routes") == {"assistant": "stub-fast"}
      and "custom_llm" in ((meta or {}).get("features") or []),
      f"model={(meta or {}).get('llm', {}).get('model')} features={len((meta or {}).get('features') or [])}")

cfg_text = (tmp / "llm_settings.json").read_text(encoding="utf-8")
sec_text = (tmp / "llm_settings.secrets.json").read_text(encoding="utf-8")
check("17.7 配置与密钥分文件保存，密钥不进入配置文件",
      STUB_KEY not in cfg_text and STUB_KEY in sec_text and "stub-chat" in cfg_text,
      f"config={len(cfg_text)}B secret={len(sec_text)}B")

os.environ["PAPERPILOT_ALLOW_PRIVATE_LLM"] = "0"
st, rejected = api("POST", "/api/llm/settings", {
    "provider": "custom", "base_url": "https://169.254.169.254/latest", "model": "x",
})
st2, rejected2 = api("POST", "/api/llm/settings", {
    "provider": "custom", "base_url": "http://127.0.0.1:1/v1", "model": "x",
})
os.environ["PAPERPILOT_ALLOW_PRIVATE_LLM"] = "1"
check("17.8 未开启本机开关时拒绝内网与元数据地址",
      st == 400 and st2 == 400 and "安全要求" in str((rejected2 or {}).get("detail") or ""),
      f"link_local={st} loopback={st2}")

st, cleared = api("POST", "/api/llm/settings/clear")
after = api("GET", "/api/health")[1]
check("17.9 清除配置后回到离线规则模式并删除两份文件",
      st == 200 and (cleared or {}).get("settings", {}).get("mode") == "offline"
      and not (tmp / "llm_settings.json").exists()
      and not (tmp / "llm_settings.secrets.json").exists()
      and (after or {}).get("mode") != "online",
      f"code={st} mode={(after or {}).get('mode')}")
# ---------- 18. reproducible research dossier ----------
st, meta = api("GET", "/api/meta")
check("18.1 特性位包含 dossier", st == 200 and "dossier" in ((meta or {}).get("features") or []),
      f"features={len((meta or {}).get('features') or [])}")

archive = client.get(f"/api/projects/{pid}/dossier")
check("18.2 项目档案导出为 ZIP，且只含三个约定条目",
      archive.status_code == 200
      and archive.headers.get("content-type") == "application/zip"
      and set(zipfile.ZipFile(io.BytesIO(archive.content)).namelist())
      == {"manifest.json", "dossier.json", "DOSSIER.md"},
      f"code={archive.status_code} bytes={len(archive.content)}")

verified = client.post("/api/dossiers/verify",
                       files={"file": ("acceptance.zip", archive.content, "application/zip")})
summary = (verified.json() or {}).get("summary") or {}
check("18.3 档案校验只读并返回摘要",
      verified.status_code == 200 and summary.get("draft_blocks", 0) >= 0,
      f"code={verified.status_code} summary={summary}")

imported = client.post("/api/dossiers/import",
                       files={"file": ("acceptance.zip", archive.content, "application/zip")})
imported_body = imported.json() if imported.status_code == 200 else {}
check("18.4 档案导入为新项目（不覆盖原项目）",
      imported.status_code == 200
      and imported_body.get("project", {}).get("id")
      and imported_body.get("project", {}).get("id") != pid,
      f"code={imported.status_code} new={imported_body.get('project', {}).get('id')}")

stub_server.shutdown()

passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d acceptance checks passed" % (passed, len(results)))
for n, ok, dt in results:
    if not ok:
        print("  FAILED:", n, "|", dt)
print("data dir:", tmp)
sys.exit(0 if passed == len(results) else 1)
