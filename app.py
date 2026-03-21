"""
Flask API — Grid Bot Dashboard Backend
"""
import os, json, io, csv, threading
from datetime import datetime
from flask import Flask, jsonify, render_template, send_file, request, Response
from bot_engine import STATE, start_bot, get_cfg, TradeCSV

app = Flask(__name__)
_bot_instance = None
_bot_lock     = threading.Lock()

# ═══════════ API ROUTES ═══════════════

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/state")
def api_state():
    return jsonify(STATE.snapshot())

@app.route("/api/start", methods=["POST"])
def api_start():
    global _bot_instance
    with _bot_lock:
        if STATE.running:
            return jsonify({"ok": False, "msg": "Bot zaten çalışıyor"})
        _bot_instance = start_bot()
    return jsonify({"ok": True, "msg": "Bot başlatıldı"})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    with STATE._lock:
        STATE.running = False
    return jsonify({"ok": True, "msg": "Bot durduruldu"})

@app.route("/api/klines")
def api_klines():
    """Binance'den doğrudan kline çek (TradingView lightweight charts için)"""
    import urllib.request
    symbol   = request.args.get("symbol", "ETHUSDT")
    interval = request.args.get("interval", "1m")
    limit    = request.args.get("limit", "300")
    testnet  = os.getenv("TESTNET","true").lower()=="true"
    base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    url  = f"{base}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            raw = json.loads(r.read())
        candles = [{"time": int(c[0])//1000,
                    "open": float(c[1]),"high": float(c[2]),
                    "low" : float(c[3]),"close":float(c[4]),
                    "volume":float(c[5])} for c in raw]
        return jsonify(candles)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/trades/csv")
def api_trades_csv():
    """CSV dosyasını indir"""
    cfg  = get_cfg()
    path = cfg["CSV_PATH"]
    if not os.path.exists(path):
        return Response("Henüz işlem yok", mimetype="text/plain")
    return send_file(path, mimetype="text/csv",
                     as_attachment=True,
                     download_name=f"grid_trades_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")

@app.route("/api/trades/json")
def api_trades_json():
    cfg  = get_cfg()
    tc   = TradeCSV(cfg["CSV_PATH"])
    rows = tc.read_all()
    return jsonify(rows)

@app.route("/api/config", methods=["GET","POST"])
def api_config():
    if request.method == "POST":
        # Env override — sadece runtime için
        data = request.json or {}
        allowed = ["N_GRIDS","ATR_MULT","CAPITAL_PER_GRID","LEVERAGE",
                   "PORTFOLIO_SL","SO_EXIT_THRESH","REBALANCE_H","SO_REBALANCE_H"]
        for k, v in data.items():
            if k in allowed:
                os.environ[k] = str(v)
        return jsonify({"ok": True})
    return jsonify(get_cfg())

@app.route("/health")
def health():
    return jsonify({"status":"ok","running":STATE.running})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    # Railway'de bot otomatik başlasın
    if os.getenv("AUTO_START","false").lower()=="true":
        _bot_instance = start_bot()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
