# Google Colab Notebooks

Notebook versions of the screeners and daily report tools, optimised for running in Google Colab (no local Python install needed).

## Notebooks

| File | Description | Open in Colab |
|---|---|---|
| `india_stock_analysis.ipynb` | NSE + BSE stock analysis — Darvas Box, Piotroski, Coffee Can | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_analysis.ipynb) |
| `india_stock_reporting.ipynb` | Indian stock daily report — quote, charts, scans | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/india_stock_reporting.ipynb) |
| `us_market_screener.ipynb` | US market screener — full NASDAQ + NYSE scan in Colab | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/claude-stock-tools/blob/main/03_colab_notebooks/us_market_screener.ipynb) |

## Script

| File | Description |
|---|---|
| `us_stocks_colab_script.py` | Combined Colab-compatible script: per-stock report + batch US screener |

## Usage in Colab

1. Click the "Open in Colab" badge above
2. In Colab: **Runtime → Run all**
3. Enter your ticker or universe when prompted

## Usage locally

```bash
jupyter notebook india_stock_analysis.ipynb
```
