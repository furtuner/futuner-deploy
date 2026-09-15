"""
Cat Diet Planner - Ingredients Service (FINAL VERSION with Fresh Weights)

This version matches exactly with:
- fixed_ingredients_updated.csv
- user_ingredients_updated.csv

All 47 nutrient columns are now tracked including histidine_g.

FIXED ALLOCATION RULES (must total ~1000g DM):
- Meat A (Mandatory): Only A=555g (545g with fruit) | A+B: A=480g (470g) | A+C: A=455g (445g) | A+B+C: A=430g (420g)
- Liver: 170g alone | 110g with Organ (Organ=60g)
- Vegetables (Mandatory, single group): 140g (no grain, no fruit, no seeds)
    | 115g (with grain, no fruit, no seeds) | 120g (no grain, with fruit, no seeds)
    | 95g (with grain, with fruit, no seeds) | 130g (no grain, no fruit, with seeds)
    | 85g (with grain, with fruit, with seeds)
- Fruits (optional): 10g — reduces Meat A by 10g, veg unchanged
- Grains + Potatoes combined (optional): 25g total
- Fruits (optional): 20g
- Oil (Mandatory): 8g
- Fiber/Seeds (optional): 10g max — reduces Veg by 12g

DIET QUALITY TARGETS:
- Protein %: 40–78%
- Fat %: >15% and <28%
- CHO %: <18%
- Fiber %: >2% and <6.5%
- Energy density (kcal/kg): 4000–5300
- Ca:P: >1.4:1 and <2:1
- Omega-6 : Omega-3: >2 and <6

UPDATED: Now calculates and returns fresh weights for each ingredient.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from collections import defaultdict

# ---- CSV locations — search multiple candidate directories ----
def _find_csv(filename: str, fallback: str = None) -> Path:
    """Search for a CSV file across all likely locations."""
    this_file = Path(__file__).resolve()
    candidates = [
        this_file.parent,                    # same folder as ingredients.py
        this_file.parent.parent,             # one level up (backend/)
        this_file.parent.parent.parent,      # two levels up (project root)
        Path.cwd(),                          # current working directory
    ]
    names = [filename]
    if fallback:
        names.append(fallback)
    for directory in candidates:
        for name in names:
            p = directory / name
            if p.exists():
                return p
    # Return the traditional location as last resort (will fail with a clear error)
    return this_file.parent.parent.parent / filename

FIXED_CSV = _find_csv("fixed_ingredients_corrected.csv", "fixed_ingredients.csv")
USER_CSV  = _find_csv("user_ingredients_corrected.csv",  "user_ingredients.csv")

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
# DIET QUALITY TARGETS - STRICTLY ENFORCED
# ============================================================================
FIBER_SUPP_MAX_G = 10.0       # Hard limit: 10g maximum total for fiber/seeds supplements
MAX_TOTAL_FIBER_PCT = 6.5     # Maximum 6.5% total dietary fiber in the diet

# Fixed ingredient overrides — key is lowercase stripped name
# None  = skip entirely (invisible, not calculated)
# float = override dm_g to this value
FIXED_OVERRIDES = {
    "fish oil":      None,   # Removed — cod liver oil covers the full oil allocation
    "bone meal":     None,   # Display only — excluded from all calculations
    # cod liver oil reads directly from CSV (no override)
}

# Fish-only Meat A rule: if salmon raw pink or talipia, fish is selected in
# Meat A (alone or mixed with other Meat A items), cod liver drops to 0g
# Meat A (absorber) automatically picks up the freed 8g
FISH_MEAT_A_NAMES = {"salmon", "talipia (fish)", "alaska pollock (fish)", "haddock (fish)"}

# Fish-in-Meat-C rule: if ANY Meat C selection is fish AND Meat A is NOT fish,
# cod liver oil also drops to 0g.
FISH_MEAT_C_NAMES = {"trout (fish)", "whitefish"}

# Mineral Group constants
MINERALS_A_DM = 30.0   # Mineral Group A total (always fixed — bone meal/dry blood meal are in fixed CSV)
MIN_TOTAL_FIBER_PCT = 2.0     # Minimum 2% total dietary fiber

# Macronutrient targets (% DM)
PROTEIN_MIN = 40.0
PROTEIN_MAX = 78.0
FAT_MIN = 15.0
FAT_MAX = 28.0
CHO_MIN = 0.0
CHO_MAX = 18.0

# Energy target (kcal/kg DM)
ENERGY_MIN = 4000.0
ENERGY_MAX = 5300.0

# Ca:P ratio
CA_P_RATIO_MIN = 1.4
CA_P_RATIO_MAX = 2.0

# Omega-6 : Omega-3 ratio
OMEGA6_OMEGA3_RATIO_MIN = 2.0
OMEGA6_OMEGA3_RATIO_MAX = 6.0

# ============================================================================
# FIXED ALLOCATION AMOUNTS (g DM)
# ============================================================================
# Meat A reference values (for documentation only — actual value is computed dynamically):
#   A only:    ~555g (no fruit) / ~545g (with fruit)
#   A + B:     ~480g / ~470g, B=75g
#   A + C:     ~455g / ~445g, C=100g
#   A + B + C: ~430g / ~420g, B=75g, C=50g
# NOTE: Actual Meat A = 1000g - all other allocations (auto-adjusts to CSV changes)
MEAT_A_ONLY_DM = 555.0          # reference only
MEAT_A_WITH_B_DM = 480.0        # reference only
MEAT_B_WITH_A_DM = 75.0
MEAT_A_WITH_C_DM = 455.0        # reference only
MEAT_C_WITH_A_DM = 100.0
MEAT_A_WITH_BC_DM = 430.0       # reference only
MEAT_B_WITH_AC_DM = 75.0
MEAT_C_WITH_AB_DM = 50.0

# Meat A with fruit selected — reference only (fruit 10g auto-deducted dynamically)
MEAT_A_ONLY_WITH_FRUIT_DM = 545.0
MEAT_A_WITH_B_WITH_FRUIT_DM = 470.0
MEAT_A_WITH_C_WITH_FRUIT_DM = 445.0
MEAT_A_WITH_BC_WITH_FRUIT_DM = 420.0

# Grain + Potato combined: optional, 25g when selected
GRAIN_TOTAL_DM = 25.0

# Liver / Organ
LIVER_ALONE_DM = 170.0          # Liver when no other organ selected
LIVER_WITH_ORGAN_DM = 110.0     # Liver when another organ is also selected
ORGAN_OTHER_DM = 60.0           # Other organ meat (only when liver also selected)

# Vegetable allocations (single group — depends on grain, fruit, seeds):
#   No grain, no fruit, no seeds:        120g
#   With grain, no fruit, no seeds:       95g
#   No grain, with fruit, no seeds:      100g
#   With grain, with fruit, no seeds:     75g
#   No grain, no fruit, with seeds:      110g
#   With grain, with fruit, with seeds:   65g
VEG_NO_GRAIN_NO_FRUIT_NO_SEEDS     = 140.0
VEG_WITH_GRAIN_NO_FRUIT_NO_SEEDS   = 115.0
VEG_NO_GRAIN_WITH_FRUIT_NO_SEEDS   = 120.0
VEG_WITH_GRAIN_WITH_FRUIT_NO_SEEDS =  95.0
VEG_NO_GRAIN_NO_FRUIT_WITH_SEEDS   = 130.0
VEG_WITH_GRAIN_WITH_FRUIT_WITH_SEEDS =  85.0

# Fiber reduces Veg by 12g (seeds_dm allocation itself is 10g)
FIBER_SUPP_DM_ALLOC = 10.0      # Optional fiber/seeds: 10g max

FRUITS_DM = 10.0
OILS_DM = 8.0


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
    
    # FOLATE: CSV has folate_ug (micrograms), convert to mg
    totals["Folate"] += get_col("folate_ug") * dm / 100.0
    
    totals["Choline"] += get_col("choline_mg") * dm / 100.0
    
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
    result["dha_percent"]     = to_pct_dm(dha)
    result["epa_dha_percent"] = to_pct_dm(epa + dha)
    
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
    
    # Category configs matching Cat Raw allocation rules
    category_configs = {
        "Meat Group A": {
            "items": grouped.get("Meat Group A", []),
            "label": "01 Meat Group A (Mandatory - Select at least one)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "Mandatory: A only=575g | A+B: A=500g | A+C: A=475g | A+B+C: A=450g"
        },
        "Meat Group B": {
            "items": grouped.get("Meat Group B", []),
            "label": "02 Meat Group B (Optional - Pick up to two)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "notes": "Optional: If A+B: B=75g | If A+B+C: B=75g"
        },
        "Meat Group C": {
            "items": grouped.get("Meat Group C", []),
            "label": "03 Meat Group C (Optional - Pick up to two)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "notes": "Optional: If A+C: C=100g | If A+B+C: C=50g"
        },
        "Organ Meat (Other)": {
            "items": grouped.get("Organ Meat (Other)", []),
            "label": "04 Organ Meat - Other (Optional - Pick up to three)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 3,
            "dm_range": {"min": 0, "max": 60},
            "percentage": "60g when selected (liver becomes 110g)",
            "notes": "Optional: 60g. If not selected, liver gets 170g."
        },
        "Organ Meat (Liver)": {
            "items": grouped.get("Organ Meat (Liver)", []),
            "label": "05 Organ Meat - Liver (Mandatory - Pick up to 2)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 2,
            "dm_range": {"min": 110, "max": 170},
            "percentage": "170g alone, 110g with other organ",
            "notes": "Mandatory: Liver is required - select up to two"
        },
        "Grain A": {
            "items": grouped.get("Grain A", []) + grouped.get("Grain B", []) + grouped.get("Vegetable C", []),
            "label": "06 Grains & Potato (Optional - Select up to two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "notes": "Optional: 25g total split equally among selections. Includes all grains and potatoes."
        },
        "Grain B": {
            "items": [],
            "label": "06 Grains & Potato (Optional - Select up to two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 2,
            "notes": "Merged into Grains group above."
        },
        "Vegetable A": {
            "items": grouped.get("Vegetable A", []) + grouped.get("Vegetable B", []),
            "label": "07 Vegetables (Mandatory - Select at least one and up to three maximum)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "120g (no grain/fruit/seeds) | 95g (grain) | 100g (fruit) | 75g (grain+fruit) | 110g (seeds) | 65g (grain+fruit+seeds)"
        },
        "Vegetable B": {
            "items": [],
            "label": "07 Vegetables (Mandatory - Select at least one and up to three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 3,
            "notes": "Merged into Vegetables group above."
        },
        "Vegetable C": {
            "items": [],
            "label": "06 Grains & Potato (Optional - Select up to two maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 1,
            "notes": "Merged into Grains group."
        },
        "Fruit": {
            "items": grouped.get("Fruit", []),
            "label": "08 Fruit (Optional - Select up to three maximum)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": 3,
            "dm_range": {"min": 0, "max": 20},
            "notes": "Optional: 20g total split among selections"
        },
        "Oil": {
            "items": grouped.get("Oil", []),
            "label": "09 Oil (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True,
            "min_selections": 1,
            "max_selections": 3,
            "notes": "Mandatory: 8g total, split among selections"
        },
        "Fiber": {
            "items": grouped.get("Fiber", []),
            "label": "10 Fiber/Seeds (Optional - Pick up to two)",
            "mandatory": False,
            "min_selections": 0,
            "max_selections": None,
            "dm_range": {"min": 0, "max": FIBER_SUPP_MAX_G},
            "percentage": f"Max {FIBER_SUPP_MAX_G}g - reduces Veg by 12g",
            "notes": "Optional: Seeds/fiber supplements. Reduces Vegetable allocation by 12g."
        },
        "Mineral Group A": {
            "items": grouped.get("Mineral Group A", []),
            "label": "14 Mineral Group A",
            "mandatory": True, "min_selections": 1, "max_selections": 2,
            "dm_range": {"min": 15, "max": 30},
            "percentage": "27g base (30g if Group B not selected) — split evenly if both selected",
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
    - Meat A (Mandatory): A only=555g (545g with fruit) | A+B: A=480g (470g) | A+C: A=455g (445g) | A+B+C: A=430g (420g)
    - Liver: 170g alone / 110g with organ (organ gets 60g)
    - Grains + Potato combined (optional): 25g total split equally
    - Vegetables (Mandatory, single group):
        140g (no grain, no fruit, no seeds) | 115g (grain, no fruit, no seeds)
        120g (no grain, fruit, no seeds)    | 95g (grain, fruit, no seeds)
        130g (no grain, no fruit, seeds)    | 85g (grain, fruit, seeds)
    - Fruits (optional): 10g total — reduces Meat A by 10g, veg unchanged
    - Oil (Mandatory): 8g total
    - Seeds/Fiber (optional): 10g max — reduces Veg by 12g
    
    Diet quality targets:
    - Protein %: 40–78%
    - Fat %: >15% and <28%
    - CHO %: <18%
    - Fiber %: >2% and <6.5%
    - Energy density (kcal/kg): 4000–5300
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
    minerals   = [r for r in picks if gname(r) == "mineral group a"]

    meats_b = [r for r in picks if gname(r) == "meat group b"]
    meats_c = [r for r in picks if gname(r) == "meat group c"]

    # Cod liver oil rule: if salmon raw pink or talipia, fish is selected
    # in Meat A (alone or mixed with other Meat A items) -> cod liver 0g.
    # If trout or whitefish raw in Meat C (and no fish in Meat A) -> cod liver 0g.
    # If neither -> cod liver reads normal CSV value.
    meat_a_names = {
        str(r.get("ingredient_name", "")).strip().lower() for r in meats_a
    }
    meat_c_names = {
        str(r.get("ingredient_name", "")).strip().lower() for r in meats_c
    }
    has_fish = bool(meat_a_names & FISH_MEAT_A_NAMES)
    has_fish_in_meat_c = bool(meat_c_names & FISH_MEAT_C_NAMES)
    effective_overrides = FIXED_OVERRIDES.copy()
    if has_fish:
        effective_overrides["cod liver oil"] = 0.0  # Meat A fish wins — 0g
    elif has_fish_in_meat_c:
        effective_overrides["cod liver oil"] = 0.0  # Meat C fish only — 0g
    grains_a = [r for r in picks if gname(r) in ("grain a", "grain b", "vegetable c")]  # All grains + potato in one group
    grains_b = []  # Kept for compatibility
    veg_a = [r for r in picks if gname(r) in ("vegetable a", "vegetable b")]  # Single combined veg group
    veg_b = []  # Merged into veg_a; kept for compatibility
    veg_c = []  # Merged into grains_a
    oils = [r for r in picks if gname(r) == "oil"]
    fruits = [r for r in picks if gname(r) == "fruit"]
    livers = [r for r in picks if (gname(r) == "organ meat" or gname(r) == "organ meat (liver)") and "liver" in str(r["ingredient_name"]).lower()]
    organs = [r for r in picks if (gname(r) == "organ meat" or gname(r) == "organ meat (liver)") and "liver" not in str(r["ingredient_name"]).lower()]
    fiber_supps = [r for r in picks if gname(r) == "fiber"]
    
    # ---------- Validation ----------
    issues: List[str] = []
    if not meats_a:
        issues.append("ERROR: Meat Group A is MANDATORY - at least one Meat Group A item must be selected.")
    if not minerals:
        issues.append("ERROR: Mineral Group A is MANDATORY - select Eggshells, Calcium Carbonate, or both.")

    # --- MINERAL ALLOCATION ---
    # Group A: always 30g (bone meal/dry blood meal are in fixed CSV)
    has_minerals = len(minerals) > 0
    mineral_a_dm = MINERALS_A_DM if has_minerals else 0.0
    if not oils:
        issues.append("ERROR: At least one Oil must be selected.")
    if not livers:
        issues.append("ERROR: Liver is MANDATORY.")
    if not veg_a:
        issues.append("ERROR: Vegetables is MANDATORY - at least one Vegetable item must be selected.")


    # --- Over-limit warnings (selection is still allowed, DM split equally) ---
    selection_limits = [
        (meats_a,      3, "Meat Group A"),
        (meats_b,      2, "Meat Group B"),
        (meats_c,      2, "Meat Group C"),
        (organs,       3, "Organ Meat (Other)"),
        (livers,       2, "Organ Meat (Liver)"),
        (grains_a,     2, "Grains & Potato"),
        (veg_a,        3, "Vegetables"),
        (fruits,       3, "Fruit"),
        (oils,         3, "Oil"),
        (fiber_supps,  2, "Fiber/Seeds"),
    ]
    for group_list, limit, label in selection_limits:
        if len(group_list) > limit:
            issues.append(
                f"WARNING: {label} has {len(group_list)} selections (recommended max: {limit}). "
                f"All {len(group_list)} will be included with DM split equally among them."
            )

    # ============================================================================
    # DETERMINE FIXED ALLOCATIONS BASED ON SELECTIONS
    # ============================================================================
    
    has_seeds = len(fiber_supps) > 0
    has_organ = len(organs) > 0
    has_meat_b = len(meats_b) > 0
    has_meat_c = len(meats_c) > 0
    has_grain_b = len(grains_b) > 0
    has_potato = len(veg_c) > 0
    
    # --- MEAT ALLOCATION ---
    # Meat B and C are fixed amounts.
    # Meat A is computed dynamically = 1000g - everything_else.
    # If fruit is selected (10g optional), that 10g comes out of Meat A automatically.
    # Reference constants (MEAT_A_ONLY_DM etc.) are for documentation only.
    has_fruit_selected = len(fruits) > 0

    if has_meat_b and has_meat_c:
        meat_b_dm = MEAT_B_WITH_AC_DM   # 75g
        meat_c_dm = MEAT_C_WITH_AB_DM   # 50g
    elif has_meat_b:
        meat_b_dm = MEAT_B_WITH_A_DM    # 75g
        meat_c_dm = 0.0
    elif has_meat_c:
        meat_b_dm = 0.0
        meat_c_dm = MEAT_C_WITH_A_DM    # 100g
    else:
        meat_b_dm = 0.0
        meat_c_dm = 0.0
    # Meat A calculated after all other allocations are known
    
    # --- GRAIN ALLOCATION (optional, 25g total when selected — includes potatoes) ---
    has_grain = len(grains_a) > 0
    has_potato = False   # Potatoes are now merged into grains_a; kept for veg logic compatibility

    if has_grain:
        grain_a_dm = GRAIN_TOTAL_DM   # 25g split equally among all selected
        potato_dm = 0.0
    else:
        grain_a_dm = 0.0
        potato_dm = 0.0
    grain_b_dm = 0.0
    
    # --- LIVER / ORGAN ALLOCATION ---
    # Liver alone: 140g (100g base + 40g absorbed when no organ)
    # Liver with organ: 100g, organ: 40g
    if has_organ:
        liver_dm = LIVER_WITH_ORGAN_DM   # 100g
        organ_dm = ORGAN_OTHER_DM         # 40g
    else:
        liver_dm = LIVER_ALONE_DM         # 140g
        organ_dm = 0.0
    
    # --- VEGETABLE ALLOCATION (single group, 6 combinations based on grain/fruit/seeds) ---
    has_veg_b = False  # No longer separate; kept for compatibility
    has_grain_or_potato = has_grain  # Potato now merged into grains

    if has_grain_or_potato and not has_fruit_selected and not has_seeds:
        veg_a_dm_alloc = VEG_WITH_GRAIN_NO_FRUIT_NO_SEEDS        #  95g
    elif has_grain_or_potato and has_fruit_selected and not has_seeds:
        veg_a_dm_alloc = VEG_WITH_GRAIN_WITH_FRUIT_NO_SEEDS       #  75g
    elif has_grain_or_potato and has_fruit_selected and has_seeds:
        veg_a_dm_alloc = VEG_WITH_GRAIN_WITH_FRUIT_WITH_SEEDS     #  65g
    elif not has_grain_or_potato and not has_fruit_selected and has_seeds:
        veg_a_dm_alloc = VEG_NO_GRAIN_NO_FRUIT_WITH_SEEDS         # 110g
    elif not has_grain_or_potato and has_fruit_selected and not has_seeds:
        veg_a_dm_alloc = VEG_NO_GRAIN_WITH_FRUIT_NO_SEEDS         # 100g
    else:
        # no grain, no fruit, no seeds — OR grain+no fruit+seeds (not in spec, fallback)
        veg_a_dm_alloc = VEG_NO_GRAIN_NO_FRUIT_NO_SEEDS           # 120g

    veg_b_dm_alloc = 0.0  # No separate Veg B

    # Fiber reduces Veg by 12g
    if has_seeds:
        seeds_dm = min(FIBER_SUPP_DM_ALLOC, FIBER_SUPP_MAX_G)   # 10g
        veg_a_dm_alloc = max(0.0, veg_a_dm_alloc - 12.0)
    else:
        seeds_dm = 0.0
    
    # --- FIXED ALLOCATIONS ---
    fruits_dm = FRUITS_DM if fruits else 0.0    # 10g when selected, optional
    oils_dm = OILS_DM if oils else 0.0          # 8g
    
    # ============================================================================
    # COMPUTE MEAT A = 1000g - everything else (always hits exactly 1000g DM)
    # Fruit (10g optional) is already included in non_meat_a_total so Meat A
    # automatically shrinks by 10g when fruit is selected.
    # ============================================================================
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
    fixed_ingredients_dm = sum(_fixed_dm_with_overrides(r) for _, r in fdf.iterrows())

    non_meat_a_total = (
        fixed_ingredients_dm +
        meat_b_dm + meat_c_dm +
        grain_a_dm + grain_b_dm +
        liver_dm + organ_dm +
        veg_a_dm_alloc + veg_b_dm_alloc + potato_dm +
        fruits_dm + oils_dm + seeds_dm +
        mineral_a_dm
    )

    meat_a_dm = FIXED_TOTAL_DM - non_meat_a_total

    if meat_a_dm < 0:
        issues.append(
            f"ERROR: Non-Meat-A categories total {non_meat_a_total:.1f}g, "
            f"exceeding {FIXED_TOTAL_DM:.0f}g budget. Meat A cannot be negative."
        )
        meat_a_dm = 0.0
    
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
    
    # 1. Fixed ingredients from CSV — applies effective_overrides
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
                if __import__("pandas").isna(dm):
                    dm = 0.0
            except (ValueError, TypeError):
                dm = 0.0

        if dm > 0:
            _add_item(r, dm, is_fixed=True)
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
    
    # 2. Liver (split equally among all selected liver ingredients)
    if livers:
        per_liver = liver_dm / len(livers)
        for r in livers:
            _add_item(r, per_liver)
    
    # 3. Other organs (split equally among all selected organ ingredients)
    if organs and has_organ:
        per_organ = organ_dm / len(organs)
        for r in organs:
            _add_item(r, per_organ)
    
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
    
    # 7. Vegetable C (Potatoes) — 50g total, split equally among all selected potato/veg_c items
    if veg_c:
        per = potato_dm / len(veg_c)
        for r in veg_c:
            _add_item(r, per)
    
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

    # 10. Mineral Group A (mandatory - 27g base, 30g if Group B not selected)
    if minerals:
        per = mineral_a_dm / len(minerals)
        for r in minerals:
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
    
    if cho_pct >= CHO_MAX:
        issues.append(f"WARNING: CHO is {cho_pct:.1f}% (target: <{CHO_MAX}%). Too high.")
    
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
        "Omega6_omega3_ratio": round(omega6_omega3_ratio, 2),  # key used by diet_router
        # Allocation summary (for transparency)
        "allocation_summary": {
            "meat_a_dm": meat_a_dm,
            "meat_b_dm": meat_b_dm,
            "meat_c_dm": meat_c_dm,
            "grain_a_dm": grain_a_dm,
            "grain_b_dm": grain_b_dm,
            "grain_selected": has_grain,
            "liver_dm": liver_dm,
            "organ_dm": organ_dm,
            "veg_a_dm": veg_a_dm_alloc,
            "veg_b_dm": veg_b_dm_alloc,
            "fruits_dm": fruits_dm,
            "oils_dm": oils_dm,
            "fiber_dm": seeds_dm,
            "fiber_selected": has_seeds,
            "mineral_a_dm": mineral_a_dm,
            "minerals_a_selected": len(minerals),
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
