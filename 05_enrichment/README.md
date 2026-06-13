# Damodaran Enrichment

Adds sector benchmarks and country risk data from [Aswath Damodaran's NYU Stern dataset archive](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html) to any scanner's triple-hits output.

## What it adds

| Column | Source | Meaning |
|---|---|---|
| `Sector_PE_Trailing` | PE Ratios by Sector | Median trailing PE for this sector |
| `Sector_PE_Fwd` | PE Ratios by Sector | Median forward PE for this sector |
| `PE_vs_Sector` | Computed | Stock PE ÷ sector median (>1 = premium) |
| `Sector_EV_EBITDA` | EV/EBITDA by Sector | Median EV/EBITDA for this sector |
| `Sector_EV_EBIT` | EV/EBITDA by Sector | Median EV/EBIT for this sector |
| `Sector_ROE` | P/B and ROE by Sector | Sector average ROE |
| `Sector_ROIC` | P/B and ROE by Sector | Sector average ROIC |
| `Sector_Net_Margin` | Margins by Sector | Sector median net margin |
| `Sector_EBITDA_Margin` | Margins by Sector | Sector median EBITDA margin |
| `Country_ERP_pct` | Country Risk Premiums | Total equity risk premium (%) for this country |
| `Country_CRP_pct` | Country Risk Premiums | Country risk premium over mature market |

Data is cached locally for 24 hours to avoid repeated downloads.

## Usage — standalone

Enrich an existing scanner Excel output:

```bash
python 05_enrichment/damodaran_enrichment.py \
    --file Downloads/europe_scan_2026-06-13.xlsx \
    --sheet "Triple Hits" \
    --market europe \
    --sector-col Sector \
    --country-col Country \
    --pe-col PE
```

Preview what's in the datasets without enriching a file:

```bash
python 05_enrichment/damodaran_enrichment.py --market global --preview
```

## Usage — import in a scanner

```python
from damodaran_enrichment import enrich_triple_hits

# df is the triple-hits DataFrame from your scanner
df_enriched = enrich_triple_hits(
    df,
    market="europe",       # us | europe | japan | india | emerging | global
    sector_col="Sector",
    country_col="Country",
    pe_col="PE",           # optional: stock's trailing PE column
)
```

## Markets

| Market arg | Damodaran region | ERP source |
|---|---|---|
| `us` | United States | Country risk: USA row |
| `europe` | Europe | Country risk: by country |
| `japan` | Japan | Country risk: Japan row |
| `india` | India | Country risk: India row |
| `emerging` | Emerging Markets | Country risk: by country |
| `global` | Global (default) | All countries |
