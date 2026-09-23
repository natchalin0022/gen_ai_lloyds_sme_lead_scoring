"""
Logistic Regression to predict Lloyds customer propensity
Uses 7_7_2026_out__1_.csv

SETUP:
    pip install scikit-learn pandas matplotlib
    python logistic_regression_model_test.py
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    roc_auc_score, classification_report, roc_curve
)

# ── 1. Load ───────────────────────────────────────────────────────
# Change this to wherever your CSV actually is on your Mac.
# The safest way: right-click the CSV in Finder, hold Option,
# then click "Copy ... as Pathname" and paste it here.
CSV_PATH = "/Users/patorn/Desktop/team_21_lloyds/Data Preprocess/7_7_2026_out (1).csv"

df = pd.read_csv(CSV_PATH, dtype=str)
df["com_num"] = df["com_num"].str.strip()
print(f"Loaded {len(df):,} companies")

# ── 2. Label ──────────────────────────────────────────────────────
df["label"] = (df["lloyds_customer"].str.strip() == "True").astype(int)
print(f"Positives: {df['label'].sum()}   Negatives: {(df['label']==0).sum()}")
print(f"Positive rate: {df['label'].mean():.1%}")
print()

# ── 3. Feature engineering ────────────────────────────────────────

# account_type — ordinal size proxy
account_order = {
    "micro-entity"          : 0,
    "total-exemption-small" : 1,
    "total-exemption-full"  : 2,
    "small"                 : 3,
    "medium"                : 4,
}
df["account_type_enc"] = df["account_type"].map(account_order).fillna(2)

# sic_code — keep top 20, group rest as "other"
top_sics = df["sic_code"].value_counts().head(20).index
df["sic_group"]    = df["sic_code"].where(df["sic_code"].isin(top_sics), "other")
df["sic_code_enc"] = LabelEncoder().fit_transform(df["sic_group"])

# recent_psc_kind — one-hot
psc_dummies = pd.get_dummies(df["recent_psc_kind"], prefix="psc")
df = pd.concat([df, psc_dummies], axis=1)

# is_dormant_sic
df["is_dormant_sic"] = (df["sic_code"] == "98000").astype(int)

# accounts_overdue → 0/1
df["accounts_overdue_enc"] = (
    df["accounts_overdue"].str.strip().str.lower() == "true"
).astype(int)

# recent_charge_status → one-hot (outstanding / satisfied / none)
charge_status_map = {
    "no_charges"      : "none",
    "outstanding"     : "outstanding",
    "fully-satisfied" : "satisfied",
    "part-satisfied"  : "satisfied",
}
df["charge_status_clean"] = df["recent_charge_status"].map(charge_status_map).fillna("none")
charge_dummies = pd.get_dummies(df["charge_status_clean"], prefix="charge")
df = pd.concat([df, charge_dummies], axis=1)

# recent_charge_created_on → bucketed age
snapshot = datetime(2026, 7, 7)

def charge_age_bucket(date_str):
    if date_str in ("no_charges", None) or not date_str or pd.isna(date_str):
        return "none"
    try:
        d     = datetime.strptime(date_str, "%Y-%m-%d")
        years = (snapshot - d).days / 365.25
        if years < 1:  return "recent_0_1"
        if years < 3:  return "medium_1_3"
        return "old_3_plus"
    except (ValueError, TypeError):
        return "none"

df["charge_age_bucket"] = df["recent_charge_created_on"].apply(charge_age_bucket)
charge_age_dummies      = pd.get_dummies(df["charge_age_bucket"], prefix="chargeage")
df                      = pd.concat([df, charge_age_dummies], axis=1)

# company_type — simplified
company_type_simple = df["company_type"].fillna("unknown").apply(
    lambda t: "ltd"       if "Private Limited Company" == t else
              "guarantee" if "PRI/LTD BY GUAR" in str(t) else
              "llp"       if "Limited Liability" in str(t) else
              "unknown"
)
company_type_dummies = pd.get_dummies(company_type_simple, prefix="cotype")
df = pd.concat([df, company_type_dummies], axis=1)

# region — postcode area (first letters), top 15
def postcode_area(pc):
    if pd.isna(pc) or not pc: return "unknown"
    pc = pc.strip().upper().split()[0]
    return "".join(c for c in pc if c.isalpha()) or "unknown"

df["postcode_area"]     = df["post_code"].apply(postcode_area)
top_areas               = df["postcode_area"].value_counts().head(15).index
df["postcode_area_grp"] = df["postcode_area"].where(
    df["postcode_area"].isin(top_areas), "other"
)
df["postcode_area_enc"] = LabelEncoder().fit_transform(df["postcode_area_grp"])

# ── 4. Build final feature list ───────────────────────────────────
feature_cols = (
    ["account_type_enc", "sic_code_enc", "is_dormant_sic",
     "accounts_overdue_enc", "postcode_area_enc"]
    + list(psc_dummies.columns)
    + list(charge_dummies.columns)
    + list(charge_age_dummies.columns)
    + list(company_type_dummies.columns)
)

X = df[feature_cols].astype(float)
y = df["label"]

print(f"Features used: {len(feature_cols)}")
print()

# ── 5. Stratified 80/20 split ─────────────────────────────────────
X_train, X_test, y_train, y_test = train_test_split(
    X, y,
    test_size    = 0.20,
    stratify     = y,       # keeps 1.7% positive rate in both sets
    random_state = 42,
)

print(f"Train: {len(X_train):,}   ({int(y_train.sum())} positive)")
print(f"Test:  {len(X_test):,}   ({int(y_test.sum())} positive)")
print()

# ── 6. Train the model ────────────────────────────────────────────
model = Pipeline([
    ("scaler", StandardScaler()),
    ("lr",     LogisticRegression(
        class_weight = "balanced",  # handles 56:1 imbalance
        max_iter     = 1000,
        C            = 1.0,          # default regularisation
        solver       = "lbfgs",
        random_state = 42,
    ))
])

model.fit(X_train, y_train)
print("Model trained.")
print()

# ── 7. Evaluate on test set ───────────────────────────────────────
test_prob = model.predict_proba(X_test)[:, 1]
test_auc  = roc_auc_score(y_test, test_prob)

print("=" * 50)
print("RESULTS")
print("=" * 50)
print(f"Test AUC: {test_auc:.3f}")
print()
print(classification_report(y_test, test_prob >= 0.5, zero_division=0))

def precision_at_k(y_true, y_prob, k):
    top_k = np.argsort(y_prob)[::-1][:k]
    return np.array(y_true)[top_k].mean()

baseline = y_test.mean()
print(f"Baseline positive rate: {baseline:.1%}")
print()
print(f"{'Metric':<15} {'Score':>8} {'Lift':>8}")
print("-" * 34)
for k in [10, 20, 50, 100]:
    if len(X_test) >= k:
        p    = precision_at_k(y_test, test_prob, k)
        lift = p / baseline if baseline > 0 else 0
        print(f"Precision@{k:<5} {p:>8.1%} {lift:>7.1f}x")
print()

# ── 8. What the model learned ─────────────────────────────────────
coefs = pd.DataFrame({
    "feature"    : feature_cols,
    "coefficient": model.named_steps["lr"].coef_[0],
}).sort_values("coefficient", ascending=False)

print("Top 8 features INCREASING propensity:")
print(coefs.head(8).to_string(index=False))
print()
print("Top 8 features DECREASING propensity:")
print(coefs.tail(8).to_string(index=False))
print()

# ── 9. Plots ──────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(15, 5))

# ROC curve
ax = axes[0]
fpr, tpr, _ = roc_curve(y_test, test_prob)
ax.plot(fpr, tpr, color="steelblue", label=f"LR (AUC={test_auc:.3f})")
ax.plot([0,1],[0,1],"k--", alpha=0.4, label="Random")
ax.fill_between(fpr, tpr, alpha=0.1, color="steelblue")
ax.set_xlabel("False positive rate")
ax.set_ylabel("True positive rate")
ax.set_title("ROC Curve — Test Set")
ax.legend()

# Coefficients
ax = axes[1]
top_coefs = pd.concat([coefs.head(8), coefs.tail(8)])
coef_plot = top_coefs.set_index("feature")["coefficient"].sort_values()
colors    = ["crimson" if v < 0 else "steelblue" for v in coef_plot]
coef_plot.plot(kind="barh", ax=ax, color=colors)
ax.axvline(0, color="black", linewidth=0.8)
ax.set_title("Top +/- features")
ax.set_xlabel("Coefficient")

plt.tight_layout()
plt.savefig("lr_results.png", dpi=150)
plt.close()
print("Saved lr_results.png")

# ── 10. Score all companies and save ranked lead list ──────────────
df["propensity_score"] = model.predict_proba(X)[:, 1]
df["tier"] = pd.cut(
    df["propensity_score"],
    bins   = [0, 0.40, 0.70, 1.0],
    labels = ["LOW", "MEDIUM", "HIGH"],
)

keep_cols = ["com_num", "name", "sic_code", "account_type", "company_type",
             "post_code", "town", "county",
             "lloyds_customer", "label",
             "propensity_score", "tier"]
keep_cols = [c for c in keep_cols if c in df.columns]

df[keep_cols].sort_values(
    "propensity_score", ascending=False
).to_csv("lr_lead_list.csv", index=False)

print(f"Saved lr_lead_list.csv")
print(f"HIGH:   {(df['tier']=='HIGH').sum():,}")
print(f"MEDIUM: {(df['tier']=='MEDIUM').sum():,}")
print(f"LOW:    {(df['tier']=='LOW').sum():,}")