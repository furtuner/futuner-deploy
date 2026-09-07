"""
Cat Diet Planner - API Router

Endpoints:
- /user-ingredients       GET  — flat ingredient list for frontend
- /ingredients-grouped    GET  — grouped with metadata
- /reload                 POST — reload CSVs
- /diet-report            POST/GET — HTML diet composition report
- /ingredients-summary    POST/GET — HTML fresh weights + feeding plan
- /diet-aafco-full        POST/GET — HTML full AAFCO cat nutrient table
- /calculate              POST — JSON diet result + auto-opens report
"""

from typing import List
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from urllib.parse import urlencode
import webbrowser
import json

from services import ingredients as svc

router = APIRouter()


class IngredientRequest(BaseModel):
    ingredients: List[str]


# -------------------------------------------------------------------
# User ingredients — flat list for frontend
# -------------------------------------------------------------------
@router.get("/user-ingredients")
def user_ingredients():
    grouped = svc.get_ingredients_grouped_by_category()

    category_order = [
        "Meat Group A", "Meat Group B", "Meat Group C",
        "Organ Meat (Other)", "Organ Meat (Liver)",
        "Grain A", "Grain B",
        "Vegetable A", "Vegetable B", "Vegetable C",
        "Fruit", "Oil", "Fiber",
        "Mineral Group A",
    ]

    category_display = {
        "Meat Group A":       "01 Meat Group A (Mandatory - Select at least one and a maximum of three)",
        "Meat Group B":       "02 Meat Group B (Optional - Pick up to one)",
        "Meat Group C":       "03 Meat Group C (Optional - Pick up to one)",
        "Organ Meat (Other)": "04 Organ Meat - Other (Optional - Pick up to one)",
        "Organ Meat (Liver)": "05 Organ Meat - Liver (Mandatory - Select one)",
        "Grain A":            "06 Grain A (Mandatory - Select at least one and a maximum of three)",
        "Grain B":            "07 Grain B (Optional - Pick up to one)",
        "Vegetable A":        "08 Vegetable A (Mandatory - Select at least one and up to three maximum)",
        "Vegetable B":        "09 Vegetable B (Optional - Pick up to two)",
        "Vegetable C":        "10 Vegetable C - Potatoes (Optional - Pick up to one)",
        "Fruit":              "11 Fruit (Optional - Up to two maximum)",
        "Oil":                "12 Oil (Mandatory - Select at least one and a maximum of three)",
        "Fiber":              "13 Fiber & Seeds (Optional - Pick up to two maximum)",
        "Mineral Group A":    "14 Mineral Group A",
    }

    result = []
    for cat in category_order:
        if cat not in grouped:
            continue
        display_name = category_display.get(cat, cat)
        for item in grouped[cat]["items"]:
            result.append({
                "ingredient_name": item["ingredient_name"],
                "group_name": display_name,
            })
    return result


@router.get("/ingredients-grouped")
def ingredients_grouped():
    return svc.get_ingredients_grouped_by_category()


@router.post("/reload")
def reload_csvs():
    return svc.load_csvs()


# -------------------------------------------------------------------
# Diet Report
# -------------------------------------------------------------------
@router.post("/diet-report", response_class=HTMLResponse)
def diet_report_post(request: Request, body: IngredientRequest):
    return _generate_diet_report(request, body.ingredients)


@router.get("/diet-report", response_class=HTMLResponse)
def diet_report_get(request: Request, ingredients: List[str] = Query(...)):
    return _generate_diet_report(request, ingredients)


def _generate_diet_report(request: Request, ingredients: List[str]):
    result = svc.calculate_diet(ingredients)

    energy      = float(result.get("Energy",          0.0))
    protein_pct = float(result.get("Protein_percent", 0.0))
    fat_pct     = float(result.get("Fat_percent",     0.0))
    cho_pct     = float(result.get("CHO_percent",     0.0))
    ash_pct     = float(result.get("Ash_percent",     0.0))
    fiber_pct   = float(result.get("Fiber_percent",   0.0))
    ca_p_ratio  = float(result.get("Ca_P_ratio",      0.0))
    ca_pct      = float(result.get("Ca_percent",      0.0))
    iron_mg     = float(result.get("iron_mg",         0.0))
    omega_ratio = (
        result.get("omega6_omega3_ratio") or result.get("Omega6_omega3_ratio") or None
    )

    if energy > 0:
        protein_energy_pct = ((4 * protein_pct) / energy) * 1000
        fat_energy_pct     = ((9 * fat_pct)     / energy) * 1000
        cho_energy_pct     = ((4 * cho_pct)     / energy) * 1000
    else:
        protein_energy_pct = fat_energy_pct = cho_energy_pct = 0

    aafco_pct = result.get("aafco_percent_of_minimum", {})

    # Summary AAFCO table — key nutrients only
    aafco_specs = [
        {"label": "Protein (% DM)",   "diet_value": protein_pct, "min_value": svc.AAFCO_MINIMUMS.get("Protein"), "pct_of_min": aafco_pct.get("Protein")},
        {"label": "Fat (% DM)",        "diet_value": fat_pct,     "min_value": svc.AAFCO_MINIMUMS.get("Fat"),     "pct_of_min": aafco_pct.get("Fat")},
        {"label": "Ash (% DM)",        "diet_value": ash_pct,     "min_value": None,                              "pct_of_min": None},
        {"label": "CHO (% DM)",        "diet_value": cho_pct,     "min_value": None,                              "pct_of_min": None},
        {"label": "Fiber (% DM)",      "diet_value": fiber_pct,   "min_value": None,                              "pct_of_min": None},
        {"label": "Calcium (% DM)",    "diet_value": ca_pct,      "min_value": svc.AAFCO_MINIMUMS.get("Ca"),      "pct_of_min": aafco_pct.get("Ca")},
        {"label": "Iron (mg/kg DM)",   "diet_value": iron_mg,     "min_value": svc.AAFCO_MINIMUMS.get("Iron"),    "pct_of_min": aafco_pct.get("Iron")},
    ]

    aafco_rows_html = ""
    for spec in aafco_specs:
        diet_val = spec["diet_value"]
        min_val  = spec["min_value"]
        pct_back = spec.get("pct_of_min")
        if pct_back is not None:
            pct_min_str = f"{pct_back * 100:.1f}%"
        elif min_val is not None and min_val > 0:
            pct_min_str = f"{diet_val / min_val * 100:.1f}%"
        else:
            pct_min_str = "—"
        min_str = f"{min_val}" if min_val is not None else "—"
        aafco_rows_html += (
            f"<tr><td>{spec['label']}</td>"
            f"<td>{diet_val:.2f}</td>"
            f"<td>{min_str}</td>"
            f"<td>{pct_min_str}</td></tr>"
        )

    ingredients_json        = json.dumps(ingredients)
    aafco_url               = str(request.url_for("diet_aafco_full"))
    ingredients_summary_url = str(request.url_for("ingredients_summary"))

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Cat Diet Report</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f6f6f6; }}
    h1 {{ margin-bottom: 0.5rem; }}
    h2 {{ margin-top: 2rem; margin-bottom: 0.5rem; }}
    .layout {{ display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }}
    table {{ border-collapse: collapse; background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; min-width: 380px; }}
    th, td {{ padding: 8px 12px; border-bottom: 1px solid #e1e1e1; font-size: 14px; }}
    th {{ background-color: #f0f0f0; text-align: left; font-weight: 600; }}
    tr:last-child td {{ border-bottom: none; }}
    .metric-label {{ font-weight: 600; }}
    #chartContainer {{ background: #fff; padding: 16px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.08); }}
    #macroPie {{ max-width: 360px; max-height: 360px; }}
    .chips {{ margin-top: 8px; margin-bottom: 16px; display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{ background: #e3f2fd; padding: 4px 10px; border-radius: 999px; font-size: 12px; }}
    .btn-link {{ margin-top: 16px; margin-right: 12px; display: inline-block; padding: 8px 14px; border-radius: 4px; background-color: #1976d2; color: #fff; font-size: 14px; border: none; cursor: pointer; text-decoration: none; }}
    .btn-link:hover {{ background-color: #12579b; }}
    .btn-link.green {{ background-color: #2e7d32; }}
    .btn-link.green:hover {{ background-color: #1b5e20; }}
    .button-container {{ margin-top: 20px; display: flex; flex-wrap: wrap; gap: 12px; }}
  </style>
</head>
<body>
  <h1>🐱 Cat Diet Composition &amp; Macros</h1>
  <div class="chips">
    {"".join(f'<span class="chip">{ing}</span>' for ing in ingredients)}
  </div>
  <div class="layout">
    <table>
      <thead><tr><th>Metric</th><th>Value</th></tr></thead>
      <tbody>
        <tr><td class="metric-label">Energy (kcal/kg DM)</td><td>{energy:.0f}</td></tr>
        <tr><td class="metric-label">Protein (% DM)</td><td>{protein_pct:.2f}</td></tr>
        <tr><td class="metric-label">Fat (% DM)</td><td>{fat_pct:.2f}</td></tr>
        <tr><td class="metric-label">CHO (% DM)</td><td>{cho_pct:.2f}</td></tr>
        <tr><td class="metric-label">Ash (% DM)</td><td>{ash_pct:.2f}</td></tr>
        <tr><td class="metric-label">Fiber (% DM)</td><td>{fiber_pct:.2f}</td></tr>
        <tr><td class="metric-label">Ca:P ratio</td><td>{ca_p_ratio:.2f}</td></tr>
        {"<tr><td class='metric-label'>Omega-6:Omega-3</td><td>" + f"{omega_ratio:.2f}" + "</td></tr>" if omega_ratio else ""}
      </tbody>
    </table>
    <div id="chartContainer">
      <h2>Macro Pie Chart (% of Energy)</h2>
      <canvas id="macroPie"></canvas>
      <p style="font-size:12px;color:#555;margin-top:8px;">Protein 4 kcal/g · Fat 9 kcal/g · CHO 4 kcal/g</p>
    </div>
  </div>

  <h2>AAFCO Cat Adult Maintenance — key nutrients</h2>
  <table>
    <thead><tr><th>Nutrient</th><th>Diet value</th><th>AAFCO min</th><th>% of min</th></tr></thead>
    <tbody>{aafco_rows_html}</tbody>
  </table>

  <div class="button-container">
    <button class="btn-link" onclick="openAAFCOFull()">Open full AAFCO table</button>
    <button class="btn-link green" onclick="openIngredientsSummary()">View Ingredients Summary</button>
  </div>

  <script>
  async function openAAFCOFull() {{
    const r = await fetch('{aafco_url}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{ingredients:{ingredients_json}}})}});
    if (!r.ok) {{ alert('Error: ' + r.statusText); return; }}
    const w = window.open('','_blank');
    if (!w) {{ alert('Allow pop-ups to open the AAFCO report'); return; }}
    w.document.write(await r.text()); w.document.close();
  }}
  async function openIngredientsSummary() {{
    const r = await fetch('{ingredients_summary_url}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{ingredients:{ingredients_json}}})}});
    if (!r.ok) {{ alert('Error: ' + r.statusText); return; }}
    const w = window.open('','_blank');
    if (!w) {{ alert('Allow pop-ups to open the summary'); return; }}
    w.document.write(await r.text()); w.document.close();
  }}
  </script>
  <script>
    Chart.register(ChartDataLabels);
    new Chart(document.getElementById('macroPie').getContext('2d'), {{
      type: 'pie',
      data: {{
        labels: ['Protein','Fat','CHO'],
        datasets: [{{
          data: [{protein_energy_pct:.2f},{fat_energy_pct:.2f},{cho_energy_pct:.2f}],
          backgroundColor: ['#4CAF50','#FF9800','#2196F3'],
          borderColor: '#fff', borderWidth: 2
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{position:'bottom'}},
          title: {{display:true, text:'Energy Contribution (% of Total kcal)'}},
          datalabels: {{formatter: v => v.toFixed(1)+'%', color:'#fff', font:{{weight:'bold'}}}}
        }}
      }},
      plugins: [ChartDataLabels]
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# Ingredients Summary
# -------------------------------------------------------------------
@router.post("/ingredients-summary", response_class=HTMLResponse, name="ingredients_summary")
def ingredients_summary_post(body: IngredientRequest):
    return _generate_ingredients_summary(body.ingredients)


@router.get("/ingredients-summary", response_class=HTMLResponse, name="ingredients_summary_get")
def ingredients_summary_get(ingredients: List[str] = Query(...)):
    return _generate_ingredients_summary(ingredients)


def _generate_ingredients_summary(ingredients: List[str]):
    result       = svc.calculate_diet(ingredients)
    allocations  = result.get("ingredient_allocations", {})
    dm_breakdown = result.get("dm_breakdown", [])
    diet_energy_kcal_per_kg = float(result.get("Energy", 0.0))

    total_fresh_weight = 0.0
    total_dm_weight    = 0.0
    rows_html          = ""
    seen               = set()

    for item in dm_breakdown:
        name = item.get("ingredient", "")
        if not name or name in seen:
            continue
        seen.add(name)
        dm_g         = float(item.get("dm_g", 0))
        fresh_weight = float(item.get("fresh_weight_g", 0))
        water_pct    = float(item.get("water_percent", 0))
        is_fixed     = item.get("fixed", False)

        if fresh_weight == 0 and name in allocations:
            alloc = allocations[name]
            if isinstance(alloc, dict):
                fresh_weight = float(alloc.get("fresh_weight_g", 0))

        total_fresh_weight += fresh_weight
        total_dm_weight    += dm_g
        fixed_marker        = " (fixed)" if is_fixed else ""

        rows_html += f"""
        <tr>
            <td>{name}{fixed_marker}</td>
            <td class="value">{dm_g:.1f}</td>
            <td class="value">{fresh_weight:.1f}</td>
        </tr>"""

    rows_html += f"""
    <tr class="total-row">
        <td><strong>Total</strong></td>
        <td class="value"><strong>{total_dm_weight:.1f}</strong></td>
        <td class="value"><strong>{total_fresh_weight:.1f}</strong></td>
    </tr>"""

    # Pre-compute JS array outside f-string (backslashes not allowed inside f-strings)
    nl = "\n"
    ingredients_js_array = nl.join(
        '    ["' + item.get("ingredient", "").replace('"', '\\\\"'  ) + '", '
        + str(round(float(item.get("dm_g", 0)), 4)) + ', '
        + str(round(float(item.get("fresh_weight_g", 0)), 4)) + ', '
        + str(round(float(item.get("water_percent", 0)), 4)) + '],'
        for item in dm_breakdown if item.get("ingredient", "")
    )

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ingredients Summary - Fresh Weights</title>
  <style>
    body {{ font-family:'Segoe UI',sans-serif; margin:0; background:linear-gradient(135deg,#2e7d32,#43a047); min-height:100vh; padding:20px; }}
    .container {{ max-width:860px; margin:0 auto; background:#fff; border-radius:12px; padding:30px; box-shadow:0 10px 40px rgba(0,0,0,0.2); }}
    h1 {{ color:#2c3e50; font-size:26px; margin-bottom:4px; }}
    .subtitle {{ color:#7f8c8d; font-size:13px; margin-bottom:18px; }}
    .tab-bar {{ display:flex; border-bottom:2px solid #2e7d32; margin-bottom:22px; gap:4px; }}
    .tab-btn {{ padding:9px 22px; border:2px solid transparent; border-bottom:none; border-radius:8px 8px 0 0; background:#f1f8f1; color:#2e7d32; font-size:14px; font-weight:600; cursor:pointer; margin-bottom:-2px; }}
    .tab-btn.active {{ background:#fff; border-color:#2e7d32; color:#1b5e20; }}
    .tab-panel {{ display:none; }}
    .tab-panel.active {{ display:block; }}
    table {{ width:100%; border-collapse:collapse; margin-top:16px; box-shadow:0 2px 8px rgba(0,0,0,0.1); border-radius:8px; overflow:hidden; }}
    th {{ background:linear-gradient(135deg,#2e7d32,#43a047); color:#fff; padding:13px 16px; text-align:left; font-size:13px; font-weight:600; }}
    td {{ padding:11px 16px; border-bottom:1px solid #ecf0f1; font-size:14px; }}
    tr:hover td {{ background:#f8f9fa; }}
    .value {{ text-align:right; font-family:'Courier New',monospace; font-weight:500; }}
    .total-row {{ background:#e8f5e9; border-top:2px solid #2e7d32; }}
    .total-row td {{ border-bottom:none; }}
    .note {{ margin-top:18px; padding:14px; background:#e8f5e9; border-left:4px solid #2e7d32; border-radius:4px; font-size:13px; color:#1b5e20; }}
    .formula {{ margin-top:12px; padding:12px; background:#fff8e1; border-left:4px solid #ff9800; border-radius:4px; font-size:12px; color:#e65100; }}
    .print-btn {{ margin-top:18px; padding:10px 20px; background:linear-gradient(135deg,#2e7d32,#43a047); color:#fff; border:none; border-radius:6px; font-size:14px; cursor:pointer; }}
    .energy-panel {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:18px; }}
    .energy-card {{ background:#f4faf4; border:1px solid #c8e6c9; border-radius:8px; padding:16px 18px; }}
    .energy-card label {{ display:block; font-size:12px; color:#555; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:8px; }}
    .energy-card input, .energy-card select {{ width:100%; padding:9px 10px; font-size:16px; border:1.5px solid #a5d6a7; border-radius:6px; box-sizing:border-box; color:#1b5e20; font-weight:700; }}
    .derived-row {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; background:#f4faf4; border:1px solid #c8e6c9; border-radius:8px; padding:14px 18px; margin-bottom:20px; }}
    .derived-label {{ font-size:11px; color:#777; font-weight:700; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px; }}
    .derived-val {{ font-size:17px; font-weight:700; color:#2e7d32; font-family:'Courier New',monospace; }}
    .plan-title {{ font-size:15px; font-weight:700; color:#2c3e50; margin:18px 0 4px; }}
    .days-table th, .days-table td {{ text-align:right; }}
    .days-table th:first-child, .days-table td:first-child {{ text-align:left; font-weight:500; }}
    @media print {{ body {{ background:#fff; padding:0; }} .container {{ box-shadow:none; }} .print-btn,.tab-bar {{ display:none; }} .tab-panel {{ display:block !important; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🥗 Ingredients Summary</h1>
    <div class="subtitle">Fresh weights and daily feeding plan for your homemade cat diet recipe</div>
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('summary',this)">📋 Ingredients Summary</button>
      <button class="tab-btn" onclick="switchTab('feeding',this)">🐾 Daily Feeding Plan</button>
    </div>

    <div id="tab-summary" class="tab-panel active">
      <table>
        <thead><tr><th>Ingredient</th><th style="text-align:right">Dry Matter (g)</th><th style="text-align:right">Fresh Weight (g)</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <div class="note"><strong>Note:</strong> Weigh ingredients before cooking. Total fresh weight: <strong>{total_fresh_weight:.1f}g</strong></div>
      <div class="formula"><strong>Formula:</strong> Fresh Weight = Dry Matter ÷ (1 − Water%/100)</div>
      <button class="print-btn" onclick="window.print()">🖨️ Print Recipe</button>
    </div>

    <div id="tab-feeding" class="tab-panel">
      <div class="energy-panel">
        <div class="energy-card">
          <label>🐱 Cat Body Weight (kg)</label>
          <input type="number" id="catWeight" min="0.1" step="0.1" placeholder="e.g. 4.5" oninput="recalc()" />
        </div>
        <div class="energy-card">
          <label>🐱 Life Stage</label>
          <select id="lifeStage" onchange="recalc()">
            <option value="1.4">Adult intact (RER × 1.4)</option>
            <option value="1.2" selected>Neutered adult (RER × 1.2)</option>
            <option value="1.0">Obese adult (RER × 1.0)</option>
          </select>
        </div>
        <div class="energy-card">
          <label>⚡ Diet Energy (kcal/kg DM)</label>
          <input type="number" id="dietEnergy" min="1" step="1" value="{diet_energy_kcal_per_kg:.0f}" oninput="recalc()" />
        </div>
      </div>
      <div class="derived-row">
        <div><div class="derived-label">RER (kcal)</div><div class="derived-val" id="dv_rer">—</div></div>
        <div><div class="derived-label">DER (kcal)</div><div class="derived-val" id="dv_catEnergy">—</div></div>
        <div><div class="derived-label">Diet energy</div><div class="derived-val" id="dv_dietEnergy">{diet_energy_kcal_per_kg:.0f} kcal/kg</div></div>
        <div><div class="derived-label">Daily DM (g)</div><div class="derived-val" id="dv_dailyIntake">—</div></div>
        <div><div class="derived-label">% of total mix</div><div class="derived-val" id="dv_pct">—</div></div>
      </div>
      <div id="planSection" style="display:none">
        <div class="plan-title">🥩 Fresh weight per batch (g)</div>
        <table class="days-table">
          <thead><tr><th>Ingredient</th><th>1 day</th><th>3 days</th><th>5 days</th><th>7 days</th><th>10 days</th></tr></thead>
          <tbody id="planBodyFresh"></tbody>
        </table>
        <div class="note" style="margin-top:14px"><strong>How:</strong> RER = 70 × W_kg^0.75. DER = RER × factor. Daily DM (g) = DER ÷ Diet energy × 1000. Fresh = DM ÷ (1 − water%).</div>
      </div>
      <div id="energyHint" style="margin-top:18px;color:#888;font-size:13px;">↑ Enter your cat's body weight to generate the feeding plan.</div>
    </div>
  </div>

<script>
  function switchTab(id, btn) {{
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-'+id).classList.add('active');
    btn.classList.add('active');
  }}
  const TOTAL_DM = {total_dm_weight:.4f};
  const INGREDIENTS = [
{ingredients_js_array}
  ];
  function recalc() {{
    const w = parseFloat(document.getElementById('catWeight').value);
    const f = parseFloat(document.getElementById('lifeStage').value);
    const e = parseFloat(document.getElementById('dietEnergy').value);
    const hint = document.getElementById('energyHint');
    const plan = document.getElementById('planSection');
    if (!w || w <= 0 || !e || e <= 0) {{
      ['dv_rer','dv_catEnergy','dv_dailyIntake','dv_pct'].forEach(id => document.getElementById(id).textContent='—');
      plan.style.display='none'; hint.style.display='block'; return;
    }}
    const rer = 70 * Math.pow(w, 0.75);
    const der = rer * f;
    const dm  = (der / e) * 1000;
    const pct = (dm / TOTAL_DM) * 100;
    document.getElementById('dv_rer').textContent        = rer.toFixed(1)+' kcal';
    document.getElementById('dv_catEnergy').textContent  = der.toFixed(1)+' kcal';
    document.getElementById('dv_dietEnergy').textContent = e.toFixed(0)+' kcal/kg';
    document.getElementById('dv_dailyIntake').textContent= dm.toFixed(1)+' g';
    document.getElementById('dv_pct').textContent        = pct.toFixed(2)+'%';
    hint.style.display='none';
    const days=[1,3,5,7,10]; let body=''; const tots=[0,0,0,0,0];
    for (const [name,dm_g,fresh_g,wp] of INGREDIENTS) {{
      if (dm_g<=0) continue;
      const frac=dm_g/TOTAL_DM, idm=frac*dm, wf=wp/100;
      let ifresh=wf<1?idm/(1-wf):idm;
      // EXCEPTION: Oyster canned applies proportional scaling of 10/14.9 for this
      // batch table only. wp (water%, 85.1) is read and used completely unchanged;
      // the CSV is never modified. Scales the result to reflect an 11g DM basis
      // instead of 14.9g, while remaining dynamic with the dog's energy needs.
      if (name === "oyster canned") {{ ifresh = ifresh * (10.0 / 14.9); }}
      let row=`<tr><td>${{name}}</td>`;
      days.forEach((d,i)=>{{ const v=ifresh*d; tots[i]+=v; row+=`<td>${{v.toFixed(1)}}</td>`; }});
      body+=row+'</tr>';
    }}
    let tot='<tr class="total-row"><td><strong>Total (g)</strong></td>';
    tots.forEach(t=>{{ tot+=`<td><strong>${{t.toFixed(1)}}</strong></td>`; }});
    document.getElementById('planBodyFresh').innerHTML=body+tot+'</tr>';
    plan.style.display='block';
  }}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# Full AAFCO Cat table
# -------------------------------------------------------------------
@router.post("/diet-aafco-full", response_class=HTMLResponse, name="diet_aafco_full")
def diet_aafco_full_post(body: IngredientRequest):
    return _generate_aafco_report(body.ingredients)


@router.get("/diet-aafco-full", response_class=HTMLResponse, name="diet_aafco_full_get")
def diet_aafco_full_get(ingredients: List[str] = Query(...)):
    return _generate_aafco_report(ingredients)


def _generate_aafco_report(ingredients: List[str]):
    result    = svc.calculate_diet(ingredients)
    aafco_pct = result.get("aafco_percent_of_minimum", {})

    def get_val(key, default=0.0):
        val = result.get(key, default)
        return float(val) if val is not None else default

    def get_pct(key):
        return aafco_pct.get(key)

    # (name, unit, result_key, aafco_pct_key, aafco_min)
    nutrients_config = {
        "Macronutrients": [
            ("Diet Energy",        "kcal/kg DM", "Energy",          None,      None),
            ("Protein",            "% DM",       "Protein_percent", "Protein", svc.AAFCO_MINIMUMS.get("Protein")),
            ("Fat",                "% DM",       "Fat_percent",     "Fat",     svc.AAFCO_MINIMUMS.get("Fat")),
            ("Carbohydrate (CHO)", "% DM",       "CHO_percent",     None,      None),
            ("Fiber",              "% DM",       "Fiber_percent",   None,      None),
            ("Ash",                "% DM",       "Ash_percent",     None,      None),
        ],
        "Major Minerals (% DM)": [
            ("Calcium (Ca)",    "% DM", "Ca_percent",  "Ca",  svc.AAFCO_MINIMUMS.get("Ca")),
            ("Phosphorus (P)",  "% DM", "P_percent",   "P",   svc.AAFCO_MINIMUMS.get("P")),
            ("Magnesium (Mg)",  "% DM", "Mg_percent",  "Mg",  svc.AAFCO_MINIMUMS.get("Mg")),
            ("Potassium (K)",   "% DM", "K_percent",   "K",   svc.AAFCO_MINIMUMS.get("K")),
            ("Sodium (Na)",     "% DM", "Na_percent",  "Na",  svc.AAFCO_MINIMUMS.get("Na")),
        ],
        "Trace Minerals (mg/kg DM)": [
            ("Iron (Fe)",    "mg/kg DM", "iron_mg_kg",    "Iron",   svc.AAFCO_MINIMUMS.get("Iron")),
            ("Zinc (Zn)",    "mg/kg DM", "zn_mg_kg",      "Zn",     svc.AAFCO_MINIMUMS.get("Zn")),
            ("Copper (Cu)",  "mg/kg DM", "cu_mg_kg",      "Cu",     svc.AAFCO_MINIMUMS.get("Cu")),
            ("Iodine (I)",   "mg/kg DM", "iodine_mg_kg",  "Iodine", svc.AAFCO_MINIMUMS.get("Iodine")),
            ("Selenium (Se)","mg/kg DM", "se_mg_kg",      "Se",     svc.AAFCO_MINIMUMS.get("Se")),
        ],
        "Vitamins": [
            ("Vitamin A",          "IU/kg DM", "vitamin_a_iu_kg",        "Vitamin_A",        svc.AAFCO_MINIMUMS.get("Vitamin_A")),
            ("Vitamin D",          "IU/kg DM", "vitamin_d_iu_kg",        "Vitamin_D",        svc.AAFCO_MINIMUMS.get("Vitamin_D")),
            ("Vitamin E",          "mg/kg DM", "vitamin_e_iu_kg",        "Vitamin_E",        svc.AAFCO_MINIMUMS.get("Vitamin_E")),
            ("Thiamin (B1)",       "mg/kg DM", "thiamin_mg_kg",          "Thiamin",          svc.AAFCO_MINIMUMS.get("Thiamin")),
            ("Riboflavin (B2)",    "mg/kg DM", "riboflavin_mg_kg",       "Riboflavin",       svc.AAFCO_MINIMUMS.get("Riboflavin")),
            ("Niacin (B3)",        "mg/kg DM", "niacin_mg_kg",           "Niacin",           svc.AAFCO_MINIMUMS.get("Niacin")),
            ("Pantothenic Acid",   "mg/kg DM", "pantothenic_acid_mg_kg", "Pantothenic_acid", svc.AAFCO_MINIMUMS.get("Pantothenic_acid")),
            ("Folate",             "mg/kg DM", "folate_mg_kg",           "Folate",           svc.AAFCO_MINIMUMS.get("Folate")),
            ("Cobalamin (B12)",    "mg/kg DM", "b12_mg_kg",              "B12",              svc.AAFCO_MINIMUMS.get("B12")),
        ],
        "Fatty Acids (% DM)": [
            ("Linoleic Acid (18:2 n-6)",      "% DM", "linoleic_percent", "FA_18_2", svc.AAFCO_MINIMUMS.get("FA_18_2")),
            ("α-Linolenic Acid (18:3 n-3)",   "% DM", "ala_percent",      "FA_18_3", svc.AAFCO_MINIMUMS.get("FA_18_3")),
            ("EPA (20:5)",                    "% DM", "epa_percent",      "EPA",     svc.AAFCO_MINIMUMS.get("EPA")),
            ("DHA (22:6)",                    "% DM", "dha_percent",      "DHA",     svc.AAFCO_MINIMUMS.get("DHA")),
        ],
        "Amino Acids (% DM)": [
            ("Arginine",      "% DM", "arginine_percent",    "Arginine",    svc.AAFCO_MINIMUMS.get("Arginine")),
            ("Isoleucine",    "% DM", "isoleucine_percent",  "Isoleucine",  svc.AAFCO_MINIMUMS.get("Isoleucine")),
            ("Leucine",       "% DM", "leucine_percent",     "Leucine",     svc.AAFCO_MINIMUMS.get("Leucine")),
            ("Lysine",        "% DM", "lysine_percent",      "Lysine",      svc.AAFCO_MINIMUMS.get("Lysine")),
            ("Methionine",    "% DM", "methionine_percent",  "Methionine",  svc.AAFCO_MINIMUMS.get("Methionine")),
            ("Phenylalanine", "% DM", "phenylalanine_percent","Phenylalanine",svc.AAFCO_MINIMUMS.get("Phenylalanine")),
            ("Threonine",     "% DM", "threonine_percent",   "Threonine",   svc.AAFCO_MINIMUMS.get("Threonine")),
            ("Tryptophan",    "% DM", "tryptophan_percent",  "Tryptophan",  svc.AAFCO_MINIMUMS.get("Tryptophan")),
            ("Tyrosine",      "% DM", "tyrosine_percent",    "Tyrosine",    svc.AAFCO_MINIMUMS.get("Tyrosine")),
            ("Valine",        "% DM", "valine_percent",      "Valine",      svc.AAFCO_MINIMUMS.get("Valine")),
        ],
    }

    def get_status(diet_val, aafco_min, pct_of_min):
        if aafco_min is None:
            return "—", "neutral"
        if pct_of_min is not None:
            if pct_of_min >= 1.0:   return "✓", "success"
            if pct_of_min >= 0.9:   return "⚠", "warning"
            return "✗", "danger"
        if diet_val < aafco_min:
            return ("⚠", "warning") if diet_val >= aafco_min * 0.9 else ("✗", "danger")
        return "✓", "success"

    table_html = ""
    for category, nutrients in nutrients_config.items():
        table_html += f'<tr class="category-header"><td colspan="6"><strong>{category}</strong></td></tr>'
        for name, unit, result_key, aafco_key, aafco_min in nutrients:
            diet_val   = get_val(result_key, 0.0)
            pct_of_min = get_pct(aafco_key) if aafco_key else None
            diet_str   = f"{diet_val:.2f}"
            min_str    = f"{aafco_min}" if aafco_min is not None else "—"
            if pct_of_min is not None:
                pct_str = f"{pct_of_min * 100:.1f}%"
            elif aafco_min is not None and aafco_min > 0:
                pct_str = f"{(diet_val / aafco_min) * 100:.1f}%"
            else:
                pct_str = "—"
            status_icon, status_class = get_status(diet_val, aafco_min, pct_of_min)
            table_html += (
                f"<tr><td>{name}</td><td>{unit}</td>"
                f'<td class="value">{diet_str}</td>'
                f'<td class="value">{min_str}</td>'
                f'<td class="value">{pct_str}</td>'
                f'<td class="status status-{status_class}">{status_icon}</td></tr>'
            )

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AAFCO Cat Adult Maintenance — Complete Nutrient Analysis</title>
  <style>
    body {{ font-family:'Segoe UI',sans-serif; margin:0; background:linear-gradient(135deg,#667eea,#764ba2); min-height:100vh; padding:20px; }}
    .container {{ max-width:1100px; margin:0 auto; background:#fff; border-radius:12px; padding:30px; box-shadow:0 10px 40px rgba(0,0,0,0.2); }}
    h1 {{ color:#2c3e50; font-size:26px; margin-bottom:8px; }}
    .subtitle {{ color:#7f8c8d; font-size:13px; margin-bottom:20px; }}
    .chips {{ margin-bottom:22px; display:flex; flex-wrap:wrap; gap:8px; }}
    .chip {{ background:linear-gradient(135deg,#667eea,#764ba2); color:#fff; padding:5px 13px; border-radius:20px; font-size:12px; font-weight:500; }}
    table {{ width:100%; border-collapse:collapse; margin-top:18px; box-shadow:0 2px 8px rgba(0,0,0,0.1); }}
    th {{ background:linear-gradient(135deg,#667eea,#764ba2); color:#fff; padding:12px 8px; text-align:left; font-size:13px; font-weight:600; }}
    td {{ padding:9px 8px; border-bottom:1px solid #ecf0f1; font-size:13px; }}
    tr:hover td {{ background:#f8f9fa; }}
    .category-header td {{ background:linear-gradient(to right,#f8f9fa,#e9ecef); font-weight:700; font-size:13px; color:#495057; padding:10px 8px; border-top:2px solid #dee2e6; border-bottom:2px solid #dee2e6; }}
    .category-header:hover td {{ background:linear-gradient(to right,#f8f9fa,#e9ecef); }}
    .value {{ text-align:center; font-family:'Courier New',monospace; font-weight:500; }}
    .status {{ text-align:center; font-size:17px; }}
    .status-success {{ color:#27ae60; }}
    .status-warning {{ color:#f39c12; }}
    .status-danger  {{ color:#e74c3c; }}
    .status-neutral {{ color:#95a5a6; }}
    .legend {{ margin-top:28px; padding:18px; background:#f8f9fa; border-radius:8px; border-left:4px solid #667eea; }}
    .legend-title {{ font-weight:700; margin-bottom:10px; color:#2c3e50; }}
    .legend-items {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }}
    .legend-item {{ display:flex; align-items:center; gap:8px; font-size:13px; }}
    .note {{ margin-top:18px; padding:14px; background:#fff3cd; border-left:4px solid #ffc107; border-radius:4px; font-size:13px; color:#856404; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🐱 AAFCO Cat Adult Maintenance — Complete Nutrient Analysis</h1>
    <div class="subtitle">All tracked nutrients compared against AAFCO 2024 cat adult maintenance standards</div>
    <div class="chips">{"".join(f'<span class="chip">{ing}</span>' for ing in ingredients)}</div>
    <table>
      <thead>
        <tr>
          <th>Nutrient</th><th>Unit</th><th>Diet Value</th><th>AAFCO Min</th><th>% of Min</th><th>Status</th>
        </tr>
      </thead>
      <tbody>{table_html}</tbody>
    </table>
    <div class="legend">
      <div class="legend-title">Status Legend</div>
      <div class="legend-items">
        <div class="legend-item"><span class="status-success" style="font-size:18px">✓</span> Meets AAFCO minimum</div>
        <div class="legend-item"><span class="status-warning" style="font-size:18px">⚠</span> Within 10% of minimum</div>
        <div class="legend-item"><span class="status-danger"  style="font-size:18px">✗</span> Below AAFCO minimum</div>
        <div class="legend-item"><span class="status-neutral" style="font-size:18px">—</span> No minimum defined</div>
      </div>
    </div>
    <div class="note"><strong>Note:</strong> Values per 1000g (1kg) dry matter. AAFCO 2024 cat adult maintenance standards. Nutrients marked "—" have no established minimum.</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# /calculate — JSON + auto-opens report
# -------------------------------------------------------------------
@router.post("/calculate")
def calculate(req: IngredientRequest, request: Request):
    result = svc.calculate_diet(req.ingredients)
    import math

    def sanitize(obj):
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [sanitize(i) for i in obj]
        return obj

    report_url = aafco_url = ingredients_summary_url = None
    if req.ingredients:
        query = urlencode([("ingredients", n) for n in req.ingredients])
        report_url              = f"{request.url_for('diet_report_get')}?{query}"
        aafco_url               = f"{request.url_for('diet_aafco_full_get')}?{query}"
        ingredients_summary_url = f"{request.url_for('ingredients_summary_get')}?{query}"
        try:
            webbrowser.open_new_tab(str(report_url))
        except Exception as e:
            print(f"[Diet] Could not auto-open browser: {e}")

    result["report_url"]              = report_url
    result["aafco_url"]               = aafco_url
    result["ingredients_summary_url"] = ingredients_summary_url
    return sanitize(result)
