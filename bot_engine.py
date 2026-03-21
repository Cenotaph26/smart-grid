"""
Smart Grid Bot v4 — Engine
Trailing Short Grid + Downtrend Mode
Backtest: +15% vs B&H -52.6% (ETH/USDT Eki2025-Şub2026)
"""
import time, logging, json, csv, os
from datetime import datetime, timezone
from collections import deque
import numpy as np
import pandas as pd
import threading

log = logging.getLogger("GridBot")

# ═══════════════════════════════════════════
# CONFIG  (env var öncelikli, sonra default)
# ═══════════════════════════════════════════
def get_cfg():
    return {
        "API_KEY"          : os.getenv("BINANCE_API_KEY",    "YOUR_API_KEY"),
        "API_SECRET"       : os.getenv("BINANCE_API_SECRET", "YOUR_API_SECRET"),
        "TESTNET"          : os.getenv("TESTNET","true").lower()=="true",
        "SYMBOL"           : os.getenv("SYMBOL","ETHUSDT"),
        "LEVERAGE"         : int(os.getenv("LEVERAGE","2")),
        "CAPITAL_PER_GRID" : float(os.getenv("CAPITAL_PER_GRID","500")),
        "INITIAL_CAPITAL"  : None,   # Binance'den otomatik çekilir
        # V4 optimized params
        "N_GRIDS"          : int(os.getenv("N_GRIDS","15")),
        "ATR_MULT"         : float(os.getenv("ATR_MULT","2.0")),
        "MIN_SPACING"      : float(os.getenv("MIN_SPACING","0.004")),
        "MAX_SPACING"      : float(os.getenv("MAX_SPACING","0.015")),
        "TREND_WINDOW_H"   : int(os.getenv("TREND_WINDOW_H","4")),
        "TREND_THRESH"     : float(os.getenv("TREND_THRESH","0.03")),
        "REBALANCE_H"      : int(os.getenv("REBALANCE_H","6")),
        "SO_REBALANCE_H"   : int(os.getenv("SO_REBALANCE_H","8")),
        "SO_TRAIL_H"       : int(os.getenv("SO_TRAIL_H","24")),
        "PORTFOLIO_SL"     : float(os.getenv("PORTFOLIO_SL","0.20")),
        "SO_EXIT_THRESH"   : float(os.getenv("SO_EXIT_THRESH","0.10")),
        "DT_EXIT_SLOPE"    : float(os.getenv("DT_EXIT_SLOPE","0.04")),
        "FEE_RATE"         : float(os.getenv("FEE_RATE","0.0004")),
        "KLINE_LIMIT"      : int(os.getenv("KLINE_LIMIT","500")),
        "LOOP_SLEEP_SEC"   : int(os.getenv("LOOP_SLEEP_SEC","30")),
        "CSV_PATH"         : os.getenv("CSV_PATH","trades.csv"),
    }

# ═══════════════════════════════════════════
# İNDİKATÖRLER
# ═══════════════════════════════════════════
def ema_last(values, period):
    if len(values) < period:
        return float(np.mean(values))
    k = 2.0 / (period + 1)
    v = float(np.mean(values[:period]))
    for x in values[period:]:
        v = x * k + v * (1 - k)
    return v

def atr_last(highs, lows, closes, period=14):
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i],
                      abs(highs[i]-closes[i-1]),
                      abs(lows[i]-closes[i-1])))
    if not tr: return closes[-1]*0.005
    arr = np.array(tr)
    if len(arr) < period: return float(np.mean(arr))
    a = float(np.mean(arr[:period]))
    for x in arr[period:]: a = (a*(period-1)+x)/period
    return a

def rsi_last(closes, period=14):
    d = np.diff(closes)
    g = d[d>0]; ls = -d[d<0]
    ag = float(np.mean(g[-period:])) if len(g)>=period else (float(np.mean(g)) if len(g) else 0.001)
    al = float(np.mean(ls[-period:])) if len(ls)>=period else (float(np.mean(ls)) if len(ls) else 0.001)
    rs = ag / (al if al > 0 else 0.001)
    return 100 - 100/(1+rs)

# ═══════════════════════════════════════════
# CSV LOGGER
# ═══════════════════════════════════════════
class TradeCSV:
    HEADERS = ["timestamp","symbol","mode","type","side","price",
                "qty","pnl","fee","portfolio_value","regime","note"]

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        if not os.path.exists(path):
            with open(path,"w",newline="") as f:
                csv.writer(f).writerow(self.HEADERS)

    def log(self, **kw):
        row = {h: kw.get(h,"") for h in self.HEADERS}
        row["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with open(self.path,"a",newline="") as f:
                csv.writer(f).writerow([row[h] for h in self.HEADERS])

    def read_all(self):
        try:
            with self._lock:
                return pd.read_csv(self.path).to_dict("records")
        except Exception:
            return []

# ═══════════════════════════════════════════
# SHARED STATE  (dashboard için thread-safe)
# ═══════════════════════════════════════════
class BotState:
    def __init__(self):
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self.running        = False
            self.mode           = "normal"          # normal|short_only|downtrend
            self.regime         = 0                 # -1|0|1
            self.price          = 0.0
            self.portfolio_value= 0.0
            self.initial_capital= 10000.0
            self.portfolio_hi   = 10000.0
            self.sl_level       = 0.0
            self.drawdown_pct   = 0.0
            self.total_pnl      = 0.0
            self.total_fees     = 0.0
            self.total_trades   = 0
            self.n_long_open    = 0
            self.n_short_open   = 0
            self.win_trades     = 0
            self.loss_trades    = 0
            self.grid_levels    = []    # [{side,trig,tp,active,pnl_est}]
            self.open_positions = {}    # {key:{side,qty,entry,tp,upnl}}
            self.equity_history = deque(maxlen=500)   # [(ts, value)]
            self.price_history  = deque(maxlen=500)   # [(ts, o,h,l,c,v)]
            self.trade_history  = deque(maxlen=200)   # son işlemler
            self.monthly_pnl    = {}   # {"2025-11": pnl}
            self.atr            = 0.0
            self.ema20          = 0.0
            self.ema50          = 0.0
            self.rsi            = 50.0
            self.last_update    = ""
            self.error_msg      = ""
            self.uptime_start   = time.time()
            self.rebalance_count= 0
            self.sl_count       = 0
            self.cfg            = {}

    def snapshot(self):
        with self._lock:
            return {
                "running"        : self.running,
                "mode"           : self.mode,
                "regime"         : self.regime,
                "price"          : round(self.price,2),
                "portfolio_value": round(self.portfolio_value,2),
                "initial_capital": round(self.initial_capital,2),
                "drawdown_pct"   : round(self.drawdown_pct,3),
                "total_pnl"      : round(self.total_pnl,2),
                "total_pnl_pct"  : round(self.total_pnl/self.initial_capital*100,2) if self.initial_capital else 0,
                "total_fees"     : round(self.total_fees,2),
                "total_trades"   : self.total_trades,
                "n_long_open"    : self.n_long_open,
                "n_short_open"   : self.n_short_open,
                "win_rate"       : round(self.win_trades/(self.win_trades+self.loss_trades)*100,1)
                                   if (self.win_trades+self.loss_trades)>0 else 0,
                "atr"            : round(self.atr,2),
                "ema20"          : round(self.ema20,2),
                "ema50"          : round(self.ema50,2),
                "rsi"            : round(self.rsi,1),
                "grid_levels"    : list(self.grid_levels),
                "open_positions" : {k:dict(v) for k,v in self.open_positions.items()},
                "equity_history" : list(self.equity_history),
                "price_history"  : list(self.price_history),
                "trade_history"  : list(self.trade_history),
                "monthly_pnl"    : dict(self.monthly_pnl),
                "last_update"    : self.last_update,
                "error_msg"      : self.error_msg,
                "uptime_sec"     : int(time.time()-self.uptime_start),
                "rebalance_count": self.rebalance_count,
                "sl_count"       : self.sl_count,
                "sl_level"       : round(self.sl_level,2),
                "portfolio_hi"   : round(self.portfolio_hi,2),
                "cfg"            : dict(self.cfg),
            }

STATE = BotState()

# ═══════════════════════════════════════════
# ANA BOT
# ═══════════════════════════════════════════
class SmartGridBotV4:
    def __init__(self, cfg: dict):
        self.cfg        = cfg
        self.client     = None
        self.csv        = TradeCSV(cfg["CSV_PATH"])
        self.mode       = "normal"
        # phi ve cash run() başında Binance'den güncellenir
        self.phi        = 10000.0
        self.sl_level   = 0.0
        self.trail_bar  = 0
        self.prev_regime= None
        self.last_reb   = 0
        self.last_sorb  = 0
        self.grid_lv    = []
        self.positions  = {}   # {key:{side,qty,entry,tp}}
        self.cash       = 10000.0
        self.total_fees = 0.0
        self.trades     = 0
        self.rl_pnl     = 0.0
        self.win        = 0
        self.loss       = 0
        self._tick      = 0
        self.monthly    = {}

    def connect(self):
        try:
            from binance.client import Client
            self.client = Client(
                self.cfg["API_KEY"], self.cfg["API_SECRET"],
                testnet=self.cfg["TESTNET"])
            try:
                self.client.futures_change_leverage(
                    symbol=self.cfg["SYMBOL"], leverage=self.cfg["LEVERAGE"])
                self.client.futures_change_margin_type(
                    symbol=self.cfg["SYMBOL"], marginType="ISOLATED")
            except Exception:
                pass
            log.info(f"✅ Bağlandı | {'TESTNET' if self.cfg['TESTNET'] else 'CANLI'}")
            return True
        except Exception as e:
            log.error(f"Bağlantı hatası: {e}")
            return False

    # ── Veri
    def get_klines(self):
        raw = self.client.futures_klines(
            symbol=self.cfg["SYMBOL"],
            interval="1m",
            limit=self.cfg["KLINE_LIMIT"])
        df = pd.DataFrame(raw, columns=[
            "ts","open","high","low","close","vol",
            "cts","qv","n","tbv","tbqv","ign"])
        for c in ["open","high","low","close","vol"]:
            df[c] = df[c].astype(float)
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        return df

    def get_portfolio(self):
        acc = self.client.futures_account()
        return float(acc["totalWalletBalance"])

    # ── İndikatörler
    def calc_indicators(self, df):
        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        return {
            "ema20": ema_last(c, 20),
            "ema50": ema_last(c, 50),
            "atr"  : atr_last(h, l, c, 14),
            "rsi"  : rsi_last(c, 14),
            "slope": (c[-1]-c[-240])/c[-240] if len(c)>=240 else 0,
        }

    # ── Rejim
    def get_regime(self, ind):
        s, r = ind["slope"], ind["rsi"]
        e20, e50 = ind["ema20"], ind["ema50"]
        if s > self.cfg["TREND_THRESH"] and e20 > e50 and r > 52: return  1
        if s < -self.cfg["TREND_THRESH"] and e20 < e50 and r < 48: return -1
        return 0

    def downtrend_ended(self, ind, df):
        c = df["close"].values
        if len(c) < 480: return False
        s4 = (c[-1]-c[-240])/c[-240]
        s8 = (c[-1]-c[-480])/c[-480]
        return (s4 > self.cfg["DT_EXIT_SLOPE"] and s8 > 0.02
                and ind["ema20"] > ind["ema50"] and ind["rsi"] > 55)

    # ── Grid
    def build_grid(self, price, atr, regime, force_short=False, n_mult=1):
        cfg = self.cfg
        sp  = float(np.clip(atr*cfg["ATR_MULT"]/price,
                             cfg["MIN_SPACING"], cfg["MAX_SPACING"]))
        ng  = cfg["N_GRIDS"]
        lvls = []
        uid  = int(time.time()*1000)

        if force_short or regime == -1:
            for i in range(1, ng*n_mult+1):
                trig = price*(1+sp*i)
                tp   = price*(1+sp*(i-1)) if i>1 else price*(1-sp*0.5)
                lvls.append({"side":"short","trig":round(trig,2),
                              "tp":round(tp,2),"sp":sp,
                              "id":f"s{i}_{uid}","active":False})
        elif regime == 1:
            for i in range(1, ng*2+1):
                trig = price*(1-sp*i)
                tp   = price*(1-sp*(i-1)) if i>1 else price*(1+sp*0.5)
                lvls.append({"side":"long","trig":round(trig,2),
                              "tp":round(tp,2),"sp":sp,
                              "id":f"l{i}_{uid}","active":False})
        else:
            for i in range(1, ng+1):
                trig = price*(1-sp*i)
                tp   = price*(1-sp*(i-1)) if i>1 else price
                lvls.append({"side":"long","trig":round(trig,2),
                              "tp":round(tp,2),"sp":sp,
                              "id":f"l{i}_{uid}","active":False})
            for i in range(1, ng+1):
                trig = price*(1+sp*i)
                tp   = price*(1+sp*(i-1)) if i>1 else price
                lvls.append({"side":"short","trig":round(trig,2),
                              "tp":round(tp,2),"sp":sp,
                              "id":f"s{i}_{uid}","active":False})
        return lvls

    # ── Emir
    def place_order(self, side, qty):
        from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
        try:
            return self.client.futures_create_order(
                symbol   = self.cfg["SYMBOL"],
                side     = SIDE_BUY if side=="long" else SIDE_SELL,
                type     = ORDER_TYPE_MARKET,
                quantity = round(qty,3))
        except Exception as e:
            log.error(f"Emir hatası [{side}]: {e}")
            return None

    def close_order(self, side, qty):
        from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
        try:
            return self.client.futures_create_order(
                symbol    = self.cfg["SYMBOL"],
                side      = SIDE_SELL if side=="long" else SIDE_BUY,
                type      = ORDER_TYPE_MARKET,
                quantity  = round(qty,3),
                reduceOnly= True)
        except Exception as e:
            log.error(f"Kapatma hatası: {e}")
            return None

    def close_all(self, price, reason):
        for key in list(self.positions.keys()):
            self._close(key, price, reason)

    def _open(self, key, lvl, price, portfolio_val):
        cfg  = self.cfg
        side = lvl["side"]; trig = lvl["trig"]
        qty  = (cfg["CAPITAL_PER_GRID"]*cfg["LEVERAGE"])/trig
        # Gerçek borsa emri
        self.place_order(side, qty)
        fee  = trig*qty*cfg["FEE_RATE"]
        self.total_fees += fee; self.trades += 1
        self.positions[key] = {"side":side,"qty":qty,"entry":trig,"tp":lvl["tp"]}
        lvl["active"] = True
        # CSV
        self.csv.log(symbol=cfg["SYMBOL"],mode=self.mode,type="OPEN",
                     side=side,price=trig,qty=round(qty,4),pnl=0,
                     fee=round(fee,4),portfolio_value=round(portfolio_val,2))
        # State güncelle
        self._update_state_trade("OPEN", side, trig, qty, 0, fee)
        log.info(f"{'📗' if side=='long' else '📕'} {side.upper()} OPEN  ${trig:.2f}  qty={qty:.4f}")

    def _close(self, key, price, reason):
        if key not in self.positions: return
        p    = self.positions[key]
        qty, ep, side = p["qty"], p["entry"], p["side"]
        cfg  = self.cfg
        pnl  = (price-ep)*qty if side=="long" else (ep-price)*qty
        fee  = price*qty*cfg["FEE_RATE"]
        net  = pnl - fee
        self.rl_pnl    += net
        self.total_fees += fee
        self.trades     += 1
        if net > 0: self.win  += 1
        else:       self.loss += 1
        # Borsa emri
        self.close_order(side, qty)
        # CSV
        self.csv.log(symbol=cfg["SYMBOL"],mode=self.mode,type=reason,
                     side=side,price=round(price,2),qty=round(qty,4),
                     pnl=round(net,4),fee=round(fee,4))
        # Aylık PnL
        mo = datetime.now().strftime("%Y-%m")
        self.monthly[mo] = self.monthly.get(mo,0) + net
        del self.positions[key]
        # Grid level aktifliğini sıfırla
        for lvl in self.grid_lv:
            if lvl.get("id") == key: lvl["active"] = False
        self._update_state_trade(reason, side, price, qty, net, fee)
        log.info(f"{'✅' if net>0 else '❌'} {side.upper()} {reason}  ${price:.2f}  pnl=${net:.2f}")

    def _update_state_trade(self, reason, side, price, qty, pnl, fee):
        with STATE._lock:
            STATE.total_trades = self.trades
            STATE.total_fees   = self.total_fees
            STATE.total_pnl    = self.rl_pnl
            STATE.win_trades   = self.win
            STATE.loss_trades  = self.loss
            STATE.trade_history.appendleft({
                "ts"    : datetime.now().strftime("%H:%M:%S"),
                "type"  : reason,
                "side"  : side,
                "price" : round(price,2),
                "qty"   : round(qty,4),
                "pnl"   : round(pnl,2),
                "fee"   : round(fee,4),
                "mode"  : self.mode,
            })

    # ── Ana tick
    def tick(self, df, portfolio_val):
        cfg      = self.cfg
        price    = float(df["close"].iloc[-1])
        ind      = self.calc_indicators(df)
        self.phi = max(self.phi, portfolio_val)
        dd       = (self.phi - portfolio_val)/self.phi if self.phi > 0 else 0
        self._tick += 1
        RB   = cfg["REBALANCE_H"]*60
        SORB = cfg["SO_REBALANCE_H"]*60
        SOTR = cfg["SO_TRAIL_H"]*60

        # ── Normal → Short-only
        if self.mode == "normal" and dd > cfg["PORTFOLIO_SL"]:
            log.warning(f"⚠️  Portfolio SL! DD={dd:.1%}  Short-Only moda geçiliyor")
            for key in [k for k,p in list(self.positions.items()) if p["side"]=="long"]:
                self._close(key, price, "PORTFOLIO_SL")
            self.mode      = "short_only"
            self.sl_level  = portfolio_val
            self.trail_bar = self._tick
            self.grid_lv   = self.build_grid(price, ind["atr"], -1, force_short=True, n_mult=2)
            self.last_sorb = self._tick
            STATE.sl_count += 1

        elif self.mode == "short_only":
            # Trailing güncelle
            if self._tick - self.trail_bar >= SOTR//cfg["LOOP_SLEEP_SEC"]:
                self.sl_level  = min(self.sl_level, portfolio_val)
                self.trail_bar = self._tick
            # Recovery
            rec = (portfolio_val-self.sl_level)/self.sl_level if self.sl_level>0 else 0
            if rec > cfg["SO_EXIT_THRESH"]:
                log.info(f"✅ Trailing recovery {rec:.1%} → Downtrend moduna geç")
                self.close_all(price, "SO_RECOVERY")
                self.mode = "downtrend"
                self.grid_lv = self.build_grid(price, ind["atr"], -1)
                self.last_reb = self._tick
            # Trailing short rebalans
            elif self._tick - self.last_sorb >= SORB//cfg["LOOP_SLEEP_SEC"]:
                for key in [k for k,p in list(self.positions.items())
                            if p["side"]=="short" and (p["entry"]-price)*p["qty"]>0]:
                    self._close(key, price, "SO_TRAIL_TP")
                self.grid_lv   = self.build_grid(price, ind["atr"], -1, force_short=True, n_mult=2)
                self.last_sorb = self._tick

        elif self.mode == "downtrend":
            if self.downtrend_ended(ind, df):
                log.info("✅ Downtrend bitti → Normal moda dön")
                for key in [k for k,p in list(self.positions.items())
                            if ((price-p["entry"])*p["qty"] if p["side"]=="long"
                                else (p["entry"]-price)*p["qty"]) > 0]:
                    self._close(key, price, "DT_EXIT")
                self.mode = "normal"; self.prev_regime = None; self.grid_lv = []
            elif self._tick - self.last_reb >= RB//cfg["LOOP_SLEEP_SEC"]:
                for key in [k for k,p in list(self.positions.items())
                            if ((price-p["entry"])*p["qty"] if p["side"]=="long"
                                else (p["entry"]-price)*p["qty"]) > 0]:
                    self._close(key, price, "DT_REBALANCE")
                self.grid_lv  = self.build_grid(price, ind["atr"], -1)
                self.last_reb = self._tick

        # Normal mod rebalans
        regime = self.get_regime(ind)
        if self.mode == "normal":
            if regime != self.prev_regime or self._tick-self.last_reb >= RB//cfg["LOOP_SLEEP_SEC"]:
                for key in [k for k,p in list(self.positions.items())
                            if ((price-p["entry"])*p["qty"] if p["side"]=="long"
                                else (p["entry"]-price)*p["qty"]) > 0]:
                    self._close(key, price, "REBALANCE")
                self.grid_lv      = self.build_grid(price, ind["atr"], regime)
                self.prev_regime  = regime
                self.last_reb     = self._tick
                STATE.rebalance_count += 1

        # Grid işlemleri
        for lvl in self.grid_lv:
            key  = lvl["id"]; side = lvl["side"]
            trig = lvl["trig"]; tp  = lvl["tp"]
            if not lvl["active"]:
                if side=="long"  and price<=trig: self._open(key, lvl, price, portfolio_val)
                elif side=="short" and price>=trig: self._open(key, lvl, price, portfolio_val)
            else:
                if key in self.positions:
                    ep = self.positions[key]["entry"]
                    if side=="long"  and price>=tp and tp>ep: self._close(key, price, "TP")
                    elif side=="short" and price<=tp and tp<ep: self._close(key, price, "TP")

        # State güncelle
        n_long  = sum(1 for p in self.positions.values() if p["side"]=="long")
        n_short = sum(1 for p in self.positions.values() if p["side"]=="short")
        upnl_map = {}
        for k, p in self.positions.items():
            upnl = (price-p["entry"])*p["qty"] if p["side"]=="long" else (p["entry"]-price)*p["qty"]
            upnl_map[k] = {**p, "upnl": round(upnl,4)}

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        with STATE._lock:
            STATE.mode           = self.mode
            STATE.regime         = regime
            STATE.price          = price
            STATE.portfolio_value= portfolio_val
            STATE.portfolio_hi   = self.phi
            STATE.sl_level       = self.sl_level
            STATE.drawdown_pct   = dd*100
            STATE.total_pnl      = self.rl_pnl
            STATE.total_fees     = self.total_fees
            STATE.total_trades   = self.trades
            STATE.n_long_open    = n_long
            STATE.n_short_open   = n_short
            STATE.win_trades     = self.win
            STATE.loss_trades    = self.loss
            STATE.atr            = ind["atr"]
            STATE.ema20          = ind["ema20"]
            STATE.ema50          = ind["ema50"]
            STATE.rsi            = ind["rsi"]
            STATE.open_positions = upnl_map
            STATE.grid_levels    = [dict(lv) for lv in self.grid_lv]
            STATE.monthly_pnl    = dict(self.monthly)
            STATE.last_update    = now_str
            STATE.error_msg      = ""
            ts = int(time.time()*1000)
            STATE.equity_history.append([ts, round(portfolio_val,2)])
            row = df.iloc[-1]
            STATE.price_history.append([
                ts,
                round(float(row["open"]),2), round(float(row["high"]),2),
                round(float(row["low"]),2),  round(float(row["close"]),2),
                round(float(row["vol"]),2),
            ])

    # ── Run Loop
    def run(self):
        cfg = self.cfg
        STATE.reset()
        STATE.running = True
        STATE.cfg     = cfg

        if not self.connect():
            STATE.error_msg = "Binance bağlantısı başarısız"
            STATE.running   = False
            return

        # ★ Başlangıç sermayesini Binance'den çek
        try:
            real_capital = self.get_portfolio()
            cfg["INITIAL_CAPITAL"] = real_capital
            self.phi  = real_capital
            self.cash = real_capital
            log.info(f"💰 Başlangıç sermayesi Binance'den alındı: ${real_capital:.2f}")
        except Exception as e:
            log.error(f"Sermaye çekme hatası: {e} — varsayılan $10000 kullanılıyor")
            cfg["INITIAL_CAPITAL"] = 10000.0
            self.phi  = 10000.0
            self.cash = 10000.0

        STATE.initial_capital = cfg["INITIAL_CAPITAL"]
        STATE.portfolio_hi    = cfg["INITIAL_CAPITAL"]
        log.info("🚀 Grid Bot v4 başlatıldı")
        while STATE.running:
            try:
                df  = self.get_klines()
                pv  = self.get_portfolio()
                self.tick(df, pv)
            except Exception as e:
                log.error(f"Tick hatası: {e}", exc_info=True)
                with STATE._lock: STATE.error_msg = str(e)
            time.sleep(cfg["LOOP_SLEEP_SEC"])

def start_bot():
    cfg = get_cfg()
    bot = SmartGridBotV4(cfg)
    t = threading.Thread(target=bot.run, daemon=True)
    t.start()
    return bot
