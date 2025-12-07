# 策略名称：月度初涨幅统计策略
from bigmodule import M

# <aistudiograph>

# ================== 初始化 ==================
# @param(id="m4", name="initialize")
def m4_initialize_bigquant_run(context):
    from bigtrader.finance.commission import PerOrder
    # 手续费设置
    context.set_commission(PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5))

    # 策略参数
    context.target_hold_count = 10       # 持仓数量
    context.target_position_pct = 0.10   # 单只仓位 10%

    # 状态标记
    context.is_data_processed = False
    context.processed_data = None
    context.calendar_info = {}           # date_str -> {day_rank, is_last_day, month_str, days_in_month}

# ================== 数据预处理 ==================
def process_data(context):
    """
    在策略开始运行时，对 context.data 进行一次性预处理：
    1. 计算每月的交易日序号 (day_rank)
    2. 标记每月最后一个交易日 (is_last_day)
    3. 计算月初至今的累计涨幅 (acc_ret)
    4. 计算每日的涨幅排名 (daily_rank)
    """
    import pandas as pd
    import numpy as np

    df = context.data.copy()
    if df is None or len(df) == 0:
        print("ERROR: 数据为空")
        return

    # 确保按日期排序
    df = df.sort_values(by=['date', 'instrument'])
    df['date_dt'] = pd.to_datetime(df['date'])
    df['month_str'] = df['date_dt'].dt.strftime('%Y-%m')
    df['date_str'] = df['date_dt'].dt.strftime('%Y-%m-%d')

    # 1. 计算每月交易日序号 & 2. 标记每月最后一天
    # 获取唯一的交易日历
    trading_days = df[['date_str', 'month_str']].drop_duplicates().sort_values('date_str')
    trading_days['day_rank'] = trading_days.groupby('month_str')['date_str'].rank(method='dense').astype(int)
    trading_days['is_last_day'] = trading_days['date_str'] == trading_days.groupby('month_str')['date_str'].transform('max')

    # 计算每月的总交易天数，用于判断是否 >= 4天
    trading_days['days_in_month'] = trading_days.groupby('month_str')['date_str'].transform('count')

    # 将日历信息合并回主表
    df = pd.merge(df, trading_days, on=['date_str', 'month_str'], how='left')

    # 3. 计算月初至今累计涨幅
    # 需要获取每月第1个交易日的开盘价
    # 筛选出 day_rank == 1 的行
    month_starts = df[df['day_rank'] == 1][['month_str', 'instrument', 'open']].rename(columns={'open': 'month_open'})
    df = pd.merge(df, month_starts, on=['month_str', 'instrument'], how='left')

    # 计算 acc_ret = (close / month_open - 1) * 100%
    # 注意：month_open 可能有空值（如果某股票月初停牌或者不在池中）
    df['acc_ret'] = (df['close'] / df['month_open'] - 1) * 100.0

    # 4. 计算每日的排名
    # 在每天内部，按 acc_ret 降序排名
    df['daily_rank'] = df.groupby('date_str')['acc_ret'].rank(method='min', ascending=False)

    # 将处理后的数据存入 context，方便后续查询
    # 使用 date_str 作为索引的一部分
    context.processed_data = df.set_index(['date_str', 'instrument'])

    # 存储每日的日历信息，Key为 date_str (YYYY-MM-DD)
    context.calendar_info = trading_days.set_index('date_str').to_dict('index')

    context.is_data_processed = True
    print("数据预处理完成")
    # 打印部分日历信息用于调试
    print("DEBUG: Calendar Info Sample:", list(context.calendar_info.items())[:5])


# ================== 每日逻辑 ==================
# @param(id="m4", name="handle_data")
def m4_handle_data_bigquant_run(context, data):
    import pandas as pd

    # 获取今日日期 (YYYY-MM-DD string)
    today = data.current_dt.strftime("%Y-%m-%d")

    # 懒加载：预处理数据
    if not context.is_data_processed:
        process_data(context)

    # 获取今日日历信息
    day_info = context.calendar_info.get(today)
    if not day_info:
        # 如果找不到，可能是数据范围问题，或者是交易日历和数据不匹配
        # print(f"WARN: {today} 无日历信息，跳过")
        return

    day_rank = day_info['day_rank']
    is_last_day = day_info['is_last_day']
    days_in_month = day_info['days_in_month']

    # 获取当前持仓
    current_positions = list(context.get_account_positions().keys())

    # === 规则3：月底强制清仓 ===
    # 无论持仓股表现如何，在每月最后一个交易日开盘时，将所有持仓股票全部清仓
    # 注意：bigtrader在handle_data下单，订单会在下一根bar（即明天）开盘成交
    # 如果今天是最后一天，我们下单卖出，明天（下月第一天）开盘成交？
    # 规则描述："在每月最后一个交易日开盘时，将所有持仓股票全部清仓，确保月底无持仓。"
    # 这意味着我们应该在“倒数第二天”的收盘后（即handle_data）下单，以便在“最后一天”的开盘成交。
    # 如果我们在“最后一天”handle_data下单，成交是在下个月第一天。
    # 所以我们需要判断 "明天是否是当月最后一天"。
    # 但是 BigQuant 的回测机制：handle_data 运行在 Close。
    # Order -> Next Open.
    # 目标：Last Day Open 卖出。
    # 动作：Last Day - 1 Close 下单。

    # 查找明天的信息
    # 我们需要找到比今天大的最小日期
    dates = sorted(list(context.calendar_info.keys()))
    try:
        curr_idx = dates.index(today)
        next_date = dates[curr_idx + 1] if curr_idx + 1 < len(dates) else None
    except ValueError:
        next_date = None

    # 检查明天是否是本月最后一天
    should_clear_for_month_end = False
    if next_date:
        next_info = context.calendar_info.get(next_date)
        if next_info and next_info['is_last_day']:
            should_clear_for_month_end = True

    # 如果触发月底清仓（即明天是最后一天），则清仓
    if should_clear_for_month_end:
        if len(current_positions) > 0:
            print(f"{today} (Day {day_rank}) 触发月底清仓 (将在 {next_date} 开盘卖出)")
            for stock in current_positions:
                context.order_target_percent(stock, 0)
        return # 清仓优先

    # === 规则1：买入规则 ===
    # "在每月第 4 个交易日开盘时...买入"
    # 动作：在每月第 3 个交易日 Close (handle_data) 下单
    if days_in_month >= 4 and day_rank == 3:
        # 筛选第 1-3 个交易日累计涨幅排名前 10
        # 数据截止到 Day 3 收盘 (today)
        # 获取 Day 3 (今天) 的数据
        try:
            day_3_df = context.processed_data.loc[today]
            # 过滤有效涨幅
            valid_candidates = day_3_df[day_3_df['acc_ret'].notnull()]
            # 排名
            top_10 = valid_candidates.sort_values(by='acc_ret', ascending=False).head(10)
            buy_list = top_10.index.tolist() # instrument list

            print(f"{today} (Day 3 Close) 生成买入信号 (将在Day 4开盘买入): {buy_list}")

            # 执行买入
            # 每只股票买入组合总资金的 10% 仓位
            weight = 0.10
            # 先卖出不在名单里的 (如果是空仓则无所谓)
            for stock in current_positions:
                if stock not in buy_list:
                    context.order_target_percent(stock, 0)

            for stock in buy_list:
                context.order_target_percent(stock, weight)

        except KeyError:
            print(f"WARN: 无法获取 {today} 的数据")

    # === 规则2：持有期卖出规则 ===
    # "从每月第 5 个交易日起...每个交易日开盘前...若跌出前10...卖出"
    # 这意味着在 Day 5 Open 检查 Day 4 的累计排名?
    # 或者是 Day 5 Open 检查 Day 4 此时的排名?
    # 通常 "基于当日最新...计算...若排名跌出...则在当日开盘时卖出"。
    # "当日" 指 Day 5, Day 6 ... Last Day.
    # 要在 Day 5 Open 卖出，需要在 Day 4 Close 下单。
    # 所以我们在 day_rank >= 4 时检查，决定是否在 "明天(Day rank+1)" Open 卖出。
    # 检查逻辑：
    # 假设今天是 T (Day 4), 明天是 T+1 (Day 5).
    # 我们需要在 T Close 预测 T+1 Open 的动作。
    # 规则说 "每个交易日(Day 5+)开盘前...计算当前持仓股的当月累计涨幅...若排名跌出...当日开盘卖出"。
    # 计算涨幅用的是 "(前一交易日收盘价 / 当月第 1 个交易日开盘价 - 1)".
    # 对于 Day 5 来说，前一交易日是 Day 4。
    # 所以在 Day 4 Close (今天), 数据已经具备 (Day 4 Close / Month Open - 1).
    # 所以我们在 Day 4 Close 计算排名，如果持仓股不在前 10，则下单卖出 (在 Day 5 Open 成交).

    # 适用范围：
    # 我们需要在 Day 4, 5, ..., LastDay-2 进行检查。
    # (LastDay-1 已经被 规则3 覆盖，全卖)
    # 所以范围是: day_rank >= 4.

    # 注意：day_rank == 4 时，既处理了 "买入(昨天Day3下的单,今天Day4开盘已买入)" 的后续，
    # 也需要检查是否要卖出(明天Day5开盘卖)。
    # 但 Day 4 刚买入，Close 时检查排名。如果 Day 4 收盘跌出前 10，明天 Day 5 开盘就卖？是的。

    if days_in_month >= 4 and day_rank >= 4 and not should_clear_for_month_end:
        if len(current_positions) > 0:
            # 计算今天 (T) 的排名
            # 今天的数据 context.processed_data.loc[today] 包含 acc_ret (基于 Today Close)
            try:
                today_df = context.processed_data.loc[today]
                # 获取今天排名前10
                valid_candidates = today_df[today_df['acc_ret'].notnull()]
                top_10_today = valid_candidates.sort_values(by='acc_ret', ascending=False).head(10).index.tolist()

                # 检查持仓
                for stock in current_positions:
                    if stock not in top_10_today:
                        print(f"{today} (Day {day_rank}) 持仓 {stock} 跌出前10 (排名依据今日收盘), 明日开盘卖出")
                        context.order_target_percent(stock, 0)
                    else:
                        # 继续持有
                        pass
            except KeyError:
                print(f"WARN: 无法获取 {today} 的数据")


# ================== 模块拼装 ==================

# @module(comment="股票池：沪深300")
m1 = M.cn_stock_basic_selector.v8(
    exchanges=["上交所", "深交所"],
    list_sectors=["主板", "创业板"],
    indexes=["沪深300"],
    st_statuses=["正常"],
    drop_suspended=True,
    m_name="m1"
)

# @module(comment="数据提取：提取所有HS300成分股的量价数据")
m2 = M.input_features_dai.v30(
    input_1=m1.data,
    mode="表达式",
    expr=(
        "open\n"
        "close\n"
    ),
    expr_filters="",
    expr_tables="cn_stock_prefactors",
    expr_drop_na=True,
    m_name="m2"
)

# @module(comment="抽取数据")
m3 = M.extract_data_dai.v20(
    sql=m2.data,
    start_date="2020-01-01",
    start_date_bound_to_trading_date=True,
    end_date="2024-01-01",
    end_date_bound_to_trading_date=True,
    before_start_days=0,
    m_name="m3"
)

# @module(comment="交易引擎")
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
