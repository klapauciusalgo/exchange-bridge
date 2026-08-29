# PRD — Crypto Historical Pattern Discovery & Recommendation Engine

**Version:** 1.0  
**Date:** 29 August 2026  
**Status:** Draft / MVP Design

---

## 1. Product Overview

### 1.1 Product Name

**Crypto Pattern Discovery Engine (CPDE)**

### 1.2 Product Vision

CPDE adalah platform analitik crypto yang mencari aset dengan **market state saat ini yang secara historis mirip dengan kondisi sebelum significant price movement** pada aset lain.

Produk tidak bertujuan memberikan kepastian bahwa suatu coin akan naik. Fokusnya adalah:

> **Find the next setup, not the next pump.**

Sistem menggabungkan:
- Technical analysis
- Historical pattern matching
- Similarity search
- Statistical outcome analysis
- Market regime analysis
- Ranking/scoring

### 1.3 Core Use Case

User melihat daftar crypto yang sedang mengalami kenaikan signifikan dalam periode tertentu.

Contoh:

```text
Top 24H Movers

SUI     +14.2%
HYPE    +12.8%
TAO     +10.7%
SEI      +8.9%
```

Sistem mengambil kondisi **sebelum significant move** dari aset tersebut, kemudian mencari aset lain yang **saat ini memiliki kondisi serupa tetapi belum mengalami move yang sama**.

Output:

```text
Potential Similar Setups

SEI     Similarity 93
TIA     Similarity 89
APT     Similarity 86
ARB     Similarity 82
```

Setiap kandidat harus disertai:
- Similarity score
- Historical matches
- Historical outcome
- Risk indicators
- Explanation / reason
- Chart comparison

---

# 2. Problem Statement

Crypto market memiliki ribuan aset dan bergerak 24/7. Trader sulit secara manual:

1. Memantau seluruh market.
2. Mengidentifikasi setup teknikal yang serupa.
3. Membandingkan kondisi saat ini dengan historical setup.
4. Menilai apakah pola tersebut memiliki statistical edge.
5. Menghindari bias karena hanya melihat coin yang sudah berhasil naik.

Existing screeners umumnya fokus pada:
- Price change
- RSI
- Volume
- Moving average
- Technical indicators

CPDE mencoba menjawab pertanyaan yang lebih spesifik:

> **"Coin mana yang sekarang berada dalam market state yang mirip dengan historical setup yang sebelumnya menghasilkan significant move?"**

---

# 3. Product Goals

## 3.1 Primary Goals

### Goal 1 — Pattern Discovery

Menemukan historical market states yang mirip dengan kondisi crypto saat ini.

### Goal 2 — Pre-Move Detection

Mengidentifikasi current setups yang menyerupai kondisi sebelum historical significant move.

### Goal 3 — Statistical Validation

Mengukur outcome dari historical setups yang serupa.

### Goal 4 — Explainable Recommendation

Setiap rekomendasi harus dapat menjawab:

> "Kenapa coin ini direkomendasikan?"

### Goal 5 — Bias-Resistant Backtesting

Sistem harus dirancang untuk meminimalkan:
- Look-ahead bias
- Survivorship bias
- Data leakage
- Overfitting

---

# 4. Non-Goals

MVP tidak bertujuan untuk:

- Menjamin profit.
- Memberikan financial advice.
- Melakukan auto-trading.
- Memprediksi harga secara deterministic.
- Menentukan entry/exit secara otomatis.
- Menggunakan LLM sebagai primary prediction model.

LLM/AI generative layer hanya digunakan jika diperlukan untuk:
- Explanation
- Natural-language summary
- Pattern interpretation

---

# 5. Target Users

## Primary User

Crypto trader / quantitative trader yang membutuhkan:

- Market scanner
- Pattern discovery
- Historical setup comparison
- Statistical edge discovery

## Secondary User

Quant researcher yang ingin:
- Membuat hypothesis
- Menguji pattern
- Membandingkan historical outcomes
- Mengeksplorasi market regimes

---

# 6. Product Concept

## 6.1 Core Pipeline

```text
Crypto Market Data
        |
        v
Data Normalization
        |
        v
Feature Engineering
        |
        v
Market State Vector
        |
        +----------------------+
        |                      |
        v                      v
Historical Pattern DB    Current Market
        |                      |
        +----------+-----------+
                   |
                   v
            Similarity Engine
                   |
                   v
            Candidate Filter
                   |
                   v
          Outcome / Probability
                   |
                   v
             Ranking Engine
                   |
                   v
          Recommendation UI
```

---

# 7. Functional Requirements

## FR-01 Market Scanner

System dapat menampilkan crypto berdasarkan:

- 24H return
- 6H return
- 12H return
- 1H return
- Volume change
- Volatility
- Market cap
- Liquidity

User dapat menentukan threshold.

Example:

```text
24H Return > +8%
Volume > $10M
Exclude BTC/ETH
```

---

## FR-02 Significant Move Detection

Sistem harus menentukan apakah suatu historical movement memenuhi kriteria significant move.

Contoh MVP:

```text
Forward Return 24H >= +10%
```

Configurable:

```text
horizon:
    1H
    4H
    12H
    24H
    48H
    72H

threshold:
    +5%
    +10%
    +20%
```

Lebih advanced:

```text
Risk-adjusted move
=
Forward Return / Historical Volatility
```

---

# 8. Historical Pattern Construction

## 8.1 Lookback Window

Default:

```text
100 candles
```

Namun sistem harus configurable:

```text
50
100
200
500
```

Timeframes:

```text
5m
15m
1h
4h
1d
```

MVP recommendation:

> **1H timeframe + 100 candles**

Artinya sistem menggunakan sekitar 100 jam historical context.

---

# 9. Feature Engineering

Raw OHLCV tidak boleh menjadi satu-satunya input.

Feature layer dibagi menjadi beberapa kelompok.

## 9.1 Price Structure

- Normalized close
- Returns
- Log returns
- Higher high
- Higher low
- Lower high
- Lower low
- Distance from local high
- Distance from local low
- Price position in range

## 9.2 Trend

- EMA 20
- EMA 50
- EMA 100
- SMA 20
- SMA 50
- SMA 200
- EMA slope
- Trend strength

## 9.3 Momentum

- RSI
- ROC
- MACD
- Stochastic
- Momentum acceleration

## 9.4 Volatility

- ATR
- ATR %
- Rolling standard deviation
- Bollinger Band width
- Volatility contraction
- Volatility expansion

## 9.5 Volume

- Volume / average volume
- Volume trend
- Volume acceleration
- Relative volume
- OBV
- Volume-price divergence

## 9.6 Market Structure

- Consolidation duration
- Breakout attempts
- Range compression
- Support distance
- Resistance distance
- Swing structure
- Drawdown from local high

## 9.7 Derivatives Features

If exchange data is available:

- Open Interest
- OI change
- Funding rate
- Funding acceleration
- Long/short ratio
- Liquidation volume

These should be optional because not all assets/exchanges provide equivalent data quality.

---

# 10. Market Regime Features

A pattern should not be interpreted independently from the broader market.

System should calculate:

```text
BTC regime
ETH regime
Total market regime
Sector regime
Market volatility regime
```

Example:

```text
BTC Trend       = Bullish
BTC Volatility  = Low
Altcoin Regime  = Expansion
Market Breadth  = Increasing
```

A candidate pattern occurring during a bullish BTC regime should not automatically be treated as equivalent to the same pattern during a bearish BTC regime.

---

# 11. Market State Vector

Each timestamp for each asset becomes a feature vector.

Example:

```text
Asset: SUI
Timestamp: T-100

Market State:

trend_score             = 0.72
momentum_score          = 0.61
volume_score            = 0.83
volatility_score        = 0.27
consolidation_score     = 0.91
breakout_pressure       = 0.76
oi_score                = 0.68
btc_regime              = 0.74
```

The vector is stored for historical search.

---

# 12. Pattern Representation

MVP should support two representations.

## 12.1 Feature-Based Representation

```text
[RSI,
 ATR,
 VolumeRatio,
 EMA_Slope,
 BBWidth,
 OIChange,
 ...]
```

Advantages:
- Explainable
- Easy to backtest
- Easy to debug

## 12.2 Sequence Representation

Represent the last 100 candles as normalized sequence:

```text
Candle 1
Candle 2
...
Candle 100
```

Normalized features:

```text
Open
High
Low
Close
Volume
```

Example normalization:

```text
price / first_close - 1
volume / rolling_average_volume
```

This prevents absolute price differences from dominating similarity.

---

# 13. Similarity Engine

## 13.1 Initial MVP

Use weighted feature similarity.

Example:

```text
Similarity Score =

30% Price Structure
20% Momentum
15% Volume
15% Volatility
10% Trend
10% Market Regime
```

Similarity algorithms:

- Cosine similarity
- Euclidean distance
- Manhattan distance

Feature scaling is mandatory.

---

# 14. Advanced Similarity

After MVP validation, introduce:

### Dynamic Time Warping (DTW)

Useful when two patterns have similar shapes but develop at different speeds.

Example:

```text
Pattern A:

__/\___/\____/\/\____

Pattern B:

____/\____/\__/\/\__
```

The shapes may be similar despite timing differences.

### Embedding-Based Similarity

A trained model can transform the 100-candle sequence into:

```text
Pattern Embedding
[0.21, -0.72, 0.44, ...]
```

Then vector similarity can be performed using:

- pgvector
- Qdrant
- Pinecone
- Milvus

MVP recommendation:

> Start with engineered features + pgvector.

Do not start with deep learning.

---

# 15. Historical Outcome Engine

This is the most important component.

For every historical market state:

```text
Timestamp T
       |
       v
Feature Vector
       |
       +----> Forward 1H Return
       +----> Forward 4H Return
       +----> Forward 12H Return
       +----> Forward 24H Return
       +----> Forward 48H Return
       +----> Maximum Favorable Excursion
       +----> Maximum Adverse Excursion
```

Example:

```text
Pattern ID: P123

Historical Matches: 347

24H Outcome:

Median Return       +4.8%
Mean Return         +7.1%
Win Rate             63%
P(+5%)               41%
P(+10%)              22%
P(-5%)               14%
Max Drawdown Median  -2.3%
```

---

# 16. Candidate Filtering

After similarity search, remove candidates that already made the move.

Example:

```text
Candidate:
Similarity = 94%

Current 24H Return = +18%
```

If the target is to find pre-move setups:

```text
Reject
```

Configurable condition:

```text
Current return < threshold

Example:
Current 24H return < +5%
```

Also filter:

- Minimum liquidity
- Minimum trading volume
- Stable data availability
- Excluded assets
- Stablecoins
- Wrapped assets
- Extremely illiquid tokens

---

# 17. Recommendation Ranking

Recommendation score should not equal similarity.

Example:

```text
Recommendation Score =

35% Historical Similarity
25% Historical Outcome Quality
15% Sample Size Confidence
10% Current Market Alignment
10% Liquidity
5% Risk Score
```

Example:

```text
Coin A

Similarity             93
Historical Win Rate     68%
Sample Size            184
Expected 24H Return    +5.7%
Liquidity Score         91
Risk Score              22

Final Score             89
```

---

# 18. Confidence Score

Confidence should be separated from recommendation score.

Example:

```text
Confidence:

Sample Size             184
Similarity              93%
Outcome consistency      High
Market regime match      Yes

Confidence = High
```

A coin with:

```text
Similarity = 97%
Sample Size = 4
```

should **not** be treated as highly reliable.

---

# 19. Regime-Aware Matching

Historical matches should preferably come from similar market regimes.

Example:

Current:

```text
BTC = Bullish
BTC volatility = Low
Altcoin breadth = High
```

Prefer historical matches with:

```text
BTC = Bullish
BTC volatility = Low
Altcoin breadth = High
```

This can substantially improve pattern relevance.

---

# 20. Avoiding Statistical Bias

## 20.1 Look-Ahead Bias

At timestamp T, the model may only use:

```text
Data <= T
```

Never:

```text
Data > T
```

Historical outcome is used only as the label.

---

## 20.2 Survivorship Bias

Do not only use currently listed coins.

Historical universe must include:

- Delisted coins
- Failed projects
- Dead tokens
- Historical exchange listings

Where data availability permits.

---

## 20.3 Data Leakage

Feature generation must happen independently at each timestamp.

Example:

Bad:

```text
Normalize using entire dataset
```

Good:

```text
Normalize using information available at T
```

---

## 20.4 Overfitting

Avoid tuning weights exclusively on one historical period.

Use:

```text
Training Period
Validation Period
Out-of-Sample Test
```

---

# 21. Backtesting Framework

## Walk-Forward Testing

Example:

```text
2019 ───── 2022
        Training

2023 ───── 2024
        Validation

2025 ───── 2026
        Out-of-Sample
```

Then rolling:

```text
Train → Validate → Test
Train → Validate → Test
Train → Validate → Test
```

This better represents live deployment.

---

# 22. Evaluation Metrics

Do not evaluate only accuracy.

Primary metrics:

### Ranking Quality

- Precision@5
- Precision@10
- Recall@10
- NDCG

### Trading-Relevant

- Average forward return
- Median forward return
- Win rate
- Profit factor
- Maximum drawdown
- Sharpe ratio
- Sortino ratio
- Hit rate at +5%
- Hit rate at +10%

### Stability

Measure performance across:

- Bull market
- Bear market
- Sideways market
- High volatility
- Low volatility

---

# 23. UI / UX

## 23.1 Dashboard

```text
---------------------------------------------------
Crypto Pattern Discovery
---------------------------------------------------

Market Regime
BTC      +2.1%   Bullish
ETH      +1.8%   Bullish
Breadth  64%     Positive

---------------------------------------------------
Recent Significant Movers
---------------------------------------------------

SUI       +14.2%
HYPE      +12.8%
TAO       +10.7%

[Find Similar Setups]

---------------------------------------------------
Potential Setups
---------------------------------------------------

SEI       Score 93
TIA       Score 89
APT       Score 86
ARB       Score 82
```

---

# 24. Candidate Detail Page

Example:

```text
SEI

Recommendation Score       93
Pattern Similarity          95
Historical Confidence       88

Current Return
1H          +0.4%
6H          +1.1%
24H         +2.0%

----------------------------------
Why is SEI recommended?
----------------------------------

✓ Price structure      94%
✓ Volume structure     91%
✓ Momentum             89%
✓ Volatility           92%
✓ Market regime        87%

----------------------------------
Historical Matches
----------------------------------

Matches                 127

Median 24H Return       +6.2%
Win Rate                 67%
P(+5%)                   48%
P(+10%)                  23%
P(-5%)                   12%
```

---

# 25. Chart Comparison

Core visualization:

```text
Historical Pattern
        vs
Current Pattern
```

Example:

```text
Historical SUI
T-100 ---------------- T-1
                  ╭──────╮
             ╭────╯      ╰──🚀

Current SEI
T-100 ---------------- NOW
                  ╭──────╮
             ╭────╯      ╰──?
```

User should be able to:

- Overlay charts
- Change timeframe
- Change lookback
- Inspect individual candles
- View indicator layers
- View matched historical examples

---

# 26. Explainability

Every recommendation must show:

### Similarity

```text
Why similar?

Price Structure       95%
Momentum               89%
Volume                 92%
Volatility             91%
```

### Historical Evidence

```text
127 similar historical cases

67% positive after 24H
48% exceeded +5%
23% exceeded +10%
12% fell below -5%
```

### Risk

```text
High funding
Low liquidity
BTC bearish
```

The system should be able to say:

> "Similarity is high, but confidence is reduced because the current BTC regime differs from most historical matches."

---

# 27. Data Architecture

```text
                  Exchange APIs
                       |
             +---------+---------+
             |                   |
           OHLCV            Derivatives
             |                   |
             +---------+---------+
                       |
                       v
                Data Ingestion
                       |
                       v
                 Data Storage
                       |
              +--------+--------+
              |                 |
          Raw Data         Feature Store
              |                 |
              +--------+--------+
                       |
                       v
              Pattern Generator
                       |
                       v
             Historical Pattern DB
                       |
                       v
                Similarity Engine
                       |
                       v
               Ranking Engine
                       |
                       v
                    API
                       |
                       v
                     Web UI
```

---

# 28. Suggested Technology Stack

## Backend

- Python
- FastAPI
- Pandas
- NumPy
- scikit-learn

## Database

- PostgreSQL
- TimescaleDB optional
- pgvector

## Data Processing

MVP:

- Python workers
- Cron / scheduler

Scale:

- Redis
- Celery / RQ
- Kafka optional

## Frontend

- Next.js
- React
- TradingView Lightweight Charts

## Infrastructure

Initial:

```text
VPS / Cloud VM
+
PostgreSQL
+
Object Storage
```

Later:

```text
Cloud Run / Kubernetes
Managed PostgreSQL
Object Storage
Queue
Workers
```

---

# 29. Suggested Database Schema

## assets

```sql
id
symbol
exchange
base_asset
quote_asset
sector
listing_date
delisting_date
status
```

## candles

```sql
asset_id
timestamp
timeframe
open
high
low
close
volume
```

## technical_features

```sql
asset_id
timestamp
timeframe
rsi
atr
ema20
ema50
ema200
bb_width
volume_ratio
momentum
trend_score
volatility_score
```

## market_features

```sql
timestamp
btc_return
btc_regime
eth_return
market_breadth
market_volatility
altcoin_regime
```

## pattern_snapshots

```sql
id
asset_id
timestamp
timeframe
lookback
feature_vector
embedding
```

## outcomes

```sql
pattern_id
forward_1h_return
forward_4h_return
forward_12h_return
forward_24h_return
forward_48h_return
max_favorable_excursion
max_adverse_excursion
```

## recommendations

```sql
timestamp
source_asset
candidate_asset
similarity_score
outcome_score
confidence_score
risk_score
final_score
```

---

# 30. API Design

## GET /market/movers

Returns current top movers.

## GET /patterns/{asset}

Returns historical pattern representation.

## GET /recommendations

Parameters:

```text
timeframe
lookback
min_similarity
max_current_return
min_volume
horizon
```

## GET /recommendations/{asset}

Returns candidate assets similar to the selected asset.

## GET /patterns/{id}/matches

Returns historical matches.

## GET /patterns/{id}/outcomes

Returns historical statistical outcomes.

---

# 31. MVP Scope

The MVP should deliberately remain simple.

## Included

### Data

- OHLCV
- Top liquid crypto assets
- 1H timeframe

### Lookback

- 100 candles

### Features

- Price structure
- RSI
- EMA
- ATR
- Bollinger Band
- Volume
- Momentum
- Volatility

### Similarity

- Standardized features
- Cosine similarity
- Weighted score

### Outcome

- 24H forward return
- Win rate
- Median return
- P(+5%)
- P(-5%)

### UI

- Market movers
- Similar setup scanner
- Candidate ranking
- Historical chart comparison
- Explanation

---

# 32. Phase 2

Add:

- Multi-timeframe analysis
- BTC regime
- ETH regime
- Market breadth
- Open Interest
- Funding
- Liquidations
- DTW
- More sophisticated ranking
- Walk-forward backtesting UI

---

# 33. Phase 3

Add machine learning.

Potential models:

- XGBoost
- LightGBM
- Random Forest
- Logistic Regression
- Temporal CNN
- Transformer / Time-Series Transformer

Important principle:

> ML should improve ranking/probability estimation, not replace the explainable pattern engine.

---

# 34. Phase 4 — Live Trading Integration

Only after sufficient out-of-sample validation.

Potential integration:

```text
Recommendation
      |
      v
Signal
      |
      v
Risk Engine
      |
      v
Portfolio Engine
      |
      v
Exchange API
```

Auto-trading must be a separate module from the recommendation engine.

---

# 35. Example End-to-End Scenario

At:

```text
2026-08-29 14:00
```

System detects:

```text
SUI +13.4% / 24H
```

It identifies:

```text
Pump Start ≈ T-18
```

System extracts SUI's pre-move state:

```text
100 candles
↓
Feature Vector
↓
Historical Pattern
```

Then scans current market:

```text
1000 assets
↓
Liquidity Filter
↓
Already-Pumped Filter
↓
Similarity Search
```

Results:

```text
SEI    94%
TIA    91%
APT    87%
ARB    83%
```

Historical analysis:

```text
SEI

Similar historical patterns: 147
Median 24H return:             +5.9%
Win rate:                       66%
P(+5%):                         47%
P(+10%):                        21%
P(-5%):                         13%
```

Final:

```text
SEI

Similarity        94
Outcome Score     86
Confidence        89
Risk Score        21

Final Score       90
```

System displays:

> **SEI is currently exhibiting a market structure similar to SUI's pre-move state. Historical matches show a 66% positive 24H outcome rate. Current setup has not yet experienced a comparable move.**

This is a **research signal**, not a guaranteed prediction.

---

# 36. Critical Product Principle

The system should never optimize for:

> "How accurately can we predict the next pump?"

Instead optimize for:

> **"Can we consistently identify market states with measurable positive conditional expectancy?"**

This changes the product from a speculative predictor into a quantitative research engine.

---

# 37. Success Criteria

MVP is considered successful if:

1. Similarity engine produces intuitively relevant matches.
2. Historical pattern results are reproducible.
3. Out-of-sample performance exceeds baseline screening methods.
4. Results remain meaningful across multiple market regimes.
5. Recommendation ranking demonstrates measurable statistical edge.
6. No major look-ahead or survivorship bias is detected.
7. Users can understand why a candidate was recommended.

Example benchmark:

```text
Baseline:
Random top-100 liquid coins

vs

CPDE:
Top-10 recommended setups
```

Compare:

```text
24H Median Return
Win Rate
Max Drawdown
Sharpe
Precision@10
```

---

# 38. Recommended Development Roadmap

## Sprint 1 — Data Foundation

- Exchange integration
- OHLCV ingestion
- Asset normalization
- Historical storage

## Sprint 2 — Feature Engine

- Technical indicators
- Market structure
- Feature normalization
- Feature validation

## Sprint 3 — Historical Pattern Engine

- Significant move labeling
- Pre-move snapshot generation
- Forward outcome calculation
- Pattern database

## Sprint 4 — Similarity Engine

- Cosine similarity
- Weighted scoring
- Candidate filtering
- Ranking

## Sprint 5 — Backtesting

- Walk-forward framework
- Bias detection
- Performance metrics
- Baseline comparison

## Sprint 6 — UI

- Market scanner
- Candidate list
- Pattern comparison
- Historical outcomes
- Explainability

## Sprint 7 — Optimization

- Multi-timeframe
- Market regime
- DTW
- Advanced ranking

---

# 39. Future Research Direction

Potential evolution:

```text
Rule-Based
     ↓
Feature Similarity
     ↓
Historical Pattern Matching
     ↓
Statistical Ranking
     ↓
ML Probability Model
     ↓
Regime-Aware Model
     ↓
Portfolio-Level Signal
```

The most valuable proprietary asset will likely not be the UI or indicator formulas.

It will be:

> **The historical pattern → market outcome dataset and the methodology used to construct it without leakage.**

That dataset can become the foundation for increasingly sophisticated models.

---

# 40. Final Product Definition

CPDE can be summarized as:

```text
CURRENT MARKET
      |
      v
"What does this market state look like?"
      |
      v
HISTORICAL PATTERN SEARCH
      |
      v
"What happened after similar states?"
      |
      v
STATISTICAL VALIDATION
      |
      v
"Which current assets have the strongest
historical similarity + expectancy?"
      |
      v
RANKED OPPORTUNITIES
```

The product should therefore position itself as:

> **A historical market-pattern discovery and statistical ranking engine for crypto.**

Not:

> "AI that predicts which coin will pump."
