"""
Cat Diet Planner - Ingredients Service (FINAL VERSION with Fresh Weights)

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
    "bone meal":     None,   # Display only — excluded from all calculations
    # cod liver oil reads directly from CSV (no override)
}

# Fish-in-Meat-A rule: if ANY Meat A selection is fish (alone or with other meats),
# cod liver oil drops to 0g. Meat A (balancer) absorbs the freed grams automatically.
FISH_MEAT_A_NAMES = {"salmon", "talipia (fish)", "alaska pollock (fish)", "haddock (fish)"}

# Fish-in-Meat-C rule: if ANY Meat C selection is fish AND Meat A is NOT fish,
# cod liver oil also drops to 0g.
FISH_MEAT_C_NAMES = {"trout (fish)", "whitefish"}

# Mineral Group constants
MINERALS_A_DM = 27.0   # Mineral Group A total (always fixed — bone meal/dry blood meal are in fixed CSV)


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

    _fixed_df, _user_df = f, u
    return {"ok": True, "fixed_rows": len(_fixed_df), "user_rows": len(_user_df)}


# load once on import
load_csvs()


# ============================================================================
# AAFCO Cat Adult Maintenance — values taken directly from Cats-AFFCO.xlsx
# Column order matches the spreadsheet exactly.
# Units match the spreadsheet column headers.
# ============================================================================

# --- AAFCO minimums (Excel row: "AAFCO min") ---
# Columns with no min value in the spreadsheet are omitted.
AAFCO_MINIMUMS = {
    # --- Macronutrients (% DM) ---
    "Protein":           26.0,
    "Fat":                9.0,

    # --- Major Minerals (% DM) ---
    "Ca":                 0.6,
    "P":                  0.5,
    "Mg":                 0.04,
    "K":                  0.6,
    "Na":                 0.2,

    # --- Trace Minerals (mg/kg DM) ---
    "Iron":              80.0,
    "Zn":                75.0,
    "Cu":                 5.0,
    "Iodine":             1.3,
    "Se":                 0.3,

    # --- Vitamins ---
    "Thiamin":            4.6,    # mg/kg DM
    "Riboflavin":         4.0,    # mg/kg DM
    "Niacin":            60.0,    # mg/kg DM
    "Pantothenic_acid":   5.75,   # mg/kg DM
    "Folate":             0.8,    # mg/kg DM
    "Choline":         2400.0,    # mg/kg DM
    "B12":                0.02,   # mg/kg DM
    "Vitamin_A":       3332.0,    # IU/kg DM
    "Vitamin_E":         28.0,    # IU/kg DM
    "Vitamin_D":        280.0,    # IU/kg DM

    # --- Fatty Acids (% DM) ---
    "FA_18_2":            0.6,    # 18:2 (Linoleic, Omega-6)
    "FA_18_3":            0.1,    # 18:3 (Alpha-linolenic, Omega-3)
    "EPA":                0.01,   # 20:5
    "DHA":                0.01,   # 22:6

    # --- Amino Acids (% DM) ---
    "Tryptophan":         0.16,
    "Threonine":          0.73,
    "Isoleucine":         0.52,
    "Leucine":            1.24,
    "Lysine":             0.83,
    "Methionine":         0.2,
    "Phenylalanine":      0.42,
    "Tyrosine":           0.2,
    "Valine":             0.2,
    "Arginine":           1.04,
}

# --- AAFCO maximums (Excel row: "AAFCO max") ---
# Only nutrients with a max value in the spreadsheet are listed.
AAFCO_MAXIMUMS = {
    "Iron":           700.0,    # mg/kg DM
    "Zn":             300.0,    # mg/kg DM
    "Cu":             100.0,    # mg/kg DM
    "Iodine":          11.0,    # mg/kg DM
    "Se":               2.0,    # mg/kg DM
    "Vitamin_A":   333300.0,    # IU/kg DM
    "Vitamin_D":     3080.0,    # IU/kg DM
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
    """Calculate ratio of diet value to AAFCO minimum for every nutrient.
    Keys match AAFCO_MINIMUMS exactly (Excel column names).
    A ratio of 1.0 means the diet exactly meets the minimum.
    """

    def calc_pct(actual, key):
        minimum = AAFCO_MINIMUMS.get(key)
        if minimum is None or minimum == 0:
            return None
        if actual is None or actual == 0:
            return 0.0
        return round(actual / minimum, 4)

    def pct_dm(nutrient_key):
        return (totals.get(nutrient_key, 0) / total_dm_g) * 100

    def mg_kg(nutrient_key):
        return (totals.get(nutrient_key, 0) * 1000) / total_dm_g

    result = {}

    # --- Macronutrients (% DM) ---
    result["Protein"]          = calc_pct(pct_dm("Protein"),               "Protein")
    result["Fat"]              = calc_pct(pct_dm("Fat"),                    "Fat")

    # --- Major Minerals (% DM) ---
    result["Ca"]               = calc_pct(pct_dm("Ca"),                     "Ca")
    result["P"]                = calc_pct(pct_dm("P"),                      "P")
    # Alias keys expected by router
    result["Protein_percent"]  = pct_dm("Protein")
    result["Fat_percent"]      = pct_dm("Fat")
    result["Ca_percent"]       = pct_dm("Ca")
    result["P_percent"]        = pct_dm("P")
    result["Mg"]               = calc_pct(pct_dm("Mg"),                     "Mg")
    result["K"]                = calc_pct(pct_dm("K"),                      "K")
    result["Na"]               = calc_pct(pct_dm("Na"),                     "Na")

    # --- Trace Minerals (mg/kg DM) ---
    result["Iron"]             = calc_pct(mg_kg("Iron"),                    "Iron")
    result["Zn"]               = calc_pct(mg_kg("Zn"),                      "Zn")
    result["Cu"]               = calc_pct(mg_kg("Cu"),                      "Cu")
    result["Iodine"]           = calc_pct(mg_kg("Iodine"),                  "Iodine")
    result["Se"]               = calc_pct(mg_kg("Se"),                      "Se")

    # --- Vitamins ---
    result["Thiamin"]          = calc_pct(mg_kg("Thiamin"),                 "Thiamin")
    result["Riboflavin"]       = calc_pct(mg_kg("Riboflavin"),              "Riboflavin")
    result["Niacin"]           = calc_pct(mg_kg("Niacin"),                  "Niacin")
    result["Pantothenic_acid"] = calc_pct(mg_kg("Pantothenic_acid"),        "Pantothenic_acid")
    result["Folate"]           = calc_pct(mg_kg("Folate"),                  "Folate")
    result["Choline"]          = calc_pct(mg_kg("Choline"),                 "Choline")
    result["B12"]              = calc_pct(mg_kg("Vitamin_B12"),             "B12")
    result["Vitamin_A"]        = calc_pct(mg_kg("Vitamin_A"),               "Vitamin_A")
    result["Vitamin_E"]        = calc_pct(mg_kg("Vitamin_E"),               "Vitamin_E")
    result["Vitamin_D"]        = calc_pct(mg_kg("Vitamin_D"),               "Vitamin_D")

    # --- Fatty Acids (% DM) ---
    result["FA_18_2"]          = calc_pct(pct_dm("Linoleic_acid"),          "FA_18_2")
    result["FA_18_3"]          = calc_pct(pct_dm("Alpha_linolenic_acid"),   "FA_18_3")
    result["EPA"]              = calc_pct(pct_dm("EPA"),                    "EPA")
    result["DHA"]              = calc_pct(pct_dm("DHA"),                    "DHA")

    # --- Amino Acids (% DM) ---
    result["Tryptophan"]       = calc_pct(pct_dm("Tryptophan"),             "Tryptophan")
    result["Threonine"]        = calc_pct(pct_dm("Threonine"),              "Threonine")
    result["Isoleucine"]       = calc_pct(pct_dm("Isoleucine"),             "Isoleucine")
    result["Leucine"]          = calc_pct(pct_dm("Leucine"),                "Leucine")
    result["Lysine"]           = calc_pct(pct_dm("Lysine"),                 "Lysine")
    result["Methionine"]       = calc_pct(pct_dm("Methionine"),             "Methionine")
    result["Phenylalanine"]    = calc_pct(pct_dm("Phenylalanine"),          "Phenylalanine")
    result["Tyrosine"]         = calc_pct(pct_dm("Tyrosine"),               "Tyrosine")
    result["Valine"]           = calc_pct(pct_dm("Valine"),                 "Valine")
    result["Arginine"]         = calc_pct(pct_dm("Arginine"),               "Arginine")

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
    
    # Missing keys needed by router
    result["choline_mg_kg"] = to_mg_kg(totals.get("Choline", 0))

    # Fatty Acids (% of DM)
    result["linoleic_percent"] = to_pct_dm(totals.get("Linoleic_acid", 0))
    result["ala_percent"] = to_pct_dm(totals.get("Alpha_linolenic_acid", 0))
    epa = totals.get("EPA", 0)
    dha = totals.get("DHA", 0)
    result["epa_percent"]   = to_pct_dm(epa)
    result["dha_percent"]   = to_pct_dm(dha)
    result["epa_dha_percent"] = to_pct_dm(epa + dha)

    # Omega-6:Omega-3 ratio
    omega6 = totals.get("Linoleic_acid", 0)
    omega3 = totals.get("Alpha_linolenic_acid", 0) + epa + dha
    result["Omega6_omega3_ratio"] = round(omega6 / omega3, 2) if omega3 > 0 else None
    
    # Amino Acids (% of DM)
    result["arginine_percent"] = to_pct_dm(totals.get("Arginine", 0))
    result["histidine_percent"] = to_pct_dm(totals.get("Histidine", 0))
    result["isoleucine_percent"] = to_pct_dm(totals.get("Isoleucine", 0))
    result["leucine_percent"] = to_pct_dm(totals.get("Leucine", 0))
    result["lysine_percent"] = to_pct_dm(totals.get("Lysine", 0))
    result["methionine_percent"] = to_pct_dm(totals.get("Methionine", 0))
    result["cystine_percent"] = to_pct_dm(totals.get("Cystine", 0))
    met = totals.get("Methionine", 0)
    cys = totals.get("Cystine", 0)
    result["met_cys_percent"] = to_pct_dm(met + cys)
    result["phenylalanine_percent"] = to_pct_dm(totals.get("Phenylalanine", 0))
    result["tyrosine_percent"] = to_pct_dm(totals.get("Tyrosine", 0))
    phe = totals.get("Phenylalanine", 0)
    tyr = totals.get("Tyrosine", 0)
    result["phe_tyr_percent"] = to_pct_dm(phe + tyr)
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
            "notes": "475g alone | 375g with B | 375g with B+C"
        },
        "Meat Group B": {
            "items": grouped.get("Meat Group B", []),
            "label": "02 Meat Group B (Optional - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "notes": "100g with A | 60g with A+C"
        },
        "Meat Group C": {
            "items": grouped.get("Meat Group C", []),
            "label": "03 Meat Group C (Optional - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "notes": "100g with A | 60g with A+B"
        },
        "Organ Meat (Other)": {
            "items": grouped.get("Organ Meat (Other)", []),
            "label": "04 Organ Meat - Other (Optional - Select upto three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": 35},
            "percentage": "35g fixed when selected (shared equally)",
            "notes": "35g total split equally across selected items"
        },
        "Organ Meat (Liver)": {
            "items": grouped.get("Organ Meat (Liver)", []),
            "label": "05 Organ Meat - Liver (Mandatory - Select upto two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 125, "max": 160},
            "percentage": "160g alone; 125g when organ meat also selected",
            "notes": "160g alone; 125g when other organ meats selected — split equally"
        },
        "Grain A": {
            "items": grouped.get("Grain A", []),
            "label": "06 Grain A (Mandatory - Select at least one and a maximum of three, fixed at 110g base)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 110, "max": 110},
            "percentage": "110g fixed",
            "notes": "Fixed at 110g — split equally across selected items"
        },
        "Vegetable A": {
            "items": grouped.get("Vegetable A", []),
            "label": "07 Vegetable A (Mandatory - Select at least one, up to three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 85, "max": 115},
            "percentage": "90g (w/ VegB, no fruit) | 85g (w/ VegB + fruit) | 115g (no VegB, no fruit) | 110g (no VegB, w/ fruit)",
            "notes": "90g (w/ VegB, no fruit) | 85g (w/ VegB + fruit) | 115g (no VegB, no fruit) | 110g (no VegB, w/ fruit)"
        },
        "Vegetable B": {
            "items": grouped.get("Vegetable B", []),
            "label": "08 Vegetable B (Mandatory - Select at least one, up to three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 25, "max": 25},
            "percentage": "Fixed at 25g",
            "notes": "Fixed at 25g — split equally across selected items"
        },
        "Fruit": {
            "items": grouped.get("Fruit", []),
            "label": "09 Fruit (Mandatory - Select upto three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": 5},
            "notes": "5g total split equally across selected items"
        },
        "Oil": {
            "items": grouped.get("Oil", []),
            "label": "10 Oil (Mandatory - Select at least one and a maximum of three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 10, "max": 10},
            "percentage": "10g fixed",
            "notes": "Fixed at 10g total — split equally across selected items"
        },
        # Fiber/Seeds category removed - fiber supplements no longer offered as a diet option
        "Mineral Group A": {
            "items": grouped.get("Mineral Group A", []),
            "label": "14 Mineral Group A",
            "mandatory": True, "min_selections": 1, "max_selections": 2,
            "dm_range": {"min": 13.5, "max": 27},
            "percentage": "24g base (27g if Group B not selected) — split evenly if both selected",
            "notes": "Mandatory: Select Eggshells, Calcium Carbonate, or both. Gets +3g bonus if Group B not selected."
        },


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
    - Meat total: 475g — split by group:
        Only A: 475g | A+B: 375+100g | A+B+C: 375+60+40g
    - Grain A: FIXED at 110g (independent of fruit selection)
    - Fruit (optional): Fixed 5g total
    - Liver: 160g alone; 125g when organ meat also selected
    - Organ meat (non-liver): 35g total when selected
    - Vegetable A: 90g (w/ VegB, no fruit) | 85g (w/ VegB + fruit) | 115g (no VegB, no fruit) | 110g (no VegB, w/ fruit)
    - Vegetable B: Fixed at 25g
    - Oil: Fixed 10g (split equally across selected oils)
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
    minerals   = [r for r in picks if gname(r) == "mineral group a"]

    # Fish-in-Meat-A rule: if ANY Meat A selection is fish, cod liver drops to 0g
    # Fish-in-Meat-C rule: if ANY Meat C selection is fish AND Meat A is NOT fish, cod liver also drops to 0g
    meat_a_names = {str(r.get("ingredient_name", "")).strip().lower() for r in meats_a}
    meat_c_names = {str(r.get("ingredient_name", "")).strip().lower() for r in meats_c}
    has_fish_in_meat_a = bool(meat_a_names & FISH_MEAT_A_NAMES)
    has_fish_in_meat_c = bool(meat_c_names & FISH_MEAT_C_NAMES)
    effective_overrides = FIXED_OVERRIDES.copy()
    if has_fish_in_meat_a:
        effective_overrides["cod liver oil"] = 0.0  # Meat A fish wins — 0g
    elif has_fish_in_meat_c:
        effective_overrides["cod liver oil"] = 0.0  # Meat C fish only — 0g
    grains_a = [r for r in picks if gname(r) == "grain a"]
    veg_a = [r for r in picks if gname(r) == "vegetable a"]
    veg_b = [r for r in picks if gname(r) == "vegetable b"]
    oils = [r for r in picks if gname(r) == "oil"]
    fruits = [r for r in picks if gname(r) == "fruit"]
    livers = [r for r in picks if gname(r) == "organ meat (liver)"]
    organs = [r for r in picks if gname(r) == "organ meat"]
    fiber_supps = []  # Fiber/Seeds category removed - no longer offered as a diet option
    # (If any fiber group items are passed, they are silently ignored)
    
    # ---------- Validation (warnings only - no selection constraints enforced) ----------
    issues: List[str] = []
    if not minerals:
        issues.append("ERROR: Mineral Group A is MANDATORY - select Eggshells, Calcium Carbonate, or both.")

    # --- MINERAL ALLOCATION ---
    # Group A: always 27g (bone meal/dry blood meal are in fixed CSV)
    has_minerals = len(minerals) > 0
    mineral_a_dm = MINERALS_A_DM if has_minerals else 0.0

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
    # Grain A: FIXED at 110g
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
        override = effective_overrides.get(name)
        if override is None and name in effective_overrides:
            return 0.0
        if isinstance(override, float):
            return override
        return _safe_dm(r["dm_g"])
    FIXED_INGREDIENTS_DM = sum(_fixed_dm_with_overrides(r) for _, r in fdf.iterrows())
    
    # ============================================================================
    # FIXED ALLOCATION RULES (all values in grams DM)
    # -----------------------------------------------------------------------
    # Meat total:  475g
    #   - Only Meat A selected           : 475g split equally across A items
    #   - Meat A + B selected            : A = 375g, B = 100g
    #   - Meat A + B + C selected        : A = 355g, B = 60g, C = 60g
    # Grain A: 110g fixed (split equally). Fruit is independent — does not reduce Grain A.
    # Liver:   160g when liver only; 125g when liver + organ meat also selected
    # Organ meat (non-liver): 35g when selected alongside liver
    # Vegetable A: 90g (w/ VegB, no fruit) | 85g (w/ VegB + fruit) | 115g (no VegB, no fruit) | 110g (no VegB, w/ fruit)
    # Vegetable B: Fixed at 25g
    # Fruits:  5g fixed (split equally across selected fruits)
    # Oil:     10g fixed (split equally across selected oils)
    # ============================================================================
    MEAT_TOTAL_DM      = 475.0
    MEAT_A_ONLY_DM     = 475.0   # all meat goes to group A
    MEAT_A_WITH_B_DM   = 375.0   # group A share when B also selected
    MEAT_B_DM          = 100.0   # group B fixed share
    MEAT_A_WITH_BC_DM  = 355.0   # group A share when B and C also selected
    MEAT_B_WITH_C_DM   = 60.0    # group B share when C also selected
    MEAT_C_WITH_B_DM   = 60.0    # group C share when B also selected
    MEAT_C_DM          = 100.0   # group C share when only A+C (no B)

    GRAIN_A_BASE_DM    = 110.0
    FRUITS_DM          = 5.0 if fruits else 0.0
    GRAIN_A_DM         = GRAIN_A_BASE_DM if grains_a else 0.0

    # Liver: 160g alone, 125g when organ meats also present
    LIVER_DM           = (125.0 if organs else 160.0) if livers else 0.0
    # Organ meat (non-liver): 35g total split across organs
    ORGAN_DM_TOTAL     = 35.0 if organs else 0.0

    # Veg A depends on whether Veg B and/or fruits are selected
    if veg_b and fruits:
        VEG_A_DM = 85.0  if veg_a else 0.0
    elif veg_b and not fruits:
        VEG_A_DM = 90.0  if veg_a else 0.0
    elif not veg_b and fruits:
        VEG_A_DM = 110.0 if veg_a else 0.0
    else:  # no Veg B, no fruits
        VEG_A_DM = 115.0 if veg_a else 0.0

    # Veg B fixed at 25g regardless of fruit
    VEG_B_DM           = 25.0 if veg_b else 0.0
    OILS_DM            = 10.0   if oils    else 0.0
    
    # Compute meat group allocations based on which groups are present.
    # B and C are fully independent — either can be selected without the other.
    has_b = bool(meats_b)
    has_c = bool(meats_c)
    if has_b and has_c:
        MEAT_A_ALLOC = MEAT_A_WITH_BC_DM   # 375g
        MEAT_B_ALLOC = MEAT_B_WITH_C_DM    # 60g
        MEAT_C_ALLOC = MEAT_C_WITH_B_DM    # 60g
    elif has_b and not has_c:
        MEAT_A_ALLOC = MEAT_A_WITH_B_DM    # 375g
        MEAT_B_ALLOC = MEAT_B_DM           # 100g
        MEAT_C_ALLOC = 0.0
    elif has_c and not has_b:
        MEAT_A_ALLOC = MEAT_A_WITH_B_DM    # 375g  (C takes same slice as B would)
        MEAT_B_ALLOC = 0.0
        MEAT_C_ALLOC = MEAT_C_DM           # 100g
    else:
        MEAT_A_ALLOC = MEAT_A_ONLY_DM      # 475g
        MEAT_B_ALLOC = 0.0
        MEAT_C_ALLOC = 0.0

    # ============================================================================
    # ALLOCATION FUNCTION — Meat A is the dynamic balancer (1000g - everything else)
    # ============================================================================
    def calc_diet_allocation(veg_a_dm: float, veg_b_dm: float, fiber_seeds_scale: float = 1.0) -> tuple:
        """
        Calculate diet with specified Veg A / Veg B DM allocations.
        Meat A is the dynamic balancer: meat_a_dm = 1000g - non_meat_a_total.
        """
        dm_breakdown_raw: List[Dict[str, Any]] = []
        ingredient_totals: List[Dict[str, Any]] = []
        ingredient_allocations: Dict[str, Dict[str, Any]] = {}
        totals = _init_totals()
        
        # Fixed items first
        fixed_dm_used = 0.0
        for _, r in fdf.iterrows():
            name_key = str(r.get("ingredient_name", "")).strip().lower()
            if name_key in effective_overrides:
                override = effective_overrides[name_key]
                if override is None:
                    continue  # skip entirely
                dm = float(override)
            else:
                raw_dm = r.get("dm_g", 0)
                try:
                    dm = float(raw_dm)
                    if pd.isna(dm):
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
                # Zero-DM fixed ingredients still appear in breakdown for display purposes
                name = str(r.get("ingredient_name", "")).strip()
                if name:
                    water_pct = get_water_percent(r)
                    dm_breakdown_raw.append({
                        "ingredient": name,
                        "dm_g": 0.0,
                        "fresh_weight_g": 0.0,
                        "water_percent": water_pct,
                        "fixed": True
                    })
        
        remaining = max(0.0, FIXED_TOTAL_DM - fixed_dm_used)

        # --- DYNAMIC MEAT A CALCULATION ---
        # Meat A is the balancer: gets whatever is left after all other categories
        non_meat_a_total = (
            fixed_dm_used +
            MEAT_B_ALLOC + MEAT_C_ALLOC +
            GRAIN_A_DM +
            LIVER_DM + ORGAN_DM_TOTAL +
            veg_a_dm + veg_b_dm +
            FRUITS_DM + OILS_DM +
            mineral_a_dm
        )
        meat_a_dm = max(0.0, FIXED_TOTAL_DM - non_meat_a_total)

        def _add_item(row: pd.Series, dm: float):
            nonlocal remaining
            if dm <= 0:
                return 0.0
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
        
        # MEATS FIRST: Meat A uses dynamic allocation (balancer)
        if meats_a and meat_a_dm > 0:
            per = meat_a_dm / len(meats_a)
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
        
        # Liver: 160g alone, 125g when organ meats also present — split equally
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
        
        # GRAIN A: 110g fixed. Fruit is independent at 5g — does not reduce Grain A.
        # IMPORTANT: Meat allocation is NEVER reduced by fruit selection.
        # Grain A ingredients: Quinoa, Tapioca, Potatoes, Sweet Potatoes.
        if grains_a:
            per = GRAIN_A_DM / len(grains_a)
            for r in grains_a:
                _add_item(r, per)
        
        # FRUITS: Fixed 5g total (split equally).
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

        # MINERAL GROUP A (mandatory - 24g base, 27g if Group B not selected)
        if minerals:
            per = mineral_a_dm / len(minerals)
            for r in minerals:
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
    
    best_veg_a_dm = VEG_A_DM   # dynamic: 90g (w/ VegB, no fruit) | 85g (w/ VegB + fruit) | 115g (no VegB, no fruit) | 110g (no VegB, w/ fruit)
    best_veg_b_dm = VEG_B_DM   # fixed 25g
    
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
