"""
Dog Diet Planner - Ingredients Service (FINAL VERSION with Fresh Weights)

This version matches exactly with:
- fixed_ingredients_updated.csv
- user_ingredients_updated.csv

All 47 nutrient columns are now tracked including histidine_g.

FIBER LIMITS ENFORCED:
- Fiber supplements: Maximum 10g
- TOTAL DIETARY FIBER: Maximum 9.5% of diet DM - AUTO-BALANCED

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
    "ingredient_name", "group_name", "protein_g", "fat_g", "cho_g", "fiber_g", "ash_g",
    "calcium_mg", "phosphorus_mg", "iron_mg", "energy_kcal"
}

# ============================================================================
# FIBER LIMITS - STRICTLY ENFORCED
# ============================================================================
FIBER_SUPP_MAX_G = 10.0       # Hard limit: 10g maximum total for fiber supplements
MAX_TOTAL_FIBER_PCT = 9.5     # Maximum 9.5% total dietary fiber in the diet

# Fixed ingredient overrides — key is lowercase stripped name
# None  = skip entirely (invisible, not calculated)
# float = override dm_g to this value
FIXED_OVERRIDES = {
    "fish oil":      None,   # Removed — cod liver oil covers the full oil allocation
    "cod liver oil": 20.0,   # Doubled from CSV 10g to 20g
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

# Mineral Group constants
MINERALS_A_BASE_DM  = 20.0   # Mineral Group A base allocation
MINERALS_A_BONUS_DM = 2.0    # Bonus to Group A when Group B not selected
MINERALS_B_TOTAL_DM = 8.0    # Mineral Group B total (bone meal / blood meal)


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    # Remove any Unnamed columns
    df = df.loc[:, ~df.columns.str.contains('^unnamed', case=False)]
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

    # Note: cod liver oil dm_g is now determined dynamically per-request based on
    # Meat Group A selection (see _cod_liver_oil_dm). Fish oil remains zeroed here.
    fish_oil_mask = f["ingredient_name"].str.strip().str.lower() == "fish oil"
    f.loc[fish_oil_mask, "dm_g"] = 0.0

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
        "Vitamin_B6": 0.0, "Folate": 0.0, "Vitamin_B12": 0.0,
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
    
    # FOLATE: CSV has folate_ug (micrograms), convert to mg
    totals["Folate"] += get_col("folate_ug") * dm / 100.0
    
    # VITAMIN B12: CSV has vitamin_b12_ug (micrograms), convert to mg
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
    raw = str(s.get("group_name", "")).lower()
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
        group_name = str(row["group_name"]).strip()
        
        # Handle organ meat categorization
        # Use CSV group_name as the source of truth — do NOT override based on ingredient name.
        # CSV explicitly marks "Organ Meat (Liver)" for Beef/Chicken/Lamb/Pork liver.
        # Turkey liver, Duck liver, Goose liver are deliberately in "Organ Meat" group.
        group_lower = group_name.lower()
        
        if group_lower == "organ meat (liver)":
            display_group = "Organ Meat (Liver)"
        elif group_lower == "organ meat":
            display_group = "Organ Meat (Other)"
        else:
            display_group = group_name
        
        # Skip rows with missing/blank ingredient names
        if not ingredient_name or ingredient_name.lower() in ('nan', 'none', ''):
            continue
        
        ingredient_dict = row.to_dict()
        ingredient_dict["ingredient_name"] = ingredient_name  # Ensure string
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
        grouped[category].sort(key=lambda x: str(x.get("ingredient_name", "") or "").lower())
    
    categories_with_metadata = {}
    
    # No selection constraints — all groups allow any number of selections
    category_configs = {
        "Meat Group A": {
            "items": grouped.get("Meat Group A", []),
            "label": "01 Meat Group A (Mandatory - Select at least one and a maximum of three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "notes": "224g alone | 164g with B | 134g with B+C"
        },
        "Meat Group B": {
            "items": grouped.get("Meat Group B", []),
            "label": "02 Meat Group B (Optional - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "notes": "60g with A | 40g with A+C"
        },
        "Meat Group C": {
            "items": grouped.get("Meat Group C", []),
            "label": "03 Meat Group C (Optional - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "notes": "Fixed at 50g total when selected"
        },
        "Organ Meat (Other)": {
            "items": grouped.get("Organ Meat (Other)", []),
            "label": "04 Organ Meat - Other (Optional - Select upto three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": 30},
            "percentage": "30g fixed when selected (shared equally)",
            "notes": "30g total split equally across selected items"
        },
        "Organ Meat (Liver)": {
            "items": grouped.get("Organ Meat (Liver)", []),
            "label": "05 Organ Meat - Liver (Mandatory - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 125, "max": 155},
            "percentage": "155g alone; 125g when organ meat also selected",
            "notes": "155g alone; 125g when other organ meats selected — split equally"
        },
        "Grain A": {
            "items": grouped.get("Grain A", []),
            "label": "06 Grain A (Mandatory - Select at least one and a maximum of three, fixed at 30%)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 359, "max": 359},
            "percentage": "35.9% of diet (359g DM)",
            "notes": "Fixed at 359g of diet DM — split equally across selected items"
        },
        "Vegetable A": {
            "items": grouped.get("Vegetable A", []),
            "label": "07 Vegetable A (Mandatory - Select at least one, up to three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 90, "max": 90},
            "percentage": "9% of diet (90g DM, fixed)",
            "notes": "Fixed at 90g total — split equally across selected items"
        },
        "Vegetable B": {
            "items": grouped.get("Vegetable B", []),
            "label": "08 Vegetable B (Mandatory - Select at least one, up to three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 40, "max": 40},
            "percentage": "4% of diet (40g DM, fixed)",
            "notes": "Fixed at 40g total — split equally across selected items"
        },
        "Fruit": {
            "items": grouped.get("Fruit", []),
            "label": "09 Fruit (Mandatory - Select upto three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": 25},
            "notes": "25g total split equally across selected items"
        },
        "Oil": {
            "items": grouped.get("Oil", []),
            "label": "10 Oil (Mandatory - Select at least one and a maximum of three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 8, "max": 8},
            "percentage": "8g fixed",
            "notes": "Fixed at 8g total — split equally across selected items"
        },
        # Fiber/Seeds category removed - fiber supplements no longer offered as a diet option
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
    
    NEW ALLOCATION RULES:
    - Meat total: 224g — split by group:
        Only A: 224g | A+B: 164+60g | A+B+C: 134+40+50g
    - Grain A: FIXED at 359g. When fruits are selected, grain reduces to 334g and fruit takes 25g.
      Meat allocation is NEVER affected by fruit selection — fruits always carve from Grain A, not meat.
    - Fruit (optional): Fixed 25g total, carved out of Grain A budget — meat is NEVER reduced
    - Liver: 155g alone; 125g when organ meat also selected
    - Organ meat (non-liver): 30g total when selected
    - Vegetable A: Fixed 90g (split equally across selected A items)
    - Vegetable B: Fixed 40g (split equally across selected B items)
    - Oil: Fixed 8g (split equally across selected oils)
    - Fiber supplement / seeds category: REMOVED
    - Protein target range of 32-40% (advisory)
    - FIBER SUPPLEMENT LIMIT: Maximum 10g
    - TOTAL DIETARY FIBER: Maximum 9.5% - auto-balanced
    
    Returns: ingredient_allocations with fresh weights for each ingredient.
    """
    
    # Protein target range (advisory - reported but no binary search)
    PROTEIN_MIN = 32.0
    PROTEIN_MAX = 40.0
    
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
    veg_a = [r for r in picks if gname(r) == "vegetable a"]
    veg_b = [r for r in picks if gname(r) == "vegetable b"]
    oils = [r for r in picks if gname(r) == "oil"]
    fruits = [r for r in picks if gname(r) == "fruit"]
    livers = [r for r in picks if gname(r) == "organ meat (liver)"]
    organs = [r for r in picks if gname(r) == "organ meat"]
    fiber_supps = []  # Fiber/Seeds category removed - no longer offered as a diet option
    minerals   = [r for r in picks if gname(r) == "mineral group a"]
    minerals_b = [r for r in picks if gname(r) == "mineral group b"]
    # (If any fiber group items are passed, they are silently ignored)
    
    # ---------- Validation (warnings only - no selection constraints enforced) ----------
    issues: List[str] = []
    if not minerals:
        issues.append("ERROR: Mineral Group A is MANDATORY - select Eggshells, Calcium Carbonate, or both.")

    RECOMMENDED_MAX = {
        "Meat Group A": 3, "Meat Group B": 2, "Meat Group C": 2,
        "Organ Meat (Other)": 3, "Organ Meat (Liver)": 2,
        "Grain A": 3, "Vegetable A": 3, "Vegetable B": 3, "Fruit": 3, "Oil": 3,
    }
    group_counts = {
        "Meat Group A": len(meats_a), "Meat Group B": len(meats_b), "Meat Group C": len(meats_c),
        "Organ Meat (Other)": len(organs), "Organ Meat (Liver)": len(livers),
        "Grain A": len(grains_a), "Vegetable A": len(veg_a), "Vegetable B": len(veg_b),
        "Fruit": len(fruits), "Oil": len(oils),
    }
    for group, count in group_counts.items():
        rec_max = RECOMMENDED_MAX[group]
        if count > rec_max:
            issues.append(
                f"WARNING: {group} has {count} ingredients selected "
                f"(recommended maximum is {rec_max}). Diet will be calculated with all selected."
            )
    
    # ============================================================================
    # PRE-CALCULATE FIBER/SEEDS ALLOCATION (determined upfront, not affected by order)
    # Rules:
    # 1. If only one is chosen: 10g
    # 2. If two are chosen: 6g each (12g total)
    # 3. If Rice bran + Psyllium husk specifically: 5g each (10g total)
    # ============================================================================
    def get_fiber_allocation() -> Dict[str, float]:
        """Pre-calculate fiber/seeds allocation amounts."""
        if not fiber_supps:
            return {}
        
        num_fiber = len(fiber_supps)
        fiber_names = [str(r["ingredient_name"]).lower().strip() for r in fiber_supps]
        
        # Check for Rice bran + Psyllium husk combination
        has_rice_bran = any("rice bran" in name for name in fiber_names)
        has_psyllium = any("psyllium" in name for name in fiber_names)
        is_rice_psyllium_combo = has_rice_bran and has_psyllium and num_fiber == 2
        
        allocations = {}
        if num_fiber == 1:
            for r in fiber_supps:
                allocations[str(r["ingredient_name"])] = 10.0
        elif num_fiber == 2 and is_rice_psyllium_combo:
            for r in fiber_supps:
                allocations[str(r["ingredient_name"])] = 5.0
        else:
            for r in fiber_supps:
                allocations[str(r["ingredient_name"])] = 6.0
        
        return allocations
    
    # Pre-calculated fiber allocations (fixed amounts)
    FIBER_ALLOCATIONS = get_fiber_allocation()
    TOTAL_FIBER_DM = sum(FIBER_ALLOCATIONS.values())
    
    # ============================================================================
    # PRE-CALCULATE ALL FIXED ALLOCATIONS (NEW RULES)
    # Grain A: FIXED at 359g
    # Veg A: starts at 100g min, can go up to 150g max (top-up)
    # Veg B: starts at 50g min, can go up to 100g max (fiber-checked)
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
            return 0.0
        if isinstance(override, float):
            return override
        return _safe_dm(r["dm_g"])
    FIXED_INGREDIENTS_DM = sum(_fixed_dm_with_overrides(r) for _, r in fdf.iterrows())
    
    # ============================================================================
    # FIXED ALLOCATION RULES (all values in grams DM)
    # -----------------------------------------------------------------------
    # Meat total:  229g  — A is always the balancer
    #   - Only Meat A selected           : A = 229g
    #   - Meat A + B selected            : A = 169g, B = 60g
    #   - Meat A + C selected            : A = 179g, C = 50g
    #   - Meat A + B + C selected        : A = 139g, B = 40g, C = 50g
    # Grain A: DYNAMIC BALANCER — takes remaining budget after all other groups.
    #          Guarantees total DM = exactly 1000g. Fruits (20g) are separate.
    # Liver:   155g when liver only; 125g when liver + organ meat also selected
    # Organ meat (non-liver): 30g when selected alongside liver
    # Vegetables: Veg A alone = 130g | Veg A + B = 90g + 40g = 130g total
    # Fruits:  20g fixed (split equally across selected fruits), from grain budget
    # Oil:     8g fixed (split equally across selected oils)
    # Minerals A: 22g alone, 20g when Group B also selected
    # Minerals B: 8g when selected
    # Fixed CSV supplements: variable → Grand total DM always = 1000g
    # ============================================================================
    MEAT_TOTAL_DM      = 229.0
    MEAT_A_ONLY_DM     = 229.0   # all meat goes to group A
    MEAT_A_WITH_B_DM   = 169.0   # group A share when B also selected (A is balancer)
    MEAT_B_DM          = 60.0    # group B fixed share
    MEAT_A_WITH_BC_DM  = 139.0   # group A share when B and C also selected (A is balancer)
    MEAT_B_WITH_C_DM   = 40.0    # group B share when C also selected
    MEAT_C_DM          = 50.0    # group C fixed share (anchor)
    MEAT_A_WITH_C_DM   = 179.0   # group A share when only C selected (229 - 50)
    
    # --- MINERAL ALLOCATION ---
    # Group A: 20g base. If Group B not selected, Group A gets +2g bonus (22g total).
    # Group B: 8g total if selected, else 0g.
    # Minerals allocate independently from remaining — NOT carved from Grain A.
    has_minerals   = len(minerals) > 0
    has_minerals_b = len(minerals_b) > 0
    if has_minerals_b:
        mineral_a_dm = MINERALS_A_BASE_DM                          # 20g
        mineral_b_dm = MINERALS_B_TOTAL_DM                         # 8g
    else:
        mineral_a_dm = MINERALS_A_BASE_DM + MINERALS_A_BONUS_DM    # 22g
        mineral_b_dm = 0.0
    if not has_minerals:
        mineral_a_dm = 0.0

    FRUITS_DM          = 20.0 if fruits else 0.0
    # GRAIN_A_DM is computed dynamically inside calc_diet_allocation as the balancer.
    
    # Liver: 155g alone, 125g when organ meats also present
    LIVER_DM           = (125.0 if organs else 155.0) if livers else 0.0
    # Organ meat (non-liver): 30g total split across up to 2 organs
    ORGAN_DM_TOTAL     = 30.0 if organs else 0.0
    
    VEG_A_DM           = (130.0 if not veg_b else 90.0) if veg_a else 0.0   # 130g alone, 90g when B also selected
    VEG_B_DM           = 40.0   if veg_b   else 0.0
    OILS_DM            = 8.0    if oils    else 0.0

    
    # Compute meat group allocations based on which groups are present.
    # B and C are fully independent — either can be selected without the other.
    has_b = bool(meats_b)
    has_c = bool(meats_c)
    if has_b and has_c:
        MEAT_A_ALLOC = MEAT_A_WITH_BC_DM   # 134g
        MEAT_B_ALLOC = MEAT_B_WITH_C_DM    # 40g
        MEAT_C_ALLOC = MEAT_C_DM           # 50g
    elif has_b and not has_c:
        MEAT_A_ALLOC = MEAT_A_WITH_B_DM    # 164g
        MEAT_B_ALLOC = MEAT_B_DM           # 60g
        MEAT_C_ALLOC = 0.0
    elif has_c and not has_b:
        MEAT_A_ALLOC = MEAT_A_WITH_C_DM    # 179g  (A is balancer: 229 - 50)
        MEAT_B_ALLOC = 0.0
        MEAT_C_ALLOC = MEAT_C_DM           # 50g  (C is anchor)
    else:
        MEAT_A_ALLOC = MEAT_A_ONLY_DM      # 224g
        MEAT_B_ALLOC = 0.0
        MEAT_C_ALLOC = 0.0

    # ============================================================================
    # ALLOCATION FUNCTION — Grain A is the dynamic balancer
    # ============================================================================
    def calc_diet_allocation(veg_a_dm: float, veg_b_dm: float, fiber_seeds_scale: float = 1.0) -> tuple:
        """
        Calculate diet with specified Veg A / Veg B DM allocations.
        Grain A is the dynamic balancer — it takes whatever remaining budget is left
        after fixed CSV ingredients and all other user allocations, guaranteeing 1000g DM.
        """
        dm_breakdown_raw: List[Dict[str, Any]] = []
        ingredient_totals: List[Dict[str, Any]] = []
        ingredient_allocations: Dict[str, Dict[str, Any]] = {}
        totals = _init_totals()
        
        # Fixed items first
        fixed_dm_used = 0.0
        for _, r in fdf.iterrows():
            name_key = str(r.get("ingredient_name", "")).strip().lower()
            if name_key == "cod liver oil":
                dm = _cod_liver_oil_dm(meats_a)
            elif name_key in FIXED_OVERRIDES:
                override = FIXED_OVERRIDES[name_key]
                if override is None:
                    continue  # skip entirely
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
                _add_row(totals, dm, r)
                water_pct = get_water_percent(r)
                fresh_wt = calculate_fresh_weight(dm, water_pct)
                dm_breakdown_raw.append({
                    "ingredient": r["ingredient_name"],
                    "dm_g": round(dm, 2),
                    "fresh_weight_g": round(fresh_wt, 2),
                    "water_percent": water_pct,
                    "fixed": True
                })
                ingredient_totals.append(_calc_ingredient_contribution(r, dm, fresh_wt))
                ingredient_allocations[r["ingredient_name"]] = {
                    "dm_g": round(dm, 2),
                    "fresh_weight_g": round(fresh_wt, 2),
                    "water_percent": water_pct,
                    "fixed": True
                }
                fixed_dm_used += dm
            else:
                # dm_g is 0 or missing/blank in the CSV (e.g. ingredient not yet
                # filled in with nutrient data). Still show it in the fixed
                # ingredients list at 0g rather than dropping it silently.
                dm_breakdown_raw.append({
                    "ingredient": r["ingredient_name"],
                    "dm_g": 0.0,
                    "fresh_weight_g": 0.0,
                    "water_percent": get_water_percent(r),
                    "fixed": True
                })
                ingredient_allocations[r["ingredient_name"]] = {
                    "dm_g": 0.0,
                    "fresh_weight_g": 0.0,
                    "water_percent": get_water_percent(r),
                    "fixed": True
                }
        
        remaining = max(0.0, FIXED_TOTAL_DM - fixed_dm_used)

        # --- GRAIN A: dynamic balancer ---
        # All other user allocations are fixed; grain takes whatever is left
        # so that fixed_dm + all user allocations = exactly 1000g.
        other_user_dm = (
            MEAT_A_ALLOC + MEAT_B_ALLOC + MEAT_C_ALLOC
            + LIVER_DM + ORGAN_DM_TOTAL
            + veg_a_dm + veg_b_dm
            + FRUITS_DM
            + OILS_DM
            + mineral_a_dm + mineral_b_dm
        )
        GRAIN_A_DM = max(0.0, min(359.0, (remaining - other_user_dm) if grains_a else 0.0))
        
        def _add_item(row: pd.Series, dm: float):
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
                "fixed": False
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
                    "fixed": False
                }
            
            remaining -= dm
            return dm
        
        # MEATS FIRST: ensures full allocation before remaining budget is consumed
        if meats_a and MEAT_A_ALLOC > 0:
            per = MEAT_A_ALLOC / len(meats_a)
            for r in meats_a:
                _add_item(r, per)
        if meats_b and MEAT_B_ALLOC > 0:
            per = MEAT_B_ALLOC / len(meats_b)
            for r in meats_b:
                _add_item(r, per)
        if meats_c and MEAT_C_ALLOC > 0:
            per = MEAT_C_ALLOC / len(meats_c)
            for r in meats_c:
                _add_item(r, per)
        
        # Liver: 155g alone, 125g when organ meats also present — split equally
        if livers:
            per = LIVER_DM / len(livers)
            for r in livers:
                _add_item(r, per)
        
        # Organ meat (non-liver): 30g total split equally
        if organs:
            per = ORGAN_DM_TOTAL / len(organs)
            for r in organs:
                _add_item(r, per)
        
        # Oils: 8g total split equally
        if oils:
            per = OILS_DM / len(oils)
            for r in oils:
                _add_item(r, per)
        
        # GRAIN A: dynamic balancer — takes whatever remaining budget is left after all other groups.
        # This guarantees total DM = exactly 1000g. Fruits are allocated separately after grain.
        if grains_a:
            per = GRAIN_A_DM / len(grains_a)
            for r in grains_a:
                _add_item(r, per)
        
        # FRUITS: Fixed 25g total (split equally), replaces 25g of Grain A.
        if fruits and FRUITS_DM > 0:
            per = FRUITS_DM / len(fruits)
            for r in fruits:
                _add_item(r, per)
        
        # VEGETABLE A: Fixed 90g (split equally)
        if veg_a and veg_a_dm > 0:
            per = veg_a_dm / len(veg_a)
            for r in veg_a:
                _add_item(r, per)
        
        # VEGETABLE B: Fixed 40g (split equally), fiber-checked if needed
        if veg_b and veg_b_dm > 0:
            per = veg_b_dm / len(veg_b)
            for r in veg_b:
                _add_item(r, per)
        
        # FIBER/SEEDS: removed — fiber_supps is always empty

        # MINERAL GROUP A (mandatory - 20g base, 22g if Group B not selected)
        if minerals:
            per = mineral_a_dm / len(minerals)
            for r in minerals:
                _add_item(r, per)

        # MINERAL GROUP B (optional - 8g total, split evenly if both selected)
        if minerals_b:
            per = mineral_b_dm / len(minerals_b)
            for r in minerals_b:
                _add_item(r, per)
        
        # Calculate percentages (always based on 1000g)
        protein_pct = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
        fiber_pct = totals["Fiber"] * 100.0 / FIXED_TOTAL_DM
        
        return totals, dm_breakdown_raw, ingredient_totals, ingredient_allocations, protein_pct, fiber_pct, remaining
    
    # ============================================================================
    # All allocations are now fully fixed — no pre-calculation or binary search needed.
    # Run a single allocation pass with fixed Veg A (90g) and Veg B (40g).
    # Fiber enforcement still runs as a safety check on the fixed values.
    # ============================================================================
    
    MIN_FIBER_PCT = 3.0
    fiber_seeds_scale = 1.0   # kept for signature compatibility; fiber_supps is always empty
    adjustment_info = []
    
    best_veg_a_dm = VEG_A_DM   # fixed 90g (or 0 if no veg_a selected)
    best_veg_b_dm = VEG_B_DM   # fixed 40g (or 0 if no veg_b selected)
    
    _, _, _, _, _, initial_fiber_pct, _ = calc_diet_allocation(
        best_veg_a_dm, best_veg_b_dm, fiber_seeds_scale
    )
    
    # Safety check: if fiber somehow exceeds 9.5% with the fixed values, log a warning.
    # (With fixed allocations this should not normally trigger.)
    if initial_fiber_pct > MAX_TOTAL_FIBER_PCT:
        adjustment_info.append(
            f"WARNING: Fiber at {initial_fiber_pct:.1f}% exceeds {MAX_TOTAL_FIBER_PCT}% limit "
            f"with fixed allocations. Review ingredient fiber content."
        )
    
    # Final calculation
    totals, dm_breakdown_raw, ingredient_totals, ingredient_allocations, protein_pct, fiber_pct, remaining = \
        calc_diet_allocation(best_veg_a_dm, best_veg_b_dm, fiber_seeds_scale)
    
    # Add adjustment info
    if adjustment_info:
        msg = f"INFO: Fiber check on fixed allocations. " + ", ".join(adjustment_info)
        issues.append(msg)
    
    # Warn if fiber is too low (< 3%)
    if fiber_pct < MIN_FIBER_PCT:
        warning_msg = f"WARNING: Fiber is {fiber_pct:.1f}% (recommended >3%). Consider adding more fiber-rich ingredients."
        issues.append(warning_msg)
    
    # Protein advisory (no longer enforced by binary search, just reported)
    if protein_pct < PROTEIN_MIN:
        issues.append(f"INFO: Protein is {protein_pct:.1f}% (target 32-40%). Consider adjusting meat selections.")
    elif protein_pct > PROTEIN_MAX:
        issues.append(f"INFO: Protein is {protein_pct:.1f}% (target 32-40%). Consider adjusting meat selections.")


    # ---------- Finalize ----------
    def pct(key: str) -> float:
        return round(totals[key] * 100.0 / FIXED_TOTAL_DM, 2)
    
    # Calculate Ca:P ratio
    total_ca_mg = totals["Ca"] * 1000
    total_p_mg = totals["P"] * 1000
    ca_p_ratio = (total_ca_mg / total_p_mg) if total_p_mg else 0.0
    
    # Energy calculation
    protein_g_per_100g = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
    fat_g_per_100g = totals["Fat"] * 100.0 / FIXED_TOTAL_DM
    cho_g_per_100g = totals["CHO"] * 100.0 / FIXED_TOTAL_DM
    energy_kcal_per_kg = ((protein_g_per_100g * 4) + (fat_g_per_100g * 9) + (cho_g_per_100g * 4)) * 10
    
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
        # Scaling factors applied (for transparency)
        "fiber_adjustments": {
            "veg_a_dm": round(best_veg_a_dm, 2),
            "veg_b_dm": round(best_veg_b_dm, 2),
            "fiber_seeds_scale": round(fiber_seeds_scale, 2),
            "was_adjusted": False  # allocations are now fully fixed
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
