from bigmodule import M

# <aistudiograph>

# ================== 初始化 ==================
# @param(id="m4", name="initialize")
def m4_initialize_bigquant_run(context):
    from bigtrader.finance.commission import PerOrder
    # 手续费设置（可按需调整）
    context.set_commission(PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5))

    # 组合与规则参数
    context.max_hold = 6                  # 最大持仓数
    context.min_hold_decisions = 2        # 最少持有“决策K=收盘”次数

    # 状态变量
    context.hold_decisions = {}           # instrument -> 已经历的“收盘决策次数”
    context.hma_up = {}                   # instrument -> HMA趋势方向(True=多头/False=非多头)


# ================== 每日逻辑 ==================
# @param(id="m4", name="handle_data")
def m4_handle_data_bigquant_run(context, data):
    import pandas as pd

    # 当前交易日（使用日线回测）
    today = data.current_dt.strftime("%Y-%m-%d")

    # === 检查因子数据是否已经更新到 today ===
    try:
        max_date = context.data["date"].max()
    except Exception as e:
        print("ERROR", today, "context.data 读取失败:", e)
        return

    max_date_str = str(max_date)
    print("DEBUG", today, "context.data.max_date =", max_date_str)

    if max_date_str < today:
        print("WARN", today, "因子数据尚未更新到当日，最大可用日期为", max_date_str, "本日信号直接跳过")
        return

    today_df = context.data[context.data["date"] == today]
    if today_df is None or len(today_df) == 0:
        print("WARN", today, "today_df 为空，虽然 max_date >= today，请检查 m3.extract_data / 上游因子是否有当日记录")
        return

    # === 1) 今日收盘时的持仓（已包含前一日下单、今日开盘成交后的结果）===
    hold_set_today = set(context.get_account_positions().keys())

    # === 2) 更新“收盘决策次数” + HMA 趋势方向 ===
    # 收盘“决策K”：当日有仓 -> +1
    for ins in list(hold_set_today):
        context.hold_decisions[ins] = context.hold_decisions.get(ins, 0) + 1

    # 更新 HMA 趋势方向（横盘保持原状态，若无历史则默认 False）
    for ins in list(hold_set_today):
        row = today_df[today_df["instrument"] == ins]
        if len(row) == 0:
            continue
        rising = int(row.iloc[0].get("hma_rising", 0))
        falling = int(row.iloc[0].get("hma_falling", 0))
        if rising == 1:
            context.hma_up[ins] = True
        elif falling == 1:
            context.hma_up[ins] = False
        else:
            context.hma_up[ins] = context.hma_up.get(ins, False)

    # === 3) 生成卖出/买入信号（对应“下一交易日开盘成交”的订单）===

    # 3.1 卖出清单：非多头趋势 且 达到最少持有次数
    sell_tomorrow = []
    for ins in list(hold_set_today):
        hma_up_state = context.hma_up.get(ins, False)
        if (not hma_up_state) and context.hold_decisions.get(ins, 0) >= context.min_hold_decisions:
            sell_tomorrow.append(ins)

    # 3.2 买入候选：今天收盘新出现 st_buy=1 的标的，且非当前持仓
    candidates = today_df[(today_df["st_buy"] == 1)].copy()

    print(today, "DEBUG st_buy=1 候选数量:", len(candidates))

    if len(candidates) > 0:
        candidates = candidates.sort_values(by=["score"], ascending=False)

    # 卖出后可用名额
    hold_after_sell = len(hold_set_today) - len(sell_tomorrow)
    buy_slots = max(context.max_hold - hold_after_sell, 0)

    print(today, "DEBUG 持仓数:", len(hold_set_today), "卖出数:", len(sell_tomorrow), "买入槽位:", buy_slots)

    buy_tomorrow = []
    selected = []
    skip_in_taken = []
    skip_in_sell = []

    if buy_slots > 0 and len(candidates) > 0:
        taken = set(hold_set_today)  # 不买已有持仓
        for _, r in candidates.iterrows():
            ins = r["instrument"]
            sc = float(r["score"]) if pd.notnull(r["score"]) else 0.0

            if ins in taken:
                skip_in_taken.append(ins)
                continue
            if ins in sell_tomorrow:
                skip_in_sell.append(ins)
                continue

            buy_tomorrow.append((ins, sc))
            selected.append(ins)
            taken.add(ins)
            if len(buy_tomorrow) >= buy_slots:
                break

    if len(skip_in_taken) > 0:
        print(today, "DEBUG 因已持仓被跳过:", skip_in_taken)
    if len(skip_in_sell) > 0:
        print(today, "DEBUG 因同时在卖出清单被跳过:", skip_in_sell)
    if len(selected) > 0:
        print(today, "DEBUG 最终选中的买入标的:", selected)

    # === 4) 直接下单：BigTrader 会在下一根 bar 的 open 成交 ===

    # 先卖出
    for ins in sell_tomorrow:
        context.order_target_percent(ins, 0)

    # 再买入（等权按 max_hold 分仓）
    if len(buy_tomorrow) > 0:
        unit_weight = 1.0 / context.max_hold
        for ins, sc in buy_tomorrow:
            context.order_target_percent(ins, unit_weight)
            # 新买的，当前这根K线还没真正持有，所以先记为0，下一根收盘再 +1
            context.hold_decisions[ins] = 0

    # === 5) 日志 + 钉钉推送（文案沿用“明日买入/卖出”）===
    if len(sell_tomorrow) > 0:
        print(f"{today} 收盘信号｜明日卖出: {sell_tomorrow}")
    else:
        print(f"{today} 收盘信号｜明日卖出: 无")
    if len(buy_tomorrow) > 0:
        print(f"{today} 收盘信号｜明日买入(按score降序择优): {[x[0] for x in buy_tomorrow]}")
    else:
        print(f"{today} 收盘信号｜明日买入: 无")



# ================== 模块拼装 ==================

# @module(comment="股票池：仅主板/创业板；过滤 ST、停牌；其余过滤放到因子筛选中")
m1 = M.cn_stock_basic_selector.v8(
    exchanges=["上交所", "深交所"],
    list_sectors=["主板", "创业板"],
    indexes=["中证500", "上证指数", "创业板指", "深证成指", "上证50", "科创50", "沪深300", "中证1000", "中证100", "深证100"],
    st_statuses=["正常"],
    drop_suspended=True,
    m_name="m1"
)

# @module(comment="日频信号：Supertrend 买入近似 + HMA 斜率方向 + 动量/量比 + 行业偏好评分")
m2 = M.input_features_dai.v30(
    input_1=m1.data,
    mode="表达式",
    expr=(
        "(high+low)/2 AS hl2\n"
        "high-low AS tr1\n"
        "abs(high-m_lag(close,1)) AS tr2\n"
        "abs(low-m_lag(close,1)) AS tr3\n"
        "IF(tr1>=tr2 AND tr1>=tr3, tr1, IF(tr2>=tr3, tr2, tr3)) AS tr\n"
        "m_ta_sma(tr, 10) AS atr\n"
        "hl2 + 3.0*atr AS st_upper\n"
        "IF(close > m_lag(st_upper,1) AND m_lag(close,1) <= m_lag(st_upper,2), 1, 0) AS st_buy\n"
        "m_ta_wma(close,50) AS wma_50\n"
        "m_ta_wma(close,25) AS wma_25\n"
        "2*wma_25 - wma_50 AS hma_raw\n"
        "m_ta_wma(hma_raw,7) AS hma\n"
        "IF(hma > m_lag(hma,1) AND m_lag(hma,1) > m_lag(hma,2) AND m_lag(hma,2) > m_lag(hma,3), 1, 0) AS hma_rising\n"
        "IF(hma < m_lag(hma,1) AND m_lag(hma,1) < m_lag(hma,2) AND m_lag(hma,2) < m_lag(hma,3), 1, 0) AS hma_falling\n"
        "close/m_lag(close,20)-1 AS momentum_20\n"
        "volume/m_avg(volume,10) AS vol_ratio_10\n"
        "IF(sw2021_level2='电力设备' OR sw2021_level2='计算机' OR sw2021_level2='通信' OR sw2021_level2='电子' OR "
        "sw2021_level2='国防军工' OR sw2021_level2='汽车' OR sw2021_level2='环保' OR sw2021_level2='信息服务' OR "
        "sw2021_level2='医药生物', 1, 0) AS policy_pref\n"
        "0.35*c_pct_rank(momentum_20) + 0.15*c_pct_rank(vol_ratio_10) + 0.5*policy_pref AS score"
    ),
    expr_filters=(
        "list_days > 365\n"
        "st_status = 0\n"
        "is_risk_warning = 0\n"
        "suspended = 0\n"
        "price_limit_status = 2\n"
        "sw2021_level2 <> '房地产'\n"
        "sw2021_level2 <> '银行'\n"
        "sw2021_level2 <> '非银金融'\n"
        "sw2021_level2 <> '钢铁'\n"
        "sw2021_level2 <> '煤炭'\n"
        "sw2021_level2 <> '石油石化'\n"
        "sw2021_level2 <> '公用事业'\n"
        "sw2021_level2 <> '交通运输'\n"
        "sw2021_level2 <> '采掘'\n"
        "sw2021_level2 <> '食品饮料'\n"
        "sw2021_level2 <> '农林牧渔'\n"
        "sw2021_level2 <> '传媒'\n"
        "sw2021_level2 <> '社会服务'\n"
        "sw2021_level2 <> '综合'\n"
        "sw2021_level2 <> '建筑装饰'\n"
        "sw2021_level2 <> '建筑材料'"
    ),
    expr_tables="cn_stock_prefactors",
    expr_drop_na=True,
    m_name="m2"
)

# @module(comment="抽取数据（日频），回测区间：2025-01-01 至 2040-09-25")
m3 = M.extract_data_dai.v20(
    sql=m2.data,
    start_date="2025-01-01",
    start_date_bound_to_trading_date=True,
    end_date="2040-09-25",
    end_date_bound_to_trading_date=True,
    before_start_days=200,
    m_name="m3"
)

# @module(comment="交易引擎：日频；收盘生成清单；次日开盘执行；不再平衡")
m4 = M.bigtrader.v43(
    data=m3.data,
    initialize=m4_initialize_bigquant_run,
    handle_data=m4_handle_data_bigquant_run,
    capital_base=100000,
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