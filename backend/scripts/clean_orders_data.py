import pandas as pd
import numpy as np
import re

file_path = r'c:\Users\Admin\Documents\AutoBI\Multi-Agent-BI-Engine\messy_orders_data.csv'
df = pd.read_csv(file_path, skip_blank_lines=False)

# Track issues
report = []

# 1. Ghost Rows
ghost_mask = df.drop(columns=['Order ID'], errors='ignore').isna().all(axis=1) | (df.drop(columns=['Order ID'], errors='ignore') == '').all(axis=1)
if ghost_mask.any():
    report.append(f"- **Ghost Rows**: Found and removed {ghost_mask.sum()} entirely blank row(s).")
df = df[~ghost_mask]

# 2. Whitespace and missing value string representations
for col in df.columns:
    if df[col].dtype == 'object':
        df[col] = df[col].str.strip()
        df[col] = df[col].replace(r'^(?i)(null|n/a|nan|none|-)$', np.nan, regex=True)

report.append("- **Whitespace & Null Strings**: Trimmed leading/trailing spaces and converted string placeholders ('NaN', 'null', 'N/A', 'None', '-') to actual null values.")

# 3. Duplicates
if 'Order ID' in df.columns:
    df['Order ID'] = pd.to_numeric(df['Order ID'], errors='coerce')
    df = df.dropna(subset=['Order ID']) # drop if Order ID is totally broken
    dup_mask = df.duplicated(subset=['Order ID'], keep=False)
    if dup_mask.any():
        num_dups = df.duplicated(subset=['Order ID'], keep='first').sum()
        df['non_null_count'] = df.notna().sum(axis=1)
        df = df.sort_values('non_null_count', ascending=False).drop_duplicates(subset=['Order ID']).drop(columns=['non_null_count'])
        report.append(f"- **Duplicates**: Found and removed {num_dups} duplicate row(s) based on Order ID, preserving the row with the most complete data.")

# 4. Email Validation
def is_valid_email(email):
    if pd.isna(email): return email
    return email if re.match(r"^[^@]+@[^@]+\.[^@]+$", str(email)) else np.nan

if 'customer_email' in df.columns:
    initial_nulls = df['customer_email'].isna().sum()
    df['customer_email'] = df['customer_email'].apply(is_valid_email)
    new_nulls = df['customer_email'].isna().sum()
    if new_nulls > initial_nulls:
        report.append(f"- **Email Validation**: Invalidated {new_nulls - initial_nulls} malformed email(s) (e.g. missing @ or domain).")

# 5. Categorical Standardization
def std_country(c):
    if pd.isna(c): return c
    c_lower = str(c).lower().replace('.', '').strip()
    if c_lower in ['us', 'usa', 'united states']: return 'United States'
    if c_lower in ['uk', 'united kingdom']: return 'United Kingdom'
    if c_lower in ['uae', 'united arab emirates']: return 'United Arab Emirates'
    return str(c).title()

if 'Country' in df.columns:
    df['Country'] = df['Country'].apply(std_country)
    report.append("- **Categorical Standardization (Country)**: Normalized country names (e.g., 'USA' -> 'United States', 'india' -> 'India').")

cols_to_title = ['Category', 'Payment Method', 'Payment Status', 'Shipping Method', 'Order Status']
for col in cols_to_title:
    if col in df.columns:
        df[col] = df[col].str.title()
report.append("- **Categorical Standardization (Others)**: Normalized casing to Title Case for 'Category', 'Payment Method', 'Payment Status', 'Shipping Method', and 'Order Status'.")

# 6. Mixed-Type / Text in Numeric & Outliers (Domain bounds)
def safe_numeric(val):
    if pd.isna(val) or val == '': return np.nan
    val = str(val).replace('$', '').replace('USD', '').replace(',', '').strip()
    try:
        return float(val)
    except ValueError:
        return np.nan

numeric_cols = ['Quantity', 'Unit Price', 'Discount (%)', 'Tax Amount', 'Total Amount', 'Shipping Cost', 'Customer Rating']
for col in numeric_cols:
    if col in df.columns:
        df[col] = df[col].apply(safe_numeric)

report.append("- **Numeric Casting & Cleaning**: Removed currency symbols ('$', 'USD') and safely cast numerical columns (e.g., Total Amount, Unit Price, Shipping Cost) to proper numeric types.")

# Domain bounds for Outliers
if 'Quantity' in df.columns:
    mask = df['Quantity'] < 0
    if mask.any():
        df.loc[mask, 'Quantity'] = np.nan
        report.append(f"- **Outliers (Quantity)**: Invalidated {mask.sum()} negative quantity value(s) due to domain constraints.")

if 'Unit Price' in df.columns:
    mask = df['Unit Price'] < 0
    if mask.any():
        df.loc[mask, 'Unit Price'] = np.nan
        report.append(f"- **Outliers (Unit Price)**: Invalidated {mask.sum()} negative unit price(s) due to domain constraints.")
        
if 'Discount (%)' in df.columns:
    mask = (df['Discount (%)'] < 0) | (df['Discount (%)'] > 100)
    if mask.any():
        df.loc[mask, 'Discount (%)'] = np.nan
        report.append(f"- **Outliers (Discount %)**: Invalidated {mask.sum()} unrealistic discount percentage(s) (not between 0 and 100).")

# 7. Date Standardization
if 'OrderDate' in df.columns:
    df['OrderDate'] = pd.to_datetime(df['OrderDate'], errors='coerce')
    report.append("- **Date Standardization**: Standardized `OrderDate` to robust datetime format and converted invalid dates (e.g. '32/13/2023') to null.")

# 8. High null columns
null_pcts = df.isna().mean()
cols_to_drop = null_pcts[null_pcts > 0.15].index.tolist()
if cols_to_drop:
    df = df.drop(columns=cols_to_drop)
    report.append(f"- **High Null Drops**: Dropped column(s) `{', '.join(cols_to_drop)}` as they exceeded the 15% missing value threshold.")

# Output
cleaned_path = r'c:\Users\Admin\Documents\AutoBI\Multi-Agent-BI-Engine\cleaned_orders_data.csv'
df.to_csv(cleaned_path, index=False)
print("SUCCESS")
with open(r'c:\Users\Admin\Documents\AutoBI\Multi-Agent-BI-Engine\cleaning_report_output.txt', 'w') as f:
    f.write('\n'.join(report))
