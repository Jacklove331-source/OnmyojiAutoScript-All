# 策略对照（自动生成，`acceptance.check_strategy_compare`）

- 基线: chess_series3_run3_1248.log（3 局）
- 评估窗口: v2 启动后最近 3 局

| 指标 | 基线 | 窗口 |
| --- | --- | --- |
| avg_rank | 6.33 | 5.67 |
| avg_early_buy_ok | 0.0 | 4.67 |
| avg_early_deploy | 0.67 | 4.0 |
| buy_confirm_rate | 0.179 | 0.913 |
| avg_buy_unconfirmed | 10.67 | 0.67 |
| soul_equipped | 0 | 10 |
| soul_success_rate | 0.0 | 0.714 |
| avg_level_ocr_invalid | 0.0 | 0.0 |
| avg_unknown_cells | 16.0 | 5.34 |

## 窗口逐局

```
log | idx | start | rank | rnd | eBuy | eDep | buy | unconf | trans | soulOK | soulX | lvlX | unk
chess_clean3_1450.log | 1 | 10-28 14:50 | 6 | 22 | 4 | 4 | 14/15 | 0 | 1 | 4 | 0 | 0 | 0
chess_clean3_1450.log | 2 | 10-28 15:17 | 5 | 20 | 6 | 5 | 18/19 | 0 | 3 | 5 | 3 | 0 | 2
chess_clean3_1450.log | 3 | 10-28 15:39 | 6 | 20 | 4 | 3 | 10/12 | 2 | 1 | 1 | 1 | 0 | 14
```