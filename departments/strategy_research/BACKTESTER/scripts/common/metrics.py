"""
Standard metrics calculator for all BACKTESTER test runs.
All 18 required metrics + additional helpers.
"""
import numpy as np
import pandas as pd


def compute_metrics(equity_curve: pd.Series, trade_log: list = None, initial_capital: float = 1000.0,
                    risk_free_rate: float = 0.0) -> dict:
    """
    Compute standard metrics from an equity curve (timestamp-indexed Series of equity values).
    Optionally pass a trade_log list of dicts with keys: pnl_usd, duration_hours, entry_time, exit_time.
    """
    equity = pd.Series(equity_curve).dropna()
    if len(equity) < 2:
        return {k: 0.0 for k in _METRIC_KEYS}

    returns = equity.pct_change().dropna()
    if len(returns) == 0:
        return {k: 0.0 for k in _METRIC_KEYS}

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 24 * 3600), 1e-9)
    if equity.iloc[-1] <= 0:
        cagr = -1.0
    else:
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1

    # Drawdown
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = abs(dd.min())

    # Sharpe (annualised)
    mean_ret = returns.mean()
    std_ret = returns.std()
    sharpe = 0.0
    if std_ret > 1e-12:
        # Annualise: assumes daily bars. For intraday, adjust by sqrt(periods_per_year)
        # Use bar count to estimate periods per year
        bars_per_year = len(returns) / years
        sharpe = (mean_ret - risk_free_rate / bars_per_year) / std_ret * np.sqrt(bars_per_year)

    # Sortino
    downside = returns[returns < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-12
    sortino = (mean_ret * np.sqrt(bars_per_year)) / downside_std if bars_per_year else 0.0

    # Calmar
    calmar = cagr / max_dd if max_dd > 1e-12 else 0.0

    # Trade-level metrics
    if trade_log and len(trade_log) > 0:
        pnls = np.array([t.get("pnl_usd", 0) for t in trade_log])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        total_trades = len(pnls)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        gross_profit = wins.sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses.sum()) if len(losses) > 0 else 1e-12
        profit_factor = gross_profit / gross_loss
        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
        best_trade = wins.max() if len(wins) > 0 else 0.0
        worst_trade = losses.min() if len(losses) > 0 else 0.0
        avg_duration = np.mean([t.get("duration_hours", 0) for t in trade_log])
        # Expectancy
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        # SQN
        if total_trades > 1 and pnls.std() > 1e-12:
            sqn = (expectancy / pnls.std()) * np.sqrt(total_trades)
        else:
            sqn = 0.0
    else:
        total_trades = 0
        win_rate = 0.0
        profit_factor = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        best_trade = 0.0
        worst_trade = 0.0
        avg_duration = 0.0
        expectancy = 0.0
        sqn = 0.0

    # Exposure time (% of bars in position)
    # If no trade_log, assume 0
    exposure_time = 0.0
    if trade_log and total_trades > 0 and len(equity) > 0:
        # Sum all trade durations in hours, divide by total time span in hours
        total_dur = sum(t.get("duration_hours", 0) for t in trade_log)
        total_span = max((equity.index[-1] - equity.index[0]).total_seconds() / 3600, 1)
        exposure_time = min(total_dur / total_span * 100, 100.0)

    # PnL
    pnl_usd = equity.iloc[-1] - initial_capital
    pnl_pct = total_return * 100

    return {
        "profit_factor": round(profit_factor, 4),
        "sharpe_ratio": round(sharpe, 4),
        "calmar_ratio": round(calmar, 4),
        "cagr": round(cagr, 4),
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 4),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 4),
        "max_drawdown": round(max_dd, 4),
        "total_trades": int(total_trades),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "best_trade": round(best_trade, 4),
        "worst_trade": round(worst_trade, 4),
        "avg_duration_hours": round(avg_duration, 2),
        "exposure_time_pct": round(exposure_time, 2),
        "sortino_ratio": round(sortino, 4),
        "sqn": round(sqn, 4),
    }


_METRIC_KEYS = [
    "profit_factor", "sharpe_ratio", "calmar_ratio", "cagr", "win_rate",
    "expectancy", "pnl_usd", "pnl_pct", "max_drawdown", "total_trades",
    "avg_win", "avg_loss", "best_trade", "worst_trade",
    "avg_duration_hours", "exposure_time_pct", "sortino_ratio", "sqn",
]
