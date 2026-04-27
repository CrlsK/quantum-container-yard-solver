"""
Container Yard — Additional Output file generator (quantum SQA solver).

Writes input/output visualization artifacts into ./additional_output/ so the
QCentroid platform exposes them as downloadable files in the Additional Output tab.

Files produced:
  00_input_summary.json            Structured snapshot of input volumes
  01_input_overview.png            Vessel sizes + container weight distribution
  02_block_heatmap.png             Top-down per-block stack-height heatmap (output)
  03_vessel_timeline.png           Reshuffles per vessel in departure order (output)
  04_convergence.png               SQA best-objective trajectory across sweeps
  05_quantum_field_evolution.png   Transverse field (gamma) + temperature decay + tunneling
  06_kpi_dashboard.json            KPI dashboard (output)
  07_stacking_plan.csv             Per-container placement table (output)
  08_quantum_metrics.json          Detailed quantum metrics (Trotter, sweeps, tunnels)
  09_report.html                   Self-contained static report (PNGs embedded)
  10_interactive_dashboard.html    Interactive Plotly explorer (hover/zoom/filter)
"""
import os
import json
import csv
import base64

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_OK = True
except Exception:
    _MPL_OK = False


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _save_png(fig, path):
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def _png_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _write_input_summary(out_dir, containers, yard_layout):
    blocks = yard_layout.get("blocks", [])
    total_capacity = sum(b.get("total_capacity", 0) for b in blocks)
    weights = [c.get("weight_tonnes", 0) for c in containers]
    vessels = {}
    for c in containers:
        vid = c.get("vessel_id", "UNKNOWN")
        vessels.setdefault(vid, {"count": 0, "weight": 0.0, "departure_order": c.get("vessel_departure_order", 0)})
        vessels[vid]["count"] += 1
        vessels[vid]["weight"] += c.get("weight_tonnes", 0)
    summary = {
        "total_containers": len(containers),
        "total_blocks": len(blocks),
        "total_capacity": total_capacity,
        "expected_utilization_pct": round(100.0 * len(containers) / max(total_capacity, 1), 1),
        "weight_tonnes": {
            "min": round(min(weights), 2) if weights else 0,
            "max": round(max(weights), 2) if weights else 0,
            "mean": round(sum(weights) / len(weights), 2) if weights else 0,
            "total": round(sum(weights), 2),
        },
        "vessels": [
            {"vessel_id": vid, "departure_order": v["departure_order"], "containers": v["count"], "total_weight_tonnes": round(v["weight"], 2)}
            for vid, v in sorted(vessels.items(), key=lambda x: x[1]["departure_order"])
        ],
        "blocks": [
            {"block_id": b.get("block_id"), "rows": b.get("rows"), "bays_per_row": b.get("bays_per_row"),
             "max_tier_height": b.get("max_tier_height"), "total_capacity": b.get("total_capacity")}
            for b in blocks
        ],
    }
    path = os.path.join(out_dir, "00_input_summary.json")
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    return path


def _plot_input_overview(out_dir, containers):
    if not _MPL_OK:
        return None
    vessels = {}
    for c in containers:
        vid = c.get("vessel_id", "UNK")
        vessels.setdefault(vid, {"order": c.get("vessel_departure_order", 0), "count": 0, "weight": 0.0})
        vessels[vid]["count"] += 1
        vessels[vid]["weight"] += c.get("weight_tonnes", 0)
    items = sorted(vessels.items(), key=lambda x: x[1]["order"])
    labels = [f"{vid}\n(dep {v['order']})" for vid, v in items]
    counts = [v["count"] for _, v in items]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].bar(labels, counts, color="#7c3aed")
    axes[0].set_title("Containers per vessel (input)")
    axes[0].set_ylabel("# containers")
    axes[0].tick_params(axis="x", rotation=30)
    w = [c.get("weight_tonnes", 0) for c in containers]
    axes[1].hist(w, bins=12, color="#06b6d4", edgecolor="white")
    axes[1].set_title(f"Container weight distribution (n={len(w)})")
    axes[1].set_xlabel("weight (tonnes)")
    axes[1].set_ylabel("# containers")
    fig.suptitle("Input overview", fontsize=12, fontweight="bold")
    path = os.path.join(out_dir, "01_input_overview.png")
    _save_png(fig, path)
    return path


def _plot_block_heatmap(out_dir, block_heatmap):
    if not _MPL_OK or not block_heatmap:
        return None
    blocks = list(block_heatmap.values())
    n = len(blocks)
    cols = min(2, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 4.2 * rows), squeeze=False)
    for i, blk in enumerate(blocks):
        ax = axes[i // cols][i % cols]
        dim = blk["dimensions"]; R, B, MT = dim["rows"], dim["bays"], dim["max_tier"]
        grid = [[blk["grid"][r][b]["height"] for b in range(B)] for r in range(R)]
        im = ax.imshow(grid, cmap="Purples", vmin=0, vmax=MT, aspect="auto")
        for r in range(R):
            for b in range(B):
                cell = blk["grid"][r][b]; h = cell["height"]
                if h > 0:
                    ax.text(b, r, str(h), ha="center", va="center",
                            color="white" if h > MT / 2 else "black", fontsize=8, fontweight="bold")
        ax.set_title(f"{blk['block_id']} — {blk['total_containers']}/{blk['capacity']} ({blk['utilization_pct']}%)")
        ax.set_xlabel("bay"); ax.set_ylabel("row")
        fig.colorbar(im, ax=ax, label="stack height (tier)")
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Yard block heatmap (output) — number = containers stacked at that bay", fontsize=12, fontweight="bold")
    path = os.path.join(out_dir, "02_block_heatmap.png")
    _save_png(fig, path)
    return path


def _plot_vessel_timeline(out_dir, vessel_timeline):
    if not _MPL_OK or not vessel_timeline:
        return None
    labels = [f"{v['vessel_id']}\n(dep {v['departure_order']})" for v in vessel_timeline]
    reshuffles = [v["reshuffles"] for v in vessel_timeline]
    cumulative = [v["cumulative_reshuffles"] for v in vessel_timeline]
    eff = [v["retrieval_efficiency_pct"] for v in vessel_timeline]
    colors = ["#22c55e" if v["status"] == "clean" else ("#f59e0b" if v["status"] == "minor" else "#ef4444") for v in vessel_timeline]
    fig, ax1 = plt.subplots(figsize=(11, 4.5))
    ax1.bar(labels, reshuffles, color=colors)
    ax1.set_ylabel("reshuffles per vessel"); ax1.tick_params(axis="x", rotation=30)
    ax2 = ax1.twinx()
    ax2.plot(labels, cumulative, color="#7c3aed", marker="o", linewidth=2, label="cumulative")
    ax2.plot(labels, eff, color="#0891b2", marker="s", linestyle="--", linewidth=1.5, label="retrieval eff (%)")
    ax2.set_ylabel("cumulative reshuffles  /  retrieval efficiency (%)")
    fig.suptitle("Vessel timeline (output) — reshuffles in departure order", fontsize=12, fontweight="bold")
    h2, l2 = ax2.get_legend_handles_labels()
    ax2.legend(h2, l2, loc="upper left", fontsize=8)
    path = os.path.join(out_dir, "03_vessel_timeline.png")
    _save_png(fig, path)
    return path


def _plot_convergence(out_dir, convergence_history):
    if not _MPL_OK or not convergence_history:
        return None
    s = [p["sweep"] for p in convergence_history]
    best = [p["best"] for p in convergence_history]
    gamma = [p["gamma"] for p in convergence_history]
    temp = [p["temp"] for p in convergence_history]
    fig, ax1 = plt.subplots(figsize=(11, 4.5))
    ax1.plot(s, best, color="#7c3aed", linewidth=2, marker="o", markersize=4, label="best objective")
    ax1.set_xlabel("sweep"); ax1.set_ylabel("objective (lower is better)")
    ax2 = ax1.twinx()
    ax2.plot(s, gamma, color="#dc2626", linewidth=1.5, linestyle="--", label="γ (transverse field)")
    ax2.plot(s, temp, color="#0891b2", linewidth=1.5, linestyle=":", label="temperature")
    ax2.set_ylabel("γ  /  temperature")
    fig.suptitle("SQA convergence (output)", fontsize=12, fontweight="bold")
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    path = os.path.join(out_dir, "04_convergence.png")
    _save_png(fig, path)
    return path


def _plot_field_evolution(out_dir, field_history):
    if not _MPL_OK or not field_history:
        return None
    s = [p["sweep"] for p in field_history]
    g = [p["gamma"] for p in field_history]
    t = [p["temperature"] for p in field_history]
    tun = [p["tunnel_events_in_sweep"] for p in field_history]
    cum = [p["cumulative_tunnels"] for p in field_history]
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True)
    ax = axes[0]
    ax.plot(s, g, color="#dc2626", linewidth=2, label="γ (transverse field)")
    ax.plot(s, t, color="#0891b2", linewidth=2, label="temperature")
    ax.set_ylabel("magnitude (log)"); ax.set_yscale("log"); ax.legend(loc="upper right")
    ax.set_title("Quantum-to-classical transition")
    ax = axes[1]
    ax.bar(s, tun, color="#7c3aed", alpha=0.8, label="tunnel events / sweep")
    ax2 = ax.twinx()
    ax2.plot(s, cum, color="#1e3a8a", linewidth=2, marker="o", markersize=3, label="cumulative tunnels")
    ax.set_xlabel("sweep"); ax.set_ylabel("tunnels per sweep")
    ax2.set_ylabel("cumulative tunnels")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax.set_title("Tunneling activity (quantum advantage indicator)")
    fig.suptitle("Quantum field evolution (output)", fontsize=12, fontweight="bold")
    path = os.path.join(out_dir, "05_quantum_field_evolution.png")
    _save_png(fig, path)
    return path


def _write_kpi_json(out_dir, kpi_dashboard):
    path = os.path.join(out_dir, "06_kpi_dashboard.json")
    with open(path, "w") as f:
        json.dump(kpi_dashboard, f, indent=2)
    return path


def _write_stacking_csv(out_dir, stacking_plan, containers):
    cm = {c["id"]: c for c in containers}
    path = os.path.join(out_dir, "07_stacking_plan.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["container_id", "vessel_id", "vessel_departure_order", "weight_tonnes",
                    "assigned_block", "assigned_row", "assigned_bay", "tier_level", "reshuffles_if_retrieved_now"])
        for a in stacking_plan:
            c = cm.get(a["id"], {})
            w.writerow([a["id"], c.get("vessel_id", ""), c.get("vessel_departure_order", ""),
                        c.get("weight_tonnes", ""), a["assigned_block"], a["assigned_row"],
                        a["assigned_bay"], a["tier_level"], a.get("reshuffles_if_retrieved_now", 0)])
    return path


def _write_quantum_metrics(out_dir, quantum_metrics):
    path = os.path.join(out_dir, "08_quantum_metrics.json")
    with open(path, "w") as f:
        json.dump(quantum_metrics, f, indent=2)
    return path


def _write_html_report(out_dir, kpi, png_paths, narrative):
    embedded = []
    for label, p in png_paths:
        if not p:
            continue
        embedded.append(f"<h3>{label}</h3><img src='data:image/png;base64,{_png_b64(p)}' style='max-width:100%;border:1px solid #e2e8f0;border-radius:6px'/>")
    kpi_rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in kpi.items())
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Container Yard — Quantum SQA Solver Report</title>
<style>
  body{{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#0f172a}}
  h1{{border-bottom:2px solid #7c3aed;padding-bottom:6px}}
  h3{{margin-top:28px;color:#5b21b6}}
  table{{border-collapse:collapse;width:100%;margin:12px 0}}
  td{{border:1px solid #e2e8f0;padding:6px 10px;font-size:13px}}
  td:first-child{{background:#faf5ff;width:280px}}
  .narrative{{background:#faf5ff;border-left:4px solid #7c3aed;padding:12px 14px;border-radius:4px}}
</style></head><body>
<h1>Container Yard Stacking — Quantum SQA Solver Report</h1>
<div class='narrative'>{narrative}</div>
<h3>KPI Dashboard</h3><table>{kpi_rows}</table>
{''.join(embedded)}
</body></html>"""
    path = os.path.join(out_dir, "09_report.html")
    with open(path, "w") as f:
        f.write(html)
    return path


_INTERACTIVE_HTML_TEMPLATE = r"""<!doctype html>
<html><head><meta charset='utf-8'>
<title>Container Yard — Interactive Dashboard (Quantum SQA)</title>
<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>
<style>
  body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;padding:20px;background:#faf5ff;color:#0f172a}
  h1{margin:0 0 4px;color:#5b21b6}
  .sub{color:#475569;margin-bottom:18px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:22px}
  .kpi{background:#fff;border:1px solid #e9d5ff;border-radius:8px;padding:14px}
  .kpi .label{font-size:11px;text-transform:uppercase;color:#64748b;letter-spacing:.05em}
  .kpi .value{font-size:22px;font-weight:600;color:#5b21b6;margin-top:4px}
  .tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
  .tab{padding:8px 14px;background:#fff;border:1px solid #e9d5ff;border-radius:6px;cursor:pointer;font-size:13px}
  .tab.active{background:#7c3aed;color:#fff;border-color:#7c3aed}
  .panel{background:#fff;border:1px solid #e9d5ff;border-radius:8px;padding:16px;display:none}
  .panel.active{display:block}
  .narrative{background:#fff;border-left:4px solid #7c3aed;padding:12px 14px;border-radius:4px;margin-bottom:18px}
  table{border-collapse:collapse;width:100%;font-size:12px}
  th,td{border-bottom:1px solid #f1f5f9;padding:6px 10px;text-align:left}
  th{background:#faf5ff;cursor:pointer;user-select:none;position:sticky;top:0}
  .filters{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
  .filters input,.filters select{padding:6px 8px;border:1px solid #e9d5ff;border-radius:4px;font-size:13px}
  .table-wrap{max-height:520px;overflow:auto;border:1px solid #e9d5ff;border-radius:6px}
  .quantum-badge{display:inline-block;background:#7c3aed;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:8px}
</style></head><body>
<h1>Container Yard — Quantum SQA Solver <span class='quantum-badge'>Suzuki–Trotter</span></h1>
<div class='sub' id='sub'></div>
<div class='narrative' id='narrative'></div>
<div class='kpis' id='kpis'></div>
<div class='tabs'>
  <div class='tab active' data-tab='heatmap'>Block Heatmap</div>
  <div class='tab' data-tab='timeline'>Vessel Timeline</div>
  <div class='tab' data-tab='convergence'>SQA Convergence</div>
  <div class='tab' data-tab='quantum'>Quantum Field Evolution</div>
  <div class='tab' data-tab='input'>Input Overview</div>
  <div class='tab' data-tab='plan'>Stacking Plan</div>
</div>
<div id='panel-heatmap' class='panel active'><div id='heatmap'></div></div>
<div id='panel-timeline' class='panel'><div id='timeline'></div></div>
<div id='panel-convergence' class='panel'><div id='convergence'></div></div>
<div id='panel-quantum' class='panel'><div id='quantum'></div></div>
<div id='panel-input' class='panel'><div id='inputCharts'></div></div>
<div id='panel-plan' class='panel'>
  <div class='filters'>
    <input id='fId' placeholder='filter by container id…'/>
    <select id='fVessel'><option value=''>all vessels</option></select>
    <select id='fBlock'><option value=''>all blocks</option></select>
  </div>
  <div class='table-wrap'><table id='planTable'><thead></thead><tbody></tbody></table></div>
</div>
<script>
const DATA = __DATA__;

const kpiHost = document.getElementById('kpis');
Object.entries(DATA.kpi).forEach(([k,v])=>{
  const el = document.createElement('div'); el.className='kpi';
  el.innerHTML = `<div class='label'>${k.replace(/_/g,' ')}</div><div class='value'>${v}</div>`;
  kpiHost.appendChild(el);
});
document.getElementById('sub').textContent = `${DATA.kpi.algorithm} — ${DATA.kpi.total_reshuffles} total reshuffles, ${DATA.kpi.quantum_tunnel_events||0} tunnel events`;
document.getElementById('narrative').textContent = DATA.narrative;

document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('panel-'+t.dataset.tab).classList.add('active');
  window.dispatchEvent(new Event('resize'));
});

const blocks = Object.values(DATA.block_heatmap);
const traces = [];
blocks.forEach((blk,bi)=>{
  const z=[],hover=[];
  for(let r=0;r<blk.dimensions.rows;r++){
    const row=[],hrow=[];
    for(let b=0;b<blk.dimensions.bays;b++){
      const cell = blk.grid[r][b];
      row.push(cell.height);
      const ids = (cell.containers||[]).map(c=>`${c.id} (T${c.tier}, ${c.weight}t, ${c.vessel})`).join('<br>');
      hrow.push(`<b>${blk.block_id}</b> r${r} b${b}<br>height=${cell.height}/${cell.max_height}<br>vessels: ${(cell.vessels||[]).join(', ')||'-'}<br>weight=${cell.total_weight_tonnes}t<br>${ids||'(empty)'}`);
    }
    z.push(row); hover.push(hrow);
  }
  traces.push({type:'heatmap',z,text:hover,hoverinfo:'text',colorscale:'Purples',xaxis:'x'+(bi+1),yaxis:'y'+(bi+1),showscale:bi===0,colorbar:{title:'tier'}});
});
const heatLayout = {grid:{rows:1,columns:blocks.length,pattern:'independent'},height:380,margin:{t:30,r:10,l:40,b:40}};
blocks.forEach((blk,bi)=>{
  heatLayout['xaxis'+(bi+1)] = {title:`bay (${blk.block_id} — ${blk.utilization_pct}%)`};
  heatLayout['yaxis'+(bi+1)] = {title:'row',autorange:'reversed'};
});
Plotly.newPlot('heatmap', traces, heatLayout, {responsive:true,displaylogo:false});

const vt = DATA.vessel_timeline;
Plotly.newPlot('timeline', [
  {type:'bar',x:vt.map(v=>v.vessel_id),y:vt.map(v=>v.reshuffles),name:'reshuffles',marker:{color:vt.map(v=>v.status==='clean'?'#22c55e':v.status==='minor'?'#f59e0b':'#ef4444')},
   text:vt.map(v=>`dep ${v.departure_order} • ${v.num_containers}c • ${v.total_weight_tonnes}t<br>eff=${v.retrieval_efficiency_pct}%`),hoverinfo:'x+y+text'},
  {type:'scatter',mode:'lines+markers',x:vt.map(v=>v.vessel_id),y:vt.map(v=>v.cumulative_reshuffles),name:'cumulative',yaxis:'y2',line:{color:'#7c3aed',width:2}},
  {type:'scatter',mode:'lines+markers',x:vt.map(v=>v.vessel_id),y:vt.map(v=>v.retrieval_efficiency_pct),name:'retrieval eff %',yaxis:'y3',line:{color:'#0891b2',width:1.5,dash:'dash'}}
], {height:420,margin:{t:30,r:60,l:50,b:60},
    yaxis:{title:'reshuffles'},
    yaxis2:{title:'cumulative',overlaying:'y',side:'right',position:1.0,showgrid:false},
    yaxis3:{title:'eff %',overlaying:'y',side:'right',position:0.95,showgrid:false},
    legend:{orientation:'h',y:-0.25}}, {responsive:true,displaylogo:false});

const ch = DATA.convergence;
Plotly.newPlot('convergence', [
  {type:'scatter',mode:'lines+markers',x:ch.map(p=>p.sweep),y:ch.map(p=>p.best),name:'best',line:{color:'#7c3aed',width:2}},
  {type:'scatter',mode:'lines',x:ch.map(p=>p.sweep),y:ch.map(p=>p.gamma),name:'γ (transverse field)',yaxis:'y2',line:{color:'#dc2626',width:1.5,dash:'dash'}},
  {type:'scatter',mode:'lines',x:ch.map(p=>p.sweep),y:ch.map(p=>p.temp),name:'temperature',yaxis:'y2',line:{color:'#0891b2',width:1.5,dash:'dot'}}
], {height:420,margin:{t:30,r:60,l:60,b:50},xaxis:{title:'sweep'},yaxis:{title:'objective'},yaxis2:{title:'γ / temperature',overlaying:'y',side:'right',showgrid:false,type:'log'},legend:{orientation:'h',y:-0.2}}, {responsive:true,displaylogo:false});

const fh = DATA.field_history;
Plotly.newPlot('quantum', [
  {type:'scatter',mode:'lines',x:fh.map(p=>p.sweep),y:fh.map(p=>p.gamma),name:'γ (transverse field)',line:{color:'#dc2626',width:2}},
  {type:'scatter',mode:'lines',x:fh.map(p=>p.sweep),y:fh.map(p=>p.temperature),name:'temperature',line:{color:'#0891b2',width:2}},
  {type:'bar',x:fh.map(p=>p.sweep),y:fh.map(p=>p.tunnel_events_in_sweep),name:'tunnels / sweep',yaxis:'y2',marker:{color:'#7c3aed',opacity:0.6}},
  {type:'scatter',mode:'lines+markers',x:fh.map(p=>p.sweep),y:fh.map(p=>p.cumulative_tunnels),name:'cumulative tunnels',yaxis:'y3',line:{color:'#1e3a8a',width:2}}
], {height:480,margin:{t:30,r:80,l:60,b:50},xaxis:{title:'sweep'},yaxis:{title:'γ / temperature',type:'log'},yaxis2:{title:'tunnels/sweep',overlaying:'y',side:'right',showgrid:false},yaxis3:{title:'cumulative',overlaying:'y',side:'right',position:0.95,showgrid:false},legend:{orientation:'h',y:-0.18}}, {responsive:true,displaylogo:false});

const vIn = DATA.vessels_in;
Plotly.newPlot('inputCharts', [
  {type:'bar',x:vIn.map(v=>v.vessel_id),y:vIn.map(v=>v.containers),name:'containers',marker:{color:'#7c3aed'},xaxis:'x1',yaxis:'y1'},
  {type:'histogram',x:DATA.weights,name:'weight (t)',marker:{color:'#06b6d4'},xaxis:'x2',yaxis:'y2'}
], {grid:{rows:1,columns:2,pattern:'independent'},height:380,margin:{t:30,r:10,l:50,b:60},
    xaxis1:{title:'vessel'},yaxis1:{title:'# containers'},
    xaxis2:{title:'weight (tonnes)'},yaxis2:{title:'# containers'},showlegend:false}, {responsive:true,displaylogo:false});

const cols = ['container_id','vessel_id','vessel_departure_order','weight_tonnes','assigned_block','assigned_row','assigned_bay','tier_level','reshuffles_if_retrieved_now'];
const thead = document.querySelector('#planTable thead');
thead.innerHTML = '<tr>'+cols.map(c=>`<th data-col='${c}'>${c.replace(/_/g,' ')}</th>`).join('')+'</tr>';
const fVes = document.getElementById('fVessel'), fBlk = document.getElementById('fBlock');
[...new Set(DATA.plan.map(r=>r.vessel_id))].sort().forEach(v=>fVes.innerHTML+=`<option>${v}</option>`);
[...new Set(DATA.plan.map(r=>r.assigned_block))].sort().forEach(b=>fBlk.innerHTML+=`<option>${b}</option>`);
let sortCol=null,sortAsc=true;
function render(){
  const id=document.getElementById('fId').value.toLowerCase();
  const v=fVes.value, bk=fBlk.value;
  let rows = DATA.plan.filter(r=>(!id||String(r.container_id).toLowerCase().includes(id))&&(!v||r.vessel_id===v)&&(!bk||r.assigned_block===bk));
  if(sortCol) rows.sort((a,b)=>{const x=a[sortCol],y=b[sortCol]; return (x>y?1:x<y?-1:0)*(sortAsc?1:-1);});
  document.querySelector('#planTable tbody').innerHTML = rows.map(r=>'<tr>'+cols.map(c=>`<td>${r[c]}</td>`).join('')+'</tr>').join('');
}
document.querySelectorAll('#planTable th').forEach(th=>th.onclick=()=>{const c=th.dataset.col; if(sortCol===c) sortAsc=!sortAsc; else {sortCol=c;sortAsc=true;} render();});
['fId','fVessel','fBlock'].forEach(id=>document.getElementById(id).oninput=render);
render();
</script></body></html>"""


def _write_interactive_html(out_dir, containers, kpi, narrative, block_heatmap,
                            vessel_timeline, convergence_history, field_history, stacking_plan):
    cm = {c["id"]: c for c in containers}
    plan_rows = []
    for a in stacking_plan:
        c = cm.get(a["id"], {})
        plan_rows.append({
            "container_id": a["id"], "vessel_id": c.get("vessel_id", ""),
            "vessel_departure_order": c.get("vessel_departure_order", 0),
            "weight_tonnes": c.get("weight_tonnes", 0),
            "assigned_block": a["assigned_block"], "assigned_row": a["assigned_row"],
            "assigned_bay": a["assigned_bay"], "tier_level": a["tier_level"],
            "reshuffles_if_retrieved_now": a.get("reshuffles_if_retrieved_now", 0),
        })
    vessels_in = {}
    for c in containers:
        vid = c.get("vessel_id", "UNK")
        vessels_in.setdefault(vid, {"vessel_id": vid, "departure_order": c.get("vessel_departure_order", 0), "containers": 0})
        vessels_in[vid]["containers"] += 1
    payload = {
        "kpi": kpi,
        "narrative": narrative,
        "block_heatmap": block_heatmap,
        "vessel_timeline": vessel_timeline,
        "convergence": convergence_history,
        "field_history": field_history,
        "vessels_in": sorted(vessels_in.values(), key=lambda x: x["departure_order"]),
        "weights": [c.get("weight_tonnes", 0) for c in containers],
        "plan": plan_rows,
    }
    html = _INTERACTIVE_HTML_TEMPLATE.replace("__DATA__", json.dumps(payload))
    path = os.path.join(out_dir, "10_interactive_dashboard.html")
    with open(path, "w") as f:
        f.write(html)
    return path


def _enrich_kpi_with_cost(kpi, total_reshuffles, n_containers, n_vessels):
    """Iter 1: add business-cost framing using $25–50/reshuffle from the use case
    business description (port operations: crane time + fuel + labor)."""
    low, high = 25.0, 50.0
    avg = (low + high) / 2
    enriched = dict(kpi)
    enriched['estimated_reshuffle_cost_usd_low']  = round(total_reshuffles * low, 0)
    enriched['estimated_reshuffle_cost_usd_high'] = round(total_reshuffles * high, 0)
    enriched['estimated_reshuffle_cost_usd_mid']  = round(total_reshuffles * avg, 0)
    enriched['cost_per_reshuffle_usd_range']      = '$25–$50 (crane time + fuel + labor)'
    return enriched


def generate_additional_output(containers, yard_layout, stacking_plan, block_heatmap,
                               vessel_timeline, convergence_history, field_history,
                               quantum_metrics, kpi_dashboard, narrative,
                               out_dir=None, logger=None):
    """Write all visualization artifacts under ./additional_output/.

    Designed to never raise: per-file failures are logged and skipped so the
    solver still returns a valid output dict.
    """
    if out_dir is None:
        out_dir = os.path.join(os.getcwd(), "additional_output")
    _ensure_dir(out_dir)
    written = []
    total_reshuffles = 0
    for v in (vessel_timeline or []): total_reshuffles += v.get('reshuffles', 0)
    n_vessels = len({c.get('vessel_id') for c in containers})
    kpi_dashboard = _enrich_kpi_with_cost(kpi_dashboard, total_reshuffles, len(containers), n_vessels)

    def _try(fn, label, *a, **kw):
        try:
            p = fn(*a, **kw)
            if p:
                written.append(p)
                if logger:
                    logger.info(f"additional_output: wrote {label} -> {os.path.basename(p)}")
        except Exception as e:
            if logger:
                logger.warning(f"additional_output: failed to write {label}: {e}")

    _try(_write_input_summary,   "input_summary.json", out_dir, containers, yard_layout)

    p_in = p_blk = p_ves = p_conv = p_q = None
    try: p_in = _plot_input_overview(out_dir, containers)
    except Exception as e:
        if logger: logger.warning(f"additional_output: input_overview failed: {e}")
    if p_in: written.append(p_in)

    try: p_blk = _plot_block_heatmap(out_dir, block_heatmap)
    except Exception as e:
        if logger: logger.warning(f"additional_output: block_heatmap failed: {e}")
    if p_blk: written.append(p_blk)

    try: p_ves = _plot_vessel_timeline(out_dir, vessel_timeline)
    except Exception as e:
        if logger: logger.warning(f"additional_output: vessel_timeline failed: {e}")
    if p_ves: written.append(p_ves)

    try: p_conv = _plot_convergence(out_dir, convergence_history)
    except Exception as e:
        if logger: logger.warning(f"additional_output: convergence failed: {e}")
    if p_conv: written.append(p_conv)

    try: p_q = _plot_field_evolution(out_dir, field_history)
    except Exception as e:
        if logger: logger.warning(f"additional_output: quantum_field_evolution failed: {e}")
    if p_q: written.append(p_q)

    _try(_write_kpi_json,         "kpi_dashboard.json",  out_dir, kpi_dashboard)
    _try(_write_stacking_csv,     "stacking_plan.csv",   out_dir, stacking_plan, containers)
    _try(_write_quantum_metrics,  "quantum_metrics.json", out_dir, quantum_metrics)

    try:
        report = _write_html_report(out_dir, kpi_dashboard, [
            ("Input overview", p_in),
            ("Block heatmap (output)", p_blk),
            ("Vessel timeline (output)", p_ves),
            ("SQA convergence (output)", p_conv),
            ("Quantum field evolution (output)", p_q),
        ], narrative)
        written.append(report)
        if logger:
            logger.info(f"additional_output: wrote report -> {os.path.basename(report)}")
    except Exception as e:
        if logger:
            logger.warning(f"additional_output: failed to write HTML report: {e}")

    try:
        interactive = _write_interactive_html(out_dir, containers, kpi_dashboard, narrative,
                                              block_heatmap, vessel_timeline, convergence_history,
                                              field_history, stacking_plan)
        written.append(interactive)
        if logger:
            logger.info(f"additional_output: wrote interactive -> {os.path.basename(interactive)}")
    except Exception as e:
        if logger:
            logger.warning(f"additional_output: failed to write interactive HTML: {e}")

    return {"out_dir": out_dir, "files": [os.path.basename(p) for p in written]}
