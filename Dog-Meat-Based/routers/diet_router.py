"""
Dog Diet Planner - API Router (UPDATED)

This router provides endpoints for:
- Ingredient management and selection
- Diet calculation with fixed allocation rules
- HTML reports (diet composition, AAFCO compliance)
- AAFCO Adult Maintenance compliance checking
- Ingredients Summary with fresh weights

FIXED ALLOCATION RULES (must total 1000g DM):
- Meat: 200g (A only: 200g | A+B: 150+50 | A+B+C: 110+40+50)
- Grains: 350g (A only: 350g | A+B: 250+100) — Grain B reduced by 50g if potato selected
- Vegetable C (Potato): 50g optional — taken from Grain B
- Liver: 155g alone / 125g with organ (organ gets 30g)
- Vegetables: 130g (A:90 + B:40) — reduced to 120g if seeds selected (A:85 + B:35)
- Fruits: 25g, Oil: 10g
- Seeds (optional): 10g (reduces veg by 10g total)

DIET QUALITY TARGETS:
- Protein %: 20–65%, Fat %: >11% <22%, CHO %: <23%, Fiber %: >3% <6.5%
- Energy: 4000–4800 kcal/kg, Ca:P: >1.4:1 <2:1, Omega-6:Omega-3: >2 <6

UPDATED: Uses corrected CSVs with proper folate/B12 and updated eggshells/wheatgerm.
"""

from typing import List
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from urllib.parse import urlencode
import webbrowser
import json

from services import ingredients as svc

router = APIRouter()


# ---------------------------------------------------------------------------
# Ingredient name display formatter
# ---------------------------------------------------------------------------
_LOWERCASE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "into", "no", "nor", "of", "on", "or", "raw", "so", "the",
    "to", "up", "via", "with", "yet",
}
_UPPERCASE_WORDS = {"dha", "epa", "pufa"}


def _fmt_word(word: str, first: bool) -> str:
    lower = word.lower()
    if lower in _UPPERCASE_WORDS:
        return word.upper()
    if not first and lower in _LOWERCASE_WORDS:
        return lower
    if word.startswith("(") and len(word) > 1:
        return "(" + _fmt_word(word[1:], True)
    if word.endswith(")") and len(word) > 1:
        return _fmt_word(word[:-1], first) + ")"
    if word and (word[0].isdigit() or word[0] == "%"):
        return word
    return word.capitalize()


def format_ingredient_name(name: str) -> str:
    """
    Return a clean, consistently title-cased ingredient name for display.
    Strips trailing/leading spaces. Keeps small words (raw, with, of…) lowercase
    mid-name. Handles parentheses like (90% Lean) correctly.

    Examples:
        'turkey breast only'   → 'Turkey Breast Only'
        'eggshells '           → 'Eggshells'
        'salmon raw pink'      → 'Salmon raw Pink'
        'Beef, (90% Lean)'     → 'Beef, (90% Lean)'
        'bone meal '           → 'Bone Meal'
    """
    if not name:
        return name
    tokens = name.strip().split()
    return " ".join(_fmt_word(t, i == 0) for i, t in enumerate(tokens))


class IngredientRequest(BaseModel):
    ingredients: List[str]


# -------------------------------------------------------------------
# User ingredients, grouped + ordered (with numeric prefixes)
# -------------------------------------------------------------------
@router.get("/user-ingredients")
def user_ingredients():
    """
    Returns a flat list of ingredients with their display group name.
    Frontend sorts by group_name, so we encode numeric prefixes
    (01, 02, ...) into the group_name itself.
    """
    grouped = svc.get_ingredients_grouped_by_category()

    # Desired logical order
    category_order = [
        "Meat Group A",
        "Meat Group B",
        "Meat Group C",
        "Organ Meat (Other)",
        "Organ Meat (Liver)",
        "Grain A",
        "Vegetable A",
        "Vegetable B",
        "Fruit",
        "Oil",
        "Fiber",
        "Mineral Group A",
        "Mineral Group B",
    ]

    # Map internal category name -> display name with numeric prefix and requirements
    category_display = {
        "Meat Group A":        "01 Meat Group A (Mandatory - Select at least one and a maximum of three)",
        "Meat Group B":        "02 Meat Group B (Optional - Pick up to two)",
        "Meat Group C":        "03 Meat Group C (Optional - Pick up to two)",
        "Organ Meat (Other)":  "04 Organ Meat - Other (Optional - Pick up to three)",
        "Organ Meat (Liver)":  "05 Organ Meat - Liver (Mandatory - Pick up to 2)",
        "Grain A":             "06 Grains (Optional - Select up to three maximum)",
        "Vegetable A":         "07 Vegetable A (Mandatory - Select at least one and up to three maximum)",
        "Vegetable B":         "08 Vegetable B (Optional - Pick up to three maximum)",
        "Fruit":               "09 Fruit (Mandatory - Select at least one and up to three maximum)",
        "Oil":                 "10 Oil (Mandatory - Select at least one and a maximum of three)",
        "Fiber":               "11 Fiber/Seeds (Optional - Pick up to two maximum)",
        "Mineral Group A":     "14 Mineral Group A (Mandatory)",
        "Mineral Group B":     "15 Mineral Group B (Optional)",
    }

    result = []

    for cat in category_order:
        if cat not in grouped:
            continue
        display_name = category_display.get(cat, cat)
        for item in grouped[cat]["items"]:
            result.append(
                {
                    "ingredient_name": item["ingredient_name"],
                    "group_name": display_name,
                }
            )

    # Cached for an hour at Vercel's CDN/edge — this list only changes when the
    # CSVs are updated and the app is redeployed, so there's no reason for every
    # page load to re-run this function. stale-while-revalidate means a visitor
    # never waits on a cold start even right after the cache expires: they get
    # the (very slightly) stale cached copy instantly while Vercel refreshes it
    # in the background for the next request.
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"},
    )


@router.get("/ingredients-grouped")
def ingredients_grouped():
    """
    Returns ingredients grouped by category with metadata.
    Used if frontend wants the full grouped structure.
    """
    return svc.get_ingredients_grouped_by_category()


@router.post("/reload")
def reload_csvs():
    """Reload CSVs into memory."""
    return svc.load_csvs()


# -------------------------------------------------------------------
# HTML Diet Report (table + pie chart + link to AAFCO page)
# -------------------------------------------------------------------
@router.post("/diet-report", response_class=HTMLResponse)
def diet_report_post(
    request: Request,
    body: IngredientRequest,
):
    """POST endpoint for diet report (used by frontend)"""
    return _generate_diet_report(request, body.ingredients)


@router.get("/diet-report", response_class=HTMLResponse)
def diet_report_get(
    request: Request,
    ingredients: List[str] = Query(...),
):
    """GET endpoint for diet report (used for auto-opening in browser)"""
    return _generate_diet_report(request, ingredients)


def _generate_diet_report(request: Request, ingredients: List[str]):
    """
    HTML report for a given list of ingredients:
      - Diet Composition, % of DM (Energy, Protein %, Fat %, CHO %, Ash %, Fiber %, Ca:P, Omega-6:Omega-3)
      - Pie chart (Protein / Fat / CHO % of DM) with % labels
      - AAFCO Adult maintenance comparison (key nutrients)
      - Link to full AAFCO Adult maintenance comparison (opens in new tab)
      - Link to Ingredients Summary (opens in new tab)
    """
    result = svc.calculate_diet(ingredients)

    # Macro-level values
    energy = float(result.get("Energy", 0.0))
    protein_pct = float(result.get("Protein_percent", 0.0))
    fat_pct = float(result.get("Fat_percent", 0.0))
    cho_pct = float(result.get("CHO_percent", 0.0))
    ash_pct = float(result.get("Ash_percent", 0.0))
    fiber_pct = float(result.get("Fiber_percent", 0.0))
    ca_p_ratio = float(result.get("Ca_P_ratio", 0.0))
    ca_pct = float(result.get("Ca_percent", 0.0))   # % of DM
    iron_mg = float(result.get("iron_mg", 0.0))     # mg/kg DM

    omega_ratio = (
        result.get("omega6_omega3_ratio")
        or result.get("Omega6_omega3_ratio")
        or result.get("Omega_6_3_ratio")
        or result.get("Omega_ratio")
        or None
    )

    # Get allocation summary info
    allocation_summary = result.get("allocation_summary", {})
    
    # Get issues/warnings from backend
    issues = result.get("issues", [])

    # ============================================================================
    # PIE CHART: Calculate % of ENERGY from each macronutrient
    # Formula from Excel:
    #   Protein % of Energy = ((4 * Protein_pct) / Energy) * 1000
    #   Fat % of Energy = ((9 * Fat_pct) / Energy) * 1000
    #   CHO % of Energy = ((4 * CHO_pct) / Energy) * 1000
    # Where: Protein=4 kcal/g, Fat=9 kcal/g, CHO=4 kcal/g
    # ============================================================================
    if energy > 0:
        protein_energy_pct = ((4 * protein_pct) / energy) * 1000
        fat_energy_pct = ((9 * fat_pct) / energy) * 1000
        cho_energy_pct = ((4 * cho_pct) / energy) * 1000
    else:
        protein_energy_pct = 0
        fat_energy_pct = 0
        cho_energy_pct = 0

    # Get AAFCO % of minimum from backend (if available)
    aafco_pct = result.get("aafco_percent_of_minimum", {})

    # ---- AAFCO Adult maintenance (dog) specs for the nutrients we track ----
    # Now using aafco_percent_of_minimum from backend where available
    # Note: Protein target is 32-40% (enforced by allocation algorithm)
    aafco_specs = [
        {
            "label": "Protein (% of DM)",
            "diet_value": protein_pct,
            "min_value": 32.0,  # Target: 32-40%
            "pct_of_min": aafco_pct.get("protein"),
        },
        {
            "label": "Fat (% of DM)",
            "diet_value": fat_pct,
            "min_value": 5.5,
            "pct_of_min": aafco_pct.get("fat"),
        },
        {
            "label": "Ash (% of DM)",
            "diet_value": ash_pct,
            "min_value": None,
            "pct_of_min": None,
        },
        {
            "label": "CHO (% of DM)",
            "diet_value": cho_pct,
            "min_value": None,
            "pct_of_min": None,
        },
        {
            "label": "Fiber (% of DM)",
            "diet_value": fiber_pct,
            "min_value": None,
            "pct_of_min": None,
        },
        {
            "label": "Calcium (% of DM)",
            "diet_value": ca_pct,
            "min_value": 0.6,
            "pct_of_min": aafco_pct.get("calcium"),
        },
        {
            "label": "Iron (mg/kg DM)",
            "diet_value": iron_mg,
            "min_value": 40.0,
            "pct_of_min": aafco_pct.get("iron"),
        },
    ]

    # Build HTML rows for AAFCO table (with % of minimum)
    aafco_rows_html = ""
    for spec in aafco_specs:
        diet_val = spec["diet_value"]
        min_val = spec["min_value"]
        pct_min_from_backend = spec.get("pct_of_min")

        # Use backend value if available, otherwise calculate
        if pct_min_from_backend is not None:
            pct_min = pct_min_from_backend * 100.0  # Convert ratio to percentage
            pct_min_str = f"{pct_min:.2f}%"
        elif min_val is not None and min_val > 0:
            pct_min = diet_val / min_val * 100.0
            pct_min_str = f"{pct_min:.2f}%"
        else:
            pct_min_str = "—"
        
        min_val_str = f"{min_val:.2f}" if min_val is not None else "—"

        aafco_rows_html += (
            f"<tr>"
            f"<td>{spec['label']}</td>"
            f"<td>{diet_val:.2f}</td>"
            f"<td>{min_val_str}</td>"
            f"<td>{pct_min_str}</td>"
            f"</tr>"
        )

    # Build JavaScript for POSTing to full AAFCO page and Ingredients Summary
    ingredients_json = json.dumps(ingredients)  # Proper JSON conversion
    aafco_url = str(request.url_for("diet_aafco_full"))
    ingredients_summary_url = str(request.url_for("ingredients_summary"))

    # HTML page
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Diet Report</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 20px;
      background-color: #f6f6f6;
    }}
    h1 {{
      margin-bottom: 0.5rem;
    }}
    h2 {{
      margin-top: 2rem;
      margin-bottom: 0.5rem;
    }}
    .layout {{
      display: flex;
      gap: 24px;
      align-items: flex-start;
      flex-wrap: wrap;
    }}
    table {{
      border-collapse: collapse;
      background: #fff;
      box-shadow: 0 2px 6px rgba(0,0,0,0.08);
      border-radius: 6px;
      overflow: hidden;
      min-width: 380px;
    }}
    th, td {{
      padding: 8px 12px;
      border-bottom: 1px solid #e1e1e1;
      font-size: 14px;
    }}
    th {{
      background-color: #f0f0f0;
      text-align: left;
      font-weight: 600;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    .metric-label {{
      font-weight: 600;
    }}
    #chartContainer {{
      background: #fff;
      padding: 16px;
      border-radius: 6px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.08);
    }}
    #macroPie {{
      max-width: 360px;
      max-height: 360px;
    }}
    .chips {{
      margin-top: 8px;
      margin-bottom: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .chip {{
      background: #e3f2fd;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
    }}
    .small-note {{
      margin-top: 8px;
      font-size: 12px;
      color: #555;
    }}
    .btn-link {{
      margin-top: 16px;
      margin-right: 12px;
      display: inline-block;
      padding: 8px 14px;
      border-radius: 4px;
      background-color: #1976d2;
      color: #fff;
      text-decoration: none;
      font-size: 14px;
      border: none;
      cursor: pointer;
    }}
    .btn-link:hover {{
      background-color: #12579b;
    }}
    .btn-link.green {{
      background-color: #2e7d32;
    }}
    .btn-link.green:hover {{
      background-color: #1b5e20;
    }}
    .button-container {{
      margin-top: 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
  </style>
</head>
<body>
  <h1>Diet Composition & Macros</h1>

  <div class="chips">
    {"".join(f'<span class="chip">{format_ingredient_name(ing)}</span>' for ing in ingredients)}
  </div>

  <div class="layout">
    <table>
      <thead>
        <tr>
          <th>Metric</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="metric-label">Energy, kcal/kg</td>
          <td>{energy:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">Protein, % of DM</td>
          <td>{protein_pct:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">Fat, % of DM</td>
          <td>{fat_pct:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">CHO, % of DM</td>
          <td>{cho_pct:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">Ash, % of DM</td>
          <td>{ash_pct:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">Fiber, % of DM</td>
          <td>{fiber_pct:.2f}</td>
        </tr>
        <tr>
          <td class="metric-label">Ca:P ratio</td>
          <td>{ca_p_ratio:.2f}</td>
        </tr>
        {"<tr><td class='metric-label'>Omega-6:Omega-3</td><td>" + f"{omega_ratio:.2f}" + "</td></tr>" if omega_ratio is not None else ""}
      </tbody>
    </table>

    <div id="chartContainer">
      <h2>Macro Pie Chart (% of Energy)</h2>
      <canvas id="macroPie"></canvas>
      <div class="small-note">
        Showing energy contribution from Protein (4 kcal/g), Fat (9 kcal/g), CHO (4 kcal/g).
      </div>
    </div>
  </div>

  <h2>AAFCO Adult maintenance comparison</h2>
  <table>
    <thead>
      <tr>
        <th>Nutrient</th>
        <th>Diet value</th>
        <th>AAFCO min</th>
        <th>% of minimum</th>
      </tr>
    </thead>
    <tbody>
      {aafco_rows_html}
    </tbody>
  </table>

  <div class="button-container">
    <button class="btn-link" onclick="openAAFCOFull()">
      Open full AAFCO Adult maintenance table
    </button>
    <button class="btn-link green" onclick="openIngredientsSummary()">
      View Ingredients Summary (Fresh Weights)
    </button>
  </div>
  
  <script>
  async function openAAFCOFull() {{
    try {{
      const ingredients = {ingredients_json};
      
      console.log('Opening AAFCO with ingredients:', ingredients);
      
      const response = await fetch('{aafco_url}', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
        }},
        body: JSON.stringify({{ ingredients: ingredients }})
      }});
      
      if (!response.ok) {{
        throw new Error('Failed to fetch AAFCO report: ' + response.statusText);
      }}
      
      const html = await response.text();
      const newWindow = window.open('', '_blank');
      
      if (!newWindow) {{
        alert('Please allow pop-ups for this site to open the AAFCO report');
        return;
      }}
      
      newWindow.document.write(html);
      newWindow.document.close();
    }} catch (error) {{
      console.error('Error opening AAFCO report:', error);
      alert('Error opening AAFCO report: ' + error.message);
    }}
  }}
  
  async function openIngredientsSummary() {{
    try {{
      const ingredients = {ingredients_json};
      
      console.log('Opening Ingredients Summary with ingredients:', ingredients);
      
      const response = await fetch('{ingredients_summary_url}', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
        }},
        body: JSON.stringify({{ ingredients: ingredients }})
      }});
      
      if (!response.ok) {{
        throw new Error('Failed to fetch Ingredients Summary: ' + response.statusText);
      }}
      
      const html = await response.text();
      const newWindow = window.open('', '_blank');
      
      if (!newWindow) {{
        alert('Please allow pop-ups for this site to open the Ingredients Summary');
        return;
      }}
      
      newWindow.document.write(html);
      newWindow.document.close();
    }} catch (error) {{
      console.error('Error opening Ingredients Summary:', error);
      alert('Error opening Ingredients Summary: ' + error.message);
    }}
  }}
  </script>

  <script>
    const ctx = document.getElementById('macroPie').getContext('2d');
    new Chart(ctx, {{
      type: 'pie',
      data: {{
        labels: ['Protein', 'Fat', 'CHO'],
        datasets: [{{
          data: [{protein_energy_pct:.2f}, {fat_energy_pct:.2f}, {cho_energy_pct:.2f}],
          backgroundColor: [
            '#4CAF50',
            '#FF9800',
            '#2196F3'
          ],
          borderColor: '#FFFFFF',
          borderWidth: 2
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ position: 'bottom' }},
          title: {{ display: true, text: 'Energy Contribution (% of Total kcal)' }},
          datalabels: {{
            formatter: (value) => value.toFixed(1) + '%',
            color: '#ffffff',
            font: {{
              weight: 'bold'
            }}
          }}
        }}
      }},
      plugins: [ChartDataLabels]
    }});
  </script>
</body>
</html>
    """

    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# ✨ NEW: Ingredients Summary page with fresh weights
# -------------------------------------------------------------------
@router.post("/ingredients-summary", response_class=HTMLResponse, name="ingredients_summary")
def ingredients_summary_post(body: IngredientRequest):
    """POST endpoint for Ingredients Summary (used by frontend)"""
    return _generate_ingredients_summary(body.ingredients)


@router.get("/ingredients-summary", response_class=HTMLResponse)
def ingredients_summary_get(ingredients: List[str] = Query(...)):
    """GET endpoint for Ingredients Summary (used for auto-opening in browser)"""
    return _generate_ingredients_summary(ingredients)


def _generate_ingredients_summary(ingredients: List[str]):
    """
    HTML page showing ingredients with their fresh weights.
    Displays a clean table with ingredient names and amounts in grams.
    
    Fresh weight is calculated from dry matter using:
        fresh_weight = dm_weight / (1 - water_percent/100)
    """
    result = svc.calculate_diet(ingredients)
    
    # Get ingredient allocations from the result (includes fresh weights)
    allocations = result.get("ingredient_allocations", {})
    
    # Also get dm_breakdown which has all ingredients including fixed ones
    dm_breakdown = result.get("dm_breakdown", [])
    
    # Color coding for certain categories (meats, organs, etc.)
    color_map = {
        "salmon": "#FFFF00",       # Yellow - fish
        "beef liver": "#00B050",   # Green - liver
        "pork loin": "#00B0F0",    # Blue - pork
        "chicken": "#00B0F0",      # Blue - poultry
        "turkey": "#00B0F0",       # Blue - poultry
        "liver": "#00B050",        # Green - any liver
    }
    
    total_fresh_weight = 0.0
    total_dm_weight = 0.0
    rows_html = ""
    
    # First, add all ingredients from dm_breakdown (which includes fixed ingredients)
    seen_ingredients = set()
    
    for item in dm_breakdown:
        ingredient_name = item.get("ingredient", "")
        if not ingredient_name or ingredient_name in seen_ingredients:
            continue
        seen_ingredients.add(ingredient_name)
        
        dm_g = float(item.get("dm_g", 0))
        fresh_weight = float(item.get("fresh_weight_g", 0))
        water_pct = float(item.get("water_percent", 0))
        is_fixed = item.get("fixed", False)
        
        # If fresh_weight not in breakdown, check allocations
        if fresh_weight == 0 and ingredient_name in allocations:
            alloc = allocations[ingredient_name]
            if isinstance(alloc, dict):
                fresh_weight = float(alloc.get("fresh_weight_g", 0))
                water_pct = float(alloc.get("water_percent", 0))
        
        total_fresh_weight += fresh_weight
        total_dm_weight += dm_g
        
        # Check for color coding
        ing_lower = ingredient_name.lower()
        bg_color = ""
        for key, color in color_map.items():
            if key in ing_lower:
                bg_color = f"background-color: {color};"
                break
        
        # Mark fixed ingredients with a subtle indicator
        fixed_marker = " (fixed)" if is_fixed else ""
        
        rows_html += f"""
        <tr style="{bg_color}">
            <td>{ingredient_name}{fixed_marker}</td>
            <td class="value">{dm_g:.1f}</td>
            <td class="value">{fresh_weight:.1f}</td>
        </tr>
        """
    
    # Add total row
    rows_html += f"""
    <tr class="total-row">
        <td><strong>Total</strong></td>
        <td class="value"><strong>{total_dm_weight:.1f}</strong></td>
        <td class="value"><strong>{total_fresh_weight:.1f}</strong></td>
    </tr>
    """

    # Diet energy (kcal/kg DM) — used for the Daily Feeding Plan tab
    diet_energy_kcal_per_kg = float(result.get("Energy", 0.0))

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Ingredients Summary - Fresh Weights</title>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      margin: 20px;
      background: linear-gradient(135deg, #2e7d32 0%, #43a047 100%);
      min-height: 100vh;
      padding: 20px;
    }}
    .container {{
      max-width: 860px;
      margin: 0 auto;
      background: white;
      border-radius: 12px;
      padding: 30px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.2);
    }}
    h1 {{
      color: #2c3e50;
      margin-bottom: 6px;
      font-size: 28px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .subtitle {{
      color: #7f8c8d;
      margin-bottom: 18px;
      font-size: 14px;
    }}
    .tab-bar {{
      display: flex;
      border-bottom: 2px solid #2e7d32;
      margin-bottom: 22px;
      gap: 4px;
    }}
    .tab-btn {{
      padding: 9px 22px;
      border: 2px solid transparent;
      border-bottom: none;
      border-radius: 8px 8px 0 0;
      background: #f1f8f1;
      color: #2e7d32;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      margin-bottom: -2px;
      transition: background 0.15s;
    }}
    .tab-btn:hover {{ background: #d0ecd0; }}
    .tab-btn.active {{
      background: white;
      border-color: #2e7d32;
      color: #1b5e20;
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 16px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
      border-radius: 8px;
      overflow: hidden;
    }}
    th {{
      background: linear-gradient(135deg, #2e7d32 0%, #43a047 100%);
      color: white;
      padding: 13px 16px;
      text-align: left;
      font-size: 13px;
      font-weight: 600;
    }}
    td {{
      padding: 11px 16px;
      border-bottom: 1px solid #ecf0f1;
      font-size: 14px;
    }}
    tr:hover td {{ background-color: #f8f9fa; }}
    .value {{
      text-align: right;
      font-family: 'Courier New', monospace;
      font-weight: 500;
    }}
    .total-row {{ background-color: #e8f5e9; border-top: 2px solid #2e7d32; }}
    .total-row td {{ border-bottom: none; }}
    .note {{
      margin-top: 18px;
      padding: 14px;
      background: #e8f5e9;
      border-left: 4px solid #2e7d32;
      border-radius: 4px;
      font-size: 13px;
      color: #1b5e20;
    }}
    .formula {{
      margin-top: 12px;
      padding: 12px;
      background: #fff8e1;
      border-left: 4px solid #ff9800;
      border-radius: 4px;
      font-size: 12px;
      color: #e65100;
    }}
    .formula code {{
      background: #ffecb3;
      padding: 2px 6px;
      border-radius: 3px;
      font-family: 'Courier New', monospace;
    }}
    .print-btn {{
      margin-top: 18px;
      padding: 10px 20px;
      background: linear-gradient(135deg, #2e7d32 0%, #43a047 100%);
      color: white;
      border: none;
      border-radius: 6px;
      font-size: 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .print-btn:hover {{ background: linear-gradient(135deg, #1b5e20 0%, #2e7d32 100%); }}
    .energy-panel {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .energy-card {{
      background: #f4faf4;
      border: 1px solid #c8e6c9;
      border-radius: 8px;
      padding: 16px 18px;
    }}
    .energy-card label {{
      display: block;
      font-size: 12px;
      color: #555;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 8px;
    }}
    .energy-card input {{
      width: 100%;
      padding: 9px 10px;
      font-size: 18px;
      border: 1.5px solid #a5d6a7;
      border-radius: 6px;
      box-sizing: border-box;
      font-family: 'Courier New', monospace;
      font-weight: 700;
      color: #1b5e20;
    }}
    .derived-row {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      background: #f4faf4;
      border: 1px solid #c8e6c9;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 20px;
    }}
    .derived-label {{
      font-size: 11px;
      color: #777;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }}
    .derived-val {{
      font-size: 17px;
      font-weight: 700;
      color: #2e7d32;
      font-family: 'Courier New', monospace;
    }}
    .plan-title {{
      font-size: 15px;
      font-weight: 700;
      color: #2c3e50;
      margin: 18px 0 4px 0;
    }}
    .days-table th {{ text-align: right; }}
    .days-table th:first-child {{ text-align: left; }}
    .days-table td {{ text-align: right; }}
    .days-table td:first-child {{ text-align: left; font-weight: 500; }}
    @media print {{
      body {{ background: white; padding: 0; }}
      .container {{ box-shadow: none; padding: 0; }}
      .print-btn, .tab-bar {{ display: none; }}
      .tab-panel {{ display: block !important; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🥗 Ingredients Summary</h1>
    <div class="subtitle">Fresh weights and daily feeding plan for your homemade dog diet recipe</div>

    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('summary', this)">📋 Ingredients Summary</button>
      <button class="tab-btn" onclick="switchTab('feeding', this)">🐾 Daily Feeding Plan</button>
    </div>

    <!-- Tab 1: Ingredients Summary -->
    <div id="tab-summary" class="tab-panel active">
      <table>
        <thead>
          <tr>
            <th>Ingredient</th>
            <th style="text-align:right">Dry Matter (g)</th>
            <th style="text-align:right">Fresh Weight (g)</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>

      <div class="note">
        <strong>Note:</strong> All fresh weights are in grams of raw ingredients before cooking.
        Weigh ingredients before cooking. Total batch fresh weight: <strong>{total_fresh_weight:.1f}g</strong>
      </div>

      <div class="formula">
        <strong>Formula:</strong> <code>Fresh Weight = Dry Matter ÷ (1 - Water%/100)</code><br>
        Example: Salmon with 75.5% water: 139.65g DM → 139.65 ÷ 0.245 = 570g fresh
      </div>

      <button class="print-btn" onclick="window.print()">🖨️ Print Recipe</button>
    </div>

    <!-- Tab 2: Daily Feeding Plan -->
    <div id="tab-feeding" class="tab-panel">

      <div class="energy-panel">
        <div class="energy-card">
          <label>🐕 Dog Daily Energy Need (kcal)</label>
          <input type="number" id="dogEnergy" min="1" step="1" placeholder="e.g. 546"
                 oninput="recalc()" />
        </div>
        <div class="energy-card">
          <label>⚡ Diet Energy — AAFCO (kcal/kg DM)</label>
          <input type="number" id="dietEnergy" min="1" step="1"
                 value="{diet_energy_kcal_per_kg:.0f}" oninput="recalc()" />
        </div>
      </div>

      <div class="derived-row" id="derivedRow">
        <div class="derived-item">
          <div class="derived-label">Dog daily energy need</div>
          <div class="derived-val" id="dv_dogEnergy">—</div>
        </div>
        <div class="derived-item">
          <div class="derived-label">Diet energy (AAFCO report)</div>
          <div class="derived-val" id="dv_dietEnergy">{diet_energy_kcal_per_kg:.0f} kcal/kg</div>
        </div>
        <div class="derived-item">
          <div class="derived-label">Daily food intake (g DM)</div>
          <div class="derived-val" id="dv_dailyIntake">—</div>
        </div>
        <div class="derived-item">
          <div class="derived-label">% of food intake of total mix</div>
          <div class="derived-val" id="dv_pct">—</div>
        </div>
      </div>

      <div id="planSection" style="display:none">
        <div class="plan-title">🥩 Fresh weight to serve per batch (g)</div>
        <table class="days-table">
          <thead>
            <tr>
              <th>Ingredient</th>
              <th>1 day</th>
              <th>3 days</th>
              <th>5 days</th>
              <th>7 days</th>
              <th>10 days</th>
            </tr>
          </thead>
          <tbody id="planBodyFresh"></tbody>
        </table>

        <div class="note" style="margin-top:14px">
          <strong>How this works:</strong>
          Daily DM intake (g) = Dog energy ÷ Diet energy × 1000.
          Fresh weight = DM ÷ (1 − water%/100). Multiply by days for each batch size.
        </div>
      </div>

      <div id="energyHint" style="margin-top:18px;color:#888;font-size:13px;">
        ↑ Enter your dog's daily energy need above to generate the feeding plan.
      </div>
    </div>

  </div>

<script>
  function switchTab(id, btn) {{
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    btn.classList.add('active');
  }}

  const DIET_ENERGY = {diet_energy_kcal_per_kg:.6f};
  const TOTAL_DM    = {total_dm_weight:.6f};

  const INGREDIENTS = [
{chr(10).join(
    '    ["' + item.get("ingredient","").replace(chr(34), chr(92)+chr(34)) + '", '
    + str(round(float(item.get("dm_g",0)),4)) + ', '
    + str(round(float(item.get("fresh_weight_g",0)),4)) + ', '
    + str(round(float(item.get("water_percent",0)),4)) + '],'
    for item in dm_breakdown if item.get("ingredient","")
)}
  ];

  function recalc() {{
    const dogEnergy  = parseFloat(document.getElementById('dogEnergy').value);
    const dietEnergy = parseFloat(document.getElementById('dietEnergy').value);
    const hint = document.getElementById('energyHint');
    const plan  = document.getElementById('planSection');

    if (!dogEnergy || dogEnergy <= 0 || !dietEnergy || dietEnergy <= 0) {{
      document.getElementById('dv_dogEnergy').textContent  = '—';
      document.getElementById('dv_dailyIntake').textContent = '—';
      document.getElementById('dv_pct').textContent         = '—';
      plan.style.display = 'none';
      hint.style.display = 'block';
      return;
    }}

    const dailyDM = (dogEnergy / dietEnergy) * 1000.0;
    const pct     = (dailyDM / TOTAL_DM) * 100.0;

    document.getElementById('dv_dogEnergy').textContent   = dogEnergy.toFixed(0) + ' kcal';
    document.getElementById('dv_dietEnergy').textContent  = dietEnergy.toFixed(0) + ' kcal/kg';
    document.getElementById('dv_dailyIntake').textContent = dailyDM.toFixed(1) + ' g';
    document.getElementById('dv_pct').textContent         = pct.toFixed(2) + '%';
    hint.style.display = 'none';

    const days = [1, 3, 5, 7, 10];
    let tbodyFresh = '';
    const totalsFresh = [0,0,0,0,0];

    for (const [name, dm_g, fresh_g, water_pct] of INGREDIENTS) {{
      if (dm_g <= 0) continue;
      const frac          = dm_g / TOTAL_DM;
      const ingDailyDM    = frac * dailyDM;
      const wf            = water_pct / 100.0;
      let ingDailyFresh   = wf < 1.0 ? ingDailyDM / (1.0 - wf) : ingDailyDM;
      // EXCEPTION: Oyster canned applies proportional scaling of 10/14.9 for this
      // batch table only. water_pct (85.1) is read and used completely unchanged;
      // the CSV is never modified. Scales the result to reflect an 11g DM basis
      // instead of 14.9g, while remaining dynamic with the dog's energy needs.
      if (name === "oyster canned") {{
        ingDailyFresh = ingDailyFresh * (10.0 / 14.9);
      }}

      let rowFresh = `<tr><td>${{name}}</td>`;
      days.forEach((d, i) => {{
        const vf = ingDailyFresh * d;
        totalsFresh[i] += vf;
        rowFresh += `<td>${{vf.toFixed(1)}}</td>`;
      }});
      tbodyFresh += rowFresh + '</tr>';
    }}

    let trFresh = '<tr class="total-row"><td><strong>Total (g)</strong></td>';
    totalsFresh.forEach(t => {{ trFresh += `<td><strong>${{t.toFixed(1)}}</strong></td>`; }});
    tbodyFresh += trFresh + '</tr>';

    document.getElementById('planBodyFresh').innerHTML = tbodyFresh;
    plan.style.display = 'block';
  }}
</script>
</body>
</html>
    """

    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# ✨ UPDATED: Full AAFCO Adult maintenance page with ALL 43 nutrients
# Now uses aafco_percent_of_minimum from backend
# -------------------------------------------------------------------
@router.post("/diet-aafco-full", response_class=HTMLResponse, name="diet_aafco_full")
def diet_aafco_full_post(body: IngredientRequest):
    """POST endpoint for AAFCO report (used by frontend)"""
    return _generate_aafco_report(body.ingredients)


@router.get("/diet-aafco-full", response_class=HTMLResponse)
def diet_aafco_full_get(ingredients: List[str] = Query(...)):
    """GET endpoint for AAFCO report (used for auto-opening in browser)"""
    return _generate_aafco_report(ingredients)


def _generate_aafco_report(ingredients: List[str]):
    """
    Detailed AAFCO Adult maintenance comparison with ALL 43 nutrients.
    
    UPDATED: Now uses the aafco_percent_of_minimum field from the backend
    instead of manually calculating percentages.
    """
    result = svc.calculate_diet(ingredients)

    # Get AAFCO % of minimum from backend
    aafco_pct = result.get("aafco_percent_of_minimum", {})
    
    # Helper to safely get values
    def get_val(key, default=0.0):
        val = result.get(key, default)
        return float(val) if val is not None else default

    # Helper to get % of minimum from backend (returns ratio, multiply by 100 for display)
    def get_pct(key):
        val = aafco_pct.get(key)
        return val if val is not None else None

    # ==================== DEFINE ALL NUTRIENTS ====================
    # Structure: (name, unit, result_key, aafco_pct_key, aafco_min)
    # Note: Protein target is 32-40% (enforced by allocation algorithm)
    
    nutrients_config = {
        "Macronutrients": [
            ("Energy", "kcal/kg DM", "Energy", None, None),
            ("Protein", "% DM", "Protein_percent", "protein", 18.0),  # Target: 32-40%
            ("Fat", "% DM", "Fat_percent", "fat", 5.5),
            ("Carbohydrate", "% DM", "CHO_percent", None, None),
            ("Fiber", "% DM", "Fiber_percent", None, None),
            ("Ash", "% DM", "Ash_percent", None, None),
        ],
        "Minerals (% of Dry Matter)": [
            ("Calcium (Ca)", "% DM", "Ca_percent", "calcium", 0.6),
            ("Phosphorus (P)", "% DM", "P_percent", "phosphorus", 0.4),
            ("Magnesium (Mg)", "% DM", "Mg_percent", "magnesium", 0.06),
            ("Potassium (K)", "% DM", "K_percent", "potassium", 0.6),
            ("Sodium (Na)", "% DM", "Na_percent", "sodium", 0.08),
        ],
        "Trace Minerals (mg/kg Dry Matter)": [
            ("Iron (Fe)", "mg/kg DM", "iron_mg_kg", "iron", 40.0),
            ("Zinc (Zn)", "mg/kg DM", "zn_mg_kg", "zinc", 80.0),
            ("Copper (Cu)", "mg/kg DM", "cu_mg_kg", "copper", 7.3),
            ("Iodine (I)", "mg/kg DM", "iodine_mg_kg", "iodine", 1.5),
            ("Selenium (Se)", "mg/kg DM", "se_mg_kg", "selenium", 0.35),
        ],
        "Vitamins": [
            ("Vitamin A", "IU/kg DM", "vitamin_a_iu_kg", "vitamin_a", 5000.0),
            ("Vitamin D", "IU/kg DM", "vitamin_d_iu_kg", "vitamin_d", 500.0),
            ("Vitamin E", "mg/kg DM", "vitamin_e_iu_kg", "vitamin_e", 34.0),
            ("Thiamin (B1)", "mg/kg DM", "thiamin_mg_kg", "thiamin", 2.25),
            ("Riboflavin (B2)", "mg/kg DM", "riboflavin_mg_kg", "riboflavin", 5.2),
            ("Niacin (B3)", "mg/kg DM", "niacin_mg_kg", "niacin", 13.6),
            ("Pantothenic Acid (B5)", "mg/kg DM", "pantothenic_acid_mg_kg", "pantothenic_acid", 12.0),
            ("Folic Acid (Folate)", "mg/kg DM", "folate_mg_kg", "folate", 0.216),
            ("Cobalamin (B12)", "mg/kg DM", "b12_mg_kg", "vitamin_b12", 0.022),
        ],
        "Essential Fatty Acids": [
            ("Linoleic Acid (18:2 n-6)", "% DM", "linoleic_percent", "linoleic_acid", 1.3),
            ("α-Linolenic Acid (18:3 n-3)", "% DM", "ala_percent", "alpha_linolenic_acid", 0.08),
            ("EPA (20:5 n-3)", "% DM", "epa_percent", "epa", 0.05),
            ("DHA (22:6 n-3)", "% DM", "dha_percent", "dha", 0.05),
        ],
        "Essential Amino Acids": [
            ("Arginine", "% DM", "arginine_percent", "arginine", 0.51),
            ("Isoleucine", "% DM", "isoleucine_percent", "isoleucine", 0.38),
            ("Leucine", "% DM", "leucine_percent", "leucine", 0.68),
            ("Lysine", "% DM", "lysine_percent", "lysine", 0.63),
            ("Methionine", "% DM", "methionine_percent", "methionine", 0.33),
            ("Phenylalanine", "% DM", "phenylalanine_percent", "phenylalanine", 0.45),
            ("Threonine", "% DM", "threonine_percent", "threonine", 0.48),
            ("Tryptophan", "% DM", "tryptophan_percent", "tryptophan", 0.16),
            ("Tyrosine", "% DM", "tyrosine_percent", "tyrosine", 0.18),
            ("Valine", "% DM", "valine_percent", "valine", 0.49),
        ],
    }

    # ==================== BUILD HTML TABLE ====================
    def get_status(diet_val, aafco_min, pct_of_min):
        """Return status indicator based on AAFCO compliance"""
        if aafco_min is None:
            return "—", "neutral"
        
        # Use pct_of_min if available (it's a ratio, 1.0 = 100%)
        if pct_of_min is not None:
            if pct_of_min >= 1.0:
                return "✓", "success"
            elif pct_of_min >= 0.9:
                return "⚠", "warning"
            else:
                return "✗", "danger"
        
        # Fallback to manual calculation
        if aafco_min is not None and diet_val < aafco_min:
            if diet_val >= aafco_min * 0.9:
                return "⚠", "warning"
            else:
                return "✗", "danger"
        
        return "✓", "success"
    
    table_html = ""
    for category, nutrients in nutrients_config.items():
        # Category header
        table_html += f"""
        <tr class="category-header">
            <td colspan="6"><strong>{category}</strong></td>
        </tr>
        """
        
        # Nutrients in this category
        for nutrient_tuple in nutrients:
            name, unit, result_key, aafco_key, aafco_min = nutrient_tuple
            
            # Get diet value from result
            diet_val = get_val(result_key, 0.0)
            
            # Get % of minimum from backend (if available)
            pct_of_min = get_pct(aafco_key) if aafco_key else None
            
            # Format values
            diet_str = f"{diet_val:.2f}"
            min_str = f"{aafco_min:.2f}" if aafco_min is not None else "—"
            
            # Calculate/display percentages
            if pct_of_min is not None:
                pct_min_str = f"{pct_of_min * 100:.1f}%"
            elif aafco_min is not None and aafco_min > 0:
                pct_min = (diet_val / aafco_min) * 100.0
                pct_min_str = f"{pct_min:.1f}%"
            else:
                pct_min_str = "—"
            
            # Get status
            status_icon, status_class = get_status(diet_val, aafco_min, pct_of_min)
            
            table_html += f"""
            <tr>
                <td>{name}</td>
                <td>{unit}</td>
                <td class="value">{diet_str}</td>
                <td class="value">{min_str}</td>
                <td class="value">{pct_min_str}</td>
                <td class="status status-{status_class}">{status_icon}</td>
            </tr>
            """
    
    # Full HTML page
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AAFCO Adult Maintenance - Complete Nutrient Analysis</title>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      margin: 20px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      padding: 20px;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
      background: white;
      border-radius: 12px;
      padding: 30px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.2);
    }}
    h1 {{
      color: #2c3e50;
      margin-bottom: 10px;
      font-size: 28px;
    }}
    .subtitle {{
      color: #7f8c8d;
      margin-bottom: 20px;
      font-size: 14px;
    }}
    .chips {{
      margin-bottom: 25px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chip {{
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 20px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}
    th {{
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 12px 8px;
      text-align: left;
      font-size: 13px;
      font-weight: 600;
      border-bottom: 3px solid #5568d3;
    }}
    td {{
      padding: 10px 8px;
      border-bottom: 1px solid #ecf0f1;
      font-size: 13px;
    }}
    tr:hover td {{
      background-color: #f8f9fa;
    }}
    .category-header td {{
      background: linear-gradient(to right, #f8f9fa, #e9ecef);
      font-weight: 700;
      font-size: 14px;
      color: #495057;
      padding: 12px 8px;
      border-top: 2px solid #dee2e6;
      border-bottom: 2px solid #dee2e6;
    }}
    .category-header:hover td {{
      background: linear-gradient(to right, #f8f9fa, #e9ecef);
    }}
    .value {{
      text-align: center;
      font-family: 'Courier New', monospace;
      font-weight: 500;
    }}
    .status {{
      text-align: center;
      font-size: 18px;
    }}
    .status-success {{
      color: #27ae60;
    }}
    .status-warning {{
      color: #f39c12;
    }}
    .status-danger {{
      color: #e74c3c;
    }}
    .status-neutral {{
      color: #95a5a6;
    }}
    .legend {{
      margin-top: 30px;
      padding: 20px;
      background: #f8f9fa;
      border-radius: 8px;
      border-left: 4px solid #667eea;
    }}
    .legend-title {{
      font-weight: 700;
      margin-bottom: 12px;
      color: #2c3e50;
      font-size: 15px;
    }}
    .legend-items {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }}
    .legend-icon {{
      font-size: 18px;
      width: 25px;
      text-align: center;
    }}
    .note {{
      margin-top: 20px;
      padding: 15px;
      background: #fff3cd;
      border-left: 4px solid #ffc107;
      border-radius: 4px;
      font-size: 13px;
      color: #856404;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🐕 AAFCO Adult Maintenance - Complete Nutrient Analysis</h1>
    <div class="subtitle">Comprehensive analysis of all 43+ tracked nutrients with AAFCO 2024 standards</div>
    
    <div class="chips">
      {"".join(f'<span class="chip">{format_ingredient_name(ing)}</span>' for ing in ingredients)}
    </div>

    <table>
      <thead>
        <tr>
          <th>Nutrient</th>
          <th>Unit</th>
          <th>Diet Value</th>
          <th>AAFCO Min</th>
          <th>% of Min</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {table_html}
      </tbody>
    </table>

    <div class="legend">
      <div class="legend-title">Status Legend</div>
      <div class="legend-items">
        <div class="legend-item">
          <span class="legend-icon status-success">✓</span>
          <span>Meets AAFCO minimum</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon status-warning">⚠</span>
          <span>Borderline (within 10% of minimum)</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon status-danger">✗</span>
          <span>Below AAFCO minimum</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon status-neutral">—</span>
          <span>No AAFCO minimum defined</span>
        </div>
      </div>
    </div>

    <div class="note">
      <strong>Note:</strong> All values are per 1000g (1kg) of dry matter (DM). AAFCO standards represent adult maintenance requirements for dogs. 
      Values shown are based on AAFCO 2024 Official Publication. Nutrients with "—" have no established minimum requirements.
    </div>
  </div>
</body>
</html>
    """

    return HTMLResponse(content=html)


# -------------------------------------------------------------------
# /calculate – JSON API + report URL (auto-opens)
# -------------------------------------------------------------------
@router.post("/calculate")
def calculate(req: IngredientRequest, request: Request):
    """
    Calculate diet from selected ingredients.

    Returns the JSON from svc.calculate_diet(...) plus:
      - report_url: HTML diet report (macros + pie chart)
      - aafco_url:  HTML AAFCO Adult maintenance table
      - ingredients_summary_url: HTML Ingredients Summary with fresh weights
      - aafco_percent_of_minimum: % of AAFCO minimum for all 43 nutrients

    When called locally from a script, we also try to auto-open the
    report_url in a new browser tab (webbrowser.open_new_tab).
    """
    result = svc.calculate_diet(req.ingredients)

    report_url = None
    aafco_url = None
    ingredients_summary_url = None

    if req.ingredients:
        # Return GET URLs with query params for auto-opening
        base_report = request.url_for("diet_report_get")
        base_aafco = request.url_for("diet_aafco_full_get")
        base_summary = request.url_for("ingredients_summary_get")
        query = urlencode([("ingredients", name) for name in req.ingredients])
        report_url = f"{base_report}?{query}"
        aafco_url = f"{base_aafco}?{query}"
        ingredients_summary_url = f"{base_summary}?{query}"

        print(f"[Diet] Report URL (GET for auto-open): {report_url}")
        print(f"[Diet] AAFCO URL (GET for auto-open): {aafco_url}")
        print(f"[Diet] Ingredients Summary URL (GET for auto-open): {ingredients_summary_url}")

        # Auto-open the report in browser
        try:
            webbrowser.open_new_tab(str(report_url))
        except Exception as e:
            print(f"[Diet] Could not auto-open browser: {e}")

    result["report_url"] = report_url
    result["aafco_url"] = aafco_url
    result["ingredients_summary_url"] = ingredients_summary_url

    return result
