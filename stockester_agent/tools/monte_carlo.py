import numpy as np
import yfinance as yf


def fetch_nifty_metrics(lookback_days: int = 252) -> tuple[float, float]:
    """
    Calculate daily drift (mu) and volatility (sigma) from real NIFTY 50 history.

    Uses log returns over the last `lookback_days` calendar days.
    Falls back to conservative defaults if the fetch fails.
    """
    try:
        df = yf.Ticker("^NSEI").history(period=f"{lookback_days}d")
        if df.empty or len(df) < 20:
            raise ValueError(f"Insufficient history returned ({len(df)} bars)")

        log_returns = np.log(df['Close'] / df['Close'].shift(1)).dropna()
        mu    = float(log_returns.mean())
        sigma = float(log_returns.std())
        return mu, sigma

    except Exception as e:
        print(f"⚠️  fetch_nifty_metrics failed ({e}). Using fallback mu=0.0005, sigma=0.015.")
        return 0.0005, 0.015


def run_monte_carlo(spot: float, sim_config: dict) -> tuple[np.ndarray, float]:
    """
    Simulate future NIFTY prices using Geometric Brownian Motion.

    mu and sigma are derived from real historical data via yfinance;
    lookback_days is read from sim_config (default 252 if key absent).

    Returns:
        final_prices : ndarray of simulated prices on the last simulated day
        prob_up      : probability of price finishing above current spot
    """
    days         = sim_config['days_to_simulate']
    iterations   = sim_config['num_iterations']
    lookback     = sim_config.get('lookback_days', 252)

    mu, sigma = fetch_nifty_metrics(lookback_days=lookback)

    # GBM: use exact log-normal step to avoid drift bias from simple returns
    dt            = 1  # one trading day per step
    rand_shocks   = np.random.normal(0, 1, size=(iterations, days))
    log_returns   = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * rand_shocks
    final_prices  = spot * np.exp(np.sum(log_returns, axis=1))

    prob_up = float(np.mean(final_prices > spot))
    return final_prices, prob_up
