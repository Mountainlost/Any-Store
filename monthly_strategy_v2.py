# Strategy Name: Monthly Rebalance Strategy V2
from bigmodule import M

# <aistudiograph>

# ================== Initialize ==================
# @param(id="m4", name="initialize")
def m4_initialize_bigquant_run(context):
    from bigtrader.finance.commission import PerOrder
    # Settings: Log level, dynamic rights adjustment (handled by platform), benchmark, slippage, costs
    context.set_commission(PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5))

    # Global Variables
    context.stock_num = 10
    context.choice = [] # Stock pool
    context.not_buy_again_list = [] # Blacklist (intersection of history hold & recent limit up)

    # Tracking variables
    context.high_limit_list = [] # Yesterday's limit up stocks held
    context.hold_list = [] # Current holdings
    context.history_hold_list = [] # List of stocks held in last 30 days. We will store simple list of stocks.
    # To strictly follow "record 30 days hold record", we might need (date, stock) pairs.
    # But description says "Update history hold list... and extract all appearing stocks as not_buy_again_list".
    # This implies not_buy_again_list IS the unique set of stocks held in last 30 days.

    # Actually, we need to know WHEN they were held to expire them after 30 days.
    # So we store: context.history_hold_map = {stock: last_held_date}
    context.history_hold_map = {}

    context.just_sold = [] # Record of sold stocks (keep last 10)
    context.no_trading_today_signal = False # April signal

# ================== Helper Functions ==================

def get_recent_limit_up_stocks(today_df):
    """
    Get stocks that have been limit up in the last 30 days.
    Relies on pre-calculated column 'recent_limit_up_30d'.
    """
    if 'recent_limit_up_30d' in today_df.columns:
        # Filter where recent_limit_up_30d > 0
        return set(today_df[today_df['recent_limit_up_30d'] > 0]['instrument'].tolist())
    return set()

def prepare_high_limit_list(context, today_df, prev_date_df):
    """
    9:05 Execution: Record yesterday's limit up stocks and update history hold list.
    """
    # 1. Update g.high_limit_list (Yesterday's limit up stocks held)
    context.high_limit_list = []
    current_positions = list(context.get_account_positions().keys())
    context.hold_list = current_positions

    if prev_date_df is not None and not prev_date_df.empty:
        for stock in current_positions:
            # Check if stock was limit up yesterday
            row = prev_date_df[prev_date_df['instrument'] == stock]
            if not row.empty:
                is_limit_up = row.iloc[0].get('is_limit_up', 0)
                # Or check price directly if flag not available
                close = row.iloc[0].get('close')
                limit_up_price = row.iloc[0].get('limit_up_price')

                # Use flag if available, else calc
                if is_limit_up == 1:
                    context.high_limit_list.append(stock)
                elif limit_up_price and abs(close - limit_up_price) < 0.01:
                    context.high_limit_list.append(stock)

    # 2. Update g.history_hold_list (Recent 30 days hold record)
    # We use a map {stock: last_seen_date_index} or {stock: date_str}
    # Here we use context.history_hold_map {stock: date_str}
    # Clean up old entries
    today_str = str(today_df.iloc[0]['date']) if not today_df.empty else ""
    # We can't easily do date math on strings without parsing.
    # But since we run daily, we can just rebuild the list from a robust structure.
    # Simplified: Add current positions to history map with today's date.

    # For simulation, just assume we keep a set of "stocks held in last 30 days".
    # Implementation: Add current holdings to a list with timestamp.
    # Filter list for entries < 30 days old.

    # Re-using context.history_hold_list as a list of (date, stock)
    import datetime
    try:
        current_dt = datetime.datetime.strptime(str(today_df.iloc[0]['date']), "%Y-%m-%d")
    except:
        current_dt = None

    if current_dt:
        # Add current holdings
        for stock in current_positions:
            context.history_hold_list.append((current_dt, stock))

        # Clean old
        cutoff = current_dt - datetime.timedelta(days=30)
        context.history_hold_list = [x for x in context.history_hold_list if x[0] > cutoff]

        # Update not_buy_again_list (Unique stocks in history)
        context.not_buy_again_list = list(set([x[1] for x in context.history_hold_list]))

    # 4 April Check (disabled per instructions but logic present)
    # g.no_trading_today_signal = False # Done in init and reset here
    context.no_trading_today_signal = False
    # if current_dt and current_dt.month == 4:
    #     context.no_trading_today_signal = True # Disabled

def check_limit_up(context, today_df):
    """
    14:00 Execution: Check if yesterday's limit up stocks opened today.
    """
    # Check yesterday's limit up stocks held (g.high_limit_list)
    # If open (current price < limit up), sell.

    positions = context.get_account_positions()

    stocks_to_sell = []

    for stock in context.high_limit_list:
        if stock in positions:
            # Check today's status
            row = today_df[today_df['instrument'] == stock]
            if not row.empty:
                close = row.iloc[0]['close']
                limit_up_price = row.iloc[0]['limit_up_price']

                # If current price (close in daily bar) < limit up price
                if limit_up_price and close < limit_up_price - 0.01:
                    # Limit opened
                    stocks_to_sell.append(stock)

    # Sell and add to just_sold
    for stock in stocks_to_sell:
        context.order_target_percent(stock, 0)
        context.just_sold.append(stock)

    # Maintain just_sold size (last 10)
    if len(context.just_sold) > 10:
        context.just_sold = context.just_sold[-10:]

    # If holdings insufficient, call my_Trader to buy
    # Insufficient means < context.stock_num
    current_hold_count = len(positions) - len(stocks_to_sell)
    if current_hold_count < context.stock_num:
        # Call selection and buy
        my_Trader(context, today_df, buy_only=True)

def close_account(context):
    """
    14:30 Execution: Clear account if signal is True (April).
    """
    if context.no_trading_today_signal:
        positions = context.get_account_positions()
        for stock in positions:
            context.order_target_percent(stock, 0)

def my_Trader(context, today_df, buy_only=False):
    """
    Stock Selection Logic.
    buy_only: If True, only buy to fill positions (used by check_limit_up).
    """
    import pandas as pd

    # 1. Filter Boards (Start with 4, 8, 68)
    # 68: STAR, 8: BSE, 4: BSE? (Depending on market, usually 8/4 are Beijing/Other)
    df = today_df.copy()
    df = df[~df['instrument'].str.match(r'^(68|4|8)')]

    # 2. Basic Filters
    # ST, Suspended, List days < 250, Limit Up, Limit Down, Price > 30
    # Price limit status: Assuming 1=Up, 2=Normal, 3=Down or similar.
    # Safer: Use calculated is_limit_up, is_limit_down columns if available.

    df = df[df['st_status'] == 0]
    df = df[df['suspended'] == 0]
    df = df[df['list_days'] >= 250]
    df = df[df['close'] <= 30]

    # Filter Limit Up/Down (Today)
    # We want to buy stocks that are NOT limit up/down today?
    # Usually we don't buy limit up (can't buy) or limit down (bad signal).
    if 'is_limit_up' in df.columns:
        df = df[df['is_limit_up'] == 0]
    if 'is_limit_down' in df.columns:
        df = df[df['is_limit_down'] == 0]

    # 3. Fundamental Screening (PEG -> ROE, ROA)
    # ROE > 15, ROA > 10 (Assuming data is percentage or raw? standard is usually percentage e.g. 15.0)
    # BigQuant usually provides raw decimals or percent?
    # Usually roe_ttm is percentage (e.g. 15.5) or ratio (0.155).
    # HMA example does not show ROE usage.
    # Common convention: if values are like 15, use 15. If < 1, use 0.15.
    # I'll assume standard BigQuant factor values. Let's assume they are percentages if > 1 is common.
    # I'll check magnitude. If max ROE is > 100, it's percent.
    # Since I can't check data, I'll assume they are percentages (common in finance apps).
    # "ROE > 15%" -> implies 15.
    df = df[df['roe_ttm'] > 15]
    df = df[df['roa_ttm'] > 10]

    # 4. Sort by Market Cap Ascending
    df = df.sort_values(by='total_market_cap', ascending=True)

    # 5. Blacklist Filter
    # "Intersection of g.not_buy_again_list and recent 30 days limit up"
    # Stocks to exclude: (In History Hold) AND (In Recent Limit Up)
    recent_limit_up = get_recent_limit_up_stocks(today_df)
    blacklist = set(context.not_buy_again_list).intersection(recent_limit_up)

    df = df[~df['instrument'].isin(blacklist)]

    # Select top N
    context.choice = df.head(context.stock_num)['instrument'].tolist()

    # Execution (Buy)
    if buy_only:
        # Buy to fill gaps.
        # Logic: Buy from context.choice until full.
        # But wait, context.choice was just updated.
        # If calling from check_limit_up, we should probably respect the monthly choice?
        # User says: "If holdings insufficient, call my_Trader and buy."
        # This implies re-running selection to find NEW candidates.
        # So using the new context.choice is correct.
        go_Trader_buy(context)

def go_Trader(context, today_df):
    """
    Monthly Rebalance Logic (14:55).
    """
    # 1. Sell
    # Sell stocks not in monthly selection (context.choice)
    # "Sell: Not in current selection ... sell, and join g.just_sold"

    positions = context.get_account_positions()
    for stock in positions:
        if stock not in context.choice:
            # Check suspended? "Stocks not in ... (and not suspended) then sell"
            # If suspended, can't sell.
            # Check suspension status from today_df
            row = today_df[today_df['instrument'] == stock]
            is_suspended = False
            if not row.empty:
                is_suspended = (row.iloc[0]['suspended'] == 1)

            if not is_suspended:
                context.order_target_percent(stock, 0)
                context.just_sold.append(stock)

    # Maintain just_sold size
    if len(context.just_sold) > 10:
        context.just_sold = context.just_sold[-10:]

    # 2. Buy
    go_Trader_buy(context)

def go_Trader_buy(context):
    """
    Buy logic: Average allocation to target 10 stocks.
    """
    positions = context.get_account_positions()
    target_count = context.stock_num

    # We want to hold 'target_count' stocks from 'context.choice'.
    # Existing holdings that are IN context.choice are kept.
    # We add new ones until we reach target_count?
    # Or just buy top 10 from choice?
    # "Buy: Average allocate ... buy until target hold count (10) reached."

    current_hold_count = len(positions)
    slots_available = target_count - current_hold_count

    if slots_available > 0:
        # Find candidates from context.choice that we don't hold
        candidates = [s for s in context.choice if s not in positions]

        # Take top 'slots_available'
        to_buy = candidates[:slots_available]

        # Allocation: "Average allocate available funds"
        # Since we are rebalancing, maybe we should rebalance ALL 10?
        # "Buy: Average allocate buy ... until 10".
        # This usually means 1/10th of portfolio per stock?
        # Or Available Cash / slots_available?
        # Standard: 1.0 / target_count per stock.
        weight = 1.0 / target_count

        for stock in to_buy:
            context.order_target_percent(stock, weight)

# ================== Daily Logic ==================
# @param(id="m4", name="handle_data")
def m4_handle_data_bigquant_run(context, data):
    import pandas as pd
    import datetime

    today = data.current_dt.strftime("%Y-%m-%d")

    # Data Validation
    try:
        max_date = context.data["date"].max()
    except Exception as e:
        print("ERROR", today, "context.data read failed:", e)
        return

    if str(max_date) < today:
        print("WARN", today, "Data not updated.")
        return

    today_df = context.data[context.data["date"] == today]
    if today_df is None or len(today_df) == 0:
        return

    # Get Previous Day Data (for 9:05 logic)
    dates = sorted(context.data['date'].unique())
    prev_df = None
    try:
        today_idx = dates.index(today)
        if today_idx > 0:
            prev_date = dates[today_idx - 1]
            prev_df = context.data[context.data["date"] == prev_date]
    except ValueError:
        pass

    # --- Schedule Execution ---

    # 1. 09:05 prepare_high_limit_list
    prepare_high_limit_list(context, today_df, prev_df)

    # 2. 14:00 check_limit_up
    check_limit_up(context, today_df)

    # 3. 14:30 close_account (April check)
    close_account(context)

    # 4. 14:55 Monthly Rebalance (go_Trader)
    # Condition: Last trading day of month
    # Check if tomorrow is in next month
    is_month_end = False
    if today_idx < len(dates) - 1:
        next_date_str = dates[today_idx + 1]
        curr_mon = int(today.split('-')[1])
        next_mon = int(next_date_str.split('-')[1])
        if curr_mon != next_mon:
            is_month_end = True
    else:
        # Last day of data is effectively month end for simulation
        is_month_end = True

    if is_month_end:
        # Run Selection First
        my_Trader(context, today_df, buy_only=False)
        # Run Rebalance
        go_Trader(context, today_df)


# ================== Module Assembly ==================

# 1. Stock Pool Selector
m1 = M.cn_stock_basic_selector.v8(
    exchanges=["上交所", "深交所"],
    list_sectors=["主板", "创业板"], # Will filter STAR (68) and BSE (8/4) later in code or here?
    # STAR is "科创板". BSE is "北交所".
    # User said: "Filter STAR and BSE".
    # We can exclude them here if options exist, but code filtering in my_Trader is explicit.
    indexes=["中证500", "上证指数", "创业板指", "深证成指", "上证50", "沪深300", "中证1000", "中证100", "深证100"],
    st_statuses=["正常"],
    drop_suspended=True,
    m_name="m1"
)

# 2. Input Features (DAI SQL)
m2 = M.input_features_dai.v30(
    input_1=m1.data,
    mode="表达式",
    expr=(
        # Basic
        "open\n"
        "high\n"
        "low\n"
        "close\n"
        "volume\n"
        "amount\n"
        "total_market_cap\n"
        "roe_ttm\n"
        "roa_ttm\n"
        "list_days\n"
        "st_status\n"
        "suspended\n"
        "limit_up_price\n"
        "limit_down_price\n"
        # Flags
        "IF(close >= limit_up_price - 0.01, 1, 0) AS is_limit_up\n"
        "IF(close <= limit_down_price + 0.01, 1, 0) AS is_limit_down\n"
        # Recent Limit Up Count (30 days)
        "m_sum(IF(close >= limit_up_price - 0.01, 1, 0), 30) AS recent_limit_up_30d\n"
    ),
    expr_filters=(
        # We handle detailed filtering in Python to match user logic precisely
        "list_days > 0"
    ),
    expr_tables="cn_stock_prefactors",
    expr_drop_na=True,
    m_name="m2"
)

# 3. Extract Data
m3 = M.extract_data_dai.v20(
    sql=m2.data,
    start_date="2024-01-01", # Adjust dates as needed
    start_date_bound_to_trading_date=True,
    end_date="2025-01-01",
    end_date_bound_to_trading_date=True,
    before_start_days=100,
    m_name="m3"
)

# 4. BigTrader
m4 = M.bigtrader.v43(
    data=m3.data,
    initialize=m4_initialize_bigquant_run,
    handle_data=m4_handle_data_bigquant_run,
    capital_base=1000000,
    frequency="daily",
    product_type="股票",
    rebalance_period_type="交易日",
    rebalance_period_days="1",
    order_price_field_buy="open",
    order_price_field_sell="open",
    benchmark="沪深300指数",
    m_name="m4"
)

# </aistudiograph>
