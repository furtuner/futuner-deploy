"""
Cat Diet Planner - Ingredients Service

This version matches exactly with:
- fixed_ingredients_corrected.csv
- user_ingredients_corrected.csv

All 47 nutrient columns are now tracked including histidine_g.

FIXED ALLOCATION RULES — Conventional Cat Diet (must total 1000g DM):
- Meat A only: 450g | A+B: 350+100 | A+C: 350+100 | A+B+C: 330+60+60
- Liver: 160g alone; 125g when other organ selected (organ takes 35g from liver allocation)
- Grains: Grain A always balances dynamically to 1000g DM;
          Grain B: 30g (with A) | 25g (with A+Potato);
          Potato: 30g (alone) | 15g (with Grain B)
- Veg A (with Veg B): 90g | Veg A alone: 130g
- Veg B: 40g; seeds/fruits each reduce Veg A by 5g AND Veg B by 5g
- Fruits: 5g total (max 3 selections, split equally)
- Seeds: 5g total (max 5g, split among selections)
- Oil: 10g total (split equally)
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from collections import defaultdict

# ---- CSV locations (project root) ----
ROOT = Path(__file__).resolve().parents[2]  # services -> backend -> project root
FIXED_CSV = ROOT / "fixed_ingredients_corrected.csv"
USER_CSV  = ROOT / "user_ingredients_corrected.csv"

if not FIXED_CSV.exists():
    FIXED_CSV = ROOT / "fixed_ingredients.csv"
if not USER_CSV.exists():
    USER_CSV  = ROOT / "user_ingredients.csv"

_fixed_df: Optional[pd.DataFrame] = None
_user_df:  Optional[pd.DataFrame] = None

REQUIRED_FIXED = {
    "ingredient_name", "dm_g", "protein_g", "fat_g", "cho_g", "fiber_g", "ash_g",
    "calcium_mg", "phosphorus_mg", "iron_mg", "energy_kcal"
}

REQUIRED_USER = {
    "ingredient_name", "group_name", "protein_g", "fat_g", "cho_g", "fiber_g", "ash_g",
    "calcium_mg", "phosphorus_mg", "iron_mg", "energy_kcal"
}

# ============================================================================
# DIET QUALITY TARGETS
# ============================================================================
FIBER_SUPP_MAX_G    = 10.0
MAX_TOTAL_FIBER_PCT = 6.5
MIN_TOTAL_FIBER_PCT = 3.0

# Fixed ingredient overrides — key is lowercase stripped name
# None  = skip entirely (invisible, not calculated)
# float = override dm_g to this value
FIXED_OVERRIDES = {
    "fish oil":      None,   # Removed — cod liver oil covers the full oil allocation
    "bone meal":     None,   # Display only — excluded from all calculations
    # cod liver oil reads directly from CSV (no override)
}

# Fish-in-Meat-A rule: if ANY Meat A selection is fish (alone or with other meats),
# cod liver oil drops to 0g. Grain A (balancer) absorbs the freed grams automatically.
FISH_MEAT_A_NAMES = {"salmon", "talipia (fish)", "alaska pollock (fish)", "haddock (fish)"}

# Fish-in-Meat-C rule: if ANY Meat C selection is fish AND Meat A is NOT fish,
# cod liver oil drops to 7g. Meat A fish always takes priority (0g).
FISH_MEAT_C_NAMES = {"trout (fish)", "whitefish"}

# Mineral Group constants
MINERALS_A_DM = 27.0   # Mineral Group A total (always fixed — bone meal/dry blood meal are in fixed CSV)

PROTEIN_MIN = 20.0
PROTEIN_MAX = 40.0
FAT_MIN     = 9.0
FAT_MAX     = 25.0
CHO_MIN     = 10.0
CHO_MAX     = 45.0

ENERGY_MIN = 3500.0
ENERGY_MAX = 5000.0

CA_P_RATIO_MIN = 1.1
CA_P_RATIO_MAX = 2.0

OMEGA6_OMEGA3_RATIO_MIN = 2.0
OMEGA6_OMEGA3_RATIO_MAX = 6.0

# ============================================================================
# FIXED ALLOCATION AMOUNTS (g DM) — Cat conventional diet
# ============================================================================
MEAT_A_ONLY_DM     = 450.0
MEAT_A_WITH_B_DM   = 350.0
MEAT_A_WITH_C_DM   = 350.0
MEAT_A_WITH_BC_DM  = 330.0
MEAT_B_WITH_A_DM   = 100.0
MEAT_B_WITH_AC_DM  = 60.0
MEAT_C_WITH_A_DM   = 100.0
MEAT_C_WITH_AB_DM  = 60.0

LIVER_ALONE_DM     = 160.0
LIVER_WITH_ORGAN_DM= 125.0  # 160 - 35 organ: organ replaces liver g, not additive
ORGAN_OTHER_DM     = 35.0

GRAIN_A_WITH_B_DM        = 85.0  # was 90: 85+30=115g matches grain-A-alone baseline
GRAIN_B_WITH_A_DM        = 30.0
GRAIN_A_WITH_POTATO_DM   = 85.0  # was 90: 85+30=115g matches grain-A-alone baseline
POTATO_NO_GRAINB_DM      = 30.0
GRAIN_A_WITH_B_POTATO_DM = 75.0  # was 80: 75+25+15=115g matches grain-A-alone baseline
GRAIN_B_WITH_POTATO_DM   = 25.0
POTATO_WITH_GRAINB_DM    = 15.0

VEG_A_WITH_B_DM   = 90.0
VEG_A_ONLY_DM     = 130.0
VEG_B_ALONE_DM    = 40.0

FRUITS_TOTAL_DM      = 5.0
FRUITS_VEG_REDUCTION = 5.0

SEEDS_TOTAL_DM       = 5.0
SEEDS_VEG_REDUCTION  = 5.0

OILS_DM = 10.0

# ============================================================================
# AAFCO Cat Adult Maintenance minimums — exact values from Cats-AAFCO.xlsx
# Units: % DM for macros/minerals, mg/kg DM for trace minerals/vitamins,
#        IU/kg DM for fat-soluble vitamins, % DM for fatty acids/amino acids
# ============================================================================
AAFCO_MINIMUMS = {
    # Macronutrients (% DM)
    "Protein":          26,
    "Fat":               9,

    # Major Minerals (% DM)
    "Ca":                0.6,
    "P":                 0.5,
    "Mg":                0.04,
    "K":                 0.6,
    "Na":                0.2,

    # Trace Minerals (mg/kg DM)
    "Iron":             80,
    "Zn":               75,
    "Cu":                5,
    "Iodine":            1.3,
    "Se":                0.3,

    # Vitamins
    "Thiamin":           4.6,    # mg/kg DM
    "Riboflavin":        4,      # mg/kg DM
    "Niacin":           60,      # mg/kg DM
    "Pantothenic_acid":  5.75,   # mg/kg DM
    "Folate":            0.8,    # mg/kg DM
    "Choline":        2400,      # mg/kg DM
    "B12":               0.02,   # mg/kg DM
    "Vitamin_A":      3332,      # IU/kg DM
    "Vitamin_E":        28,      # IU/kg DM
    "Vitamin_D":       280,      # IU/kg DM

    # Fatty Acids (% DM)
    "FA_18_2":           0.6,    # Linoleic (Omega-6)
    "FA_18_3":           0.1,    # Alpha-linolenic (Omega-3)
    "EPA":               0.01,
    "DHA":               0.01,

    # Amino Acids (% DM)
    "Tryptophan":        0.16,
    "Threonine":         0.73,
    "Isoleucine":        0.52,
    "Leucine":           1.24,
    "Lysine":            0.83,
    "Methionine":        0.2,
    "Phenylalanine":     0.42,
    "Tyrosine":          0.2,
    "Valine":            0.2,
    "Arginine":          1.04,
}

# No AAFCO maximums used per requirements
AAFCO_MAXIMUMS: dict = {}


# ============================================================================
# Internal helpers
# ============================================================================
def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.loc[:, ~df.columns.str.contains('^unnamed', case=False)]
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


load_csvs()


def fixed_df() -> pd.DataFrame:
    return _fixed_df


def user_df() -> pd.DataFrame:
    return _user_df


FIXED_TOTAL_DM = 1000.0


def _safe_float(val, default=0.0):
    try:
        result = float(val)
        return default if pd.isna(result) else result
    except (ValueError, TypeError):
        return default


def _init_totals() -> Dict[str, float]:
    return {
        "Protein": 0.0, "Fat": 0.0, "CHO": 0.0, "Fiber": 0.0, "Ash": 0.0,
        "Ca": 0.0, "P": 0.0, "Mg": 0.0, "K": 0.0, "Na": 0.0,
        "Iron": 0.0, "Zn": 0.0, "Cu": 0.0, "Iodine": 0.0, "Se": 0.0,
        "Thiamin": 0.0, "Riboflavin": 0.0, "Niacin": 0.0, "Pantothenic_acid": 0.0,
        "Vitamin_B6": 0.0, "Folate": 0.0, "Choline": 0.0, "Vitamin_B12": 0.0,
        "Vitamin_A": 0.0, "Vitamin_E": 0.0, "Vitamin_D": 0.0,
        "Linoleic_acid": 0.0, "Alpha_linolenic_acid": 0.0, "EPA": 0.0, "DHA": 0.0,
        "Tryptophan": 0.0, "Threonine": 0.0, "Isoleucine": 0.0, "Leucine": 0.0,
        "Lysine": 0.0, "Methionine": 0.0, "Cystine": 0.0, "Phenylalanine": 0.0,
        "Tyrosine": 0.0, "Valine": 0.0, "Arginine": 0.0, "Histidine": 0.0,
        "Energy": 0.0
    }


def _add_row(totals: Dict[str, float], dm: float, row: pd.Series):
    def get_col(name, default=0.0):
        return _safe_float(row.get(name, default), default)

    totals["Protein"] += get_col("protein_g") * dm / 100.0
    totals["Fat"]     += get_col("fat_g")     * dm / 100.0
    totals["CHO"]     += get_col("cho_g")     * dm / 100.0
    totals["Fiber"]   += get_col("fiber_g")   * dm / 100.0
    totals["Ash"]     += get_col("ash_g")     * dm / 100.0

    totals["Ca"] += get_col("calcium_mg")    * dm / 100.0 / 1000.0
    totals["P"]  += get_col("phosphorus_mg") * dm / 100.0 / 1000.0
    totals["Mg"] += get_col("magnesium_mg")  * dm / 100.0 / 1000.0
    totals["K"]  += get_col("potassium_mg")  * dm / 100.0 / 1000.0
    totals["Na"] += get_col("sodium_mg")     * dm / 100.0 / 1000.0

    totals["Iron"]   += get_col("iron_mg")     * dm / 100.0
    totals["Zn"]     += get_col("zinc_mg")     * dm / 100.0
    totals["Cu"]     += get_col("copper_mg")   * dm / 100.0
    totals["Iodine"] += get_col("iodine_mg")   * dm / 100.0
    totals["Se"]     += get_col("selenium_mg") * dm / 100.0

    totals["Thiamin"]          += get_col("thiamin_mg")           * dm / 100.0
    totals["Riboflavin"]       += get_col("riboflavin_mg")        * dm / 100.0
    totals["Niacin"]           += get_col("niacin_mg")            * dm / 100.0
    totals["Pantothenic_acid"] += get_col("pantothenic_acid_mg")  * dm / 100.0
    totals["Vitamin_B6"]       += get_col("vitamin_b6_mg")        * dm / 100.0
    totals["Folate"]           += get_col("folate_ug")            * dm / 100.0 
    totals["Choline"]          += get_col("choline_mg")           * dm / 100.0
    totals["Vitamin_B12"]      += get_col("vitamin_b12_ug")       * dm / 100.0 

    totals["Vitamin_A"] += get_col("vitamin_a_iu") * dm / 100.0
    totals["Vitamin_E"] += get_col("vitamin_e_mg") * dm / 100.0
    totals["Vitamin_D"] += get_col("vitamin_d_iu") * dm / 100.0

    totals["Linoleic_acid"]        += get_col("pufa_18_2_g") * dm / 100.0
    totals["Alpha_linolenic_acid"] += get_col("pufa_18_3_g") * dm / 100.0
    totals["EPA"]                  += get_col("pufa_20_5_g") * dm / 100.0
    totals["DHA"]                  += get_col("pufa_22_6_g") * dm / 100.0

    totals["Tryptophan"]    += get_col("tryptophan_g")    * dm / 100.0
    totals["Threonine"]     += get_col("threonine_g")     * dm / 100.0
    totals["Isoleucine"]    += get_col("isoleucine_g")    * dm / 100.0
    totals["Leucine"]       += get_col("leucine_g")       * dm / 100.0
    totals["Lysine"]        += get_col("lysine_g")        * dm / 100.0
    totals["Methionine"]    += get_col("methionine_g")    * dm / 100.0
    totals["Cystine"]       += get_col("cystine_g")       * dm / 100.0
    totals["Phenylalanine"] += get_col("phenylalanine_g") * dm / 100.0
    totals["Tyrosine"]      += get_col("tyrosine_g")      * dm / 100.0
    totals["Valine"]        += get_col("valine_g")        * dm / 100.0
    totals["Arginine"]      += get_col("arginine_g")      * dm / 100.0
    totals["Histidine"]     += get_col("histidine_g")     * dm / 100.0

    totals["Energy"] += get_col("energy_kcal") * dm / 100.0


def _compress_breakdown(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    agg = defaultdict(lambda: {"ingredient": "", "dm_g": 0.0, "fresh_weight_g": 0.0, "water_percent": 0.0, "fixed": False})
    for row in raw:
        name = row["ingredient"]
        agg[name]["ingredient"]    = name
        agg[name]["dm_g"]          = round(agg[name]["dm_g"] + float(row["dm_g"]), 2)
        agg[name]["fresh_weight_g"]= round(agg[name]["fresh_weight_g"] + float(row.get("fresh_weight_g", 0)), 2)
        agg[name]["water_percent"] = row.get("water_percent", 0.0)
        agg[name]["fixed"]         = agg[name]["fixed"] or bool(row.get("fixed", False))
    merged = list(agg.values())
    merged.sort(key=lambda r: (not r["fixed"], r["ingredient"].lower()))
    return merged


def gname(s: pd.Series) -> str:
    raw = str(s.get("group_name", "")).lower()
    return " ".join(raw.split())


def _normkey(x: str) -> str:
    return " ".join(
        str(x).lower()
        .replace("–", "-").replace("—", "-")
        .replace("\u2018", "'").replace("\u2019", "'")
        .split()
    )


def calculate_fresh_weight(dm_g: float, water_percent: float) -> float:
    if water_percent >= 100 or water_percent <= 0:
        return dm_g
    dry_matter_fraction = 1.0 - (water_percent / 100.0)
    if dry_matter_fraction <= 0:
        return dm_g
    return dm_g / dry_matter_fraction


def get_water_percent(row: pd.Series) -> float:
    for col in ['water', 'water_percent', 'water_pct', 'moisture', 'moisture_percent', 'water_g']:
        if col in row.index:
            val = _safe_float(row.get(col, 0), 0)
            if val > 0:
                return val
    return 0.0


# ============================================================================
# AAFCO Compliance Calculator
# ============================================================================
def calculate_aafco_percent_of_minimum(totals: Dict[str, float], total_dm_g: float = 1000.0) -> Dict[str, Any]:
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
    result["Protein"]          = calc_pct(pct_dm("Protein"),          "Protein")
    result["Fat"]              = calc_pct(pct_dm("Fat"),               "Fat")
    result["Ca"]               = calc_pct(pct_dm("Ca"),                "Ca")
    result["P"]                = calc_pct(pct_dm("P"),                 "P")
    result["Mg"]               = calc_pct(pct_dm("Mg"),                "Mg")
    result["K"]                = calc_pct(pct_dm("K"),                 "K")
    result["Na"]               = calc_pct(pct_dm("Na"),                "Na")
    result["Iron"]             = calc_pct(mg_kg("Iron"),               "Iron")
    result["Zn"]               = calc_pct(mg_kg("Zn"),                 "Zn")
    result["Cu"]               = calc_pct(mg_kg("Cu"),                 "Cu")
    result["Iodine"]           = calc_pct(mg_kg("Iodine"),             "Iodine")
    result["Se"]               = calc_pct(mg_kg("Se"),                 "Se")
    result["Thiamin"]          = calc_pct(mg_kg("Thiamin"),            "Thiamin")
    result["Riboflavin"]       = calc_pct(mg_kg("Riboflavin"),         "Riboflavin")
    result["Niacin"]           = calc_pct(mg_kg("Niacin"),             "Niacin")
    result["Pantothenic_acid"] = calc_pct(mg_kg("Pantothenic_acid"),   "Pantothenic_acid")
    result["Folate"]           = calc_pct(mg_kg("Folate"),             "Folate")
    result["Choline"]          = calc_pct(mg_kg("Choline"),            "Choline")
    result["B12"]              = calc_pct(mg_kg("Vitamin_B12"),        "B12")
    result["Vitamin_A"]        = calc_pct(mg_kg("Vitamin_A"),          "Vitamin_A")
    result["Vitamin_E"]        = calc_pct(mg_kg("Vitamin_E"),          "Vitamin_E")
    result["Vitamin_D"]        = calc_pct(mg_kg("Vitamin_D"),          "Vitamin_D")
    result["FA_18_2"]          = calc_pct(pct_dm("Linoleic_acid"),     "FA_18_2")
    result["FA_18_3"]          = calc_pct(pct_dm("Alpha_linolenic_acid"), "FA_18_3")
    result["EPA"]              = calc_pct(pct_dm("EPA"),               "EPA")
    result["DHA"]              = calc_pct(pct_dm("DHA"),               "DHA")
    result["Tryptophan"]       = calc_pct(pct_dm("Tryptophan"),        "Tryptophan")
    result["Threonine"]        = calc_pct(pct_dm("Threonine"),         "Threonine")
    result["Isoleucine"]       = calc_pct(pct_dm("Isoleucine"),        "Isoleucine")
    result["Leucine"]          = calc_pct(pct_dm("Leucine"),           "Leucine")
    result["Lysine"]           = calc_pct(pct_dm("Lysine"),            "Lysine")
    result["Methionine"]       = calc_pct(pct_dm("Methionine"),        "Methionine")
    result["Phenylalanine"]    = calc_pct(pct_dm("Phenylalanine"),     "Phenylalanine")
    result["Tyrosine"]         = calc_pct(pct_dm("Tyrosine"),          "Tyrosine")
    result["Valine"]           = calc_pct(pct_dm("Valine"),            "Valine")
    result["Arginine"]         = calc_pct(pct_dm("Arginine"),          "Arginine")
    return result


def _add_extended_nutrient_output(result: dict, totals: dict, total_dm_g: float = 1000.0):
    def to_pct_dm(total_g):
        return round((total_g / total_dm_g) * 100, 4)
    def to_mg_kg(total_mg):
        return round((total_mg * 1000) / total_dm_g, 4)
    def to_iu_kg(total_iu):
        return round((total_iu * 1000) / total_dm_g, 4)

    result["Mg_percent"] = to_pct_dm(totals.get("Mg", 0))
    result["K_percent"]  = to_pct_dm(totals.get("K",  0))
    result["Na_percent"] = to_pct_dm(totals.get("Na", 0))

    result["iron_mg_kg"]   = to_mg_kg(totals.get("Iron",   0))
    result["zn_mg_kg"]     = to_mg_kg(totals.get("Zn",     0))
    result["cu_mg_kg"]     = to_mg_kg(totals.get("Cu",     0))
    result["iodine_mg_kg"] = to_mg_kg(totals.get("Iodine", 0))
    result["se_mg_kg"]     = to_mg_kg(totals.get("Se",     0))

    result["vitamin_a_iu_kg"]        = to_iu_kg(totals.get("Vitamin_A", 0))
    result["vitamin_d_iu_kg"]        = to_iu_kg(totals.get("Vitamin_D", 0))
    result["vitamin_e_iu_kg"]        = to_iu_kg(totals.get("Vitamin_E", 0))
    result["thiamin_mg_kg"]          = to_mg_kg(totals.get("Thiamin",          0))
    result["riboflavin_mg_kg"]       = to_mg_kg(totals.get("Riboflavin",       0))
    result["niacin_mg_kg"]           = to_mg_kg(totals.get("Niacin",           0))
    result["pantothenic_acid_mg_kg"] = to_mg_kg(totals.get("Pantothenic_acid", 0))
    result["b6_mg_kg"]               = to_mg_kg(totals.get("Vitamin_B6",       0))
    result["folate_mg_kg"]           = to_mg_kg(totals.get("Folate",           0))
    result["b12_mg_kg"]              = to_mg_kg(totals.get("Vitamin_B12",      0))
    result["choline_mg_kg"]          = to_mg_kg(totals.get("Choline",          0))

    result["linoleic_percent"] = to_pct_dm(totals.get("Linoleic_acid",        0))
    result["ala_percent"]      = to_pct_dm(totals.get("Alpha_linolenic_acid",  0))
    result["epa_percent"]      = to_pct_dm(totals.get("EPA",                  0))
    result["dha_percent"]      = to_pct_dm(totals.get("DHA",                  0))
    result["epa_dha_percent"]  = to_pct_dm(totals.get("EPA", 0) + totals.get("DHA", 0))

    result["arginine_percent"]    = to_pct_dm(totals.get("Arginine",    0))
    result["histidine_percent"]   = to_pct_dm(totals.get("Histidine",   0))
    result["isoleucine_percent"]  = to_pct_dm(totals.get("Isoleucine",  0))
    result["leucine_percent"]     = to_pct_dm(totals.get("Leucine",     0))
    result["lysine_percent"]      = to_pct_dm(totals.get("Lysine",      0))
    result["methionine_percent"]  = to_pct_dm(totals.get("Methionine",  0))
    result["cystine_percent"]     = to_pct_dm(totals.get("Cystine",     0))
    result["met_cys_percent"]     = to_pct_dm(totals.get("Methionine", 0) + totals.get("Cystine", 0))
    result["phenylalanine_percent"]= to_pct_dm(totals.get("Phenylalanine", 0))
    result["tyrosine_percent"]    = to_pct_dm(totals.get("Tyrosine",    0))
    result["phe_tyr_percent"]     = to_pct_dm(totals.get("Phenylalanine", 0) + totals.get("Tyrosine", 0))
    result["threonine_percent"]   = to_pct_dm(totals.get("Threonine",   0))
    result["tryptophan_percent"]  = to_pct_dm(totals.get("Tryptophan",  0))
    result["valine_percent"]      = to_pct_dm(totals.get("Valine",      0))


# ============================================================================
# Ingredient grouping helpers
# ============================================================================
def get_user_ingredients_with_subcategories() -> List[Dict[str, Any]]:
    df = user_df().copy()
    ingredients = []
    for _, row in df.iterrows():
        group_name  = str(row["group_name"]).strip()
        group_lower = group_name.lower()
        if group_lower == "organ meat (liver)":
            display_group = "Organ Meat (Liver)"
        elif group_lower == "organ meat":
            display_group = "Organ Meat (Other)"
        else:
            display_group = group_name
        ingredient_dict = row.to_dict()
        ingredient_dict["display_group_name"]   = display_group
        ingredient_dict["original_group_name"]  = group_name
        ingredients.append(ingredient_dict)
    return ingredients


def get_ingredients_grouped_by_category() -> Dict[str, Dict[str, Any]]:
    ingredients = get_user_ingredients_with_subcategories()
    grouped = defaultdict(list)
    for ingredient in ingredients:
        grouped[ingredient["display_group_name"]].append(ingredient)
    for category in grouped:
        grouped[category].sort(key=lambda x: x["ingredient_name"].lower())

    category_configs = {
        "Meat Group A": {
            "items": grouped.get("Meat Group A", []),
            "label": "01 Meat Group A (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True, "min_selections": 1, "max_selections": 3,
            "notes": "Mandatory: Primary meat source."
        },
        "Meat Group B": {
            "items": grouped.get("Meat Group B", []),
            "label": "02 Meat Group B (Optional - Pick up to one)",
            "mandatory": False, "min_selections": 0, "max_selections": 1,
            "notes": "Optional."
        },
        "Meat Group C": {
            "items": grouped.get("Meat Group C", []),
            "label": "03 Meat Group C (Optional - Pick up to one)",
            "mandatory": False, "min_selections": 0, "max_selections": 1,
            "notes": "Optional."
        },
        "Organ Meat (Other)": {
            "items": grouped.get("Organ Meat (Other)", []),
            "label": "04 Organ Meat - Other (Optional - Pick up to one)",
            "mandatory": False, "min_selections": 0, "max_selections": 1,
            "notes": "Optional: 35g when selected."
        },
        "Organ Meat (Liver)": {
            "items": grouped.get("Organ Meat (Liver)", []),
            "label": "05 Organ Meat - Liver (Mandatory - Select one)",
            "mandatory": True, "min_selections": 1, "max_selections": 1,
            "notes": "Mandatory: 160g always."
        },
        "Grain A": {
            "items": grouped.get("Grain A", []),
            "label": "06 Grain A (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True, "min_selections": 1, "max_selections": 3,
            "notes": "Mandatory: Balancing ingredient."
        },
        "Grain B": {
            "items": grouped.get("Grain B", []),
            "label": "07 Grain B (Optional - Pick up to one)",
            "mandatory": False, "min_selections": 0, "max_selections": 1,
            "notes": "Optional."
        },
        "Vegetable A": {
            "items": grouped.get("Vegetable A", []),
            "label": "08 Vegetable A (Mandatory - Select at least one and up to three maximum)",
            "mandatory": True, "min_selections": 1, "max_selections": 3,
            "notes": "90g with Veg B, 130g alone."
        },
        "Vegetable B": {
            "items": grouped.get("Vegetable B", []),
            "label": "09 Vegetable B (Optional - Pick up to two)",
            "mandatory": False, "min_selections": 0, "max_selections": 2,
            "notes": "40g total."
        },
        "Vegetable C": {
            "items": grouped.get("Vegetable C", []),
            "label": "10 Vegetable C - Potatoes (Optional - Pick up to one)",
            "mandatory": False, "min_selections": 0, "max_selections": 1,
            "notes": "Optional: 30g."
        },
        "Fruit": {
            "items": grouped.get("Fruit", []),
            "label": "11 Fruit (Optional - Up to two maximum)",
            "mandatory": False, "min_selections": 0, "max_selections": 2,
            "notes": "5g total if selected."
        },
        "Oil": {
            "items": grouped.get("Oil", []),
            "label": "12 Oil (Mandatory - Select at least one and a maximum of three)",
            "mandatory": True, "min_selections": 1, "max_selections": 3,
            "notes": "Mandatory: 10g total."
        },
        "Fiber": {
            "items": grouped.get("Fiber", []),
            "label": "13 Others (Optional - Pick up to two maximum)",
            "mandatory": False, "min_selections": 0, "max_selections": 2,
            "notes": f"Optional: Max {FIBER_SUPP_MAX_G}g total."
        },
        "Mineral Group A": {
            "items": grouped.get("Mineral Group A", []),
            "label": "14 Mineral Group A",
            "mandatory": True, "min_selections": 1, "max_selections": 2,
            "dm_range": {"min": 12.5, "max": 25},
            "percentage": "22g base (25g if Group B not selected) — split evenly if both selected",
            "notes": "Mandatory: Select Eggshells, Calcium Carbonate, or both. Gets +3g bonus if Group B not selected."
        },

    }

    return {k: v for k, v in category_configs.items() if v["items"]}


# ============================================================================
# Per-ingredient contribution helper
# ============================================================================
def _calc_ingredient_contribution(row: pd.Series, dm: float, fresh_weight: float = None) -> Dict[str, Any]:
    def get_col(col_name, default=0.0):
        try:
            val = float(row.get(col_name, default))
            return default if pd.isna(val) else val
        except:
            return default

    water_pct = get_water_percent(row)
    if fresh_weight is None:
        fresh_weight = calculate_fresh_weight(dm, water_pct)

    calcium_val    = round(get_col("calcium_mg")    * dm / 100.0, 3)
    phosphorus_val = round(get_col("phosphorus_mg") * dm / 100.0, 3)

    return {
        "ingredient":       row["ingredient_name"],
        "dm_g":             round(dm, 2),
        "fresh_weight_g":   round(fresh_weight, 2),
        "water_percent":    round(water_pct, 2),
        "fiber_g":          round(get_col("fiber_g") * dm / 100.0, 3),
        "protein_g":        round(get_col("protein_g") * dm / 100.0, 3),
        "fat_g":            round(get_col("fat_g")     * dm / 100.0, 3),
        "cho_g":            round(get_col("cho_g")     * dm / 100.0, 3),
        "ash_g":            round(get_col("ash_g")     * dm / 100.0, 3),
        "calcium_mg":       calcium_val,
        "ca_mg":            calcium_val,
        "phosphorus_mg":    phosphorus_val,
        "p_mg":             phosphorus_val,
        "magnesium_mg":     round(get_col("magnesium_mg")    * dm / 100.0, 3),
        "potassium_mg":     round(get_col("potassium_mg")    * dm / 100.0, 3),
        "sodium_mg":        round(get_col("sodium_mg")       * dm / 100.0, 3),
        "iron_mg":          round(get_col("iron_mg")         * dm / 100.0, 3),
        "zinc_mg":          round(get_col("zinc_mg")         * dm / 100.0, 3),
        "copper_mg":        round(get_col("copper_mg")       * dm / 100.0, 3),
        "iodine_mg":        round(get_col("iodine_mg")       * dm / 100.0, 4),
        "selenium_mg":      round(get_col("selenium_mg")     * dm / 100.0, 4),
        "thiamin_mg":       round(get_col("thiamin_mg")      * dm / 100.0, 4),
        "riboflavin_mg":    round(get_col("riboflavin_mg")   * dm / 100.0, 4),
        "niacin_mg":        round(get_col("niacin_mg")       * dm / 100.0, 4),
        "pantothenic_acid_mg": round(get_col("pantothenic_acid_mg") * dm / 100.0, 4),
        "vitamin_b6_mg":    round(get_col("vitamin_b6_mg")   * dm / 100.0, 4),
        "folate_ug":        round(get_col("folate_ug")       * dm / 100.0, 4),
        "choline_mg":       round(get_col("choline_mg")      * dm / 100.0, 4),
        "vitamin_b12_ug":   round(get_col("vitamin_b12_ug") * dm / 100.0, 4),
        "vitamin_a_iu":     round(get_col("vitamin_a_iu")    * dm / 100.0, 2),
        "vitamin_e_mg":     round(get_col("vitamin_e_mg")    * dm / 100.0, 4),
        "vitamin_d_iu":     round(get_col("vitamin_d_iu")    * dm / 100.0, 2),
        "linoleic_g":       round(get_col("pufa_18_2_g") * dm / 100.0, 4),
        "ala_g":            round(get_col("pufa_18_3_g") * dm / 100.0, 4),
        "epa_g":            round(get_col("pufa_20_5_g") * dm / 100.0, 4),
        "dha_g":            round(get_col("pufa_22_6_g") * dm / 100.0, 4),
        "tryptophan_g":     round(get_col("tryptophan_g")    * dm / 100.0, 4),
        "threonine_g":      round(get_col("threonine_g")     * dm / 100.0, 4),
        "isoleucine_g":     round(get_col("isoleucine_g")    * dm / 100.0, 4),
        "leucine_g":        round(get_col("leucine_g")       * dm / 100.0, 4),
        "lysine_g":         round(get_col("lysine_g")        * dm / 100.0, 4),
        "methionine_g":     round(get_col("methionine_g")    * dm / 100.0, 4),
        "cystine_g":        round(get_col("cystine_g")       * dm / 100.0, 4),
        "phenylalanine_g":  round(get_col("phenylalanine_g") * dm / 100.0, 4),
        "tyrosine_g":       round(get_col("tyrosine_g")      * dm / 100.0, 4),
        "valine_g":         round(get_col("valine_g")        * dm / 100.0, 4),
        "arginine_g":       round(get_col("arginine_g")      * dm / 100.0, 4),
        "histidine_g":      round(get_col("histidine_g")     * dm / 100.0, 4),
        "energy_kcal":      round(get_col("energy_kcal")     * dm / 100.0, 2),
    }


# ============================================================================
# Main calculate_diet function
# ============================================================================
def calculate_diet(selected_names: List[str]) -> Dict[str, Any]:
    fdf = fixed_df()
    udf = user_df()

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

    meats_a     = [r for r in picks if gname(r) == "meat group a"]
    meats_b     = [r for r in picks if gname(r) == "meat group b"]
    meats_c     = [r for r in picks if gname(r) == "meat group c"]

    # Fish-in-Meat-A rule: if ANY Meat A selection is fish, cod liver drops to 0g
    # (alone or alongside other meats). Grain A (balancer) automatically absorbs the freed grams.
    # Fish-in-Meat-C rule: if ANY Meat C selection is fish AND Meat A is NOT fish, cod liver drops to 7g.
    meat_a_names = {str(r.get("ingredient_name", "")).strip().lower() for r in meats_a}
    meat_c_names = {str(r.get("ingredient_name", "")).strip().lower() for r in meats_c}
    has_fish_in_meat_a = bool(meat_a_names & FISH_MEAT_A_NAMES)
    has_fish_in_meat_c = bool(meat_c_names & FISH_MEAT_C_NAMES)
    effective_overrides = FIXED_OVERRIDES.copy()
    if has_fish_in_meat_a:
        effective_overrides["cod liver oil"] = 0.0  # Meat A fish wins — 0g
    elif has_fish_in_meat_c:
        effective_overrides["cod liver oil"] = 0.0  # Meat C fish only — 0g
    grains_a    = [r for r in picks if gname(r) == "grain a"]
    grains_b    = [r for r in picks if gname(r) == "grain b"]
    veg_a       = [r for r in picks if gname(r) == "vegetable a"]
    veg_b       = [r for r in picks if gname(r) == "vegetable b"]
    veg_c       = [r for r in picks if gname(r) == "vegetable c"]
    oils        = [r for r in picks if gname(r) == "oil"]
    fruits      = [r for r in picks if gname(r) == "fruit"]
    livers      = [r for r in picks if gname(r) == "organ meat (liver)"]
    organs      = [r for r in picks if gname(r) == "organ meat"]
    fiber_supps = [r for r in picks if gname(r) == "fiber"]
    minerals   = [r for r in picks if gname(r) == "mineral group a"]

    issues: List[str] = []
    if not grains_a:
        issues.append("ERROR: Grain A is MANDATORY.")
    if not minerals:
        issues.append("ERROR: Mineral Group A is MANDATORY - select Eggshells, Calcium Carbonate, or both.")

    # --- MINERAL ALLOCATION ---
    # Group A: always 27g (bone meal/dry blood meal are in fixed CSV)
    has_minerals = len(minerals) > 0
    mineral_a_dm = MINERALS_A_DM if has_minerals else 0.0
    if not meats_a:
        issues.append("ERROR: Meat Group A is MANDATORY.")
    if not oils:
        issues.append("ERROR: At least one Oil must be selected.")
    if not livers:
        issues.append("ERROR: Liver is MANDATORY.")
    if not veg_a:
        issues.append("ERROR: Vegetable A is MANDATORY.")
    if len(veg_a) + len(veg_b) < 2:
        issues.append("ERROR: Must select at least 2 vegetables total.")

    has_seeds   = len(fiber_supps) > 0
    has_organ   = len(organs) > 0
    has_meat_b  = len(meats_b) > 0
    has_meat_c  = len(meats_c) > 0
    has_grain_b = len(grains_b) > 0
    has_potato  = len(veg_c) > 0
    has_fruits  = len(fruits) > 0
    has_veg_b   = len(veg_b) > 0

    # Meat allocation
    if has_meat_b and has_meat_c:
        meat_a_dm, meat_b_dm, meat_c_dm = MEAT_A_WITH_BC_DM, MEAT_B_WITH_AC_DM, MEAT_C_WITH_AB_DM
    elif has_meat_b:
        meat_a_dm, meat_b_dm, meat_c_dm = MEAT_A_WITH_B_DM, MEAT_B_WITH_A_DM, 0.0
    elif has_meat_c:
        meat_a_dm, meat_b_dm, meat_c_dm = MEAT_A_WITH_C_DM, 0.0, MEAT_C_WITH_A_DM
    else:
        meat_a_dm, meat_b_dm, meat_c_dm = MEAT_A_ONLY_DM, 0.0, 0.0

    # Liver / organ allocation
    liver_dm = LIVER_WITH_ORGAN_DM if has_organ else LIVER_ALONE_DM
    organ_dm = ORGAN_OTHER_DM if has_organ else 0.0

    oils_dm   = OILS_DM if oils else 0.0
    fruits_dm = FRUITS_TOTAL_DM if has_fruits else 0.0
    seeds_dm  = SEEDS_TOTAL_DM  if has_seeds  else 0.0

    # Vegetable allocation
    veg_a_base = VEG_A_WITH_B_DM if has_veg_b else VEG_A_ONLY_DM
    veg_b_base = VEG_B_ALONE_DM  if has_veg_b else 0.0
    veg_a_reduction = 0.0
    veg_b_reduction = 0.0
    if has_seeds:
        veg_a_reduction += SEEDS_VEG_REDUCTION
        if has_veg_b:
            veg_b_reduction += SEEDS_VEG_REDUCTION
    if has_fruits:
        veg_a_reduction += FRUITS_VEG_REDUCTION
        if has_veg_b:
            veg_b_reduction += FRUITS_VEG_REDUCTION
    veg_a_dm_alloc = veg_a_base - veg_a_reduction
    veg_b_dm_alloc = veg_b_base - veg_b_reduction

    # Grain allocation — grain A always balances to 1000g dynamically
    if has_grain_b and has_potato:
        grain_b_dm, potato_dm = GRAIN_B_WITH_POTATO_DM, POTATO_WITH_GRAINB_DM
    elif has_grain_b:
        grain_b_dm, potato_dm = GRAIN_B_WITH_A_DM, 0.0
    elif has_potato:
        grain_b_dm, potato_dm = 0.0, POTATO_NO_GRAINB_DM
    else:
        grain_b_dm, potato_dm = 0.0, 0.0

    def _safe_dm(val):
        try:
            v = float(val)
            return v if not pd.isna(v) else 0.0
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

    non_grain_a_total = (
        fixed_ingredients_dm +
        meat_a_dm + meat_b_dm + meat_c_dm +
        grain_b_dm + liver_dm + organ_dm +
        veg_a_dm_alloc + veg_b_dm_alloc + potato_dm +
        fruits_dm + oils_dm + seeds_dm +
        mineral_a_dm
    )

    # Grain A always balances to 1000g
    grain_a_dm = FIXED_TOTAL_DM - non_grain_a_total

    if grain_a_dm < 0:
        issues.append(
            f"ERROR: Non-Grain-A total {non_grain_a_total:.1f}g exceeds {FIXED_TOTAL_DM:.0f}g budget."
        )
        grain_a_dm = 0.0

    total_allocated = non_grain_a_total + grain_a_dm
    if abs(total_allocated - FIXED_TOTAL_DM) > 1.0:
        issues.append(
            f"WARNING: Total DM = {total_allocated:.1f}g (expected {FIXED_TOTAL_DM:.0f}g)."
        )

    # Build diet
    dm_breakdown_raw: List[Dict[str, Any]] = []
    ingredient_totals: List[Dict[str, Any]] = []
    ingredient_allocations: Dict[str, Dict[str, Any]] = {}
    totals = _init_totals()
    remaining = FIXED_TOTAL_DM

    def _add_item(row: pd.Series, dm: float, is_fixed: bool = False):
        nonlocal remaining
        if dm <= 0 or remaining <= 0:
            return
        dm = min(dm, remaining)
        name = row["ingredient_name"]
        _add_row(totals, dm, row)
        water_pct  = get_water_percent(row)
        fresh_wt   = calculate_fresh_weight(dm, water_pct)
        dm_breakdown_raw.append({"ingredient": name, "dm_g": round(dm, 2),
                                  "fresh_weight_g": round(fresh_wt, 2),
                                  "water_percent": water_pct, "fixed": is_fixed})
        ingredient_totals.append(_calc_ingredient_contribution(row, dm, fresh_wt))
        if name in ingredient_allocations:
            ingredient_allocations[name]["dm_g"]          += round(dm, 2)
            ingredient_allocations[name]["fresh_weight_g"]+= round(fresh_wt, 2)
        else:
            ingredient_allocations[name] = {"dm_g": round(dm, 2), "fresh_weight_g": round(fresh_wt, 2),
                                             "water_percent": water_pct, "fixed": is_fixed}
        remaining -= dm

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

    if livers:
        per = liver_dm / len(livers)
        for r in livers: _add_item(r, per)

    if organs and has_organ:
        per = organ_dm / len(organs)
        for r in organs: _add_item(r, per)

    if oils:
        per = oils_dm / len(oils)
        for r in oils: _add_item(r, per)

    if veg_a:
        per = veg_a_dm_alloc / len(veg_a)
        for r in veg_a: _add_item(r, per)

    if veg_b:
        per = veg_b_dm_alloc / len(veg_b)
        for r in veg_b: _add_item(r, per)

    if veg_c:
        _add_item(veg_c[0], potato_dm)

    if fruits:
        per = fruits_dm / len(fruits)
        for r in fruits: _add_item(r, per)

    if fiber_supps:
        per = seeds_dm / len(fiber_supps)
        for r in fiber_supps: _add_item(r, per)

    # Mineral Group A (mandatory - 22g base, 25g if Group B not selected)
    if minerals:
        per = mineral_a_dm / len(minerals)
        for r in minerals: _add_item(r, per)

    if grains_a:
        per = grain_a_dm / len(grains_a)
        for r in grains_a: _add_item(r, per)

    if grains_b:
        per = grain_b_dm / len(grains_b)
        for r in grains_b: _add_item(r, per)

    if meats_a:
        per = meat_a_dm / len(meats_a)
        for r in meats_a: _add_item(r, per)

    if meats_b:
        per = meat_b_dm / len(meats_b)
        for r in meats_b: _add_item(r, per)

    if meats_c:
        per = meat_c_dm / len(meats_c)
        for r in meats_c: _add_item(r, per)

    # Quality checks
    protein_pct = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
    fat_pct     = totals["Fat"]     * 100.0 / FIXED_TOTAL_DM
    cho_pct     = totals["CHO"]     * 100.0 / FIXED_TOTAL_DM
    fiber_pct   = totals["Fiber"]   * 100.0 / FIXED_TOTAL_DM

    protein_g_per_100g = totals["Protein"] * 100.0 / FIXED_TOTAL_DM
    fat_g_per_100g     = totals["Fat"]     * 100.0 / FIXED_TOTAL_DM
    cho_g_per_100g     = totals["CHO"]     * 100.0 / FIXED_TOTAL_DM
    energy_kcal_per_kg = ((protein_g_per_100g * 4) + (fat_g_per_100g * 9) + (cho_g_per_100g * 4)) * 10

    total_ca_mg = totals["Ca"] * 1000
    total_p_mg  = totals["P"]  * 1000
    ca_p_ratio  = (total_ca_mg / total_p_mg) if total_p_mg else 0.0

    omega6 = totals.get("Linoleic_acid", 0)
    omega3 = totals.get("Alpha_linolenic_acid", 0) + totals.get("EPA", 0) + totals.get("DHA", 0)
    omega6_omega3_ratio = (omega6 / omega3) if omega3 > 0 else 0.0

    if protein_pct < PROTEIN_MIN:
        issues.append(f"WARNING: Protein {protein_pct:.1f}% (target {PROTEIN_MIN}–{PROTEIN_MAX}%). Too low.")
    elif protein_pct > PROTEIN_MAX:
        issues.append(f"WARNING: Protein {protein_pct:.1f}% (target {PROTEIN_MIN}–{PROTEIN_MAX}%). Too high.")
    if fat_pct <= FAT_MIN:
        issues.append(f"WARNING: Fat {fat_pct:.1f}% (target >{FAT_MIN}% <{FAT_MAX}%). Too low.")
    elif fat_pct >= FAT_MAX:
        issues.append(f"WARNING: Fat {fat_pct:.1f}% (target >{FAT_MIN}% <{FAT_MAX}%). Too high.")
    if fiber_pct <= MIN_TOTAL_FIBER_PCT:
        issues.append(f"WARNING: Fiber {fiber_pct:.1f}% (target >{MIN_TOTAL_FIBER_PCT}% <{MAX_TOTAL_FIBER_PCT}%). Too low.")
    elif fiber_pct >= MAX_TOTAL_FIBER_PCT:
        issues.append(f"WARNING: Fiber {fiber_pct:.1f}% (target >{MIN_TOTAL_FIBER_PCT}% <{MAX_TOTAL_FIBER_PCT}%). Too high.")
    if energy_kcal_per_kg < ENERGY_MIN:
        issues.append(f"WARNING: Energy {energy_kcal_per_kg:.0f} kcal/kg (target {ENERGY_MIN:.0f}–{ENERGY_MAX:.0f}). Too low.")
    elif energy_kcal_per_kg > ENERGY_MAX:
        issues.append(f"WARNING: Energy {energy_kcal_per_kg:.0f} kcal/kg (target {ENERGY_MIN:.0f}–{ENERGY_MAX:.0f}). Too high.")
    if ca_p_ratio < CA_P_RATIO_MIN:
        issues.append(f"WARNING: Ca:P {ca_p_ratio:.2f}:1 (target >{CA_P_RATIO_MIN} <{CA_P_RATIO_MAX}). Too low.")
    elif ca_p_ratio > CA_P_RATIO_MAX:
        issues.append(f"WARNING: Ca:P {ca_p_ratio:.2f}:1 (target >{CA_P_RATIO_MIN} <{CA_P_RATIO_MAX}). Too high.")
    if omega3 > 0:
        if omega6_omega3_ratio < OMEGA6_OMEGA3_RATIO_MIN:
            issues.append(f"WARNING: Omega-6:3 {omega6_omega3_ratio:.1f}:1 (target >{OMEGA6_OMEGA3_RATIO_MIN} <{OMEGA6_OMEGA3_RATIO_MAX}). Too low.")
        elif omega6_omega3_ratio > OMEGA6_OMEGA3_RATIO_MAX:
            issues.append(f"WARNING: Omega-6:3 {omega6_omega3_ratio:.1f}:1 (target >{OMEGA6_OMEGA3_RATIO_MIN} <{OMEGA6_OMEGA3_RATIO_MAX}). Too high.")

    def pct(key: str) -> float:
        return round(totals[key] * 100.0 / FIXED_TOTAL_DM, 2)

    total_fresh_weight = sum(alloc["fresh_weight_g"] for alloc in ingredient_allocations.values())

    result = {
        "Protein_percent": pct("Protein"),
        "Fat_percent":     pct("Fat"),
        "CHO_percent":     pct("CHO"),
        "Fiber_percent":   pct("Fiber"),
        "Ash_percent":     pct("Ash"),
        "Ca_percent":      round(totals["Ca"] / FIXED_TOTAL_DM * 100, 4),
        "P_percent":       round(totals["P"]  / FIXED_TOTAL_DM * 100, 4),
        "Ca_P_ratio":      round(ca_p_ratio, 2),
        "Energy":          round(energy_kcal_per_kg, 2),
        "DM_percent":      FIXED_TOTAL_DM,
        "iron_mg":         round(totals["Iron"] * 1000 / FIXED_TOTAL_DM, 2),
        "total_fresh_weight_g": round(total_fresh_weight, 2),
        "omega6_omega3_ratio":  round(omega6_omega3_ratio, 2),
        "allocation_summary": {
            "meat_a_dm": meat_a_dm, "meat_b_dm": meat_b_dm, "meat_c_dm": meat_c_dm,
            "grain_a_dm": grain_a_dm, "grain_b_dm": grain_b_dm, "potato_dm": potato_dm,
            "liver_dm": liver_dm, "organ_dm": organ_dm,
            "veg_a_dm": veg_a_dm_alloc, "veg_b_dm": veg_b_dm_alloc,
            "fruits_dm": fruits_dm, "oils_dm": oils_dm, "seeds_dm": seeds_dm,
            "mineral_a_dm": mineral_a_dm,
            "minerals_a_selected": len(minerals),
        }
    }

    _add_extended_nutrient_output(result, totals, FIXED_TOTAL_DM)
    aafco_percent_of_minimum = calculate_aafco_percent_of_minimum(totals, FIXED_TOTAL_DM)

    result.update({
        "dm_breakdown":           _compress_breakdown(dm_breakdown_raw),
        "ingredient_totals":      ingredient_totals,
        "ingredient_allocations": ingredient_allocations,
        "issues":                 issues,
        "aafco_percent_of_minimum": aafco_percent_of_minimum,
    })

    return result
