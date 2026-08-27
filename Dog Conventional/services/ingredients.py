"""
Dog Diet Planner - Ingredients Service (FINAL VERSION with Fresh Weights)

This version matches exactly with:
- fixed_ingredients_updated.csv
- user_ingredients_updated.csv

All 47 nutrient columns are now tracked including histidine_g.

FIXED ALLOCATION RULES (must total 1000g DM):
- Meat: 205g total
    - Only A selected: 205g
    - A + B: 155 + 50
    - A + B + C: 115 + 40 + 50
- Grains: 350g total
    - Only A selected: 350g
    - A + B: 250 + 100
- Liver: 155g (alone) / 125g liver + 30g organ (if organ also selected)
- Vegetables: 130g total (A: 90g, B: 40g)
- Vegetable C (Potatoes): optional, 50g — taken from Grain B if selected, otherwise Grain A
- Fruits: 20g (fixed)
- Oil: 10g (fixed)
- Seeds (optional): 10g — reduces Veg A to 85g and Veg B to 35g

DIET QUALITY TARGETS:
- Protein %: 32–40%
- Fat %: >12% and <17%
- CHO %: >30% and <45%
- Fiber %: >3% and <6.5%
- Energy density (kcal/kg): 4000–4500
- Ca:P: >1.4:1 and <2:1
- Omega-6 : Omega-3: >2 and <6

UPDATED: Now calculates and returns fresh weights for each ingredient.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from collections import defaultdict

# ---- CSV locations (project root) ----
ROOT = Path(__file__).resolve().parents[1]  # services -> project root
FIXED_CSV = ROOT / "fixed_ingredients_corrected.csv"  # Corrected CSV with proper folate/B12 values
USER_CSV = ROOT / "user_ingredients_corrected.csv"    # Corrected CSV with proper folate/B12 values

# Fallback to original names if corrected versions don't exist
if not FIXED_CSV.exists():
    FIXED_CSV = ROOT / "fixed_ingredients.csv"
if not USER_CSV.exists():
    USER_CSV = ROOT / "user_ingredients.csv"

# in-memory dataframes
_fixed_df: Optional[pd.DataFrame] = None
_user_df: Optional[pd.DataFrame] = None

REQUIRED_FIXED = {
    "ingredient_name", "dm_g", "protein_g", "fat_g", "cho_g", "fiber_g", "ash_g",
    "calcium_mg", "phosphorus_mg", "iron_mg", "energy_kcal"
}

REQUIRED_USER = {
    "ingredient_name", "meat group a", "protein_g", "fat_g", "cho_g", "fiber_g", "ash_g",
    "calcium_mg", "phosphorus_mg", "iron_mg", "energy_kcal"
}

# ============================================================================
# DIET QUALITY TARGETS - STRICTLY ENFORCED
# ============================================================================
FIBER_SUPP_MAX_G = 10.0       # Hard limit: 10g maximum total for fiber/seeds supplements
MAX_TOTAL_FIBER_PCT = 6.5     # Maximum 6.5% total dietary fiber in the diet
MIN_TOTAL_FIBER_PCT = 3.0     # Minimum 3% total dietary fiber

# Macronutrient targets (% DM)
PROTEIN_MIN = 20.0
PROTEIN_MAX = 40.0
FAT_MIN = 11.0
FAT_MAX = 18.5
CHO_MIN = 30.0
CHO_MAX = 45.0

# Energy target (kcal/kg DM)
ENERGY_MIN = 4000.0
ENERGY_MAX = 4500.0

# Ca:P ratio
CA_P_RATIO_MIN = 1.4
CA_P_RATIO_MAX = 2.0

# Omega-6 : Omega-3 ratio
OMEGA6_OMEGA3_RATIO_MIN = 2.0
OMEGA6_OMEGA3_RATIO_MAX = 6.0

# ============================================================================
# FIXED ALLOCATION AMOUNTS (g DM)
# ============================================================================
MEAT_TOTAL_DM = 205.0
GRAIN_TOTAL_DM = 350.0
GRAIN_A_WITH_POTATO_DM = 300.0  # Legacy constant — Grain A is now computed dynamically
POTATO_DM = 50.0                # Vegetable C (potatoes) — taken from Grain B if selected, else Grain A
LIVER_ALONE_DM = 155.0          # Liver when no other organ selected
LIVER_WITH_ORGAN_DM = 125.0     # Liver when another organ is also selected
ORGAN_OTHER_DM = 30.0           # Other organ meat (only when liver also selected)
VEG_A_DM = 90.0
VEG_B_DM = 40.0
VEG_TOTAL_DM = 130.0            # VEG_A + VEG_B
VEG_A_WITH_SEEDS_DM = 85.0      # Veg A reduced when seeds selected
VEG_B_WITH_SEEDS_DM = 35.0      # Veg B reduced when seeds selected
VEG_TOTAL_WITH_SEEDS_DM = 120.0 # Reduced veg total when seeds selected
SEEDS_DM = 10.0                 # Optional seeds allocation
FRUITS_DM = 20.0
OILS_DM = 8.0
# Fixed ingredient overrides — key is lowercase stripped name
# None  = skip entirely (invisible, not calculated)
# float = override dm_g to this value
FIXED_OVERRIDES = {
    "fish oil":      None,   # Removed — cod liver oil covers the full oil allocation
    "cod liver oil": 16.0,  # Doubled from CSV 10g to 20g
}

# Cod liver oil dm_g depends on whether salmon/tilapia is selected in Meat Group A
COD_LIVER_OIL_DEFAULT_DM       = 16.0  # No salmon/tilapia in Meat A
COD_LIVER_OIL_FISH_ONLY_DM     = 10.0  # Meat A is salmon/tilapia only
COD_LIVER_OIL_FISH_WITH_OTHER_DM = 14.0  # Meat A includes salmon/tilapia + another meat
COD_LIVER_OIL_FISH_NAMES = {"salmon", "talipia (fish)", "alaska pollock (fish)", "haddock (fish)"}


def _cod_liver_oil_dm(meats_a):
    """Determine cod liver oil dm_g based on Meat Group A selection."""
    meat_a_names = {str(r.get("ingredient_name", "")).strip().lower() for r in meats_a}
    fish_in_a = meat_a_names & COD_LIVER_OIL_FISH_NAMES
    if not fish_in_a:
        return COD_LIVER_OIL_DEFAULT_DM
    if meat_a_names == fish_in_a:
        return COD_LIVER_OIL_FISH_ONLY_DM
    return COD_LIVER_OIL_FISH_WITH_OTHER_DM

MINERALS_A_BASE_DM  = 20.0   # Mineral Group A base allocation
MINERALS_A_BONUS_DM = 2.0    # Bonus to Group A when Group B not selected
MINERALS_B_TOTAL_DM = 8.0    # Mineral Group B total (bone meal / blood meal)


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    # Remove any Unnamed columns
    df = df.loc[:, ~df.columns.str.contains('^unnamed', case=False)]
    # Remove rows with blank/empty ingredient_name
    if 'ingredient_name' in df.columns:
        df = df[df['ingredient_name'].astype(str).str.strip().ne('')]
        df = df.dropna(subset=['ingredient_name'])
    return df


def load_csvs() -> dict:
    """(Re)load CSVs into memory; called at import and via /reload."""
    global _fixed_df, _user_df
    
    f = _norm(pd.read_csv(FIXED_CSV))
    u = _norm(pd.read_csv(USER_CSV))

    if not REQUIRED_FIXED.issubset(f.columns):
        missing = list(REQUIRED_FIXED - set(f.columns))
        raise RuntimeError(f"{FIXED_CSV.name} missing columns: {missing}")
    if not REQUIRED_USER.issubset(u.columns):
        missing = list(REQUIRED_USER - set(u.columns))
        raise RuntimeError(f"{USER_CSV.name} missing columns: {missing}")

    _fixed_df, _user_df = f, u
    return {"ok": True, "fixed_rows": len(_fixed_df), "user_rows": len(_user_df)}


# load once on import
load_csvs()


# ============================================================================
# AAFCO Adult Maintenance Minimums (per kg DM basis)
# ============================================================================
AAFCO_MINIMUMS = {
    # Macronutrients (% DM)
    "protein": 18.0,  # Target range: 32-40%
    "fat": 5.5,
    
    # Major Minerals (% DM)
    "calcium": 0.6,
    "phosphorus": 0.4,
    "magnesium": 0.06,
    "potassium": 0.6,
    "sodium": 0.08,
    
    # Trace Minerals (mg/kg DM)
    "iron": 40.0,
    "zinc": 80.0,
    "copper": 7.3,
    "iodine": 1.0,
    "selenium": 0.35,
    
    # Vitamins (mg/kg DM or IU/kg DM)
    "thiamin": 2.25,
    "riboflavin": 5.2,
    "niacin": 13.6,
    "pantothenic_acid": 12.0,
    "folate": 0.216,
    "vitamin_b12": 0.022,
    "vitamin_a": 5000.0,
    "vitamin_e": 34.0,
    "vitamin_d": 500.0,
    
    # Fatty Acids (% DM)
    "linoleic_acid": 1.3,
    "alpha_linolenic_acid": 0.08,
    "epa": 0.05,
    "dha": 0.05,
    
    # Amino Acids (% DM)
    "tryptophan": 0.16,
    "tyrosine": 0.18,
    "threonine": 0.48,
    "isoleucine": 0.38,
    "leucine": 0.68,
    "lysine": 0.63,
    "methionine": 0.33,
    "phenylalanine": 0.45,
    "valine": 0.49,
    "arginine": 0.51,
}

AAFCO_MAXIMUMS = {
    "calcium": 2.5,
    "phosphorus": 1.6,
    "magnesium": 0.3,
    "iron": 3000.0,
    "zinc": 1000.0,
    "copper": 250.0,
    "iodine": 50.0,
    "selenium": 2.0,
    "vitamin_a": 250000.0,
    "vitamin_e": 1000.0,
    "vitamin_d": 3000.0,
}


# ---- accessors used by routers ----
def fixed_df() -> pd.DataFrame:
    return _fixed_df


def user_df() -> pd.DataFrame:
    return _user_df


# ---- helpers ----
FIXED_TOTAL_DM = 1000.0


def _safe_float(val, default=0.0):
    """Safely convert a value to float, returning default if NaN or invalid."""
    try:
        result = float(val)
        if pd.isna(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def _init_totals() -> Dict[str, float]:
    """Initialize all nutrient totals to zero."""
    return {
        # Macros
        "Protein": 0.0, "Fat": 0.0, "CHO": 0.0, "Fiber": 0.0, "Ash": 0.0,
        # Major minerals (stored in grams)
        "Ca": 0.0, "P": 0.0, "Mg": 0.0, "K": 0.0, "Na": 0.0,
        # Trace minerals (stored in mg)
        "Iron": 0.0, "Zn": 0.0, "Cu": 0.0, "Iodine": 0.0, "Se": 0.0,
        # Vitamins (stored in mg or IU)
        "Thiamin": 0.0, "Riboflavin": 0.0, "Niacin": 0.0, "Pantothenic_acid": 0.0,
        "Vitamin_B6": 0.0, "Folate": 0.0, "Choline": 0.0, "Vitamin_B12": 0.0,
        "Vitamin_A": 0.0, "Vitamin_E": 0.0, "Vitamin_D": 0.0,
        # Fatty acids (stored in grams)
        "Linoleic_acid": 0.0, "Alpha_linolenic_acid": 0.0, "EPA": 0.0, "DHA": 0.0,
        # Amino acids (stored in grams)
        "Tryptophan": 0.0, "Threonine": 0.0, "Isoleucine": 0.0, "Leucine": 0.0,
        "Lysine": 0.0, "Methionine": 0.0, "Cystine": 0.0, "Phenylalanine": 0.0,
        "Tyrosine": 0.0, "Valine": 0.0, "Arginine": 0.0, "Histidine": 0.0,
        # Energy
        "Energy": 0.0
    }


def _add_row(totals: Dict[str, float], dm: float, row: pd.Series):
    """
    Add nutrient contributions from an ingredient row to totals.
    """
    def get_col(name, default=0.0):
        return _safe_float(row.get(name, default), default)
    
    # ========== MACRONUTRIENTS (g) ==========
    totals["Protein"] += get_col("protein_g") * dm / 100.0
    totals["Fat"] += get_col("fat_g") * dm / 100.0
    totals["CHO"] += get_col("cho_g") * dm / 100.0
    totals["Fiber"] += get_col("fiber_g") * dm / 100.0
    totals["Ash"] += get_col("ash_g") * dm / 100.0
    
    # ========== MAJOR MINERALS (mg → g in totals) ==========
    totals["Ca"] += get_col("calcium_mg") * dm / 100.0 / 1000.0
    totals["P"] += get_col("phosphorus_mg") * dm / 100.0 / 1000.0
    totals["Mg"] += get_col("magnesium_mg") * dm / 100.0 / 1000.0
    totals["K"] += get_col("potassium_mg") * dm / 100.0 / 1000.0
    totals["Na"] += get_col("sodium_mg") * dm / 100.0 / 1000.0
    
    # ========== TRACE MINERALS (mg) ==========
    totals["Iron"] += get_col("iron_mg") * dm / 100.0
    totals["Zn"] += get_col("zinc_mg") * dm / 100.0
    totals["Cu"] += get_col("copper_mg") * dm / 100.0
    totals["Iodine"] += get_col("iodine_mg") * dm / 100.0
    totals["Se"] += get_col("selenium_mg") * dm / 100.0
    
    # ========== B VITAMINS (mg) ==========
    totals["Thiamin"] += get_col("thiamin_mg") * dm / 100.0
    totals["Riboflavin"] += get_col("riboflavin_mg") * dm / 100.0
    totals["Niacin"] += get_col("niacin_mg") * dm / 100.0
    totals["Pantothenic_acid"] += get_col("pantothenic_acid_mg") * dm / 100.0
    totals["Vitamin_B6"] += get_col("vitamin_b6_mg") * dm / 100.0
    
    # FOLATE: CSV values are in mg (despite _ug column name)
    totals["Folate"] += get_col("folate_ug") * dm / 100.0
    
    totals["Choline"] += get_col("choline_mg") * dm / 100.0
    
    # VITAMIN B12: CSV values are in mg (despite _ug column name)
    totals["Vitamin_B12"] += get_col("vitamin_b12_ug") * dm / 100.0
    
    # ========== FAT-SOLUBLE VITAMINS ==========
    totals["Vitamin_A"] += get_col("vitamin_a_iu") * dm / 100.0
    totals["Vitamin_E"] += get_col("vitamin_e_mg") * dm / 100.0
    totals["Vitamin_D"] += get_col("vitamin_d_iu") * dm / 100.0
    
    # ========== FATTY ACIDS (g) ==========
    totals["Linoleic_acid"] += get_col("pufa_18_2_g") * dm / 100.0
    totals["Alpha_linolenic_acid"] += get_col("pufa_18_3_g") * dm / 100.0
    totals["EPA"] += get_col("pufa_20_5_g") * dm / 100.0
    totals["DHA"] += get_col("pufa_22_6_g") * dm / 100.0
    
    # ========== AMINO ACIDS (g) ==========
    totals["Tryptophan"] += get_col("tryptophan_g") * dm / 100.0
    totals["Threonine"] += get_col("threonine_g") * dm / 100.0
    totals["Isoleucine"] += get_col("isoleucine_g") * dm / 100.0
    totals["Leucine"] += get_col("leucine_g") * dm / 100.0
    totals["Lysine"] += get_col("lysine_g") * dm / 100.0
    totals["Methionine"] += get_col("methionine_g") * dm / 100.0
    totals["Cystine"] += get_col("cystine_g") * dm / 100.0
    totals["Phenylalanine"] += get_col("phenylalanine_g") * dm / 100.0
    totals["Tyrosine"] += get_col("tyrosine_g") * dm / 100.0
    totals["Valine"] += get_col("valine_g") * dm / 100.0
    totals["Arginine"] += get_col("arginine_g") * dm / 100.0
    totals["Histidine"] += get_col("histidine_g") * dm / 100.0
    
    # ========== ENERGY ==========
    totals["Energy"] += get_col("energy_kcal") * dm / 100.0


def _compress_breakdown(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine multiple entries for the same ingredient into one line."""
    agg = defaultdict(lambda: {"ingredient": "", "dm_g": 0.0, "fresh_weight_g": 0.0, "water_percent": 0.0, "fixed": False})
    for row in raw:
        name = row["ingredient"]
        agg[name]["ingredient"] = name
        agg[name]["dm_g"] = round(agg[name]["dm_g"] + float(row["dm_g"]), 2)
        agg[name]["fresh_weight_g"] = round(agg[name]["fresh_weight_g"] + float(row.get("fresh_weight_g", 0)), 2)
        agg[name]["water_percent"] = row.get("water_percent", 0.0)
        agg[name]["fixed"] = agg[name]["fixed"] or bool(row.get("fixed", False))
    merged = list(agg.values())
    merged.sort(key=lambda r: (not r["fixed"], r["ingredient"].lower()))
    return merged


# ---------- robust normalizers ----------
def gname(s: pd.Series) -> str:
    raw = str(s.get("meat group a", "")).lower()
    return " ".join(raw.split())


def _normkey(x: str) -> str:
    return " ".join(
        str(x).lower()
        .replace("–", "-").replace("—", "-")
        .replace("'", "'").replace("'", "'")
        .split()
    )


# ============================================================================
# Fresh Weight Calculation
# ============================================================================
def calculate_fresh_weight(dm_g: float, water_percent: float) -> float:
    """
    Calculate fresh weight from dry matter weight and water percentage.
    
    Formula: fresh_weight = dm_weight / (1 - water_percent/100)
    """
    if water_percent >= 100:
        return dm_g
    if water_percent <= 0:
        return dm_g
    
    dry_matter_fraction = 1.0 - (water_percent / 100.0)
    if dry_matter_fraction <= 0:
        return dm_g
    
    return dm_g / dry_matter_fraction


def get_water_percent(row: pd.Series) -> float:
    """Get water percentage from a row."""
    water_cols = ['water', 'water_percent', 'water_pct', 'moisture', 'moisture_percent', 'water_g']
    
    for col in water_cols:
        if col in row.index:
            val = _safe_float(row.get(col, 0), 0)
            if val > 0:
                return val
    
    return 0.0


def get_fiber_percent(row: pd.Series) -> float:
    """Get fiber percentage (g/100g DM) from a row."""
    return _safe_float(row.get("fiber_g", 0), 0)


# ============================================================================
# AAFCO Compliance Calculator
# ============================================================================
def calculate_aafco_percent_of_minimum(totals: Dict[str, float], total_dm_g: float = 1000.0) -> Dict[str, Any]:
    """Calculate % of AAFCO minimum for all nutrients."""
    
    def calc_pct(actual, minimum):
        if minimum is None or minimum == 0:
            return None
        if actual is None or actual == 0:
            return 0.0
        return round(actual / minimum, 4)
    
    result = {}
    
    # MACRONUTRIENTS (% DM)
    protein_pct = (totals.get("Protein", 0) / total_dm_g) * 100
    fat_pct = (totals.get("Fat", 0) / total_dm_g) * 100
    
    result["protein"] = calc_pct(protein_pct, AAFCO_MINIMUMS["protein"])
    result["fat"] = calc_pct(fat_pct, AAFCO_MINIMUMS["fat"])
    
    # MAJOR MINERALS (% DM)
    ca_pct = (totals.get("Ca", 0) / total_dm_g) * 100
    p_pct = (totals.get("P", 0) / total_dm_g) * 100
    mg_pct = (totals.get("Mg", 0) / total_dm_g) * 100
    k_pct = (totals.get("K", 0) / total_dm_g) * 100
    na_pct = (totals.get("Na", 0) / total_dm_g) * 100
    
    result["calcium"] = calc_pct(ca_pct, AAFCO_MINIMUMS["calcium"])
    result["phosphorus"] = calc_pct(p_pct, AAFCO_MINIMUMS["phosphorus"])
    result["magnesium"] = calc_pct(mg_pct, AAFCO_MINIMUMS["magnesium"])
    result["potassium"] = calc_pct(k_pct, AAFCO_MINIMUMS["potassium"])
    result["sodium"] = calc_pct(na_pct, AAFCO_MINIMUMS["sodium"])
    
    # TRACE MINERALS (mg/kg DM)
    iron_mg_kg = (totals.get("Iron", 0) * 1000) / total_dm_g
    zn_mg_kg = (totals.get("Zn", 0) * 1000) / total_dm_g
    cu_mg_kg = (totals.get("Cu", 0) * 1000) / total_dm_g
    iodine_mg_kg = (totals.get("Iodine", 0) * 1000) / total_dm_g
    se_mg_kg = (totals.get("Se", 0) * 1000) / total_dm_g
    
    result["iron"] = calc_pct(iron_mg_kg, AAFCO_MINIMUMS["iron"])
    result["zinc"] = calc_pct(zn_mg_kg, AAFCO_MINIMUMS["zinc"])
    result["copper"] = calc_pct(cu_mg_kg, AAFCO_MINIMUMS["copper"])
    result["iodine"] = calc_pct(iodine_mg_kg, AAFCO_MINIMUMS["iodine"])
    result["selenium"] = calc_pct(se_mg_kg, AAFCO_MINIMUMS["selenium"])
    
    # VITAMINS (mg/kg or IU/kg)
    thiamin_mg_kg = (totals.get("Thiamin", 0) * 1000) / total_dm_g
    riboflavin_mg_kg = (totals.get("Riboflavin", 0) * 1000) / total_dm_g
    niacin_mg_kg = (totals.get("Niacin", 0) * 1000) / total_dm_g
    panto_mg_kg = (totals.get("Pantothenic_acid", 0) * 1000) / total_dm_g
    b6_mg_kg = (totals.get("Vitamin_B6", 0) * 1000) / total_dm_g
    folate_mg_kg = (totals.get("Folate", 0) * 1000) / total_dm_g
    choline_mg_kg = (totals.get("Choline", 0) * 1000) / total_dm_g
    b12_mg_kg = (totals.get("Vitamin_B12", 0) * 1000) / total_dm_g
    
    vit_a_iu_kg = (totals.get("Vitamin_A", 0) * 1000) / total_dm_g
    vit_e_iu_kg = (totals.get("Vitamin_E", 0) * 1000) / total_dm_g
    vit_d_iu_kg = (totals.get("Vitamin_D", 0) * 1000) / total_dm_g
    
    result["thiamin"] = calc_pct(thiamin_mg_kg, AAFCO_MINIMUMS["thiamin"])
    result["riboflavin"] = calc_pct(riboflavin_mg_kg, AAFCO_MINIMUMS["riboflavin"])
    result["niacin"] = calc_pct(niacin_mg_kg, AAFCO_MINIMUMS["niacin"])
    result["pantothenic_acid"] = calc_pct(panto_mg_kg, AAFCO_MINIMUMS["pantothenic_acid"])
    result["folate"] = calc_pct(folate_mg_kg, AAFCO_MINIMUMS["folate"])
    result["vitamin_b12"] = calc_pct(b12_mg_kg, AAFCO_MINIMUMS["vitamin_b12"])
    result["vitamin_a"] = calc_pct(vit_a_iu_kg, AAFCO_MINIMUMS["vitamin_a"])
    result["vitamin_e"] = calc_pct(vit_e_iu_kg, AAFCO_MINIMUMS["vitamin_e"])
    result["vitamin_d"] = calc_pct(vit_d_iu_kg, AAFCO_MINIMUMS["vitamin_d"])
    
    # FATTY ACIDS (% DM)
    linoleic_pct = (totals.get("Linoleic_acid", 0) / total_dm_g) * 100
    ala_pct = (totals.get("Alpha_linolenic_acid", 0) / total_dm_g) * 100
    epa_pct = (totals.get("EPA", 0) / total_dm_g) * 100
    dha_pct = (totals.get("DHA", 0) / total_dm_g) * 100
    
    result["linoleic_acid"] = calc_pct(linoleic_pct, AAFCO_MINIMUMS["linoleic_acid"])
    result["alpha_linolenic_acid"] = calc_pct(ala_pct, AAFCO_MINIMUMS["alpha_linolenic_acid"])
    result["epa"] = calc_pct(epa_pct, AAFCO_MINIMUMS["epa"])
    result["dha"] = calc_pct(dha_pct, AAFCO_MINIMUMS["dha"])
    
    # AMINO ACIDS (% DM)
    tryptophan_pct = (totals.get("Tryptophan", 0) / total_dm_g) * 100
    tyrosine_pct = (totals.get("Tyrosine", 0) / total_dm_g) * 100
    threonine_pct = (totals.get("Threonine", 0) / total_dm_g) * 100
    isoleucine_pct = (totals.get("Isoleucine", 0) / total_dm_g) * 100
    leucine_pct = (totals.get("Leucine", 0) / total_dm_g) * 100
    lysine_pct = (totals.get("Lysine", 0) / total_dm_g) * 100
    methionine_pct = (totals.get("Methionine", 0) / total_dm_g) * 100
    phenylalanine_pct = (totals.get("Phenylalanine", 0) / total_dm_g) * 100
    valine_pct = (totals.get("Valine", 0) / total_dm_g) * 100
    arginine_pct = (totals.get("Arginine", 0) / total_dm_g) * 100
    
    result["tryptophan"] = calc_pct(tryptophan_pct, AAFCO_MINIMUMS["tryptophan"])
    result["tyrosine"] = calc_pct(tyrosine_pct, AAFCO_MINIMUMS["tyrosine"])
    result["threonine"] = calc_pct(threonine_pct, AAFCO_MINIMUMS["threonine"])
    result["isoleucine"] = calc_pct(isoleucine_pct, AAFCO_MINIMUMS["isoleucine"])
    result["leucine"] = calc_pct(leucine_pct, AAFCO_MINIMUMS["leucine"])
    result["lysine"] = calc_pct(lysine_pct, AAFCO_MINIMUMS["lysine"])
    result["methionine"] = calc_pct(methionine_pct, AAFCO_MINIMUMS["methionine"])
    result["phenylalanine"] = calc_pct(phenylalanine_pct, AAFCO_MINIMUMS["phenylalanine"])
    result["valine"] = calc_pct(valine_pct, AAFCO_MINIMUMS["valine"])
    result["arginine"] = calc_pct(arginine_pct, AAFCO_MINIMUMS["arginine"])
    
    return result


def _add_extended_nutrient_output(result: dict, totals: dict, total_dm_g: float = 1000.0):
    """Add extended nutrient values to the result dictionary for the router."""
    
    def to_pct_dm(total_g):
        return round((total_g / total_dm_g) * 100, 4)
    
    def to_mg_kg(total_mg):
        return round((total_mg * 1000) / total_dm_g, 4)
    
    def to_iu_kg(total_iu):
        return round((total_iu * 1000) / total_dm_g, 4)
    
    # Major Minerals (% of DM)
    result["Mg_percent"] = to_pct_dm(totals.get("Mg", 0))
    result["K_percent"] = to_pct_dm(totals.get("K", 0))
    result["Na_percent"] = to_pct_dm(totals.get("Na", 0))
    
    # Trace Minerals (mg/kg DM)
    result["iron_mg_kg"] = to_mg_kg(totals.get("Iron", 0))
    result["zn_mg_kg"] = to_mg_kg(totals.get("Zn", 0))
    result["cu_mg_kg"] = to_mg_kg(totals.get("Cu", 0))
    result["iodine_mg_kg"] = to_mg_kg(totals.get("Iodine", 0))
    result["se_mg_kg"] = to_mg_kg(totals.get("Se", 0))
    
    # Vitamins
    result["vitamin_a_iu_kg"] = to_iu_kg(totals.get("Vitamin_A", 0))
    result["vitamin_d_iu_kg"] = to_iu_kg(totals.get("Vitamin_D", 0))
    result["vitamin_e_iu_kg"] = to_iu_kg(totals.get("Vitamin_E", 0))
    result["thiamin_mg_kg"] = to_mg_kg(totals.get("Thiamin", 0))
    result["riboflavin_mg_kg"] = to_mg_kg(totals.get("Riboflavin", 0))
    result["niacin_mg_kg"] = to_mg_kg(totals.get("Niacin", 0))
    result["pantothenic_acid_mg_kg"] = to_mg_kg(totals.get("Pantothenic_acid", 0))
    result["b6_mg_kg"] = to_mg_kg(totals.get("Vitamin_B6", 0))
    result["folate_mg_kg"] = to_mg_kg(totals.get("Folate", 0))
    result["b12_mg_kg"] = to_mg_kg(totals.get("Vitamin_B12", 0))
    result["choline_mg_kg"] = to_mg_kg(totals.get("Choline", 0))
    
    # Fatty Acids (% of DM)
    result["linoleic_percent"] = to_pct_dm(totals.get("Linoleic_acid", 0))
    result["ala_percent"] = to_pct_dm(totals.get("Alpha_linolenic_acid", 0))
    result["epa_percent"] = to_pct_dm(totals.get("EPA", 0))
    result["dha_percent"] = to_pct_dm(totals.get("DHA", 0))
    
    # Amino Acids (% of DM)
    result["arginine_percent"] = to_pct_dm(totals.get("Arginine", 0))
    result["isoleucine_percent"] = to_pct_dm(totals.get("Isoleucine", 0))
    result["leucine_percent"] = to_pct_dm(totals.get("Leucine", 0))
    result["lysine_percent"] = to_pct_dm(totals.get("Lysine", 0))
    result["methionine_percent"] = to_pct_dm(totals.get("Methionine", 0))
    result["cystine_percent"] = to_pct_dm(totals.get("Cystine", 0))
    result["phenylalanine_percent"] = to_pct_dm(totals.get("Phenylalanine", 0))
    result["tyrosine_percent"] = to_pct_dm(totals.get("Tyrosine", 0))
    result["threonine_percent"] = to_pct_dm(totals.get("Threonine", 0))
    result["tryptophan_percent"] = to_pct_dm(totals.get("Tryptophan", 0))
    result["valine_percent"] = to_pct_dm(totals.get("Valine", 0))


# ============================================================================
# Helper functions for grouping ingredients
# ============================================================================
def get_user_ingredients_with_subcategories() -> List[Dict[str, Any]]:
    """Get user ingredients with proper organ subcategories."""
    df = user_df().copy()
    ingredients = []
    
    for _, row in df.iterrows():
        ingredient_name = str(row["ingredient_name"]).strip()
        group_name = str(row["meat group a"]).strip()
        
        # Handle organ meat categorization
        # CSV has: "Organ Meat (Liver)" for livers, "Organ Meat" for others
        group_lower = group_name.lower()
        
        if group_lower == "organ meat (liver)":
            display_group = "Organ Meat (Liver)"
        elif group_lower == "organ meat":
            display_group = "Organ Meat (Other)"
        else:
            display_group = group_name
        
        ingredient_dict = row.to_dict()
        ingredient_dict["display_group_name"] = display_group
        ingredient_dict["original_group_name"] = group_name
        ingredients.append(ingredient_dict)
    
    return ingredients


def get_ingredients_grouped_by_category() -> Dict[str, Dict[str, Any]]:
    """Get ingredients grouped by category with metadata."""
    ingredients = get_user_ingredients_with_subcategories()
    grouped = defaultdict(list)
    
    for ingredient in ingredients:
        category = ingredient["display_group_name"]
        grouped[category].append(ingredient)
    
    for category in grouped:
        grouped[category].sort(key=lambda x: x["ingredient_name"].lower())
    
    categories_with_metadata = {}
    
    # Updated category configs to match new fixed allocation rules
    category_configs = {
        "Meat Group A": {
            "items": grouped.get("Meat Group A", []),
            "label": "01 Meat Group A (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "Mandatory: Primary meat source - select 1-3. Total meat = 205g DM."
        },
        "Meat Group B": {
            "items": grouped.get("Meat Group B", []),
            "label": "02 Meat Group B (Optional - Pick up to one)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "notes": "Optional: Select up to one. If selected with A: A=155g, B=50g"
        },
        "Meat Group C": {
            "items": grouped.get("Meat Group C", []),
            "label": "03 Meat Group C (Optional - Pick up to one)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "notes": "Optional: Select up to one. If A+B+C: A=115g, B=40g, C=50g"
        },
        "Organ Meat (Other)": {
            "items": grouped.get("Organ Meat (Other)", []),
            "label": "04 Organ Meat - Other (Optional - Pick up to one)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "dm_range": {"min": 0, "max": 30},
            "percentage": "30g when selected (liver becomes 125g)",
            "notes": "Optional: Select up to one. Liver adjusts to 125g when organ selected."
        },
        "Organ Meat (Liver)": {
            "items": grouped.get("Organ Meat (Liver)", []),
            "label": "05 Organ Meat - Liver (Mandatory - Select one)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 1,
            "dm_range": {"min": 125, "max": 155},
            "percentage": "155g alone, 125g with other organ",
            "notes": "Mandatory: Liver is required - select one"
        },
        "Grain A": {
            "items": grouped.get("Grain A", []),
            "label": "06 Grain A (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "Mandatory: Primary grain source - select 1-3. Total grain = 350g DM."
        },
        "Grain B": {
            "items": grouped.get("Grain B", []),
            "label": "07 Grain B (Optional - Pick up to one)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "notes": "Optional: Select up to one. If selected: A=250g, B=100g"
        },
        "Vegetable A": {
            "items": grouped.get("Vegetable A", []),
            "label": "08 Vegetable A (Mandatory - Select at least one and up to three maximum)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "90g total (85g if seeds selected), split among selections"
        },
        "Vegetable B": {
            "items": grouped.get("Vegetable B", []),
            "label": "09 Vegetable B (Optional - Pick up to two)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "notes": "40g total (35g if seeds selected), split among selections"
        },
        "Vegetable C": {
            "items": grouped.get("Vegetable C", []),
            "label": "10 Vegetable C - Potatoes (Optional - Pick up to one)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "dm_range": {"min": 0, "max": 50},
            "percentage": "50g — taken from Grain A (Grain A reduces by 50g)",
            "notes": "Optional: Select up to one potato. 50g taken from Grain B (or Grain A if no Grain B)."
        },
        "Fruit": {
            "items": grouped.get("Fruit", []),
            "label": "11 Fruit (Mandatory - Select at least one and up to two maximum)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 2,
            "dm_range": {"min": 0, "max": 20},
            "notes": "20g total, split among selections"
        },
        "Oil": {
            "items": grouped.get("Oil", []),
            "label": "12 Oil (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "Mandatory: 10g total, split among selections"
        },
        "Fiber": {
            "items": grouped.get("Fiber", []),
            "label": "13 Fiber/Seeds (Optional - Pick up to two)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": FIBER_SUPP_MAX_G},
            "percentage": f"Max {FIBER_SUPP_MAX_G}g - reduces Veg A to 85g & Veg B to 35g",
            "notes": "Optional: Seeds/fiber supplements. Selecting reduces vegetable allocation by 10g."
        },
        "Mineral Group A": {
            "items": grouped.get("Mineral Group A", []),
            "label": "14 Mineral Group A",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 2,
            "dm_range": {"min": 11, "max": 22},
            "percentage": "20g base (22g if Group B not selected) — split evenly if both selected",
            "notes": "Mandatory: Select Eggshells, Calcium Carbonate, or both. Gets +2g bonus if Group B not selected."
        },
        "Mineral Group B": {
            "items": grouped.get("Mineral Group B", []),
            "label": "15 Mineral Group B",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "dm_range": {"min": 4, "max": 8},
            "percentage": "8g total — one selected: 8g | both selected: 4g each",
            "notes": "Optional: Select Bone Meal, Blood Meal, or both. If not selected, 2g goes to Group A and 6g to Grain A."
        }
    }
    
    for category_key, config in category_configs.items():
        if config["items"]:
            categories_with_metadata[category_key] = config
    
    return categories_with_metadata


# ============================================================================
# Helper to calculate per-ingredient nutrient contribution
# ============================================================================
def _calc_ingredient_contribution(row: pd.Series, dm: float, fresh_weight: float = None) -> Dict[str, Any]:
    """Calculate nutrient contribution for a single ingredient."""
    
    def get_col(col_name, default=0.0):
        try:
            val = float(row.get(col_name, default))
            return default if pd.isna(val) else val
        except:
            return default
    
    # Get water percent and calculate fresh weight if not provided
    water_pct = get_water_percent(row)
    if fresh_weight is None:
        fresh_weight = calculate_fresh_weight(dm, water_pct)
    
    # Calculate values
    calcium_val = round(get_col("calcium_mg") * dm / 100.0, 3)
    phosphorus_val = round(get_col("phosphorus_mg") * dm / 100.0, 3)
    
    return {
        "ingredient": row["ingredient_name"],
        "dm_g": round(dm, 2),
        "fresh_weight_g": round(fresh_weight, 2),
        "water_percent": round(water_pct, 2),
        "fiber_g": round(get_col("fiber_g") * dm / 100.0, 3),
        # Macros (g)
        "protein_g": round(get_col("protein_g") * dm / 100.0, 3),
        "fat_g": round(get_col("fat_g") * dm / 100.0, 3),
        "cho_g": round(get_col("cho_g") * dm / 100.0, 3),
        "ash_g": round(get_col("ash_g") * dm / 100.0, 3),
        # Major Minerals (mg)
        "calcium_mg": calcium_val,
        "ca_mg": calcium_val,
        "Ca_mg": calcium_val,
        "phosphorus_mg": phosphorus_val,
        "p_mg": phosphorus_val,
        "P_mg": phosphorus_val,
        "magnesium_mg": round(get_col("magnesium_mg") * dm / 100.0, 3),
        "potassium_mg": round(get_col("potassium_mg") * dm / 100.0, 3),
        "sodium_mg": round(get_col("sodium_mg") * dm / 100.0, 3),
        # Trace Minerals (mg)
        "iron_mg": round(get_col("iron_mg") * dm / 100.0, 3),
        "zinc_mg": round(get_col("zinc_mg") * dm / 100.0, 3),
        "copper_mg": round(get_col("copper_mg") * dm / 100.0, 3),
        "iodine_mg": round(get_col("iodine_mg") * dm / 100.0, 4),
        "selenium_mg": round(get_col("selenium_mg") * dm / 100.0, 4),
        # B Vitamins (mg or ug)
        "thiamin_mg": round(get_col("thiamin_mg") * dm / 100.0, 4),
        "riboflavin_mg": round(get_col("riboflavin_mg") * dm / 100.0, 4),
        "niacin_mg": round(get_col("niacin_mg") * dm / 100.0, 4),
        "pantothenic_acid_mg": round(get_col("pantothenic_acid_mg") * dm / 100.0, 4),
        "vitamin_b6_mg": round(get_col("vitamin_b6_mg") * dm / 100.0, 4),
        "folate_ug": round(get_col("folate_ug") * dm / 100.0, 4),
        "choline_mg": round(get_col("choline_mg") * dm / 100.0, 4),
        "vitamin_b12_ug": round(get_col("vitamin_b12_ug") * dm / 100.0, 4),
        # Fat-soluble Vitamins (IU or mg)
        "vitamin_a_iu": round(get_col("vitamin_a_iu") * dm / 100.0, 2),
        "vitamin_e_mg": round(get_col("vitamin_e_mg") * dm / 100.0, 4),
        "vitamin_d_iu": round(get_col("vitamin_d_iu") * dm / 100.0, 2),
        # Fatty Acids (g)
        "linoleic_g": round(get_col("pufa_18_2_g") * dm / 100.0, 4),
        "ala_g": round(get_col("pufa_18_3_g") * dm / 100.0, 4),
        "epa_g": round(get_col("pufa_20_5_g") * dm / 100.0, 4),
        "dha_g": round(get_col("pufa_22_6_g") * dm / 100.0, 4),
        # Amino Acids (g)
        "tryptophan_g": round(get_col("tryptophan_g") * dm / 100.0, 4),
        "threonine_g": round(get_col("threonine_g") * dm / 100.0, 4),
        "isoleucine_g": round(get_col("isoleucine_g") * dm / 100.0, 4),
        "leucine_g": round(get_col("leucine_g") * dm / 100.0, 4),
        "lysine_g": round(get_col("lysine_g") * dm / 100.0, 4),
        "methionine_g": round(get_col("methionine_g") * dm / 100.0, 4),
        "cystine_g": round(get_col("cystine_g") * dm / 100.0, 4),
        "phenylalanine_g": round(get_col("phenylalanine_g") * dm / 100.0, 4),
        "tyrosine_g": round(get_col("tyrosine_g") * dm / 100.0, 4),
        "valine_g": round(get_col("valine_g") * dm / 100.0, 4),
        "arginine_g": round(get_col("arginine_g") * dm / 100.0, 4),
        "histidine_g": round(get_col("histidine_g") * dm / 100.0, 4),
        # Energy
        "energy_kcal": round(get_col("energy_kcal") * dm / 100.0, 2),
    }


# ============================================================================
# Main calculate_diet function with FIBER ENFORCEMENT
# ============================================================================
def calculate_diet(selected_names: List[str]) -> Dict[str, Any]:
    """
    Calculate diet from selected ingredients.
    
    FIXED ALLOCATION RULES (must total 1000g DM):
    - Meat: 205g (A only: 205g | A+B: 155+50 | A+B+C: 115+40+50)
    - Grains: 350g (A only: 350g | A+B: 250+100) — potato takes 50g from Grain B if selected, else Grain A
    - Vegetable C (Potato): 50g optional — taken from Grain B (or Grain A if no B)
    - Liver: 155g alone / 125g with organ (organ gets 30g)
    - Vegetables: 130g (A:90 + B:40) — reduced to 120g if seeds selected (A:85 + B:35)
    - Fruits: 20g
    - Oil: 10g
    - Seeds (optional): 10g (reduces veg by 10g total)
    - Mineral Group A: 20g base (22g if Group B not selected — one: 20/22g | both: 10/11g each)
    - Mineral Group B: 8g optional (one: 8g | both: 4g each) — if not selected: +2g to Group A, +6g to Grain A
    
    Diet quality targets:
    - Protein %: 32–40%
    - Fat %: >12% and <17%
    - CHO %: >30% and <45%
    - Fiber %: >3% and <6.5%
    - Energy density (kcal/kg): 4000–4500
    - Ca:P: >1.4:1 and <2:1
    - Omega-6 : Omega-3: >2 and <6
    
    Returns: ingredient_allocations with fresh weights for each ingredient.
    """
    
    fdf = fixed_df()
    udf = user_df()
    
    # ---------- User selections ----------
    seen: set = set()
    picks: List[pd.Series] = []
    
    for name in selected_names:
        key = _normkey(name)
        if key in seen:
            continue
        seen.add(key)
        found = udf[udf["ingredient_name"].apply(_normkey) == key]
        if not found.empty:
            picks.append(found.iloc[0])
    
    # ---------- Partition by group ----------
    meats_a = [r for r in picks if gname(r) == "meat group a"]
    meats_b = [r for r in picks if gname(r) == "meat group b"]
    meats_c = [r for r in picks if gname(r) == "meat group c"]
    grains_a = [r for r in picks if gname(r) == "grain a"]
    grains_b = [r for r in picks if gname(r) == "grain b"]
    veg_a = [r for r in picks if gname(r) == "vegetable a"]
    veg_b = [r for r in picks if gname(r) == "vegetable b"]
    veg_c = [r for r in picks if gname(r) == "vegetable c"]
    oils = [r for r in picks if gname(r) == "oil"]
    fruits = [r for r in picks if gname(r) == "fruit"]
    livers = [r for r in picks if gname(r) == "organ meat (liver)"]
    organs = [r for r in picks if gname(r) == "organ meat"]
    fiber_supps = [r for r in picks if gname(r) == "fiber"]
    minerals   = [r for r in picks if gname(r) == "mineral group a"]
    minerals_b = [r for r in picks if gname(r) == "mineral group b"]
    
    # ---------- Validation ----------
    issues: List[str] = []
    if not grains_a:
        issues.append("ERROR: Grain A is MANDATORY - at least one Grain A item must be selected.")
    if not meats_a:
        issues.append("ERROR: Meat Group A is MANDATORY - at least one Meat Group A item must be selected.")
    if not oils:
        issues.append("ERROR: At least one Oil must be selected.")
    if not livers:
        issues.append("ERROR: Liver is MANDATORY.")
    if not veg_a:
        issues.append("ERROR: Vegetable A is MANDATORY - at least one Vegetable A item must be selected.")
    if not fruits:
        issues.append("ERROR: Fruit is MANDATORY - at least one Fruit must be selected.")
    if not minerals:
        issues.append("ERROR: Minerals is MANDATORY - select Eggshells, Calcium Carbonate, or both.")
    
    total_veg_items = len(veg_a) + len(veg_b)
    if total_veg_items < 2:
        issues.append("ERROR: Must select at least 2 vegetables total from Group A and/or Group B.")
    
    # ============================================================================
    # DETERMINE FIXED ALLOCATIONS BASED ON SELECTIONS
    # ============================================================================
    
    has_seeds = len(fiber_supps) > 0
    has_organ = len(organs) > 0
    has_minerals   = len(minerals) > 0
    has_minerals_b = len(minerals_b) > 0
    has_meat_b = len(meats_b) > 0
    has_meat_c = len(meats_c) > 0
    has_grain_b = len(grains_b) > 0
    has_potato = len(veg_c) > 0
    
    # --- MEAT ALLOCATION (205g total — Meat A gets +9g) ---
    if has_meat_b and has_meat_c:
        # A + B + C: 115 + 40 + 50
        meat_a_dm = 115.0
        meat_b_dm = 40.0
        meat_c_dm = 50.0
    elif has_meat_b:
        # A + B: 155 + 50
        meat_a_dm = 155.0
        meat_b_dm = 50.0
        meat_c_dm = 0.0
    elif has_meat_c:
        # A + C: 159 + 50
        meat_a_dm = 155.0
        meat_b_dm = 0.0
        meat_c_dm = 50.0
    else:
        # A only: 205g
        meat_a_dm = 205.0
        meat_b_dm = 0.0
        meat_c_dm = 0.0
    
    # --- GRAIN ALLOCATION (350g total) ---
    # Potato rule: If Grain B is selected, potato takes 50g from Grain B.
    #              If Grain B is NOT selected, potato takes 50g from Grain A.
    if has_potato:
        potato_dm = POTATO_DM  # 50g
        if has_grain_b:
            # Grain B absorbs the potato swap: 100 - 50 = 50
            grain_a_dm = 250.0
            grain_b_dm = 50.0
        else:
            # No Grain B — Grain A absorbs: 350 - 50 = 300
            grain_a_dm = 300.0
            grain_b_dm = 0.0
    else:
        potato_dm = 0.0
        if has_grain_b:
            grain_a_dm = 250.0
            grain_b_dm = 100.0
        else:
            grain_a_dm = 350.0
            grain_b_dm = 0.0
    
    # --- LIVER / ORGAN ALLOCATION ---
    if has_organ:
        liver_dm = LIVER_WITH_ORGAN_DM   # 125g
        organ_dm = ORGAN_OTHER_DM         # 30g
    else:
        liver_dm = LIVER_ALONE_DM         # 155g
        organ_dm = 0.0
    
    # --- VEGETABLE ALLOCATION (130g normal, 120g with seeds) ---
    has_veg_b = len(veg_b) > 0
    if has_seeds:
        veg_a_dm_alloc = VEG_A_WITH_SEEDS_DM                  # 85g
        veg_b_dm_alloc = VEG_B_WITH_SEEDS_DM if has_veg_b else 0.0  # 35g or 0
        # If no Veg B, give all veg allocation to Veg A (85+35=120g)
        if not has_veg_b:
            veg_a_dm_alloc = VEG_TOTAL_WITH_SEEDS_DM          # 120g
        seeds_dm = SEEDS_DM                                    # 10g
    else:
        veg_a_dm_alloc = VEG_A_DM                             # 90g
        veg_b_dm_alloc = VEG_B_DM if has_veg_b else 0.0      # 40g or 0
        # If no Veg B, give all veg allocation to Veg A (90+40=130g)
        if not has_veg_b:
            veg_a_dm_alloc = VEG_TOTAL_DM                     # 130g
        seeds_dm = 0.0
    
    # --- FIXED ALLOCATIONS ---
    fruits_dm = FRUITS_DM if fruits else 0.0      # 20g
    oils_dm = OILS_DM if oils else 0.0            # 8g
    # --- MINERAL ALLOCATION ---
    # Group A: 20g base. If Group B not selected, Group A gets +2g bonus (22g total).
    # Group B: 8g total if selected, else 0g. Remaining 6g goes to Grain A.
    if has_minerals_b:
        mineral_a_dm = MINERALS_A_BASE_DM                          # 20g
        mineral_b_dm = MINERALS_B_TOTAL_DM                         # 8g
    else:
        mineral_a_dm = MINERALS_A_BASE_DM + MINERALS_A_BONUS_DM    # 22g
        mineral_b_dm = 0.0
    
    if not has_minerals:
        mineral_a_dm = 0.0
    
    # ============================================================================
    # ADJUST GRAIN A TO ENSURE TOTAL = EXACTLY 1000g
    # Fixed CSV ingredients may not total exactly 130g (e.g. eggshells/wheatgerm
    # changes), so Grain A absorbs the difference to keep total at 1000g.
    # ============================================================================
    # Fixed ingredients from CSV (supplements, etc.)
    def _safe_dm(val):
        try:
            v = float(val)
            return v if not __import__("pandas").isna(v) else 0.0
        except (ValueError, TypeError):
            return 0.0
    def _fixed_dm_with_overrides(r):
        name = str(r.get("ingredient_name", "")).strip().lower()
        if name == "cod liver oil":
            return _cod_liver_oil_dm(meats_a)
        override = FIXED_OVERRIDES.get(name)
        if override is None and name in FIXED_OVERRIDES:
            return 0.0   # explicitly skipped
        if isinstance(override, float):
            return override
        return _safe_dm(r["dm_g"])
    fixed_ingredients_dm = sum(_fixed_dm_with_overrides(r) for _, r in fdf.iterrows())
    
    # Calculate what everything EXCEPT grain_a totals
    non_grain_a_total = (
        fixed_ingredients_dm +
        meat_a_dm + meat_b_dm + meat_c_dm +
        grain_b_dm +
        liver_dm + organ_dm +
        veg_a_dm_alloc + veg_b_dm_alloc + potato_dm +
        fruits_dm + oils_dm + seeds_dm +
        mineral_a_dm + mineral_b_dm
    )
    
    # Grain A gets whatever is left to hit exactly 1000g
    grain_a_dm = FIXED_TOTAL_DM - non_grain_a_total
    
    if grain_a_dm < 0:
        issues.append(
            f"ERROR: Non-Grain-A categories total {non_grain_a_total:.1f}g, "
            f"exceeding {FIXED_TOTAL_DM:.0f}g budget. Grain A cannot be negative."
        )
        grain_a_dm = 0.0
    
    total_allocated = non_grain_a_total + grain_a_dm
    
    if abs(total_allocated - FIXED_TOTAL_DM) > 1.0:
        issues.append(
            f"WARNING: Total allocated DM = {total_allocated:.1f}g "
            f"(expected {FIXED_TOTAL_DM:.0f}g). Difference = {total_allocated - FIXED_TOTAL_DM:.1f}g."
        )
    
    # ============================================================================
    # BUILD THE DIET
    # ============================================================================
    dm_breakdown_raw: List[Dict[str, Any]] = []
    ingredient_totals: List[Dict[str, Any]] = []
    ingredient_allocations: Dict[str, Dict[str, Any]] = {}
    totals = _init_totals()
    remaining = FIXED_TOTAL_DM
    
    def _add_item(row: pd.Series, dm: float, is_fixed: bool = False):
        nonlocal remaining
        if dm <= 0 or remaining <= 0:
            return 0.0
        dm = min(dm, remaining)
        name = row["ingredient_name"]
        _add_row(totals, dm, row)
        
        water_pct = get_water_percent(row)
        fresh_wt = calculate_fresh_weight(dm, water_pct)
        
        dm_breakdown_raw.append({
            "ingredient": name, 
            "dm_g": round(dm, 2), 
            "fresh_weight_g": round(fresh_wt, 2),
            "water_percent": water_pct,
            "fixed": is_fixed
        })
        ingredient_totals.append(_calc_ingredient_contribution(row, dm, fresh_wt))
        
        if name in ingredient_allocations:
            ingredient_allocations[name]["dm_g"] += round(dm, 2)
            ingredient_allocations[name]["fresh_weight_g"] += round(fresh_wt, 2)
        else:
            ingredient_allocations[name] = {
                "dm_g": round(dm, 2),
                "fresh_weight_g": round(fresh_wt, 2),
                "water_percent": water_pct,
                "fixed": is_fixed
            }
        
        remaining -= dm
        return dm
    
    # 1. Fixed ingredients from CSV (supplements, etc.)
    # Applies FIXED_OVERRIDES: None = skip entirely, float = override dm_g
    for _, r in fdf.iterrows():
        name = str(r.get("ingredient_name", "")).strip()
        name_key = name.lower()

        # Check override
        if name_key == "cod liver oil":
            dm = _cod_liver_oil_dm(meats_a)
        elif name_key in FIXED_OVERRIDES:
            override = FIXED_OVERRIDES[name_key]
            if override is None:
                continue  # skip entirely — invisible, not calculated
            dm = float(override)
        else:
            raw_dm = r.get("dm_g", 0)
            try:
                dm = float(raw_dm)
                if __import__("pandas").isna(dm):
                    dm = 0.0
            except (ValueError, TypeError):
                dm = 0.0

        if dm > 0:
            _add_item(r, dm, is_fixed=True)
        else:
            if name:
                water_pct = get_water_percent(r)
                dm_breakdown_raw.append({
                    "ingredient": name,
                    "dm_g": 0.0,
                    "fresh_weight_g": 0.0,
                    "water_percent": water_pct,
                    "fixed": True
                })
    
    # 2. Liver
    if livers:
        per = liver_dm / len(livers)
        for r in livers:
            _add_item(r, per)
    
    # 3. Other organs
    if organs and has_organ:
        per = organ_dm / len(organs)
        for r in organs:
            _add_item(r, per)
    
    # 4. Oils
    if oils:
        per = oils_dm / len(oils)
        for r in oils:
            _add_item(r, per)
    
    # 5. Vegetables A
    if veg_a:
        per = veg_a_dm_alloc / len(veg_a)
        for r in veg_a:
            _add_item(r, per)
    
    # 6. Vegetables B
    if veg_b:
        per = veg_b_dm_alloc / len(veg_b)
        for r in veg_b:
            _add_item(r, per)
    
    # 7. Vegetable C (Potatoes) — 50g, taken from Grain B (or Grain A if no B)
    if veg_c:
        _add_item(veg_c[0], potato_dm)  # max 1 selection
    
    # 8. Fruits
    if fruits:
        per = fruits_dm / len(fruits)
        for r in fruits:
            _add_item(r, per)
    
    # 9. Seeds/Fiber (optional - 10g total, reduces veg by 10g)
    if fiber_supps:
        per = seeds_dm / len(fiber_supps)
        for r in fiber_supps:
            _add_item(r, per)
    
    # 10. Mineral Group A (mandatory - 20g base, 22g if Group B not selected)
    if minerals:
        per = mineral_a_dm / len(minerals)
        for r in minerals:
            _add_item(r, per)
    
    # 10b. Mineral Group B (optional - 8g total, split evenly if both selected)
    if minerals_b:
        per = mineral_b_dm / len(minerals_b)
        for r in minerals_b:
            _add_item(r, per)
    
    # 12. Grains A
    if grains_a:
        per = grain_a_dm / len(grains_a)
        for r in grains_a:
            _add_item(r, per)
    
    # 13. Grains B
    if grains_b:
        per = grain_b_dm / len(grains_b)
        for r in grains_b:
            _add_item(r, per)
    
    # 14. Meat A
    if meats_a:
        per = meat_a_dm / len(meats_a)
        for r in meats_a:
            _add_item(r, per)
    
    # 15. Meat B
    if meats_b:
        per = meat_b_dm / len(meats_b)
        for r in meats_b:
            _add_item(r, per)
    
    # 16. Meat C
    if meats_c:
        per = meat_c_dm / len(meats_c)
        for r in meats_c:
            _add_item(r, per)
    
    # ============================================================================
    # DIET QUALITY CHECKS
    # ============================================================================
    protein_pct = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
    fat_pct = totals["Fat"] * 100.0 / FIXED_TOTAL_DM
    cho_pct = totals["CHO"] * 100.0 / FIXED_TOTAL_DM
    fiber_pct = totals["Fiber"] * 100.0 / FIXED_TOTAL_DM
    
    # Energy calculation
    protein_g_per_100g = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
    fat_g_per_100g = totals["Fat"] * 100.0 / FIXED_TOTAL_DM
    cho_g_per_100g = totals["CHO"] * 100.0 / FIXED_TOTAL_DM
    energy_kcal_per_kg = ((protein_g_per_100g * 4) + (fat_g_per_100g * 9) + (cho_g_per_100g * 4)) * 10
    
    # Ca:P ratio
    total_ca_mg = totals["Ca"] * 1000
    total_p_mg = totals["P"] * 1000
    ca_p_ratio = (total_ca_mg / total_p_mg) if total_p_mg else 0.0
    
    # Omega-6 : Omega-3 ratio
    omega6 = totals.get("Linoleic_acid", 0)
    omega3 = totals.get("Alpha_linolenic_acid", 0) + totals.get("EPA", 0) + totals.get("DHA", 0)
    omega6_omega3_ratio = (omega6 / omega3) if omega3 > 0 else 0.0
    
    # --- Quality target warnings ---
    if protein_pct < PROTEIN_MIN:
        issues.append(f"WARNING: Protein is {protein_pct:.1f}% (target: {PROTEIN_MIN}–{PROTEIN_MAX}%). Too low.")
    elif protein_pct > PROTEIN_MAX:
        issues.append(f"WARNING: Protein is {protein_pct:.1f}% (target: {PROTEIN_MIN}–{PROTEIN_MAX}%). Too high.")
    
    if fat_pct <= FAT_MIN:
        issues.append(f"WARNING: Fat is {fat_pct:.1f}% (target: >{FAT_MIN}% and <{FAT_MAX}%). Too low.")
    elif fat_pct >= FAT_MAX:
        issues.append(f"WARNING: Fat is {fat_pct:.1f}% (target: >{FAT_MIN}% and <{FAT_MAX}%). Too high.")
    
    if cho_pct <= CHO_MIN:
        issues.append(f"WARNING: CHO is {cho_pct:.1f}% (target: >{CHO_MIN}% and <{CHO_MAX}%). Too low.")
    elif cho_pct >= CHO_MAX:
        issues.append(f"WARNING: CHO is {cho_pct:.1f}% (target: >{CHO_MIN}% and <{CHO_MAX}%). Too high.")
    
    if fiber_pct <= MIN_TOTAL_FIBER_PCT:
        issues.append(f"WARNING: Fiber is {fiber_pct:.1f}% (target: >{MIN_TOTAL_FIBER_PCT}% and <{MAX_TOTAL_FIBER_PCT}%). Too low.")
    elif fiber_pct >= MAX_TOTAL_FIBER_PCT:
        issues.append(f"WARNING: Fiber is {fiber_pct:.1f}% (target: >{MIN_TOTAL_FIBER_PCT}% and <{MAX_TOTAL_FIBER_PCT}%). Too high.")
    
    if energy_kcal_per_kg < ENERGY_MIN:
        issues.append(f"WARNING: Energy density is {energy_kcal_per_kg:.0f} kcal/kg (target: {ENERGY_MIN:.0f}–{ENERGY_MAX:.0f}). Too low.")
    elif energy_kcal_per_kg > ENERGY_MAX:
        issues.append(f"WARNING: Energy density is {energy_kcal_per_kg:.0f} kcal/kg (target: {ENERGY_MIN:.0f}–{ENERGY_MAX:.0f}). Too high.")
    
    if ca_p_ratio < CA_P_RATIO_MIN:
        issues.append(f"WARNING: Ca:P ratio is {ca_p_ratio:.2f}:1 (target: >{CA_P_RATIO_MIN}:1 and <{CA_P_RATIO_MAX}:1). Too low.")
    elif ca_p_ratio > CA_P_RATIO_MAX:
        issues.append(f"WARNING: Ca:P ratio is {ca_p_ratio:.2f}:1 (target: >{CA_P_RATIO_MIN}:1 and <{CA_P_RATIO_MAX}:1). Too high.")
    
    if omega3 > 0:
        if omega6_omega3_ratio < OMEGA6_OMEGA3_RATIO_MIN:
            issues.append(f"WARNING: Omega-6:Omega-3 ratio is {omega6_omega3_ratio:.1f}:1 (target: >{OMEGA6_OMEGA3_RATIO_MIN}:1 and <{OMEGA6_OMEGA3_RATIO_MAX}:1). Too low.")
        elif omega6_omega3_ratio > OMEGA6_OMEGA3_RATIO_MAX:
            issues.append(f"WARNING: Omega-6:Omega-3 ratio is {omega6_omega3_ratio:.1f}:1 (target: >{OMEGA6_OMEGA3_RATIO_MIN}:1 and <{OMEGA6_OMEGA3_RATIO_MAX}:1). Too high.")
    
    # ---------- Finalize ----------
    def pct(key: str) -> float:
        return round(totals[key] * 100.0 / FIXED_TOTAL_DM, 2)
    
    # Calculate total fresh weight
    total_fresh_weight = sum(alloc["fresh_weight_g"] for alloc in ingredient_allocations.values())
    
    result = {
        "Protein_percent": pct("Protein"),
        "Fat_percent": pct("Fat"),
        "CHO_percent": pct("CHO"),
        "Fiber_percent": pct("Fiber"),
        "Ash_percent": pct("Ash"),
        "Ca_percent": round(totals["Ca"] / FIXED_TOTAL_DM * 100, 4),
        "P_percent": round(totals["P"] / FIXED_TOTAL_DM * 100, 4),
        "Ca_P_ratio": round(ca_p_ratio, 2),
        "Energy": round(energy_kcal_per_kg, 2),
        "DM_percent": FIXED_TOTAL_DM,
        "iron_mg": round(totals["Iron"] * 1000 / FIXED_TOTAL_DM, 2),
        "total_fresh_weight_g": round(total_fresh_weight, 2),
        "omega6_omega3_ratio": round(omega6_omega3_ratio, 2),
        # Allocation summary (for transparency)
        "allocation_summary": {
            "meat_a_dm": meat_a_dm,
            "meat_b_dm": meat_b_dm,
            "meat_c_dm": meat_c_dm,
            "grain_a_dm": grain_a_dm,
            "grain_b_dm": grain_b_dm,
            "potato_dm": potato_dm,
            "liver_dm": liver_dm,
            "organ_dm": organ_dm,
            "veg_a_dm": veg_a_dm_alloc,
            "veg_b_dm": veg_b_dm_alloc,
            "fruits_dm": fruits_dm,
            "oils_dm": oils_dm,
            "seeds_dm": seeds_dm,
            "seeds_selected": has_seeds,
            "potato_selected": has_potato,
            "mineral_a_dm": mineral_a_dm,
            "mineral_b_dm": mineral_b_dm,
            "minerals_a_selected": len(minerals),
            "minerals_b_selected": len(minerals_b),
        }
    }
    
    # Add extended nutrient output for router
    _add_extended_nutrient_output(result, totals, FIXED_TOTAL_DM)
    
    # Calculate AAFCO % of minimum
    aafco_percent_of_minimum = calculate_aafco_percent_of_minimum(totals, FIXED_TOTAL_DM)
    
    result.update({
        "dm_breakdown": _compress_breakdown(dm_breakdown_raw),
        "ingredient_totals": ingredient_totals,
        "ingredient_allocations": ingredient_allocations,
        "issues": issues,
        "aafco_percent_of_minimum": aafco_percent_of_minimum,
    })
    
    return result
