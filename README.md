# Smart Grid Bot v4 — Railway Deployment

## 📊 Backtest Performansı
- **Net Getiri:** +15.0% (ETH -52.6% iken)
- **B&H Üstü Avantaj:** +67.6 puan
- **Sharpe:** 0.825 | **Max DD:** 44.2%

## 🚀 Railway'e Deploy

### 1. Repo hazırla
```bash
git init
git add .
git commit -m "Grid Bot v4"
```

### 2. Railway'e yükle
- railway.app → New Project → Deploy from GitHub
- Ya da: `railway up` (Railway CLI)

### 3. Environment Variables ekle
Railway Dashboard → Variables:
```
BINANCE_API_KEY     = your_key
BINANCE_API_SECRET  = your_secret
TESTNET             = true          # Önce testnet!
AUTO_START          = false         # Dashboard'dan manuel başlat
PORT                = 8080
```
(Diğer değişkenler için .env.example'a bak)

### 4. Dashboard'a eriş
Railway verdiği URL'den dashboard'a gir.

## 📈 Dashboard Özellikleri
- **Canlı Mum Grafikleri** — Binance Futures API'den 15s'de bir güncellenir
- **Grid Çizgileri** — Tüm aktif/bekleyen seviyeleri gösterir
- **EMA20/EMA50** — Trend tespiti için overlay
- **Equity Curve** — Anlık portföy grafiği
- **RSI Mini Chart** — Overbought/oversold göstergesi
- **Trade Log** — Son 200 işlem canlı
- **CSV Export** — `/api/trades/csv` veya dashboard butonu

## 📥 CSV Analizi
Dashboard'daki **⬇ CSV** butonuna bas ya da:
```
GET /api/trades/csv
```
Sütunlar: timestamp, symbol, mode, type, side, price, qty, pnl, fee, portfolio_value, regime

## 🔧 Bot Modları
| Mod | Açıklama | Koşul |
|-----|----------|-------|
| **normal** | Long+Short neutral grid | Varsayılan |
| **short_only** | Agresif short grid (trailing) | Portfolio %20 düşünce |
| **downtrend** | Short ağırlıklı grid | Short-only'den recovery sonrası |

## ⚠️ Önemli
- İlk olarak **TESTNET=true** ile test edin
- Canlıya geçmeden önce backtest sonuçlarını inceleyin
- Railway ücretsiz planda 500 saat/ay limit var → Aylık deployment planlayın

## API Endpoints
| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/` | GET | Dashboard |
| `/api/state` | GET | Bot durumu (JSON) |
| `/api/start` | POST | Bot başlat |
| `/api/stop` | POST | Bot durdur |
| `/api/klines` | GET | Mum verisi (chart için) |
| `/api/trades/csv` | GET | Trade geçmişi CSV |
| `/api/trades/json` | GET | Trade geçmişi JSON |
| `/api/config` | GET/POST | Config oku/güncelle |
| `/health` | GET | Sağlık kontrolü |
