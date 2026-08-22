"""端到端闭环验证：真实数据 对象池→因子→卡片→风控→计划（TODO 1 收官）。"""
import sys

sys.path.insert(0, ".")
from invest.config import get_settings
from invest.db import connect, init_db

db = get_settings().db_path
init_db(db)
conn = connect(db)
SYM = "000001"  # 平安银行（真实库唯一有日线的标的）

print("=" * 60)
print("阶段1 闭环 E2E（真实数据）")
print("=" * 60)

# 1) 对象池：硬门槛
from invest.discipline.pool_rules import check_and_add, hard_gate_check

v = hard_gate_check(conn, SYM)
print(f"\n[1] 硬门槛 {SYM}: {'通过' if not v else v}")
check_and_add(conn, SYM, level="core", industry="银行", reason="E2E 验证标的")

# 2) 因子与主价差
from invest.discipline.spread import factor_score, price_spread

spread = price_spread(conn, SYM, years=3)
print(f"\n[2] 主价差 {SYM}: 当前={spread.get('current')} 中位={spread.get('median')} "
      f"分位={spread.get('pct_rank')} Z={spread.get('z_score')} 锚={spread.get('anchor_range')}")
fscore = factor_score([
    {"name": "估值分位", "score": 4 if (spread.get("pct_rank") or 0) < 0.3 else 2, "role": "错价"},
    {"name": "盈利修复", "score": 3, "role": "修复"},
    {"name": "宏观流动性", "score": 5, "role": "背景"},
])
print(f"    因子打分: {fscore['total']}（{fscore['grade']}）")

# 3) 卡片：建卡→锁卡
from invest.discipline.cards import compute_rr, create_card, lock_card, validate_card

# 用 000001 真实价格区间构造（当前价 ~11 元）
row = conn.execute("SELECT close FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT 1", (SYM,)).fetchone()
cur = float(row["close"])
card = create_card(
    conn, SYM, level="A", cycle="short",
    thesis="银行股低估值修复，股息率支撑，roe 稳定，市场风格切换受益",
    falsify="净息差大幅收窄或不良率跳升",
    entry_range=f"{round(cur*0.97,2)},{round(cur*1.02,2)}",
    stop_loss=round(cur*0.90, 2), target=round(cur*1.15, 2),
)
lock_card(conn, card["card_id"])
print(f"\n[3] 卡片 #{card['card_id']} 已锁定; 校验={validate_card(conn, card['card_id'])}")
entry = cur * 0.97
rr = compute_rr(entry, cur*0.90, cur*1.15)
print(f"    赔率 RR={rr}（入场~{entry:.2f}）")

# 4) 风控校验 + 总闸
from invest.discipline.costs import compute_cost
from invest.discipline.macro_gate import apply_gate
from invest.discipline.rating import get_position_limit
from invest.discipline.risk import check_position

viol = check_position(conn, proposed=0.06, total_position=0.10, industry_position=0.10,
                      data_ok=True, symbol=SYM)
print(f"\n[4] 风控: 评级仓位上限={get_position_limit(conn):.0%}, 违规={viol or '无'}")
gate = apply_gate(conn, get_position_limit(conn), erp_pct=0.5)
print(f"    总闸: 环境={gate['env']} → 最终仓位上限={gate['final_gate']:.1%}")

# 5) 计划 + 固定风险仓位
from invest.discipline.position import create_plan_from_card

plan = create_plan_from_card(conn, card["card_id"], equity=1_000_000)
print(f"\n[5] 计划 #{plan['plan_id']}（{plan['symbol']}, card={plan['card_id']}）")
pos = plan["suggested_position"]
print(f"    固定风险仓位: {pos['qty']} 股 = {pos['position_fraction']:.1%}（R={pos['risk_fraction']:.2%}, 上限{pos['cap']:.0%}）")
cost = compute_cost(entry, pos["qty"], "buy")
print(f"    买入成本: {cost.breakdown()}")

# 6) 收盘扫描快照（PIT 存档）
from invest.scan import take_snapshot

snap = take_snapshot(db)
print(f"\n[6] 快照已存档: {snap['date']}（pool {len(snap['pool'])} 标的）")
conn.close()
print("\n闭环 E2E 完成 ✅")
